"""Generate the 12 curated Phase-5 digest fixtures (AI-SPEC §5 reference dataset).

One-shot authoring helper: builds each ``PageResult`` from the canonical models so
every fixture is guaranteed to round-trip through ``PageResult.model_validate_json``.
Run via ``uv run python scripts/gen_digest_fixtures.py``. The emitted JSON files
under ``tests/fixtures/digests/`` are the committed artifacts; this script is the
authoring record (re-runnable to regenerate them deterministically).

Conventions honored (RESEARCH Pattern 1 / Pitfall 1, AI-SPEC §5):
  - Category scores are 0-100, higher-is-better (NEVER 0-1, never ms).
  - INP appears ONLY as ``inp_proxy_tbt_ms`` (the labeled TBT lab proxy) — never a bare ``inp``.
  - CWV bands for realism: LCP good <=2500 / poor >4000; CLS good <=0.1 / poor >0.25;
    TBT(proxy) good <=200 / poor >500.
  - The fully-null error row leaves EVERY measurable metric null so ``crawl.is_error_row`` is True.
"""

from pathlib import Path

from perfcrawl.models import MetricSample, PageResult, WaterfallEntry

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "digests"
BASE = "https://www.studyhalo.com"


def ms(median: float) -> MetricSample:
    return MetricSample(median=median, samples=[median])


def wf(url, rtype, size, timing, status=200) -> WaterfallEntry:
    return WaterfallEntry(
        url=url, resource_type=rtype, size_bytes=size, timing_ms=timing, status_code=status
    )


def page(path: str, **kw) -> PageResult:
    url = f"{BASE}{path}"
    return PageResult(url=url, url_key=url, **kw)


