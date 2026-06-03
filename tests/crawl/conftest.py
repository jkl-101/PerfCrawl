"""Wave-0 test substrate for the Phase 3 crawl suite (no network).

Provides:
  - ``fixtures_dir`` — absolute path to ``tests/crawl/fixtures/`` (the HTML site,
    sitemap/robots variants, and the calendar-trap fixture live here).
  - ``local_server`` — a stdlib ``http.server`` running on an ephemeral port in a
    background thread, serving ``tests/crawl/fixtures/site/``. Yields the base URL
    (e.g. ``http://127.0.0.1:54321``) and shuts down cleanly on teardown, so every
    discovery/CLI test in plans 02/03 can hit a real-but-local HTTP origin without
    touching the network.

The server uses ``ThreadingHTTPServer`` so a test issuing several requests (BFS
fetches multiple pages) is not serialized behind a single blocking handler. Port 0
lets the kernel pick a free port, mirroring the orchestrator's own
``--remote-debugging-port=0`` discipline (no port collisions across parallel tests).
"""

import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SITE_DIR = FIXTURES_DIR / "site"


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to tests/crawl/fixtures/."""
    return FIXTURES_DIR


@pytest.fixture
def local_server() -> Iterator[str]:
    """Serve tests/crawl/fixtures/site/ over a local HTTP thread; yield the base URL.

    Ephemeral port (kernel-picked); background daemon thread; clean shutdown on
    teardown. ``SimpleHTTPRequestHandler`` returns 404 for any path not on disk,
    which the discovery error-tagging tests (plan 02) rely on (one fixture link
    points at a path that 404s).
    """
    handler = partial(SimpleHTTPRequestHandler, directory=str(SITE_DIR))
    # Quiet the per-request stderr logging so the pytest output stays clean.
    handler.log_message = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
