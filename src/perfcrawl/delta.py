"""The RunDelta engine — Phase 1 success criterion #2 (D-09/D-10/D-11/D-12).

Computes per-page, per-metric ``RunDelta`` records from two stored ``RunRecord``s
(a current run vs the prior run for the same site). This closes the last of the
four Phase 1 success criteria; Phase 6 will layer the variance gate ON TOP of the
raw direction computed here — Phase 1 must NOT pre-empt it (D-12).

Design invariants:

  - ``direction`` is DERIVED from the single ``METRIC_POLARITY`` registry
    (``perfcrawl.registry``) — a lower-is-better metric improves when it falls
    and regresses when it rises; a higher-is-better metric is the mirror. The
    lower/higher-is-better fact lives in exactly one editable place (D-09); this
    call site never hardcodes it. Adding a metric is a one-line edit in the
    registry, not here.
  - ``DirectionStatus`` is reused from ``perfcrawl.models`` — never redefined
    here (one enum, one source of truth, D-11).
  - ``delta_pct`` is guarded against ``previous == 0`` (and ``None`` on either
    side): it emits ``None`` rather than ``inf``/``NaN``/``ZeroDivisionError``
    (D-10). ``delta_abs`` is still computed whenever both sides are present.
  - cross-run edge cases route through the status enum (D-11): a current-only
    page => ``new``; a previous-only page => ``removed`` (EMITTED, never silently
    dropped); a metric present on only one side of a both-runs page =>
    ``not_comparable``.
  - ``unchanged`` is LITERAL equality (D-12) — raw direction only; the
    variance-aware gate that suppresses small movements is Phase 6 (HIST-02) and
    is deliberately absent here.
  - ``compute_deltas`` returns a FLAT ``list[RunDelta]`` keyed by
    ``(url_key, metric)`` over the union of pages and, per page, the union of
    comparable metrics actually present on at least one side (Open Q2).
"""

from math import isfinite

from pydantic import BaseModel, ConfigDict

from perfcrawl.models import DirectionStatus, MetricSample, PageResult, RunRecord
from perfcrawl.registry import METRIC_POLARITY, Polarity


class RunDelta(BaseModel):
    """One page's one-metric delta between two runs (D-10 field set).

    ``current``/``previous`` are the compared scalar values (``None`` when that
    side is missing the page or the metric); ``delta_abs`` is ``current -
    previous`` when both are present (else ``None``); ``delta_pct`` is the percent
    change guarded against ``previous == 0``/``None`` (D-10); ``direction`` is the
    polarity-derived status (D-09/D-11/D-12).
    """

    # extra="ignore": match every model in models.py for uniform forward-compat
    # intent (IN-01). allow_inf_nan=False is the load-bearing half (WR-01): a
    # ``delta_abs`` that overflowed to inf would otherwise serialize to ``null``
    # in Pydantic JSON mode and silently drop a real delta. The ``_safe_abs`` /
    # ``safe_pct`` finite guards are the first line of defense; this is the
    # model-layer backstop so a non-finite delta fails loud instead of nulling.
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    url_key: str
    metric: str
    current: float | None = None
    previous: float | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None
    direction: DirectionStatus


def _scalar(page: PageResult | None, metric: str) -> float | None:
    """Extract the comparable scalar for ``metric`` from a page, or ``None``.

    A ``MetricSample`` field (median-of-N distribution, D-14) is compared by its
    ``median``; a plain scalar field is used directly. A missing page or a
    ``None`` field both yield ``None`` (the absent-side signal).
    """
    if page is None:
        return None
    value = getattr(page, metric, None)
    if value is None:
        return None
    if isinstance(value, MetricSample):
        return value.median
    return value


