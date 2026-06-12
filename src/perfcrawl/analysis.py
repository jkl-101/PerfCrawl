"""Phase-5 AI analysis — the public contract (Wave-0 stub).

This module is the per-page AI-enrichment seam: it turns each measured
``PageResult`` into a deterministic metric *digest*, dispatches one stateless
structured-output call per page through the provider-agnostic
``Provider.parse_structured(output_model=AnalysisResult)`` seam (D-04) via a
bounded pool, and writes the grounded ``AnalysisResult`` (or a clean ``None``)
back onto ``page.analysis`` — which the unchanged ``output.write_outputs`` /
``store.write_run`` path then serializes for free.

**This file is an interface-first CONTRACT STUB.** Every public name the test
harness imports exists here, but the request/response bodies raise
``NotImplementedError`` — Plan 02 (engine) fills them and turns the Wave-0
deterministic eval suite GREEN. The names + signatures are the binding contract
between this plan's RED tests and the Plan-02 implementation:

  - ``build_digest(page)``         — deterministic, sorted, timestamp-free digest text
  - ``RUBRIC``                     — the frozen ≥1,024-token cite-the-numbers system prefix
  - ``analyze_page(provider, ...)`` — one structured-output call; degrades to ``None`` (D-09)
  - ``analyze_run(run_record, ...)`` — bounded-pool driver + per-run summary counts (D-03/D-06/D-09)
  - ``check_no_bare_inp`` / ``find_fabricated_numbers`` / ``find_unsupported_entities``
        — the grounding PURE functions, run both in CI (eval) and at runtime (pre-write guardrails)

Reuse seams (do NOT hand-roll — RESEARCH "Don't Hand-Roll"):
  - ``crawl.is_error_row`` is the WR-01 single source of truth for the D-06
    null short-circuit — import it, never re-derive "is this page empty".
  - ``output.write_outputs`` already serializes a populated ``analysis`` and
    already threads ``scrub`` to every sink — analyze_run only mutates in place.

Layering: a library module must NOT import ``cli.py``'s console (that is a
layering cycle). This module owns its own stderr ``Console`` exactly like
``crawl/measure_pass.py`` — the CLI may still pass its own ``err_console`` into
``analyze_run`` so degraded-page log lines land on the shared stream.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from rich.console import Console

from perfcrawl.constants import (
    AI_MAX_TOKENS,
    AI_POOL_SIZE,
    AI_WATERFALL_TOP_N,
    DEFAULT_AI_MODEL,
)
from perfcrawl.crawl import is_error_row
from perfcrawl.models import AnalysisResult, PageResult
from perfcrawl.provider import Provider

if TYPE_CHECKING:  # pragma: no cover - typing-only import for annotations
    from perfcrawl.models import RunRecord, WaterfallEntry

# Module-owned stderr console (mirrors measure_pass.py:69-72). A library module
# never imports cli.py's console — that would be a layering cycle.
_err_console = Console(stderr=True)

# --- The frozen cite-the-numbers rubric (D-05 / D-15) ------------------------
# A byte-stable module constant: the STATIC system prefix sent on every call with
# ``cache_control: ephemeral`` so the prompt cache hits (AI-SPEC Pitfall 1/2). It
# MUST clear the ~1,024-token Sonnet-4.6 / Opus-4.8 cache minimum, so it is bulked
# with a metric glossary, the explicit 0-100-higher-is-better scale, the labeled-
# INP-proxy rule, the CWV bands, the "insufficient data over speculation" rule,
# and two worked examples. Never interpolate per-page data here — the variable
# digest goes in ``messages``, NEVER in this prefix (would drift the cache).
RUBRIC: str = """\
You are a web-performance analyst. You will be given a DIGEST of ONE web page's
measured performance metrics. Your job is to write three short, plain-language
fields about THAT page, grounded ONLY in the numbers present in the digest:

  - observation: what the numbers say about this page's performance.
  - potential_cause: the most likely mechanism, tied to a SPECIFIC metric/value
    that appears in the digest.
  - suggested_optimization: one concrete, evidence-backed thing to try.

