"""Canonical URL key derivation — success criterion #4, D-01..D-05.

``canonical_key(url)`` derives the stable cross-run page-identity key used to
self-join the same logical page across runs (D-01: the raw URL is stored
separately and never mutated — this function only *derives* the key).

w3lib does the RFC-3986 heavy lifting (lowercase scheme+host, uppercase
percent-hex, sort the remaining query, drop the fragment). This thin wrapper
adds the three things w3lib does NOT do and the project requires:

  - drop tracking params (D-04) via ``url_query_cleaner`` + the ONE editable
    denylist in ``perfcrawl.registry``;
  - strip default ports ``:80`` / ``:443`` (D-02 — verified at execution time
    that w3lib leaves these in place);
  - strip the trailing slash except for root ``/`` (D-03).

It deliberately does NOT strip ``www`` or ``index.html`` and keeps functional
query params, so genuinely distinct pages are never over-merged (D-03/D-04,
Pitfall 6). Malformed / non-URL input returns a deterministic value without
raising (Security Domain DoS mitigation, threat T-01-01).
"""

from urllib.parse import urlsplit, urlunsplit

from w3lib.url import canonicalize_url, url_query_cleaner

from perfcrawl.registry import TRACKING_PARAM_DENYLIST

# Default ports that carry no identity and are stripped per D-02. w3lib's
# canonicalize_url leaves these in the netloc, so the wrapper removes them.
_DEFAULT_PORTS: dict[str, str] = {"http": "80", "https": "443"}


def _strip_default_port(scheme: str, netloc: str) -> str:
    """Remove a default port (:80 for http, :443 for https) from ``netloc`` (D-02)."""
    default = _DEFAULT_PORTS.get(scheme)
    if default and netloc.endswith(f":{default}"):
        return netloc[: -(len(default) + 1)]
    return netloc


def canonical_key(url: str) -> str:
    """Derive the canonical cross-run identity key for ``url`` (D-01..D-05).

    Never raises on malformed input — returns a deterministic string so an
    untrusted/hostile URL cannot crash the pipeline (threat T-01-01).
    """
    try:
        # 1) Drop tracking params (D-04). remove=True drops the denylisted keys;
        #    keep_fragments=False so the fragment never survives this stage.
        cleaned = url_query_cleaner(
            url, TRACKING_PARAM_DENYLIST, remove=True, keep_fragments=False
        )
        # 2) RFC-3986 normalize: lowercase scheme+host, uppercase %-hex, sort the
        #    remaining query, drop the fragment (D-02 / D-04 / D-05).
        canon = canonicalize_url(cleaned, keep_fragments=False)
        # 3) Wrapper-only rules w3lib does not apply:
        parts = urlsplit(canon)
        netloc = _strip_default_port(parts.scheme, parts.netloc)  # D-02 default-port strip
        path = parts.path
        if len(path) > 1 and path.endswith("/"):  # D-03 trailing slash (except root)
            path = path.rstrip("/")
        return urlunsplit((parts.scheme, netloc, path or "/", parts.query, ""))
    except Exception:
        # Deterministic, never-raising fallback for non-URL / malformed input.
        return (url or "").strip()
