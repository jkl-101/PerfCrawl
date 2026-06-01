"""Protego robots.txt gate — CRAWL-03 / D-07 / D-11.

A thin defensive wrapper over ``protego`` (the way ``canonical.py`` wraps
``w3lib``): parse robots.txt once per host, gate every candidate fetch through
``can_fetch``, expose the effective politeness delay and the ``Sitemap:``
directives. The project-specific rules ``protego`` does NOT impose are added
here:

  - **404 / missing robots.txt = allow-all** (Pitfall 4): a missing policy is
    NOT block-all and NOT an error. ``robots_txt=None`` → ``_rp is None`` →
    ``can_fetch`` returns True for everything.
  - **``--ignore-robots`` (D-11)**: ``ignore=True`` short-circuits ``can_fetch``
    to True for owned sites; the CLI surfaces this loudly on stderr (plan 03).
  - **stricter-of Crawl-delay (D-11)**: ``effective_delay`` is the MAX of the
    configured default and the robots ``Crawl-delay`` for the UA — the politer
    of the two always wins.

``fetch_robots_gate`` is the soft-failing fetch helper: any HTTP / parse error
collapses to the allow-all gate (``robots_txt=None``) so an unreachable or
malformed robots.txt never blocks or crashes the crawl (Pitfall 4 / V5).
"""

from collections.abc import Callable

from protego import Protego

from perfcrawl.constants import CRAWLER_USER_AGENT, DEFAULT_MIN_DELAY_S


class RobotsGate:
    """Per-host robots policy gate (CRAWL-03 / D-07 / D-11).

    Construct with the raw robots.txt text (or ``None`` for a 404/missing
    robots.txt = allow-all). ``can_fetch(url)`` gates every candidate;
    ``effective_delay`` is the stricter-of politeness delay; ``.sitemaps`` lists
    the ``Sitemap:`` directives (empty when robots is missing).
    """

    def __init__(
        self,
        robots_txt: str | None,
        *,
        user_agent: str = CRAWLER_USER_AGENT,
        default_delay: float = DEFAULT_MIN_DELAY_S,
        ignore: bool = False,
    ) -> None:
        self._ignore = ignore
        self._ua = user_agent
        # 404/missing robots.txt = allow-all (Pitfall 4): no parser, no rules.
        self._rp = Protego.parse(robots_txt) if robots_txt else None
        # D-11: stricter-of crawl-delay vs the configured default (max wins).
        cd = self._rp.crawl_delay(user_agent) if self._rp else None
        self.effective_delay: float = (
            max(default_delay, cd) if cd is not None else default_delay
        )
        # D-07: expose the Sitemap: directives (empty list when robots missing).
        self.sitemaps: list[str] = list(self._rp.sitemaps) if self._rp else []

    def can_fetch(self, url: str) -> bool:
        """True iff ``url`` may be fetched under this robots policy (D-11).

        ``ignore=True`` (--ignore-robots) and a missing robots.txt both return
        True unconditionally; otherwise the decision is delegated to Protego's
        wildcard-aware ``can_fetch`` for the configured user-agent.
        """
        if self._ignore:  # D-11: --ignore-robots (owned sites)
            return True
        if self._rp is None:  # Pitfall 4: 404/missing robots.txt = allow-all
            return True
        return self._rp.can_fetch(url, self._ua)


def fetch_robots_gate(
    base_url: str,
    *,
    fetch: Callable[[str], object],
    user_agent: str = CRAWLER_USER_AGENT,
    default_delay: float = DEFAULT_MIN_DELAY_S,
    ignore: bool = False,
) -> RobotsGate:
    """Fetch ``/robots.txt`` for ``base_url`` and build a ``RobotsGate``.

    Soft-fails to the allow-all gate (``robots_txt=None``) on ANY fetch/parse
    error or non-2xx status (Pitfall 4): an unreachable or malformed robots.txt
    must never block or crash the crawl. ``fetch`` is injectable so tests pass a
    local-server client; it is expected to return an object with ``.status_code``
    and ``.text``.
    """
    robots_txt: str | None = None
    try:
        robots_url = base_url.rstrip("/") + "/robots.txt"
        resp = fetch(robots_url)
        status = getattr(resp, "status_code", None)
        if status is not None and 200 <= status < 300:
            robots_txt = getattr(resp, "text", None)
    except Exception:
        # Soft-fail: any error → allow-all gate (Pitfall 4 / V5).
        robots_txt = None
    return RobotsGate(
        robots_txt,
        user_agent=user_agent,
        default_delay=default_delay,
        ignore=ignore,
    )
