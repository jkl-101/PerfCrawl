"""The HIST-02 hybrid noise-band gate — Phase 6 (D-01 / D-02 / D-03).

Sits ON TOP of the Phase-1 ``compute_deltas`` engine. ``compute_deltas`` already
produces the RAW per-page, per-metric ``RunDelta`` with a ``direction`` derived
by ``classify`` from the central polarity registry. This layer adds the
*variance gate* deliberately deferred from Phase 1 (D-12): it decides which of
those raw ``regression``/``improvement`` movements are large enough to surface,
and DEMOTES the rest to ``unchanged`` — without ever recomputing direction.

The hybrid band (D-01):

  - A ``regression``/``improvement`` is FLAGGED only when BOTH floors clear:
    ``abs(delta_abs) >= abs_floor`` AND ``abs(delta_pct) >= pct_floor``.
  - Score-type metrics (``perf_score``/``a11y_score``/…) carry ``pct_floor=None``
    in the single-source ``METRIC_BAND`` table, so they gate on the ABSOLUTE floor
    ALONE (a 2-point score move on a 0-100 scale has no meaningful "percent"; D-02).
  - A ``None`` ``delta_pct`` (zero baseline — ``safe_pct`` returns ``None`` off a
    ``previous == 0``) falls back to the absolute floor ALONE (D-01, load-bearing):
    a real ``0.0 -> 0.05`` move still flags; ``0.0 -> 0.01`` does not. Without this
    fallback every "perfect previous score" page could never flag a real regression.

Direction is NEVER recomputed here (D-03): a flagged change keeps its raw
``REGRESSION``/``IMPROVEMENT``; a demoted one becomes ``UNCHANGED``. The cross-run
statuses (``new``/``removed``/``not_comparable``/``unchanged``) pass through
untouched with ``flagged=False`` — only ``regression``/``improvement`` are subject
to the gate. The lower/higher-is-better polarity fact lives in exactly one place
(``classify`` owns it); this module imports ``METRIC_BAND`` for the
thresholds and ``DirectionStatus`` for the demotion target, and re-derives
neither (single-source grep discipline, threat T-06-07).
"""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from perfcrawl.constants import METRIC_BAND
from perfcrawl.delta import RunDelta
from perfcrawl.models import DirectionStatus

# The raw directions the variance gate may act on. Everything else (the cross-run
# edge statuses) passes through untouched — the band only suppresses small
# regression/improvement movements, never reclassifies an edge case.
_GATED = {DirectionStatus.REGRESSION, DirectionStatus.IMPROVEMENT}


class BandResult(BaseModel):
    """A ``RunDelta`` paired with the band gate's verdict.

    ``flagged`` is ``True`` only when the raw delta was a ``regression``/
    ``improvement`` that cleared the band; ``direction`` is the raw direction when
    flagged (or any passthrough status), demoted to ``UNCHANGED`` when a gated
    movement failed to clear the band. The wrapped ``delta`` is retained so the
    caller keeps the full numbers (current/previous/delta_abs/delta_pct).
    """

    model_config = ConfigDict(extra="ignore")

    delta: RunDelta
    flagged: bool
    direction: DirectionStatus

    @property
    def url_key(self) -> str:
        """The wrapped delta's page key (so verdicts stay addressable)."""
        return self.delta.url_key

    @property
    def metric(self) -> str:
        """The wrapped delta's metric name."""
        return self.delta.metric


def _clears_band(metric: str, delta_abs: float | None, delta_pct: float | None) -> bool:
    """True when a movement clears the metric's hybrid band (D-01/D-02).

    Looks up ``(abs_floor, pct_floor)`` from the single-source ``METRIC_BAND``
    (never an inlined literal — threat T-06-07). The absolute floor is always
    required; the percent floor is required only when both a ``pct_floor`` and a
    ``delta_pct`` are present:

      - ``delta_abs is None`` or ``abs(delta_abs) < abs_floor`` -> not cleared.
      - ``pct_floor is None`` (score metric, D-02) -> absolute-only -> cleared.
      - ``delta_pct is None`` (zero baseline, D-01) -> absolute-only fallback ->
        cleared (the abs floor already passed above).
      - otherwise -> ``abs(delta_pct) >= pct_floor``.
    """
    abs_floor, pct_floor = METRIC_BAND[metric]
    if delta_abs is None or abs(delta_abs) < abs_floor:
        return False
    if pct_floor is None:
        return True  # score metric: absolute floor alone decides (D-02)
    if delta_pct is None:
        return True  # zero baseline: fall back to the absolute floor alone (D-01)
    return abs(delta_pct) >= pct_floor


def flag(delta: RunDelta) -> BandResult:
    """Apply the hybrid noise band to one ``RunDelta`` (D-01/D-02/D-03).

    Only a raw ``regression``/``improvement`` on a banded metric is subject to the
    gate. When it clears the band the raw direction is preserved and ``flagged`` is
    ``True``; when it fails to clear, the direction is DEMOTED to ``UNCHANGED`` and
    ``flagged`` is ``False`` (the band never recomputes direction — D-03). Every
    other status — and any metric absent from ``METRIC_BAND`` — passes through
    untouched with ``flagged=False`` and its raw direction.
    """
    if delta.direction in _GATED and delta.metric in METRIC_BAND:
        flagged = _clears_band(delta.metric, delta.delta_abs, delta.delta_pct)
        direction = delta.direction if flagged else DirectionStatus.UNCHANGED
        return BandResult(delta=delta, flagged=flagged, direction=direction)
    # Passthrough: new/removed/not_comparable/unchanged (or an unbanded metric)
    # are never flagged and never have their direction mutated.
    return BandResult(delta=delta, flagged=False, direction=delta.direction)


def flag_run(deltas: Iterable[RunDelta]) -> list[BandResult]:
    """Map :func:`flag` over a run's deltas, preserving order and identity."""
    return [flag(delta) for delta in deltas]
