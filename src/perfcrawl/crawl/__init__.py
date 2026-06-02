"""Phase 3 site-wide crawler subsystem.

Cohesive home for the new discovery + scope/robots/sitemap + measurement-pass
code (D-01..D-15). Everything downstream of discovery (`measure_url`,
`write_outputs`, `write_run`, `canonical_key`, `page_slug`, the
`PageResult`/`RunRecord` models) is reused unchanged from Phases 1/2.
"""

from perfcrawl.models import PageResult


def is_error_row(page: PageResult) -> bool:
    """True iff ``page`` carries no measured data at all (D-03 error row).

    WR-01: the SINGLE source of truth for the "every measurable metric is null"
    error-row classifier. Both the CLI exit-code/summary split (``cli.py``) and
    the measurement-pass dedup tie-break (``measure_pass.py``) import this so they
    can never drift out of lockstep — a future ``PageResult`` metric field MUST be
    added here once, and both call sites pick it up.

    WR-05: requires EVERY measurable metric to be null, not just
    ``perf_score``/``lcp_ms`` — a 2xx page that measured TTFB/request_count/bytes
    but for which Lighthouse returned no perf score or LCP is genuine data and
    must NOT be classed an error row (which could otherwise flip a partial-success
    crawl to exit 2, or let a colliding error row overwrite a measured page).
    """
    return (
        page.perf_score is None
        and page.lcp_ms is None
        and page.cls is None
        and page.inp_proxy_tbt_ms is None
        and page.ttfb_ms is None
        and page.request_count is None
        and page.total_bytes is None
        and page.slowest_request_url is None
        and page.slowest_request_ms is None
    )
