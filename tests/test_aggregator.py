"""Median-of-N aggregator tests — Phase 2 D-08/D-14/D-16/RUN-04 + Pitfall 3/7.

One-test-per-invariant mapping (aggregate_samples — Task 1):

  - ``test_median_of_n``           -> RUN-04 happy path (median + samples preserved)
  - ``test_median_of_one``         -> Pitfall 7 (--samples 1 end-to-end)
  - ``test_median_of_even_n``      -> statistics.median midpoint-average (NOT sorted[len//2])
  - ``test_empty_samples_median_none`` -> D-16 + Pitfall 3 (no StatisticsError on [])
  - ``test_drops_none_samples``    -> D-16 honest-empty over successful samples (None dropped)
  - ``test_all_none_returns_empty`` -> D-16 extreme case (all-None == empty)
  - ``test_aggregator_drops_non_finite_samples`` -> finite-guard defense-in-depth over allow_inf_nan=False
  - ``test_samples_preserved_in_order`` -> D-16 list-comprehension order preservation

One-test-per-invariant mapping (aggregate_page_samples — Task 2):

  - ``test_aggregates_metric_sample_fields_across_samples`` -> RUN-04 core (lcp_ms median across N)
  - ``test_aggregates_all_metric_sample_fields``           -> RUN-04 coverage (all 4 MetricSample fields)
  - ``test_drops_per_sample_failed_metric``                -> D-16 (one sample's metric None)
  - ``test_scalar_fields_from_first_sample``               -> first-canonical-sample policy
  - ``test_url_identity_preserved``                        -> url/url_key carried from first sample
  - ``test_url_mismatch_raises``                           -> mixing different pages is a bug
  - ``test_waterfall_and_diagnostics_from_first_sample``   -> first-canonical-sample for non-MetricSample collections
  - ``test_empty_input_raises``                            -> orchestrator never calls with []
  - ``test_single_sample_passthrough``                     -> Pitfall 7 at the page level
"""

import math
import statistics

import pytest

from perfcrawl.aggregator import aggregate_page_samples, aggregate_samples
from perfcrawl.models import AnalysisResult, MetricSample, PageResult, WaterfallEntry


def _make_sample(
    url: str = "https://x/",
    url_key: str | None = None,
    *,
    lcp: float | None = None,
    cls: float | None = None,
    tbt: float | None = None,
    ttfb: float | None = None,
    perf: float | None = None,
    rc: int | None = None,
    tb: int | None = None,
    sc: int | None = None,
    slowest_url: str | None = None,
    slowest_ms: float | None = None,
    waterfall: list[WaterfallEntry] | None = None,
    diagnostics: dict | None = None,
    analysis: AnalysisResult | None = None,
) -> PageResult:
    """Factory: build a per-sample PageResult inline for aggregate_page_samples tests.

    Lighter than the conftest ``sample_run`` RunRecord fixture (per the plan's
    "build small PageResults inline" guidance); MetricSample fields are wrapped
    so callers pass plain floats.
    """
    def _ms(v: float | None) -> MetricSample | None:
        return MetricSample(median=v, samples=[v]) if v is not None else None

    return PageResult(
        url=url,
        url_key=url_key if url_key is not None else url,
        perf_score=perf,
        lcp_ms=_ms(lcp),
        cls=_ms(cls),
        inp_proxy_tbt_ms=_ms(tbt),
        ttfb_ms=_ms(ttfb),
        request_count=rc,
        total_bytes=tb,
        status_code=sc,
        slowest_request_url=slowest_url,
        slowest_request_ms=slowest_ms,
        waterfall=waterfall if waterfall is not None else [],
        diagnostics=diagnostics,
        analysis=analysis,
    )


def test_median_of_n():
    """RUN-04: median + samples preserved across N values."""
    values = [2300.0, 2410.0, 2520.0]
    result = aggregate_samples(values)
    assert result == MetricSample(
        median=statistics.median(values), samples=[2300.0, 2410.0, 2520.0]
    )
    assert result.median == 2410.0


def test_median_of_one():
    """Pitfall 7: --samples 1 must work end-to-end."""
    result = aggregate_samples([42.0])
    assert result == MetricSample(median=42.0, samples=[42.0])


