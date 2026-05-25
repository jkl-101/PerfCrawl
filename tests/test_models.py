"""Canonical record-model tests — Phase 1 data contract (D-06/D-13/D-14/D-15/D-17).

These pin the observable model contract every downstream phase targets:

  - ``schema_version`` defaults to the module constant and survives a
    JSON round-trip (D-06, criterion #3).
  - the INP field is the explicitly-labeled TBT lab proxy
    (``inp_proxy_tbt_ms``) and a bare ``inp`` is forbidden at the model layer
    (D-15).
  - a ``PageResult`` built with only ``url`` + ``url_key`` leaves the full
    later-phase metric superset ``None``/empty (D-13 nullable superset).
  - an older-schema blob (fewer fields) loads cleanly with missing fields
    defaulting to ``None`` and unknown keys ignored (D-06/D-08, criterion #3).
  - ``MetricSample`` carries a first-class ``median`` plus raw ``samples[]``
    so Phase 2 fills the distribution without retrofitting (D-14).
  - the ``DirectionStatus`` enum has all six members for Plan 03 (D-11).
"""

from datetime import UTC, datetime

from perfcrawl.models import (
    SCHEMA_VERSION,
    AnalysisResult,
    DirectionStatus,
    MetricSample,
    PageResult,
    RunRecord,
)


def test_schema_version_default():
    """RunRecord.schema_version defaults to SCHEMA_VERSION and persists round-trip (D-06)."""
    run = RunRecord(started_at=datetime(2026, 1, 1, tzinfo=UTC), target="x")
    assert run.schema_version == SCHEMA_VERSION
    # persists through a JSON round-trip (the store-layer mechanism)
    reloaded = RunRecord.model_validate_json(run.model_dump_json())
    assert reloaded.schema_version == SCHEMA_VERSION


def test_inp_proxy_naming():
    """The labeled TBT proxy exists; no bare-INP field is present (D-15)."""
    fields = set(PageResult.model_fields)
    # the labeled lab proxy is the only INP-flavored field
    assert "inp_proxy_tbt_ms" in fields
    # bare-INP names that could be mistaken for field INP are forbidden
    forbidden = {"inp", "inp_ms", "interaction_to_next_paint"}
    assert not (forbidden & fields)
    # the proxy is a MetricSample slot (median + samples), nullable now
    page = PageResult(url="https://x.com/", url_key="https://x.com/")
    assert page.inp_proxy_tbt_ms is None


def test_nullable_superset():
    """A minimal PageResult leaves every later-phase field None/empty (D-13)."""
    page = PageResult(url="https://x.com/p", url_key="https://x.com/p")
    assert page.url == "https://x.com/p"
    assert page.url_key == "https://x.com/p"
    # Lighthouse category scores
    assert page.perf_score is None
    assert page.a11y_score is None
    assert page.seo_score is None
    assert page.best_practices_score is None
    # CWV
    assert page.lcp_ms is None
    assert page.cls is None
    assert page.inp_proxy_tbt_ms is None
    # network facts (map to the existing Google Sheet columns)
    assert page.ttfb_ms is None
    assert page.request_count is None
    assert page.total_bytes is None
    assert page.status_code is None
    assert page.slowest_request_url is None
    assert page.slowest_request_ms is None
    # waterfall list + diagnostics blob + Phase-5 analysis slot
    assert page.waterfall == []
    assert page.diagnostics is None
    assert page.analysis is None


def test_old_schema_loads():
    """An older-schema blob (fewer fields) loads with missing fields None (D-06/D-08).

    ``extra="ignore"`` also drops keys the current model does not know about,
    so a NEWER-schema blob loads under OLDER code too (forward compat).
    """
    older_json = (
        '{"id":"3f1c2b9a-0000-4000-8000-000000000001",'
        '"started_at":"2026-01-01T00:00:00+00:00","target":"x","schema_version":1,'
        '"pages":[{"url":"https://a/","url_key":"https://a/"}]}'
    )
    run = RunRecord.model_validate_json(older_json)
    assert run.schema_version == 1
    assert len(run.pages) == 1
    # fields added in a later schema default to None
    assert run.pages[0].lcp_ms is None
    assert run.pages[0].perf_score is None
    assert run.auth_used is None
    # a newer-schema blob with an unknown key still loads (extra="ignore")
    newer_json = (
        '{"started_at":"2026-01-01T00:00:00+00:00","target":"x",'
        '"future_field":"surprise","pages":[]}'
    )
    run2 = RunRecord.model_validate_json(newer_json)
    assert not hasattr(run2, "future_field")


def test_metric_sample():
    """MetricSample stores a first-class median + raw samples[] (D-14)."""
    # default: empty distribution, no median yet (Phase 2 fills it)
    empty = MetricSample()
    assert empty.median is None
    assert empty.samples == []
    # populated: median + the raw sample distribution
    s = MetricSample(median=2.5, samples=[1.0, 2.5, 4.0])
    assert s.median == 2.5
    assert s.samples == [1.0, 2.5, 4.0]
    # survives a JSON round-trip
    again = MetricSample.model_validate_json(s.model_dump_json())
    assert again.median == 2.5
    assert again.samples == [1.0, 2.5, 4.0]


def test_analysis_result_nullable():
    """AnalysisResult is the Phase-5 AI slot — all fields nullable now (D-13)."""
    a = AnalysisResult()
    assert a.observation is None
    assert a.potential_cause is None
    assert a.suggested_optimization is None


def test_direction_status_enum():
    """DirectionStatus has all six members Plan 03 consumes (D-11)."""
    assert len(list(DirectionStatus)) == 6
    members = {d.value for d in DirectionStatus}
    assert members == {
        "improvement",
        "regression",
        "unchanged",
        "new",
        "removed",
        "not_comparable",
    }


def test_run_record_metadata():
    """RunRecord carries the D-17 metadata; id auto-generates, env slot nullable."""
    run = RunRecord(started_at=datetime(2026, 1, 1, tzinfo=UTC), target="studyhalo.com")
    assert run.id is not None  # auto-generated UUID
    assert run.target == "studyhalo.com"
    assert run.auth_used is None  # Phase 4
    # stamped-environment slot — defined now, Phase 2 fills
    assert run.chrome_version is None
    assert run.lighthouse_version is None
    assert run.throttling is None
    assert run.emulation is None
    assert run.pages == []
