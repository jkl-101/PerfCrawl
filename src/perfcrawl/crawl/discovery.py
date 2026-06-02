"""BFS discovery driver — CRAWL-01/02/03/04 + D-03/D-05.

``discover(seed, *, cfg, robots, fetch)`` turns a seed URL into the frozen
in-scope URL list (plus tagged error rows) that the measurement pass (plan 03)
consumes and ``--dry-run`` prints. It mirrors ``orchestrator.py``'s per-unit
driver shape: validate up front, loop with a HARD bound, build typed records,
return them.

The loop (Pattern 1):

  1. Seed the depth-0 frontier with the seed URL plus (when ``cfg.use_sitemap``)
     the recursively-expanded ``/sitemap.xml`` + robots ``Sitemap:`` directives,
     each gated through ``in_scope`` + ``passes_filters`` + the variant cap.
  2. On every ``popleft``: **break** once ``len(in_scope) >= cfg.max_pages``
     (the D-05 enqueue bound — Pitfall 1: a trap can never explode past the cap);
     **skip** if robots disallows the URL (CRAWL-03).
  3. ``fetch(url)`` with redirect-following; a final status outside 2xx becomes a
     status-only error ``PageResult`` (D-03 — never measured) and the URL is
     dropped; a 2xx becomes an ``InScope(url, depth)``.
  4. While ``depth < cfg.max_depth``, extract ``<a href>``, resolve each via
     ``urljoin`` + drop the fragment via ``urldefrag`` (Pitfall 6), and enqueue
     the candidate at ``depth+1`` iff its ``canonical_key`` is unseen AND it
     passes scope + filters + the per-base-path variant cap (D-06/D-08/D-14).

Three independent termination bounds (Pattern 5, threat T-03-04) — max-depth,
the max-pages enqueue bound, and the variant cap — together with the
``canonical_key`` visited set guarantee the BFS RETURNS even on a calendar/facet
trap (proven by ``tests/crawl/test_termination.py``).

``fetch`` is injectable (tests pass a loopback httpx client; the CLI passes the
polite httpx client) and must return an object with ``.status_code`` and
``.text`` (HTML) / ``.content`` (sitemap bytes). ``sleep`` is injectable so the
per-host politeness delay (``robots.effective_delay``) can be stubbed out in
tests; in production it throttles successive GETs (threat T-03-07).
"""

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

from perfcrawl.canonical import canonical_key
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.robots import RobotsGate
from perfcrawl.crawl.scope import VariantCounter, in_scope, passes_filters
from perfcrawl.crawl.sitemap import collect_sitemap_urls
from perfcrawl.models import PageResult


@dataclass(frozen=True)
class InScope:
    """A discovered, in-scope, measured-eligible URL + its BFS depth.

    ``url`` is the URL as discovered (never mutated — D-01); ``depth`` is the BFS
    distance from the seed (sitemap-seeded URLs are depth 0).

    WR-02: ``url_key`` is the ``canonical_key`` computed ONCE at admit time and
    carried through the frontier (the WR-06 discipline), so the measurement pass
    reuses the discovery key for a page's error-row sibling rather than
    re-deriving it — keeping the dedup map and store url_key uniqueness in lockstep
    with discovery's visited set even if canonicalization is ever non-idempotent.
    """

    url: str
    depth: int
    url_key: str


