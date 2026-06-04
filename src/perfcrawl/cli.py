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

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import typer
from rich.console import Console
from rich.table import Table

from perfcrawl.auth import AuthError, make_scrubber, resolve_auth_state
from perfcrawl.constants import (
    CRAWLER_USER_AGENT,
    DEFAULT_CONCURRENCY,
    DEFAULT_CRAWL_SAMPLES_N,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_PAGES,
    DEFAULT_MIN_DELAY_S,
    DEFAULT_SAMPLES_N,
    INP_PROXY_DISPLAY_LABEL,
    PERFCRAWL_PASSWORD_ENV,
    PERFCRAWL_USERNAME_ENV,
    ExitCode,
)
from perfcrawl.crawl import is_error_row
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import discover
from perfcrawl.crawl.measure_pass import measure_pass
from perfcrawl.crawl.robots import fetch_robots_gate
from perfcrawl.crawl.scope import is_denied
from perfcrawl.orchestrator import (
    MeasurementError,
    UserError,
    _launch_chrome_with_cdp_port,
    measure_url,
)
from perfcrawl.output import write_outputs
from perfcrawl.store import init_db, write_run


def _load_dotenv_if_present() -> None:
    """Best-effort ``.env`` load so ``PERFCRAWL_USERNAME``/``PERFCRAWL_PASSWORD``
    can live in a (gitignored) ``.env`` for local dev convenience (D-07).

    python-dotenv is NOT a hard dependency — when it is absent this is a clean
    no-op and credentials are read straight from ``os.environ`` as set by the
    shell. Adopting dotenv as a runtime dep would be gated behind a supply-chain
    checkpoint; the default path takes no install.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _teardown_chrome(chrome, user_data_dir: Path) -> None:
    """Kill + reap Chrome and rmtree its temp profile (the orchestrator pattern).

    Reused by the crawl form-login resolution and the ``login`` subcommand so a
    short-lived login Chrome never leaks a zombie or a tempdir (T-02-03-Z).
    """
    import shutil
    import subprocess as _sp

    try:
        chrome.kill()
        try:
            chrome.wait(timeout=5)
        except _sp.TimeoutExpired:
            # Already SIGKILL'd; the kernel will reap eventually.
            pass
    except Exception:
        pass
    shutil.rmtree(user_data_dir, ignore_errors=True)


def _resolve_crawl_auth(
    cfg: CrawlConfig,
    *,
    username: str | None,
    password: str | None,
    success_rule: dict[str, str] | None,
) -> dict:
    """Resolve the crawl's authenticated session ONCE (D-01/D-02).

    Two mutually-exclusive paths (already validated mutually exclusive in the
    caller). The ``--auth-state`` path is Chrome-free — just load + validate the
    saved JSON. The driven-form-login path launches ONE headless Chrome via the
    reused ``_launch_chrome_with_cdp_port`` seam (D-03 #3), logs in on its DEFAULT
    context, captures the ``storage_state``, then tears the login Chrome down
    (the measurement pool launches its OWN independent Chromes per worker — the
    login Chrome's only job is to mint the session). Raises ``AuthError`` on any
    failure (the caller maps it to ExitCode.AUTH_ERROR=3).
    """
    # Saved-state escape hatch: no Chrome needed.
    if cfg.auth_state_path:
        return resolve_auth_state(auth_state_path=cfg.auth_state_path)

    # Form-login path: launch a short-lived Chrome, log in, capture, tear down.
    chrome, port, user_data_dir = _launch_chrome_with_cdp_port()
    try:
        return resolve_auth_state(
            port=port,
            login_url=cfg.login_url,
            user_sel=cfg.user_sel,
            pass_sel=cfg.pass_sel,
            submit_sel=cfg.submit_sel,
            username=username,
            password=password,
            success_rule=success_rule,
        )
    finally:
        _teardown_chrome(chrome, user_data_dir)


# D-05 requires explicit subcommand verbs (``measure`` / ``crawl``, plus future
# Phase 6 ``budget`` siblings) in one namespace. With two real ``@app.command()``s
# registered, Typer already dispatches subcommand-style — ``perfcrawl measure
# <url>`` / ``perfcrawl crawl <url>`` — so the earlier hidden ``_internal`` no-op
# shim (needed only when a single command would otherwise collapse to the implicit
# root command) is no longer required and has been removed (IN-04).
app = typer.Typer(no_args_is_help=True, add_completion=False)

# D-06: progress + errors → stderr; final result → stdout.
err_console = Console(stderr=True)
out_console = Console()


def _format_scalar(value: float | None, *, fmt: str = "{:.0f}") -> str:
    if value is None:
        return "-"
    return fmt.format(value)


def _format_metric_sample(sample, *, fmt: str = "{:.0f}") -> str:
    if sample is None or sample.median is None:
        return "-"
    return fmt.format(sample.median)


def _origin_of(url: str) -> str:
    """Return the ``scheme://netloc`` origin of ``url``, or ``url`` unchanged
    when it has no parseable scheme+host (so callers degrade safely)."""
    parts = urlsplit(url)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return url


def _relativize_url(url: str, origin: str) -> str:
    """Strip the shared ``origin`` from ``url`` for compact crawl-table display.

    A whole-site crawl shares one origin, so the per-row URL is just noise once
    the origin lives in the table header. Returns the path (+query/fragment)
    when ``url`` is on ``origin``; the site root collapses to ``/``. URLs on a
    different origin — or that don't parse to a scheme+host — fall back to the
    full URL so cross-origin / malformed rows stay unambiguous.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    if f"{parts.scheme}://{parts.netloc}" != origin:
        return url
    rel = parts.path or "/"
    if parts.query:
        rel = f"{rel}?{parts.query}"
    if parts.fragment:
        rel = f"{rel}#{parts.fragment}"
    return rel


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
            f"[yellow]No pages measured for {run.target}[/yellow] · written to {run_dir}"
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


def _render_crawl_summary(run, *, samples: int, run_dir: Path) -> None:
    """Render the multi-page crawl result as a Rich table on stdout (D-06).

    One row per page with a Page (url) column + the headline metrics, plus a
    measured/errors summary caption. Honors the existing zero-page guard so an
    all-pages-failed crawl degrades to a clean notice rather than crashing.
    """
    if not run.pages:
        out_console.print(
            f"[yellow]No pages measured for {run.target}[/yellow] · written to {run_dir}"
        )
        return

    origin = _origin_of(run.target)
    table = Table(title=f"perfcrawl crawl · {origin}")
    table.add_column("Page", style="bold", overflow="fold")
    table.add_column("Perf", justify="right")
    table.add_column("LCP (ms)", justify="right")
    table.add_column(INP_PROXY_DISPLAY_LABEL, justify="right")
    table.add_column("TTFB (ms)", justify="right")
    table.add_column("Status", justify="right")

    measured = 0
    errors = 0
    for page in run.pages:
        if is_error_row(page):
            errors += 1
            table.add_row(
                _relativize_url(page.url, origin),
                "[red]error[/red]",
                "-",
                "-",
                "-",
                _format_scalar(page.status_code),
            )
        else:
            measured += 1
            table.add_row(
                _relativize_url(page.url, origin),
                _format_scalar(page.perf_score),
                _format_metric_sample(page.lcp_ms),
                _format_metric_sample(page.inp_proxy_tbt_ms),
                _format_metric_sample(page.ttfb_ms),
                _format_scalar(page.status_code),
            )

    table.caption = (
        f"{measured} measured · {errors} errors · (median of {samples}) · written to {run_dir}"
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
        run_record, raw_artifacts = measure_url(url=url, samples=samples, emulation=emulation)
    except UserError as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    except MeasurementError as e:
        err_console.print(f"[red]measurement failed:[/red] {e}")
        raise typer.Exit(code=int(ExitCode.MEASUREMENT_ERROR)) from None

    # --- Write outputs (OUT-03 / OUT-04). OSError → USER_ERROR per D-15. ---
    try:
        run_dir = write_outputs(run_record, output_dir=output_dir, raw_artifacts=raw_artifacts)
    except OSError as e:
        err_console.print(f"[red]error:[/red] cannot write to {output_dir}: {e}")
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
    login_url: str = typer.Option(
        None,
        "--login-url",
        help="Form-login URL. With --user-sel/--pass-sel/--submit-sel, log in once "
        "before the crawl. Credentials come from env (PERFCRAWL_USERNAME / "
        "PERFCRAWL_PASSWORD) — NEVER a flag (argv is visible in ps/history; D-07).",
    ),
    user_sel: str = typer.Option(
        None, "--user-sel", help="CSS selector for the username field (form login)."
    ),
    pass_sel: str = typer.Option(
        None, "--pass-sel", help="CSS selector for the password field (form login)."
    ),
    submit_sel: str = typer.Option(
        None, "--submit-sel", help="CSS selector for the submit control (form login)."
    ),
    auth_state: str = typer.Option(
        None,
        "--auth-state",
        help="Path to a saved Playwright storage_state JSON (from `perfcrawl login`). "
        "Mutually exclusive with --login-url form login (D-01).",
    ),
    success_text: str = typer.Option(
        None,
        "--success-text",
        help="Optional login-success marker text (for the 200-logged-out edge case).",
    ),
    success_url: str = typer.Option(
        None,
        "--success-url",
        help="Optional login-success landing-URL fragment.",
    ),
    deny: list[str] = typer.Option(
        None,
        "--deny",
        help="Extra destructive-link deny substring (repeatable; extends the always-on "
        "built-in denylist — logout/delete/admin/… ; D-05).",
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

    # D-01: the two auth inputs are mutually exclusive — a saved storage_state OR
    # a driven form login, never both.
    if auth_state and login_url:
        err_console.print(
            "[red]error:[/red] --auth-state and --login-url are mutually exclusive "
            "(supply a saved session OR drive a form login, not both)"
        )
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # D-07: credentials enter via env ONLY (NEVER argv — visible in ps/history).
    # Best-effort .env load first (no-op without python-dotenv).
    _load_dotenv_if_present()
    username = os.environ.get(PERFCRAWL_USERNAME_ENV)
    password = os.environ.get(PERFCRAWL_PASSWORD_ENV)

    # Seed the central credential scrubber ONCE (D-07). Applied to every sink
    # (stderr, RunRecord JSON, LH artifacts) so no credential ever lands in a log
    # or on disk. Empty/None secrets are filtered by make_scrubber (no-op scrub).
    scrub = make_scrubber(username, password)

    success_rule: dict[str, str] | None = None
    if success_text:
        success_rule = {"text": success_text}
    elif success_url:
        success_rule = {"url": success_url}

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
        # D-05: --deny extends (never replaces) the always-on built-in denylist.
        deny_patterns=[*CrawlConfig().deny_patterns, *(deny or [])],
        # D-01 auth carrier fields → discovery login-URL exclusion + measure_pass.
        login_url=login_url,
        user_sel=user_sel,
        pass_sel=pass_sel,
        submit_sel=submit_sel,
        auth_state_path=auth_state,
        success_text=success_text,
        success_url=success_url,
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

    # --- Resolve the authenticated session ONCE, before measuring (D-01/D-02). ---
    # Either path (saved --auth-state OR a driven form login) yields a validated
    # storage_state that is threaded into every worker's measure_url. An AuthError
    # (login can't be confirmed, stale/empty state, unreadable file) maps to
    # ExitCode.AUTH_ERROR (3) — fail fast at t=0, before paying for a crawl
    # (Pitfall 4). All auth-adjacent error text is scrubbed before printing.
    resolved_auth_state: dict | None = None
    if cfg.auth_state_path or cfg.login_url:
        try:
            resolved_auth_state = _resolve_crawl_auth(
                cfg, username=username, password=password, success_rule=success_rule
            )
        except AuthError as e:
            err_console.print(scrub(f"[red]auth failed:[/red] {e}"))
            raise typer.Exit(code=int(ExitCode.AUTH_ERROR)) from None
        except MeasurementError as e:
            # WR-01: a Chrome/launch failure from `_launch_chrome_with_cdp_port`
            # ("Chrome did not write DevToolsActivePort", any launch breakage) is
            # a MEASUREMENT_ERROR (2), NOT an auth problem. Keep its own exit band
            # so `case $? in 3) re-auth ;; esac` scripts can distinguish a
            # session/login failure (3) from Chrome/LH breakage (2) — otherwise a
            # broken Chrome env on a form-login crawl makes a re-auth loop forever.
            # Still scrubbed, since the launch path is on an auth-adjacent flow.
            err_console.print(scrub(f"[red]measurement failed:[/red] {e}"))
            raise typer.Exit(code=int(ExitCode.MEASUREMENT_ERROR)) from None
        except Exception as e:
            # Defense-in-depth (AUTH-04): even a leaked NON-AuthError (e.g. a raw
            # Playwright exception that slipped a wrap) is scrubbed via the
            # creds-seeded `scrub` and mapped to AUTH_ERROR — never an unscrubbed
            # Typer traceback. Runs AFTER the AuthError/MeasurementError arms so
            # each keeps its own message; `from None` suppresses the chain.
            err_console.print(scrub(f"[red]auth failed:[/red] {e}"))
            raise typer.Exit(code=int(ExitCode.AUTH_ERROR)) from None

    # --- Measurement pass: bounded pool over the unchanged measure_url seam. ---
    # CR-01: carry the robots-aware effective delay into the measurement pass so a
    # robots Crawl-delay honored during discovery is ALSO honored when the real
    # Lighthouse load is generated (the --delay help text promises this).
    # abort_state: the out-parameter measure_pass sets on a mid-crawl session-loss
    # abort (AUTH-03). The (run_record, merged) contract is unchanged; this dict is
    # how the CLI learns the partial run aborted on a session loss → exit 3 (D-06).
    abort_state: dict = {}
    run_record, merged_artifacts = measure_pass(
        in_scope,
        errors,
        cfg=cfg,
        target=url,
        min_delay_s=gate.effective_delay,
        auth_state=resolved_auth_state,
        abort_state=abort_state,
    )
    session_lost = bool(abort_state.get("session_lost"))

    # D-15 MEASUREMENT_ERROR arm: a crawl where there were pages to measure but
    # every one collapsed to an error row is a measurement failure (exit 2), not
    # a clean partial. An all-error run with zero in-scope seeds (nothing to
    # measure) still surfaces as a measurement error so a silent zero-data crawl
    # is never reported as success (T-02-03-PARTIAL lifted to the crawl level).
    measured_count = sum(1 for p in run_record.pages if not is_error_row(p))

    # WR-03: decide the failure exit BEFORE any side effect. An all-failed crawl
    # must not (1) persist a zero-data run that later pollutes regression
    # self-joins, nor (2) let a subsequent write_outputs OSError downgrade the
    # determined MEASUREMENT_ERROR (exit 2) to USER_ERROR (exit 1). So we raise
    # exit 2 here, before write_outputs/persist run.
    if measured_count == 0:
        err_console.print(
            "[red]measurement failed:[/red] no page was measured "
            f"({len(run_record.pages)} discovered, all errored or none in scope)"
        )
        # WR-06 / IN-04: the always-on destructive-link denylist is a
        # case-insensitive SUBSTRING match (tokens like `admin`, `remove`,
        # `archive`, `disable`), with no `--allow` un-deny in v1. For the named
        # first-target class — owned Django sites whose seed is under `/admin/` —
        # the seed is silently denied and the crawl reports a generic "0
        # measured" exit 2 with no hint that the DENYLIST ate it. When the seed
        # itself is denied, name the denylist explicitly so the user isn't left
        # guessing between "all errored", "none in scope", and "denied".
        if is_denied(url, patterns=cfg.deny_patterns):
            err_console.print(
                "[yellow]hint:[/yellow] the seed URL was dropped by the "
                "destructive-link denylist (a case-insensitive substring match on "
                "tokens like 'admin'/'remove'/'archive'/'disable'). Narrow --deny "
                "or seed a non-denied path if this crawl was intentional."
            )
        raise typer.Exit(code=int(ExitCode.MEASUREMENT_ERROR))

    # D-07 / AUTH-04: redact credentials from EVERY artifact before it touches
    # disk. A Lighthouse HTML/JSON capture of an authenticated page (or the login
    # form itself) can embed a submitted password in a rendered field value; the
    # central scrubber strips username+password from both the reportJson and the
    # reportHtml of every page before write_outputs lands them on disk. The
    # RunRecord JSON write_outputs emits is scrubbed by scrubbing the model JSON
    # the same way (no credential survives into result.json either).
    scrubbed_artifacts = {
        key: (scrub(report_json), scrub(report_html))
        for key, (report_json, report_html) in merged_artifacts.items()
    }

    # --- Write outputs (OUT-03/OUT-04). OSError → USER_ERROR per D-15. ---
    # D-07 / AUTH-04 (CR-01, WR-02): thread the credential scrubber INTO
    # write_outputs so result.json AND result.csv are scrubbed at write time —
    # the first and only on-disk copy of each is already redacted. This closes
    # two holes at once: (CR-01) result.csv was previously written unscrubbed
    # from the raw RunRecord, leaking a URL-embedded credential into a persisted
    # artifact; (WR-02) the old post-write result.json re-scrub was a second,
    # NON-atomic write that briefly left an unscrubbed file on disk and could
    # truncate the file on a short write. Scrubbing inside the single atomic
    # write removes both the leak and the window, and means a future fourth
    # text output sink cannot silently miss the scrubber.
    try:
        run_dir = write_outputs(
            run_record,
            output_dir=output_dir,
            raw_artifacts=scrubbed_artifacts,
            scrub=scrub,
        )
    except OSError as e:
        err_console.print(scrub(f"[red]error:[/red] cannot write to {output_dir}: {e}"))
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
        # D-07: scrub stdout too — a piped `--json` capture is as much a sink as a
        # file. No credential reaches the terminal or a redirected JSON stream.
        sys.stdout.write(scrub(run_record.model_dump_json(indent=2)))
        sys.stdout.write("\n")
    else:
        _render_crawl_summary(run_record, samples=samples, run_dir=run_dir)

    # AUTH-03 / D-06: a mid-crawl session loss flushed the already-measured
    # authenticated pages as a valid tagged-partial run (written + persisted
    # above), but the crawl did NOT complete cleanly — exit AUTH_ERROR (3) so a
    # `case $? in 3) re-auth ;; esac` script can re-authenticate and resume. The
    # loud stderr report already fired inside measure_pass.
    if session_lost:
        raise typer.Exit(code=int(ExitCode.AUTH_ERROR))
    # Implicit exit 0 (success-or-partial per D-13).


@app.command()
def login(
    url: str = typer.Argument(
        ..., help="URL to open for an interactive login (e.g. the site's /login/ page)."
    ),
    out: Path = typer.Option(
        ...,
        "--out",
        help="Write the captured Playwright storage_state JSON here. This file is a "
        "FULL logged-in session — a credential-equivalent secret. Keep it gitignored "
        "(*.authstate.json is) and feed it to `crawl --auth-state <path>`.",
    ),
) -> None:
    """Open a HEADED browser at ``URL``, let the user log in by hand, capture the session.

    The SSO/MFA escape hatch (D-04): some logins cannot be scripted (OAuth popups,
    2FA prompts, CAPTCHAs). ``perfcrawl login`` launches a VISIBLE Chrome on the
    reused CDP-port seam, navigates to ``URL``, and waits for the user to finish
    logging in and press Enter. It then captures the DEFAULT context's
    ``storage_state`` and writes it to ``--out`` — the portable session currency
    (D-02) that ``crawl --auth-state`` replays.

    The captured file is a credential-equivalent secret (it IS the live session);
    ``*.authstate.json`` is gitignored so it is never committed (D-07).
    """
    from playwright.sync_api import sync_playwright

    from perfcrawl.auth import validate_storage_state

    # WR-03 / AUTH-04: unlike `crawl`, `login` has no --user/--pass to seed a
    # scrubber from — its one secret-bearing input is a URL that may carry
    # userinfo (`https://user:pass@site/login/`). The banner (success) and the
    # `except` message both interpolate the URL, so a credential-bearing URL
    # would echo live userinfo to stderr. Reject userinfo up front — a login URL
    # never legitimately needs HTTP basic-auth userinfo (the whole point is an
    # interactive form/SSO login), and refusing it removes the leak class
    # entirely rather than relying on after-the-fact redaction. Defense in depth:
    # seed a scrubber from any userinfo we did see so even the rejection/diagnostic
    # paths below never print the raw secret.
    parsed_url = urlsplit(url)
    scrub = make_scrubber(parsed_url.username, parsed_url.password)
    if parsed_url.username or parsed_url.password:
        err_console.print(
            "[red]error:[/red] the login URL carries embedded credentials "
            "(user:pass@host). Pass a plain login URL and log in interactively in "
            "the opened browser instead — userinfo in the URL is a credential leak risk."
        )
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # Headed launch — the ONLY difference from the audit launch (spike req #3).
    chrome, port, user_data_dir = _launch_chrome_with_cdp_port(headless=False)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")
            # DEFAULT context (contexts[0]) — the same context a later audit's
            # Lighthouse CDP target navigates in (D-02/D-03). Capturing here means
            # the saved session replays correctly under `crawl --auth-state`.
            ctx = browser.contexts[0]
            page = ctx.new_page()
            page.goto(url, wait_until="load")
            err_console.print(
                "[bold]Log in in the opened browser window, then press Enter here "
                "to capture the session...[/bold]"
            )
            # Block until the user finishes the interactive login. Stdin prompt on
            # stderr-adjacent flow (users never run commands; they act in the UI).
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                # No TTY / Ctrl-C: capture whatever session exists rather than crash.
                err_console.print("[yellow]no Enter received — capturing current session[/yellow]")
            state = ctx.storage_state()
            browser.close()  # disconnect only — the Popen'd Chrome stays alive
    except Exception as e:  # noqa: BLE001 — surface a clean message, never a traceback
        # WR-03: scrub the exception text — a Playwright error can echo back the
        # navigated URL (and any secret it carried) into its message.
        err_console.print(scrub(f"[red]error:[/red] could not capture a session: {e}"))
        raise typer.Exit(code=int(ExitCode.AUTH_ERROR)) from None
    finally:
        # Pitfall 5: kill + reap Chrome + rmtree the temp profile. For a headed
        # `uv run`-style re-exec, also process-group-kill so no orphaned Chrome
        # survives (spike requirement #4) — best-effort over the direct child.
        try:
            os.killpg(os.getpgid(chrome.pid), 15)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        _teardown_chrome(chrome, user_data_dir)

    # Validate before writing so an empty/failed login does not persist a useless
    # (and misleading) state file. AuthError -> exit 3.
    try:
        validate_storage_state(state)
    except AuthError as e:
        err_console.print(f"[red]auth failed:[/red] {e}")
        raise typer.Exit(code=int(ExitCode.AUTH_ERROR)) from None

    # WR-04: the captured session file IS a live logged-in session — a
    # credential-equivalent secret (the docstring and .gitignore both classify
    # it as such). `Path.write_text` would create it with the process umask
    # default (typically 0644, world-readable), so on a shared/multi-user host
    # every local user could read a live session. The gitignore only guards
    # against commit, not local filesystem disclosure. Create it owner-only
    # (0o600) via os.open so the perms are set atomically at creation, before
    # any byte of the session is written.
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # The 0o600 mode arg only applies when os.open CREATES the file; if `out`
    # already existed (an earlier capture with looser perms), O_CREAT leaves its
    # mode untouched. Force it owner-only on the open fd so a re-captured
    # session is never left world-readable.
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(state, indent=2))
    # WR-03: scrub the banner. The URL is rejected up front if it carried
    # userinfo, so `out` is the only interpolated value here — but route it
    # through the scrubber too as belt-and-suspenders against any secret that
    # could have reached the output path.
    err_console.print(
        scrub(
            f"[green]session captured[/green] → {out}\n"
            "[yellow]This file is a credential-equivalent secret (gitignored). "
            "Feed it to `perfcrawl crawl <url> --auth-state " + str(out) + "`.[/yellow]"
        )
    )
