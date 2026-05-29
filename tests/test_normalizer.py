"""Normalizer tests — Phase 2 D-09..D-15 (LH-13.3.0 JSON → PageResult).

Each Phase 2 requirement gets one named test against a real-LH-13.3.0 fixture:

  - METRIC-01 (Lighthouse category scores)        -> test_category_scores_mapped
  - METRIC-02 (CWV: LCP, CLS, INP-proxy)          -> test_cwv_mapping
  - METRIC-03 (waterfall + LH-13 timing keys)     -> test_waterfall_timing_uses_lh13_keys
  - METRIC-04 (network facts: TTFB/bytes/...)     -> test_network_facts
  - METRIC-05 (curated diagnostics, D-12)         -> test_diagnostics_curated
  - MEDIUM-4 carve-out (ALWAYS_INCLUDE_AUDITS)    -> test_diagnostics_always_includes_interactive
  - D-10 version gate                             -> test_version_gate_rejects_major_drift
  - D-13 partial-result on non-2xx                -> test_partial_result_on_non_2xx
  - url_key via canonical_key                     -> test_url_key_set_via_canonical_key
  - D-11/D-15 labeled-proxy invariant             -> test_normalizer_source_has_no_bare_inp

RUN-02 throttling stamp is set by the orchestrator in plan 02-03, NOT by the
normalizer — out of scope here.
"""

import inspect
import json
import re

import pytest

from perfcrawl.canonical import canonical_key
from perfcrawl.constants import ALWAYS_INCLUDE_AUDITS
from perfcrawl.normalizer import normalize_lh


URL_200 = "https://example.com/"
URL_404 = "https://example.com/__nope-404__"


# --- METRIC-01: Lighthouse category scores ----------------------------------


def test_category_scores_mapped(lh_home_200):
    """METRIC-01: perf/a11y/seo/best-practices scores are mapped 0-1 -> 0-100."""
    result = normalize_lh(lh_home_200, url_as_measured=URL_200)

    cats = lh_home_200["categories"]
    # Each category score may legitimately be None for an unscoreable run,
    # but in this fixture (real example.com capture) they are non-None.
    expected_perf = cats["performance"]["score"]
    if expected_perf is not None:
        assert result.perf_score == pytest.approx(expected_perf * 100)
        assert 0.0 <= result.perf_score <= 100.0
    expected_a11y = cats["accessibility"]["score"]
    if expected_a11y is not None:
        assert result.a11y_score == pytest.approx(expected_a11y * 100)
    expected_seo = cats["seo"]["score"]
    if expected_seo is not None:
        assert result.seo_score == pytest.approx(expected_seo * 100)
    expected_bp = cats["best-practices"]["score"]
    if expected_bp is not None:
        assert result.best_practices_score == pytest.approx(expected_bp * 100)


# --- METRIC-02 + D-11/D-15: CWV mapping (LCP, CLS, TBT->inp_proxy_tbt_ms) ---


def test_cwv_mapping(lh_home_200):
    """METRIC-02 + D-11: LCP/CLS map straight; TBT writes into inp_proxy_tbt_ms."""
    result = normalize_lh(lh_home_200, url_as_measured=URL_200)

    audits = lh_home_200["audits"]
    expected_lcp = audits["largest-contentful-paint"]["numericValue"]
    expected_cls = audits["cumulative-layout-shift"]["numericValue"]
    expected_tbt = audits["total-blocking-time"]["numericValue"]

    assert result.lcp_ms is not None
    assert result.lcp_ms.median == pytest.approx(expected_lcp)
    assert result.cls is not None
    assert result.cls.median == pytest.approx(expected_cls)
    # D-11/D-15: TBT IS the labeled lab proxy — written into inp_proxy_tbt_ms.
    assert result.inp_proxy_tbt_ms is not None
    assert result.inp_proxy_tbt_ms.median == pytest.approx(expected_tbt)


# --- METRIC-03: waterfall + LH-13 timing keys (Pitfall 2) -------------------


def test_waterfall_timing_uses_lh13_keys(lh_home_200):
    """METRIC-03 + Pitfall 2: every WaterfallEntry uses networkRequestTime/networkEndTime."""
    result = normalize_lh(lh_home_200, url_as_measured=URL_200)
    items = lh_home_200["audits"]["network-requests"]["details"]["items"]
    assert len(result.waterfall) == len(items)
    assert len(result.waterfall) > 0  # METRIC-03 non-empty
    for w, item in zip(result.waterfall, items, strict=True):
        start = item.get("networkRequestTime")
        end = item.get("networkEndTime")
        if start is not None and end is not None:
            assert w.timing_ms == pytest.approx(end - start)
        else:
            assert w.timing_ms is None  # defensive: never crash on missing keys


# --- METRIC-04: network facts ------------------------------------------------


