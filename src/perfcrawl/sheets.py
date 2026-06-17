"""The OUT-02 Google Sheets append-down writer — Phase 6 (D-08/D-09/D-10/D-11/D-12).

The Sheets sink mirrors ``output.py``'s posture: a docstring-as-contract module
whose ``scrub`` argument defaults to identity (non-auth callers pass nothing) and
whose every text cell is scrubbed at the write boundary (CR-01 "scrub every sink").

Critical invariants:

  - **Single-source schema (D-09).** ``SHEET_COLUMNS`` is a *strict superset* that
    STARTS WITH ``output.CSV_COLUMNS`` (imported, NEVER re-spelled), then appends one
    ``delta_<metric>`` column per banded metric (in ``registry.METRIC_POLARITY``
    order) and the three AI columns. A future CSV column edit forces the Sheets
    header to update — ``SHEET_COLUMNS[:len(CSV_COLUMNS)] == CSV_COLUMNS`` is the
    drift guard the planner named.

  - **One batched write per run (D-08).** All page rows go in a SINGLE
    ``append_rows(..., value_input_option="USER_ENTERED")`` call (so numeric metric
    + delta cells land as *numbers*, not text — required for the conditional-format
    rules to fire). The header is written exactly once via ``append_row`` and only
    when the sheet is empty. Never loop ``append_row`` per page (quota-critical).

  - **Open by key OR url (D-10).** ``--sheets-id`` accepts a bare key or a full URL;
    ``startswith("http")`` dispatches ``open_by_url`` vs ``open_by_key``.

  - **Formatting direction from polarity (D-11).** Red/green conditional-format
    direction is derived from ``registry.METRIC_POLARITY`` — never hardcoded per
    metric. For a lower-is-better metric a +Δ → red / −Δ → green; a higher-is-better
    score mirrors.

  - **Scrub every cell (D-12 / CR-01).** Every string cell passes through ``scrub``
    before ``append_rows``. ``scrub`` is a *value-based* redactor: it only removes the
    exact CONFIGURED secret strings it was seeded with, so on a no-secret run it is
    identity. The unconditional, value-independent guarantee for a credential embedded
    in ``page.url`` / ``slowest_request_url`` (the as-measured PageResult fields) is
    ``output.redact_url_userinfo`` (WR-01): the Sheets row reuses ``output._build_csv_row``,
    whose ``url`` / ``slowest_request_url`` cells already have their
    ``scheme://user:pass@`` userinfo stripped before they ever reach a Sheets cell —
    regardless of what (if anything) ``scrub`` was seeded with. The service-account
    JSON PATH is the only credential input and is NEVER written into a cell/row/header.

  - **Spreadsheets-only scope (D-10 / threat T-06-10).** ``Credentials`` requests
    the ``spreadsheets`` scope ONLY (not ``drive``) — ``open_by_key`` /
    ``open_by_url`` + append need nothing more, and an over-broad scope would grant
    Drive access.

``gspread`` + ``Credentials`` are imported at module top so tests can monkeypatch
``perfcrawl.sheets.gspread`` / ``perfcrawl.sheets.Credentials`` and inject a fake
client via ``gc=`` (no network, no ``gspread.authorize``).
"""

from collections.abc import Callable

import gspread
from google.oauth2.service_account import Credentials
from gspread_formatting import (
    BooleanCondition,
    BooleanRule,
    CellFormat,
    Color,
    ConditionalFormatRule,
    GridRange,
    get_conditional_format_rules,
)

from perfcrawl.models import PageResult, RunRecord
from perfcrawl.output import CSV_COLUMNS, _build_csv_row, _identity_scrub
from perfcrawl.registry import METRIC_POLARITY, Polarity
from perfcrawl.regression import BandResult

# D-10 / T-06-10: spreadsheets-only OAuth scope. open_by_key/open_by_url + append
# need nothing more; the Drive scope (open_by_name / create) is deliberately NOT
# requested so a leaked SA token cannot reach the wider Drive.
SHEETS_SCOPES: list[str] = ["https://www.googleapis.com/auth/spreadsheets"]

# AI columns appended after the per-metric delta columns (D-09 superset tail).
_AI_COLUMNS: list[str] = ["observation", "potential_cause", "suggested_optimization"]

