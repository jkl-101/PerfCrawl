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

import httpx
import typer
from rich.console import Console
from rich.table import Table

from perfcrawl.constants import (
    CRAWLER_USER_AGENT,
    DEFAULT_CONCURRENCY,
    DEFAULT_CRAWL_SAMPLES_N,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_PAGES,
    DEFAULT_MIN_DELAY_S,
    DEFAULT_SAMPLES_N,
    INP_PROXY_DISPLAY_LABEL,
    ExitCode,
)
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import discover
from perfcrawl.crawl.measure_pass import measure_pass
from perfcrawl.crawl.robots import fetch_robots_gate
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


def _is_error_row(page) -> bool:
    """True iff ``page`` is a tagged error row (no score, no CWV — D-03).

    An error row carries only ``url``/``url_key`` (and maybe ``status_code``);
    every metric is None. Used to split the crawl summary into measured vs errors.
    """
    return page.perf_score is None and page.lcp_ms is None


def _render_crawl_summary(run, *, samples: int, run_dir: Path) -> None:
    """Render the multi-page crawl result as a Rich table on stdout (D-06).

    One row per page with a Page (url) column + the headline metrics, plus a
    measured/errors summary caption. Honors the existing zero-page guard so an
    all-pages-failed crawl degrades to a clean notice rather than crashing.
    """
    if not run.pages:
        out_console.print(
            f"[yellow]No pages measured for {run.target}[/yellow] · "
            f"written to {run_dir}"
        )
        return

    table = Table(title=f"perfcrawl crawl: {run.target}")
    table.add_column("Page", style="bold", overflow="fold")
    table.add_column("Perf", justify="right")
    table.add_column("LCP (ms)", justify="right")
    table.add_column(INP_PROXY_DISPLAY_LABEL, justify="right")
    table.add_column("TTFB (ms)", justify="right")
    table.add_column("Status", justify="right")

    measured = 0
    errors = 0
    for page in run.pages:
        if _is_error_row(page):
            errors += 1
            table.add_row(
                page.url,
                "[red]error[/red]",
                "-",
                "-",
                "-",
                _format_scalar(page.status_code),
            )
        else:
            measured += 1
            table.add_row(
                page.url,
                _format_scalar(page.perf_score),
                _format_metric_sample(page.lcp_ms),
                _format_metric_sample(page.inp_proxy_tbt_ms),
                _format_metric_sample(page.ttfb_ms),
                _format_scalar(page.status_code),
            )

    table.caption = (
        f"{measured} measured · {errors} errors · "
        f"(median of {samples}) · written to {run_dir}"
    )
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


