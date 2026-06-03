"""Tests for ``perfcrawl crawl`` — CRAWL-01/CRAWL-04 + D-04/D-06/D-11/D-15.

The ``crawl`` command is the end-to-end controller (sibling of ``measure``):
discover → (--dry-run? print : measure_pass) → write_outputs → init_db/write_run
→ multi-page render. It reuses the Phase-2 ``measure_url`` / ``write_outputs`` /
``write_run`` seams unchanged.

Test strategy (mirrors ``tests/test_cli.py``):

  - Monkeypatch ``perfcrawl.cli.measure_url`` to a canned single-page RunRecord
    so no real Chrome/Node launches; the measurement pass loops it per URL.
  - Drive discovery against the ``local_server`` fixture (a real-but-local HTTP
    origin serving ``tests/crawl/fixtures/site/``) so the BFS + scope + filters
    run for real with no network.
  - ``CliRunner`` splits stdout/stderr so the ``--ignore-robots`` warning
    (stderr) and the dry-run list (stdout) are asserted independently.
  - Persistence runs against a real SQLite file under ``tmp_path``.

D-04: ``--dry-run`` measures nothing (``measure_url`` asserted NOT called).
D-15: exit codes reuse ``ExitCode`` (UserError→1, measurement collapse→2, 0 else).
"""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from typer.testing import CliRunner

from perfcrawl.cli import app
from perfcrawl.constants import ExitCode
from perfcrawl.models import MetricSample, PageResult, RunRecord

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _canned_run(url: str, samples: int = 1, emulation: str = "mobile"):
    """A canned one-page (RunRecord, raw_artifacts) — the measure_url contract."""
    from perfcrawl.canonical import canonical_key

    key = canonical_key(url)
    page = PageResult(
        url=url,
        url_key=key,
        perf_score=90.0,
        lcp_ms=MetricSample(median=1200.0, samples=[1200.0]),
        status_code=200,
    )
    run = RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-0000000000a1"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target=url,
        chrome_version="137.0.7151.40",
        lighthouse_version="13.3.0",
        emulation="mobile",
        pages=[page],
    )
    return run, {key: ('{"lhr":{}}', "<html/>")}


def _patch_measure(monkeypatch, *, side_effect=None) -> list:
    """Patch the measure_url seam the crawl actually invokes; record every call.

    ``measure_pass`` calls ``perfcrawl.crawl.measure_pass.measure_url`` (not
    ``perfcrawl.cli.measure_url`` — the CLI delegates measurement entirely to the
    pool driver). Patch THAT symbol so no real Chrome/Node launches and the
    "measured nothing under --dry-run" assertion is meaningful (dry-run never
    reaches the pool, so this fake is never called).
    """
    calls: list[str] = []

    def fake(*, url, samples=1, emulation="mobile", auth_state=None):
        # auth_state: Plan 04-01 extended the measure_url seam with the replayed
        # session kwarg, and Plan 04-03 threads it through the pool — the fake must
        # accept it (defaulting None for these public-crawl CLI tests) or every
        # call TypeErrors into a tagged error row and the crawl reports 0 measured.
        calls.append(url)
        if side_effect is not None:
            raise side_effect
        return _canned_run(url, samples, emulation)

    monkeypatch.setattr("perfcrawl.crawl.measure_pass.measure_url", fake)
    return calls


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #


def test_crawl_help_lists_flags() -> None:
    """``crawl --help`` lists the full flag surface and notes the JS-link limit."""
    result = runner.invoke(app, ["crawl", "--help"])
    assert result.exit_code == 0
    for flag in (
        "--max-pages",
        "--max-depth",
        "--concurrency",
        "--delay",
        "--samples",
        "--include",
        "--exclude",
        "--dry-run",
        "--ignore-robots",
        "--emulation",
        "--output-dir",
        "--json",
    ):
        assert flag in result.stdout, f"missing flag in help: {flag}"
    # D-02 JS-link limitation documented in the help text.
    assert "javascript" in result.stdout.lower() or "js" in result.stdout.lower()


# --------------------------------------------------------------------------- #
# D-04: --dry-run discovers only, measures nothing
# --------------------------------------------------------------------------- #


def test_dry_run(monkeypatch, tmp_path: Path, local_server: str) -> None:
    """``crawl <url> --dry-run`` prints the in-scope list and measures nothing."""
    calls = _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--dry-run",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    # D-04: NOTHING was measured.
    assert calls == [], f"measure_url called under --dry-run: {calls}"
    # The in-scope index page is listed on stdout.
    assert local_server + "/index.html" in result.stdout
    # No run dir was written (discovery-only).
    assert not any(p.is_dir() for p in tmp_path.iterdir() if p.name != "perfcrawl.db")


# --------------------------------------------------------------------------- #
# Multi-page output (D-15): one row per page CSV + per-page LH artifacts
# --------------------------------------------------------------------------- #


