"""Unit tests for the BFS discovery driver (CRAWL-01/02/03/04 + D-03 / Success #5).

Pins, per decision ID:
  - CRAWL-01: ``discover()`` follows same-origin ``<a href>`` from a seed via BFS,
    resolves relative hrefs (urljoin) + drops fragments (urldefrag), dedupes by
    ``canonical_key``, and tracks depth. The fixture's out-of-scope cross-domain
    link and non-http (mailto/javascript) links are never enqueued.
  - CRAWL-02 / D-07: sitemap URLs are merged as depth-0 seeds, filtered through
    scope+filters (covered indirectly here; the recursion itself is in test_sitemap).
  - CRAWL-03: robots ``Disallow`` URLs are skipped unless ``ignore_robots``.
  - CRAWL-04 (``test_caps``): max_depth bounds BFS depth; max_pages bounds the
    count of in-scope (measured-eligible) results.
  - D-03 / Success #5 (``test_error_tagging``): the fixture's 404 link becomes a
    PageResult with status_code set + every metric None, and is ABSENT from the
    in-scope measurement list.

Strategy: a real ``httpx.Client`` against the plan-01 ``local_server`` fixture
(no network egress — loopback only). The ``RobotsGate`` is constructed in-test
(allow-all by default; a Disallow gate for the robots-skip test).
"""

import httpx
import pytest

from perfcrawl.constants import CRAWLER_USER_AGENT
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import InScope, discover
from perfcrawl.crawl.robots import RobotsGate
from perfcrawl.models import PageResult


@pytest.fixture
def client():
    """A loopback httpx client with redirect-following + a short timeout."""
    with httpx.Client(
        follow_redirects=True,
        timeout=5.0,
        headers={"user-agent": CRAWLER_USER_AGENT},
    ) as c:
        yield c


def _allow_all() -> RobotsGate:
    return RobotsGate(None)  # 404/missing robots = allow-all


def _fetch(client):
    def fetch(url: str):
        return client.get(url)

    return fetch


def test_bfs_follows_same_origin_links(local_server, client):
    """CRAWL-01: BFS discovers about+blog (and their variants), dedupes self/fragment."""
    cfg = CrawlConfig(use_sitemap=False)
    seed = local_server + "/index.html"
    in_scope, errors = discover(
        seed, cfg=cfg, robots=_allow_all(), fetch=_fetch(client)
    )
    found = {r.url for r in in_scope}
    # seed + the two in-scope linked pages are discovered.
    assert any(u.endswith("/index.html") for u in found)
    assert any(u.endswith("/about.html") for u in found)
    assert any(u.endswith("/blog.html") for u in found)


def test_out_of_scope_link_dropped(local_server, client):
    """CRAWL-01: the cross-domain + non-http links are never enqueued."""
    cfg = CrawlConfig(use_sitemap=False)
    seed = local_server + "/index.html"
    in_scope, _ = discover(
        seed, cfg=cfg, robots=_allow_all(), fetch=_fetch(client)
    )
    found = {r.url for r in in_scope}
    assert not any("other.example.com" in u for u in found)
    assert not any(u.startswith("mailto:") for u in found)
    assert not any(u.startswith("javascript:") for u in found)


def test_depth_is_tracked_and_seed_is_zero(local_server, client):
    """CRAWL-01: the seed is depth 0; linked pages are depth 1."""
    cfg = CrawlConfig(use_sitemap=False)
    seed = local_server + "/index.html"
    in_scope, _ = discover(
        seed, cfg=cfg, robots=_allow_all(), fetch=_fetch(client)
    )
    by_url = {r.url: r.depth for r in in_scope}
    seed_depth = next(d for u, d in by_url.items() if u.endswith("/index.html"))
    assert seed_depth == 0
    about_depth = next(
        (d for u, d in by_url.items() if u.endswith("/about.html")), None
    )
    assert about_depth == 1


def test_returns_inscope_dataclass(local_server, client):
    """CRAWL-01: results are InScope(url, depth) records."""
    cfg = CrawlConfig(use_sitemap=False)
    seed = local_server + "/index.html"
    in_scope, _ = discover(
        seed, cfg=cfg, robots=_allow_all(), fetch=_fetch(client)
    )
    assert in_scope and all(isinstance(r, InScope) for r in in_scope)


def test_robots_disallow_skips_url(local_server, client):
    """CRAWL-03: a robots Disallow on /about.html drops it from the in-scope list."""
    # The fixture serves /about.html; a Disallow of its path must skip it.
    robots_txt = "User-agent: *\nDisallow: /about.html\n"
    gate = RobotsGate(robots_txt)
    cfg = CrawlConfig(use_sitemap=False)
    seed = local_server + "/index.html"
    in_scope, _ = discover(seed, cfg=cfg, robots=gate, fetch=_fetch(client))
    found = {r.url for r in in_scope}
    assert not any(u.endswith("/about.html") for u in found)
    # blog.html is NOT disallowed and is still discovered.
    assert any(u.endswith("/blog.html") for u in found)


def test_ignore_robots_overrides_disallow(local_server, client):
    """CRAWL-03: ignore_robots=True re-admits a Disallow'd page."""
    robots_txt = "User-agent: *\nDisallow: /about.html\n"
    gate = RobotsGate(robots_txt, ignore=True)
    cfg = CrawlConfig(use_sitemap=False)
    seed = local_server + "/index.html"
    in_scope, _ = discover(seed, cfg=cfg, robots=gate, fetch=_fetch(client))
    found = {r.url for r in in_scope}
    assert any(u.endswith("/about.html") for u in found)


def test_caps(local_server, client):
    """CRAWL-04: max_pages bounds the in-scope count; max_depth bounds BFS depth."""
    cfg = CrawlConfig(use_sitemap=False, max_pages=2, max_depth=1)
    seed = local_server + "/index.html"
    in_scope, _ = discover(
        seed, cfg=cfg, robots=_allow_all(), fetch=_fetch(client)
    )
    assert len(in_scope) <= 2  # max_pages bound
    assert all(r.depth <= 1 for r in in_scope)  # max_depth bound

    # depth-0-only cap: with max_depth=0 the seed is measured but nothing deeper.
    cfg0 = CrawlConfig(use_sitemap=False, max_pages=100, max_depth=0)
    in_scope0, _ = discover(
        seed, cfg=cfg0, robots=_allow_all(), fetch=_fetch(client)
    )
    assert all(r.depth == 0 for r in in_scope0)
    assert len(in_scope0) == 1


def test_error_tagging(local_server, client):
    """D-03 / Success #5: the 404 link is a status-only error row, excluded from in-scope."""
    cfg = CrawlConfig(use_sitemap=False)
    seed = local_server + "/index.html"
    in_scope, errors = discover(
        seed, cfg=cfg, robots=_allow_all(), fetch=_fetch(client)
    )
    # The fixture's missing.html link 404s → one error row.
    err = next((e for e in errors if e.url.endswith("/missing.html")), None)
    assert err is not None
    assert isinstance(err, PageResult)
    assert err.status_code == 404
    # Every metric field is None on an error row.
    assert err.perf_score is None
    assert err.lcp_ms is None
    assert err.ttfb_ms is None
    assert err.request_count is None
    # url_key is set (canonical key); the error row never appears in-scope.
    assert err.url_key
    assert not any(r.url.endswith("/missing.html") for r in in_scope)
