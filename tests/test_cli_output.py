"""RED ``--output`` / fail-fast / exit-code tests — Phase 6 OUT-01 + D-14.

Wave-0 INTERFACE-FIRST contract. These tests are AUTHORED BEFORE the implementation
and MUST fail until Plans 05 + 06 land ``sheets.py`` + the CLI ``--output`` /
``--sheets-id`` wiring. "RED-as-expected" is the success condition — do NOT implement
production code to green them.

Today the ``--output`` / ``--sheets-id`` Options do not exist, so every invocation
that passes ``--output …`` aborts with Typer's usage error (exit code 2), and the
default-path test cannot even patch ``perfcrawl.sheets.write_sheets`` (the module is
absent). Both are the RED signal.

The contract they lock for Plans 05/06 (D-05/D-06/D-07/D-10/D-14):

  - ``--output`` is a comma-list (e.g. ``--output sheets,csv,json``); tokens are the
    closed set ``{csv, json, sheets, artifacts}``. An unknown token fails fast at t=0
    with ``ExitCode.USER_ERROR`` (1) BEFORE any measurement (D-05/D-07).
  - Default when ``--output`` is omitted = ``csv,json`` — no surprise network writes;
    ``sheets`` only when explicitly named (D-06). The Sheets writer is never called and
    no service-account env is read on the default path.
  - Each token independently selects its writer (``json`` writes result.json but not
    result.csv; ``sheets`` calls ``write_sheets``) (D-07).
  - ``--output sheets`` with ``PERFCRAWL_SHEETS_SA`` unset fails fast at t=0 with
    ``USER_ERROR`` — and ONLY when ``sheets`` is selected (D-10).
  - A flagged regression NEVER changes the process exit code — flags are informational
    only (D-14; BUDG-01 is v2).

These tests RELY on Plan 06-02's hermetic-fixture extension (the autouse
``_hermetic_provider_env`` in conftest.py clears ``PERFCRAWL_SHEETS_SA`` for non-``llm``
runs) and do NOT edit conftest.py themselves.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from typer.testing import CliRunner

from perfcrawl.cli import app
from perfcrawl.constants import ExitCode
from perfcrawl.models import MetricSample, PageResult, RunRecord

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Helpers — patch the measure seam so nothing touches Chrome/Node/network.
# --------------------------------------------------------------------------- #


def _make_stub_run(perf_score: float = 92.0, lcp_median: float = 1234.0) -> RunRecord:
    """A minimal valid single-page RunRecord (the measure_url contract)."""
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
                perf_score=perf_score,
                lcp_ms=MetricSample(median=lcp_median, samples=[lcp_median]),
                status_code=200,
            )
        ],
    )


def _make_stub_artifacts(run: RunRecord) -> dict[str, tuple[str, str]]:
    return {run.pages[0].url_key: ('{"lhr":{}}', "<html/>")}


def _patch_measure(monkeypatch: pytest.MonkeyPatch, runs=None) -> list:
    """Patch ``perfcrawl.cli.measure_url``; record calls. ``runs`` (optional) is a list
    of RunRecords returned on successive calls (else a fresh stub each time)."""
    calls: list = []
    seq = list(runs) if runs is not None else None

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        run = seq[len(calls) - 1] if seq is not None else _make_stub_run()
        return run, _make_stub_artifacts(run)

    monkeypatch.setattr("perfcrawl.cli.measure_url", fake)
    return calls


def _patch_write_sheets(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch ``perfcrawl.sheets.write_sheets`` to a recorder (``.call_count``).

    RED today: ``perfcrawl.sheets`` does not exist, so this ``setattr`` raises
    ``ModuleNotFoundError`` — the deliberate Wave-0 failure for the Sheets-path tests."""
    mock = MagicMock(return_value=None)
    monkeypatch.setattr("perfcrawl.sheets.write_sheets", mock)
    return mock


# --------------------------------------------------------------------------- #
# D-05 / D-07: unknown --output token fails fast at t=0 with USER_ERROR.
# --------------------------------------------------------------------------- #


