"""Unit tests for the Protego robots gate (CRAWL-03 / D-07 / D-11).

Pins, per decision ID:
  - CRAWL-03 / D-11: ``Disallow`` (plain + wildcard) obeyed by default; a 404 /
    missing robots.txt is allow-all (Pitfall 4), NOT block-all; ``--ignore-robots``
    (``ignore=True``) short-circuits ``can_fetch`` to True for owned sites.
  - D-11: ``effective_delay`` is the STRICTER of the robots ``Crawl-delay`` vs the
    configured default (max of the two), and falls back to the default when robots
    declares none.
  - D-07: the ``Sitemap:`` directive is exposed via ``.sitemaps``.

Strategy mirrors ``tests/test_canonical.py``: one test fn per behavior, asserting
the observable boolean/numeric/list output. The fixtures (``robots-disallow.txt``,
``robots-crawldelay.txt``) are the plan-01 substrate; we parse their text directly
(no network) and also feed ``robots_txt=None`` for the 404/allow-all path.
"""

from perfcrawl.constants import CRAWLER_USER_AGENT, DEFAULT_MIN_DELAY_S
from perfcrawl.crawl.robots import RobotsGate


def _gate(robots_txt, *, ignore=False, default_delay=DEFAULT_MIN_DELAY_S):
    return RobotsGate(
        robots_txt,
        user_agent=CRAWLER_USER_AGENT,
        default_delay=default_delay,
        ignore=ignore,
    )


def test_disallow_plain_path_blocked(fixtures_dir):
    """CRAWL-03: a plain ``Disallow: /private/`` path returns can_fetch False."""
    txt = (fixtures_dir / "robots-disallow.txt").read_text()
    gate = _gate(txt)
    assert gate.can_fetch("https://studyhalo.com/private/secret") is False
    assert gate.can_fetch("https://studyhalo.com/about") is True


def test_disallow_wildcard_blocked(fixtures_dir):
    """CRAWL-03: a wildcard ``Disallow: /*.pdf$`` rule blocks matching paths."""
    txt = (fixtures_dir / "robots-disallow.txt").read_text()
    gate = _gate(txt)
    assert gate.can_fetch("https://studyhalo.com/docs/report.pdf") is False
    assert gate.can_fetch("https://studyhalo.com/docs/report.html") is True


def test_missing_robots_is_allow_all():
    """Pitfall 4 / D-11: a 404/missing robots.txt (None) = allow-all, not block-all."""
    gate = _gate(None)
    assert gate.can_fetch("https://studyhalo.com/private/secret") is True
    assert gate.can_fetch("https://studyhalo.com/anything") is True


def test_ignore_robots_overrides_disallow(fixtures_dir):
    """D-11: ``ignore=True`` (--ignore-robots) returns True even for a Disallow'd path."""
    txt = (fixtures_dir / "robots-disallow.txt").read_text()
    gate = _gate(txt, ignore=True)
    assert gate.can_fetch("https://studyhalo.com/private/secret") is True


def test_effective_delay_takes_stricter_crawl_delay(fixtures_dir):
    """D-11: effective_delay = max(default, robots Crawl-delay) when robots sets one."""
    txt = (fixtures_dir / "robots-crawldelay.txt").read_text()
    gate = _gate(txt, default_delay=0.5)  # robots declares Crawl-delay: 5 -> stricter
    assert gate.effective_delay == 5


def test_effective_delay_falls_back_to_default(fixtures_dir):
    """D-11: with no robots Crawl-delay, effective_delay is the configured default."""
    txt = (fixtures_dir / "robots-disallow.txt").read_text()  # no Crawl-delay here
    gate = _gate(txt, default_delay=0.5)
    assert gate.effective_delay == 0.5


def test_effective_delay_default_when_missing_robots():
    """D-11: a missing robots.txt (None) also yields the configured default delay."""
    gate = _gate(None, default_delay=0.5)
    assert gate.effective_delay == 0.5


def test_sitemaps_exposes_directive(fixtures_dir):
    """D-07: the ``Sitemap:`` directive URL is exposed via ``.sitemaps``."""
    txt = (fixtures_dir / "robots-disallow.txt").read_text()
    gate = _gate(txt)
    assert "https://studyhalo.com/sitemap.xml" in gate.sitemaps


def test_sitemaps_empty_when_missing_robots():
    """D-07: a missing robots.txt exposes an empty ``.sitemaps`` list (never None)."""
    gate = _gate(None)
    assert gate.sitemaps == []
