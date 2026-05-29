"""Tests for ``perfcrawl.output`` — Phase 2 OUT-03 / OUT-04 / D-07 (Plan 02-04 Task 1).

The on-disk writers are the persistence half of the CLI vertical slice:

  - ``write_outputs`` produces ``<output_dir>/<run_id>/result.json`` (full-fidelity
    RunRecord; round-trip-identical to the SQLite blob — same hybrid-store contract
    as Phase 1), ``result.csv`` (locked CSV_COLUMNS one-row-per-page), and an
    optional ``lighthouse/<page-slug>.{json,html}`` raw-artifact pair per page.
  - The ``<page-slug>`` filename component flows through ``page_slug`` — the
    IN-02 sanitization boundary. ``..`` survives in url_key (w3lib decodes
    ``%2e%2e``) but never reaches the filesystem.
  - The CSV column for the TBT lab proxy is named ``inp_proxy_tbt_ms`` — the
    field name itself is the labeled-proxy signal (D-11/D-15). The header NEVER
    contains a bare ``inp`` token.
"""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from perfcrawl.models import MetricSample, PageResult, RunRecord
from perfcrawl.output import CSV_COLUMNS, write_outputs
from perfcrawl.slug import page_slug


# --------------------------------------------------------------------------- #
# Round-trip identity: the OUT-04 JSON file equals the Phase 1 hybrid-store
# round-trip (model_dump equality) — same contract, different sink.
# --------------------------------------------------------------------------- #


def test_json_round_trip(sample_run: RunRecord, tmp_path: Path) -> None:
    """``result.json`` reads back into a RunRecord whose ``model_dump`` equals the source."""
    run_dir = write_outputs(sample_run, output_dir=tmp_path)
    assert run_dir == tmp_path / str(sample_run.id)
    json_path = run_dir / "result.json"
    assert json_path.exists()
    loaded = RunRecord.model_validate_json(json_path.read_text())
    assert loaded.model_dump() == sample_run.model_dump()


# --------------------------------------------------------------------------- #
# CSV column order is LOCKED. Drift = downstream Sheets exporter breakage.
# --------------------------------------------------------------------------- #


def test_csv_column_order(sample_run: RunRecord, tmp_path: Path) -> None:
    """First row of ``result.csv`` equals CSV_COLUMNS verbatim; second row has same arity."""
    run_dir = write_outputs(sample_run, output_dir=tmp_path)
    with open(run_dir / "result.csv") as f:
        rows = list(csv.reader(f))
    assert rows[0] == CSV_COLUMNS
    # One row per page in the fixture.
    assert len(rows) == 1 + len(sample_run.pages)
    for data_row in rows[1:]:
        assert len(data_row) == len(CSV_COLUMNS)


# --------------------------------------------------------------------------- #
# Labeled-proxy invariant at the CSV layer: the column name is the field name
# ``inp_proxy_tbt_ms`` — the column header itself is the labeling signal. The
# header NEVER contains a bare ``inp`` / ``inp_ms`` / ``interaction_to_next_paint``.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("forbidden", ["inp", "inp_ms", "interaction_to_next_paint"])
def test_csv_inp_proxy_column_is_labeled(forbidden: str) -> None:
    """CSV_COLUMNS contains ``inp_proxy_tbt_ms`` and no bare-INP variant."""
    assert "inp_proxy_tbt_ms" in CSV_COLUMNS
    assert forbidden not in CSV_COLUMNS


# --------------------------------------------------------------------------- #
# CSV values map to RunRecord/PageResult fields per the column comments.
# --------------------------------------------------------------------------- #


