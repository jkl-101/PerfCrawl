"""RED Sheets-writer tests — Phase 6 OUT-02 (D-08 / D-09 / D-10 / D-11 / D-12).

Wave-0 INTERFACE-FIRST contract. These tests are AUTHORED BEFORE the implementation
and MUST fail (collection-time ``ModuleNotFoundError`` on ``perfcrawl.sheets``) until
Plan 05 lands ``sheets.py``. "RED-as-expected" is the success condition here — do NOT
implement production code to green them.

The contract they lock for Plan 05:

  - ``sheets.SHEET_COLUMNS: list[str]`` = ``output.CSV_COLUMNS`` (in order, the D-09
    drift guard) + ``delta_<metric>`` columns (one per banded metric, in
    ``METRIC_POLARITY`` order) + ``["observation", "potential_cause",
    "suggested_optimization"]`` (the AI columns).
  - ``sheets.write_sheets(run, *, sheets_id, creds_path, band_results, scrub, gc=None)
    -> None``. A fake gspread client is injected via ``gc=`` so NO network / no
    ``gspread.authorize`` is ever touched (D-12 / T-06-04). The writer:
      * reads ``ws.get_all_values()`` once; writes the header row (``SHEET_COLUMNS``)
        via ``append_row`` ONLY when the sheet is empty (D-08, header-once);
      * appends ALL page rows in a SINGLE ``append_rows`` call with
        ``value_input_option="USER_ENTERED"`` (D-08, quota-critical batching);
      * dispatches ``open_by_url`` for an ``http…`` ``--sheets-id`` and
        ``open_by_key`` for a bare key (D-10);
      * applies ``scrub`` to EVERY cell so an embedded credential never reaches a
        Sheets cell (D-12 / CR-01 "scrub every sink").
  - ``sheets.delta_cell_colors(metric) -> (positive_color, negative_color)`` derives
    the conditional-format direction from ``registry.METRIC_POLARITY`` — never
    hardcoded per metric (D-11). ``sheets.RED`` / ``sheets.GREEN`` are the two fills.

Single-source discipline: the schema base comes from ``output.CSV_COLUMNS`` and the
delta-column set / formatting direction from ``registry.METRIC_POLARITY`` — never
re-spelled here.
"""

import importlib
from datetime import UTC, datetime
from uuid import UUID

from perfcrawl import sheets  # RED: Plan 05 adds this module.
from perfcrawl.auth import make_scrubber
from perfcrawl.constants import REDACTION_PLACEHOLDER
from perfcrawl.models import MetricSample, PageResult, RunRecord
from perfcrawl.output import CSV_COLUMNS
from perfcrawl.registry import METRIC_POLARITY, Polarity

# AI columns appended after the per-metric delta columns (D-09 superset tail).
_AI_COLUMNS = ["observation", "potential_cause", "suggested_optimization"]


# --------------------------------------------------------------------------- #
# Fully-mocked gspread surface — NO network, NO authorize, NO real client.
# A fake worksheet/spreadsheet/client records every call so the D-08 batching,
# header-once, and open-by-key/url dispatch are all assertable offline.
# --------------------------------------------------------------------------- #


class _FakeWorksheet:
    """Records append_row / append_rows / get_all_values calls; canned existing grid."""

    def __init__(self, existing: list[list[str]] | None = None) -> None:
        self._existing = existing if existing is not None else []
        self.get_all_values_calls = 0
        self.append_row_calls: list[tuple] = []
        self.append_rows_calls: list[tuple] = []

    def get_all_values(self) -> list[list[str]]:
        self.get_all_values_calls += 1
        return self._existing

    def append_row(self, row, value_input_option=None):
        self.append_row_calls.append((row, value_input_option))

    def append_rows(self, rows, value_input_option=None):
        self.append_rows_calls.append((rows, value_input_option))


class _FakeSpreadsheet:
    def __init__(self, ws: _FakeWorksheet) -> None:
        self.sheet1 = ws

    def worksheet(self, name):  # noqa: ARG002 — signature parity
        return self.sheet1


class _FakeGspreadClient:
    """A stand-in ``gspread.Client``: open_by_key / open_by_url record their dispatch."""

    def __init__(self, ws: _FakeWorksheet) -> None:
        self._sh = _FakeSpreadsheet(ws)
        self.open_by_key_calls: list[str] = []
        self.open_by_url_calls: list[str] = []

    def open_by_key(self, key: str) -> _FakeSpreadsheet:
        self.open_by_key_calls.append(key)
        return self._sh

    def open_by_url(self, url: str) -> _FakeSpreadsheet:
        self.open_by_url_calls.append(url)
        return self._sh


def _identity_scrub(text: str) -> str:
    return text