def test_network_facts(lh_home_200):
    """METRIC-04: TTFB, total_bytes, request_count, status_code, slowest_request_*."""
    result = normalize_lh(lh_home_200, url_as_measured=URL_200)

    audits = lh_home_200["audits"]
    expected_ttfb = audits["server-response-time"]["numericValue"]
    expected_total_bytes = int(audits["total-byte-weight"]["numericValue"])
    items = audits["network-requests"]["details"]["items"]

    assert result.ttfb_ms is not None
    assert result.ttfb_ms.median == pytest.approx(expected_ttfb)
    assert result.total_bytes == expected_total_bytes
    assert result.request_count == len(items)

    # status_code from the main-doc item (the one whose url == finalDisplayedUrl)
    main = [i for i in items if i.get("url") == lh_home_200.get("finalDisplayedUrl")]
    assert main, "fixture must have a main-doc item"
    assert result.status_code == main[0]["statusCode"]

    # slowest_request_* is the entry with the largest timing_ms over non-None timings
    timed = [
        (i.get("url"), i.get("networkEndTime", 0) - i.get("networkRequestTime", 0))
        for i in items
        if i.get("networkRequestTime") is not None and i.get("networkEndTime") is not None
    ]
    if timed:
        expected_slowest = max(timed, key=lambda t: t[1])
        assert result.slowest_request_url == expected_slowest[0]
        assert result.slowest_request_ms == pytest.approx(expected_slowest[1])


# --- METRIC-05 + D-12: curated diagnostics ----------------------------------


def test_diagnostics_curated(lh_home_200):
    """METRIC-05 + D-12: diagnostics keeps only failing audits (score < 1) + always-include carve-out."""
    result = normalize_lh(lh_home_200, url_as_measured=URL_200)
    audits = lh_home_200["audits"]

    if result.diagnostics is None:
        # All audits passed AND ALWAYS_INCLUDE_AUDITS was empty/missing — allowed.
        # If interactive is present, it should be carved in regardless.
        return

    for aid, entry in result.diagnostics.items():
        score = entry.get("score")
        # Carve-out: ALWAYS_INCLUDE_AUDITS may be present regardless of score
        if aid in ALWAYS_INCLUDE_AUDITS:
            continue
        # Otherwise: present means score < 1
        assert score is not None and score < 1, (
            f"audit {aid!r} has score={score!r}; should be < 1 to be in diagnostics"
        )

    # Audits with score >= 1 AND not in carve-out must be ABSENT
    for aid, audit in audits.items():
        score = audit.get("score")
        if score is not None and score >= 1 and aid not in ALWAYS_INCLUDE_AUDITS:
            assert aid not in result.diagnostics, (
                f"passing audit {aid!r} (score={score}) leaked into diagnostics"
            )


def test_diagnostics_always_includes_interactive(lh_home_200):
    """MEDIUM-4 carve-out: 'interactive' is in diagnostics even when score == 1."""
    # Mutate the fixture in-memory to force interactive score=1
    lh = json.loads(json.dumps(lh_home_200))  # deep copy
    if "interactive" not in lh["audits"]:
        lh["audits"]["interactive"] = {
            "id": "interactive",
            "score": 1,
            "numericValue": 1234.5,
        }
    else:
        lh["audits"]["interactive"]["score"] = 1
        lh["audits"]["interactive"]["numericValue"] = 1234.5

    result = normalize_lh(lh, url_as_measured=URL_200)
    assert result.diagnostics is not None
    assert "interactive" in result.diagnostics, (
        "interactive must be present even with score=1 (OUT-04 carve-out)"
    )
    assert result.diagnostics["interactive"]["numericValue"] == 1234.5


# --- D-10: version gate ------------------------------------------------------


def test_version_gate_rejects_major_drift(lh_version_14_drift):
    """D-10: a 14.x LH JSON raises ValueError citing 14 and 13."""
    with pytest.raises(ValueError) as excinfo:
        normalize_lh(lh_version_14_drift, url_as_measured=URL_200)
    msg = str(excinfo.value)
    assert "14" in msg
    assert "13" in msg


# --- D-13: partial result on non-2xx ----------------------------------------


def test_partial_result_on_non_2xx(lh_404):
    """D-13: non-2xx page records status_code; metrics may be null."""
    result = normalize_lh(lh_404, url_as_measured=URL_404)
    # The load-bearing assertion: status_code is recorded
    assert result.status_code == 404
    # url_key is set
    assert result.url_key
    # The metrics MAY be null per D-13 — but if Lighthouse captured them, that's
    # LH-driven not normalizer-driven, so we do not assert nullness rigidly.


# --- url_key via canonical_key ----------------------------------------------


def test_url_key_set_via_canonical_key(lh_home_200):
    """The PageResult.url_key equals canonical_key(url_as_measured) (D-01)."""
    result = normalize_lh(lh_home_200, url_as_measured=URL_200)
    assert result.url_key == canonical_key(URL_200)


# --- D-11/D-15: labeled-proxy defense-in-depth grep -------------------------


def test_normalizer_source_has_no_bare_inp():
    """Meta-test: normalizer source text has zero bare 'inp' tokens outside 'inp_proxy_tbt_ms'.

    Defense in depth above the model-layer ``_no_bare_inp`` validator (D-11/D-15).
    A regression must break this grep AND the model floor to surface a bare INP.
    """
    import perfcrawl.normalizer as norm_mod

    src = inspect.getsource(norm_mod)
    # Find every bare 'inp' token NOT immediately followed by '_proxy'.
    bare = re.findall(r"\binp\b(?!_proxy)", src)
    assert bare == [], f"bare-INP found in normalizer.py: {bare}"
    # And the labeled name must appear at least once (the TBT mapping site).
    assert "inp_proxy_tbt_ms" in src