ABSOLUTE GROUNDING RULES (these are the contract; violating them makes the note
worse than useless because it erodes trust in every other row):

1. CITE THE NUMBERS. Every quantitative claim MUST cite a metric that is present
   in the digest AND its exact value as shown. Never state a number that does not
   appear in the digest. If you want to mention a value, copy it from the digest
   VERBATIM — do NOT convert units (e.g. do not turn "2410000 bytes" into
   "2.4 MB"), do NOT round, and do NOT compute derived figures (percentages,
   ratios, sums). A converted or rounded number is a number that does NOT appear
   in the digest, and it will be flagged as fabricated. Quote bytes as the raw
   integer the digest shows, milliseconds as the integer shown, and scores as
   shown.

2. NEVER GUESS THE STACK. Do NOT name or assert any framework, library, server,
   CDN, third-party script, or specific render-blocking resource UNLESS that exact
   string already appears in the digest evidence (for example, as a waterfall
   request URL). The digest carries metric numbers and request URLs only — it
   never carries the page's HTML, its framework, or its server software. So you
   cannot know them. Do NOT say "React", "Vue", "Angular", "Django", "Rails",
   "nginx", "Apache", "your CDN", "Cloudflare", "WordPress", "jQuery", "webpack",
   "Google Tag Manager", or any other named technology unless the digest text
   literally contains that token. This is the single most important rule and the
   project's #1 failure mode: a plausible-but-fabricated cause ("React hydration
   is slow", "nginx is misconfigured") sends an engineer chasing a phantom.

3. PREFER "INSUFFICIENT DATA" OVER SPECULATION. If a metric needed to support a
   claim is shown as "n/a" (missing) in the digest, say the data is insufficient
   to determine that cause. An honest "insufficient data to identify the cause"
   is ALWAYS better than confidently-wrong prose. Do not fill a gap with a
   plausible-sounding guess.

4. NO BARE INP. The digest reports TBT (Total Blocking Time) as the LAB PROXY for
   INP. TBT is measured in a headless lab pass; it is NOT a real field INP value.
   Never write "INP is N ms" or otherwise assert a real field-INP number. If you
   refer to interactivity, say "TBT (the lab proxy for INP) is N ms" and make
   clear real field INP was not measured.

THE SCORE SCALE. The four category scores (Performance, Accessibility, SEO,
Best-practices) are on a 0-100 scale where HIGHER IS BETTER. A score of 98 is
excellent; a score near 100 is essentially perfect. Do NOT treat a score like 98
as "98 ms" or as a near-zero ratio, and do NOT invent a problem on a page whose
scores are all high. A score of 90 or above is good; 50-89 is mixed; below 50 is
poor. If every score is >= 90 and the Core Web Vitals are in the good band, the
correct observation is that the page is healthy — say so plainly.

METRIC GLOSSARY (what each digest line means):

  - URL: the page measured. CONTEXT ONLY. You may NOT infer the technology stack,
    framework, or server from the URL or from any request path. The URL is not
    evidence of a cause.
  - HTTP status: the page's response status code (200 is a normal success).
  - Performance / Accessibility / SEO / Best-practices score: Lighthouse category
    scores, 0-100, higher is better (see the score scale above).
  - LCP (ms): Largest Contentful Paint in milliseconds. Core Web Vital. Band:
    GOOD <= 2500 ms, needs-improvement 2500-4000 ms, POOR > 4000 ms. Lower is
    better. LCP is usually bound by the largest above-the-fold element (often the
    hero image or a large block of text) and by how long the server and render
    path take to deliver it.
  - CLS: Cumulative Layout Shift, a unitless score. Core Web Vital. Band: GOOD
    <= 0.1, needs-improvement 0.1-0.25, POOR > 0.25. Lower is better. CLS is
    caused by content that moves after it first paints (images/ads/fonts without
    reserved space).
  - TBT (ms, lab proxy for INP): Total Blocking Time in milliseconds, the LAB
    PROXY for INP (see rule 4). Lower is better; high TBT means long main-thread
    tasks (usually heavy JavaScript) blocking interactivity. NEVER call this INP.
  - TTFB (ms): Time To First Byte in milliseconds. Lower is better. High TTFB
    points at slow server response, slow upstream, or redirects — server-side,
    not front-end.
  - Request count: number of network requests the page made. More requests means
    more connection/parse overhead.
  - Total bytes: total transferred bytes for the page. Large total bytes (driven
    by big images, fonts, or JS/CSS bundles) slows load on constrained networks.
  - Slowest request: the single slowest network request URL and its time in ms —
    often the most actionable single fact on the page.
  - Top requests: the slowest requests by timing, each with its URL, resource
    type, transferred size in bytes, timing in ms, and status code.