def _leak_run(secret: str) -> RunRecord:
    """A RunRecord with a credential embedded in page.url AND slowest_request_url."""
    return RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-000000000fff"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target=f"https://admin:{secret}@x.com/",
        pages=[
            PageResult(
                url=f"https://admin:{secret}@x.com/dashboard/",
                url_key="https://x.com/dashboard/",
                perf_score=80.0,
                slowest_request_url=f"https://x.com/api/?token={secret}",
            )
        ],
    )


# --------------------------------------------------------------------------- #
# D-08: ONE append_rows call per run, one row per page, USER_ENTERED.
# --------------------------------------------------------------------------- #


def test_append_rows_batched(sample_run: RunRecord) -> None:
    """All pages append in a SINGLE ``append_rows`` call (D-08 quota batching)."""
    ws = _FakeWorksheet(existing=[])
    gc = _FakeGspreadClient(ws)
    sheets.write_sheets(
        sample_run,
        sheets_id="SOME_BARE_KEY",
        creds_path="unused-when-gc-injected.json",
        band_results=[],
        scrub=_identity_scrub,
        gc=gc,
    )
    # Exactly one batched write carrying one row per page.
    assert len(ws.append_rows_calls) == 1
    rows, value_input_option = ws.append_rows_calls[0]
    assert len(rows) == len(sample_run.pages)
    # D-08 / Pitfall 2: data rows go in USER_ENTERED so numeric + delta cells stay numeric.
    assert value_input_option == "USER_ENTERED"


# --------------------------------------------------------------------------- #
# D-08: header written ONCE — only when the sheet is empty; never duplicated.
# --------------------------------------------------------------------------- #


def test_header_once(sample_run: RunRecord) -> None:
    """Header ``append_row`` fires on an empty sheet; a second run with a non-empty
    sheet does NOT re-write the header (the single growing worksheet, D-08)."""
    # First run: empty sheet → header is written exactly once.
    ws_empty = _FakeWorksheet(existing=[])
    sheets.write_sheets(
        sample_run,
        sheets_id="SOME_BARE_KEY",
        creds_path="unused.json",
        band_results=[],
        scrub=_identity_scrub,
        gc=_FakeGspreadClient(ws_empty),
    )
    assert len(ws_empty.append_row_calls) == 1, "header must be written on an empty sheet"
    header_row, _ = ws_empty.append_row_calls[0]
    assert header_row == sheets.SHEET_COLUMNS

    # Second run: a sheet that already has the header + a data row → NO header re-write.
    ws_nonempty = _FakeWorksheet(existing=[sheets.SHEET_COLUMNS, ["x"] * len(sheets.SHEET_COLUMNS)])
    sheets.write_sheets(
        sample_run,
        sheets_id="SOME_BARE_KEY",
        creds_path="unused.json",
        band_results=[],
        scrub=_identity_scrub,
        gc=_FakeGspreadClient(ws_nonempty),
    )
    assert ws_nonempty.append_row_calls == [], "header must NOT be re-written on a non-empty sheet"
    # Data still appended (one batched call) regardless of header state.
    assert len(ws_nonempty.append_rows_calls) == 1


# --------------------------------------------------------------------------- #
# D-09: Sheets schema is a SUPERSET that STARTS WITH CSV_COLUMNS (drift guard).
# --------------------------------------------------------------------------- #


def test_schema_superset_of_csv() -> None:
    """``SHEET_COLUMNS`` starts with ``CSV_COLUMNS`` then appends delta + AI columns."""
    # The drift guard the planner named (D-09): a future CSV column edit forces the
    # Sheets header to update or this fails.
    assert sheets.SHEET_COLUMNS[: len(CSV_COLUMNS)] == CSV_COLUMNS
    # Strict superset: deltas (one per banded metric in METRIC_POLARITY order) + 3 AI cols.
    expected = list(CSV_COLUMNS) + [f"delta_{m}" for m in METRIC_POLARITY] + _AI_COLUMNS
    assert sheets.SHEET_COLUMNS == expected
    # No metric in the polarity registry is silently un-delta'd.
    for metric in METRIC_POLARITY:
        assert f"delta_{metric}" in sheets.SHEET_COLUMNS


def test_drift_guard_survives_optimize() -> None:
    """WR-04: the D-09 drift guard is an unconditional ``if/raise``, not an ``assert``.

    Importing ``perfcrawl.sheets`` under ``python -O`` (which strips ``assert``
    statements) must still enforce the superset invariant at module load. We assert
    the runtime invariant with a real ``==`` against ``list(CSV_COLUMNS)`` so the
    check does not rely on the optimizer-stripped ``assert`` form, and confirm the
    module re-imports cleanly.
    """
    importlib.reload(sheets)  # module imports cleanly (guard did not raise)
    assert sheets.SHEET_COLUMNS[: len(CSV_COLUMNS)] == list(CSV_COLUMNS)