def test_csv_values_round_trip(sample_run: RunRecord, tmp_path: Path) -> None:
    """Spot-check that CSV cells route to the correct model fields."""
    run_dir = write_outputs(sample_run, output_dir=tmp_path)
    with open(run_dir / "result.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(sample_run.pages)

    row0 = rows[0]
    page0 = sample_run.pages[0]

    assert row0["run_id"] == str(sample_run.id)
    assert row0["url"] == page0.url
    assert row0["test_date"] == sample_run.started_at.isoformat()
    # RUN-03 cold cache invariant — every Phase 2 run is cache_disabled=TRUE.
    assert row0["cache_disabled"] == "TRUE"
    assert row0["schema_version"] == str(sample_run.schema_version)
    assert row0["chrome_version"] == str(sample_run.chrome_version)
    assert row0["lighthouse_version"] == str(sample_run.lighthouse_version)
    assert row0["emulation"] == str(sample_run.emulation)
    # MetricSample.median → string-coerced cell.
    assert row0["lcp_ms"] == str(page0.lcp_ms.median)
    # Defensive: TBT proxy column populated from inp_proxy_tbt_ms.median.
    assert row0["inp_proxy_tbt_ms"] == str(page0.inp_proxy_tbt_ms.median)
    # Page 2 has no CLS in the fixture — empty cell, never a literal "None".
    row1 = rows[1]
    assert row1["cls"] == ""
    assert row1["inp_proxy_tbt_ms"] == ""


# --------------------------------------------------------------------------- #
# Raw LH artifacts (OUT-03) land at lighthouse/<page-slug>.{json,html}.
# --------------------------------------------------------------------------- #


def test_raw_lh_artifacts_on_disk(sample_run: RunRecord, tmp_path: Path) -> None:
    """Raw LH JSON + HTML written under ``<run_dir>/lighthouse/<slug>.{json,html}``."""
    raw_json = '{"lhr":{}}'
    raw_html = "<html/>"
    page0 = sample_run.pages[0]
    raw_artifacts = {page0.url_key: (raw_json, raw_html)}
    run_dir = write_outputs(sample_run, output_dir=tmp_path, raw_artifacts=raw_artifacts)

    slug = page_slug(page0.url_key)
    json_path = run_dir / "lighthouse" / f"{slug}.json"
    html_path = run_dir / "lighthouse" / f"{slug}.html"
    assert json_path.exists()
    assert html_path.exists()
    assert json_path.read_text() == raw_json
    assert html_path.read_text() == raw_html


def test_raw_lh_artifacts_skipped_when_empty(sample_run: RunRecord, tmp_path: Path) -> None:
    """No ``lighthouse/`` subdir when no raw artifacts are passed."""
    run_dir = write_outputs(sample_run, output_dir=tmp_path)
    lh_dir = run_dir / "lighthouse"
    # Allow either "absent" or "present and empty" — we assert no files were written.
    if lh_dir.exists():
        assert list(lh_dir.iterdir()) == []


# --------------------------------------------------------------------------- #
# IN-02 boundary: literal ``..`` in url_key never reaches the filesystem path.
# --------------------------------------------------------------------------- #


def test_slug_in_artifact_path_never_traverses(tmp_path: Path) -> None:
    """A url_key with literal ``..`` is sanitized to a safe-charset stem."""
    traversal_run = RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-000000000ddd"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target="https://x.com",
        pages=[
            PageResult(
                url="https://x.com/a/../b",
                url_key="https://x.com/a/../b",  # LITERAL .. — the IN-02 vector
                perf_score=80.0,
            )
        ],
    )
    raw_artifacts = {traversal_run.pages[0].url_key: ("{}", "<html/>")}
    run_dir = write_outputs(
        traversal_run, output_dir=tmp_path, raw_artifacts=raw_artifacts
    )
    lh_dir = run_dir / "lighthouse"
    written = list(lh_dir.iterdir())
    assert written, "expected at least one artifact written"
    for path in written:
        assert ".." not in path.name
        # All files live strictly under <run_dir>/lighthouse/.
        assert path.parent == lh_dir
        # The stem matches the IN-02-safe charset.
        for ch in path.stem:
            assert ch.isalnum() or ch in "._-", f"unsafe char in stem: {ch!r}"


# --------------------------------------------------------------------------- #
# Filesystem behavior: intermediate dirs created; unwriteable parents raise OSError
# which the CLI maps to ExitCode.USER_ERROR (D-15).
# --------------------------------------------------------------------------- #


def test_output_dir_created_when_missing(sample_run: RunRecord, tmp_path: Path) -> None:
    """``write_outputs`` creates missing intermediate directories (mkdir parents=True)."""
    target = tmp_path / "deep" / "nested" / "out"
    run_dir = write_outputs(sample_run, output_dir=target)
    assert run_dir.exists()
    assert (run_dir / "result.json").exists()
    assert (run_dir / "result.csv").exists()


def test_output_dir_unwriteable_raises_oserror(sample_run: RunRecord, tmp_path: Path) -> None:
    """When ``output_dir``'s parent is a regular file, ``write_outputs`` raises OSError."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    # The output_dir would have to live under a regular file — impossible.
    bad_dir = blocker / "out"
    with pytest.raises((OSError, NotADirectoryError, FileExistsError)):
        write_outputs(sample_run, output_dir=bad_dir)


# --------------------------------------------------------------------------- #
# Atomic-write hygiene: no .tmp file left behind after a successful write.
# --------------------------------------------------------------------------- #


def test_no_tmp_files_left_after_write(sample_run: RunRecord, tmp_path: Path) -> None:
    """``tempfile.NamedTemporaryFile`` + ``os.replace`` leaves no ``*.tmp*`` behind."""
    run_dir = write_outputs(sample_run, output_dir=tmp_path)
    stray = list(run_dir.glob("*.tmp*"))
    assert stray == [], f"stray tmp files after atomic write: {stray}"


# --------------------------------------------------------------------------- #
# JSON file is well-formed JSON (not just bytes).
# --------------------------------------------------------------------------- #


def test_json_file_is_valid_json(sample_run: RunRecord, tmp_path: Path) -> None:
    """``result.json`` parses via ``json.loads`` and exposes the expected top-level keys."""
    run_dir = write_outputs(sample_run, output_dir=tmp_path)
    data = json.loads((run_dir / "result.json").read_text())
    assert data["id"] == str(sample_run.id)
    assert data["target"] == sample_run.target
    assert isinstance(data["pages"], list)
    assert len(data["pages"]) == len(sample_run.pages)
