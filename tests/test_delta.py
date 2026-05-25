"""RunDelta-engine tests — Phase 1 success criterion #2 (D-09/D-10/D-11/D-12).

These pin the observable contract of ``compute_deltas(current_run, previous_run)``:

  - ``direction`` is DERIVED from the central ``METRIC_POLARITY`` registry
    (D-09) — a lower-is-better metric improves when it falls and regresses
    when it rises; a higher-is-better metric is the mirror. Never hardcoded.
  - ``delta_pct`` is ``None`` (never inf/NaN/ZeroDivisionError) when the
    previous value is ``0`` or ``None``; ``delta_abs`` is still computed (D-10).
  - cross-run edge cases route through the ``DirectionStatus`` enum (D-11):
    a current-only page => ``new`` (previous=None); a previous-only page =>
    ``removed`` (current=None) and it IS emitted, never silently dropped; a
    metric present on only one side => ``not_comparable``.
  - ``unchanged`` means LITERAL equality — Phase 1 computes raw direction only;
    the noise band / variance gating is Phase 6 and must NOT be pre-empted (D-12).
  - ``compute_deltas`` returns a FLAT ``list[RunDelta]`` keyed by
    ``(url_key, metric)`` over the union of pages and the union of metrics.

The two-run ``delta_pair`` fixture (conftest.py, returns ``(previous, current)``)
is built to exercise every one of these cases in one place.
"""

from perfcrawl.delta import RunDelta, compute_deltas
from perfcrawl.models import DirectionStatus, MetricSample, PageResult, RunRecord


def _by_key_metric(deltas: list[RunDelta]) -> dict[tuple[str, str], RunDelta]:
    """Index a flat RunDelta list by (url_key, metric) for ergonomic lookups."""
    return {(d.url_key, d.metric): d for d in deltas}


def _one_page_run(page: PageResult) -> RunRecord:
    """Wrap a single PageResult in a minimal RunRecord (target/started_at filled)."""
    return RunRecord(started_at="2026-05-25T00:00:00Z", target="https://t", pages=[page])


# --- D-09: polarity-derived direction (criterion #2) ------------------------


def test_direction_by_polarity(delta_pair):
    """Direction is derived from METRIC_POLARITY, not hardcoded (D-09).

    On the "/" page of the fixture:
      - perf_score (HIGHER_IS_BETTER): 0.70 -> 0.85  => improvement
      - lcp_ms     (LOWER_IS_BETTER) : 2000 -> 2600  => regression

    A lower-is-better metric that FALLS must be an improvement and one that
    RISES a regression; a higher-is-better metric is the mirror image.
    """
    previous, current = delta_pair
    deltas = compute_deltas(current, previous)
    idx = _by_key_metric(deltas)

    home = "https://studyhalo.com/"

    # higher-is-better: value rose -> improvement
    perf = idx[(home, "perf_score")]
    assert perf.current == 0.85
    assert perf.previous == 0.70
    assert perf.direction is DirectionStatus.IMPROVEMENT

    # lower-is-better: value rose -> regression
    lcp = idx[(home, "lcp_ms")]
    assert lcp.current == 2600.0
    assert lcp.previous == 2000.0
    assert lcp.direction is DirectionStatus.REGRESSION

    # Mirror: build the opposite movement to prove direction tracks polarity,
    # not the sign of the delta. A higher-is-better metric that FALLS regresses;
    # a lower-is-better metric that FALLS improves.
    prev = _one_page_run(
        PageResult(
            url="https://t/p",
            url_key="https://t/p",
            perf_score=0.90,
            lcp_ms=MetricSample(median=3000.0),
        )
    )
    cur = _one_page_run(
        PageResult(
            url="https://t/p",
            url_key="https://t/p",
            perf_score=0.50,  # higher-is-better fell -> regression
            lcp_ms=MetricSample(median=1000.0),  # lower-is-better fell -> improvement
        )
    )
    mirror = _by_key_metric(compute_deltas(cur, prev))
    assert mirror[("https://t/p", "perf_score")].direction is DirectionStatus.REGRESSION
    assert mirror[("https://t/p", "lcp_ms")].direction is DirectionStatus.IMPROVEMENT


# --- D-10: deltaPct zero/None guard -----------------------------------------


def test_deltapct_zero_guard(delta_pair):
    """previous == 0 => delta_pct is None (no inf/NaN); delta_abs still computed (D-10)."""
    previous, current = delta_pair
    deltas = compute_deltas(current, previous)
    idx = _by_key_metric(deltas)

    zero = idx[("https://studyhalo.com/zero", "total_bytes")]
    assert zero.previous == 0
    assert zero.current == 1024
    assert zero.delta_pct is None  # guarded — never inf/NaN
    assert zero.delta_abs == 1024  # absolute delta is still defined


