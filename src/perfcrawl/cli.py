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

import gspread
import httpx
import typer
from rich.console import Console
from rich.table import Table

from perfcrawl import analysis, regression, sheets
from perfcrawl.auth import AuthError, make_scrubber, resolve_auth_state
from perfcrawl.constants import (
    AI_DEGRADED_WARN_FRACTION,
    ANTHROPIC_API_KEY_ENV,
    CRAWLER_USER_AGENT,
    DEFAULT_CONCURRENCY,
    DEFAULT_CRAWL_SAMPLES_N,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_PAGES,
    DEFAULT_MIN_DELAY_S,
    DEFAULT_SAMPLES_N,
    INP_PROXY_DISPLAY_LABEL,
    METRIC_BAND,
    OPENAI_API_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
    PERFCRAWL_PASSWORD_ENV,
    PERFCRAWL_SHEETS_SA_ENV,
    PERFCRAWL_USERNAME_ENV,
    PROVIDERS,
    ExitCode,
)
from perfcrawl.crawl import is_error_row
from perfcrawl.crawl.config import CrawlConfig
from perfcrawl.crawl.discovery import discover
from perfcrawl.crawl.measure_pass import measure_pass
from perfcrawl.crawl.robots import fetch_robots_gate
from perfcrawl.crawl.scope import is_denied
from perfcrawl.delta import compute_deltas
from perfcrawl.models import DirectionStatus
from perfcrawl.orchestrator import (
    MeasurementError,
    UserError,
    _launch_chrome_with_cdp_port,
    measure_url,
)
from perfcrawl.output import write_outputs
from perfcrawl.provider import build_provider, resolve_provider
from perfcrawl.regression import BandResult
from perfcrawl.store import init_db, read_previous_run, write_run


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


def _run_ai_post_pass(
    run_record,
    *,
    ai_provider: str | None,
    ai_model: str | None,
    ai_base_url: str | None = None,
    scrub,
) -> dict:
    """Build the RESOLVED provider and run the ``analyze_run`` post-pass (D-01/D-02).

    D-02 / D-10: invoked only when ``--ai`` is set AND ``resolve_provider`` already
    validated the resolved provider's key at t=0, so re-resolving here is a cheap,
    deterministic repeat (no Chrome cost) that yields the same ``provider_name``.
    ``build_provider`` constructs the concrete SDK client (Anthropic OR OpenAI) with
    the single-source retry budget + timeout — one thread-safe client shared across
    the bounded analyze pool (AI-SPEC Pitfall 4); the SDK owns retry (``max_retries``)
    and per-request timeout — never a hand-rolled loop (D-11). When ``ai_model`` is
    omitted (None) the RESOLVED provider's ``default_model`` governs (each provider's
    ``PROVIDERS[name]["default_model"]`` — no model id is inlined here); an explicit
    ``--ai-model`` wins. The key-seeded ``scrub`` is threaded in so every
    degrade/grounding stderr line and the in-place-mutated ``analysis`` fields are
    redacted (AUTH-04 / T-05-redact / CR-01). ``analyze_run`` mutates
    ``run_record.pages`` in place, so the unchanged scrub→write_outputs→write_run path
    serializes the populated ``analysis`` for free. Returns ``analyze_run``'s per-run
    summary (``{"analyzed", "degraded", "insufficient", "violations"}``) so the caller
    can surface AI health in the user-facing result (AI-SPEC §7 Key Metric 3) rather
    than only in the stderr log.
    """
    provider_name = resolve_provider(ai_provider, os.environ)
    cfg = PROVIDERS[provider_name]
    provider = build_provider(
        provider_name, os.environ[cfg["key_env"]], base_url=ai_base_url
    )
    return analysis.analyze_run(
        run_record,
        provider=provider,
        model=ai_model or cfg["default_model"],
        scrub=scrub,
        err_console=err_console,
    )


def _scrub_judge_sink(text: str, scrub) -> str:
    """Route one judge-lane sink (prompt, stderr log, calibration report) through scrub.

    The judge lane spends real tokens with ``ANTHROPIC_API_KEY``, so EVERY string it
    emits is an AUTH-04 sink (T-05.1-10 / CR-01 "scrub every sink"). This reuses the
    same key-seeded ``make_scrubber`` closure the analyze lane already uses (never a
    second hand-rolled redactor); a ``None`` scrubber is identity so non-AI callers
    are untouched.
    """
    return scrub(text) if scrub else text


