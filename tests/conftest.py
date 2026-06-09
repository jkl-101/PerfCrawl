"""Shared pytest fixtures for the Phase 1 data-contract suite.

Provides the fixture-JSON loaders + a programmatic sample ``RunRecord`` (test_store
round-trips these) and a two-run delta pair (Plan 03's ``test_delta`` consumes it).
The delta pair deliberately exercises every D-09..D-12 edge case in one place:
an improved metric, a regressed metric, an unchanged metric, a ``previous == 0``
metric, a NEW page, a REMOVED page, and a metric present on only one side
(``not_comparable``).
"""

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import anthropic
import httpx
import pytest

from perfcrawl.models import (
    AnalysisResult,
    MetricSample,
    PageResult,
    RunRecord,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
LH_FIXTURES_DIR = FIXTURES_DIR / "lighthouse"
DIGEST_FIXTURES_DIR = FIXTURES_DIR / "digests"


# --------------------------------------------------------------------------- #
# Phase 5: FakeAnthropic test double — no network, no key (Validation Arch.)
#
# A drop-in stand-in for ``anthropic.Anthropic`` whose ``.messages.parse(...)``
# returns a canned object exposing ``.parsed_output`` (an ``AnalysisResult`` or
# ``None``) and ``.usage.cache_read_input_tokens``, or raises ``anthropic.APIError``.
# ``.call_count`` records how many times ``parse`` was invoked — the D-06
# short-circuit test asserts it stays 0 for a fully-null error row.
# --------------------------------------------------------------------------- #


def _dummy_api_error() -> anthropic.APIError:
    """Construct a real ``anthropic.APIError`` (degrade path) with no live request."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIError("fake transient API failure (test)", request, body=None)


class _FakeUsage:
    """Mimics ``message.usage`` — only the cache-read counter the monitoring path reads."""

    def __init__(self, cache_read_input_tokens: int = 0) -> None:
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = 0


class _FakeParsed:
    """Mimics what ``client.messages.parse(...)`` returns (``.parsed_output`` + ``.usage``)."""

    def __init__(self, parsed_output: AnalysisResult | None, cache_read: int = 0) -> None:
        self.parsed_output = parsed_output
        self.usage = _FakeUsage(cache_read_input_tokens=cache_read)


class _FakeMessages:
    """The ``client.messages`` namespace — only ``parse`` is implemented."""

    def __init__(self, parent: "FakeAnthropic") -> None:
        self._parent = parent

    def parse(self, **kwargs) -> _FakeParsed:
        self._parent.call_count += 1
        self._parent.calls.append(kwargs)
        if self._parent.error is not None:
            raise self._parent.error
        return _FakeParsed(self._parent.result, cache_read=self._parent.cache_read)


class FakeAnthropic:
    """A canned ``anthropic.Anthropic`` double for the deterministic eval suite.

    Variants (all keyword-only):
      - good result:   ``FakeAnthropic(result=AnalysisResult(...))``
      - refusal/None:  ``FakeAnthropic(result=None)`` (degrade like an exception)
      - APIError:      ``FakeAnthropic(error=anthropic.APIError(...))`` — or omit
                       ``error`` and pass ``raise_api_error=True`` for a default one
      - call-count:    every variant records ``.call_count`` + ``.calls`` so the
                       D-06 short-circuit can assert ``parse`` was NEVER invoked.

    No network, no API key — constructing or calling it never touches Anthropic.
    """

    def __init__(
        self,
        *,
        result: AnalysisResult | None = None,
        error: BaseException | None = None,
        raise_api_error: bool = False,
        cache_read: int = 0,
    ) -> None:
        if error is None and raise_api_error:
            error = _dummy_api_error()
        self.result = result
        self.error = error
        self.cache_read = cache_read
        self.call_count = 0
        self.calls: list[dict] = []
        self.messages = _FakeMessages(self)


@pytest.fixture
def fake_anthropic_good() -> FakeAnthropic:
    """A FakeAnthropic that returns a schema-valid grounded ``AnalysisResult``."""
    return FakeAnthropic(
        result=AnalysisResult(
            observation="LCP is 2410 ms (good, <2500).",
            potential_cause="The main JS bundle is the slowest request at 612 ms.",
            suggested_optimization="Code-split the app bundle to shed blocking JS.",
        ),
        cache_read=1200,
    )


@pytest.fixture
def fake_anthropic_none() -> FakeAnthropic:
    """A FakeAnthropic whose ``parse`` returns ``parsed_output=None`` (refusal → degrade)."""
    return FakeAnthropic(result=None)


@pytest.fixture
def fake_anthropic_error() -> FakeAnthropic:
    """A FakeAnthropic whose ``parse`` raises ``anthropic.APIError`` (degrade path)."""
    return FakeAnthropic(raise_api_error=True)

# The spike's reusable single-file Django auth fixture (CSRF + sessionid +
# @login_required /dashboard/, admin/admin123). Phase 4's e2e auth test
# (tests/test_auth_e2e.py) drives a real form login against it.
SPIKE_DJANGO_APP = (
    Path(__file__).resolve().parents[1]
    / ".claude"
    / "skills"
    / "spike-findings-performance-statistics-gathering"
    / "sources"
    / "_shared"
    / "django_app.py"
)
# The Phase-3 crawl fixture site (HTML pages the BFS walks). Reused here so the
# root-level CLI crawl tests (tests/test_cli_crawl.py) can hit a real-but-local
# HTTP origin without duplicating the fixture server in tests/crawl/conftest.py.
CRAWL_SITE_DIR = Path(__file__).parent / "crawl" / "fixtures" / "site"


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to tests/fixtures/."""
    return FIXTURES_DIR


@pytest.fixture
def local_server() -> Iterator[str]:
    """Serve the Phase-3 crawl fixture site over a local HTTP thread; yield base URL.

    A root-level mirror of ``tests/crawl/conftest.py``'s ``local_server`` so the
    CLI ``crawl`` tests at ``tests/test_cli_crawl.py`` can drive real discovery
    against ``tests/crawl/fixtures/site/`` with no network. Ephemeral kernel-picked
    port; daemon thread; clean shutdown on teardown. ``SimpleHTTPRequestHandler``
    returns 404 for any off-disk path (the discovery error-tagging target).
    """
    handler = partial(SimpleHTTPRequestHandler, directory=str(CRAWL_SITE_DIR))
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


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _stop_django(proc: subprocess.Popen) -> None:
    """Tear down the whole Django process group (uv parent + grandchild python).

    ``uv run`` spawns the real Django ``python`` as a GRANDCHILD; killing only the
    ``uv`` parent orphans runserver (it survives with PPID 1). Kill the whole
    session/process-group so no Django process ever leaks (spike requirement #4).
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


@pytest.fixture
def django_auth_fixture(tmp_path) -> Iterator[str]:
    """Run the spike's Django auth fixture as a subprocess; yield its base URL.

    Mirrors the spike's ``chrome_cdp.start_django`` / ``stop_django``: launches
    the single-file ``django_app.py`` via ``uv run --with 'django>=5,<6'`` (so the
    project takes no Django dependency), waits until ``/login/`` answers 200, then
    yields ``http://127.0.0.1:<port>``. The whole process group is torn down on
    teardown. Used only by the ``e2e``-marked authenticated-audit test, which is
    skipped by default (it needs Node + Chrome + a working ``uv``).
    """
    if not SPIKE_DJANGO_APP.exists():
        pytest.skip(f"spike Django fixture not found at {SPIKE_DJANGO_APP}")

    addr_port = _free_port()
    addr = f"127.0.0.1:{addr_port}"
    base = f"http://{addr}"
    db_path = str(tmp_path / "django-auth-e2e.sqlite3")

    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "--with",
            "django>=5,<6",
            "python",
            str(SPIKE_DJANGO_APP),
            "runserver",
        ],
        cwd=str(SPIKE_DJANGO_APP.parent),
        env={**os.environ, "SPIKE_ADDR": addr, "SPIKE_DB": db_path},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.monotonic() + 40
    up = False
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/login/", timeout=1) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:
            time.sleep(0.25)
    if not up:
        _stop_django(proc)
        pytest.skip("Django auth fixture did not come up within 40s")

    try:
        yield base
    finally:
        _stop_django(proc)


@pytest.fixture
def run_v1_json() -> str:
    """Raw JSON text of the full RunRecord fixture (>=2 pages, metrics + analysis)."""
    return (FIXTURES_DIR / "run_v1.json").read_text()


@pytest.fixture
def run_v1_old_schema_json() -> str:
    """Raw JSON text of the same run with later-phase fields absent (criterion #3)."""
    return (FIXTURES_DIR / "run_v1_old_schema.json").read_text()


@pytest.fixture
def run_v1(run_v1_json: str) -> RunRecord:
    """The full fixture parsed into a RunRecord."""
    return RunRecord.model_validate_json(run_v1_json)


# --- Phase 2 LH-13.3.0 fixtures (consumed by tests/test_normalizer.py) -------


@pytest.fixture
def lh_home_200() -> dict:
    """A real LH 13.3.0 JSON capture of a 200-response homepage (Phase 2 D-09/D-12)."""
    return json.loads((LH_FIXTURES_DIR / "studyhalo-home-200.json").read_text())


# --- Phase 5 digest fixtures (the 12 AI-SPEC §5 reference cases) -------------


@pytest.fixture
def digest_page():
    """Load a curated digest fixture by name into a ``PageResult`` (mirrors lh_home_200).

    Usage: ``digest_page("slow-lcp")`` → the ``PageResult`` parsed from
    ``tests/fixtures/digests/slow-lcp.json``. The fixtures double as the eval
    reference dataset and as ``build_digest`` inputs (same canned-fixture
    discipline as ``tests/fixtures/lighthouse/*.json``).
    """

    def _load(name: str) -> PageResult:
        path = DIGEST_FIXTURES_DIR / f"{name}.json"
        return PageResult.model_validate_json(path.read_text())

    return _load


@pytest.fixture
def all_digest_pages() -> list[PageResult]:
    """Every curated digest fixture parsed into a ``PageResult`` (sorted by filename)."""
    return [
        PageResult.model_validate_json(p.read_text())
        for p in sorted(DIGEST_FIXTURES_DIR.glob("*.json"))
    ]


@pytest.fixture
def load_gold():
    """Load a fixture's raw ``gold`` label dict by name (mirrors ``digest_page``).

    Usage: ``load_gold("slow-lcp")`` → the ``gold`` object authored beside the
    inputs-only digest, or ``None`` for an unlabeled fixture (e.g. the
    ``fully-null-error-row`` D-06 case).

    The gold label is the human authority the LLM-judge is calibrated against
    (Phase 5.1). It rides as a NEW top-level ``gold`` key, which ``PageResult``'s
    ``extra="ignore"`` config drops on validation — so ``digest_page`` /
    ``build_digest`` never see it and the byte-stability contract is preserved.
    That is exactly why this loader reads the RAW JSON (``json.loads``) instead of
    going through ``PageResult`` (which would discard the key).
    """

    def _load(name: str) -> dict | None:
        path = DIGEST_FIXTURES_DIR / f"{name}.json"
        return json.loads(path.read_text()).get("gold")

    return _load


@pytest.fixture
def lh_404() -> dict:
    """LH 13.3.0 JSON capture of a 404 main-document (Phase 2 D-13 partial-result)."""
    return json.loads((LH_FIXTURES_DIR / "studyhalo-404.json").read_text())


@pytest.fixture
def lh_version_14_drift() -> dict:
    """Synthetic LH 14.0.0 JSON for the D-10 version-gate test."""
    return json.loads((LH_FIXTURES_DIR / "version-drift-14.json").read_text())


@pytest.fixture
def sample_run() -> RunRecord:
    """A programmatically-built RunRecord with >=2 pages for store round-trip tests.

    Built in code (not from JSON) so the round-trip test proves model->store->read
    equals the original Pydantic object, not just a JSON re-parse.
    """
    return RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-0000000000c3"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target="https://studyhalo.com",
        auth_used=False,
        chrome_version="137.0.7151.40",
        lighthouse_version="13.3.0",
        throttling={"rttMs": 150, "cpuSlowdownMultiplier": 4},
        emulation="mobile",
        pages=[
            PageResult(
                url="https://studyhalo.com/",
                url_key="https://studyhalo.com/",
                perf_score=0.82,
                lcp_ms=MetricSample(median=2410.0, samples=[2300.0, 2410.0, 2520.0]),
                inp_proxy_tbt_ms=MetricSample(median=180.0, samples=[160.0, 180.0, 210.0]),
                ttfb_ms=MetricSample(median=320.0, samples=[300.0, 320.0, 360.0]),
                request_count=48,
                total_bytes=1843200,
                status_code=200,
                slowest_request_url="https://studyhalo.com/static/app.bundle.js",
                slowest_request_ms=612.0,
                analysis=AnalysisResult(observation="LCP bound by main bundle."),
            ),
            PageResult(
                url="https://studyhalo.com/courses?page=2",
                url_key="https://studyhalo.com/courses?page=2",
                perf_score=0.74,
                lcp_ms=MetricSample(median=3120.0, samples=[3000.0, 3120.0, 3300.0]),
                request_count=71,
                total_bytes=2621440,
                status_code=200,
            ),
        ],
    )


@pytest.fixture
def delta_pair() -> tuple[RunRecord, RunRecord]:
    """A (previous, current) two-run pair exercising every D-09..D-12 edge case.

    Page identity is by ``url_key`` (the cross-run self-join key):
      - "/"          present in both: perf_score improved, lcp regressed,
                     ttfb unchanged (literal-equal), request_count present on
                     only the current side (not_comparable for that metric).
      - "/zero"      present in both with previous total_bytes == 0 (deltaPct guard).
      - "/removed"   present in PREVIOUS only  -> direction=removed.
      - "/new"       present in CURRENT only   -> direction=new.

    Plan 03's ``compute_deltas(current, previous)`` is tested against this pair.
    """
    previous = RunRecord(
        started_at=datetime(2026, 5, 1, tzinfo=UTC),
        target="https://studyhalo.com",
        pages=[
            PageResult(
                url="https://studyhalo.com/",
                url_key="https://studyhalo.com/",
                perf_score=0.70,
                lcp_ms=MetricSample(median=2000.0),
                ttfb_ms=MetricSample(median=300.0),
                # request_count intentionally absent here -> not_comparable
            ),
            PageResult(
                url="https://studyhalo.com/zero",
                url_key="https://studyhalo.com/zero",
                total_bytes=0,  # previous == 0 -> deltaPct must be None
            ),
            PageResult(
                url="https://studyhalo.com/removed",
                url_key="https://studyhalo.com/removed",
                perf_score=0.60,
            ),
        ],
    )
    current = RunRecord(
        started_at=datetime(2026, 5, 25, tzinfo=UTC),
        target="https://studyhalo.com",
        pages=[
            PageResult(
                url="https://studyhalo.com/",
                url_key="https://studyhalo.com/",
                perf_score=0.85,  # higher-is-better -> improvement
                lcp_ms=MetricSample(median=2600.0),  # lower-is-better, went up -> regression
                ttfb_ms=MetricSample(median=300.0),  # literal-equal -> unchanged
                request_count=50,  # only on current side -> not_comparable
            ),
            PageResult(
                url="https://studyhalo.com/zero",
                url_key="https://studyhalo.com/zero",
                total_bytes=1024,  # previous was 0 -> deltaPct None, deltaAbs defined
            ),
            PageResult(
                url="https://studyhalo.com/new",
                url_key="https://studyhalo.com/new",
                perf_score=0.95,  # only in current -> direction=new
            ),
        ],
    )
    return previous, current