def test_median_of_even_n():
    """statistics.median midpoint-average, NOT a hand-rolled sorted[len//2]."""
    # sorted[len//2] of [10.0, 20.0] would return 20.0; statistics.median returns 15.0.
    result = aggregate_samples([10.0, 20.0])
    assert result == MetricSample(median=15.0, samples=[10.0, 20.0])


def test_empty_samples_median_none():
    """D-16 + Pitfall 3: aggregate_samples([]) returns honest-empty without raising.

    Without the empty-guard, ``statistics.median([])`` would raise
    ``StatisticsError`` and crash the orchestrator's per-page reduce step.
    """
    result = aggregate_samples([])
    assert result == MetricSample(median=None, samples=[])


def test_drops_none_samples():
    """D-16: per-sample failed metric (None) is dropped; survivors define the median."""
    result = aggregate_samples([1.0, None, 2.0])
    assert result == MetricSample(median=1.5, samples=[1.0, 2.0])


def test_all_none_returns_empty():
    """D-16 extreme case: all-None collapses to honest empty (same as [])."""
    result = aggregate_samples([None, None, None])
    assert result == MetricSample(median=None, samples=[])


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_aggregator_drops_non_finite_samples(bad):
    """Defense-in-depth above MetricSample.allow_inf_nan=False: drop inf/nan/-inf up front.

    Mirrors the finite-guard pattern from delta.py::_safe_abs (Phase 1 LEARNINGS).
    """
    result = aggregate_samples([1.0, bad, 2.0])
    assert result == MetricSample(median=1.5, samples=[1.0, 2.0])


def test_samples_preserved_in_order():
    """D-16: list-comprehension preserves insertion order across the surviving values."""
    # Deliberately unsorted input — the aggregator MUST NOT sort the surface list;
    # only statistics.median internally takes the middle of the sorted view.
    values = [5.0, 1.0, 3.0, 2.0, 4.0]
    result = aggregate_samples(values)
    assert result.samples == [5.0, 1.0, 3.0, 2.0, 4.0]
    assert result.median == 3.0  # statistics.median sorts internally


# ---------------------------------------------------------------------------
# aggregate_page_samples — Task 2: per-page cross-sample reducer
# ---------------------------------------------------------------------------


def test_aggregates_metric_sample_fields_across_samples():
    """RUN-04 core: lcp_ms median across N samples + samples list preserved in order."""
    samples = [
        _make_sample(url="https://x/", lcp=2300.0),
        _make_sample(url="https://x/", lcp=2410.0),
        _make_sample(url="https://x/", lcp=2520.0),
    ]
    result = aggregate_page_samples(samples)
    assert result.lcp_ms.median == 2410.0
    assert result.lcp_ms.samples == [2300.0, 2410.0, 2520.0]


def test_aggregates_all_metric_sample_fields():
    """RUN-04 coverage: every MetricSample field (lcp_ms, cls, inp_proxy_tbt_ms, ttfb_ms)."""
    samples = [
        _make_sample(url="https://x/", lcp=2300.0, cls=0.10, tbt=160.0, ttfb=300.0),
        _make_sample(url="https://x/", lcp=2410.0, cls=0.12, tbt=180.0, ttfb=320.0),
        _make_sample(url="https://x/", lcp=2520.0, cls=0.14, tbt=210.0, ttfb=360.0),
    ]
    result = aggregate_page_samples(samples)
    assert len(result.lcp_ms.samples) == 3
    assert len(result.cls.samples) == 3
    assert len(result.inp_proxy_tbt_ms.samples) == 3
    assert len(result.ttfb_ms.samples) == 3
    assert result.lcp_ms.median == statistics.median([2300.0, 2410.0, 2520.0])
    assert result.cls.median == statistics.median([0.10, 0.12, 0.14])
    assert result.inp_proxy_tbt_ms.median == statistics.median([160.0, 180.0, 210.0])
    assert result.ttfb_ms.median == statistics.median([300.0, 320.0, 360.0])


def test_drops_per_sample_failed_metric():
    """D-16: a None per-sample MetricSample is dropped — survivors define the median."""
    # Sample 2's lcp_ms entirely failed (LH could not capture LCP that run).
    samples = [
        _make_sample(url="https://x/", lcp=2300.0),
        _make_sample(url="https://x/", lcp=None),
        _make_sample(url="https://x/", lcp=2520.0),
    ]
    result = aggregate_page_samples(samples)
    assert result.lcp_ms.samples == [2300.0, 2520.0]
    assert result.lcp_ms.median == statistics.median([2300.0, 2520.0])


