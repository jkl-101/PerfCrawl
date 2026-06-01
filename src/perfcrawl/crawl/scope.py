"""Pure scope / filter / variant-cap predicates — CRAWL-05, D-06/D-08/D-13/D-14.

Three pure ``str -> value`` helpers the discovery BFS (plan 02) calls per URL.
They follow ``canonical.py``'s and ``slug.py``'s defensive shape EXACTLY: an
untrusted remote URL must NEVER crash the crawl, so each helper wraps its real
logic in ``try/except Exception: return <deterministic fallback>`` (threat
T-03-03 / V5 input validation). Same deterministic-fallback discipline; same
"one editable place" — the variant cap reuses ``canonical_key`` and never
re-derives canonicalization (Pitfall 3).

  - ``in_scope(url, seed, *, include_subdomains=False) -> bool`` (D-06):
    same registrable domain (``www.``-folded), scheme restricted to http/https.
    Cross-domain / internal-host / non-http links are dropped here BEFORE any
    later fetch — the SSRF-ish guard (threat T-03-01). Fallback on garbage = False
    (a URL we cannot parse is conservatively out of scope).

  - ``passes_filters(url, *, includes, excludes) -> bool`` (D-13/D-14):
    stdlib ``fnmatch`` globs; exclude-wins; empty includes = all in scope.
    Fallback on garbage = False.

  - ``VariantCounter(cap).admit(url) -> bool`` (D-08): a per-base-path counter
    keyed by ``urlsplit(url)._replace(query="").geturl()`` (scheme+host+path, no
    query — Open Question 3) that counts DISTINCT ``canonical_key(url)`` values
    per base path, admitting up to ``cap`` then rejecting. Bounds facet/calendar
    query-string explosion — one of the three independent termination bounds
    (threat T-03-02). Fallback on garbage = False (do not admit what we cannot key).
"""

from fnmatch import fnmatch
from urllib.parse import urlsplit

from perfcrawl.canonical import canonical_key


def _registrable(host: str) -> str:
    """The www-folded, lowercased registrable host for the D-06 scope compare.

    D-06: the studyhalo target is a plain ``.com``, so a www-fold + lowercase is
    sufficient (a full Public Suffix List / ``tldextract`` is only needed for
    multi-label TLDs like ``.co.uk`` — deferred per RESEARCH A3). ``www.`` is the
    one folded prefix (``www.studyhalo.com`` ≡ ``studyhalo.com``).
    """
    return (host or "").lower().removeprefix("www.")


def in_scope(url: str, seed: str, *, include_subdomains: bool = False) -> bool:
    """True iff ``url`` is in the crawl scope of ``seed`` (D-06). Never raises.

    Scope = same registrable domain (www-folded) + http/https scheme. Subdomains
    are OUT by default; ``include_subdomains=True`` admits any host ending in the
    seed's registrable domain. A malformed/garbage URL or seed returns ``False``
    (conservatively out of scope — threat T-03-03).
    """
    try:
        u, s = urlsplit(url), urlsplit(seed)
        if u.scheme not in ("http", "https"):  # D-06: only http/https is "same site"
            return False
        uh, sh = u.hostname or "", s.hostname or ""
        if not uh or not sh:  # cannot decide scope without both hosts
            return False
        if include_subdomains:  # --include-subdomains opt-in (D-06)
            reg = _registrable(sh)
            uhl = uh.lower()
            return uhl == sh.lower() or uhl.endswith("." + reg) or _registrable(uh) == reg
        return _registrable(uh) == _registrable(sh)
    except Exception:
        # Deterministic, never-raising fallback (threat T-03-03 / V5).
        return False


def passes_filters(url: str, *, includes: list[str], excludes: list[str]) -> bool:
    """True iff ``url`` survives the glob include/exclude filters (D-13/D-14).

    Exclude wins (any matching exclude glob drops the URL); with no includes,
    everything in scope passes; otherwise the URL must match ANY include glob.
    Never raises — garbage input returns ``False`` (threat T-03-03).
    """
    try:
        if any(fnmatch(url, pat) for pat in excludes):  # D-14: exclude wins
            return False
        if not includes:  # D-14: no --include = all in scope
            return True
        return any(fnmatch(url, pat) for pat in includes)  # D-14: include narrows
    except Exception:
        return False


def _base_path(url: str) -> str:
    """Base-path key for the variant cap: scheme+host+path, query/fragment dropped.

    Open Question 3 RESOLVED: the base path is the path component (no query), so
    a facet trap ``/products?color=…&size=…`` shares ONE base-path counter across
    all its query variants.
    """
    parts = urlsplit(url)
    return parts._replace(query="", fragment="").geturl()


class VariantCounter:
    """Per-base-path distinct-query-variant cap (D-08). Bounds query explosion.

    Tracks, per base path (scheme+host+path), the set of distinct
    ``canonical_key(url)`` values seen so far. ``admit(url)`` returns ``True`` for
    a genuinely new variant while the base path is under ``cap``, ``False`` once
    the base path is full (or on a variant already counted). One of the three
    independent termination bounds (threat T-03-02); layers ON ``canonical_key``
    and never re-derives canonicalization (Pitfall 3).
    """

    def __init__(self, cap: int) -> None:
        self._cap = cap
        # base-path key -> set of distinct canonical-key query-variants admitted
        self._seen: dict[str, set[str]] = {}

    def admit(self, url: str) -> bool:
        """True iff ``url`` is admitted under its base path's cap. Never raises.

        A URL whose canonical variant was already admitted re-returns ``True``
        without consuming a fresh slot (idempotent on the same logical variant);
        a new variant is admitted only while the base path holds < ``cap`` distinct
        variants. Garbage input returns ``False`` (do not admit an unkeyable URL —
        threat T-03-03).
        """
        try:
            base = _base_path(url)
            key = canonical_key(url)
            variants = self._seen.setdefault(base, set())
            if key in variants:  # same logical variant — does not consume a slot
                return True
            if len(variants) >= self._cap:  # base path full — reject the new variant
                return False
            variants.add(key)
            return True
        except Exception:
            # Deterministic, never-raising fallback (threat T-03-03 / V5).
            return False
