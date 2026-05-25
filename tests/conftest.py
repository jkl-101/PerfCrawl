"""Shared pytest fixtures for the Phase 1 data-contract suite.

Provides the fixture-JSON loaders + a programmatic sample ``RunRecord`` (test_store
round-trips these) and a two-run delta pair (Plan 03's ``test_delta`` consumes it).
The delta pair deliberately exercises every D-09..D-12 edge case in one place:
an improved metric, a regressed metric, an unchanged metric, a ``previous == 0``
metric, a NEW page, a REMOVED page, and a metric present on only one side
(``not_comparable``).
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from perfcrawl.models import (
    AnalysisResult,
    MetricSample,
    PageResult,
    RunRecord,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to tests/fixtures/."""
    return FIXTURES_DIR


@pytest.fixture
def run_v1_json() -> str:
    """Raw JSON text of the full RunRecord fixture (>=2 pages, metrics + analysis)."""
    return (FIXTURES_DIR / "run_v1.json").read_text()


@pytest.fixture
def run_v1_old_schema_json() -> str:
    """Raw JSON text of the same run with later-phase fields absent (criterion #3)."""
    return (FIXTURES_DIR / "run_v1_old_schema.json").read_text()


@pytest.fixture
def run_v1(run_v1_json: str) -> RunRecord:
    """The full fixture parsed into a RunRecord."""
    return RunRecord.model_validate_json(run_v1_json)


@pytest.fixture
def sample_run() -> RunRecord:
    """A programmatically-built RunRecord with >=2 pages for store round-trip tests.

    Built in code (not from JSON) so the round-trip test proves model->store->read
    equals the original Pydantic object, not just a JSON re-parse.
    """
    return RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-0000000000c3"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target="https://studyhalo.com",
        auth_used=False,
        chrome_version="137.0.7151.40",
        lighthouse_version="13.3.0",
        throttling={"rttMs": 150, "cpuSlowdownMultiplier": 4},
        emulation="mobile",
        pages=[
            PageResult(
                url="https://studyhalo.com/",
                url_key="https://studyhalo.com/",
                perf_score=0.82,
                lcp_ms=MetricSample(median=2410.0, samples=[2300.0, 2410.0, 2520.0]),
                inp_proxy_tbt_ms=MetricSample(median=180.0, samples=[160.0, 180.0, 210.0]),
                ttfb_ms=MetricSample(median=320.0, samples=[300.0, 320.0, 360.0]),
                request_count=48,
                total_bytes=1843200,
                status_code=200,
                slowest_request_url="https://studyhalo.com/static/app.bundle.js",
                slowest_request_ms=612.0,
                analysis=AnalysisResult(observation="LCP bound by main bundle."),
            ),
            PageResult(
                url="https://studyhalo.com/courses?page=2",
                url_key="https://studyhalo.com/courses?page=2",
                perf_score=0.74,
                lcp_ms=MetricSample(median=3120.0, samples=[3000.0, 3120.0, 3300.0]),
                request_count=71,
                total_bytes=2621440,
                status_code=200,
            ),
        ],
    )


@pytest.fixture
def delta_pair() -> tuple[RunRecord, RunRecord]:
    """A (previous, current) two-run pair exercising every D-09..D-12 edge case.

    Page identity is by ``url_key`` (the cross-run self-join key):
      - "/"          present in both: perf_score improved, lcp regressed,
                     ttfb unchanged (literal-equal), request_count present on
                     only the current side (not_comparable for that metric).
      - "/zero"      present in both with previous total_bytes == 0 (deltaPct guard).
      - "/removed"   present in PREVIOUS only  -> direction=removed.
      - "/new"       present in CURRENT only   -> direction=new.

    Plan 03's ``compute_deltas(current, previous)`` is tested against this pair.
    """
    previous = RunRecord(
        started_at=datetime(2026, 5, 1, tzinfo=UTC),
        target="https://studyhalo.com",
        pages=[
            PageResult(
                url="https://studyhalo.com/",
                url_key="https://studyhalo.com/",
                perf_score=0.70,
                lcp_ms=MetricSample(median=2000.0),
                ttfb_ms=MetricSample(median=300.0),
                # request_count intentionally absent here -> not_comparable
            ),
            PageResult(
                url="https://studyhalo.com/zero",
                url_key="https://studyhalo.com/zero",
                total_bytes=0,  # previous == 0 -> deltaPct must be None
            ),
            PageResult(
                url="https://studyhalo.com/removed",
                url_key="https://studyhalo.com/removed",
                perf_score=0.60,
            ),
        ],
    )
    current = RunRecord(
        started_at=datetime(2026, 5, 25, tzinfo=UTC),
        target="https://studyhalo.com",
        pages=[
            PageResult(
                url="https://studyhalo.com/",
                url_key="https://studyhalo.com/",
                perf_score=0.85,  # higher-is-better -> improvement
                lcp_ms=MetricSample(median=2600.0),  # lower-is-better, went up -> regression
                ttfb_ms=MetricSample(median=300.0),  # literal-equal -> unchanged
                request_count=50,  # only on current side -> not_comparable
            ),
            PageResult(
                url="https://studyhalo.com/zero",
                url_key="https://studyhalo.com/zero",
                total_bytes=1024,  # previous was 0 -> deltaPct None, deltaAbs defined
            ),
            PageResult(
                url="https://studyhalo.com/new",
                url_key="https://studyhalo.com/new",
                perf_score=0.95,  # only in current -> direction=new
            ),
        ],
    )
    return previous, current
