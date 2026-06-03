"""Opt-in end-to-end crawl test — discover → measure → terminate → output.

Gated by ``@pytest.mark.e2e``; excluded from the default ``uv run pytest`` run
(``addopts = "-ra -m 'not e2e'"``). Run explicitly with::

    uv run pytest -m e2e tests/crawl/test_crawl_e2e.py -x

Requires the same developer-side one-time setup as ``tests/test_e2e.py``:

  - Node >=22.19
  - ``cd lighthouse-worker && npm ci``
  - ``uv run playwright install chromium``

Unlike ``tests/test_e2e.py`` (a single-URL ``measure`` smoke), this exercises the
FULL Phase-3 crawl slice through the real ``crawl`` command against the local
fixture site (deterministic, no external dependency): the BFS discovers >1 page,
the worker pool measures each via the real ``measure_url`` path (real Node +
Chrome), the crawl terminates, and one run dir is written containing the
aggregated one-row-per-page ``result.csv`` + ``result.json`` + per-page raw
Lighthouse ``*.json``/``*.html`` artifacts.

This is the green-e2e evidence documented in 03-VALIDATION.md (SC#1 e2e row) +
the "before /gsd-verify-work" manual smoke.
"""

import csv
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_e2e_crawl_fixture_site(tmp_path: Path, local_server: str) -> None:
    """Real ``perfcrawl crawl`` over the local fixture site discovers >1 page, outputs."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "perfcrawl",
            "crawl",
            local_server + "/index.html",
            "--max-pages",
            "5",
            "--samples",
            "1",
            "--delay",
            "0",
            "--json",
            "--output-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"exit {result.returncode}; "
        f"stderr={result.stderr}; "
        f"stdout={result.stdout[:500]}"
    )

    import json

    from perfcrawl.crawl import is_error_row
    from perfcrawl.models import RunRecord

    run_record = RunRecord.model_validate(json.loads(result.stdout))
    # The fixture index links to about.html + blog.html → discovery finds >1 page.
    # WR-05: use the SAME shared classifier the production crawl uses for its
    # exit-code/summary split, so "measured" here means exactly what it means in
    # cli.py (any of the nine metric fields non-null), not a narrower perf/LCP-only
    # proxy that can disagree with production on a partial-metric page.
    measured = [p for p in run_record.pages if not is_error_row(p)]
    assert len(measured) > 1, (
        f"expected >1 measured page from the fixture crawl, got {len(measured)} "
        f"({len(run_record.pages)} total pages)"
    )

    # One run dir with the aggregated multi-page artifacts (D-15).
    run_dir = tmp_path / str(run_record.id)
    assert (run_dir / "result.json").exists()
    csv_path = run_dir / "result.csv"
    assert csv_path.exists()
    with csv_path.open() as fh:
        rows = list(csv.DictReader(fh))
    # One row per measured page (the CSV is the one-row-per-page superset).
    assert len(rows) >= len(measured)

    # Per-page raw Lighthouse artifacts written via the reused write_outputs.
    lh_dir = run_dir / "lighthouse"
    assert lh_dir.exists()
    assert any(p.suffix == ".json" for p in lh_dir.iterdir())
    assert any(p.suffix == ".html" for p in lh_dir.iterdir())