FIXTURES: dict[str, PageResult] = {
    # 1 — healthy / all-green: model must NOT invent a problem (dim 7).
    "healthy-all-green": page(
        "/",
        perf_score=98.0,
        a11y_score=100.0,
        seo_score=95.0,
        best_practices_score=100.0,
        lcp_ms=ms(1200.0),
        cls=ms(0.02),
        inp_proxy_tbt_ms=ms(90.0),
        ttfb_ms=ms(180.0),
        request_count=24,
        total_bytes=480_000,
        status_code=200,
        slowest_request_url=f"{BASE}/static/app.css",
        slowest_request_ms=160.0,
        waterfall=[
            wf(f"{BASE}/static/app.css", "stylesheet", 42_000, 160.0),
            wf(f"{BASE}/static/app.js", "script", 88_000, 140.0),
            wf(f"{BASE}/", "document", 18_000, 120.0),
        ],
    ),
    # 2 — slow-LCP: grounded cause tied to the actual slow hero image (dims 2/6/8).
    "slow-lcp": page(
        "/courses/intro",
        perf_score=55.0,
        a11y_score=92.0,
        seo_score=90.0,
        best_practices_score=88.0,
        lcp_ms=ms(4800.0),  # poor (>4000)
        cls=ms(0.03),
        inp_proxy_tbt_ms=ms(150.0),
        ttfb_ms=ms(300.0),
        request_count=40,
        total_bytes=3_200_000,
        status_code=200,
        slowest_request_url=f"{BASE}/media/hero.png",
        slowest_request_ms=1820.0,
        waterfall=[
            wf(f"{BASE}/media/hero.png", "image", 2_410_000, 1820.0),
            wf(f"{BASE}/static/app.js", "script", 320_000, 540.0),
            wf(f"{BASE}/static/app.css", "stylesheet", 64_000, 210.0),
        ],
    ),
    # 3 — high-CLS: correct flagging against the CLS band (dim 7).
    "high-cls": page(
        "/blog/launch",
        perf_score=70.0,
        a11y_score=95.0,
        seo_score=93.0,
        best_practices_score=90.0,
        lcp_ms=ms(2300.0),
        cls=ms(0.42),  # poor (>0.25)
        inp_proxy_tbt_ms=ms(120.0),
        ttfb_ms=ms(250.0),
        request_count=35,
        total_bytes=900_000,
        status_code=200,
        slowest_request_url=f"{BASE}/media/banner.jpg",
        slowest_request_ms=640.0,
        waterfall=[
            wf(f"{BASE}/media/banner.jpg", "image", 540_000, 640.0),
            wf(f"{BASE}/static/ads.js", "script", 120_000, 480.0),
        ],
    ),
    # 4 — high-TBT: TBT labeled as the INP lab proxy, never bare INP (dim 4).
    "high-tbt": page(
        "/dashboard",
        perf_score=60.0,
        a11y_score=90.0,
        seo_score=88.0,
        best_practices_score=85.0,
        lcp_ms=ms(2400.0),
        cls=ms(0.05),
        inp_proxy_tbt_ms=ms(780.0),  # poor TBT (INP lab proxy)
        ttfb_ms=ms(280.0),
        request_count=55,
        total_bytes=1_500_000,
        status_code=200,
        slowest_request_url=f"{BASE}/static/vendor.bundle.js",
        slowest_request_ms=1340.0,
        waterfall=[
            wf(f"{BASE}/static/vendor.bundle.js", "script", 980_000, 1340.0),
            wf(f"{BASE}/static/app.bundle.js", "script", 420_000, 760.0),
        ],
    ),
    # 5 — high-TTFB: server-response read correctly without guessing a server (dims 3/6).
    "high-ttfb": page(
        "/search",
        perf_score=65.0,
        a11y_score=94.0,
        seo_score=91.0,
        best_practices_score=92.0,
        lcp_ms=ms(3000.0),
        cls=ms(0.04),
        inp_proxy_tbt_ms=ms(140.0),
        ttfb_ms=ms(2400.0),  # poor server response
        request_count=30,
        total_bytes=800_000,
        status_code=200,
        slowest_request_url=f"{BASE}/search?q=algebra",
        slowest_request_ms=2400.0,
        waterfall=[
            wf(f"{BASE}/search?q=algebra", "document", 36_000, 2400.0),
            wf(f"{BASE}/static/app.js", "script", 180_000, 320.0),
        ],
    ),
    # 6 — heavy: byte/request-grounded optimization, not boilerplate (dim 8).
    "heavy": page(
        "/gallery",
        perf_score=45.0,
        a11y_score=88.0,
        seo_score=85.0,
        best_practices_score=80.0,
        lcp_ms=ms(3800.0),
        cls=ms(0.08),
        inp_proxy_tbt_ms=ms(350.0),
        ttfb_ms=ms(400.0),
        request_count=180,
        total_bytes=6_500_000,
        status_code=200,
        slowest_request_url=f"{BASE}/media/gallery-01.jpg",
        slowest_request_ms=1450.0,
        waterfall=[
            wf(f"{BASE}/media/gallery-01.jpg", "image", 1_800_000, 1450.0),
            wf(f"{BASE}/media/gallery-02.jpg", "image", 1_600_000, 1280.0),
            wf(f"{BASE}/media/gallery-03.jpg", "image", 1_400_000, 1110.0),
        ],
    ),
    # 7 — fully-null error row: short-circuits to analysis=None with NO API call (dim 5 / D-06).
    "fully-null-error-row": page(
        "/broken",
        status_code=500,
        # every measurable metric intentionally left null → crawl.is_error_row() is True
    ),
    # 8 — partial-null / sparse: says "insufficient data" for the gaps, grounds the rest (dim 5).
    "partial-null": page(
        "/profile",
        perf_score=None,  # Lighthouse returned no category score
        lcp_ms=None,  # CWV not captured
        cls=ms(0.06),
        inp_proxy_tbt_ms=None,
        ttfb_ms=ms(220.0),
        request_count=28,
        total_bytes=600_000,
        status_code=200,
        slowest_request_url=f"{BASE}/static/profile.js",
        slowest_request_ms=410.0,
        waterfall=[wf(f"{BASE}/static/profile.js", "script", 210_000, 410.0)],
    ),
    # 9 — green-metric trap: a metric already fine a naive model would "optimize" (dims 7/8).
    "green-trap": page(
        "/about",
        perf_score=92.0,
        a11y_score=98.0,
        seo_score=96.0,
        best_practices_score=100.0,
        lcp_ms=ms(1100.0),  # already good — do NOT recommend optimizing it
        cls=ms(0.01),
        inp_proxy_tbt_ms=ms(80.0),
        ttfb_ms=ms(150.0),
        request_count=18,
        total_bytes=350_000,
        status_code=200,
        slowest_request_url=f"{BASE}/static/about.css",
        slowest_request_ms=120.0,
        waterfall=[wf(f"{BASE}/static/about.css", "stylesheet", 28_000, 120.0)],
    ),
    # 10 — multi-problem: lead on the worst (TTFB+bytes), not the mediocre CLS (dim 9).
    "multi-problem": page(
        "/library",
        perf_score=38.0,
        a11y_score=84.0,
        seo_score=82.0,
        best_practices_score=78.0,
        lcp_ms=ms(5200.0),  # poor
        cls=ms(0.15),  # needs-improvement (the lesser problem)
        inp_proxy_tbt_ms=ms(620.0),  # poor
        ttfb_ms=ms(3100.0),  # poor — among the worst
        request_count=140,
        total_bytes=5_800_000,  # very heavy — among the worst
        status_code=200,
        slowest_request_url=f"{BASE}/media/catalog.json",
        slowest_request_ms=2950.0,
        waterfall=[
            wf(f"{BASE}/media/catalog.json", "fetch", 1_200_000, 2950.0),
            wf(f"{BASE}/media/cover-pack.jpg", "image", 2_100_000, 1680.0),
            wf(f"{BASE}/static/vendor.bundle.js", "script", 760_000, 980.0),
        ],
    ),
    # 11 — stack-bait: numbers a model is tempted to explain with React/Django/nginx (dim 3).
    "stack-bait": page(
        "/enroll",
        perf_score=50.0,
        a11y_score=90.0,
        seo_score=89.0,
        best_practices_score=86.0,
        lcp_ms=ms(3500.0),
        cls=ms(0.05),
        inp_proxy_tbt_ms=ms(300.0),
        ttfb_ms=ms(1200.0),
        request_count=60,
        total_bytes=2_000_000,
        status_code=200,
        slowest_request_url=f"{BASE}/static/main.js",  # generic — names NO framework
        slowest_request_ms=980.0,
        waterfall=[
            wf(f"{BASE}/static/main.js", "script", 540_000, 980.0),
            wf(f"{BASE}/static/main.css", "stylesheet", 96_000, 320.0),
            wf(f"{BASE}/enroll", "document", 22_000, 1200.0),
        ],
    ),
    # 12 — adversarial-number: values exactly on band boundaries (dims 2/7).
    "adversarial-number": page(
        "/pricing",
        perf_score=90.0,  # exactly the "good" score cutoff
        a11y_score=89.0,
        seo_score=90.0,
        best_practices_score=90.0,
        lcp_ms=ms(2500.0),  # exactly the LCP good cutoff
        cls=ms(0.1),  # exactly the CLS good cutoff
        inp_proxy_tbt_ms=ms(200.0),  # exactly the TBT good cutoff
        ttfb_ms=ms(800.0),
        request_count=37,
        total_bytes=1_234_567,  # unusual magnitude — must not be "rounded" in the analysis
        status_code=200,
        slowest_request_url=f"{BASE}/static/pricing.js",
        slowest_request_ms=789.0,
        waterfall=[
            wf(f"{BASE}/static/pricing.js", "script", 256_000, 789.0),
            wf(f"{BASE}/static/pricing.css", "stylesheet", 41_000, 233.0),
        ],
    ),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, p in FIXTURES.items():
        target = OUT / f"{name}.json"
        target.write_text(p.model_dump_json(indent=2) + "\n")
        # Re-parse to guarantee the committed artifact round-trips.
        PageResult.model_validate_json(target.read_text())
    print(f"wrote {len(FIXTURES)} digest fixtures to {OUT}")


if __name__ == "__main__":
    main()
