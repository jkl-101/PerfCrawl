"""Tests for the Playwright + CDP orchestrator (Phase 2 D-01..D-04, D-14, D-15, D-16).

The real Playwright + Chrome + Node loop is too expensive (and non-deterministic)
for unit tests, so these mock at three layers:

  1. ``perfcrawl.orchestrator.sync_playwright`` — the ``with sync_playwright() as p:``
     context manager. Stub ``p.chromium.connect_over_cdp`` returns a fake Browser
     whose ``new_context()`` returns fake BrowserContext objects with ``close()``
     mocked. Lets us assert RUN-03 cycling without launching anything.
  2. ``perfcrawl.orchestrator._launch_chrome_with_cdp_port`` (the
     ``subprocess.Popen`` + DevToolsActivePort reader) — return a fake Popen
     with ``kill()`` mocked + an arbitrary port + a real tmp_path user_data_dir.
  3. ``perfcrawl.orchestrator.run_one_sample`` — the test controls per-sample
     success/failure via monkeypatch on the module-level reference.

Each test docstring cites the D-XX it pins so the verifier can map tests → decisions.
"""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_lhr() -> dict:
    """A minimal LH-13.x lhr dict that normalize_lh will accept (D-10 version gate)."""
    return {
        "lighthouseVersion": "13.3.0",
        "finalDisplayedUrl": "https://example.com/",
        "categories": {
            "performance": {"score": 0.95},
            "accessibility": {"score": 0.98},
            "seo": {"score": 0.92},
            "best-practices": {"score": 1.0},
        },
        "audits": {
            "largest-contentful-paint": {"numericValue": 1500.0, "score": 1.0},
            "cumulative-layout-shift": {"numericValue": 0.05, "score": 1.0},
            "total-blocking-time": {"numericValue": 50.0, "score": 1.0},
            "server-response-time": {"numericValue": 100.0, "score": 1.0},
            "total-byte-weight": {"numericValue": 500000.0, "score": 1.0},
            "network-requests": {
                "details": {
                    "items": [
                        {
                            "url": "https://example.com/",
                            "resourceType": "Document",
                            "transferSize": 1000,
                            "statusCode": 200,
                            "networkRequestTime": 0.0,
                            "networkEndTime": 100.0,
                        }
                    ]
                }
            },
        },
        "environment": {"hostUserAgent": "Mozilla/5.0 Chrome/137.0.7151.40"},
        "configSettings": {"throttling": {"rttMs": 150, "cpuSlowdownMultiplier": 4}},
    }


def _stub_worker_envelope() -> dict:
    """Full worker envelope: lhr + reportJson + reportHtml (OUT-03 side-channel)."""
    return {
        "lhr": _stub_lhr(),
        "reportJson": "{}",
        "reportHtml": "<html></html>",
    }


