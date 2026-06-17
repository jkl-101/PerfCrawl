"""RED band-gate tests — Phase 6 HIST-02 (D-01 / D-02 / D-03 / D-14).

Wave-0 INTERFACE-FIRST contract. These tests are AUTHORED BEFORE the
implementation and MUST fail (collection-time ``ModuleNotFoundError`` on
``perfcrawl.regression``) until Plan 04 lands ``regression.py``. "RED-as-expected"
is the success condition here — do NOT implement production code to green them.

The contract they lock for Plan 04:

  - ``regression.flag(delta: RunDelta) -> BandResult`` where ``BandResult`` exposes
    ``.flagged: bool`` and ``.direction: DirectionStatus``.
  - ``regression.flag_run(deltas: list[RunDelta]) -> list[BandResult]``.
  - The hybrid gate (D-01): a ``regression``/``improvement`` is FLAGGED only when
    ``abs(delta_abs) >= abs_floor AND abs(delta_pct) >= pct_floor``. Score-type
    metrics use ``pct_floor=None`` → absolute floor ALONE (D-02). A ``None``
    ``delta_pct`` (zero baseline) falls back to the absolute floor alone (D-01).
  - The band DEMOTES but NEVER recomputes direction (D-03): a flagged regression
    keeps ``REGRESSION``; ``new``/``removed``/``not_comparable``/``unchanged``
    deltas pass through with ``flagged=False`` and direction unchanged.

Thresholds are NEVER inlined here — every band literal comes from
``constants.METRIC_BAND`` (the Phase-1 single-source grep discipline).
"""

import pytest

from perfcrawl import regression  # RED: Plan 04 adds this module.
from perfcrawl.constants import METRIC_BAND
from perfcrawl.delta import RunDelta, compute_deltas
from perfcrawl.models import DirectionStatus
from perfcrawl.registry import METRIC_POLARITY

BASE = "https://studyhalo.com"


def _flag_by_key_metric(band_pair) -> dict[tuple[str, str], "regression.BandResult"]:
    """Run the band gate over the ``band_pair`` deltas, indexed by (url_key, metric)."""
    previous, current = band_pair
    deltas = compute_deltas(current, previous)
    results = regression.flag_run(deltas)
    # flag_run preserves the per-delta url_key/metric so each verdict is addressable.
    return {(r.url_key, r.metric): r for r in results}


def test_cls_near_zero_not_flagged(band_pair):
    """CLS 0.01 -> 0.02 (+100%) is NOT flagged — the near-zero guard (D-01).

    Δabs 0.01 < the ``cls`` abs floor, so even a +100% pct can't flag it.
    """
    idx = _flag_by_key_metric(band_pair)
    result = idx[(f"{BASE}/cls-near-zero", "cls")]
    assert result.flagged is False
    # abs floor comes from the single-source band table, never inlined.
    assert abs(0.01) < METRIC_BAND["cls"][0]


def test_clear_cls_regression_flagged(band_pair):
    """CLS 0.05 -> 0.12 (Δ0.07, +140%) IS flagged as a regression (D-01)."""
    idx = _flag_by_key_metric(band_pair)
    result = idx[(f"{BASE}/cls-clear-regression", "cls")]
    assert result.flagged is True
    assert result.direction is DirectionStatus.REGRESSION


def test_zero_baseline_absolute_only(band_pair):
    """Zero-baseline fallback gates on the absolute floor alone (D-01).

    delta_pct is None off a 0 baseline, so the pct half is skipped:
      - 0.0 -> 0.05: Δabs 0.05 >= cls abs floor -> FLAGGED.
      - 0.0 -> 0.01: Δabs 0.01 <  cls abs floor -> NOT flagged.
    A real move away from a perfect baseline must stay flaggable.
    """
    idx = _flag_by_key_metric(band_pair)
    flagged = idx[(f"{BASE}/cls-zero-baseline-flag", "cls")]
    not_flagged = idx[(f"{BASE}/cls-zero-baseline-noflag", "cls")]
    assert flagged.flagged is True
    assert flagged.direction is DirectionStatus.REGRESSION
    assert not_flagged.flagged is False


def test_score_absolute_only(band_pair):
    """Lighthouse score metrics gate on the absolute floor ALONE (pct_floor=None, D-02).

      - perf_score 88 -> 90: Δ2 < the perf abs floor -> NOT flagged.
      - perf_score 88 -> 92: Δ4 >= the perf abs floor -> FLAGGED.
    """
    idx = _flag_by_key_metric(band_pair)
    not_flagged = idx[(f"{BASE}/score-noflag", "perf_score")]
    flagged = idx[(f"{BASE}/score-flag", "perf_score")]
    assert not_flagged.flagged is False
    assert flagged.flagged is True
    # perf_score is absolute-only: the pct floor is None in the single-source table.
    assert METRIC_BAND["perf_score"][1] is None


