"""Tests for ``perfcrawl.crawl.measure_pass`` — D-09/D-10/D-12 + Pitfall 2/8.

``measure_pass(in_scope, errors, *, cfg, target)`` is the worker-pool driver: it
loops the frozen in-scope list (from ``discover()``) through the unchanged
Phase-2 ``measure_url()`` seam — one independent Chrome per worker — merges every
call's ``raw_artifacts`` dict into one ``{url_key: (json, html)}`` map and all
``PageResult``s into one multi-page ``RunRecord``.

Test strategy:

  - Monkeypatch ``perfcrawl.crawl.measure_pass.measure_url`` to a canned
    single-page ``RunRecord`` + canned ``raw_artifacts`` so no real Chrome/Node
    launches (mirrors ``tests/test_cli.py``'s ``measure_url`` patch).
  - Pin the seam contract: pool size == ``cfg.concurrency``; one ``measure_url``
    invocation per URL; per-page ``MeasurementError`` → tagged error row (other
    URLs still measure); aggregate round-trips through ``init_db``/``write_run``
    with no duplicate-url_key ``ValueError``; ``KeyboardInterrupt`` mid-pass
    flushes the already-collected pages (Pitfall 8).
"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from perfcrawl.canonical import canonical_key
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import InScope
from perfcrawl.crawl.measure_pass import measure_pass
from perfcrawl.models import MetricSample, PageResult, RunRecord
from perfcrawl.store import init_db, write_run

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _canned_run(url: str) -> tuple[RunRecord, dict[str, tuple[str, str]]]:
    """A canned single-page (RunRecord, raw_artifacts) for one URL.

    Mirrors the real ``measure_url`` tuple-return contract: a one-page RunRecord
    whose ``raw_artifacts`` is keyed by the page's ``url_key``.
    """
    key = canonical_key(url)
    page = PageResult(
        url=url,
        url_key=key,
        perf_score=90.0,
        lcp_ms=MetricSample(median=1200.0, samples=[1200.0]),
        status_code=200,
    )
    run = RunRecord(
        id=uuid4(),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target=url,
        chrome_version="137.0.7151.40",
        lighthouse_version="13.3.0",
        emulation="mobile",
        pages=[page],
    )
    return run, {key: ('{"lhr":{}}', "<html/>")}


def _in_scope(*urls: str) -> list[InScope]:
    # WR-02: InScope now carries the discovery-computed canonical key; mirror that
    # here so the pool reuses the same key discovery would have admitted.
    return [InScope(url=u, depth=0, url_key=canonical_key(u)) for u in urls]


def _patch_measure(monkeypatch, *, side_effects=None, record=None, landed=None):
    """Patch the measure_url seam; record each call's url; optional per-url errors.

    ``side_effects`` maps a url → an Exception instance to raise for that url.
    ``landed`` maps a url → the ``final_displayed_url`` (and a status_code parsed
    from it) so a test can simulate a mid-crawl /login/ redirect (session loss).
    The patched ``fake`` also records the ``auth_state`` it was called with so the
    auth_state-threading test can assert the value flowed through the pool.
    """
    side_effects = side_effects or {}
    landed = landed or {}
    if record is None:
        record = []

    def fake(*, url, samples=1, emulation="mobile", auth_state=None):
        record.append((url, auth_state))
        if url in side_effects:
            raise side_effects[url]
        run, artifacts = _canned_run(url)
        if url in landed:
            # Simulate a mid-crawl session loss: the audit landed on /login/.
            run.final_displayed_url = landed[url]
        return run, artifacts

    monkeypatch.setattr("perfcrawl.crawl.measure_pass.measure_url", fake)
    return record


# --------------------------------------------------------------------------- #
# Pool / merge behavior
# --------------------------------------------------------------------------- #


def test_measure_pass_merges_three_pages(monkeypatch) -> None:
    """A 3-URL pass yields a 3-page RunRecord with a 3-entry merged artifact map."""
    urls = [
        "https://example.com/",
        "https://example.com/about",
        "https://example.com/blog",
    ]
    _patch_measure(monkeypatch)
    cfg = CrawlConfig()
    run, merged = measure_pass(_in_scope(*urls), [], cfg=cfg, target="https://example.com/")
    assert len(run.pages) == 3
    assert len(merged) == 3
    keys = {canonical_key(u) for u in urls}
    assert set(merged) == keys
    assert {p.url_key for p in run.pages} == keys


def test_measure_pass_one_call_per_url(monkeypatch) -> None:
    """Each URL drives exactly one independent measure_url invocation (no shared port)."""
    urls = ["https://example.com/a", "https://example.com/b"]
    record = _patch_measure(monkeypatch)
    measure_pass(_in_scope(*urls), [], cfg=CrawlConfig(), target="https://example.com/")
    called_urls = [u for (u, _auth) in record]
    assert sorted(called_urls) == sorted(urls)
    assert len(record) == len(urls)


def test_measure_pass_pool_size_is_concurrency(monkeypatch) -> None:
    """The ThreadPoolExecutor is built with max_workers == cfg.concurrency."""
    captured = {}
    real_init = ThreadPoolExecutor.__init__

    def spy_init(self, *args, **kwargs):
        captured["max_workers"] = kwargs.get("max_workers", args[0] if args else None)
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(ThreadPoolExecutor, "__init__", spy_init)
    _patch_measure(monkeypatch)
    cfg = CrawlConfig(concurrency=5)
    measure_pass(_in_scope("https://example.com/x"), [], cfg=cfg, target="https://example.com/")
    assert captured["max_workers"] == 5


# --------------------------------------------------------------------------- #
# Error tagging (per-page failure never crashes the crawl)
# --------------------------------------------------------------------------- #


def test_measurement_error_tags_one_page(monkeypatch) -> None:
    """A MeasurementError on one URL → tagged error row; the others still measure."""
    from perfcrawl.orchestrator import MeasurementError

    bad = "https://example.com/broken"
    urls = ["https://example.com/ok1", bad, "https://example.com/ok2"]
    _patch_measure(monkeypatch, side_effects={bad: MeasurementError("all samples failed")})
    run, merged = measure_pass(
        _in_scope(*urls), [], cfg=CrawlConfig(), target="https://example.com/"
    )
    assert len(run.pages) == 3  # all three appear (one tagged)
    by_key = {p.url_key: p for p in run.pages}
    bad_key = canonical_key(bad)
    assert bad_key in by_key
    err_page = by_key[bad_key]
    assert err_page.perf_score is None  # metrics null on the tagged error row
    assert err_page.lcp_ms is None
    # The failed URL has no artifact in the merged map.
    assert bad_key not in merged
    # The other two measured fine.
    assert by_key[canonical_key("https://example.com/ok1")].perf_score == 90.0


def test_unexpected_exception_tags_one_page(monkeypatch) -> None:
    """CR-01: a non-MeasurementError from measure_url → tagged error row, not a crash.

    A bare RuntimeError raised for one URL must degrade to a tagged error row
    (like a MeasurementError), leaving the other URLs measured and the pass
    returning a valid RunRecord — never propagating out of the worker to crash
    the whole crawl and discard already-measured pages.
    """
    bad = "https://example.com/explode"
    urls = ["https://example.com/ok1", bad, "https://example.com/ok2"]
    _patch_measure(monkeypatch, side_effects={bad: RuntimeError("chrome went sideways")})
    run, merged = measure_pass(
        _in_scope(*urls), [], cfg=CrawlConfig(), target="https://example.com/"
    )
    assert len(run.pages) == 3  # the crawl survived; all three pages present
    by_key = {p.url_key: p for p in run.pages}
    bad_key = canonical_key(bad)
    assert bad_key in by_key
    assert by_key[bad_key].perf_score is None  # the failed URL is a tagged error row
    assert bad_key not in merged  # no artifact for the failed page
    # The other two measured fine despite the sibling's unexpected blow-up.
    assert by_key[canonical_key("https://example.com/ok1")].perf_score == 90.0
    assert by_key[canonical_key("https://example.com/ok2")].perf_score == 90.0


def test_discovery_errors_appended_as_rows(monkeypatch) -> None:
    """Discovery-supplied non-2xx error rows surface as pages too (D-03)."""
    _patch_measure(monkeypatch)
    disco_err = PageResult(
        url="https://example.com/404",
        url_key=canonical_key("https://example.com/404"),
        status_code=404,
    )
    run, _ = measure_pass(
        _in_scope("https://example.com/ok"),
        [disco_err],
        cfg=CrawlConfig(),
        target="https://example.com/",
    )
    keys = {p.url_key for p in run.pages}
    assert canonical_key("https://example.com/404") in keys
    assert canonical_key("https://example.com/ok") in keys
    assert len(run.pages) == 2


# --------------------------------------------------------------------------- #
# Run-level metadata stamped from the first successful call
# --------------------------------------------------------------------------- #


def test_run_metadata_stamped(monkeypatch) -> None:
    """Aggregate RunRecord stamps target + chrome/lighthouse version metadata."""
    _patch_measure(monkeypatch)
    run, _ = measure_pass(
        _in_scope("https://example.com/p1", "https://example.com/p2"),
        [],
        cfg=CrawlConfig(),
        target="https://example.com/",
    )
    assert run.target == "https://example.com/"
    assert run.chrome_version == "137.0.7151.40"
    assert run.lighthouse_version == "13.3.0"
    assert run.started_at.tzinfo is not None  # tz-aware (D-17)


def test_auth_used_true_when_auth_state_passed(monkeypatch) -> None:
    """WR-05 (D-17): the crawl-level aggregate RunRecord records auth_used=True
    when an auth_state is threaded into measure_pass."""
    _patch_measure(monkeypatch)
    run, _ = measure_pass(
        _in_scope("https://example.com/p1", "https://example.com/p2"),
        [],
        cfg=CrawlConfig(),
        target="https://example.com/",
        auth_state={"cookies": [{"name": "sid", "value": "abc"}]},
    )
    assert run.auth_used is True


def test_auth_used_false_when_no_auth_state(monkeypatch) -> None:
    """WR-05 (D-17): an unauthenticated crawl records auth_used=False (NOT None),
    so downstream history/export/regression never see a null auth flag."""
    _patch_measure(monkeypatch)
    run, _ = measure_pass(
        _in_scope("https://example.com/p1", "https://example.com/p2"),
        [],
        cfg=CrawlConfig(),
        target="https://example.com/",
        # no auth_state passed
    )
    assert run.auth_used is False


# --------------------------------------------------------------------------- #
# Aggregate round-trips through the store with no duplicate-url_key ValueError
# --------------------------------------------------------------------------- #


def test_aggregate_round_trips_through_store(monkeypatch, tmp_path: Path) -> None:
    """init_db/write_run accept the aggregate with no duplicate-url_key ValueError."""
    urls = [
        "https://example.com/x",
        "https://example.com/y",
        "https://example.com/z",
    ]
    _patch_measure(monkeypatch)
    run, _ = measure_pass(_in_scope(*urls), [], cfg=CrawlConfig(), target="https://example.com/")
    db = tmp_path / "perfcrawl.db"
    conn = sqlite3.connect(db)
    try:
        init_db(conn)
        write_run(conn, run)  # must NOT raise ValueError(duplicate url_key)
        rows = conn.execute("SELECT id FROM runs WHERE id = ?", (str(run.id),)).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Partial-flush on KeyboardInterrupt (Pitfall 8)
# --------------------------------------------------------------------------- #


def test_keyboard_interrupt_partial_flush(monkeypatch) -> None:
    """A KeyboardInterrupt mid-pass returns the already-collected pages, not lost."""
    boom = "https://example.com/interrupt"
    urls = ["https://example.com/done", boom, "https://example.com/never"]
    _patch_measure(monkeypatch, side_effects={boom: KeyboardInterrupt()})
    # concurrency=1 makes ordering deterministic: /done completes, then /interrupt
    # raises KeyboardInterrupt, and /never is not reached.
    cfg = CrawlConfig(concurrency=1)
    run, merged = measure_pass(_in_scope(*urls), [], cfg=cfg, target="https://example.com/")
    # The partial run is returned (reaching this assert is the proof it flushed).
    keys = {p.url_key for p in run.pages}
    assert canonical_key("https://example.com/done") in keys
    # The already-measured page kept its artifact.
    assert canonical_key("https://example.com/done") in merged


# --------------------------------------------------------------------------- #
# AUTH-03: per-page session-loss detection + partial-flush abort
# --------------------------------------------------------------------------- #


def test_session_loss_signal() -> None:
    """_is_session_loss truth table: /login/ redirect + 401/403 = loss; else not.

    A never-raise pure predicate: a healthy authenticated 200 is NOT a loss, and a
    None landed_url with an empty login_path is NOT a loss (no false positive on a
    public crawl where no login path is configured).
    """
    from perfcrawl.crawl.measure_pass import _is_session_loss

    # finalDisplayedUrl redirected to the login path → session lost.
    assert _is_session_loss("https://x/login/", None, "/login/") is True
    # 401 / 403 status → session lost regardless of where it landed.
    assert _is_session_loss("https://x/dashboard/", 401, "/login/") is True
    assert _is_session_loss("https://x/dashboard/", 403, "/login/") is True
    # Healthy authenticated page (200, landed on the requested page) → NOT a loss.
    assert _is_session_loss("https://x/dashboard/", 200, "/login/") is False
    # No login path + no signal → NOT a loss (never-raise, no false positive).
    assert _is_session_loss(None, None, "") is False


def test_session_loss_partial_flush_abort(monkeypatch) -> None:
    """A mid-crawl session loss flushes already-measured pages and aborts.

    measure_url yields a /login/ landing on the 2nd page (session expired). The
    1st page must be flushed into the RunRecord; the logged-out 2nd page must NOT
    be recorded as performance data (AUTH-03); the pass aborts (the 3rd page is
    never measured).
    """
    first = "https://example.com/dash1"
    lost = "https://example.com/dash2"
    never = "https://example.com/dash3"
    urls = [first, lost, never]
    record = _patch_measure(
        monkeypatch,
        landed={lost: "https://example.com/login/?next=/dash2"},
    )
    # concurrency=1 makes ordering deterministic: /dash1 measures, then /dash2
    # lands on /login/ → SessionLost → abort before /dash3.
    cfg = CrawlConfig(concurrency=1, login_url="https://example.com/login/")
    run, merged = measure_pass(_in_scope(*urls), [], cfg=cfg, target="https://example.com/")
    keys = {p.url_key for p in run.pages}
    # The healthy 1st page is flushed (the work is kept).
    assert canonical_key(first) in keys
    assert canonical_key(first) in merged
    # The logged-out page is NEVER recorded as a measured page.
    assert canonical_key(lost) not in keys
    # The pass aborted before reaching the 3rd page.
    assert canonical_key(never) not in keys
    called_urls = [u for (u, _auth) in record]
    assert never not in called_urls


def test_auth_state_threaded_into_pool(monkeypatch) -> None:
    """auth_state is threaded through the worker pool into each measure_url call."""
    auth_state = {"cookies": [{"name": "sessionid", "value": "abc"}], "origins": []}
    urls = ["https://example.com/a", "https://example.com/b"]
    record = _patch_measure(monkeypatch)
    measure_pass(
        _in_scope(*urls),
        [],
        cfg=CrawlConfig(),
        target="https://example.com/",
        auth_state=auth_state,
    )
    # Every measure_url call for THIS pass's URLs received the same auth_state
    # object. (Filter to this test's own URLs: a prior test's pool may leave a
    # non-daemon worker draining a cancelled future that records under the
    # re-patched seam — that lingering call carries this test's URLs' siblings,
    # never one of `urls`, so the filter keeps the assertion deterministic.)
    seen = [(u, auth) for (u, auth) in record if u in urls]
    assert seen  # at least one call for this pass happened
    assert all(auth is auth_state for (_url, auth) in seen)


def test_denied_url_not_measured(monkeypatch) -> None:
    """A denied URL reaching the pass becomes a skip row; measure_url is NOT called.

    Defense-in-depth (D-05 / Pitfall 6 "checked before every fetch"): a destructive
    URL that slipped past discovery is re-checked at the top of _measure_one and
    tagged as a skip row instead of being fetched.
    """
    denied = "https://example.com/account/logout/"
    ok = "https://example.com/safe"
    urls = [denied, ok]
    record = _patch_measure(monkeypatch)
    run, merged = measure_pass(
        _in_scope(*urls),
        [],
        cfg=CrawlConfig(),  # DEFAULT_DENY_PATTERNS includes logout
        target="https://example.com/",
    )
    called_urls = [u for (u, _auth) in record]
    # The denied URL was NEVER passed to measure_url.
    assert denied not in called_urls
    assert ok in called_urls
    # It still appears as a tagged skip row (metrics null), with no artifact.
    by_key = {p.url_key: p for p in run.pages}
    assert canonical_key(denied) in by_key
    assert by_key[canonical_key(denied)].perf_score is None
    assert canonical_key(denied) not in merged