@app.command()
def crawl(
    url: str = typer.Argument(..., help="Seed URL to crawl (same-origin BFS)."),
    max_pages: int = typer.Option(
        DEFAULT_MAX_PAGES,
        "--max-pages",
        min=1,
        help="Stop after this many in-scope pages (D-09 enqueue bound).",
    ),
    max_depth: int = typer.Option(
        DEFAULT_MAX_DEPTH,
        "--max-depth",
        min=0,
        help="BFS depth bound; sitemap seeds are depth 0 (D-09).",
    ),
    concurrency: int = typer.Option(
        DEFAULT_CONCURRENCY,
        "--concurrency",
        min=1,
        help="Worker-pool size = one independent Chrome per worker (D-09).",
    ),
    delay: float = typer.Option(
        DEFAULT_MIN_DELAY_S,
        "--delay",
        min=0.0,
        help="Minimum inter-request delay (s) per host; robots Crawl-delay wins if stricter.",
    ),
    samples: int = typer.Option(
        DEFAULT_CRAWL_SAMPLES_N,
        "--samples",
        "-n",
        min=1,
        help="LH samples per page (crawl defaults to 1; median reported; D-10).",
    ),
    include: list[str] = typer.Option(
        None,
        "--include",
        help="Glob to include (repeatable); no --include means all in-scope (D-13).",
    ),
    exclude: list[str] = typer.Option(
        None,
        "--exclude",
        help="Glob to exclude (repeatable; exclude wins over include; D-14).",
    ),
    include_subdomains: bool = typer.Option(
        False,
        "--include-subdomains",
        help="Treat sibling subdomains of the seed host as in-scope (D-06).",
    ),
    no_sitemap: bool = typer.Option(
        False,
        "--no-sitemap",
        help="Skip sitemap.xml / robots Sitemap: seeding (D-07).",
    ),
    ignore_robots: bool = typer.Option(
        False,
        "--ignore-robots",
        help="Bypass robots.txt (OWNED SITES ONLY — emits a loud stderr warning; D-11).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Discover only: print the in-scope URLs + error tags, measure nothing (D-04).",
    ),
    emulation: str = typer.Option(
        "mobile",
        "--emulation",
        help="mobile | desktop (D-02 form factor).",
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output-dir",
        help="Per-run artifacts land under <output_dir>/<run_id>/ (D-07).",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the full multi-page RunRecord JSON to stdout (D-06).",
    ),
) -> None:
    """Crawl ``URL`` end to end: discover same-origin pages, measure each, output.

    Discovers in-scope pages by following ``<a href>`` links (BFS, bounded by
    --max-pages/--max-depth + the per-base-path variant cap), then measures each
    via the unchanged single-page measurement seam, writing one multi-page run
    (aggregated CSV/JSON + per-page raw Lighthouse artifacts).

    Limitation (D-02): only static ``<a href>`` links are discovered —
    JavaScript-rendered / ``javascript:``-scheme navigation is NOT followed.
    """
    # --- D-15 USER_ERROR arm: validate the seed before any network/subprocess ---
    if not (url or "").strip():
        err_console.print("[red]error:[/red] seed URL is empty")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    if emulation not in {"mobile", "desktop"}:
        err_console.print(
            f"[red]error:[/red] --emulation must be 'mobile' or 'desktop'; got {emulation!r}"
        )
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    cfg = CrawlConfig(
        max_pages=max_pages,
        max_depth=max_depth,
        concurrency=concurrency,
        min_delay_s=delay,
        samples=samples,
        emulation=emulation,
        includes=list(include or []),
        excludes=list(exclude or []),
        include_subdomains=include_subdomains,
        use_sitemap=not no_sitemap,
        ignore_robots=ignore_robots,
        dry_run=dry_run,
    )

    # D-11: --ignore-robots is a policy bypass — make it impossible to do silently.
    if cfg.ignore_robots:
        err_console.print(
            "[bold yellow]WARNING:[/bold yellow] --ignore-robots set — bypassing "
            "robots.txt. Use only on sites you own or are authorized to crawl."
        )

    # --- Discovery pass (CRAWL-01..04). httpx client = the injected fetch. ---
    # A polite default UA + redirect-following + a per-request timeout so an
    # unresponsive host cannot hang the crawl. Discovery itself never raises on
    # untrusted remote HTML/robots/sitemap (soft-fail / never-raise discipline).
    with httpx.Client(
        follow_redirects=True,
        timeout=30.0,
        headers={"User-Agent": CRAWLER_USER_AGENT},
    ) as client:
        gate = fetch_robots_gate(
            url,
            fetch=client.get,
            default_delay=cfg.min_delay_s,
            ignore=cfg.ignore_robots,
        )
        in_scope, errors = discover(url, cfg=cfg, robots=gate, fetch=client.get)

    # --- D-04: dry-run prints discovery output, measures NOTHING, exits 0. ---
    if cfg.dry_run:
        out_console.print(f"[bold]In-scope ({len(in_scope)}):[/bold]")
        for item in in_scope:
            # One per line, copy-pasteable (no Rich markup interpolated into the URL).
            sys.stdout.write(item.url + "\n")
        if errors:
            out_console.print(f"[bold]Errors ({len(errors)}):[/bold]")
            for err in errors:
                status = err.status_code if err.status_code is not None else "fetch-error"
                sys.stdout.write(f"{err.url}\t{status}\n")
        raise typer.Exit(code=int(ExitCode.SUCCESS))

    # --- Measurement pass: bounded pool over the unchanged measure_url seam. ---
    run_record, merged_artifacts = measure_pass(
        in_scope, errors, cfg=cfg, target=url
    )

    # D-15 MEASUREMENT_ERROR arm: a crawl where there were pages to measure but
    # every one collapsed to an error row is a measurement failure (exit 2), not
    # a clean partial. An all-error run with zero in-scope seeds (nothing to
    # measure) still surfaces as a measurement error so a silent zero-data crawl
    # is never reported as success (T-02-03-PARTIAL lifted to the crawl level).
    measured_count = sum(1 for p in run_record.pages if not _is_error_row(p))
    if measured_count == 0:
        err_console.print(
            "[red]measurement failed:[/red] no page was measured "
            f"({len(run_record.pages)} discovered, all errored or none in scope)"
        )

    # --- Write outputs (OUT-03/OUT-04). OSError → USER_ERROR per D-15. ---
    try:
        run_dir = write_outputs(
            run_record, output_dir=output_dir, raw_artifacts=merged_artifacts
        )
    except OSError as e:
        err_console.print(f"[red]error:[/red] cannot write to {output_dir}: {e}")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # --- Persist the one multi-page run to SQLite (HIST-01). ---
    db_path = output_dir / "perfcrawl.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        write_run(conn, run_record)
    finally:
        conn.close()

    # --- Render the multi-page result on stdout (D-06). ---
    if output_json:
        sys.stdout.write(run_record.model_dump_json(indent=2))
        sys.stdout.write("\n")
    else:
        _render_crawl_summary(run_record, samples=samples, run_dir=run_dir)

    # Exit 2 if nothing measured; else 0 (success-or-partial per D-13).
    if measured_count == 0:
        raise typer.Exit(code=int(ExitCode.MEASUREMENT_ERROR))