def test_band_table_covers_all_metrics():
    """Every numeric METRIC_POLARITY metric has a band entry, and vice versa (D-02).

    A new metric must be a one-line edit in BOTH tables — this guards against a
    silently un-banded metric. Each band value is an (abs_floor, pct_floor) tuple.
    """
    assert set(METRIC_BAND) == set(METRIC_POLARITY)
    for metric, band in METRIC_BAND.items():
        assert isinstance(band, tuple)
        assert len(band) == 2
        abs_floor, pct_floor = band
        assert abs_floor is not None
        assert pct_floor is None or isinstance(pct_floor, (int, float))


def test_band_preserves_raw_direction(band_pair):
    """The band DEMOTES but NEVER recomputes direction (D-03).

    A flagged regression keeps REGRESSION; the cross-run statuses
    (new / removed / not_comparable / unchanged) pass through untouched with
    flagged=False — only regression/improvement are subject to the gate.
    """
    # A flagged regression keeps its raw direction.
    idx = _flag_by_key_metric(band_pair)
    clear = idx[(f"{BASE}/cls-clear-regression", "cls")]
    assert clear.flagged is True
    assert clear.direction is DirectionStatus.REGRESSION

    # Passthrough statuses: never flagged, direction never mutated. Built directly
    # so the contract is asserted independent of compute_deltas' fixture shape.
    passthrough = {
        DirectionStatus.NEW: RunDelta(
            url_key=f"{BASE}/n", metric="cls", current=0.5, previous=None,
            delta_abs=None, delta_pct=None, direction=DirectionStatus.NEW,
        ),
        DirectionStatus.REMOVED: RunDelta(
            url_key=f"{BASE}/r", metric="cls", current=None, previous=0.5,
            delta_abs=None, delta_pct=None, direction=DirectionStatus.REMOVED,
        ),
        DirectionStatus.NOT_COMPARABLE: RunDelta(
            url_key=f"{BASE}/nc", metric="cls", current=0.5, previous=None,
            delta_abs=None, delta_pct=None, direction=DirectionStatus.NOT_COMPARABLE,
        ),
        DirectionStatus.UNCHANGED: RunDelta(
            url_key=f"{BASE}/u", metric="cls", current=0.5, previous=0.5,
            delta_abs=0.0, delta_pct=0.0, direction=DirectionStatus.UNCHANGED,
        ),
    }
    for status, delta in passthrough.items():
        result = regression.flag(delta)
        assert result.flagged is False
        assert result.direction is status


def test_offender_ranking_normalizes_units():
    """WR-02: top-N offenders rank by normalized band-multiple, not raw delta_abs.

    A small-unit metric (cls, abs_floor 0.02) that clears its band by a LARGER
    multiple must outrank a large-unit metric (total_bytes, abs_floor 51200) that
    clears by a SMALLER multiple — even though the raw ``abs(delta_abs)`` of the
    large-unit metric is orders of magnitude bigger. Raw-magnitude sorting would
    truncate the genuine CLS regression out of the summary.
    """
    from perfcrawl.cli import _band_multiple

    # cls: 0.2 / 0.02 abs_floor = 10 noise-bands cleared (small raw magnitude).
    cls_br = regression.flag(
        RunDelta(
            url_key=f"{BASE}/cls",
            metric="cls",
            current=0.25,
            previous=0.05,
            delta_abs=0.2,
            delta_pct=400.0,
            direction=DirectionStatus.REGRESSION,
        )
    )
    # total_bytes: 102400 / 51200 abs_floor = 2 noise-bands cleared (huge raw magnitude).
    bytes_br = regression.flag(
        RunDelta(
            url_key=f"{BASE}/bytes",
            metric="total_bytes",
            current=204800,
            previous=102400,
            delta_abs=102400,
            delta_pct=100.0,
            direction=DirectionStatus.REGRESSION,
        )
    )
    assert cls_br.flagged and bytes_br.flagged

    # Raw-magnitude ranking would put total_bytes first; normalized ranking flips it.
    assert abs(bytes_br.delta.delta_abs) > abs(cls_br.delta.delta_abs)
    assert _band_multiple(cls_br) > _band_multiple(bytes_br)
    ranked = sorted([bytes_br, cls_br], key=_band_multiple, reverse=True)
    assert ranked[0] is cls_br