HOW TO REASON ABOUT A CAUSE. Connect a NAMED, PRESENT metric to a plausible
mechanism using only the evidence. Good: "LCP is 4800 ms (poor); the slowest
request is 2410000 bytes at 1820 ms, which is the likely LCP element." That cites
real digest values VERBATIM (the raw byte and ms integers as shown — no "2.4 MB"
conversion) and names a real digest request. Bad: "LCP is slow because of React
hydration" — the digest never mentions React, so this is a fabricated cause
(rule 2). Bad: "the 2.4 MB hero image" — "2.4" is a derived/rounded figure that
does not appear in the digest, so it is a fabricated number (rule 1).

WORKED EXAMPLE A — healthy page (do not invent a problem):
  Digest says all four scores are 90+ (e.g. Performance 98), LCP 1200 ms (good),
  CLS 0.02 (good), TBT 90 ms (low). Correct observation: "This page is healthy —
  Performance score 98/100, LCP 1200 ms and CLS 0.02 are both in the good band,
  and TBT (the lab proxy for INP) is a low 90 ms." Correct cause: "No performance
  problem is evident from the captured metrics." Correct optimization: "No action
  needed; the page already meets the Core Web Vitals good thresholds."

WORKED EXAMPLE B — insufficient data (be honest about gaps):
  Digest shows LCP as "n/a", TTFB 220 ms, CLS 0.06, and the rest present. Correct
  observation: "TTFB is a healthy 220 ms and CLS 0.06 is in the good band, but LCP
  was not captured (n/a) for this page." Correct cause: "Insufficient data to
  determine the loading bottleneck — LCP, the key paint metric, is missing for
  this page." Correct optimization: "Re-measure to capture LCP before drawing a
  loading-performance conclusion."