def _format_calibration_note(calibration: dict | None, scrub=None) -> str | None:
    """Render the scrubbed judge-calibration note for the surfaced summary (D-03).

    ``calibration`` maps each judged dimension (6-9) to its ``calibrate`` result —
    ``{spearman, kappa, trusted}``. Returns a single note line citing each dim's
    ``spearman``/``kappa``/``trusted`` by name (so the surfacing is a real payload
    reference, not a stub), routed through the judge-lane scrubber (the calibration
    report is a key-bearing sink, T-05.1-10). Returns ``None`` when no payload is
    present, so a normal ``--ai`` run (which does not run the paid judge) is
    untouched — the runtime judge-threading seam is intentionally NOT built (CONTEXT
    Claude's-Discretion; this stays the calibration-report surface, D-03).
    """
    if not calibration:
        return None
    parts = []
    for dim, result in calibration.items():
        spearman = result.get("spearman")
        kappa = result.get("kappa")
        trusted = result.get("trusted")
        spearman_s = "n/a" if spearman is None else f"{spearman:.2f}"
        kappa_s = "n/a" if kappa is None else f"{kappa:.2f}"
        parts.append(f"{dim} spearman={spearman_s} kappa={kappa_s} trusted={trusted}")
    return _scrub_judge_sink("Judge calibration: " + " · ".join(parts), scrub)


def _render_ai_health(summary: dict | None, *, calibration: dict | None = None, scrub=None) -> None:
    """Surface the AI post-pass health in the user-facing result (AI-SPEC §7 KM-3/KM-6).

    ``analyze_run`` already logs a per-run line to stderr, but that never reached
    the stdout summary the user actually reads — so a systemic AI failure (bad key
    tier, model outage) or a grounding-violation spike was invisible in the result.
    This lifts the same counts under the result table. Warns (yellow ⚠) when more
    than ``AI_DEGRADED_WARN_FRACTION`` of the attempted pages degraded to null OR
    any grounding violation fired; otherwise a neutral dim note. No-ops when there
    is no summary (``--ai`` was not set), so non-AI runs are untouched. Never called
    in ``--json`` mode (it would corrupt the JSON stream).

    When a ``calibration`` payload is threaded in (the build-and-run-once judge
    meta-eval, D-03), its per-dim ``{spearman, kappa, trusted}`` is surfaced in the
    SAME block following the existing warn-vs-dim convention: yellow ⚠ when ANY
    judged dim is below the 0.70 trust bar (advisory-until-calibrated), a neutral
    dim note when every dim is ``trusted``. The note is scrubbed (the calibration
    report is a judge-lane key sink). This builds on the 20e50de surfaced summary —
    it does NOT re-architect it (D-05).
    """
    if summary:
        analyzed = summary.get("analyzed", 0)
        degraded = summary.get("degraded", 0)
        insufficient = summary.get("insufficient", 0)
        total_violations = sum((summary.get("violations") or {}).values())
        attempted = analyzed + degraded
        degraded_fraction = (degraded / attempted) if attempted else 0.0
        note = (
            f"AI: {analyzed} analyzed · {degraded} degraded · "
            f"{insufficient} insufficient · {total_violations} grounding flags"
        )
        if degraded_fraction > AI_DEGRADED_WARN_FRACTION or total_violations > 0:
            out_console.print(f"[yellow]⚠ {note}[/yellow]")
        else:
            out_console.print(f"[dim]{note}[/dim]")

    calibration_note = _format_calibration_note(calibration, scrub=scrub)
    if calibration_note is not None:
        # Advisory-until-calibrated: ANY untrusted judged dim warns; all-trusted is dim.
        any_untrusted = any(not result.get("trusted") for result in calibration.values())
        if any_untrusted:
            out_console.print(f"[yellow]⚠ {calibration_note}[/yellow]")
        else:
            out_console.print(f"[dim]{calibration_note}[/dim]")


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


