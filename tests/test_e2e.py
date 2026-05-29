"""End-to-end smoke test (Phase 2 RESEARCH § Validation Architecture E2E row).

Gated by ``@pytest.mark.e2e``; excluded from the default ``uv run pytest`` run.
Run explicitly with::

    uv run pytest -m e2e tests/test_e2e.py -x

Requires (the developer-side one-time setup the CLI's preflight() surfaces in
its error messages):

  - Node >=22.19
  - ``cd lighthouse-worker && npm ci``
  - ``uv run playwright install chromium``

The test exercises the full vertical slice: argv → orchestrator (Chrome via
CDP) → normalizer → aggregator → output writers → SQLite store → stdout JSON.

This is the "before /gsd-verify-work" manual smoke documented in
02-VALIDATION.md § Sampling Rate.
"""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from perfcrawl.models import RunRecord

pytestmark = pytest.mark.e2e


def test_e2e_measure_example_com(tmp_path: Path) -> None:
    """Real ``perfcrawl measure https://example.com/`` produces a valid RunRecord on disk + SQLite."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "perfcrawl",
            "measure",
            "https://example.com/",
            "--samples",
            "1",
            "--json",
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"exit {result.returncode}; "
        f"stderr={result.stderr}; "
        f"stdout={result.stdout[:500]}"
    )

    parsed = json.loads(result.stdout)
    run_record = RunRecord.model_validate(parsed)
    assert len(run_record.pages) == 1
    page = run_record.pages[0]
    assert page.perf_score is not None
    assert page.lcp_ms is not None and page.lcp_ms.median is not None
    # D-11/D-15 — labeled proxy. Field name itself is the labeling signal.
    assert page.inp_proxy_tbt_ms is not None
    # D-04 / RUN-02 stamped metadata.
    assert run_record.chrome_version is not None
    assert run_record.lighthouse_version is not None
    assert run_record.lighthouse_version.startswith("13.")

    # D-07 on-disk layout.
    run_dir = tmp_path / str(run_record.id)
    assert (run_dir / "result.json").exists()
    assert (run_dir / "result.csv").exists()
    lh_dir = run_dir / "lighthouse"
    assert lh_dir.exists()
    assert any(p.suffix == ".json" for p in lh_dir.iterdir())
    assert any(p.suffix == ".html" for p in lh_dir.iterdir())

    # HIST-01 — persisted to SQLite alongside the artifacts.
    db = tmp_path / "perfcrawl.db"
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT id FROM runs WHERE id = ?", (str(run_record.id),)
        ).fetchall()
        assert len(rows) == 1
    finally:
        conn.close()
