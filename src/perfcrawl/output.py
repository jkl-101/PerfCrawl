"""On-disk artifact writers for Phase 2 (OUT-03 / OUT-04 / D-07).

Writes:

- ``<output_dir>/<run_id>/result.json`` — full-fidelity RunRecord
  (``RunRecord.model_dump_json()``), round-trip-identical to the SQLite blob
  (mirrors the Phase 1 hybrid-store contract).
- ``<output_dir>/<run_id>/result.csv`` — flat one-row-per-page CSV in the
  locked ``CSV_COLUMNS`` order (a superset of the existing studyhalo Google
  Sheet schema; Phase 6 Sheets exporter reads the same shape).
- ``<output_dir>/<run_id>/lighthouse/<page-slug>.{json,html}`` — raw Lighthouse
  JSON + HTML artifacts per page, with ``<page-slug>`` going through
  ``page_slug()`` (IN-02 sanitization boundary — url_key can contain literal
  ``..`` from w3lib's ``%2e%2e`` decode and must never reach a filesystem path).

Critical invariants:

  - EVERY filesystem path component derived from a URL goes through
    ``page_slug()``. Never use the raw url_key as a filename interpolation
    target. The plan-level grep guard in 02-04 verify asserts this.
  - The CSV column header for the TBT field is ``inp_proxy_tbt_ms`` — the field
    name IS the labeling signal (D-11/D-15). The Rich human table in cli.py
    uses ``INP_PROXY_DISPLAY_LABEL``; the CSV uses the field name.
  - All file writes are atomic via ``tempfile.NamedTemporaryFile`` +
    ``os.replace`` so a crash mid-write never leaves a half-CSV that
    downstream tooling would silently mis-parse.

Pattern lineage: file-I/O analog of Phase 1 ``store.py``'s ``with conn:``
transaction wrapper — a write either lands whole or not at all (CR-01).
"""

import csv
import io
import os
import tempfile
from pathlib import Path

from perfcrawl.models import MetricSample, PageResult, RunRecord
from perfcrawl.slug import page_slug

# --- OUT-04: the CSV column order (the ONE editable place) ------------------
# Verbatim from 02-RESEARCH § "CSV column order (OUT-04)" lines 848-881.
# Phase 6 Sheets exporter reads this same list — drift here = drift there.
CSV_COLUMNS: list[str] = [
    # --- existing Google Sheet columns (preserve order, names match team's spreadsheet) ---
    "page",                  # human label — empty for now; Phase 3 fills from <title>/path
    "url",                   # PageResult.url (as measured)
    "test_date",             # RunRecord.started_at.isoformat()
    "cache_disabled",        # always "TRUE" — RUN-03 cold cache invariant
    "total_page_load_time",  # ms — audits['interactive'].numericValue (TTI proxy, Open Q4)
    "request_count",         # PageResult.request_count
    "total_bytes",           # PageResult.total_bytes
    "slowest_request_url",   # PageResult.slowest_request_url
    "slowest_request_ms",    # PageResult.slowest_request_ms
    "ttfb_ms",               # PageResult.ttfb_ms.median
    "status_code",           # PageResult.status_code
    # --- Phase 1 / Phase 2 additions ---
    "perf_score",            # PageResult.perf_score
    "a11y_score",            # PageResult.a11y_score
    "seo_score",             # PageResult.seo_score
    "best_practices_score",  # PageResult.best_practices_score
    "lcp_ms",                # PageResult.lcp_ms.median
    "cls",                   # PageResult.cls.median
    "inp_proxy_tbt_ms",      # D-11/D-15: labeled column name — never a bare 'inp' header
    "schema_version",        # RunRecord.schema_version
    "run_id",                # RunRecord.id (as string)
    "chrome_version",        # RunRecord.chrome_version
    "lighthouse_version",    # RunRecord.lighthouse_version
    "emulation",             # RunRecord.emulation
]


def _metric_sample_median(ms: MetricSample | None) -> float | None:
    """Flatten a MetricSample to its median (or None if the sample is absent)."""
    return ms.median if ms is not None else None


def _stringify(value: object) -> str:
    """Coerce a scalar to a CSV cell. None / empty become the empty string."""
    if value is None:
        return ""
    return str(value)


def _total_page_load_time(page: PageResult) -> str:
    """Pull TTI from ``audits['interactive'].numericValue`` (Open Q4 / MEDIUM-4).

    The normalizer (02-01 Task 3) keeps ``interactive`` in diagnostics
    unconditionally regardless of audit score, so this lookup never goes
    missing on a healthy page. The only case the key is absent is a D-13
    non-2xx response (LH skips category-level audits entirely); empty string
    is the documented fallback in that case.
    """
    diag = page.diagnostics or {}
    interactive = diag.get("interactive")
    if isinstance(interactive, dict):
        value = interactive.get("numericValue")
        if value is not None:
            return str(value)
    return ""


