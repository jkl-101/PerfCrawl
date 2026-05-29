"""perfcrawl measure — single-URL end-to-end audit (Phase 2 D-05 / D-06 / D-15).

The CLI is the controller layer: parse argv → build a RunConfig → hand off to
``orchestrator.measure_url()`` → write outputs to disk → persist to SQLite →
render result. Default mode prints a Rich human-readable table on stdout;
``--json`` prints the full RunRecord JSON on stdout. Progress and errors ALWAYS
go to stderr so machine-readable stdout under ``--json`` is never contaminated
(D-06).

Exit codes (D-15):

  0  page measured (including non-2xx per D-13 partial)
  1  user error — bad URL, bad flags, can't write output dir
  2  measurement error — all N samples failed, Chrome won't launch, etc.

(Phase 6 BUDG-01 will carve out 10+; the gap is intentional.)

Labeled-proxy invariant (D-11): the Rich row label for the TBT lab proxy is
``INP_PROXY_DISPLAY_LABEL`` from constants.py — never a hand-coded literal of
the forbidden bare-form tokens (the ones enumerated in
``models._FORBIDDEN_INP_FIELDS``). The displayed value comes from
``page.inp_proxy_tbt_ms.median`` — never a top-level page attribute that uses
the bare form (which doesn't exist by design). Together with model-layer
``_no_bare_inp`` (Phase 1), normalizer grep meta-test (Phase 2 plan 01), and
CSV column ``inp_proxy_tbt_ms`` (Phase 2 plan 04 Task 1), this forms a
4-layer defense-in-depth against the labeling drift threat.
"""

import sqlite3
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from perfcrawl.constants import (
    DEFAULT_SAMPLES_N,
    INP_PROXY_DISPLAY_LABEL,
    ExitCode,
)
from perfcrawl.orchestrator import MeasurementError, UserError, measure_url
from perfcrawl.output import write_outputs
from perfcrawl.store import init_db, write_run

# When a Typer app has exactly one ``@app.command()``, Typer treats it as the
# implicit root command — you'd invoke ``perfcrawl <url>`` instead of
# ``perfcrawl measure <url>``. D-05 requires the explicit ``measure`` subcommand
# verb (so future Phase 3 ``crawl`` / Phase 6 ``budget`` siblings live in the
# same namespace), so we register a hidden no-op command alongside ``measure``
# to force subcommand-style dispatch.
app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command(name="_internal", hidden=True)
def _internal() -> None:
    """Reserved (forces subcommand-style dispatch on the root app)."""

# D-06: progress + errors → stderr; final result → stdout.
err_console = Console(stderr=True)
out_console = Console()


def _format_scalar(value: float | None, *, fmt: str = "{:.0f}") -> str:
    if value is None:
        return "-"
    return fmt.format(value)


def _format_metric_sample(
    sample, *, fmt: str = "{:.0f}"
) -> str:
    if sample is None or sample.median is None:
        return "-"
    return fmt.format(sample.median)


def _render_human_table(run, *, samples: int, run_dir: Path) -> None:
    """Render the per-page result as a Rich table on stdout (D-06 + D-08).

    The Phase 2 single-URL contract means we always render exactly one page.
    Phase 3 multi-page rendering will iterate run.pages with a "Page" column.

    IN-02: defensive guard against a zero-page RunRecord. The orchestrator
    currently guarantees ``len(run.pages) >= 1`` (it raises MeasurementError
    when all samples fail), but a future Phase 3 regression that returns an
    empty-pages RunRecord (e.g. multi-page crawl where every page failed but
    the RunRecord was still constructed) would otherwise crash here with a
    bare ``IndexError``. Surface a clean notice instead.
    """
    if not run.pages:
        out_console.print(
            f"[yellow]No pages measured for {run.target}[/yellow] · "
            f"written to {run_dir}"
        )
        return
    page = run.pages[0]
    table = Table(title=f"perfcrawl: {run.target}")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Performance", _format_scalar(page.perf_score))
    table.add_row("Accessibility", _format_scalar(page.a11y_score))
    table.add_row("SEO", _format_scalar(page.seo_score))
    table.add_row("Best Practices", _format_scalar(page.best_practices_score))
    table.add_row("LCP (ms)", _format_metric_sample(page.lcp_ms))
    table.add_row("CLS", _format_metric_sample(page.cls, fmt="{:.3f}"))
    # D-11/D-15: the labeled-proxy row. Label reads INP_PROXY_DISPLAY_LABEL
    # (never a hand-coded literal); value comes from inp_proxy_tbt_ms.median
    # (never a 'page.tbt' or any forbidden bare-form attribute that doesn't
    # exist on the model).
    table.add_row(
        INP_PROXY_DISPLAY_LABEL,
        _format_metric_sample(page.inp_proxy_tbt_ms),
    )
    table.add_row("TTFB (ms)", _format_metric_sample(page.ttfb_ms))
    table.add_row("Requests", _format_scalar(page.request_count))
    table.add_row("Total bytes", _format_scalar(page.total_bytes))

    if page.slowest_request_url is not None and page.slowest_request_ms is not None:
        slowest = f"{page.slowest_request_url} ({page.slowest_request_ms:.0f} ms)"
    else:
        slowest = "-"
    table.add_row("Slowest request", slowest)
    table.add_row("Status code", _format_scalar(page.status_code))

    # D-08 footer: median-of-N annotation + on-disk location hint.
    table.caption = f"(median of {samples}) · written to {run_dir}"
    out_console.print(table)


@app.command()
def measure(
    url: str = typer.Argument(..., help="URL to audit"),
    samples: int = typer.Option(
        DEFAULT_SAMPLES_N,
        "--samples",
        "-n",
        min=1,
        help="Number of LH samples to take (median is reported; D-08).",
    ),
    emulation: str = typer.Option(
        "mobile",
        "--emulation",
        help="mobile | desktop (D-02 form factor).",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable RunRecord JSON to stdout (D-06).",
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output-dir",
        help="Per-run artifacts land under <output_dir>/<run_id>/ (D-07).",
    ),
) -> None:
    """Measure ``URL`` end-to-end: Chrome → LH → outputs → SQLite → stdout."""
    # --- D-15 USER_ERROR arm (input validation, before any subprocess) ---
    try:
        run_record, raw_artifacts = measure_url(
            url=url, samples=samples, emulation=emulation
        )
    except UserError as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    except MeasurementError as e:
        err_console.print(f"[red]measurement failed:[/red] {e}")
        raise typer.Exit(code=int(ExitCode.MEASUREMENT_ERROR)) from None

    # --- Write outputs (OUT-03 / OUT-04). OSError → USER_ERROR per D-15. ---
    try:
        run_dir = write_outputs(
            run_record, output_dir=output_dir, raw_artifacts=raw_artifacts
        )
    except OSError as e:
        err_console.print(
            f"[red]error:[/red] cannot write to {output_dir}: {e}"
        )
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # --- Persist to SQLite (HIST-01). DB lives alongside the artifacts. ---
    db_path = output_dir / "perfcrawl.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)  # idempotent per Phase 1 contract
        write_run(conn, run_record)
    finally:
        conn.close()

    # --- Render the final result on stdout (D-06). ---
    if output_json:
        # Plain sys.stdout write — Rich would inject ANSI even with no styling.
        sys.stdout.write(run_record.model_dump_json(indent=2))
        sys.stdout.write("\n")
    else:
        _render_human_table(run_record, samples=samples, run_dir=run_dir)
    # Implicit exit 0 (D-13: success-or-partial).