class _FakeContext:
    """Mocked Playwright BrowserContext — tracks close() calls."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    """Mocked Playwright Browser returned by connect_over_cdp."""

    def __init__(self) -> None:
        self.contexts: list[_FakeContext] = []

    def new_context(self) -> _FakeContext:
        c = _FakeContext()
        self.contexts.append(c)
        return c


class _FakePlaywrightCM:
    """Context-manager replacement for ``sync_playwright()``."""

    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser

    def __enter__(self) -> SimpleNamespace:
        chromium = SimpleNamespace(
            connect_over_cdp=lambda _endpoint: self._browser,
            executable_path="/fake/chromium",
        )
        return SimpleNamespace(chromium=chromium)

    def __exit__(self, *args) -> None:
        return None


class _FakeChromeProc:
    """Mocked subprocess.Popen — tracks kill() and wait() calls.

    CR-02 invariant: every kill() must be followed by wait() so the killed
    process is reaped instead of becoming a <defunct> zombie. The ``waited``
    flag lets tests assert the wait() call actually happens.
    """

    def __init__(self) -> None:
        self.killed = False
        self.waited = False

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0


def _install_orchestrator_stubs(
    monkeypatch,
    *,
    browser: _FakeBrowser | None = None,
    chrome_proc: _FakeChromeProc | None = None,
    tmp_user_data_dir: Path | None = None,
    port: int = 9222,
):
    """Replace sync_playwright + _launch_chrome_with_cdp_port with deterministic stubs."""
    import perfcrawl.orchestrator as orch

    browser = browser or _FakeBrowser()
    chrome_proc = chrome_proc or _FakeChromeProc()
    user_data_dir = tmp_user_data_dir or Path(tempfile.mkdtemp(prefix="test-orch-"))

    monkeypatch.setattr(orch, "sync_playwright", lambda: _FakePlaywrightCM(browser))
    monkeypatch.setattr(
        orch,
        "_launch_chrome_with_cdp_port",
        lambda: (chrome_proc, port, user_data_dir),
    )
    # Always pass preflight — the lighthouse-worker may not be npm-installed
    # in the test env; the orchestrator should not block on that for mocked runs.
    monkeypatch.setattr(orch, "preflight", lambda: None)

    return browser, chrome_proc, user_data_dir


# ---------------------------------------------------------------------------
# RUN-04 + OUT-03: happy path
# ---------------------------------------------------------------------------


def test_measure_url_returns_run_record_and_raw_artifacts(monkeypatch):
    """RUN-04 happy path + OUT-03 side-channel.

    measure_url returns ``(run_record, raw_artifacts)`` where ``run_record.pages``
    has the aggregated PageResult with ``lcp_ms.samples`` length 3 (one per
    successful sample) and ``raw_artifacts`` is a dict with one key (the page's
    ``url_key``) → ``(reportJson, reportHtml)`` from the FIRST successful sample.
    """
    from perfcrawl.canonical import canonical_key
    from perfcrawl.orchestrator import measure_url
    import perfcrawl.orchestrator as orch

    _install_orchestrator_stubs(monkeypatch)
    monkeypatch.setattr(orch, "run_one_sample", lambda **kw: _stub_worker_envelope())

    run_record, raw_artifacts = measure_url(
        url="https://example.com/", samples=3, emulation="mobile"
    )

    assert run_record.pages, "expected at least one aggregated PageResult"
    page = run_record.pages[0]
    assert page.url_key == canonical_key("https://example.com/")
    assert page.lcp_ms is not None
    assert len(page.lcp_ms.samples) == 3

    assert isinstance(raw_artifacts, dict)
    assert set(raw_artifacts.keys()) == {page.url_key}
    assert raw_artifacts[page.url_key] == ("{}", "<html></html>")


# ---------------------------------------------------------------------------
# RUN-03: cold cache via fresh BrowserContext per sample
# ---------------------------------------------------------------------------


def test_fresh_context_per_sample(monkeypatch):
    """RUN-03: browser.new_context() called once per sample; each context closed."""
    from perfcrawl.orchestrator import measure_url
    import perfcrawl.orchestrator as orch

    browser, _, _ = _install_orchestrator_stubs(monkeypatch)
    monkeypatch.setattr(orch, "run_one_sample", lambda **kw: _stub_worker_envelope())

    measure_url(url="https://example.com/", samples=3, emulation="mobile")

    assert len(browser.contexts) == 3
    assert all(c.closed for c in browser.contexts)


# ---------------------------------------------------------------------------
# D-14: timeout retry + drop, recovery, all-fail
# ---------------------------------------------------------------------------


def test_timeout_retry_then_drop(monkeypatch):
    """D-14 retry-then-drop.

    With samples=3 and the worker returning None on calls 1+2 (initial + retry
    of sample 1 both fail) and then succeeding from call 3 onward, the final
    PageResult.lcp_ms.samples has length 2 (samples 2 and 3 succeeded on first
    try). The worker was called 4 times total: 1 (sample 1 initial fail),
    2 (sample 1 retry fail; dropped), 3 (sample 2 succeeds), 4 (sample 3 succeeds).
    """
    from perfcrawl.orchestrator import measure_url
    import perfcrawl.orchestrator as orch

    _install_orchestrator_stubs(monkeypatch)

    call_count = {"n": 0}

    def _staged(**kw):
        call_count["n"] += 1
        # First two calls return None (sample 1 initial + retry), rest succeed.
        if call_count["n"] <= 2:
            return None
        return _stub_worker_envelope()

    monkeypatch.setattr(orch, "run_one_sample", _staged)

    run_record, _ = measure_url(url="https://example.com/", samples=3, emulation="mobile")
    page = run_record.pages[0]
    assert len(page.lcp_ms.samples) == 2
    assert call_count["n"] == 4


def test_one_retry_recovery(monkeypatch):
    """D-14 one-retry recovery: sample 1 fails once, retry succeeds, included in result."""
    from perfcrawl.orchestrator import measure_url
    import perfcrawl.orchestrator as orch

    _install_orchestrator_stubs(monkeypatch)

    call_count = {"n": 0}

    def _staged(**kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None
        return _stub_worker_envelope()

    monkeypatch.setattr(orch, "run_one_sample", _staged)

    run_record, _ = measure_url(url="https://example.com/", samples=1, emulation="mobile")
    page = run_record.pages[0]
    assert len(page.lcp_ms.samples) == 1
    assert call_count["n"] == 2  # initial + 1 retry


def test_all_samples_fail_raises_measurement_error(monkeypatch):
    """D-14 + D-15: all N samples fail → MeasurementError mentioning the sample count."""
    from perfcrawl.orchestrator import MeasurementError, measure_url
    import perfcrawl.orchestrator as orch

    _install_orchestrator_stubs(monkeypatch)
    monkeypatch.setattr(orch, "run_one_sample", lambda **kw: None)

    with pytest.raises(MeasurementError) as exc_info:
        measure_url(url="https://example.com/", samples=3, emulation="mobile")
    assert "3" in str(exc_info.value)
    assert "samples" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Security: Chrome lifecycle + tempdir cleanup (T-02-03-Z)
# ---------------------------------------------------------------------------


def test_chrome_killed_on_success(monkeypatch):
    """T-02-03-Z: chrome.kill() runs after a successful measure_url."""
    from perfcrawl.orchestrator import measure_url
    import perfcrawl.orchestrator as orch

    _, chrome_proc, _ = _install_orchestrator_stubs(monkeypatch)
    monkeypatch.setattr(orch, "run_one_sample", lambda **kw: _stub_worker_envelope())

    measure_url(url="https://example.com/", samples=1, emulation="mobile")
    assert chrome_proc.killed is True
    assert chrome_proc.waited is True  # CR-02: must reap, not just kill


def test_chrome_killed_on_failure(monkeypatch):
    """T-02-03-Z: chrome.kill() runs even when measure_url raises MeasurementError."""
    from perfcrawl.orchestrator import MeasurementError, measure_url
    import perfcrawl.orchestrator as orch

    _, chrome_proc, _ = _install_orchestrator_stubs(monkeypatch)
    monkeypatch.setattr(orch, "run_one_sample", lambda **kw: None)

    with pytest.raises(MeasurementError):
        measure_url(url="https://example.com/", samples=1, emulation="mobile")
    assert chrome_proc.killed is True
    assert chrome_proc.waited is True  # CR-02: must reap on failure too


def test_tempdir_cleaned_on_failure(monkeypatch, tmp_path):
    """T-02-03-Z: user_data_dir is cleaned up via shutil.rmtree even on failure."""
    from perfcrawl.orchestrator import MeasurementError, measure_url
    import perfcrawl.orchestrator as orch

    user_data_dir = tmp_path / "chrome-data"
    user_data_dir.mkdir()
    # Drop a marker file so we can prove the dir was removed.
    (user_data_dir / "marker").write_text("hi")

    _, chrome_proc, _ = _install_orchestrator_stubs(
        monkeypatch, tmp_user_data_dir=user_data_dir
    )
    monkeypatch.setattr(orch, "run_one_sample", lambda **kw: None)

    with pytest.raises(MeasurementError):
        measure_url(url="https://example.com/", samples=1, emulation="mobile")
    assert not user_data_dir.exists()
    # CR-02: the kill in measure_url's finally must be followed by wait().
    # _FakeChromeProc.waited flips True on .wait(timeout=...).
    assert chrome_proc.waited is True


# ---------------------------------------------------------------------------
# Pitfall 1: DevToolsActivePort polling + timeout
# ---------------------------------------------------------------------------


def test_devtools_port_polling(monkeypatch, tmp_path):
    """Pitfall 1: the port reader polls and reads DevToolsActivePort on attempt 3.

    Bypasses `_install_orchestrator_stubs` to exercise the real
    `_launch_chrome_with_cdp_port` polling logic. Mocks Popen + sync_playwright
    but lets the file-polling code path run.
    """
    import perfcrawl.orchestrator as orch

    user_data_dir = tmp_path / "chrome-port-test"
    user_data_dir.mkdir()
    fake_proc = _FakeChromeProc()

    # subprocess.Popen returns the fake process; tempfile.mkdtemp returns our dir.
    monkeypatch.setattr(orch.subprocess, "Popen", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(orch.tempfile, "mkdtemp", lambda prefix=None: str(user_data_dir))

    # Mock sync_playwright for executable_path lookup.
    fake_browser = _FakeBrowser()
    monkeypatch.setattr(orch, "sync_playwright", lambda: _FakePlaywrightCM(fake_browser))

    # Patch time.sleep to write the port file on attempt 3 (counted by call count).
    attempt = {"n": 0}
    original_sleep = orch.time.sleep

    def _fake_sleep(_seconds):
        attempt["n"] += 1
        if attempt["n"] == 3:
            (user_data_dir / "DevToolsActivePort").write_text("54321\n/devtools/browser/abc")

    monkeypatch.setattr(orch.time, "sleep", _fake_sleep)

    proc, port, returned_dir = orch._launch_chrome_with_cdp_port()
    try:
        assert port == 54321
        assert str(returned_dir) == str(user_data_dir)
    finally:
        # Restore + cleanup.
        monkeypatch.setattr(orch.time, "sleep", original_sleep)


def test_devtools_port_polling_checks_before_sleep(monkeypatch, tmp_path):
    """WR-05: the polling loop checks for DevToolsActivePort BEFORE the first sleep.

    Pre-fix shape:

        for _ in range(max_attempts):
            time.sleep(DEVTOOLS_PORT_POLL_INTERVAL_S)
            if port_file.exists(): ...

    paid one full ``DEVTOOLS_PORT_POLL_INTERVAL_S`` wait even when Chrome had
    already written the file before the loop started — which is the common
    case on fast machines. Post-fix the loop must check existence FIRST so a
    file present at t=0 returns without sleeping.

    Pin: monkeypatch ``time.monotonic`` to be deterministic, ensure the file
    exists before the launcher polls, and assert ``time.sleep`` is NEVER
    called.
    """
    import perfcrawl.orchestrator as orch

    user_data_dir = tmp_path / "chrome-port-fast"
    user_data_dir.mkdir()
    # Write the DevToolsActivePort file BEFORE the launcher polls.
    (user_data_dir / "DevToolsActivePort").write_text("12345\n/devtools/browser/abc")
    fake_proc = _FakeChromeProc()

    monkeypatch.setattr(orch.subprocess, "Popen", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(orch.tempfile, "mkdtemp", lambda prefix=None: str(user_data_dir))
    fake_browser = _FakeBrowser()
    monkeypatch.setattr(orch, "sync_playwright", lambda: _FakePlaywrightCM(fake_browser))

    sleep_calls = {"n": 0}

    def _tracked_sleep(_seconds):
        sleep_calls["n"] += 1

    monkeypatch.setattr(orch.time, "sleep", _tracked_sleep)

    proc, port, _ = orch._launch_chrome_with_cdp_port()
    assert port == 12345
    assert sleep_calls["n"] == 0, (
        f"polling slept {sleep_calls['n']} times even though port file was already "
        f"present at t=0; check-before-sleep regression"
    )


def test_devtools_port_timeout_raises(monkeypatch, tmp_path):
    """Pitfall 1: DevToolsActivePort never appears → MeasurementError."""
    import perfcrawl.orchestrator as orch

    user_data_dir = tmp_path / "chrome-port-timeout"
    user_data_dir.mkdir()
    fake_proc = _FakeChromeProc()

    monkeypatch.setattr(orch.subprocess, "Popen", lambda *a, **kw: fake_proc)
    monkeypatch.setattr(orch.tempfile, "mkdtemp", lambda prefix=None: str(user_data_dir))
    fake_browser = _FakeBrowser()
    monkeypatch.setattr(orch, "sync_playwright", lambda: _FakePlaywrightCM(fake_browser))
    # No-op sleep, so polling loops as fast as possible without ever finding the file.
    monkeypatch.setattr(orch.time, "sleep", lambda _s: None)
    # WR-05: deadline is monotonic-clock based; advance the fake clock past
    # ``DEVTOOLS_PORT_FILE_TIMEOUT_S`` after one iteration so the loop exits
    # quickly without waiting real wall-clock seconds.
    tick = {"n": 0}

    def _fake_monotonic():
        # First call sets deadline; subsequent calls jump past it.
        tick["n"] += 1
        return 0.0 if tick["n"] == 1 else 999.0

    monkeypatch.setattr(orch.time, "monotonic", _fake_monotonic)

    with pytest.raises(orch.MeasurementError) as exc_info:
        orch._launch_chrome_with_cdp_port()
    assert "DevToolsActivePort" in str(exc_info.value)
    # The fake Chrome process should have been killed on timeout.
    assert fake_proc.killed is True
    # CR-02: launcher-side reap on the timeout path too.
    assert fake_proc.waited is True
    # CR-03: the launcher's failure path is self-contained — the freshly
    # created user_data_dir must be removed BEFORE the raise propagates,
    # because the caller's `chrome, port, user_data_dir = ...` assignment
    # never completes and so the caller's finally cannot clean it up.
    assert not user_data_dir.exists()


# ---------------------------------------------------------------------------
# D-15: UserError on bad input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_url", ["", "   ", "\t\n"])
def test_user_error_on_malformed_url(monkeypatch, bad_url):
    """D-15: empty/whitespace URL raises UserError (CLI maps to ExitCode.USER_ERROR)."""
    from perfcrawl.orchestrator import UserError, measure_url

    # Don't install stubs — UserError should fire before Chrome is launched.
    with pytest.raises(UserError):
        measure_url(url=bad_url, samples=1, emulation="mobile")


def test_user_error_on_bad_samples(monkeypatch):
    """D-15: samples < 1 raises UserError."""
    from perfcrawl.orchestrator import UserError, measure_url

    with pytest.raises(UserError):
        measure_url(url="https://x.com/", samples=0, emulation="mobile")


def test_user_error_on_bad_emulation(monkeypatch):
    """D-15: emulation not in {'mobile','desktop'} raises UserError."""
    from perfcrawl.orchestrator import UserError, measure_url

    with pytest.raises(UserError):
        measure_url(url="https://x.com/", samples=1, emulation="tablet")


# ---------------------------------------------------------------------------
# RUN-02 + D-04: RunRecord metadata stamping from worker output
# ---------------------------------------------------------------------------


def test_runrecord_metadata_stamping(monkeypatch):
    """RUN-02 + D-04: RunRecord is stamped with chrome_version, lighthouse_version,
    throttling, and emulation from the first successful sample's lhr."""
    from perfcrawl.orchestrator import measure_url
    import perfcrawl.orchestrator as orch

    _install_orchestrator_stubs(monkeypatch)
    monkeypatch.setattr(orch, "run_one_sample", lambda **kw: _stub_worker_envelope())

    run_record, _ = measure_url(
        url="https://example.com/", samples=1, emulation="desktop"
    )
    assert run_record.chrome_version is not None
    assert "Chrome/137.0.7151.40" in run_record.chrome_version
    assert run_record.lighthouse_version == "13.3.0"
    assert run_record.throttling == {"rttMs": 150, "cpuSlowdownMultiplier": 4}
    assert run_record.emulation == "desktop"


# ---------------------------------------------------------------------------
# Source-level security guards (defense in depth)
# ---------------------------------------------------------------------------


def test_orchestrator_source_has_no_shell_invocation():
    """Defense-in-depth grep: no shell-invocation kwarg in orchestrator code."""
    import inspect
    import re

    import perfcrawl.orchestrator as orch

    src = inspect.getsource(orch)
    # Strip docstrings + line comments crudely: line.lstrip().startswith('#')
    # check for the source-level grep; the pattern below is the grep guard's regex.
    pattern = re.compile(r"shell\s*=\s*True")
    for lineno, line in enumerate(src.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if pattern.search(line):
            # Permit the pattern only inside string literals — the grep guard at
            # the plan level filters comments only; we mirror that here.
            assert False, f"shell-invocation kwarg present at line {lineno}: {line!r}"
