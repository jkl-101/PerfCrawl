"""Scope / filter / variant-cap predicate tests — CRAWL-05, D-06/D-08/D-13/D-14.

These pin the observable behavior of the three pure, never-raising predicates the
discovery BFS (plan 02) calls per URL:

  - ``in_scope(url, seed)``    — same-registrable-domain + www-fold, http/https only
                                 (D-06); drops cross-domain / non-http / malformed.
  - ``passes_filters(url)``    — repeatable glob include/exclude, exclude-wins,
                                 no-include=all (D-13/D-14).
  - the variant-cap helper     — per-base-path distinct query-variant cap (D-08).

Like ``tests/test_canonical.py``, the predicates NEVER raise, so this file is
``pytest.raises``-free; a malformed URL is asserted to return a deterministic bool.
One test function per behavior; docstrings lead with the requirement/decision IDs.
"""

from perfcrawl.crawl.scope import VariantCounter, in_scope, passes_filters

SEED = "https://www.studyhalo.com"


# --- in_scope: registrable domain + www-fold (D-06) ------------------------


def test_same_domain_www_fold_in_scope():
    """D-06: www and apex of the same registrable domain are in scope (www-fold)."""
    assert in_scope("https://studyhalo.com/x", SEED) is True
    assert in_scope("https://www.studyhalo.com/x", "https://studyhalo.com") is True


def test_http_scheme_in_scope():
    """D-06: http and https are treated as the same site (scheme normalized)."""
    assert in_scope("http://studyhalo.com/x", SEED) is True


def test_subdomain_excluded_by_default():
    """D-06: subdomains are OUT by default."""
    assert in_scope("https://blog.studyhalo.com/x", SEED) is False


def test_subdomain_opt_in():
    """D-06: --include-subdomains opts subdomains back in."""
    assert in_scope("https://blog.studyhalo.com/x", SEED, include_subdomains=True) is True
    # apex itself still in scope under the opt-in
    assert in_scope("https://studyhalo.com/x", SEED, include_subdomains=True) is True


def test_cross_domain_excluded():
    """D-06 / T-03-01: a different registrable domain is out of scope (SSRF guard)."""
    assert in_scope("https://other.example.com/x", SEED) is False
    assert in_scope("https://other.example.com/x", SEED, include_subdomains=True) is False


def test_non_http_schemes_excluded():
    """D-06: mailto/javascript/tel are not http/https → out of scope."""
    assert in_scope("mailto:a@b.com", SEED) is False
    assert in_scope("javascript:void(0)", SEED) is False
    assert in_scope("tel:+15551234", SEED) is False
    assert in_scope("ftp://studyhalo.com/x", SEED) is False


def test_malformed_url_never_raises():
    """T-03-03: a garbage URL returns a deterministic bool, never raises."""
    for bad in ["::::not a url", "", "   ", "http://", "://broken"]:
        result = in_scope(bad, SEED)
        assert isinstance(result, bool)
        # deterministic: same input → same output
        assert in_scope(bad, SEED) == result


def test_malformed_seed_never_raises():
    """T-03-03: a garbage seed also yields a deterministic bool, never raises."""
    result = in_scope("https://studyhalo.com/x", "::::not a seed")
    assert isinstance(result, bool)


# --- passes_filters: glob include/exclude, exclude-wins (D-13/D-14) --------


def test_filters():
    """CRAWL-05 / D-14: exclude-wins, no-include=all, include narrows.

    Combines the three filter behaviors the plan's <behavior> block pins:
      - excludes drop matching URLs; non-matching pass (no-include = all)
      - includes narrow to URLs matching ANY include glob
      - exclude takes precedence over include
    """
    cal = "https://studyhalo.com/events/calendar/2027"
    blog = "https://studyhalo.com/blog/post-1"
    draft = "https://studyhalo.com/blog/draft/post-2"

    # exclude-wins + no-include = all in scope (D-14)
    assert passes_filters(cal, includes=[], excludes=["*/calendar/*"]) is False
    assert passes_filters(blog, includes=[], excludes=["*/calendar/*"]) is True

    # include narrows to URLs matching ANY include glob (D-14)
    assert passes_filters(blog, includes=["*/blog/*"], excludes=[]) is True
    assert passes_filters(cal, includes=["*/blog/*"], excludes=[]) is False

    # exclude precedence: a draft blog URL matches the include but the exclude wins
    assert passes_filters(draft, includes=["*/blog/*"], excludes=["*/blog/draft/*"]) is False
    assert passes_filters(blog, includes=["*/blog/*"], excludes=["*/blog/draft/*"]) is True


def test_filters_never_raise_on_malformed():
    """T-03-03: passes_filters returns a deterministic bool on garbage input."""
    assert isinstance(passes_filters("::::bad", includes=[], excludes=[]), bool)


# --- variant cap: per-base-path query-variant bound (D-08) -----------------


def test_variant_cap():
    """CRAWL-05 / D-08: a per-base-path query-variant cap bounds query explosion.

    Fed 25 distinct ``?`` query variants of ONE base path with cap=10, exactly 10
    are admitted and the rest rejected. Distinct base paths each keep their own
    count (base path = scheme+host+path, no query — Open Question 3).
    """
    counter = VariantCounter(cap=10)
    base = "https://studyhalo.com/products"

    admitted = sum(1 for i in range(25) if counter.admit(f"{base}?color={i}"))
    assert admitted == 10  # exactly cap admitted, the other 15 rejected

    # a distinct base path gets its OWN independent count (not consumed above)
    other = "https://studyhalo.com/courses"
    admitted_other = sum(1 for i in range(25) if counter.admit(f"{other}?q={i}"))
    assert admitted_other == 10


def test_variant_cap_dedups_same_canonical_variant():
    """D-08: re-offering the SAME canonical variant does not consume a fresh slot.

    The cap counts DISTINCT canonical query-variants per base path; two spellings
    that canonicalize to one key share a single slot (variant cap layers on
    canonical_key, it does not re-derive canonicalization).
    """
    counter = VariantCounter(cap=2)
    base = "https://studyhalo.com/products"
    # same logical variant offered twice (trailing-slash / utm spelling differences)
    assert counter.admit(f"{base}?page=2") is True
    assert counter.admit(f"{base}?page=2&utm_source=x") is True  # utm dropped → same key
    # only one distinct variant so far; a genuinely new one still fits under cap=2
    assert counter.admit(f"{base}?page=3") is True
    # cap now full for this base path
    assert counter.admit(f"{base}?page=4") is False


def test_variant_cap_never_raises_on_malformed():
    """T-03-03: the variant cap admits/rejects deterministically on garbage input."""
    counter = VariantCounter(cap=1)
    result = counter.admit("::::not a url")
    assert isinstance(result, bool)
