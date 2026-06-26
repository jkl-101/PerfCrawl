"""BFS discovery driver — CRAWL-01/02/03/04 + D-03/D-05.

``discover(seed, *, cfg, robots, fetch)`` turns a seed URL into the frozen
in-scope URL list (plus tagged error rows) that the measurement pass (plan 03)
consumes and ``--dry-run`` prints. It mirrors ``orchestrator.py``'s per-unit
driver shape: validate up front, loop with a HARD bound, build typed records,
return them.

The loop (Pattern 1):

  1. Seed the depth-0 frontier with the seed URL plus (when ``cfg.use_sitemap``)
     the recursively-expanded ``/sitemap.xml`` + robots ``Sitemap:`` directives.
     The explicit seed is admitted to the frontier regardless of the
     include/exclude filter (the seed-only free pass) so a homepage seed can be
     traversed to reach a filtered subtree; it is gated through ``in_scope`` + the
     denylist + login exclusion + robots + the variant cap. Sitemap-sourced seeds
     keep the ``passes_filters`` admission gate.
  2. On every ``popleft``: **break** once ``len(in_scope) >= cfg.max_pages``
     (the D-05 enqueue bound — Pitfall 1: a trap can never explode past the cap);
     **skip** if robots disallows the URL (CRAWL-03).
  3. ``fetch(url)`` with redirect-following; a final status outside 2xx becomes a
     status-only error ``PageResult`` (D-03 — never measured) and the URL is
     dropped; a 2xx page is appended to the in-scope (MEASURED) set ONLY when it
     passes the include/exclude filter — the filter is the MEASURE gate, applied
     here, not at seed frontier admission. (A non-matching seed is therefore
     traversed for discovery but not measured.)
  4. While ``depth < cfg.max_depth``, extract ``<a href>``, resolve each via
     ``urljoin`` + drop the fragment via ``urldefrag`` (Pitfall 6), and enqueue
     the candidate at ``depth+1`` iff its ``canonical_key`` is unseen AND it
     passes scope + filters + the per-base-path variant cap (D-06/D-08/D-14).
     Children always keep the ``passes_filters`` admission gate, so ``--max-pages``
     remains a traversal bound and a non-matching path trap is never fetched
     unboundedly.

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
from perfcrawl.crawl.scope import VariantCounter, in_scope, is_denied, passes_filters
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
        # IN-03: belt-and-suspenders only. stdlib HTMLParser is lenient and does
        # NOT raise on malformed markup, so this arm is effectively unreachable for
        # any HTML input — it exists purely so a future parser swap can't turn a
        # hostile page into a crawl-killing exception (V5 never-raise discipline).
        return []


def _sitemap_seeds(seed: str, *, robots: RobotsGate, fetch: Callable[[str], object]) -> set[str]:
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
    # WR-04: the login-URL exclusion key is loop-invariant — derive it ONCE here
    # rather than recomputing canonical_key(cfg.login_url) for every candidate.
    # Reusing the carried `key` for the candidate side (below) honors the same
    # WR-06 "compute once / never re-derive" discipline the frontier already uses,
    # closing the "key diverges from a later re-derived key if canonicalization is
    # ever non-idempotent" hazard the surrounding comments warn against.
    login_key = canonical_key(cfg.login_url) if cfg.login_url else None

    def _try_admit(url: str, depth: int, *, ignore_filters: bool = False) -> None:
        """Gate + enqueue a candidate once (IN-05: ONE admission path for all).

        The single key/seen → in_scope → [passes_filters] → denylist → login-excl
        → robots → variant-cap → seen.add → frontier.append sequence, shared by
        the depth-0 seed/sitemap loop and the depth>0 child loop so the gating is
        defined exactly once and cannot drift between the two (the hazard that made
        the WR-06 robots gate need touching in two places).

        ``ignore_filters`` (the seed-only free pass): when True, SKIP ONLY the
        include/exclude ``passes_filters`` check so the explicit user-provided seed
        is always admitted to the frontier for link discovery. Every other gate —
        scope, the D-05 denylist, AUTH-04 login exclusion, WR-06 robots, and the
        per-base-path variant cap — still applies, so the seed cannot bypass any
        termination/security bound; it only bypasses the MEASURE filter. The filter
        is re-applied at the in-scope append point below, which is what excludes a
        non-matching seed (e.g. a homepage that doesn't match ``--include``) from
        the measured set while still letting its links be discovered. Children are
        always passed ``ignore_filters=False`` so the filter keeps gating frontier
        admission for them (a non-matching child is neither traversed nor measured,
        exactly as before) — this preserves ``--max-pages`` as a traversal bound and
        keeps a non-matching path trap from being fetched unboundedly.

        WR-06: a robots-Disallow'd candidate is dropped here, BEFORE it consumes a
        per-base-path variant-cap slot (CRAWL-03 intent). A sitemap commonly
        advertises URLs robots.txt disallows; admitting them to the frontier and
        the cap would crowd out crawlable siblings. The main-loop can_fetch stays
        as defense-in-depth.
        """
        key = canonical_key(url)
        if not key or key in seen:
            return
        if not in_scope(url, seed, include_subdomains=cfg.include_subdomains):
            return
        if not ignore_filters and not passes_filters(
            url, includes=cfg.includes, excludes=cfg.excludes
        ):
            return
        # D-05 / T-04-04: always-on destructive-link denylist. Placed EARLY (before
        # robots and the variant cap) so a denied URL never consumes a per-base-path
        # cap slot — the SAME WR-06 robots-before-cap rationale documented above:
        # admitting a destructive link to the frontier+cap would crowd out crawlable
        # siblings, and a /logout/ must be structurally unreachable, not merely
        # deprioritized. is_denied is fail-CLOSED (garbage -> denied).
        if is_denied(url, patterns=cfg.deny_patterns):
            return
        # AUTH-04 / D-07 / T-04-05: exclude the configured login URL from the audited
        # set — the login form may echo submitted credentials into a captured
        # artifact. Done once here, in the single admission path (IN-05), so the
        # exclusion cannot drift across call sites. WR-04: reuse the already-computed
        # `key` and the loop-invariant `login_key` rather than re-deriving either.
        if login_key is not None and key == login_key:
            return
        if not robots.can_fetch(url):
            return
        if not variants.admit(url):  # D-08: per-base-path variant cap
            return
        seen.add(key)
        # WR-06: carry the already-computed key through the frontier (compute once).
        frontier.append((url, key, depth))

    # --- depth-0 seeds: the seed itself + (optional) sitemap-sourced URLs ---
    # The explicit user-provided seed gets the include/exclude free pass
    # (ignore_filters=True) so "seed the homepage, measure only the /dashboard/
    # subtree" traverses via the seed instead of failing "0 discovered". It is
    # measured only if it ALSO matches the filter (re-checked at the in-scope
    # append below). Sitemap-sourced seeds keep the filter — only the explicit
    # seed gets the traversal free pass.
    _try_admit(seed, 0, ignore_filters=True)
    if cfg.use_sitemap:
        for sm_url in _sitemap_seeds(seed, robots=robots, fetch=fetch):
            _try_admit(sm_url, 0)

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
        # The include/exclude filter is the MEASURE gate: a page is appended to the
        # measured set ONLY when it passes the filter. The seed gets a frontier free
        # pass (ignore_filters=True above) so it is always traversed for discovery,
        # but a non-matching seed (e.g. a homepage that doesn't match --include) is
        # excluded here from the measured set. For non-seed children this re-check is
        # redundant (they already cleared the same filter at admission); it is the
        # seed where it does the real work.
        if passes_filters(url, includes=cfg.includes, excludes=cfg.excludes):
            in_scope_results.append(InScope(url=url, depth=depth, url_key=key))
        if depth < cfg.max_depth:  # CRAWL-04: don't expand past the depth bound
            # Always traverse for discovery regardless of whether THIS page was
            # measured — this is how a non-matching seed still yields its matching
            # descendants. Children are gated by the filter at admission.
            html = getattr(resp, "text", "") or ""
            for href in _extract_links(html):
                child = urldefrag(urljoin(url, href)).url  # Pitfall 6
                # IN-05: children go through the SAME admission path as the seeds.
                _try_admit(child, depth + 1)

    return in_scope_results, errors
