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
from uuid import uuid4

from perfcrawl.canonical import canonical_key
from perfcrawl.constants import (
    BACKOFF_BASE_S,
    BACKOFF_MAX_RETRIES,
)
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import InScope
from perfcrawl.models import PageResult, RunRecord
from perfcrawl.orchestrator import MeasurementError, measure_url

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
        """Block until at least ``min_delay_s`` has elapsed since the last dispatch."""
        if self._min_delay <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_dispatch
            if elapsed < self._min_delay:
                time.sleep(self._min_delay - elapsed)
            self._last_dispatch = time.monotonic()


def _error_row(url: str) -> PageResult:
    """Build a tagged error ``PageResult`` (D-03): url + url_key, metrics null."""
    return PageResult(url=url, url_key=canonical_key(url))


def _measure_one(
    url: str, *, cfg: CrawlConfig, gate: _PolitenessGate
) -> tuple[PageResult, tuple[str, str] | None, RunRecord | None]:
    """Measure one URL via the unchanged ``measure_url`` seam.

    Returns ``(page_result, artifact_or_None, run_record_or_None)``. On a
    ``MeasurementError`` (all samples failed / Chrome won't launch / transient
    after backoff) the page is a tagged error row, the artifact is ``None``, and
    the run_record is ``None`` (no metadata to stamp from a failed call).

    Retryable transients (429/503) are retried with exponential backoff up to
    ``BACKOFF_MAX_RETRIES`` before the URL is finally tagged-and-abandoned (D-12).
    """
    attempt = 0
    while True:
        gate.wait()
        try:
            run, artifacts = measure_url(
                url=url, samples=cfg.samples, emulation=cfg.emulation
            )
        except MeasurementError as exc:
            if _is_retryable(exc) and attempt < BACKOFF_MAX_RETRIES:
                time.sleep(BACKOFF_BASE_S * (2**attempt))
                attempt += 1
                continue
            # All samples failed / non-retryable: tag-and-move-on (D-03).
            return _error_row(url), None, None
        # measure_url returns a one-page RunRecord; lift its single PageResult.
        page = run.pages[0] if run.pages else _error_row(url)
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
) -> tuple[RunRecord, dict[str, tuple[str, str]]]:
    """Measure every in-scope URL via a bounded pool of ``measure_url`` calls.

    Returns ``(run_record, merged_artifacts)`` where ``run_record`` is ONE
    multi-page ``RunRecord`` over all collected ``PageResult``s (measured pages,
    per-page measurement-error rows, and the discovery-supplied non-2xx ``errors``
    rows) and ``merged_artifacts`` is the union of every successful call's
    ``{url_key: (reportJson, reportHtml)}`` map.

    Provably terminates: the in-scope list is already bounded by discovery's
    caps; the pool drains it once. On ``KeyboardInterrupt`` the already-collected
    pages are flushed as a valid tagged-partial run (Pitfall 8).
    """
    gate = _PolitenessGate(cfg.min_delay_s)
    measured: list[PageResult] = []
    merged: dict[str, tuple[str, str]] = {}
    first_run: RunRecord | None = None

    # Pool size == per-host concurrency == Chrome-pool size (D-09). Each future
    # is an independent measure_url call (its own Chrome/port/tempdir — A6).
    executor = ThreadPoolExecutor(max_workers=max(1, cfg.concurrency))
    try:
        # Lazily iterate map() so a KeyboardInterrupt during the pass still yields
        # every page produced up to the interrupt (Pitfall 8 partial-flush).
        results = executor.map(
            lambda u: _measure_one(u.url, cfg=cfg, gate=gate), in_scope
        )
        for page, artifact, run in results:
            measured.append(page)
            if artifact is not None:
                merged[page.url_key] = artifact
            if first_run is None and run is not None:
                first_run = run
    except KeyboardInterrupt:
        # Pitfall 8: flush what we have. Cancel any still-queued futures so we
        # don't block on the full in-scope list during interpreter teardown.
        executor.shutdown(wait=False, cancel_futures=True)
    finally:
        # Don't wait on cancelled work; threads measuring in-flight are daemonic
        # enough that measure_url's own try/finally reaps their Chrome.
        executor.shutdown(wait=False, cancel_futures=True)

    # D-03: discovery's non-2xx error rows surface as pages too.
    all_pages: list[PageResult] = [*measured, *errors]

    # Defensive last-write url_key dedup so the aggregate never trips write_run's
    # duplicate-url_key ValueError, even if a measured page and a discovery error
    # row share a canonical key (the visited set should prevent this upstream).
    by_key: dict[str, PageResult] = {}
    for page in all_pages:
        by_key[page.url_key] = page
    unique_pages = list(by_key.values())

    # Stamp run-level metadata from the first successful call's RunRecord; fall
    # back to a bare aggregate when every page failed (still a valid RunRecord).
    run_record = RunRecord(
        id=uuid4(),
        started_at=datetime.now(UTC),
        target=target,
        chrome_version=first_run.chrome_version if first_run else None,
        lighthouse_version=first_run.lighthouse_version if first_run else None,
        throttling=first_run.throttling if first_run else None,
        emulation=first_run.emulation if first_run else cfg.emulation,
        pages=unique_pages,
    )
    return run_record, merged