def test_unknown_output_token_user_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--output csv,bogus`` exits USER_ERROR at t=0 — BEFORE any measurement."""
    measure_calls = _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        ["measure", "https://example.com", "--output", "csv,bogus", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == int(ExitCode.USER_ERROR), result.stdout + result.stderr
    assert measure_calls == [], "measurement must never run on the unknown-token fail-fast"


# --------------------------------------------------------------------------- #
# D-06: --output omitted → default csv,json → Sheets writer NEVER called.
# --------------------------------------------------------------------------- #


def test_default_no_sheets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ``--output`` → default ``csv,json``; the Sheets writer is never invoked and
    no service-account env is read (D-06; the autouse hermetic fixture clears the SA env)."""
    _patch_measure(monkeypatch)
    write_sheets = _patch_write_sheets(monkeypatch)
    result = runner.invoke(
        app, ["measure", "https://example.com", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert write_sheets.call_count == 0, "default path must never touch Sheets (D-06)"


# --------------------------------------------------------------------------- #
# D-07: each token independently selects its writer.
# --------------------------------------------------------------------------- #


def test_token_selects_writer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--output json`` writes result.json but NOT result.csv; ``--output sheets``
    calls write_sheets (each token selects exactly its writer, D-07)."""
    run = _make_stub_run()

    # json-only: result.json present, result.csv absent, no Sheets call.
    _patch_measure(monkeypatch, runs=[run])
    write_sheets = _patch_write_sheets(monkeypatch)
    json_dir = tmp_path / "json-only"
    result = runner.invoke(
        app,
        ["measure", "https://example.com", "--output", "json", "--output-dir", str(json_dir)],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    run_dir = json_dir / str(run.id)
    assert (run_dir / "result.json").exists(), "json token must write result.json"
    assert not (run_dir / "result.csv").exists(), "json token must NOT write result.csv"
    assert write_sheets.call_count == 0


def test_token_selects_writer_sheets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--output sheets`` (with the SA env present) routes to ``write_sheets`` (D-07)."""
    monkeypatch.setenv("PERFCRAWL_SHEETS_SA", str(tmp_path / "fake-sa.json"))
    _patch_measure(monkeypatch)
    write_sheets = _patch_write_sheets(monkeypatch)
    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--output",
            "sheets",
            "--sheets-id",
            "1AbCkeyOnly",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert write_sheets.call_count == 1, "sheets token must call the Sheets writer"


# --------------------------------------------------------------------------- #
# D-10: --output sheets with PERFCRAWL_SHEETS_SA unset → USER_ERROR at t=0,
# and ONLY when `sheets` is selected.
# --------------------------------------------------------------------------- #


def test_missing_sa_env_user_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--output sheets`` with no ``PERFCRAWL_SHEETS_SA`` exits USER_ERROR at t=0.

    The autouse hermetic fixture has already cleared the SA env, so this exercises the
    missing-credential fail-fast. Measurement must never run (fail-fast before Chrome)."""
    measure_calls = _patch_measure(monkeypatch)
    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--output",
            "sheets",
            "--sheets-id",
            "1AbCkeyOnly",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == int(ExitCode.USER_ERROR), result.stdout + result.stderr
    assert measure_calls == [], "measurement must never run on the missing-SA fail-fast"


# --------------------------------------------------------------------------- #
# D-14: a flagged regression NEVER changes the process exit code.
# --------------------------------------------------------------------------- #


def test_regression_exit_code_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two runs against the same target+output-dir where the second REGRESSES still
    exit 0 — flags are informational only (D-14; BUDG-01 is v2).

    Both invocations share one SQLite store under ``--output-dir`` (the prior-run
    baseline source), so the second run's worse LCP/perf is band-flagged against the
    first — and the process exit code stays ``SUCCESS`` regardless."""
    # First (baseline) run: good metrics.
    baseline = _make_stub_run(perf_score=95.0, lcp_median=1000.0)
    # Second run: clearly-worse metrics (same target/url_key) → a flagged regression.
    regressed = _make_stub_run(perf_score=60.0, lcp_median=4200.0)

    _patch_measure(monkeypatch, runs=[baseline, regressed])

    first = runner.invoke(
        app, ["measure", "https://example.com", "--output", "csv,json", "--output-dir", str(tmp_path)]
    )
    assert first.exit_code == ExitCode.SUCCESS, first.stdout + first.stderr

    second = runner.invoke(
        app, ["measure", "https://example.com", "--output", "csv,json", "--output-dir", str(tmp_path)]
    )
    # D-14: even with a regression flagged, the exit code is unchanged (success).
    assert second.exit_code == ExitCode.SUCCESS, second.stdout + second.stderr
