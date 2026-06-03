"""Recursive gzip-aware sitemap-index expander — CRAWL-02 / D-07.

``collect_sitemap_urls(start_urls, *, fetch)`` fetches each sitemap URL, expands
a ``<sitemapindex>`` recursively into its child ``<urlset>`` documents, decom-
presses gzip (``.gz`` suffix OR the gzip magic bytes), and returns the flat set
of ``<loc>`` URLs. It mirrors ``canonical.py``'s never-raise + deterministic-
fallback discipline at every parse boundary: a missing / malformed / empty /
gzip-corrupt sitemap is a SOFT NO-OP (skipped, contributes nothing) and NEVER
raises (D-07).

Two trap defenses (Pitfall 7, V5 input validation):

  - **recursion bound** — ``max_depth`` (defaults to
    ``SITEMAP_MAX_RECURSION_DEPTH``) caps nested-index expansion so a
    self-referential ``<sitemapindex>`` cannot infinite-loop.
  - **stdlib ElementTree** — CPython 3.12+ ``ET.fromstring`` does not expand
    external/DTD entities by default (billion-laughs defense); the recursion +
    soft-fail + (caller-side) bounded body size complete the V5 control.

``fetch`` is injected (tests pass a dict-backed stub; the discovery pass passes
the polite httpx client) and must return an object with ``.status_code`` and
``.content`` (raw bytes — gzip is detected on the bytes, not decoded text).
"""

import gzip
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable

from perfcrawl.constants import SITEMAP_MAX_RECURSION_DEPTH

# Module-level namespace constant (mirrors slug.py's module-top compiled regexes).
_SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
# gzip magic bytes — a sitemap may be gzipped without a ``.gz`` suffix.
_GZIP_MAGIC = b"\x1f\x8b"


def collect_sitemap_urls(
    start_urls: Iterable[str],
    *,
    fetch: Callable[[str], object],
    max_depth: int = SITEMAP_MAX_RECURSION_DEPTH,
    _depth: int = 0,
) -> set[str]:
    """Collect the flat set of ``<loc>`` URLs from ``start_urls`` (CRAWL-02 / D-07).

    Recursively expands nested ``<sitemapindex>`` documents (bounded by
    ``max_depth``), decompresses gzip, and soft-no-ops on any bad/missing/empty
    sitemap — never raising. Returns an empty set when nothing parses.
    """
    out: set[str] = set()
    if _depth > max_depth:  # Pitfall 7: recursion bound — sitemap-trap defense.
        return out
    for sm_url in start_urls:
        try:
            resp = fetch(sm_url)
            status = getattr(resp, "status_code", None)
            if status is None or not (200 <= status < 300):
                continue  # D-07: soft no-op on missing/bad fetch
            body = resp.content
            if not body:
                continue  # D-07: empty body is a soft no-op
            if sm_url.endswith(".gz") or body[:2] == _GZIP_MAGIC:
                body = gzip.decompress(body)
            root = ET.fromstring(body)  # CPython 3.12+: no external-entity expansion
        except Exception:
            continue  # D-07: never raise on a bad/corrupt sitemap
        tag = root.tag.removeprefix(_SM_NS)
        if tag == "sitemapindex":  # recurse into child sitemaps (bounded)
            children = [
                loc.text.strip()
                for loc in root.iter(f"{_SM_NS}loc")
                if loc.text
            ]
            out |= collect_sitemap_urls(
                children, fetch=fetch, max_depth=max_depth, _depth=_depth + 1
            )
        else:  # <urlset> (or anything else) — harvest its <loc> text
            for loc in root.iter(f"{_SM_NS}loc"):
                if loc.text:
                    out.add(loc.text.strip())
    return out
