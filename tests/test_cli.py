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
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from typer.testing import CliRunner

from perfcrawl.cli import app
from perfcrawl.constants import ExitCode, INP_PROXY_DISPLAY_LABEL
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
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr


def test_exit_one_on_user_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_measure(monkeypatch, side_effect=UserError("URL is empty"))
    result = runner.invoke(
        app, ["measure", "", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.USER_ERROR
    # Error message surfaces on stderr per D-06.
    assert "URL is empty" in result.stderr or "URL is empty" in result.stdout


def test_exit_two_on_measurement_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_measure(monkeypatch, side_effect=MeasurementError("all 3 samples failed"))
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.MEASUREMENT_ERROR
    assert (
        "samples failed" in result.stderr
        or "samples failed" in result.stdout
    )


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
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.SUCCESS
    # Not valid JSON.
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
    # D-11/D-15: the labeled-proxy row label appears verbatim in stdout.
    assert INP_PROXY_DISPLAY_LABEL in result.stdout


def test_inp_label_visible_in_rich_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Rich row label reads the constants string verbatim (defense in depth)."""
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.SUCCESS
    assert INP_PROXY_DISPLAY_LABEL in result.stdout
    # The TBT median (42) appears alongside the label.
    assert "42" in result.stdout


# --------------------------------------------------------------------------- #
# Persistence: the SQLite store under output_dir gets one row per run (HIST-01).
# --------------------------------------------------------------------------- #


def test_persistence_writes_to_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _make_stub_run()
    _patch_measure(monkeypatch, return_value=(run, _make_stub_artifacts(run)))
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.SUCCESS

    db_path = tmp_path / "perfcrawl.db"
    assert db_path.exists(), f"SQLite db missing at {db_path}"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM runs WHERE id = ?", (str(run.id),)
        ).fetchall()
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
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(tmp_path)]
    )
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
