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

    def fake(*, url, samples=1, emulation="mobile"):
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


def test_json_flag_emits_multipage_json(
    monkeypatch, tmp_path: Path, local_server: str
) -> None:
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


def test_ignore_robots_warns_on_stderr(
    monkeypatch, tmp_path: Path, local_server: str
) -> None:
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


def test_exit_two_when_all_pages_fail(
    monkeypatch, tmp_path: Path, local_server: str
) -> None:
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
    assert result.exit_code == ExitCode.MEASUREMENT_ERROR, (
        result.stdout + result.stderr
    )


def test_exit_one_on_empty_url(monkeypatch, tmp_path: Path) -> None:
    """An empty seed URL is a user error (1) before any measurement."""
    calls = _patch_measure(monkeypatch)
    result = runner.invoke(
        app, ["crawl", "", "--output-dir", str(tmp_path)]
    )
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