def test_multipage_output(monkeypatch, tmp_path: Path, local_server: str) -> None:
    """A real crawl writes result.csv (one row/page) + per-page lighthouse artifacts."""
    _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr

    # Exactly one run dir under tmp_path (named by run id).
    run_dirs = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1, run_dirs
    run_dir = run_dirs[0]

    csv_path = run_dir / "result.csv"
    json_path = run_dir / "result.json"
    assert csv_path.exists()
    assert json_path.exists()

    # CSV is one row per page (header + >= 2 data rows for the fixture site).
    with csv_path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) >= 2, f"expected one row per page, got {len(rows)}"
    # Each measured page has a url cell.
    assert all(r["url"] for r in rows)

    # Per-page lighthouse artifacts written via the reused write_outputs.
    lh_dir = run_dir / "lighthouse"
    assert lh_dir.exists()
    assert any(p.suffix == ".json" for p in lh_dir.iterdir())
    assert any(p.suffix == ".html" for p in lh_dir.iterdir())

    # Persisted to SQLite alongside the artifacts (one run row).
    import sqlite3

    db = tmp_path / "perfcrawl.db"
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        run_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert run_count == 1
    finally:
        conn.close()


def test_json_flag_emits_multipage_json(monkeypatch, tmp_path: Path, local_server: str) -> None:
    """``--json`` prints the full multi-page RunRecord JSON to stdout (D-06)."""
    _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--json",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert "pages" in data
    assert len(data["pages"]) >= 2


# --------------------------------------------------------------------------- #
# D-11: --ignore-robots loud warning to stderr (not stdout)
# --------------------------------------------------------------------------- #


def test_ignore_robots_warns_on_stderr(monkeypatch, tmp_path: Path, local_server: str) -> None:
    """``--ignore-robots`` emits a loud warning to stderr, not stdout (D-11/D-06)."""
    _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--ignore-robots",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    # The warning is on stderr (machine-readable stdout stays clean).
    assert "robots" in result.stderr.lower()
    assert "ignore" in result.stderr.lower() or "warn" in result.stderr.lower()


# --------------------------------------------------------------------------- #
# Exit codes (D-15)
# --------------------------------------------------------------------------- #


def test_exit_two_when_all_pages_fail(monkeypatch, tmp_path: Path, local_server: str) -> None:
    """Every page failing to measure → MEASUREMENT_ERROR (2) with a stderr note."""
    from perfcrawl.orchestrator import MeasurementError

    _patch_measure(monkeypatch, side_effect=MeasurementError("all samples failed"))
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.MEASUREMENT_ERROR, result.stdout + result.stderr


def test_exit_one_on_empty_url(monkeypatch, tmp_path: Path) -> None:
    """An empty seed URL is a user error (1) before any measurement."""
    calls = _patch_measure(monkeypatch)
    result = runner.invoke(app, ["crawl", "", "--output-dir", str(tmp_path)])
    assert result.exit_code == ExitCode.USER_ERROR
    assert calls == []


# --------------------------------------------------------------------------- #
# Source-level: command is a sibling of measure + reads ExitCode from constants
# --------------------------------------------------------------------------- #


def test_crawl_is_registered() -> None:
    """``crawl`` is a sibling command of ``measure`` on the same app."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "crawl" in result.stdout
    assert "measure" in result.stdout


# --------------------------------------------------------------------------- #
# Phase 4 (AUTH-01/AUTH-04): auth/deny flags, env-only creds, AUTH_ERROR exit
# --------------------------------------------------------------------------- #


def _crawl_command_params() -> dict:
    """Introspect the ``crawl`` command's parameters by name → Click Parameter."""
    from typer.main import get_command

    group = get_command(app)  # a Click Group with `measure`/`crawl` subcommands
    crawl_cmd = group.commands["crawl"]
    return {p.name: p for p in crawl_cmd.params}


def test_crawl_has_auth_and_deny_flags() -> None:
    """``crawl --help`` exposes the new auth + deny flags (D-01/D-05)."""
    result = runner.invoke(app, ["crawl", "--help"])
    assert result.exit_code == 0
    for flag in ("--login-url", "--user-sel", "--pass-sel", "--submit-sel",
                 "--auth-state", "--deny", "--success-text", "--success-url"):
        assert flag in result.stdout, f"missing auth/deny flag: {flag}"


def test_crawl_has_no_password_option() -> None:
    """T-04-10: the password (and username) must NEVER be a Typer/CLI Option.

    argv is visible in ``ps``/shell history, so credentials are env-only (D-07).
    Introspect the command's parameters and assert no option carries a
    password/username — the only credential intake is ``os.environ``.
    """
    params = _crawl_command_params()
    # No parameter NAME mentions password/username.
    for name in params:
        assert "password" not in name.lower(), f"forbidden password param: {name}"
        assert "username" not in name.lower(), f"forbidden username param: {name}"
    # No option string flags a password/username either.
    for param in params.values():
        for opt in getattr(param, "opts", []) + getattr(param, "secondary_opts", []):
            low = opt.lower()
            assert "password" not in low and "passwd" not in low, f"forbidden opt: {opt}"
            assert "username" not in low, f"forbidden opt: {opt}"