# --- OUT-01 / D-05/D-06/D-07: the --output token contract ----------------------
# The closed set of output formats --output accepts (a comma-list). ``csv``/``json``/
# ``artifacts`` are on-disk sinks owned by write_outputs (output.py); ``sheets`` is a
# network sink the CLI routes to sheets.write_sheets. The default INCLUDES artifacts
# so the raw Lighthouse JSON+HTML the existing workflow archives still land by default
# (matches the pre-Phase-6 on-disk layout — test_on_disk_layout / the crawl
# artifacts test); ``sheets`` is opt-in only (D-06: a default run never authenticates
# or writes to Google Sheets).
_VALID_OUTPUT_TOKENS: frozenset[str] = frozenset({"csv", "json", "sheets", "artifacts"})
_FILE_OUTPUT_TOKENS: frozenset[str] = frozenset({"csv", "json", "artifacts"})
_DEFAULT_OUTPUT: str = "csv,json,artifacts"

# Cap on the regression-summary offenders list (D-13: counts + top-N by magnitude).
_REGRESSION_TOP_N: int = 10


def _emit_err(message: str, scrub) -> None:
    """Print one error/warning line on stderr, scrubbed when a scrubber is seeded."""
    err_console.print(scrub(message) if scrub else message)


def _resolve_output_tokens(output: str, sheets_id: str | None) -> set[str]:
    """Parse + validate ``--output`` at t=0, BEFORE any measurement (D-05/D-06/D-07).

    Splits the comma-list into a token set and validates it against the closed
    allowlist (T-06-12). An unknown token fails fast with ``USER_ERROR`` before a
    single page is measured. When ``sheets`` is selected (and ONLY then — D-06) the
    Sheets prerequisites are also checked at t=0: a non-empty ``--sheets-id`` AND the
    ``PERFCRAWL_SHEETS_SA`` env var set (the service-account JSON PATH; env-only,
    never a flag — T-06-14). The file is NOT opened/validated here — that happens in
    ``sheets.write_sheets`` — so a default run never touches the network and a
    sheets run fails fast on a missing credential rather than after a full crawl.
    """
    tokens = {t.strip() for t in output.split(",") if t.strip()}
    unknown = sorted(tokens - _VALID_OUTPUT_TOKENS)
    if unknown:
        err_console.print(
            f"[red]error:[/red] unknown --output token(s): {', '.join(unknown)} "
            "(valid: artifacts, csv, json, sheets)"
        )
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    if "sheets" in tokens:
        # A key in a gitignored .env should count for the SA path too.
        _load_dotenv_if_present()
        if not (sheets_id or "").strip():
            err_console.print(
                "[red]error:[/red] --output sheets requires --sheets-id "
                "(a Google Sheet key or full URL)"
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
        if not (os.environ.get(PERFCRAWL_SHEETS_SA_ENV) or "").strip():
            err_console.print(
                f"[red]error:[/red] --output sheets requires the {PERFCRAWL_SHEETS_SA_ENV} "
                "env var (path to the service-account JSON); it is unset. The path is "
                "env-only, never a flag."
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    return tokens


def _compute_regression(conn, run_record) -> tuple[list[BandResult], bool]:
    """Compare the just-stored run to its previous same-target run (HIST-02 / D-04).

    Returns ``(band_results, had_baseline)``. ``read_previous_run`` is passed the
    CURRENT run's ``started_at`` so the run can never be its own baseline (Pitfall 4);
    on the first-ever run for a target it returns ``None`` → ``([], False)`` (a first
    run never flags — D-04). Otherwise the raw ``compute_deltas`` go through the
    HIST-02 hybrid noise band (``flag_run``) and the resulting ``BandResult`` list is
    returned for BOTH the Rich summary (D-13) and the Sheets delta columns (D-11).
    This NEVER raises typer.Exit on a flag — flags are informational only (D-14).
    """
    previous = read_previous_run(
        conn, run_record.target, run_record.started_at.isoformat()
    )
    if previous is None:
        return [], False
    return regression.flag_run(compute_deltas(run_record, previous)), True


def _band_multiple(br: BandResult) -> float:
    """Normalized offender magnitude: how many abs-floors the delta cleared (WR-02).

    Cross-metric-comparable (a 0.05 CLS move and a 500ms LCP move become rankable
    on the same scale) so small-unit regressions aren't truncated out of the top-N.
    Ranking on raw ``abs(delta_abs)`` mixes units (bytes vs ms vs CLS vs score
    points), so a large-unit metric always outranks a genuine small-unit regression.
    """
    abs_floor = METRIC_BAND.get(br.metric, (0.0, None))[0]
    delta = abs(br.delta.delta_abs or 0.0)
    return delta / abs_floor if abs_floor else delta


def _render_regression_summary(
    band_results: list[BandResult], *, had_baseline: bool, target: str
) -> None:
    """Surface band-flagged regressions/improvements in a Rich summary (D-13/D-14).

    INFORMATIONAL ONLY: this never raises typer.Exit (the exit code is independent of
    any flag — D-14). On a first run (no baseline) it prints a neutral note, NOT an
    error (D-04). Otherwise it shows counts of flagged regressions (▲ worse) vs
    improvements (▼ better) and the top-N offenders by NORMALIZED band-multiple
    magnitude (``abs(delta_abs) / abs_floor`` — how many noise-bands each delta
    cleared, comparable across units). Only FLAGGED BandResults appear — within-band
    movement is not surfaced.
    """
    origin = _origin_of(target)
    if not had_baseline:
        out_console.print(
            f"[dim]No prior run for {origin} to compare — "
            "regression flagging starts on the next run.[/dim]"
        )
        return

    flagged = [br for br in band_results if br.flagged]
    if not flagged:
        out_console.print(
            f"[dim]No band-flagged regressions vs the previous run of {origin}.[/dim]"
        )
        return

    regressions = sum(1 for br in flagged if br.direction is DirectionStatus.REGRESSION)
    improvements = sum(1 for br in flagged if br.direction is DirectionStatus.IMPROVEMENT)

    table = Table(title="perfcrawl: regression band (vs previous run)")
    table.add_column("Page", style="bold", overflow="fold")
    table.add_column("Metric")
    table.add_column("Δ", justify="right")
    table.add_column("", justify="center")

    top = sorted(flagged, key=_band_multiple, reverse=True)
    for br in top[:_REGRESSION_TOP_N]:
        if br.direction is DirectionStatus.REGRESSION:
            arrow = "[red]▲ worse[/red]"
        else:
            arrow = "[green]▼ better[/green]"
        delta_abs = br.delta.delta_abs
        delta_s = "-" if delta_abs is None else f"{delta_abs:+.3g}"
        table.add_row(_relativize_url(br.delta.url_key, origin), br.metric, delta_s, arrow)

    table.caption = f"{regressions} regression(s) · {improvements} improvement(s) flagged"
    out_console.print(table)


def _write_sheets_sink(
    run_record, *, sheets_id: str | None, band_results: list[BandResult], scrub
) -> None:
    """Route the run to the scrubbed, env-credentialed Sheets writer (D-10/D-12).

    The service-account JSON PATH is read from ``PERFCRAWL_SHEETS_SA`` ONLY (validated
    present at t=0) and is NEVER printed/serialized (T-06-14). The SAME key-seeded
    ``scrub`` closure used for the on-disk sinks is threaded in so a credential
    embedded in a page URL never reaches a Sheets cell (D-12 / CR-01). A gspread 403
    maps to a clear USER_ERROR naming the share-the-sheet remedy (RESEARCH Pitfall 6);
    any other gspread failure surfaces scrubbed without leaking the SA path.
    """
    try:
        sheets.write_sheets(
            run_record,
            sheets_id=sheets_id,
            creds_path=os.environ[PERFCRAWL_SHEETS_SA_ENV],
            band_results=band_results,
            scrub=scrub,
        )
    except gspread.exceptions.APIError as e:
        text = str(e)
        if "403" in text or "PERMISSION_DENIED" in text:
            _emit_err(
                "[red]error:[/red] Google Sheets denied access (403). Share the "
                "target sheet with the service-account email (the client_email in "
                "your service-account JSON) as an Editor, then retry.",
                scrub,
            )
        else:
            _emit_err(f"[red]error:[/red] Google Sheets write failed: {e}", scrub)
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    except Exception as e:  # noqa: BLE001 — scrubbed message, never an SA-path traceback
        _emit_err(f"[red]error:[/red] Google Sheets write failed: {e}", scrub)
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None


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
    ai: bool = typer.Option(
        False,
        "--ai",
        help="Run AI analysis on the measured page (D-01). Requires an "
        "ANTHROPIC_API_KEY or OPENAI_API_KEY in the env (or .env) — the key is "
        "NEVER a flag (argv is visible in ps/history; D-02/D-10).",
    ),
    ai_provider: str | None = typer.Option(
        None,
        "--ai-provider",
        help="anthropic | openai | openrouter. Optional — omit to auto-detect "
        "from whichever key is in the env (Anthropic wins when both present, "
        "D-01; openrouter is opt-in via this flag, never auto-detected, D-03). "
        "The key is env-only, NEVER a flag (D-02).",
    ),
    ai_model: str | None = typer.Option(
        None,
        "--ai-model",
        help=(
            "Override the model for --ai analysis WITHIN the resolved provider "
            "(default: the provider's cost-appropriate bulk model — "
            f"{PROVIDERS['anthropic']['default_model']} or "
            f"{PROVIDERS['openai']['default_model']}; override with e.g. "
            f"{PROVIDERS['anthropic']['judge_model']})."
        ),
    ),
    ai_base_url: str | None = typer.Option(
        None,
        "--ai-base-url",
        help=(
            "Custom OpenAI-compatible endpoint base_url (escape hatch for LM Studio "
            "/ Together / Groq / self-hosted); optional — the openrouter provider "
            "bakes its own default. base_url is non-secret so a flag is fine (D-02)."
        ),
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
    output: str = typer.Option(
        _DEFAULT_OUTPUT,
        "--output",
        help="Comma-list of output formats: csv,json,artifacts,sheets (default: "
        f"{_DEFAULT_OUTPUT}). 'sheets' requires --sheets-id + the "
        f"{PERFCRAWL_SHEETS_SA_ENV} env var and is opt-in only (a default run never "
        "writes to Google Sheets; D-06). An unknown token fails fast (D-07).",
    ),
    sheets_id: str | None = typer.Option(
        None,
        "--sheets-id",
        help="Target Google Sheet — a bare key or a full URL (D-10). Required when "
        "'sheets' is in --output. Non-secret, so a flag is fine.",
    ),
) -> None:
    """Measure ``URL`` end-to-end: Chrome → LH → outputs → SQLite → stdout."""
    # --- OUT-01 / D-05/D-06/D-07: parse + validate --output at t=0, BEFORE any
    # measurement. An unknown token (or a sheets selection missing its --sheets-id /
    # SA env) fails fast here so a bad invocation never costs a Chrome launch.
    tokens = _resolve_output_tokens(output, sheets_id)
    # --- D-01 / D-02 / D-10: --ai resolves a provider from the env ONLY (the key is
    # never a flag — argv is visible in ps/history). resolve_provider implements the
    # D-01 order (explicit --ai-provider wins → Anthropic-wins tie-break → OpenAI →
    # fail) and the D-02 env-only fail-fast (UserError when the resolved provider's
    # key is absent). Run it at t=0 — BEFORE measure_url launches Chrome — so a bad
    # invocation never costs a measurement. Read AFTER the .env load so a key in a
    # gitignored .env counts (mirrors crawl()).
    if ai:
        _load_dotenv_if_present()
        try:
            resolved_provider = resolve_provider(ai_provider, os.environ)
        except UserError as e:
            err_console.print(f"[red]error:[/red] {e}")
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
        # WR-01: the anthropic adapter ignores base_url entirely — silently
        # honoring --ai-base-url on anthropic would send traffic to
        # api.anthropic.com while the user believes they're routing through a
        # gateway. Fail fast instead of dropping the flag.
        if ai_base_url and resolved_provider == "anthropic":
            err_console.print(
                "[red]error:[/red] --ai-base-url is not supported with the "
                "anthropic provider; use --ai-provider openai or openrouter "
                "for a custom OpenAI-compatible endpoint."
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
        # D-05: a custom --ai-base-url on the GENERIC openai provider without an
        # explicit --ai-model would silently aim the OpenAI-specific default slug at
        # a gateway that rejects it — degrading EVERY page. Fail fast at t=0 instead.
        # The named openrouter provider is exempt: it ships a valid default slug.
        if ai_base_url and resolved_provider == "openai" and ai_model is None:
            err_console.print(
                "[red]error:[/red] a custom --ai-base-url needs an explicit "
                f"--ai-model; the default {PROVIDERS['openai']['default_model']} "
                "is OpenAI-specific."
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # AUTH-04 / RESEARCH Pitfall 5 / CR-01: measure() had NO scrubber. When --ai is
    # set the provider key(s) become secrets that can leak into the measure output
    # path (result.json, --json stdout, the analysis fields), so seed the scrubber
    # from BOTH present provider keys (the factory filters None — auth.py:80 — so an
    # absent second key is a no-op; never write a second redactor) and thread it into
    # write_outputs. A non-AI measure run keeps the prior behavior (scrub=None →
    # identity in write_outputs).
    # D-12 / CR-01 (scrub-every-sink): seed the VALUE scrubber whenever a
    # credential-bearing sink is in play — an --ai run (provider keys) OR a --output
    # sheets run. The factory filters falsy secrets (auth.py:80) so an absent key is a
    # no-op; a plain non-AI, non-sheets run keeps scrub=None → identity. NOTE the
    # value scrubber only redacts the exact CONFIGURED secret strings — for a
    # credential embedded directly in a page URL (https://user:SECRET@host/) on the
    # no-secret path it is identity. That gap is covered UNCONDITIONALLY at the output
    # boundary by output.redact_url_userinfo (WR-01), which strips scheme://user:pass@
    # userinfo from every URL written to result.csv / result.json / a Sheets cell
    # regardless of what secrets were seeded here.
    scrub = (
        make_scrubber(
            os.environ.get(ANTHROPIC_API_KEY_ENV),
            os.environ.get(OPENAI_API_KEY_ENV),
            os.environ.get(OPENROUTER_API_KEY_ENV),
        )
        if (ai or "sheets" in tokens)
        else None
    )

    # --- D-15 USER_ERROR arm (input validation, before any subprocess) ---
    try:
        run_record, raw_artifacts = measure_url(url=url, samples=samples, emulation=emulation)
    except UserError as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
    except MeasurementError as e:
        err_console.print(f"[red]measurement failed:[/red] {e}")
        raise typer.Exit(code=int(ExitCode.MEASUREMENT_ERROR)) from None

    # --- D-02: AI analysis post-pass (only when --ai), AFTER measurement and BEFORE
    # output. analyze_run mutates run_record.pages in place so the write path below
    # serializes page.analysis for free; the key-seeded scrub redacts every sink.
    ai_summary = None
    if ai:
        ai_summary = _run_ai_post_pass(
            run_record,
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_base_url=ai_base_url,
            scrub=scrub,
        )

    # --- Write outputs (OUT-03 / OUT-04). OSError → USER_ERROR per D-15. ---
    # OUT-01: gate the on-disk writers on the selected file tokens (sheets is a
    # network sink, handled separately below).
    try:
        run_dir = write_outputs(
            run_record,
            output_dir=output_dir,
            raw_artifacts=raw_artifacts,
            scrub=scrub,
            formats=tokens & _FILE_OUTPUT_TOKENS,
        )
    except OSError as e:
        err_console.print(f"[red]error:[/red] cannot write to {output_dir}: {e}")
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # --- Persist to SQLite (HIST-01) + compute the HIST-02 baseline comparison. ---
    db_path = output_dir / "perfcrawl.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)  # idempotent per Phase 1 contract
        try:
            write_run(conn, run_record)
        except sqlite3.IntegrityError as e:
            # A duplicate run id (re-persisting an already-stored run) must never
            # crash a run whose artifacts are already on disk — surface a warning
            # and keep the exit code unchanged. The current run is then absent from
            # the store, so the baseline lookup below simply finds no NEW baseline.
            _emit_err(f"[yellow]warning:[/yellow] run not persisted: {e}", scrub)
        band_results, had_baseline = _compute_regression(conn, run_record)
    finally:
        conn.close()

    # --- OUT-02 / D-10: route to Google Sheets when 'sheets' selected (after the
    # AI post-pass so page.analysis is populated; band columns from the comparison). ---
    if "sheets" in tokens:
        _write_sheets_sink(
            run_record, sheets_id=sheets_id, band_results=band_results, scrub=scrub
        )

    # --- Render the final result on stdout (D-06). ---
    if output_json:
        # Plain sys.stdout write — Rich would inject ANSI even with no styling.
        # WR-04: scrub --json stdout too (mirror crawl()). A piped `--json`
        # capture is as much a sink as a file; when --ai seeded a scrubber, redact
        # before the JSON reaches the terminal / a redirected stream. Non-AI runs
        # keep scrub=None → identity.
        payload = run_record.model_dump_json(indent=2)
        sys.stdout.write(scrub(payload) if scrub else payload)
        sys.stdout.write("\n")
    else:
        _render_human_table(run_record, samples=samples, run_dir=run_dir)
        # scrub threaded so any judge-lane calibration note is key-redacted (AUTH-04).
        _render_ai_health(ai_summary, scrub=scrub)
        # HIST-02 / D-13: surface band-flagged regressions — informational only (D-14).
        _render_regression_summary(
            band_results, had_baseline=had_baseline, target=run_record.target
        )
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
    ai: bool = typer.Option(
        False,
        "--ai",
        help="Run per-page AI analysis after measurement (D-01). Requires an "
        "ANTHROPIC_API_KEY or OPENAI_API_KEY in the env (or .env) — the key is "
        "NEVER a flag (argv is visible in ps/history; D-02/D-10).",
    ),
    ai_provider: str | None = typer.Option(
        None,
        "--ai-provider",
        help="anthropic | openai | openrouter. Optional — omit to auto-detect "
        "from whichever key is in the env (Anthropic wins when both present, "
        "D-01; openrouter is opt-in via this flag, never auto-detected, D-03). "
        "The key is env-only, NEVER a flag (D-02).",
    ),
    ai_model: str | None = typer.Option(
        None,
        "--ai-model",
        help=(
            "Override the model for --ai analysis WITHIN the resolved provider "
            "(default: the provider's cost-appropriate bulk model — "
            f"{PROVIDERS['anthropic']['default_model']} or "
            f"{PROVIDERS['openai']['default_model']}; override with e.g. "
            f"{PROVIDERS['anthropic']['judge_model']} for small high-value crawls)."
        ),
    ),
    ai_base_url: str | None = typer.Option(
        None,
        "--ai-base-url",
        help=(
            "Custom OpenAI-compatible endpoint base_url (escape hatch for LM Studio "
            "/ Together / Groq / self-hosted); optional — the openrouter provider "
            "bakes its own default. base_url is non-secret so a flag is fine (D-02)."
        ),
    ),
    output_dir: Path = typer.Option(
        Path("output"),
        "--output-dir",
        help="Per-run artifacts land under <output_dir>/<run_id>/ (D-07).",
    ),
    output: str = typer.Option(
        _DEFAULT_OUTPUT,
        "--output",
        help="Comma-list of output formats: csv,json,artifacts,sheets (default: "
        f"{_DEFAULT_OUTPUT}). 'sheets' requires --sheets-id + the "
        f"{PERFCRAWL_SHEETS_SA_ENV} env var and is opt-in only (a default run never "
        "writes to Google Sheets; D-06). An unknown token fails fast (D-07).",
    ),
    sheets_id: str | None = typer.Option(
        None,
        "--sheets-id",
        help="Target Google Sheet — a bare key or a full URL (D-10). Required when "
        "'sheets' is in --output. Non-secret, so a flag is fine.",
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

    # OUT-01 / D-05/D-06/D-07: parse + validate --output at t=0, BEFORE discovery or
    # any measurement. An unknown token (or a sheets selection missing its --sheets-id
    # / SA env) fails fast here so a bad invocation never produces a silent no-op crawl.
    tokens = _resolve_output_tokens(output, sheets_id)

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

    # D-01 / D-02 / D-10: --ai resolves a provider from the env ONLY (the key is never
    # a flag — argv is visible in ps/history). resolve_provider implements the D-01
    # order (explicit --ai-provider wins → Anthropic-wins tie-break → OpenAI → fail)
    # and the D-02 env-only fail-fast (UserError when the resolved provider's key is
    # absent). Fail fast at t=0 (USER_ERROR, reusing the "bad flags" band), BEFORE
    # discovery or any measurement, so a bad invocation never produces a silent no-op
    # crawl. Read AFTER the .env load above so a key in a gitignored .env counts.
    if ai:
        try:
            resolved_provider = resolve_provider(ai_provider, os.environ)
        except UserError as e:
            err_console.print(f"[red]error:[/red] {e}")
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
        # WR-01: the anthropic adapter ignores base_url entirely — silently
        # honoring --ai-base-url on anthropic would send traffic to
        # api.anthropic.com while the user believes they're routing through a
        # gateway. Fail fast instead of dropping the flag.
        if ai_base_url and resolved_provider == "anthropic":
            err_console.print(
                "[red]error:[/red] --ai-base-url is not supported with the "
                "anthropic provider; use --ai-provider openai or openrouter "
                "for a custom OpenAI-compatible endpoint."
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None
        # D-05: a custom --ai-base-url on the GENERIC openai provider without an
        # explicit --ai-model would silently aim the OpenAI-specific default slug at
        # a gateway that rejects it — degrading EVERY page. Fail fast at t=0 instead.
        # The named openrouter provider is exempt: it ships a valid default slug.
        if ai_base_url and resolved_provider == "openai" and ai_model is None:
            err_console.print(
                "[red]error:[/red] a custom --ai-base-url needs an explicit "
                f"--ai-model; the default {PROVIDERS['openai']['default_model']} "
                "is OpenAI-specific."
            )
            raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # Seed the central credential scrubber ONCE (D-07 / AUTH-04 / CR-01). Applied to
    # every sink (stderr, RunRecord JSON, result.csv, --json stdout, LH artifacts, AND
    # the AI analysis fields) so no credential ever lands in a log or on disk. BOTH
    # provider keys are seeded (T-05-redact / CR-01) so either key is redacted at
    # every sink; the factory filters None (auth.py:80) so a non-AI run or an absent
    # second key is a no-op. Never write a second redactor.
    scrub = make_scrubber(
        username,
        password,
        os.environ.get(ANTHROPIC_API_KEY_ENV),
        os.environ.get(OPENAI_API_KEY_ENV),
        os.environ.get(OPENROUTER_API_KEY_ENV),
    )

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

    # --- D-02: AI analysis post-pass (only when --ai; runs AFTER measurement and
    # BEFORE any output is scrubbed/written). analyze_run mutates run_record.pages
    # in place, filling page.analysis, so the unchanged scrub→write_outputs→
    # write_run path below serializes the populated analysis for free (output.py /
    # store.py / models.py UNCHANGED — Phase 6 owns output formats). The key is
    # threaded through `scrub` so every analysis field + degrade/grounding log line
    # is redacted (AUTH-04 / T-05-redact).
    ai_summary = None
    if ai:
        ai_summary = _run_ai_post_pass(
            run_record,
            ai_provider=ai_provider,
            ai_model=ai_model,
            ai_base_url=ai_base_url,
            scrub=scrub,
        )

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
    # OUT-01: gate the on-disk writers on the selected file tokens (sheets handled below).
    try:
        run_dir = write_outputs(
            run_record,
            output_dir=output_dir,
            raw_artifacts=scrubbed_artifacts,
            scrub=scrub,
            formats=tokens & _FILE_OUTPUT_TOKENS,
        )
    except OSError as e:
        err_console.print(scrub(f"[red]error:[/red] cannot write to {output_dir}: {e}"))
        raise typer.Exit(code=int(ExitCode.USER_ERROR)) from None

    # --- Persist the one multi-page run to SQLite (HIST-01) + HIST-02 comparison. ---
    db_path = output_dir / "perfcrawl.db"
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        try:
            write_run(conn, run_record)
        except sqlite3.IntegrityError as e:
            # A duplicate run id must never crash a crawl whose artifacts are already
            # on disk — warn and keep the exit code unchanged (D-14 persist posture).
            _emit_err(f"[yellow]warning:[/yellow] run not persisted: {e}", scrub)
        band_results, had_baseline = _compute_regression(conn, run_record)
    finally:
        conn.close()

    # --- OUT-02 / D-10: route to Google Sheets when 'sheets' selected. ---
    if "sheets" in tokens:
        _write_sheets_sink(
            run_record, sheets_id=sheets_id, band_results=band_results, scrub=scrub
        )

    # --- Render the multi-page result on stdout (D-06). ---
    if output_json:
        # D-07: scrub stdout too — a piped `--json` capture is as much a sink as a
        # file. No credential reaches the terminal or a redirected JSON stream.
        sys.stdout.write(scrub(run_record.model_dump_json(indent=2)))
        sys.stdout.write("\n")
    else:
        _render_crawl_summary(run_record, samples=samples, run_dir=run_dir)
        # scrub threaded so any judge-lane calibration note is key-redacted (AUTH-04).
        _render_ai_health(ai_summary, scrub=scrub)
        # HIST-02 / D-13: surface band-flagged regressions — informational only (D-14).
        _render_regression_summary(
            band_results, had_baseline=had_baseline, target=run_record.target
        )

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

    from perfcrawl.auth import capture_storage_state, validate_storage_state

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
            state = capture_storage_state(ctx, page)
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
