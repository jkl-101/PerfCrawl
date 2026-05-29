"""Median-of-N aggregation across per-sample measurements (Phase 2 D-08/D-14/D-16/RUN-04).

Pure function over Phase 1's MetricSample shape — no side effects, no I/O, called
once by the orchestrator after the per-sample loop. Mirrors the finite-guard
pattern from delta.py::_safe_abs (Phase 1 LEARNINGS): drop None and non-finite
up front, statistics.median over what remains, honestly empty when nothing
remains.

Public entry point (Task 1):

  - ``aggregate_samples(list[float|None]) -> MetricSample``
        The inner reducer over one metric's per-sample values. Honors D-16
        (honest empty), drops ``None`` (per-sample metric failure) and non-finite
        values up front (defense-in-depth above ``MetricSample.allow_inf_nan=False``),
        and explicitly guards Pitfall 3 (``statistics.median([])`` would raise).
"""

import math
import statistics

from perfcrawl.models import MetricSample


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