# --------------------------------------------------------------------------- #
# D-10: --sheets-id accepts a bare key OR a full URL — dispatch the right opener.
# --------------------------------------------------------------------------- #


def test_open_key_or_url(sample_run: RunRecord) -> None:
    """A bare key → ``open_by_key``; an ``http…`` value → ``open_by_url`` (D-10)."""
    # Bare key.
    ws_key = _FakeWorksheet(existing=[])
    gc_key = _FakeGspreadClient(ws_key)
    sheets.write_sheets(
        sample_run,
        sheets_id="1AbCkeyOnly",
        creds_path="unused.json",
        band_results=[],
        scrub=_identity_scrub,
        gc=gc_key,
    )
    assert gc_key.open_by_key_calls == ["1AbCkeyOnly"]
    assert gc_key.open_by_url_calls == []

    # Full URL.
    ws_url = _FakeWorksheet(existing=[])
    gc_url = _FakeGspreadClient(ws_url)
    full_url = "https://docs.google.com/spreadsheets/d/1AbCkeyOnly/edit"
    sheets.write_sheets(
        sample_run,
        sheets_id=full_url,
        creds_path="unused.json",
        band_results=[],
        scrub=_identity_scrub,
        gc=gc_url,
    )
    assert gc_url.open_by_url_calls == [full_url]
    assert gc_url.open_by_key_calls == []


# --------------------------------------------------------------------------- #
# D-11: conditional-format direction comes from METRIC_POLARITY, never hardcoded.
# --------------------------------------------------------------------------- #


def test_format_direction_from_polarity() -> None:
    """For a lower-is-better metric a +Δ → RED / −Δ → GREEN; mirrored for higher (D-11)."""
    for metric, polarity in METRIC_POLARITY.items():
        positive_color, negative_color = sheets.delta_cell_colors(metric)
        if polarity is Polarity.LOWER_IS_BETTER:
            # value went UP → worse → red; value went DOWN → better → green.
            assert positive_color == sheets.RED, metric
            assert negative_color == sheets.GREEN, metric
        else:
            # higher-is-better score: value went UP → better → green; down → red.
            assert positive_color == sheets.GREEN, metric
            assert negative_color == sheets.RED, metric
    # Mirror sanity: a lower-is-better metric and a higher-is-better score map oppositely.
    assert sheets.delta_cell_colors("lcp_ms") != sheets.delta_cell_colors("perf_score")


# --------------------------------------------------------------------------- #
# D-12 / CR-01: the scrubber is seeded into the Sheets sink — an embedded
# credential is REDACTED in EVERY appended cell (mirrors the result.csv test).
# --------------------------------------------------------------------------- #


def test_scrubber_seeded_into_sheets() -> None:
    """A credential in page.url / slowest_request_url is redacted in every Sheets cell."""
    secret = "admin123"
    run = _leak_run(secret)
    scrub = make_scrubber("admin", secret)
    ws = _FakeWorksheet(existing=[])
    sheets.write_sheets(
        run,
        sheets_id="SOME_BARE_KEY",
        creds_path="unused.json",
        band_results=[],
        scrub=scrub,
        gc=_FakeGspreadClient(ws),
    )
    assert len(ws.append_rows_calls) == 1
    rows, _ = ws.append_rows_calls[0]
    # Flatten every appended cell to a string and assert the secret is gone everywhere.
    cells = [str(cell) for row in rows for cell in row]
    joined = "\n".join(cells)
    assert secret not in joined, "credential leaked into a Sheets cell (CR-01 / D-12)"
    # Prove the scrubber actually fired (not that the URL merely happened to drop).
    assert REDACTION_PLACEHOLDER in joined


def test_url_userinfo_stripped_in_sheet_row_no_secret_path() -> None:
    """WR-01: URL userinfo is stripped from a Sheets row even when scrub is identity.

    ``_build_sheet_row`` reuses ``output._build_csv_row``, so the unconditional
    ``redact_url_userinfo`` strip on the ``url`` / ``slowest_request_url`` cells means
    a credential embedded in a page URL never reaches a Sheets cell on the no-secret
    path (where the value scrubber is identity).
    """
    secret = "SECRET"
    run = RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-000000000aaa"),
        started_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC),
        target="https://x.com/",
        pages=[],
    )
    page = PageResult(
        url=f"https://user:{secret}@host/dashboard/",
        url_key="https://host/dashboard/",
        perf_score=80.0,
        slowest_request_url=f"https://user:{secret}@host/api",
    )
    row = sheets._build_sheet_row(run, page, {}, scrub=_identity_scrub)
    cells = [str(cell) for cell in row]
    joined = "\n".join(cells)
    assert secret not in joined, "credential leaked into a Sheets cell (WR-01)"
    assert "user:" not in joined
    # Host/path survives (no over-stripping).
    assert any("host/dashboard/" in c for c in cells)