def test_scalar_fields_from_first_sample():
    """First-canonical-sample policy: scalar fields are taken from samples[0]."""
    # Distinct scalar values across samples — assert the FIRST wins on every one.
    samples = [
        _make_sample(
            url="https://x/",
            lcp=2300.0,
            perf=80.0,
            rc=48,
            tb=1843200,
            sc=200,
            slowest_url="https://x/a.js",
            slowest_ms=612.0,
        ),
        _make_sample(
            url="https://x/",
            lcp=2410.0,
            perf=75.0,
            rc=51,
            tb=1900000,
            sc=200,
            slowest_url="https://x/b.js",
            slowest_ms=700.0,
        ),
        _make_sample(
            url="https://x/",
            lcp=2520.0,
            perf=70.0,
            rc=53,
            tb=2000000,
            sc=200,
            slowest_url="https://x/c.js",
            slowest_ms=800.0,
        ),
    ]
    result = aggregate_page_samples(samples)
    # All scalar/non-MetricSample fields come from samples[0]
    assert result.perf_score == 80.0
    assert result.request_count == 48
    assert result.total_bytes == 1843200
    assert result.status_code == 200
    assert result.slowest_request_url == "https://x/a.js"
    assert result.slowest_request_ms == 612.0


def test_url_identity_preserved():
    """url + url_key are inherited from samples[0] (identical across input by contract)."""
    samples = [
        _make_sample(url="https://x/p", url_key="https://x/p", lcp=2300.0),
        _make_sample(url="https://x/p", url_key="https://x/p", lcp=2410.0),
    ]
    result = aggregate_page_samples(samples)
    assert result.url == "https://x/p"
    assert result.url_key == "https://x/p"


def test_url_mismatch_raises():
    """Mixing different pages into one aggregate is a bug, not a behavior — must raise."""
    samples = [
        _make_sample(url="https://x/a", url_key="https://x/a", lcp=2300.0),
        _make_sample(url="https://x/b", url_key="https://x/b", lcp=2410.0),
    ]
    with pytest.raises(ValueError, match="url_key"):
        aggregate_page_samples(samples)


def test_waterfall_and_diagnostics_from_first_sample():
    """waterfall + diagnostics come from samples[0] (first-canonical-sample policy)."""
    wf_first = [
        WaterfallEntry(url="https://x/a.js", resource_type="script", timing_ms=120.0)
    ]
    wf_second = [
        WaterfallEntry(url="https://x/b.js", resource_type="script", timing_ms=140.0)
    ]
    samples = [
        _make_sample(
            url="https://x/",
            lcp=2300.0,
            waterfall=wf_first,
            diagnostics={"speedIndex": 1800.0},
        ),
        _make_sample(
            url="https://x/",
            lcp=2410.0,
            waterfall=wf_second,
            diagnostics={"speedIndex": 1900.0},
        ),
    ]
    result = aggregate_page_samples(samples)
    assert len(result.waterfall) == 1
    assert result.waterfall[0].url == "https://x/a.js"
    assert result.diagnostics == {"speedIndex": 1800.0}


def test_empty_input_raises():
    """The orchestrator must never call this with [] (D-14) — raise loud."""
    with pytest.raises(ValueError, match="at least one sample"):
        aggregate_page_samples([])


def test_single_sample_passthrough():
    """Pitfall 7 at the page level: --samples 1 must produce a valid aggregated PageResult."""
    only = _make_sample(
        url="https://x/",
        lcp=2410.0,
        cls=0.12,
        tbt=180.0,
        ttfb=320.0,
        perf=80.0,
        rc=48,
    )
    result = aggregate_page_samples([only])
    # MetricSample fields: median == single value, samples == [value]
    assert result.lcp_ms == MetricSample(median=2410.0, samples=[2410.0])
    assert result.cls == MetricSample(median=0.12, samples=[0.12])
    assert result.inp_proxy_tbt_ms == MetricSample(median=180.0, samples=[180.0])
    assert result.ttfb_ms == MetricSample(median=320.0, samples=[320.0])
    # Scalar fields inherited from the single sample
    assert result.perf_score == 80.0
    assert result.request_count == 48
