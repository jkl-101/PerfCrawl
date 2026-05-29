"""Median-of-N aggregation across per-sample measurements (Phase 2 D-08/D-14/D-16/RUN-04).

Pure function over Phase 1's MetricSample shape — no side effects, no I/O, called
once by the orchestrator after the per-sample loop. Mirrors the finite-guard
pattern from delta.py::_safe_abs (Phase 1 LEARNINGS): drop None and non-finite
up front, statistics.median over what remains, honestly empty when nothing
remains.

Two public entry points:

  - ``aggregate_samples(list[float|None]) -> MetricSample``
        The inner reducer over one metric's per-sample values. Honors D-16
        (honest empty), drops ``None`` (per-sample metric failure) and non-finite
        values up front (defense-in-depth above ``MetricSample.allow_inf_nan=False``),
        and explicitly guards Pitfall 3 (``statistics.median([])`` would raise).

  - ``aggregate_page_samples(list[PageResult]) -> PageResult``
        The per-page cross-sample reducer the orchestrator (02-03) calls once
        per page to collapse N per-sample PageResults into one aggregated
        PageResult. MetricSample fields (lcp_ms, cls, inp_proxy_tbt_ms, ttfb_ms)
        are aggregated via ``aggregate_samples`` across the per-sample medians;
        scalar/list/dict fields come from the first canonical sample (cold-cache
        cross-sample drift on those is negligible for Phase 2 — first-canonical-
        sample policy keeps the contract simple; Phase 6 may revisit).
"""

import math
import statistics

from perfcrawl.models import MetricSample, PageResult

# MetricSample-typed PageResult fields aggregated across samples (D-14).
# These are the only fields whose per-sample distribution carries meaning;
# every other PageResult field is taken from the canonical first sample
# (see aggregate_page_samples docstring). The "inp_proxy_tbt_ms" entry is
# load-bearing — naming the LABELED TBT lab proxy explicitly (D-15) instead of
# any bare-INP alias keeps the model-layer _no_bare_inp validator intact.
_METRIC_SAMPLE_FIELDS: tuple[str, ...] = (
    "lcp_ms",
    "cls",
    "inp_proxy_tbt_ms",
    "ttfb_ms",
)


def aggregate_samples(per_sample_values: list[float | None]) -> MetricSample:
    """Median-of-N reducer with D-16 honest-empty + finite guard (D-14).

    Drops ``None`` (per-sample metric failure) and any non-finite value
    (``inf``/``-inf``/``nan``) up front, then returns
    ``MetricSample(median=statistics.median(clean), samples=clean)``. If nothing
    remains the result is the honest empty ``MetricSample(median=None, samples=[])``
    rather than a raise (Pitfall 3: ``statistics.median([])`` would otherwise
    raise ``StatisticsError`` and crash the orchestrator's per-page reduce step).

    No min-sample floor, no padding — D-16 mandates honest empty. The
    list comprehension preserves insertion order across the surviving values.
    """
    clean = [v for v in per_sample_values if v is not None and math.isfinite(v)]
    if not clean:
        return MetricSample(median=None, samples=[])
    return MetricSample(median=statistics.median(clean), samples=clean)


def aggregate_page_samples(samples: list[PageResult]) -> PageResult:
    """Reduce N per-sample PageResults into a single aggregated PageResult.

    Phase 2 RUN-04 + D-14 + D-16. MetricSample fields (lcp_ms, cls,
    inp_proxy_tbt_ms, ttfb_ms) are aggregated via ``aggregate_samples`` across
    the per-sample medians; scalar/list/dict fields (perf_score, request_count,
    status_code, slowest_request_url, waterfall, diagnostics, …) come from the
    first sample (cold-cache cross-sample drift on those is negligible; the
    first-canonical-sample policy keeps the contract simple). Phase 6 may revisit
    if cross-sample variance becomes interesting.

    The aggregated PageResult still passes the Phase 1 ``_no_bare_inp`` validator
    by construction: no bare ``inp`` variable name appears here, only the
    labeled ``inp_proxy_tbt_ms`` field (D-15). ``model_copy(update=...)`` preserves
    the validator path so the labeled-proxy invariant cannot regress here.

    Raises:
        ValueError: if ``samples`` is empty (the orchestrator must never call
            this with zero samples per D-14).
        ValueError: if the input samples carry differing ``url_key`` values
            (mixing different pages into one aggregate would silently merge two
            pages' metrics — that is a bug, not a behavior).
    """
    if not samples:
        raise ValueError("aggregate_page_samples requires at least one sample")

    keys = {s.url_key for s in samples}
    if len(keys) > 1:
        raise ValueError(
            f"Cannot aggregate PageResults with differing url_key: {sorted(keys)}"
        )

    # For each MetricSample field: collect per-sample medians (or None if that
    # sample had no value for the metric), then aggregate across them. The
    # aggregator's honest-empty contract gives us MetricSample(median=None,
    # samples=[]) when every sample's field was None — stored as-is to keep the
    # model shape consistent across pages.
    updates: dict[str, MetricSample] = {}
    for field in _METRIC_SAMPLE_FIELDS:
        per_sample_medians: list[float | None] = [
            getattr(s, field).median if getattr(s, field) is not None else None
            for s in samples
        ]
        updates[field] = aggregate_samples(per_sample_medians)

    # Pydantic v2: model_copy(update=...) preserves model_config + validators
    # (including _no_bare_inp) more cheaply than reconstructing from scratch.
    # Scalar/list/dict fields are inherited from samples[0] (the canonical first
    # sample); only the four MetricSample fields are overridden.
    return samples[0].model_copy(update=updates)
