"""Lighthouse-JSON → PageResult normalizer (Phase 2 D-09/D-10/D-11/D-12/D-13).

Single source of truth for LH 13.3.0 audit-shape interpretation; downstream code
reads only ``PageResult``, never raw ``lhr``.

The D-10 version gate hard-errors on Lighthouse major drift (e.g. a 14.x
lockfile bump). The realistic failure mode is silent audit-shape drift: a worker
package-lock.json bumps the Lighthouse pin without updating this parser, and
30 audit fields begin reading from renamed keys and silently produce ``None``.
Failing loud at the boundary surfaces the problem at the first measurement
instead of in week-3 of cross-run regression debugging.

# INVARIANT: never construct a local variable whose name is the bare INP token
# (forbidden field names enumerated in models._FORBIDDEN_INP_FIELDS). TBT
# writes DIRECTLY into inp_proxy_tbt_ms (D-11/D-15). The model-layer
# _no_bare_inp validator is the floor; this normalizer should never approach
# it. A defense-in-depth grep meta-test in tests/test_normalizer.py asserts
# this textually.
"""

from perfcrawl.canonical import canonical_key
from perfcrawl.constants import ALWAYS_INCLUDE_AUDITS, EXPECTED_LIGHTHOUSE_MAJOR
from perfcrawl.models import MetricSample, PageResult, WaterfallEntry


def _check_version(lhr: dict) -> None:
    """Hard-error on Lighthouse major drift (D-10).

    Prevents silent audit-shape corruption on a lockfile bump (the realistic
    failure mode where someone upgrades to 14.0 without updating the parser).
    Mirrors the model-layer fail-loud invariant from PageResult.allow_inf_nan
    (WR-01): silent corruption is the worst-case outcome, so raise here.
    """
    actual = lhr.get("lighthouseVersion", "")
    if not actual.startswith(EXPECTED_LIGHTHOUSE_MAJOR + "."):
        raise ValueError(
            f"Lighthouse version mismatch: expected major "
            f"{EXPECTED_LIGHTHOUSE_MAJOR}.x, got {actual!r}. Normalizer is "
            f"locked to LH {EXPECTED_LIGHTHOUSE_MAJOR}.x audit shape; "
            f"refusing to silently produce a corrupted PageResult."
        )