class _LinkExtractor(HTMLParser):
    """Collect ``<a href>`` values from untrusted HTML — never raises (V5).

    stdlib ``HTMLParser`` is lenient (it does not raise on malformed markup),
    so a hostile/broken page yields whatever hrefs it can find and nothing else.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def _extract_links(html: str) -> list[str]:
    """Extract ``<a href>`` values from HTML; soft-fail to ``[]`` (never raises)."""
    try:
        parser = _LinkExtractor()
        parser.feed(html)
        return parser.hrefs
    except Exception:
        return []


def _sitemap_seeds(
    seed: str, *, robots: RobotsGate, fetch: Callable[[str], object]
) -> set[str]:
    """Collect depth-0 sitemap seeds: ``/sitemap.xml`` + robots ``Sitemap:`` dirs.

    Soft no-op on anything that does not parse (D-07) — ``collect_sitemap_urls``
    already never raises.
    """
    parts = urlsplit(seed)
    default_sm = parts._replace(path="/sitemap.xml", query="", fragment="").geturl()
    start = [default_sm, *robots.sitemaps]
    return collect_sitemap_urls(start, fetch=fetch)


def discover(
    seed: str,
    *,
    cfg: CrawlConfig,
    robots: RobotsGate,
    fetch: Callable[[str], object],
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[InScope], list[PageResult]]:
    """Run the BFS discovery pass from ``seed`` (CRAWL-01/02/03/04 + D-03/D-05).

    Returns ``(in_scope, errors)`` where ``in_scope`` is the bounded list of
    measured-eligible ``InScope(url, depth)`` records and ``errors`` is the list
    of status-only error ``PageResult`` rows (non-2xx after redirect-following).
    Provably terminates: bounded by max-depth + the max-pages enqueue bound + the
    per-base-path variant cap + the ``canonical_key`` visited set.
    """
    seen: set[str] = set()  # canonical keys already enqueued (dedup + visited)
    # WR-06: carry the canonical key THROUGH the frontier as (url, key, depth) so
    # it is computed exactly once at admit time and reused for the error row /
    # in-scope record. This avoids recompute and removes the consistency risk of
    # an enqueued key diverging from a later re-derived key if canonicalization is
    # ever non-idempotent for an edge input.
    frontier: deque[tuple[str, str, int]] = deque()
    in_scope_results: list[InScope] = []
    errors: list[PageResult] = []
    variants = VariantCounter(cfg.query_variant_cap)
    delay = robots.effective_delay

    def _admit_seed(url: str) -> None:
        """Gate + enqueue a depth-0 candidate (seed/sitemap) once."""
        key = canonical_key(url)
        if not key or key in seen:
            return
        if not in_scope(url, seed, include_subdomains=cfg.include_subdomains):
            return
        if not passes_filters(url, includes=cfg.includes, excludes=cfg.excludes):
            return
        # WR-06: drop a robots-Disallow'd candidate as early as scope does
        # (CRAWL-03 intent), BEFORE it consumes a per-base-path variant-cap slot.
        # A sitemap commonly advertises URLs robots.txt disallows; admitting them
        # to the frontier (and the variant cap) would crowd out crawlable siblings
        # under a tight cap. The main-loop can_fetch check stays as defense-in-depth.
        if not robots.can_fetch(url):
            return
        if not variants.admit(url):
            return
        seen.add(key)
        frontier.append((url, key, 0))

    # --- depth-0 seeds: the seed itself + (optional) sitemap-sourced URLs ---
    _admit_seed(seed)
    if cfg.use_sitemap:
        for sm_url in _sitemap_seeds(seed, robots=robots, fetch=fetch):
            _admit_seed(sm_url)

    fetched_any = False  # WR-04: gates the per-host delay to BEFORE the next fetch
    while frontier:
        if len(in_scope_results) >= cfg.max_pages:  # D-05: stop at the cap
            break
        url, key, depth = frontier.popleft()
        if not robots.can_fetch(url):  # CRAWL-03: robots Disallow (unless ignore)
            continue
        # WR-04 (threat T-03-07): apply the politeness delay BEFORE a real GET,
        # not after one. This drops the trailing dead-time sleep after the final
        # frontier item and the pointless sleep against a host that errored — the
        # delay only ever spaces out actual successive requests to the host.
        if delay and fetched_any:
            sleep(delay)
        fetched_any = True
        try:
            resp = fetch(url)
        except Exception:
            # A transport-level failure is a tagged error row, not a crash (D-03).
            # WR-06: reuse the key computed at admit time, never re-derive it.
            errors.append(PageResult(url=url, url_key=key, status_code=None))
            continue
        status = getattr(resp, "status_code", None)
        if status is None or not (200 <= status < 300):  # D-03: tag + exclude non-2xx
            errors.append(PageResult(url=url, url_key=key, status_code=status))
            continue

        # WR-02: carry the admit-time canonical key into the in-scope record so the
        # measurement pass reuses it instead of re-deriving it downstream.
        in_scope_results.append(InScope(url=url, depth=depth, url_key=key))
        if depth < cfg.max_depth:  # CRAWL-04: don't expand past the depth bound
            html = getattr(resp, "text", "") or ""
            for href in _extract_links(html):
                child = urldefrag(urljoin(url, href)).url  # Pitfall 6
                ckey = canonical_key(child)
                if not ckey or ckey in seen:
                    continue
                if not in_scope(
                    child, seed, include_subdomains=cfg.include_subdomains
                ):
                    continue
                if not passes_filters(
                    child, includes=cfg.includes, excludes=cfg.excludes
                ):
                    continue
                # WR-06: gate robots Disallow at admit time so a disallowed child
                # is never enqueued or counted against the per-base-path variant
                # cap; the main-loop can_fetch stays as defense-in-depth.
                if not robots.can_fetch(child):
                    continue
                if not variants.admit(child):  # D-08: per-base-path variant cap
                    continue
                seen.add(ckey)
                # WR-06: carry the already-computed child key through the frontier.
                frontier.append((child, ckey, depth + 1))

    return in_scope_results, errors