# --- D-09: the Sheets header (single source — CSV_COLUMNS + deltas + AI cols) ---
# STRICT SUPERSET that STARTS WITH CSV_COLUMNS. The drift guard
# ``SHEET_COLUMNS[:len(CSV_COLUMNS)] == CSV_COLUMNS`` must hold at runtime: a future
# CSV column edit forces this header to update or test_schema_superset_of_csv fails.
SHEET_COLUMNS: list[str] = (
    list(CSV_COLUMNS) + [f"delta_{metric}" for metric in METRIC_POLARITY] + _AI_COLUMNS
)
# D-09 drift guard (load-bearing). An unconditional ``if ... raise`` rather than an
# ``assert`` so it survives ``python -O`` / ``-OO`` / ``PYTHONOPTIMIZE`` (which strip
# asserts), keeping the runtime superset invariant enforced in optimized deployments.
if SHEET_COLUMNS[: len(CSV_COLUMNS)] != list(CSV_COLUMNS):
    raise RuntimeError(
        "SHEET_COLUMNS must start with CSV_COLUMNS (D-09 drift guard)"
    )

# --- D-11: the two conditional-format fills (worse / better) ---
RED = Color(0.96, 0.80, 0.80)  # worse
GREEN = Color(0.80, 0.92, 0.80)  # better


def delta_cell_colors(metric: str) -> tuple[Color, Color]:
    """Return ``(positive_color, negative_color)`` for a metric's delta column (D-11).

    The direction is derived from ``registry.METRIC_POLARITY`` — never hardcoded per
    metric. For a **lower-is-better** metric a *positive* delta (value went up) is
    WORSE → red and a *negative* delta is BETTER → green; for a **higher-is-better**
    score the mapping mirrors (positive → green, negative → red).
    """
    if METRIC_POLARITY[metric] is Polarity.LOWER_IS_BETTER:
        return RED, GREEN
    return GREEN, RED


def _col_to_a1(col: int) -> str:
    """Convert a 1-based column number to its spreadsheet A1 letter(s) (1→A, 27→AA)."""
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _delta_columns() -> dict[str, str]:
    """Map each banded metric to the A1 column letter of its ``delta_<metric>`` cell.

    The delta columns immediately follow ``CSV_COLUMNS`` in ``SHEET_COLUMNS`` order,
    so metric *i* lives at 1-based column ``len(CSV_COLUMNS) + 1 + i``.
    """
    base = len(CSV_COLUMNS)
    return {metric: _col_to_a1(base + 1 + i) for i, metric in enumerate(METRIC_POLARITY)}


def _ai_cell(value: str | None, scrub: Callable[[str], str]) -> str:
    """Scrub one AI text field (empty string when the field is absent)."""
    return scrub(value) if value else ""


def _build_sheet_row(
    run: RunRecord,
    page: PageResult,
    band_by_metric: dict[str, BandResult],
    *,
    scrub: Callable[[str], str],
) -> list:
    """Flatten one (run, page) pair into a SHEET_COLUMNS-ordered row.

    Reuses ``output._build_csv_row`` for the base columns (D-09 — don't fork the CSV
    shape), then appends per-metric signed deltas and the AI fields:

      - **Base columns:** every string cell passes through ``scrub`` (D-12).
      - **delta_<metric>:** the signed ``delta_abs`` ONLY when that metric's
        ``BandResult`` is flagged (a real, banded regression/improvement); otherwise
        BLANK so no conditional-format rule fires (within-band = neutral, D-11).
        Flagged deltas stay NUMERIC (not str) so USER_ENTERED keeps them numbers and
        the NUMBER_GREATER/NUMBER_LESS rules can colour them.
      - **AI columns:** ``observation`` / ``potential_cause`` /
        ``suggested_optimization`` from ``page.analysis`` (scrubbed; blank if None).
    """
    base = _build_csv_row(run, page)
    row: list = [scrub(base[col]) for col in CSV_COLUMNS]

    for metric in METRIC_POLARITY:
        band = band_by_metric.get(metric)
        if band is not None and band.flagged and band.delta.delta_abs is not None:
            row.append(band.delta.delta_abs)  # numeric → formatting fires
        else:
            row.append("")  # within-band / not-comparable → neutral, no rule fires

    analysis = page.analysis
    row.append(_ai_cell(analysis.observation if analysis else None, scrub))
    row.append(_ai_cell(analysis.potential_cause if analysis else None, scrub))
    row.append(_ai_cell(analysis.suggested_optimization if analysis else None, scrub))
    return row


def _open(gc: gspread.Client, sheets_id: str):
    """Open the target spreadsheet by URL when ``sheets_id`` is an http(s) URL,
    else by bare key (D-10)."""
    if sheets_id.startswith("http"):
        return gc.open_by_url(sheets_id)
    return gc.open_by_key(sheets_id)


