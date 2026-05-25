"""The two "one editable place" registry tables for PerfCrawl.

Later phases extend these constants *here only* — call sites never inline
the denylist or hardcode metric direction.

- ``TRACKING_PARAM_DENYLIST`` (D-04): query-param keys dropped during URL
  canonicalization. Consumed by ``perfcrawl.canonical.canonical_key``.
- ``Polarity`` / ``METRIC_POLARITY`` (D-09): each metric's "which direction is
  better" declaration. The RunDelta engine (Plan 03) derives ``direction`` from
  this table and never hardcodes lower/higher-is-better at call sites.
"""

from enum import StrEnum

# --- D-04: tracking-param denylist (the ONE editable place) -----------------
# Query keys stripped during canonicalization so cosmetic/analytics params do
# not split one logical page into many canonical keys. Functional params
# (e.g. ?page=2, ?id=5) are deliberately NOT listed — they identify distinct
# resources and must be preserved (D-03/D-04, Pitfall 6).
TRACKING_PARAM_DENYLIST: list[str] = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "gclid",
    "fbclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "_ga",
    "ref",
    "ref_src",
]


# --- D-09: metric polarity (the ONE editable place) -------------------------
class Polarity(StrEnum):
    """Whether a smaller or larger value is the improvement for a metric."""

    LOWER_IS_BETTER = "lower"  # LCP, CLS, TBT/INP-proxy, TTFB, bytes, request count, slowest-ms
    HIGHER_IS_BETTER = "higher"  # Lighthouse perf / a11y / SEO / best-practices scores


# Maps a metric field name -> its Polarity. The RunDelta engine derives
# ``direction`` from this table (D-09); adding a metric is a one-line edit here.
METRIC_POLARITY: dict[str, Polarity] = {
    # lower-is-better
    "lcp_ms": Polarity.LOWER_IS_BETTER,
    "cls": Polarity.LOWER_IS_BETTER,
    "inp_proxy_tbt_ms": Polarity.LOWER_IS_BETTER,
    "ttfb_ms": Polarity.LOWER_IS_BETTER,
    "total_bytes": Polarity.LOWER_IS_BETTER,
    "request_count": Polarity.LOWER_IS_BETTER,
    "slowest_request_ms": Polarity.LOWER_IS_BETTER,
    # higher-is-better
    "perf_score": Polarity.HIGHER_IS_BETTER,
    "a11y_score": Polarity.HIGHER_IS_BETTER,
    "seo_score": Polarity.HIGHER_IS_BETTER,
    "best_practices_score": Polarity.HIGHER_IS_BETTER,
}
