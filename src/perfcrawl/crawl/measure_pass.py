"""Worker-pool measurement driver — D-09/D-10/D-12 + Pitfall 2/8.

``measure_pass(in_scope, errors, *, cfg, target)`` is the measurement half of the
D-01 two-pass crawl: it loops the frozen in-scope list (from ``discover()``)
through the unchanged Phase-2 ``measure_url()`` seam — one INDEPENDENT Chrome per
worker — and assembles the results into one multi-page ``RunRecord`` plus one
merged ``raw_artifacts`` map for ``write_outputs`` to land on disk.

Analog: ``orchestrator.py`` ``measure_url`` is the EXACT unit this module loops
over. The highest-value reuse in the phase — this module is the bounded pool +
politeness gate + artifact-merge wrapper around an unchanged seam.

Key invariants:

  - **One Chrome per call (Pitfall 2 / A6):** ``measure_url`` launches its own
    Chromium with ``--remote-debugging-port=0`` (kernel-picked) + a per-call
    ``tempfile.mkdtemp`` user-data-dir, so N concurrent calls get N independent
    Chromes/ports/tempdirs — no same-port conflict. The pool MUST call
    ``measure_url`` independently per worker; it must NEVER share a port.
  - **Bounded pool:** ``ThreadPoolExecutor(max_workers=cfg.concurrency)`` — the
    per-host concurrency IS the Chrome-pool size (D-09).
  - **Artifact merge:** every ``measure_url`` call returns its OWN
    ``{url_key: (json, html)}`` dict; this module MERGES them into one map and
    builds ONE multi-page ``RunRecord`` from all collected ``PageResult``s, then
    the CLI passes the merged map to ``write_outputs`` (which already iterates
    ``run_record.pages`` and looks up ``raw_artifacts[page.url_key]`` — so the
    merged map "just works").
  - **Per-page failure tagging (D-03):** a ``MeasurementError`` on one URL becomes
    a tagged error ``PageResult`` (``url_key`` set via ``canonical_key``, metrics
    null) — never crashes the whole crawl. Discovery's non-2xx error rows are
    appended to the page list too.
  - **Run-level metadata** (chrome/lighthouse version, throttling, emulation) is
    stamped from the FIRST successful call's ``RunRecord``.
  - **Unique ``url_key`` (store invariant):** discovery's ``canonical_key``
    visited set guarantees one page per key, so the aggregate round-trips through
    ``write_run`` with no duplicate-url_key ``ValueError``. A defensive last-write
    dedup here keeps that invariant even if a discovery+error-row collision slips
    through.
  - **Partial-flush on Ctrl-C (Pitfall 8):** a ``KeyboardInterrupt`` mid-pass
    returns the already-collected pages as a valid tagged-partial ``RunRecord``
    rather than losing the work.

Politeness/backoff: a per-host gate applies ``cfg.min_delay_s`` between dispatches
and Retry-After-aware exponential backoff (``BACKOFF_BASE_S`` up to
``BACKOFF_MAX_RETRIES``) on 429/503 surfaced by ``measure_url``, then
tags-and-moves-on. All tunables import from ``constants.py`` (never inlined).
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

from rich.console import Console

from perfcrawl.constants import (
    BACKOFF_BASE_S,
    BACKOFF_MAX_RETRIES,
)
from perfcrawl.crawl import is_error_row
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import InScope
from perfcrawl.crawl.scope import is_denied
from perfcrawl.models import PageResult, RunRecord
from perfcrawl.orchestrator import MeasurementError, measure_url

# Stderr console for the loud session-loss abort report (D-06). measure_pass is a
# library module, so it owns its own stderr Console rather than importing cli.py's
# (which would be a layering cycle: cli imports measure_pass, not the reverse).
_err_console = Console(stderr=True)


class SessionLost(Exception):
    """AUTH-03: an authenticated audit landed logged-out mid-crawl.

    Raised by ``_measure_one`` when ``_is_session_loss`` fires (a redirect to the
    login path, or a 401/403 status). It propagates the SAME way ``KeyboardInterrupt``
    does — out of the worker and up into ``measure_pass``'s abort handler, which
    flushes the already-measured authenticated pages as a tagged-partial run and
    stops. A logged-out page is NEVER recorded as performance data. The CLI maps the
    resulting partial run to ``ExitCode.AUTH_ERROR`` (D-06).
    """


# Retryable transient signals a target can surface under load. ``measure_url``
# does not currently classify these, but if a future revision raises a
# MeasurementError whose message carries one of these status codes the politeness
# gate backs off before tagging-and-moving-on (D-12). Imported as a module
# constant so the retry policy lives in one editable place alongside the backoff
# tunables in constants.py.
_RETRYABLE_STATUSES = (429, 503)


class _PolitenessGate:
    """Per-host inter-dispatch throttle (threat T-03-07 / D-09).

    Serializes the minimum-delay wait across worker threads so the aggregate
    request rate to one host honors ``min_delay_s`` regardless of pool size.
    """

    def __init__(self, min_delay_s: float) -> None:
        self._min_delay = max(0.0, min_delay_s)
        self._lock = threading.Lock()
        self._last_dispatch = 0.0

    def wait(self) -> None:
        """Block until this worker's scheduled dispatch slot (min_delay-spaced).

        WR-03: the sleep happens OUTSIDE the lock. Under the lock we only compute
        this worker's wait window and advance the scheduled next-dispatch time;
        the lock is then released so other workers can claim their (later) slots
        and run their measurements concurrently. Holding the lock across the sleep
        serialized the whole pool — with any non-zero ``--delay`` the concurrency
        flag was silently ineffective because every worker queued behind the sleep.
        Now only the *scheduling* is serialized; the actual measurements overlap
        while the minimum inter-dispatch spacing is still honored.
        """
        if self._min_delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait_for = max(0.0, self._last_dispatch + self._min_delay - now)
            # Advance from the later of "now" and the last scheduled dispatch so a
            # burst of waiting workers gets distinct, monotonically-spaced slots.
            self._last_dispatch = max(now, self._last_dispatch) + self._min_delay
        if wait_for:
            time.sleep(wait_for)


def _error_row(url: str, url_key: str) -> PageResult:
    """Build a tagged error ``PageResult`` (D-03): url + url_key, metrics null.

    WR-02: ``url_key`` is the discovery-computed canonical key carried through the
    frontier — NEVER re-derived here — so a page's error-row sibling shares the
    exact key discovery admitted to its visited set.
    """
    return PageResult(url=url, url_key=url_key)


def _is_session_loss(landed_url: str | None, status: int | None, login_path: str) -> bool:
    """True iff an authenticated audit landed logged-out (AUTH-03). Never raises.

    Two spike-proven signals, either of which means the session was lost mid-crawl:

      - a 401/403 status on the audited page, OR
      - the Lighthouse ``finalDisplayedUrl`` redirected to the login path
        (``login_path in landed_url``) — the audit followed an auth redirect back
        to /login/ instead of rendering the requested page.

    Never-raise / no false positive: with no ``login_path`` configured (a public
    crawl) and no error status, this returns ``False`` — a public page is never a
    "session loss". ``login_path in (landed_url or "")`` guards a ``None`` landed
    URL so a missing/garbage signal can never crash or false-trip (T-04-09 / V5).
    """
    if status in (401, 403):
        return True
    return bool(login_path) and login_path in (landed_url or "")


def _measure_one(
    url: str,
    url_key: str,
    *,
    cfg: CrawlConfig,
    gate: _PolitenessGate,
    auth_state: dict | None = None,
    login_path: str = "",
) -> tuple[PageResult, tuple[str, str] | None, RunRecord | None]:
    """Measure one URL via the unchanged ``measure_url`` seam.

    Returns ``(page_result, artifact_or_None, run_record_or_None)``. On a
    ``MeasurementError`` (all samples failed / Chrome won't launch / transient
    after backoff) the page is a tagged error row, the artifact is ``None``, and
    the run_record is ``None`` (no metadata to stamp from a failed call).

    Retryable transients (429/503) are retried with exponential backoff up to
    ``BACKOFF_MAX_RETRIES`` before the URL is finally tagged-and-abandoned (D-12).

    ``auth_state`` (D-02) is threaded straight into ``measure_url`` so an
    authenticated crawl replays the resolved session in every worker — the pool
    stays concurrent for authenticated runs. ``login_path`` feeds the per-page
    session-loss check (AUTH-03): after a successful audit, if the landed URL
    redirected to the login path (or the status is 401/403) this raises
    ``SessionLost`` rather than returning a logged-out page.
    """
    # Pitfall 6 deny re-check (D-05 defense-in-depth — "checked before every
    # fetch"): a destructive URL that slipped past discovery's admission gate is
    # tagged as a skip row and NEVER fetched. is_denied is the same fail-CLOSED
    # predicate the discovery gate uses, re-applied here at the last moment.
    if is_denied(url, patterns=cfg.deny_patterns):
        return _error_row(url, url_key), None, None

    attempt = 0
    while True:
        gate.wait()
        try:
            run, artifacts = measure_url(
                url=url,
                samples=cfg.samples,
                emulation=cfg.emulation,
                auth_state=auth_state,
            )
        except MeasurementError as exc:
            if _is_retryable(exc) and attempt < BACKOFF_MAX_RETRIES:
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                attempt += 1
                continue
            # All samples failed / non-retryable: tag-and-move-on (D-03).
            return _error_row(url, url_key), None, None
        except (KeyboardInterrupt, SessionLost):
            # Let measure_pass's partial-flush handler catch these (Pitfall 8 /
            # D-06) — a Ctrl-C OR a mid-crawl session loss is a crawl-level abort
            # signal, never a per-page error row.
            raise
        except Exception:
            # CR-01: ANY other per-page failure (a non-MeasurementError raised by
            # measure_url — UserError, a Playwright/connect error, a bare
            # RuntimeError/OSError from one misbehaving Chrome) degrades to a tagged
            # error row, exactly like a MeasurementError. It must NEVER propagate
            # out of the worker, where the map() loop would re-raise it in the main
            # thread and crash the whole crawl, discarding every page measured so
            # far — violating the D-03 "per-page failure never crashes the crawl"
            # invariant and the Pitfall-8 partial-flush promise.
            return _error_row(url, url_key), None, None
        # measure_url returns a one-page RunRecord; lift its single PageResult.
        page = run.pages[0] if run.pages else _error_row(url, url_key)
        # AUTH-03 (D-06): on a session loss, raise SessionLost BEFORE returning this
        # page so it is never recorded as authenticated performance data. The landed
        # URL is the Lighthouse finalDisplayedUrl (RunRecord.final_displayed_url,
        # surfaced by Plan 01 — a run-metadata field, NOT a return-tuple element);
        # the status is the audited page's status_code. Propagates like Ctrl-C
        # (re-raised above) up to measure_pass's abort handler.
        if _is_session_loss(run.final_displayed_url, page.status_code, login_path):
            raise SessionLost(
                f"session lost auditing {url}: landed on "
                f"{run.final_displayed_url!r} (status {page.status_code})"
            )
        artifact = artifacts.get(page.url_key)
        return page, artifact, run


def _is_retryable(exc: MeasurementError) -> bool:
    """True iff ``exc`` looks like a transient 429/503 worth backing off on (D-12)."""
    msg = str(exc)
    return any(str(code) in msg for code in _RETRYABLE_STATUSES)


def measure_pass(
    in_scope: list[InScope],
    errors: list[PageResult],
    *,
    cfg: CrawlConfig,
    target: str,
    min_delay_s: float | None = None,
    auth_state: dict | None = None,
    abort_state: dict | None = None,
) -> tuple[RunRecord, dict[str, tuple[str, str]]]:
    """Measure every in-scope URL via a bounded pool of ``measure_url`` calls.

    Returns ``(run_record, merged_artifacts)`` where ``run_record`` is ONE
    multi-page ``RunRecord`` over all collected ``PageResult``s (measured pages,
    per-page measurement-error rows, and the discovery-supplied non-2xx ``errors``
    rows) and ``merged_artifacts`` is the union of every successful call's
    ``{url_key: (reportJson, reportHtml)}`` map.

    ``min_delay_s`` (D-11 / CR-01): the robots-aware effective delay the caller
    computed (``RobotsGate.effective_delay``). When provided, the politeness gate
    is built with the STRICTER of ``cfg.min_delay_s`` and ``min_delay_s`` so a
    robots ``Crawl-delay`` honored during discovery is ALSO honored during the
    measurement pass — the phase that generates the real Lighthouse load. When
    ``None`` the gate falls back to ``cfg.min_delay_s`` alone.

    ``auth_state`` (D-02): the resolved Playwright ``storage_state`` for an
    authenticated crawl. Threaded into every worker's ``measure_url`` call so the
    session replays on each independent Chrome (the pool stays concurrent for
    authenticated runs). ``None`` for a public crawl.

    ``abort_state`` (AUTH-03 / D-06 CLI signal): an optional mutable dict the
    caller passes to learn WHY the pass ended. The ``(run_record, merged)`` return
    contract is unchanged (existing call sites + tests keep their 2-tuple unpack),
    so a SessionLost abort is otherwise indistinguishable from a clean partial run.
    When a mid-crawl session loss aborts the pass, ``abort_state["session_lost"]``
    is set to ``True`` — the CLI reads it to map the tagged-partial run to
    ``ExitCode.AUTH_ERROR`` (3). A plain Ctrl-C or a clean full pass leaves it unset.

    Provably terminates: the in-scope list is already bounded by discovery's
    caps; the pool drains it once. On ``KeyboardInterrupt`` OR ``SessionLost``
    (AUTH-03 mid-crawl session loss) the already-collected pages are flushed as a
    valid tagged-partial run (Pitfall 8 / D-06) — a logged-out page is never
    recorded.
    """
    delay = cfg.min_delay_s if min_delay_s is None else max(cfg.min_delay_s, min_delay_s)
    gate = _PolitenessGate(delay)
    measured: list[PageResult] = []
    merged: dict[str, tuple[str, str]] = {}
    first_run: RunRecord | None = None

    # AUTH-03: the login path the session-loss check compares finalDisplayedUrl
    # against. Derived once from cfg.login_url (path component only, so a redirect
    # to /login/?next=… still matches). Empty for a public crawl → never a loss.
    login_path = ""
    if cfg.login_url:
        try:
            login_path = urlsplit(cfg.login_url).path or ""
        except Exception:
            login_path = ""

    # Pool size == per-host concurrency == Chrome-pool size (D-09). Each future
    # is an independent measure_url call (its own Chrome/port/tempdir — A6).
    executor = ThreadPoolExecutor(max_workers=max(1, cfg.concurrency))
    try:
        # Lazily iterate map() so a KeyboardInterrupt during the pass still yields
        # every page produced up to the interrupt (Pitfall 8 partial-flush).
        results = executor.map(
            lambda u: _measure_one(
                u.url,
                u.url_key,
                cfg=cfg,
                gate=gate,
                auth_state=auth_state,
                login_path=login_path,
            ),
            in_scope,
        )
        for page, artifact, run in results:
            measured.append(page)
            if artifact is not None:
                merged[page.url_key] = artifact
            if first_run is None and run is not None:
                first_run = run
    except (KeyboardInterrupt, SessionLost) as abort:
        # Pitfall 8 / D-06: flush what we have. A Ctrl-C OR a mid-crawl session
        # loss (AUTH-03) aborts the pass; the already-measured authenticated pages
        # are kept as a tagged-partial run, but no logged-out page is recorded.
        # Cancel any still-queued futures so we don't block on the full in-scope
        # list during teardown.
        executor.shutdown(wait=False, cancel_futures=True)
        if isinstance(abort, SessionLost):
            # D-06: report the session loss LOUDLY to stderr (the Ctrl-C path is
            # silent — the user already knows they pressed Ctrl-C).
            _err_console.print(
                "[red]session lost mid-crawl — aborting; "
                "already-measured pages kept, no logged-out page recorded[/red]"
            )
            # AUTH-03 CLI signal: flag the out-parameter so the CLI maps this
            # tagged-partial run to ExitCode.AUTH_ERROR (3), not a clean exit 0.
            # The Ctrl-C branch deliberately leaves it unset (Ctrl-C is exit 0
            # partial, not an auth failure).
            if abort_state is not None:
                abort_state["session_lost"] = True
    finally:
        # WR-04: cancel still-queued futures and return the partial run promptly
        # (wait=False). NOTE: ThreadPoolExecutor workers are NON-daemon, so any
        # page already mid-measurement is NOT killed here — the interpreter joins
        # those worker threads at process exit, and control does not fully return
        # until each in-flight measure_url finishes its current sample (up to the
        # per-sample Lighthouse timeout). measure_url's own try/finally still reaps
        # that page's Chrome when its sample completes. So Ctrl-C flushes the
        # already-collected pages immediately but does not hard-abort an in-flight
        # sample; a true hard abort would require a custom daemon-thread factory.
        executor.shutdown(wait=False, cancel_futures=True)

    # D-03: discovery's non-2xx error rows surface as pages too.
    all_pages: list[PageResult] = [*measured, *errors]

    # Defensive url_key dedup so the aggregate never trips write_run's
    # duplicate-url_key ValueError, even if a measured page and a discovery error
    # row share a canonical key (the visited set should prevent this upstream).
    # WR-02: on a collision PREFER the richer record — never let a colliding error
    # row (metrics null) overwrite a successfully measured page.
    by_key: dict[str, PageResult] = {}
    for page in all_pages:
        existing = by_key.get(page.url_key)
        if existing is not None and is_error_row(page) and not is_error_row(existing):
            continue  # keep the measured page over a colliding error row
        by_key[page.url_key] = page
    unique_pages = list(by_key.values())

    # Stamp run-level metadata from the first successful call's RunRecord; fall
    # back to a bare aggregate when every page failed (still a valid RunRecord).
    run_record = RunRecord(
        id=uuid4(),
        started_at=datetime.now(UTC),
        target=target,
        # WR-05 (D-17): stamp the crawl-level auth flag. The single-URL path sets
        # this on its RunRecord (orchestrator.measure_url), but the crawl path
        # built its aggregate RunRecord here and omitted the field, so it
        # defaulted to None — every `perfcrawl crawl` run, authenticated or not,
        # persisted/exported auth_used=None. measure_pass already receives
        # auth_state, so the information is in hand; propagate it so SQLite
        # history, CSV/JSON export, and future regression self-joins get correct
        # run metadata.
        auth_used=auth_state is not None,
        chrome_version=first_run.chrome_version if first_run else None,
        lighthouse_version=first_run.lighthouse_version if first_run else None,
        throttling=first_run.throttling if first_run else None,
        emulation=first_run.emulation if first_run else cfg.emulation,
        pages=unique_pages,
    )
    return run_record, merged