Stay terse. Each field is one to three sentences. Cite the digest's own numbers.
When in doubt, say less and stay grounded.
"""


_NA = "n/a"


def _fmt_plain(v: int | None) -> str:
    """Render an int (or ``None`` → ``n/a``) deterministically."""
    return _NA if v is None else str(v)


def _fmt_int_val(v: float | None) -> str:
    """Render a float as fixed-precision integer ms (or ``n/a``)."""
    return _NA if v is None else str(int(round(v)))


def _fmt_median_ms(sample) -> str:
    """Render a ``MetricSample.median`` as integer ms (or ``n/a``)."""
    if sample is None or sample.median is None:
        return _NA
    return str(int(round(sample.median)))


def _fmt_cls(sample) -> str:
    """Render a CLS ``MetricSample.median`` at a fixed 3 dp (or ``n/a``)."""
    if sample is None or sample.median is None:
        return _NA
    return f"{sample.median:.3f}"


def _fmt_score(v: float | None) -> str:
    """Render a 0-100 category score with no trailing-zero drift (or ``n/a``)."""
    if v is None:
        return _NA
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _sorted_waterfall(entries: list[WaterfallEntry]) -> list[WaterfallEntry]:
    """Sort waterfall rows by ``timing_ms`` desc, tie-break ``url`` asc (deterministic)."""
    return sorted(entries, key=lambda e: (-(e.timing_ms or 0.0), e.url or ""))


def build_digest(page: PageResult) -> str:
    """Render ``page`` into the deterministic, sorted, timestamp-free digest text.

    Selected ``PageResult`` fields only — url, status, the four 0-100 category
    scores, LCP/CLS/TBT(labeled INP proxy)/TTFB medians, request count, total
    bytes, slowest request, and the top-``AI_WATERFALL_TOP_N`` slowest waterfall
    rows (sorted by timing desc / url asc). ``url_key``, ``diagnostics``, the full
    waterfall, and the per-metric ``samples`` are excluded. Nulls render as an
    explicit ``n/a``; floats round to fixed precision; NO ``datetime``/UUID/run-id
    — so the same page yields byte-identical text every call (and a reordered
    waterfall renders identically).
    """
    lines = [
        f"URL: {page.url}",
        f"HTTP status: {_fmt_plain(page.status_code)}",
        f"Performance score (0-100, higher is better): {_fmt_score(page.perf_score)}",
        f"Accessibility score (0-100, higher is better): {_fmt_score(page.a11y_score)}",
        f"SEO score (0-100, higher is better): {_fmt_score(page.seo_score)}",
        f"Best-practices score (0-100, higher is better): {_fmt_score(page.best_practices_score)}",
        f"LCP (ms): {_fmt_median_ms(page.lcp_ms)}",
        f"CLS: {_fmt_cls(page.cls)}",
        (
            "TBT (ms, lab proxy for INP — not real field INP): "
            f"{_fmt_median_ms(page.inp_proxy_tbt_ms)}"
        ),
        f"TTFB (ms): {_fmt_median_ms(page.ttfb_ms)}",
        f"Request count: {_fmt_plain(page.request_count)}",
        f"Total bytes: {_fmt_plain(page.total_bytes)}",
        (
            f"Slowest request: {page.slowest_request_url or _NA} "
            f"({_fmt_int_val(page.slowest_request_ms)} ms)"
        ),
        f"Top {AI_WATERFALL_TOP_N} requests (slowest first):",
    ]
    entries = _sorted_waterfall(page.waterfall)[:AI_WATERFALL_TOP_N]
    if not entries:
        lines.append(f"  {_NA}")
    else:
        for i, e in enumerate(entries, 1):
            lines.append(
                f"  {i}. {e.url or _NA} [{e.resource_type or _NA}] "
                f"{_fmt_plain(e.size_bytes)} bytes, {_fmt_int_val(e.timing_ms)} ms, "
                f"status {_fmt_plain(e.status_code)}"
            )
    return "\n".join(lines)


def analyze_page(provider: Provider, digest_text: str, model: str) -> AnalysisResult | None:
    """Run one structured-output call for ``digest_text``; degrade to ``None`` (D-09).

    Delegates to ``provider.parse_structured(system_text=RUBRIC,
    user_text=digest_text, output_model=AnalysisResult, model=model,
    max_tokens=AI_MAX_TOKENS)`` — the single seam (D-04) that both the Anthropic
    and OpenAI impls narrow to. The static RUBRIC is the ONLY thing in
    ``system_text`` (so the cache prefix stays byte-stable — Pitfall 1); the
    variable digest is the ``user_text``. The degrade-to-None disposition — a
    refusal / truncation / SDK error returning ``None`` so a single AI miss never
    crashes the run — now lives inside the provider impl (D-09/D-11), so there is
    no app-level try/except or retry here.
    """
    return provider.parse_structured(
        system_text=RUBRIC,
        user_text=digest_text,
        output_model=AnalysisResult,
        model=model,
        max_tokens=AI_MAX_TOKENS,
    )


def analyze_run(
    run_record: RunRecord,
    *,
    provider: Provider,
    model: str = DEFAULT_AI_MODEL,
    scrub=None,
    err_console: Console | None = None,
) -> dict:
    """Bounded-pool post-pass: fill ``page.analysis`` for every page; return a summary.

    Mirrors ``measure_pass`` (D-03/D-09). Per page: ``is_error_row(page)`` (the
    WR-01 single-source classifier, reused — never hand-rolled) →  short-circuit to
    ``analysis=None`` with NO API call (D-06), counted "insufficient"; else
    ``build_digest`` → ``analyze_page`` → assign the ``AnalysisResult | None`` back
    onto ``page.analysis`` (mutate ``run_record.pages`` in place so the unchanged
    scrub/write path serializes it). The first non-error page is analyzed
    SYNCHRONOUSLY to warm the prompt cache, then the rest fan out through a
    ``ThreadPoolExecutor(max_workers=AI_POOL_SIZE)`` sharing the one thread-safe
    ``provider`` (AI-SPEC Pitfall 4).

    AI-SPEC §6 ONLINE pre-write grounding guardrails (runtime, not CI-only): for
    every page that got a non-None ``AnalysisResult``, BEFORE it is final the three
    Task-2 pure functions run over the concatenated Observation/Cause/Optimization
    text against THAT page's digest. Disposition is FLAG + LOG + COUNT, analysis
    RETAINED: each hit increments the matching ``violations`` per-check count
    (AI-SPEC §7 Key Metric 3), one scrubbed stderr line is logged per flagged page,
    and the (cite-the-numbers-RUBRIC-primary-defended) analysis stays on the page
    as the monitoring signal — never silently nulled.

    Degrade (D-09): a ``None`` from ``analyze_page`` leaves ``analysis=None``,
    counts "degraded", logs one scrubbed stderr line — the run never fails. On
    ``KeyboardInterrupt`` the pool is shut down (``wait=False, cancel_futures=
    True``) and whatever analyses were filled are kept (partial-flush). Returns
    ``{"analyzed", "degraded", "insufficient", "violations": {...}}``.
    """
    _scrub = scrub if scrub is not None else (lambda t: t)
    console = err_console if err_console is not None else _err_console

    def _emit(msg: str) -> None:
        console.print(_scrub(msg))

    error_pages = [p for p in run_record.pages if is_error_row(p)]
    data_pages = [p for p in run_record.pages if not is_error_row(p)]

    # D-06: error rows short-circuit to analysis=None with NO API call.
    for page in error_pages:
        page.analysis = None
    insufficient = len(error_pages)

    def _process(page: PageResult) -> tuple[PageResult, str, AnalysisResult | None]:
        # WR-01 / T-05.2-04: scrub the digest BEFORE it egresses to ANY provider
        # (Anthropic or OpenAI). A credential embedded in a measured request URL
        # (slowest_request_url / waterfall entry.url) is a network sink just like
        # result.json / --json stdout; the scrubber must redact it before the API
        # call. The scrub runs here, ahead of provider.parse_structured, so the
        # security invariant holds for every provider sink unchanged. Scrubbing
        # here also keeps the grounding pass (which compares analysis text against
        # `digest`) consistent with what the model actually saw.
        digest = _scrub(build_digest(page))
        return page, digest, analyze_page(provider, digest, model)

    # Pitfall 4: warm the cache on the FIRST page synchronously, then fan out.
    processed: list[tuple[PageResult, str, AnalysisResult | None]] = []
    try:
        if data_pages:
            processed.append(_process(data_pages[0]))
            rest = data_pages[1:]
            if rest:
                executor = ThreadPoolExecutor(max_workers=max(1, AI_POOL_SIZE))
                try:
                    for result in executor.map(_process, rest):
                        processed.append(result)
                finally:
                    # Partial-flush: cancel still-queued futures and return promptly.
                    executor.shutdown(wait=False, cancel_futures=True)
    except KeyboardInterrupt:
        # Keep whatever completed; never lose measured work (mirror measure_pass).
        pass
    except Exception:
        # WR-05: the docstring promises "the run never fails". analyze_page already
        # swallows its own errors, but build_digest / the executor.map re-raise are
        # outside that guard. A non-KI failure here must degrade to "no analysis"
        # for the remaining pages rather than propagate and discard the entire
        # measured-and-paid-for run (the --ai post-pass runs BEFORE write_outputs).
        _emit("AI analysis post-pass failed; continuing with no analysis")

    analyzed = 0
    # WR-02: if executor.map aborted mid-stream (a non-KeyboardInterrupt raise from
    # build_digest or a future), pages past the failure never reached `processed`.
    # Their analysis stays None (the default), but they MUST be counted as degraded so
    # the AI-health summary always sums to len(data_pages) — otherwise a partial
    # post-pass failure is misreported as healthier than it was (the degraded-fraction
    # warning keys off attempted = analyzed + degraded, which would undercount).
    dropped = len(data_pages) - len(processed)
    degraded = dropped
    if dropped:
        _emit(f"AI analysis: {dropped} page(s) dropped before processing (counted as degraded)")
    violations = {"bare_inp": 0, "fabricated_number": 0, "out_of_evidence_entity": 0}

    for page, digest, result in processed:
        if result is None:
            page.analysis = None
            degraded += 1
            _emit(f"AI analysis degraded to null: {page.url}")
            continue

        # Mutate in place: a populated analysis serializes via the unchanged path.
        page.analysis = result
        analyzed += 1

        # AI-SPEC §6 runtime pre-write guardrails — FLAG + LOG + COUNT, retain.
        # WR-05: a grounding function raising over unexpected input must not abort
        # the post-pass and discard the run. The analysis is already retained on
        # the page above; on a non-KI failure here, skip this page's violation
        # counting and continue (KeyboardInterrupt still propagates).
        try:
            text = " ".join(
                part
                for part in (
                    result.observation,
                    result.potential_cause,
                    result.suggested_optimization,
                )
                if part
            )
            fired: list[str] = []
            if not check_no_bare_inp(text):
                violations["bare_inp"] += 1
                fired.append("bare_inp")
            fabricated = find_fabricated_numbers(text, digest)
            if fabricated:
                violations["fabricated_number"] += len(fabricated)
                fired.append("fabricated_number")
            entities = find_unsupported_entities(text, digest)
            if entities:
                violations["out_of_evidence_entity"] += len(entities)
                fired.append("out_of_evidence_entity")
            if fired:
                _emit(f"grounding flags on {page.url}: {', '.join(fired)} (analysis retained)")
        except KeyboardInterrupt:
            raise
        except Exception:
            _emit(f"grounding check failed on {page.url}; analysis retained, not flagged")

    total_violations = sum(violations.values())
    _emit(
        f"AI analysis: {degraded}/{len(data_pages)} degraded to null; "
        f"{total_violations} grounding violations flagged"
    )

    return {
        "analyzed": analyzed,
        "degraded": degraded,
        "insufficient": insufficient,
        "violations": violations,
    }


# --- Grounding pure functions (run in CI eval AND at runtime as guardrails) ---
# These three are the deterministic grounding invariants. Plan 02 implements the
# bodies; the names/signatures are fixed here so the Wave-0 eval tests import a
# resolvable symbol and fail RED on NotImplementedError, not ImportError.


# Numeric token: an integer or decimal, optionally thousands-grouped with commas
# (e.g. "4800", "1,234,567", "0.020"). Used by both the digest-set extraction and
# the analysis-text extraction so the two normalize identically.
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Window (chars) around an "INP" mention in which an adjacent number makes a claim
# "about INP", and the wider window in which a TBT / proxy / lab label clears it.
_INP_NUM_WINDOW = 15
_INP_LABEL_WINDOW = 40

# Curated stack/vendor wordlist (D-05 / AI-SPEC dim 3). A named framework, server,
# CDN, or library asserted in the analysis but ABSENT from the digest evidence is
# an out-of-evidence entity. Matched on word boundaries, case-insensitively.
_ENTITY_WORDLIST: tuple[str, ...] = (
    # frontend frameworks / libraries
    "react",
    "angular",
    "vue",
    "svelte",
    "ember",
    "backbone",
    "preact",
    "jquery",
    "next.js",
    "nextjs",
    "nuxt",
    "gatsby",
    "remix",
    "alpine.js",
    "bootstrap",
    "tailwind",
    "material-ui",
    # build tools / bundlers
    "webpack",
    "vite",
    "rollup",
    "parcel",
    "babel",
    "esbuild",
    # backend frameworks / languages
    "django",
    "flask",
    "fastapi",
    "rails",
    "laravel",
    "symfony",
    "express",
    "node.js",
    "nodejs",
    "spring",
    "asp.net",
    "php",
    "phoenix",
    # CMS / e-commerce
    "wordpress",
    "drupal",
    "joomla",
    "shopify",
    "magento",
    "wix",
    "squarespace",
    # servers / runtimes / caches
    "nginx",
    "apache",
    "iis",
    "gunicorn",
    "uvicorn",
    "varnish",
    "redis",
    "memcached",
    "postgres",
    "mysql",
    "mongodb",
    # CDNs / third-parties
    "cloudflare",
    "fastly",
    "akamai",
    "cloudfront",
    "netlify",
    "vercel",
    "google tag manager",
    "google analytics",
    "hotjar",
    "segment",
    "stripe",
    "intercom",
    "hubspot",
    "optimizely",
)


def _norm_num(token: str) -> float | None:
    """Normalize a numeric token to a comparable float (strip commas); ``None`` on failure."""
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def check_no_bare_inp(text: str) -> bool:
    """True iff ``text`` contains no bare-INP claim (D-15 / mirrors ``_no_bare_inp``).

    PASS (True) when no real field-INP value is asserted; FAIL (False) on a bare
    "INP is 480 ms"-style claim — a number sitting next to an "INP" mention that is
    NOT cleared by a nearby TBT / "lab proxy" label. The headless pass measures
    TBT (the lab proxy), never real field INP, so a numbered bare INP is wrong.
    """
    lowered = text.lower()
    # WR-02: anchor on word boundaries. Unanchored "inp" matched "input" (false
    # positive) and unanchored "lab" matched "available"/"label" (false negative
    # that cleared a genuinely bare-INP claim). \b... requires a standalone word.
    for m in re.finditer(r"\binp\b", lowered):
        start, end = m.start(), m.end()
        num_window = lowered[max(0, start - _INP_NUM_WINDOW) : end + _INP_NUM_WINDOW]
        if not re.search(r"\d", num_window):
            continue  # no number adjacent to this INP mention → not a numeric claim
        label_window = lowered[max(0, start - _INP_LABEL_WINDOW) : end + _INP_LABEL_WINDOW]
        if not re.search(r"\btbt\b|\bproxy\b|\blab\b", label_window):
            return False  # a bare-INP number with no TBT/proxy/lab label nearby
    return True


def find_fabricated_numbers(text: str, digest_text: str) -> list[str]:
    """Return numeric tokens in ``text`` absent from ``digest_text`` (AI-01 anti-hallucination).

    Extract every numeric from the analysis ``text`` and return those whose
    normalized value does not appear among the digest's numbers (commas stripped,
    trailing-zero/format differences collapsed via float comparison). Empty list =
    every cited number is grounded in the digest.
    """
    digest_nums = {n for tok in _NUM_RE.findall(digest_text) if (n := _norm_num(tok)) is not None}
    fabricated: list[str] = []
    for tok in _NUM_RE.findall(text):
        n = _norm_num(tok)
        if n is not None and n not in digest_nums:
            fabricated.append(tok)
    return fabricated


def find_unsupported_entities(text: str, digest_text: str) -> list[str]:
    """Return framework/server/CDN/etc. entities in ``text`` absent from ``digest_text`` (AI-02).

    Flag any curated stack/vendor wordlist entity asserted in the analysis ``text``
    (word-boundary, case-insensitive) that does NOT also appear in the digest
    evidence (e.g. as a waterfall request URL). Empty list = no guessed stack.
    """
    text_l = text.lower()
    digest_l = digest_text.lower()
    found: list[str] = []
    for term in _ENTITY_WORDLIST:
        pat = r"\b" + re.escape(term.lower()) + r"\b"
        if re.search(pat, text_l) and not re.search(pat, digest_l):
            found.append(term)
    return found