def _build_csv_row(run: RunRecord, page: PageResult) -> dict[str, str]:
    """Flatten one (run, page) pair into a dict keyed by CSV_COLUMNS."""
    return {
        # Phase 2 is single-URL; Phase 3 will fill this from <title>/path (Open Q2).
        "page": "",
        "url": _stringify(page.url),
        "test_date": run.started_at.isoformat(),
        # RUN-03 invariant: every Phase 2 sample runs cold-cache.
        "cache_disabled": "TRUE",
        "total_page_load_time": _total_page_load_time(page),
        "request_count": _stringify(page.request_count),
        "total_bytes": _stringify(page.total_bytes),
        "slowest_request_url": _stringify(page.slowest_request_url),
        "slowest_request_ms": _stringify(page.slowest_request_ms),
        "ttfb_ms": _stringify(_metric_sample_median(page.ttfb_ms)),
        "status_code": _stringify(page.status_code),
        "perf_score": _stringify(page.perf_score),
        "a11y_score": _stringify(page.a11y_score),
        "seo_score": _stringify(page.seo_score),
        "best_practices_score": _stringify(page.best_practices_score),
        "lcp_ms": _stringify(_metric_sample_median(page.lcp_ms)),
        "cls": _stringify(_metric_sample_median(page.cls)),
        # D-11/D-15: column NAME (the dict key) is the labeling — value comes
        # from inp_proxy_tbt_ms.median, never a bare 'inp' anywhere.
        "inp_proxy_tbt_ms": _stringify(_metric_sample_median(page.inp_proxy_tbt_ms)),
        "schema_version": str(run.schema_version),
        "run_id": str(run.id),
        "chrome_version": _stringify(run.chrome_version),
        "lighthouse_version": _stringify(run.lighthouse_version),
        "emulation": _stringify(run.emulation),
    }


def _atomic_write_text(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` atomically (tmp + os.replace).

    Mirrors store.py's ``with conn:`` transaction: the file either appears
    whole at ``target`` or it doesn't appear at all. A crash mid-write never
    leaves a half-written file at the consumer-visible path.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    # delete=False so we own the lifecycle; suffix differs from the final name
    # so a stray .tmp.* glob (test_no_tmp_files_left_after_write) can find it.
    with tempfile.NamedTemporaryFile(
        dir=target.parent,
        delete=False,
        mode="w",
        suffix=f".tmp.{target.suffix.lstrip('.')}",
        encoding="utf-8",
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, target)


def _unique_slug_path(directory: Path, base_slug: str, suffix: str) -> Path:
    """Pick a non-colliding ``<slug>{__N}?<suffix>`` path under ``directory``.

    Phase 2 is single-URL so the suffix mechanism is forward-compat for
    Phase 3 (D-07: collision suffix ``__1``, ``__2``, ... per planner
    convention).
    """
    candidate = directory / f"{base_slug}{suffix}"
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = directory / f"{base_slug}__{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def write_outputs(
    run_record: RunRecord,
    *,
    output_dir: Path,
    raw_artifacts: dict[str, tuple[str, str]] | None = None,
) -> Path:
    """Write the per-run artifact tree under ``<output_dir>/<run_id>/``.

    Layout (D-07):

      <output_dir>/<run_id>/
        result.json                          # full-fidelity RunRecord JSON
        result.csv                           # flat one-row-per-page CSV
        lighthouse/<page-slug>.json          # raw LH JSON (if provided)
        lighthouse/<page-slug>.html          # raw LH HTML (if provided)

    Returns the run-scoped directory so the caller can surface it to the user
    ("written to <path>" footer in the Rich table). Raises ``OSError`` if the
    output directory can't be created or written to (the CLI maps this to
    ``ExitCode.USER_ERROR`` per D-15).

    ``raw_artifacts`` maps a page's ``url_key`` to ``(reportJson, reportHtml)``
    strings. Pages with no entry in the map don't get a ``lighthouse/`` artifact.
    """
    run_dir = Path(output_dir) / str(run_record.id)
    run_dir.mkdir(parents=True, exist_ok=True)

    # --- result.json: full-fidelity, atomic ---
    _atomic_write_text(
        run_dir / "result.json",
        run_record.model_dump_json(indent=2),
    )

    # --- result.csv: locked column order, atomic ---
    # Build the CSV content in-memory then write it atomically. csv.DictWriter
    # against a StringIO keeps the locked column order intact without a
    # mid-write window where result.csv would be half-populated.
    #
    # WR-09: Python's ``csv`` module always emits ``\r\n`` per RFC 4180, and
    # StringIO does not apply newline translation. Without stripping the CR
    # bytes here, naive consumers (jq, awk, gspread cell-upload, and any
    # ``open(..., newline="\n")`` reader) see literal ``\r`` characters at
    # end-of-row. Normalize to LF-only before the atomic write.
    # IN-06: ``import io`` lives at the module top (alongside ``import csv``,
    # ``import os``, ``import tempfile``) rather than inline here. Stdlib
    # imports are cached and free on subsequent calls; inline imports
    # complicate static analysis (mypy, ruff's I001) and grep discovery for
    # no real benefit.
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="raise")
    writer.writeheader()
    for page in run_record.pages:
        writer.writerow(_build_csv_row(run_record, page))
    csv_content = buf.getvalue().replace("\r\n", "\n")
    _atomic_write_text(run_dir / "result.csv", csv_content)

    # --- lighthouse/<slug>.{json,html} ---
    if raw_artifacts:
        lh_dir = run_dir / "lighthouse"
        lh_dir.mkdir(parents=True, exist_ok=True)
        for page in run_record.pages:
            artifact = raw_artifacts.get(page.url_key)
            if artifact is None:
                continue
            report_json, report_html = artifact
            # IN-02 BOUNDARY: url_key flows through page_slug() before becoming
            # a filename component. The raw url_key must NEVER be interpolated
            # directly into a path string — page_slug() is the sanitizer.
            base_slug = page_slug(page.url_key)
            # WR-04: truthiness, not ``is not None`` — empty strings (the
            # orchestrator's previous default for a missing key) used to slip
            # through and produce zero-byte ``<slug>.{json,html}`` files. A
            # missing payload should produce a MISSING file, not an empty one.
            if report_json:
                json_path = _unique_slug_path(lh_dir, base_slug, ".json")
                _atomic_write_text(json_path, report_json)
            if report_html:
                html_path = _unique_slug_path(lh_dir, base_slug, ".html")
                _atomic_write_text(html_path, report_html)

    return run_dir
