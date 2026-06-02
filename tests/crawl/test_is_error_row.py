"""Tests for the shared ``is_error_row`` classifier — WR-01.

``perfcrawl.crawl.is_error_row`` is the SINGLE source of truth for the "every
measurable metric is null" error-row decision, imported by both the CLI
exit-code/summary split and the measurement-pass dedup tie-break so they can
never drift. These tests assert it returns True iff ALL measurable fields are
None — so a future ``PageResult`` metric field added without updating the
classifier fails loudly here.
"""

from perfcrawl.canonical import canonical_key
from perfcrawl.crawl import is_error_row
from perfcrawl.models import MetricSample, PageResult

# Every nullable measurable field the classifier inspects, paired with a
# representative non-null value. If a new measured field is added to PageResult
# but not to is_error_row, the parametrized "any one field set ⇒ not an error
# row" assertion below will FAIL for that field, surfacing the drift.
_MEASURED_FIELDS = {
    "perf_score": 90.0,
    "lcp_ms": MetricSample(median=1200.0, samples=[1200.0]),
    "cls": MetricSample(median=0.01, samples=[0.01]),
    "inp_proxy_tbt_ms": MetricSample(median=50.0, samples=[50.0]),
    "ttfb_ms": MetricSample(median=80.0, samples=[80.0]),
    "request_count": 12,
    "total_bytes": 34567,
    "slowest_request_url": "https://example.com/big.js",
    "slowest_request_ms": 420.0,
}


def _bare(url: str = "https://example.com/") -> PageResult:
    return PageResult(url=url, url_key=canonical_key(url))


def test_all_null_is_error_row() -> None:
    """A page with no measured data at all is an error row."""
    assert is_error_row(_bare()) is True


def test_status_only_is_still_error_row() -> None:
    """A status-only non-2xx row (no metrics) is an error row (D-03)."""
    page = _bare()
    page.status_code = 404
    assert is_error_row(page) is True


def test_any_single_measured_field_is_not_error_row() -> None:
    """If ANY measurable field is non-null the page counts as measured (WR-05)."""
    for field, value in _MEASURED_FIELDS.items():
        page = _bare()
        setattr(page, field, value)
        assert is_error_row(page) is False, (
            f"page with only {field!r} set must NOT be an error row"
        )
