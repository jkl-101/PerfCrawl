"""Deterministic dim-7 CWV band pre-flag (AI-SPEC §4 / 05-EVAL-REVIEW remediation #3).

This is a FREE Lane-1 test — no ``@pytest.mark.llm``, no network, no key. It
classifies each curated fixture's raw ``lcp_ms.median`` / ``cls.median`` into the
web.dev Core Web Vitals band via ``constants.cwv_band`` against the shared
cutoffs, catching obvious threshold inversions (e.g. "LCP needs work" at 1.2 s)
before the paid judge spends a single token.

``test_band_cutoffs_match_rubric`` is the FM-5 single-source-of-truth guard
(mirrors ``test_digest.py::test_rubric_frozen``): it asserts the ``constants``
cutoffs appear verbatim in ``analysis.RUBRIC`` so the cheap pre-flag and the
paid judge can never disagree on a band.
"""

from __future__ import annotations

import pytest

from perfcrawl import analysis
from perfcrawl import constants as c


def _lcp_band(page) -> str:
    """The page's LCP band, sourced from the raw median against the shared cutoffs."""
    value = page.lcp_ms.median if page.lcp_ms else None
    return c.cwv_band(value, c.LCP_GOOD_MS, c.LCP_POOR_MS)


def _cls_band(page) -> str:
    """The page's CLS band, sourced from the raw median against the shared cutoffs."""
    value = page.cls.median if page.cls else None
    return c.cwv_band(value, c.CLS_GOOD, c.CLS_POOR)


# (fixture name, expected LCP band, expected CLS band) — bands derived from the
# actual fixture medians (read before asserting; do NOT invent values):
#   healthy-all-green  LCP 1200  -> good            CLS 0.02 -> good
#   green-trap         LCP 1100  -> good            CLS 0.01 -> good
#   high-cls           LCP 2300  -> good            CLS 0.42 -> poor
#   slow-lcp           LCP 4800  -> poor            CLS 0.03 -> good
#   adversarial-number LCP 2500  -> good (boundary) CLS 0.10 -> good (boundary)
_BAND_CASES = [
    ("healthy-all-green", "good", "good"),
    ("green-trap", "good", "good"),
    ("high-cls", "good", "poor"),
    ("slow-lcp", "poor", "good"),
    ("adversarial-number", "good", "good"),
]


@pytest.mark.parametrize(("name", "want_lcp", "want_cls"), _BAND_CASES)
def test_band_preflag_classifies_fixtures(digest_page, name, want_lcp, want_cls) -> None:
    """Each fixture's raw LCP/CLS median lands in its expected web.dev band.

    The GOOD-band fixtures (healthy-all-green, green-trap, slow-lcp's CLS=0.03)
    classify "good"; the poor/boundary fixtures (slow-lcp LCP=4800, high-cls
    CLS=0.42) land in the right band. A pre-flag mismatch here is exactly the
    "page is fine but analysis claims a CWV problem" inversion the dim-7 gate
    is meant to catch before the judge runs.
    """
    page = digest_page(name)
    assert _lcp_band(page) == want_lcp, f"{name}: LCP band"
    assert _cls_band(page) == want_cls, f"{name}: CLS band"


def test_band_boundary_is_inclusive_good(digest_page) -> None:
    """A value sitting exactly on the GOOD cutoff classifies as good (inclusive).

    ``adversarial-number`` carries LCP=2500 ms and CLS=0.10 — both exactly on the
    GOOD boundary. The rubric wording is ``GOOD <= 2500 ms`` / ``GOOD <= 0.1``
    (inclusive lower bound), so the pre-flag must NOT over-flag a value that is
    precisely at the threshold. This is the adversarial trap fixture's whole job.
    """
    page = digest_page("adversarial-number")
    assert page.lcp_ms.median == c.LCP_GOOD_MS
    assert page.cls.median == c.CLS_GOOD
    assert _lcp_band(page) == "good"
    assert _cls_band(page) == "good"


def test_band_preflag_is_none_safe(digest_page) -> None:
    """A page with a missing CWV metric pre-flags as ``n/a`` (never raises)."""
    page = digest_page("partial-null")
    # partial-null has a null LCP capture — the pre-flag must degrade, not crash.
    assert _lcp_band(page) == "n/a"


def test_band_cutoffs_match_rubric() -> None:
    """FM-5 single-source-of-truth guard: cutoffs are verbatim in analysis.RUBRIC.

    Mirrors ``test_digest.py::test_rubric_frozen`` freeze discipline. If the
    rubric glossary and the ``constants`` cutoffs ever drift, the deterministic
    pre-flag and the paid judge would silently grade against different bands —
    so the four band numbers MUST appear verbatim in the frozen rubric text.
    """
    rubric = analysis.RUBRIC
    assert f"<= {c.LCP_GOOD_MS} ms" in rubric, "LCP good cutoff must match RUBRIC"
    assert f"> {c.LCP_POOR_MS} ms" in rubric, "LCP poor cutoff must match RUBRIC"
    assert f"<= {c.CLS_GOOD}" in rubric, "CLS good cutoff must match RUBRIC"
    assert f"> {c.CLS_POOR}" in rubric, "CLS poor cutoff must match RUBRIC"