def classify(
    metric: str,
    current: float | None,
    previous: float | None,
    *,
    page_in_current: bool = True,
    page_in_previous: bool = True,
) -> DirectionStatus:
    """Derive the per-metric direction status — RAW direction only (D-09..D-12).

    ``page_in_current``/``page_in_previous`` distinguish a whole-PAGE presence
    edge case (``new``/``removed``) from a per-METRIC one-sided value on a page
    that exists in both runs (``not_comparable``, schema drift) — both surface as
    a ``None`` scalar, so the page-level flags are required to tell them apart
    (D-11). Order matters: whole-page presence first, then unknown/one-sided
    metric, then literal equality, then polarity. The lower/higher-is-better fact
    is read from the central ``METRIC_POLARITY`` registry, never hardcoded (D-09).
    """
    # Whole-page presence edge cases (D-11): a page in only one run.
    if page_in_current and not page_in_previous:
        return DirectionStatus.NEW  # current-only page
    if page_in_previous and not page_in_current:
        return DirectionStatus.REMOVED  # previous-only page — emitted, never dropped

    # Page exists in BOTH runs from here on. Schema-drift / one-sided metric:
    if metric not in METRIC_POLARITY:
        return DirectionStatus.NOT_COMPARABLE  # unknown metric — schema drift
    if current is None or previous is None:
        return DirectionStatus.NOT_COMPARABLE  # metric on only one side of a both-runs page

    if current == previous:
        return DirectionStatus.UNCHANGED  # literal equality only — raw direction (D-12)
    if METRIC_POLARITY[metric] is Polarity.LOWER_IS_BETTER:
        better = current < previous
    else:
        better = current > previous
    return DirectionStatus.IMPROVEMENT if better else DirectionStatus.REGRESSION


def safe_pct(current: float | None, previous: float | None) -> float | None:
    """Percent change guarded against a zero/None baseline (D-10).

    Returns ``None`` when ``previous`` is ``0`` or ``None`` (no inf/NaN/
    ZeroDivisionError) or when ``current`` is ``None``; otherwise the signed
    percentage ``(current - previous) / previous * 100`` — but only if that result
    is finite. A non-finite input or a result that overflows to ``inf``/``nan``
    also yields ``None`` (WR-01's model layer is the first line of defense; this is
    the second), honoring the documented "never inf/NaN" contract (D-10).
    """
    if previous in (None, 0) or current is None:
        return None
    pct = (current - previous) / previous * 100.0
    return pct if isfinite(pct) else None


def _safe_abs(current: float | None, previous: float | None) -> float | None:
    """Absolute delta when both sides are present and finite, else ``None``.

    Mirrors the ``safe_pct`` finite guard (WR-01): subtracting two individually
    finite floats can still overflow to ``inf`` (e.g. ``1.5e308 - -1.5e308``).
    An ``inf``/``nan`` ``delta_abs`` serializes to ``null`` in Pydantic JSON
    mode, silently nulling a real delta, so a non-finite diff yields ``None``
    here (and ``RunDelta``'s ``allow_inf_nan=False`` is the model-layer backstop)
    to honor the documented "never inf/NaN" contract (D-10).
    """
    if current is None or previous is None:
        return None
    diff = current - previous
    return diff if isfinite(diff) else None


def compute_deltas(current_run: RunRecord, previous_run: RunRecord) -> list[RunDelta]:
    """Compute the flat ``list[RunDelta]`` between two runs (criterion #2).

    Iterates the UNION of ``url_key``s across both runs (so a disappeared page is
    emitted with ``direction=removed``, never dropped — D-11/Pitfall 4). For each
    page it iterates the comparable metric field names (the keys of
    ``METRIC_POLARITY``) that are present on at least one side, emitting one
    ``RunDelta`` per ``(url_key, metric)``. Direction is derived from the central
    polarity registry; ``delta_pct`` is zero-guarded. RAW direction only — the
    variance gate is Phase 6 (D-12).
    """
    cur_by_key: dict[str, PageResult] = {p.url_key: p for p in current_run.pages}
    prev_by_key: dict[str, PageResult] = {p.url_key: p for p in previous_run.pages}

    deltas: list[RunDelta] = []
    # Stable order: previous-run page order first (so removed pages keep their
    # relative position), then any current-only pages in their own order.
    ordered_keys: list[str] = list(prev_by_key)
    ordered_keys += [k for k in cur_by_key if k not in prev_by_key]

    for url_key in ordered_keys:
        cur_page = cur_by_key.get(url_key)
        prev_page = prev_by_key.get(url_key)
        page_in_current = cur_page is not None
        page_in_previous = prev_page is not None
        for metric in METRIC_POLARITY:
            current = _scalar(cur_page, metric)
            previous = _scalar(prev_page, metric)
            # Skip metrics absent on BOTH sides of this page — do not fabricate
            # spurious rows for metrics neither run measured for this page.
            if current is None and previous is None:
                continue
            deltas.append(
                RunDelta(
                    url_key=url_key,
                    metric=metric,
                    current=current,
                    previous=previous,
                    delta_abs=_safe_abs(current, previous),
                    delta_pct=safe_pct(current, previous),
                    direction=classify(
                        metric,
                        current,
                        previous,
                        page_in_current=page_in_current,
                        page_in_previous=page_in_previous,
                    ),
                )
            )
    return deltas