def apply_delta_formatting(ws, delta_col_for_metric: dict[str, str]) -> None:
    """Install red/green conditional-format rules over the open-ended delta columns.

    Idempotent (Pitfall 3): clears existing rules then rebuilds, so re-running a
    crawl against the same sheet never STACKS duplicate rules. Each delta column
    gets two rules over an open-ended ``<col>2:<col>`` range (covers every future
    appended row): NUMBER_GREATER(0) → ``positive_color`` and NUMBER_LESS(0) →
    ``negative_color``, both derived from :func:`delta_cell_colors` (polarity, D-11).
    """
    rules = get_conditional_format_rules(ws)
    rules.clear()  # idempotent: rebuild, don't stack (Pitfall 3)
    for metric, col_a1 in delta_col_for_metric.items():
        positive_color, negative_color = delta_cell_colors(metric)
        rng = GridRange.from_a1_range(f"{col_a1}2:{col_a1}", ws)  # row 2 → end (open)
        rules.append(
            ConditionalFormatRule(
                ranges=[rng],
                booleanRule=BooleanRule(
                    condition=BooleanCondition("NUMBER_GREATER", ["0"]),
                    format=CellFormat(backgroundColor=positive_color),
                ),
            )
        )
        rules.append(
            ConditionalFormatRule(
                ranges=[rng],
                booleanRule=BooleanRule(
                    condition=BooleanCondition("NUMBER_LESS", ["0"]),
                    format=CellFormat(backgroundColor=negative_color),
                ),
            )
        )
    rules.save()


def write_sheets(
    run: RunRecord,
    *,
    sheets_id: str,
    creds_path: str,
    band_results: list[BandResult],
    scrub: Callable[[str], str] | None = None,
    gc: gspread.Client | None = None,
) -> None:
    """Append one batched run to the target Google Sheet (OUT-02 / D-08..D-12).

    Workflow:

      1. Resolve a ``gspread.Client`` from the service-account JSON at ``creds_path``
         (spreadsheets-only scope, D-10) UNLESS a ``gc`` is injected (the test seam —
         no network, no ``gspread.authorize``).
      2. ``_open`` the spreadsheet by key or URL (D-10); take ``sheet1``.
      3. Read ``ws.get_all_values()`` once; write the ``SHEET_COLUMNS`` header via
         ``append_row(..., value_input_option="RAW")`` ONLY when the sheet is empty
         (D-08 header-once).
      4. Build EVERY page row (deltas keyed by ``(url_key, metric)`` from
         ``band_results``; AI fields from ``page.analysis``; every string cell
         scrubbed — D-12) then append them in ONE
         ``append_rows(..., value_input_option="USER_ENTERED")`` call (D-08).
      5. Install the polarity-driven conditional formatting once (D-11).

    ``scrub`` defaults to identity (non-auth callers pass nothing). ``band_results``
    is the HIST-02 ``flag_run`` output for this run; an empty list means no metric is
    flagged so every delta cell is blank.

    Returns ``None`` — the side effect is the Sheet append.
    """
    if scrub is None:
        scrub = _identity_scrub

    if gc is None:
        # T-06-09: the SA-JSON PATH is the only credential input; its contents are
        # never serialized into a cell/row/header. T-06-10: spreadsheets-only scope.
        creds = Credentials.from_service_account_file(creds_path, scopes=SHEETS_SCOPES)
        gc = gspread.authorize(creds)

    sh = _open(gc, sheets_id)
    ws = sh.sheet1

    # D-08: header written ONCE — only when the sheet has no rows yet.
    if not ws.get_all_values():
        ws.append_row(SHEET_COLUMNS, value_input_option="RAW")

    # Index the band verdicts by (page key, metric) so each page's delta cells are
    # addressable without re-scanning the flat band_results list per metric.
    band_index: dict[tuple[str, str], BandResult] = {
        (br.url_key, br.metric): br for br in band_results
    }

    rows = [
        _build_sheet_row(
            run,
            page,
            {
                metric: band
                for metric in METRIC_POLARITY
                if (band := band_index.get((page.url_key, metric))) is not None
            },
            scrub=scrub,
        )
        for page in run.pages
    ]
    # D-08 / Pitfall 2: ONE batched write, USER_ENTERED so numeric + delta cells
    # stay numbers (not text) and the conditional-format rules can fire.
    ws.append_rows(rows, value_input_option="USER_ENTERED")

    # D-11: install the polarity-driven red/green rules once. Conditional formatting
    # is a cosmetic layer over the data already written above; it needs a real
    # gspread Worksheet (``.spreadsheet`` API surface). The fully-mocked test
    # worksheet does not expose it, so guard on the duck-typed attribute rather than
    # letting a cosmetic step break (or require) the offline data-write path.
    if hasattr(ws, "spreadsheet"):
        apply_delta_formatting(ws, _delta_columns())