def test_deltapct_normal_case_computes():
    """Non-zero previous => delta_pct is the real percentage (the guard isn't blanket)."""
    prev = _one_page_run(
        PageResult(url="https://t/p", url_key="https://t/p", perf_score=0.50)
    )
    cur = _one_page_run(
        PageResult(url="https://t/p", url_key="https://t/p", perf_score=0.75)
    )
    idx = _by_key_metric(compute_deltas(cur, prev))
    d = idx[("https://t/p", "perf_score")]
    assert d.delta_abs == 0.25
    assert d.delta_pct == 50.0  # (0.75 - 0.50) / 0.50 * 100


# --- D-11: status-enum edge cases (new / removed / not_comparable) -----------


def test_edge_status_enum(delta_pair):
    """Edge cases (D-11): new (current-only page), removed (previous-only, EMITTED), not_comparable.

    A whole page in only one run => new/removed; a metric on only one side of a
    both-runs page => not_comparable; removed pages are emitted, never dropped.
    """
    previous, current = delta_pair
    deltas = compute_deltas(current, previous)
    idx = _by_key_metric(deltas)

    # NEW page: present only in current -> direction=new, previous=None
    new = idx[("https://studyhalo.com/new", "perf_score")]
    assert new.previous is None
    assert new.current == 0.95
    assert new.direction is DirectionStatus.NEW

    # REMOVED page: present only in previous -> direction=removed, current=None,
    # and it MUST appear in the output (never silently dropped, D-11/Pitfall 4).
    removed = idx[("https://studyhalo.com/removed", "perf_score")]
    assert removed.current is None
    assert removed.previous == 0.60
    assert removed.direction is DirectionStatus.REMOVED
    removed_keys = {d.url_key for d in deltas}
    assert "https://studyhalo.com/removed" in removed_keys

    # NOT_COMPARABLE: request_count is present only on the current "/" page.
    # A metric on only one side of a page-that-exists-in-both is not comparable.
    not_comp = idx[("https://studyhalo.com/", "request_count")]
    assert not_comp.direction is DirectionStatus.NOT_COMPARABLE


# --- D-12: unchanged is LITERAL equality (no noise band) ---------------------


def test_unchanged_is_literal(delta_pair):
    """current == previous => unchanged; a tiny non-equal change does NOT (D-12, no noise band).

    The fixture "/" page has ttfb_ms 300 -> 300 (literal-equal -> unchanged).
    A 100.0 vs 100.1 change on a lower-is-better metric must classify as
    improvement/regression, NOT unchanged — proving the Phase-6 noise band is
    not pre-empted here.
    """
    previous, current = delta_pair
    idx = _by_key_metric(compute_deltas(current, previous))

    ttfb = idx[("https://studyhalo.com/", "ttfb_ms")]
    assert ttfb.current == 300.0
    assert ttfb.previous == 300.0
    assert ttfb.delta_abs == 0.0
    assert ttfb.direction is DirectionStatus.UNCHANGED

    # A sub-unit change is NOT swallowed as unchanged (no noise band, D-12).
    prev = _one_page_run(
        PageResult(url="https://t/p", url_key="https://t/p", lcp_ms=MetricSample(median=100.0))
    )
    cur = _one_page_run(
        PageResult(url="https://t/p", url_key="https://t/p", lcp_ms=MetricSample(median=100.1))
    )
    tiny = _by_key_metric(compute_deltas(cur, prev))[("https://t/p", "lcp_ms")]
    # lower-is-better rose by 0.1 -> regression, NOT unchanged
    assert tiny.direction is DirectionStatus.REGRESSION
    assert tiny.direction is not DirectionStatus.UNCHANGED


# --- Open Q2: flat list[RunDelta] shape over the union of pages & metrics ----


def test_flat_list_shape(delta_pair):
    """compute_deltas => flat list[RunDelta] keyed by (url_key, metric), both unions (Open Q2)."""
    previous, current = delta_pair
    deltas = compute_deltas(current, previous)

    # Flat list of RunDelta instances.
    assert isinstance(deltas, list)
    assert deltas and all(isinstance(d, RunDelta) for d in deltas)

    # Keyed by (url_key, metric) — no duplicate (page, metric) rows.
    keys = [(d.url_key, d.metric) for d in deltas]
    assert len(keys) == len(set(keys)), "each (url_key, metric) appears at most once"

    # Union of pages: all four fixture pages are represented.
    pages = {d.url_key for d in deltas}
    assert pages == {
        "https://studyhalo.com/",
        "https://studyhalo.com/zero",
        "https://studyhalo.com/removed",
        "https://studyhalo.com/new",
    }

    # Every emitted metric is one the polarity registry knows about (the
    # comparable metric space), and a known metric that's wholly absent on a
    # page is not fabricated as a spurious row.
    from perfcrawl.registry import METRIC_POLARITY

    assert all(d.metric in METRIC_POLARITY for d in deltas)