def normalize_lh(lhr: dict, *, url_as_measured: str) -> PageResult:
    """Transform a single LH-13.3.0 ``lhr`` dict into a single-sample PageResult.

    The orchestrator (plan 02-03) calls this once per sample and then the
    aggregator (plan 02-02) zips N samples into a final ``MetricSample.median``
    + ``samples`` distribution. The single-sample shape produced here therefore
    has ``MetricSample.samples`` of length 0 or 1.

    D-10 version gate runs FIRST; any major-mismatch raises ValueError before
    any audit shape is touched. D-13 partial-result contract: if Lighthouse
    captured network-requests, the main-doc statusCode is surfaced; metric
    fields that LH left null stay null (PageResult is the nullable superset).
    """
    _check_version(lhr)  # D-10 — hard error on major mismatch

    audits = lhr.get("audits", {})
    cats = lhr.get("categories", {})
    final_url = lhr.get("finalDisplayedUrl")

    def _cat_score(key: str) -> float | None:
        """Read a LH category score in [0, 1] and scale to the legacy 0-100 range.

        Defensive chained .get(): missing key -> None, not KeyError. Scaling
        matches the existing studyhalo Google Sheet (per 02-CONTEXT § Specifics).
        """
        score = cats.get(key, {}).get("score")
        return float(score * 100) if score is not None else None

    def _numeric(audit_id: str) -> float | None:
        """Read a LH audit numericValue defensively (None if absent / not set)."""
        v = audits.get(audit_id, {}).get("numericValue")
        return float(v) if v is not None else None

    # METRIC-03 waterfall. CRITICAL: LH 13 renamed the timing keys to
    # networkRequestTime / networkEndTime — Pitfall 2. The old startTime/
    # endTime keys silently return None and produce a null timing_ms on every
    # WaterfallEntry. The D-10 version gate above catches any major mismatch,
    # but using the new key names is the second layer of protection.
    waterfall: list[WaterfallEntry] = []
    main_doc_status: int | None = None
    items = audits.get("network-requests", {}).get("details", {}).get("items", [])
    for item in items:
        start = item.get("networkRequestTime")
        end = item.get("networkEndTime")
        timing = (end - start) if (start is not None and end is not None) else None
        entry = WaterfallEntry(
            url=item.get("url"),
            resource_type=item.get("resourceType"),
            size_bytes=item.get("transferSize"),
            timing_ms=timing,
            status_code=item.get("statusCode"),
        )
        waterfall.append(entry)
        # The main-document item is the one whose URL matches finalDisplayedUrl;
        # its statusCode is the page-level status_code per D-13.
        if main_doc_status is None and item.get("url") == final_url:
            main_doc_status = item.get("statusCode")

    # Slowest request — max over waterfall entries with a non-None timing_ms.
    # Empty waterfall -> default=None (never raises on max of empty).
    slowest = max(
        (w for w in waterfall if w.timing_ms is not None),
        key=lambda w: w.timing_ms,
        default=None,
    )

    # D-12 + MEDIUM-4 (plan-check) carve-out for OUT-04: curated diagnostics.
    # Default filter drops any audit with score >= 1 (passing audits + meta
    # audits with no score). ALWAYS_INCLUDE_AUDITS (currently {"interactive"})
    # is the per-audit carve-out: kept REGARDLESS of score because the CSV
    # column "total_page_load_time" sources from audits["interactive"]
    # .numericValue, which is empty for fast pages that pass with score == 1.0.
    diagnostics: dict = {
        aid: a
        for aid, a in audits.items()
        if (a.get("score") is not None and a["score"] < 1)
        or aid in ALWAYS_INCLUDE_AUDITS
    }

    total_byte_weight = _numeric("total-byte-weight")

    return PageResult(
        url=url_as_measured,
        url_key=canonical_key(url_as_measured),
        perf_score=_cat_score("performance"),
        a11y_score=_cat_score("accessibility"),
        seo_score=_cat_score("seo"),
        best_practices_score=_cat_score("best-practices"),
        lcp_ms=_single_sample_metric("largest-contentful-paint", _numeric),
        cls=_single_sample_metric("cumulative-layout-shift", _numeric),
        # D-11/D-15: TBT IS the labeled lab proxy. The TBT read goes DIRECTLY
        # into the inp_proxy_tbt_ms keyword argument; we never bind to a local
        # named with any of the forbidden bare-INP tokens. The grep meta-test
        # in tests/test_normalizer.py enforces this textually.
        inp_proxy_tbt_ms=_single_sample_metric("total-blocking-time", _numeric),
        ttfb_ms=_single_sample_metric("server-response-time", _numeric),
        request_count=len(waterfall),
        total_bytes=int(total_byte_weight) if total_byte_weight is not None else None,
        status_code=main_doc_status,
        slowest_request_url=slowest.url if slowest else None,
        slowest_request_ms=slowest.timing_ms if slowest else None,
        waterfall=waterfall,
        diagnostics=diagnostics or None,
        analysis=None,  # Phase 5 fills.
    )


def _single_sample_metric(audit_id: str, numeric_reader) -> MetricSample:
    """Build a single-sample MetricSample from one audit's numericValue.

    The aggregator (plan 02-02) collects these across N samples and rebuilds the
    final MetricSample with median + full samples distribution. Per-sample shape:
    samples=[v] if non-None else [], median=v (or None if v is None).
    """
    value = numeric_reader(audit_id)
    return MetricSample(
        median=value,
        samples=[value] if value is not None else [],
    )
