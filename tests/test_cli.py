"""Tests for ``perfcrawl.cli`` — Phase 2 CLI-01 / D-05 / D-06 / D-15 (Plan 02-04 Task 2).

The CLI is the controller layer: parse argv → orchestrator.measure_url →
write outputs → persist to SQLite → render stdout (Rich table OR JSON).

Test strategy:

  - Mock ``perfcrawl.cli.measure_url`` at the module-level so no real Chrome /
    Node subprocess launches; the orchestrator's tuple-return contract
    (RunRecord, raw_artifacts) is verified at the 02-03 boundary.
  - Use ``CliRunner(mix_stderr=False)`` so stderr never contaminates stdout
    capture — needed for the ``--json`` test where we ``json.loads(stdout)``.
  - Persistence is exercised against a real SQLite file under ``tmp_path``.
"""

import inspect
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from perfcrawl.auth import validate_storage_state
from perfcrawl.cli import app
from perfcrawl.constants import INP_PROXY_DISPLAY_LABEL, ExitCode
from perfcrawl.models import MetricSample, PageResult, RunRecord
from perfcrawl.orchestrator import MeasurementError, UserError

# Newer Click (>=8.2 via Typer 0.26+) split stdout/stderr by default and
# dropped the ``mix_stderr`` kwarg; ``result.stdout`` and ``result.stderr``
# are now separate streams without an opt-in flag.
runner = CliRunner()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_stub_run() -> RunRecord:
    """Build a minimal valid RunRecord with one page (the Phase 2 single-URL shape)."""
    return RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-0000000000c3"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target="https://example.com/",
        chrome_version="137.0.7151.40",
        lighthouse_version="13.3.0",
        emulation="mobile",
        pages=[
            PageResult(
                url="https://example.com/",
                url_key="https://example.com/",
                perf_score=92.0,
                a11y_score=95.0,
                seo_score=100.0,
                best_practices_score=100.0,
                lcp_ms=MetricSample(median=1234.0, samples=[1230.0, 1234.0, 1240.0]),
                cls=MetricSample(median=0.012, samples=[0.010, 0.012, 0.015]),
                inp_proxy_tbt_ms=MetricSample(median=42.0, samples=[40.0, 42.0, 45.0]),
                ttfb_ms=MetricSample(median=180.0, samples=[170.0, 180.0, 195.0]),
                request_count=18,
                total_bytes=345_600,
                status_code=200,
                slowest_request_url="https://example.com/static/main.js",
                slowest_request_ms=420.0,
            )
        ],
    )


def _make_stub_artifacts(run: RunRecord) -> dict[str, tuple[str, str]]:
    return {run.pages[0].url_key: ('{"lhr":{}}', "<html/>")}


def _patch_measure(monkeypatch: pytest.MonkeyPatch, return_value=None, side_effect=None) -> list:
    """Patch ``perfcrawl.cli.measure_url`` and record calls."""
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if side_effect is not None:
            raise side_effect
        return return_value

    monkeypatch.setattr("perfcrawl.cli.measure_url", fake)
    return calls


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #


def test_help_runs() -> None:
    """``--help`` succeeds and mentions the subcommand and key flags."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "measure" in result.stdout


def test_measure_help_runs() -> None:
    """``measure --help`` lists the URL argument, --samples, --emulation, --json, --output-dir."""
    result = runner.invoke(app, ["measure", "--help"])
    assert result.exit_code == 0
    assert "--samples" in result.stdout
    assert "--emulation" in result.stdout
    assert "--json" in result.stdout
    assert "--output-dir" in result.stdout


def test_no_args_shows_help() -> None:
    """Bare invocation triggers Typer's ``no_args_is_help`` behavior."""
    result = runner.invoke(app, [])
    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Exit code mapping (D-15)
# --------------------------------------------------------------------------- #


def test_exit_zero_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _make_stub_run()
    artifacts = _make_stub_artifacts(run)
    _patch_measure(monkeypatch, return_value=(run, artifacts))
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr


