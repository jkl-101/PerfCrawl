"""Median-of-N aggregator tests — Phase 2 D-08/D-14/D-16/RUN-04 + Pitfall 3/7.

One-test-per-invariant mapping:

  - ``test_median_of_n``           -> RUN-04 happy path (median + samples preserved)
  - ``test_median_of_one``         -> Pitfall 7 (--samples 1 end-to-end)
  - ``test_median_of_even_n``      -> statistics.median midpoint-average (NOT sorted[len//2])
  - ``test_empty_samples_median_none`` -> D-16 + Pitfall 3 (no StatisticsError on [])
  - ``test_drops_none_samples``    -> D-16 honest-empty over successful samples (None dropped)
  - ``test_all_none_returns_empty`` -> D-16 extreme case (all-None == empty)
  - ``test_aggregator_drops_non_finite_samples`` -> finite-guard defense-in-depth over allow_inf_nan=False
  - ``test_samples_preserved_in_order`` -> D-16 list-comprehension order preservation
"""

import math
import statistics

import pytest

from perfcrawl.aggregator import aggregate_samples
from perfcrawl.models import MetricSample


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
