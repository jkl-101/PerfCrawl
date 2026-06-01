"""Provable-termination test against a calendar/facet trap (CRAWL-04 + Success #3).

This is the success-#3 guarantee: a synthetic site whose every page links to
``N+1`` NEW dated/faceted URLs (an infinite frontier) MUST still terminate. The
three independent bounds together guarantee it:

  - ``--max-depth`` → finite BFS tree height,
  - ``--max-pages`` + the D-05 enqueue bound → never measure more than N in-scope,
  - the per-base-path query-variant cap (D-08) → a facet base path emits at most
    ``query_variant_cap`` distinct canonical variants.

The test asserts ``discover()`` RETURNS (does not hang — enforced by the test
process simply completing) AND ``len(in_scope) <= cfg.max_pages`` AND the
variant-capped ``/calendar`` base path emits ``<= query_variant_cap`` variants.

The trap server is a stdlib ``ThreadingHTTPServer`` that templates an infinite
calendar: GET ``/calendar`` (any ``?month=…`` query) returns a page linking five
brand-new ``?month=…`` variants, so a naive crawler would never stop.
"""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from perfcrawl.constants import CRAWLER_USER_AGENT
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import discover
from perfcrawl.crawl.robots import RobotsGate
from perfcrawl.crawl.scope import _base_path


class _TrapHandler(BaseHTTPRequestHandler):
    """Serves an infinite calendar: every page links 5 NEW ?month= / facet variants."""

    # A monotonically-increasing counter so each page emits genuinely-new URLs,
    # guaranteeing the frontier is unbounded absent the crawler's own caps.
    _counter = 0
    _lock = threading.Lock()

    def log_message(self, *args, **kwargs):  # noqa: D401 - silence stderr noise
        return

    def do_GET(self):  # noqa: N802 - stdlib handler name
        path = urlsplit(self.path).path
        if path not in ("/", "/calendar"):
            self.send_response(404)
            self.end_headers()
            return
        with _TrapHandler._lock:
            base = _TrapHandler._counter
            _TrapHandler._counter += 5
        # Each render links 5 NEW dated/faceted /calendar variants — N+1 forever.
        links = "".join(
            f'<a href="/calendar?month=2099-{base + i:04d}'
            f'&view=day&n={base + i}">m{base + i}</a>'
            for i in range(5)
        )
        html = (
            "<!doctype html><html><body><h1>trap</h1>"
            f"<nav>{links}</nav></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


@pytest.fixture
def trap_server() -> Iterator[str]:
    """A local infinite-calendar trap server; yields its base URL."""
    _TrapHandler._counter = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TrapHandler)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fetch(client):
    def fetch(url: str):
        return client.get(url)

    return fetch


def test_calendar_trap_terminates(trap_server):
    """CRAWL-04 + Success #3: the infinite trap RETURNS with len(in_scope) <= max_pages."""
    cfg = CrawlConfig(
        use_sitemap=False, max_pages=15, max_depth=4, query_variant_cap=10
    )
    seed = trap_server + "/calendar"
    with httpx.Client(
        follow_redirects=True,
        timeout=5.0,
        headers={"user-agent": CRAWLER_USER_AGENT},
    ) as client:
        # If discovery did not terminate, this call would hang and the test would
        # never complete — reaching the assert IS the termination proof.
        in_scope, errors = discover(
            seed, cfg=cfg, robots=RobotsGate(None), fetch=_fetch(client)
        )

    # max-pages bound (D-05): never more than the cap.
    assert len(in_scope) <= cfg.max_pages

    # variant cap (D-08): the /calendar base path emits <= query_variant_cap variants.
    cal_base = _base_path(seed)
    cal_variants = [r for r in in_scope if _base_path(r.url) == cal_base]
    assert len(cal_variants) <= cfg.query_variant_cap