def test_exit_one_on_user_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_measure(monkeypatch, side_effect=UserError("URL is empty"))
    result = runner.invoke(app, ["measure", "", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.USER_ERROR
    # Error message surfaces on stderr per D-06.
    assert "URL is empty" in result.stderr or "URL is empty" in result.stdout


def test_exit_two_on_measurement_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_measure(monkeypatch, side_effect=MeasurementError("all 3 samples failed"))
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.MEASUREMENT_ERROR
    assert "samples failed" in result.stderr or "samples failed" in result.stdout


def test_exit_one_when_output_dir_unwriteable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """OSError from write_outputs maps to ExitCode.USER_ERROR per D-15."""
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    # output_dir points under a regular file → mkdir raises NotADirectoryError.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(blocker / "out")]
    )
    assert result.exit_code == ExitCode.USER_ERROR


# --------------------------------------------------------------------------- #
# --json flag (D-06): stdout is parseable JSON; stderr carries progress.
# --------------------------------------------------------------------------- #


def test_json_flag_emits_valid_json_on_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--json",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS
    # The whole of stdout must parse as JSON — no Rich table allowed.
    data = json.loads(result.stdout)
    assert data["id"] == str(run.id)
    assert "pages" in data
    assert "started_at" in data
    assert "schema_version" in data


def test_no_json_flag_emits_rich_table_on_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default mode prints a human-readable Rich table with the labeled INP row."""
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.SUCCESS
    # Not valid JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
    # D-11/D-15: the labeled-proxy row label appears verbatim in stdout.
    assert INP_PROXY_DISPLAY_LABEL in result.stdout


def test_inp_label_visible_in_rich_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The Rich row label reads the constants string verbatim (defense in depth)."""
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.SUCCESS
    assert INP_PROXY_DISPLAY_LABEL in result.stdout
    # The TBT median (42) appears alongside the label.
    assert "42" in result.stdout


# --------------------------------------------------------------------------- #
# Persistence: the SQLite store under output_dir gets one row per run (HIST-01).
# --------------------------------------------------------------------------- #


def test_persistence_writes_to_sqlite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.SUCCESS

    db_path = tmp_path / "perfcrawl.db"
    assert db_path.exists(), f"SQLite db missing at {db_path}"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id FROM runs WHERE id = ?", (str(run.id),)).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# On-disk layout (D-07 / OUT-03 / OUT-04)
# --------------------------------------------------------------------------- #


def test_on_disk_layout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``<output_dir>/<run_id>/{result.json,result.csv,lighthouse/*.json,*.html}`` all exist."""
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.SUCCESS

    run_dir = tmp_path / str(run.id)
    assert (run_dir / "result.json").exists()
    assert (run_dir / "result.csv").exists()
    lh_dir = run_dir / "lighthouse"
    assert lh_dir.exists()
    assert any(p.suffix == ".json" for p in lh_dir.iterdir())
    assert any(p.suffix == ".html" for p in lh_dir.iterdir())


# --------------------------------------------------------------------------- #
# Defense-in-depth: source-level grep meta-test for the labeling invariant.
# Mirrors normalizer.py's labeled-proxy floor (Phase 2 plan 01 Task 3).
# --------------------------------------------------------------------------- #


def test_cli_source_has_no_bare_inp() -> None:
    """``cli.py`` references INP only via ``INP_PROXY_DISPLAY_LABEL`` / ``inp_proxy_tbt_ms``."""
    import perfcrawl.cli as cli_module

    src = inspect.getsource(cli_module)
    assert "INP_PROXY_DISPLAY_LABEL" in src, "cli.py must reference the labeled constant"
    # Bare \binp\b (not followed by _proxy) — case-insensitive guard so a
    # hand-coded "INP" or "Inp" in a table row label gets caught too.
    bare = re.findall(r"\binp\b(?!_proxy)", src, flags=re.IGNORECASE)
    assert bare == [], f"bare-INP token in cli.py source: {bare}"


def test_cli_imports_exit_code_constant() -> None:
    """``cli.py`` reads ``ExitCode`` from constants — never inlines 0/1/2."""
    import perfcrawl.cli as cli_module

    src = inspect.getsource(cli_module)
    assert "ExitCode" in src


def test_render_human_table_handles_empty_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """IN-02: ``_render_human_table`` must not raise IndexError on a zero-page RunRecord.

    The orchestrator currently guarantees ``len(run.pages) >= 1`` because it
    raises ``MeasurementError`` when all samples fail. A future Phase 3
    regression that builds a zero-page RunRecord (e.g. a multi-page crawl
    where every page failed but the RunRecord was still constructed) would
    crash ``cli._render_human_table`` with a bare ``IndexError`` instead of a
    clean message. Guard the indexing so the CLI degrades gracefully.

    Pin: build a stub RunRecord with ``pages=[]`` and assert the CLI exits 0
    with a "no pages" notice on stdout instead of ``IndexError``.
    """
    empty_run = RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-0000000000e3"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target="https://example.com/",
        chrome_version="137.0.7151.40",
        lighthouse_version="13.3.0",
        emulation="mobile",
        pages=[],  # IN-02: this is the regression vector.
    )
    _patch_measure(monkeypatch, return_value=(empty_run, {}))
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    # Should not raise IndexError — exit cleanly (success, with a notice).
    assert result.exit_code == ExitCode.SUCCESS, (
        f"empty-pages RunRecord crashed the CLI: "
        f"stdout={result.stdout!r} stderr={result.stderr!r} exc={result.exception!r}"
    )
    # A clean message tells the user there was nothing to render.
    assert "No pages measured" in result.stdout


# --------------------------------------------------------------------------- #
# perfcrawl login — D-04 SSO/MFA escape hatch (UAT test-6 regression)
#
# Root cause (04-UAT.md, test 6): login()'s finally runs
# ``os.killpg(os.getpgid(chrome.pid), 15)`` to reap an orphaned headed Chrome,
# but ``_launch_chrome_with_cdp_port`` Popen'd Chrome WITHOUT
# ``start_new_session=True`` — so Chrome shared perfcrawl's process group and the
# killpg SIGTERM'd perfcrawl itself, AFTER the session was captured but BEFORE
# validate + the owner-only file write. The shell returned clean, no file, no
# error. The existing unit suite stubbed the launch/teardown, so the real
# killpg-against-a-shared-group path never executed in test — this section
# closes that blind spot. Default (non-e2e) suite: no real Chrome / Node /
# network. Credential-safety invariant: the fabricated session carries a fake
# ``sessionid`` cookie only — never a username/password literal.
# --------------------------------------------------------------------------- #


class _FakePage:
    """Stand-in Playwright page: goto is a no-op."""

    def goto(self, url, wait_until=None):  # noqa: ARG002 — signature parity
        return None


class _FakeContext:
    """Stand-in DEFAULT context: new_page + a VALID minimal storage_state."""

    def new_page(self):
        return _FakePage()

    def storage_state(self):
        # Valid per validate_storage_state (>=1 cookie). Fake sessionid only —
        # no credential literal ever appears in the captured artifact.
        return {"cookies": [{"name": "sessionid", "value": "x"}], "origins": []}

    def close(self):  # pragma: no cover — login() does not call ctx.close()
        return None


class _FakeBrowser:
    """Stand-in CDP browser: exposes a single DEFAULT context, close is a no-op."""

    def __init__(self):
        self.contexts = [_FakeContext()]

    def close(self):
        return None


class _FakeChromium:
    def connect_over_cdp(self, url):  # noqa: ARG002 — signature parity
        return _FakeBrowser()


class _FakeSyncPlaywright:
    """Context manager mimicking ``sync_playwright()`` → object with ``.chromium``."""

    def __enter__(self):
        obj = type("PW", (), {})()
        obj.chromium = _FakeChromium()
        return obj

    def __exit__(self, *exc):
        return False


def _fake_sync_playwright():
    return _FakeSyncPlaywright()


def test_launch_isolation_killpg_does_not_kill_parent() -> None:
    """Test A (launch-side isolation): a child Popen'd with start_new_session=True
    is its own process-group leader, and killpg on its group leaves THIS process
    (the perfcrawl stand-in) alive.

    This is the invariant ``_launch_chrome_with_cdp_port`` must guarantee so that
    login()'s ``os.killpg(os.getpgid(chrome.pid), ...)`` targets only Chrome's
    group, never perfcrawl's.
    """
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # The child is its own session/process-group leader, distinct from ours.
        assert os.getpgid(child.pid) == child.pid
        assert os.getpgid(child.pid) != os.getpgid(0)

        # killpg the child's group — SIGTERM must NOT reach this process.
        os.killpg(os.getpgid(child.pid), signal.SIGTERM)
    finally:
        # Reap to avoid a zombie regardless of how the assertions land.
        child.wait()

    # Reaching here proves the parent (perfcrawl stand-in) survived the killpg.
    assert child.poll() is not None


def test_login_escape_hatch_writes_session_and_parent_survives_killpg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test B (real login teardown, real child group, real killpg): drive the
    REAL ``perfcrawl.cli.login`` Click command so its ``finally`` block runs the
    real ``os.killpg(os.getpgid(chrome.pid), 15)`` against a REAL isolated child
    process group — the precise blind spot the stubbed-launch suite missed.

    Before Task 2 (orchestrator launches Chrome WITHOUT start_new_session=True)
    the real login() flow self-terminates via the killpg and the --out file is
    never written. After the fix the test process survives and the file is
    written + validates.
    """
    out = tmp_path / "session.authstate.json"
    spawned: list = []  # captures the stand-in child for the post-run liveness check

    # Couple the stand-in's launch to the REAL launcher's behavior so this is a
    # genuine regression, not a tautology: the child is spawned with the SAME
    # ``start_new_session`` value the real ``_launch_chrome_with_cdp_port`` uses.
    # Before Task 2 the orchestrator source lacks ``start_new_session=True`` →
    # the child shares THIS test process's group → login()'s real killpg SIGTERMs
    # the test process before the file is written (UAT test-6 reproduced: the
    # CliRunner subprocess of pytest dies / the --out file is never written).
    # After Task 2 the source carries the flag → the child is its own group
    # leader → the killpg is isolated and the file IS written + validates.
    import perfcrawl.orchestrator as _orch

    _launcher_uses_new_session = "start_new_session=True" in inspect.getsource(
        _orch._launch_chrome_with_cdp_port
    )

    def fake_launch(headless: bool = True):  # noqa: ARG001 — signature parity
        # Spawn a real long-sleeping child EXACTLY the way the real launcher does
        # (same start_new_session disposition). The real login() finally will
        # ``os.killpg(os.getpgid(child.pid), 15)`` this child's group.
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=_launcher_uses_new_session,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        spawned.append(child)
        user_data_dir = tmp_path / "chrome-user-data"
        user_data_dir.mkdir(parents=True, exist_ok=True)
        # Port is unused — the fake CDP browser ignores it.
        return child, 0, user_data_dir

    # login() does ``from playwright.sync_api import sync_playwright`` locally, so
    # the local import binds to playwright.sync_api.sync_playwright at call time.
    monkeypatch.setattr("playwright.sync_api.sync_playwright", _fake_sync_playwright)
    monkeypatch.setattr("perfcrawl.cli._launch_chrome_with_cdp_port", fake_launch)

    result = runner.invoke(
        app, ["login", "https://example.com/login/", "--out", str(out)], input="\n"
    )

    # 1. The command completed and the result is reachable — the test process
    #    (perfcrawl stand-in) was NOT killed by the teardown killpg. If the
    #    killpg had targeted the shared group, this process would have died
    #    before we could assert. (Before Task 2: the real launcher shares the
    #    group, so this is the assertion that flips RED→GREEN.)
    assert result.exit_code == ExitCode.SUCCESS, (
        f"login did not exit cleanly: exit={result.exit_code} "
        f"stdout={result.stdout!r} stderr={result.stderr!r} exc={result.exception!r}"
    )

    # 2. The --out file exists, parses as JSON, and validates.
    assert out.exists(), f"--out session file was not written at {out}"
    state = json.loads(out.read_text())
    validate_storage_state(state)  # raises AuthError if invalid

    # 3. WR-04 preserved: the session file is owner-only (0o600).
    assert oct(out.stat().st_mode & 0o777) == "0o600"

    # 4. The stand-in child (headed-Chrome proxy) was killed by the teardown
    #    killpg. _teardown_chrome (chrome.kill() + chrome.wait()) reaps it, so
    #    poll() is not None once login() returned.
    assert spawned, "fake_launch was never invoked — login() did not launch Chrome"
    child = spawned[0]
    assert child.poll() is not None, "stand-in Chrome child survived the teardown killpg"