def test_auth_error_exits_three(monkeypatch, tmp_path: Path, local_server: str) -> None:
    """A failed auth resolution maps to ExitCode.AUTH_ERROR (3) (D-15)."""
    from perfcrawl.auth import AuthError

    _patch_measure(monkeypatch)

    def _boom(*args, **kwargs):
        raise AuthError("login could not be confirmed")

    # Patch the CLI's auth resolver so no real Chrome launches.
    monkeypatch.setattr("perfcrawl.cli._resolve_crawl_auth", _boom)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--login-url",
            local_server + "/login/",
            "--user-sel",
            "#username",
            "--pass-sel",
            "#password",
            "--submit-sel",
            "#submit",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.AUTH_ERROR, result.stdout + result.stderr


def test_non_autherror_leak_is_scrubbed_and_exits_three(
    monkeypatch, tmp_path: Path, local_server: str
) -> None:
    """Defense-in-depth (Task 2 / AUTH-04): a NON-AuthError leak from the
    auth-resolution path is scrubbed and mapped to AUTH_ERROR, with neither the
    sentinel username nor password literal in the combined output."""
    sentinel_user = "SENTINEL_USER"
    sentinel_pass = "SENTINEL_PASS"

    # Creds enter via env ONLY (D-07); this seeds the CLI's scrubber.
    monkeypatch.setenv("PERFCRAWL_USERNAME", sentinel_user)
    monkeypatch.setenv("PERFCRAWL_PASSWORD", sentinel_pass)

    _patch_measure(monkeypatch)

    def _leak(*args, **kwargs):
        # A raw (non-AuthError) exception carrying the live password substring —
        # exactly the wrong-selector Playwright failure shape before CR-01, or
        # any future leak. The catch-all must redact it.
        raise RuntimeError(f"boom user={sentinel_user} pw={sentinel_pass}")

    monkeypatch.setattr("perfcrawl.cli._resolve_crawl_auth", _leak)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--login-url",
            local_server + "/login/",
            "--user-sel",
            "#username",
            "--pass-sel",
            "#password",
            "--submit-sel",
            "#submit",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert result.exit_code == int(ExitCode.AUTH_ERROR), combined
    assert sentinel_user not in combined
    assert sentinel_pass not in combined


def test_auth_state_and_login_url_mutually_exclusive(
    monkeypatch, tmp_path: Path, local_server: str
) -> None:
    """``--auth-state`` + ``--login-url`` together is a user error (D-01)."""
    calls = _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--login-url",
            local_server + "/login/",
            "--auth-state",
            str(tmp_path / "session.authstate.json"),
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.USER_ERROR
    assert calls == []  # never reached measurement


# --------------------------------------------------------------------------- #
# URL truncation for the crawl summary table (260603-fos)
#
# A whole-site crawl shares one origin, so the per-row URL collapses to its
# path (the origin lives in the table header). ``_relativize_url`` strips the
# shared origin; off-origin / malformed URLs fall back to the full URL.
# --------------------------------------------------------------------------- #


def test_origin_of_extracts_scheme_and_host() -> None:
    from perfcrawl.cli import _origin_of

    assert _origin_of("https://www.studyhalo.com/courses?p=2") == ("https://www.studyhalo.com")
    # No scheme+host → returned unchanged so callers degrade safely.
    assert _origin_of("not-a-url") == "not-a-url"


def test_relativize_strips_shared_origin() -> None:
    from perfcrawl.cli import _relativize_url

    origin = "https://www.studyhalo.com"
    assert _relativize_url("https://www.studyhalo.com/courses", origin) == "/courses"
    # Query and fragment are preserved on the path.
    assert (
        _relativize_url("https://www.studyhalo.com/courses?page=2#top", origin)
        == "/courses?page=2#top"
    )


def test_relativize_root_collapses_to_slash() -> None:
    from perfcrawl.cli import _relativize_url

    origin = "https://www.studyhalo.com"
    assert _relativize_url("https://www.studyhalo.com/", origin) == "/"
    # Origin with no path component still renders the root as "/".
    assert _relativize_url("https://www.studyhalo.com", origin) == "/"


def test_relativize_falls_back_for_off_origin_and_lookalike_hosts() -> None:
    from perfcrawl.cli import _relativize_url

    origin = "https://www.studyhalo.com"
    # Genuinely different origin → full URL.
    assert (
        _relativize_url("https://cdn.other.com/asset.js", origin)
        == "https://cdn.other.com/asset.js"
    )
    # Prefix-lookalike host must NOT be treated as on-origin (host compared
    # structurally, not by string prefix).
    assert (
        _relativize_url("https://www.studyhalo.com.evil.com/x", origin)
        == "https://www.studyhalo.com.evil.com/x"
    )
    # Unparseable / scheme-relative input falls back to the raw value.
    assert _relativize_url("javascript:void(0)", origin) == "javascript:void(0)"


def test_crawl_summary_table_shows_relative_paths(
    monkeypatch, tmp_path: Path, local_server: str
) -> None:
    """End-to-end: rows render the page path, not the full URL."""
    _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    origin = local_server.rstrip("/")
    # The seed page renders as its relative path...
    assert "/index.html" in result.stdout
    # ...and the full per-row URL (origin + path) is truncated away.
    assert f"{origin}/index.html" not in result.stdout
