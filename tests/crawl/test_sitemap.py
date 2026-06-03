"""Unit tests for the recursive gzip-aware sitemap expander (CRAWL-02 / D-07).

Pins, per decision ID:
  - CRAWL-02: a flat ``<urlset>`` yields its ``<loc>`` URLs; a ``<sitemapindex>``
    recursively expands into its child ``<urlset>`` (incl. a ``.xml.gz`` child that
    must be gzip-decompressed before parse).
  - D-07: a malformed / empty / missing / garbage-bytes sitemap is a SOFT NO-OP —
    ``collect_sitemap_urls`` returns an empty set and NEVER raises.
  - Pitfall 7: a self-referential ``<sitemapindex>`` is bounded by
    ``SITEMAP_MAX_RECURSION_DEPTH`` and does not infinite-loop.

Strategy mirrors ``tests/test_canonical.py``: one test fn per behavior. ``fetch``
is injected as a dict-backed stub returning a tiny response object with
``.status_code`` + ``.content`` — no network. The fixtures
(``sitemap.xml``, ``sitemap-index.xml``, ``sitemap-child.xml.gz``) are the plan-01
substrate, served by absolute URL keys.
"""

from perfcrawl.constants import SITEMAP_MAX_RECURSION_DEPTH
from perfcrawl.crawl.sitemap import collect_sitemap_urls


class _Resp:
    """Minimal duck-typed response: ``.status_code`` + ``.content`` (bytes)."""

    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code


def _make_fetch(mapping: dict[str, _Resp]):
    """A fetch(url)->_Resp stub; unknown URLs return a 404 (soft no-op path)."""

    def fetch(url: str) -> _Resp:
        return mapping.get(url, _Resp(b"", status_code=404))

    return fetch


def test_flat_urlset_returns_locs(fixtures_dir):
    """CRAWL-02: a flat ``<urlset>`` returns its ``<loc>`` URLs."""
    body = (fixtures_dir / "sitemap.xml").read_bytes()
    fetch = _make_fetch({"https://studyhalo.com/sitemap.xml": _Resp(body)})
    urls = collect_sitemap_urls(
        ["https://studyhalo.com/sitemap.xml"], fetch=fetch
    )
    assert urls == {
        "https://studyhalo.com/",
        "https://studyhalo.com/about",
        "https://studyhalo.com/blog",
    }


def test_nested_index_recurses_into_gzip_child(fixtures_dir):
    """CRAWL-02: a ``<sitemapindex>`` recurses into a gzip ``.xml.gz`` child urlset."""
    index = (fixtures_dir / "sitemap-index.xml").read_bytes()
    child_gz = (fixtures_dir / "sitemap-child.xml.gz").read_bytes()
    fetch = _make_fetch(
        {
            "https://studyhalo.com/sitemap-index.xml": _Resp(index),
            "https://studyhalo.com/sitemap-child.xml.gz": _Resp(child_gz),
        }
    )
    urls = collect_sitemap_urls(
        ["https://studyhalo.com/sitemap-index.xml"], fetch=fetch
    )
    assert urls == {
        "https://studyhalo.com/courses",
        "https://studyhalo.com/pricing",
    }


def test_missing_sitemap_is_soft_noop():
    """D-07: a 404 sitemap returns an empty set, never raises."""
    fetch = _make_fetch({})  # every URL 404s
    assert collect_sitemap_urls(["https://x.com/sitemap.xml"], fetch=fetch) == set()


def test_garbage_bytes_is_soft_noop():
    """D-07: malformed/garbage XML returns an empty set, never raises."""
    fetch = _make_fetch(
        {"https://x.com/sitemap.xml": _Resp(b"\x00\x01not-xml-at-all<<<")}
    )
    assert collect_sitemap_urls(["https://x.com/sitemap.xml"], fetch=fetch) == set()


def test_empty_body_is_soft_noop():
    """D-07: an empty body returns an empty set, never raises."""
    fetch = _make_fetch({"https://x.com/sitemap.xml": _Resp(b"")})
    assert collect_sitemap_urls(["https://x.com/sitemap.xml"], fetch=fetch) == set()


def test_self_referential_index_is_recursion_bounded():
    """Pitfall 7: a self-referential sitemapindex does not infinite-loop (bounded)."""
    loop = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://x.com/loop.xml</loc></sitemap>"
        b"</sitemapindex>"
    )
    fetch = _make_fetch({"https://x.com/loop.xml": _Resp(loop)})
    # Must RETURN (not hang) and yield no urlset locs — the index only ever
    # points at itself; recursion is bounded by SITEMAP_MAX_RECURSION_DEPTH.
    assert SITEMAP_MAX_RECURSION_DEPTH >= 1
    assert collect_sitemap_urls(["https://x.com/loop.xml"], fetch=fetch) == set()
