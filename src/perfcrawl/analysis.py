"""Phase-5 AI analysis — the public contract (Wave-0 stub).

This module is the per-page AI-enrichment seam: it turns each measured
``PageResult`` into a deterministic metric *digest*, dispatches one stateless,
prompt-cached ``client.messages.parse(output_format=AnalysisResult)`` call per
page through a bounded pool, and writes the grounded ``AnalysisResult`` (or a
clean ``None``) back onto ``page.analysis`` — which the unchanged
``output.write_outputs`` / ``store.write_run`` path then serializes for free.

**This file is an interface-first CONTRACT STUB.** Every public name the test
harness imports exists here, but the request/response bodies raise
``NotImplementedError`` — Plan 02 (engine) fills them and turns the Wave-0
deterministic eval suite GREEN. The names + signatures are the binding contract
between this plan's RED tests and the Plan-02 implementation:

  - ``build_digest(page)``         — deterministic, sorted, timestamp-free digest text
  - ``RUBRIC``                     — the frozen ≥1,024-token cite-the-numbers system prefix
  - ``analyze_page(client, ...)``  — one structured-output call; degrades to ``None`` (D-09)
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

from typing import TYPE_CHECKING

from rich.console import Console

from perfcrawl.constants import DEFAULT_AI_MODEL
from perfcrawl.models import AnalysisResult, PageResult

if TYPE_CHECKING:  # pragma: no cover - typing-only import for annotations
    import anthropic

    from perfcrawl.models import RunRecord

# Module-owned stderr console (mirrors measure_pass.py:69-72). A library module
# never imports cli.py's console — that would be a layering cycle.
_err_console = Console(stderr=True)

# --- The frozen cite-the-numbers rubric (Plan 02 writes the real prose) ------
# Plan 02 replaces this placeholder with the real ≥1,024-token frozen system
# prefix (metric glossary + the 0-100-higher-is-better scale + the labeled-INP-
# proxy rule + the "insufficient data over speculation" rule + worked examples).
# It MUST stay a byte-stable module constant so the prompt-cache prefix hits
# (AI-SPEC Pitfall 1/2). The empty placeholder here makes ``test_rubric_frozen``
# fail RED until Plan 02 lands the real rubric — the intended Wave-0 outcome.
RUBRIC: str = ""


def build_digest(page: PageResult) -> str:
    """Render ``page`` into the deterministic, sorted, timestamp-free digest text.

    CONTRACT (Plan 02 implements): selected ``PageResult`` fields only — url,
    status, the four 0-100 category scores, LCP/CLS/TBT(labeled INP proxy)/TTFB
    medians, request count, total bytes, slowest request, and the top-N slowest
    waterfall rows (``AI_WATERFALL_TOP_N``, sorted by timing desc / url asc).
    Nulls render as an explicit ``n/a``; floats round to fixed precision; NO
    ``datetime``/UUID/run-id — so the same page yields byte-identical text every
    call (and a reordered waterfall renders identically).
    """
    raise NotImplementedError("build_digest is a Wave-0 contract stub; Plan 02 implements it.")


def analyze_page(
    client: anthropic.Anthropic,
    digest_text: str,
    model: str = DEFAULT_AI_MODEL,
) -> AnalysisResult | None:
    """Run one structured-output call for ``digest_text``; degrade to ``None`` (D-09).

    CONTRACT (Plan 02 implements): ``client.messages.parse(model=...,
    max_tokens=AI_MAX_TOKENS, temperature=0, system=[{RUBRIC, cache_control}],
    messages=[{user, digest_text}], output_format=AnalysisResult)`` →
    ``resp.parsed_output``. Catches ``anthropic.APIError`` AND a broad
    ``Exception`` (defense-in-depth), and treats ``parsed_output is None``
    identically → returns ``None`` so a single AI miss never crashes the run.
    """
    raise NotImplementedError("analyze_page is a Wave-0 contract stub; Plan 02 implements it.")


def analyze_run(
    run_record: RunRecord,
    *,
    client: anthropic.Anthropic,
    model: str = DEFAULT_AI_MODEL,
    scrub=None,
    err_console: Console | None = None,
) -> dict:
    """Bounded-pool post-pass: fill ``page.analysis`` for every page; return a summary.

    CONTRACT (Plan 02 implements): mirror ``measure_pass`` —
    ``ThreadPoolExecutor(max_workers=AI_POOL_SIZE)`` over the pages with one
    shared thread-safe ``client``. Per page: ``is_error_row(page)`` →
    short-circuit to ``analysis=None`` with NO API call (D-06); else
    ``build_digest`` → ``analyze_page`` → assign the result back onto
    ``page.analysis`` (mutate ``run_record.pages`` in place so the existing
    scrub/write path serializes it). KeyboardInterrupt does a partial flush.
    Returns a summary dict with analyzed / degraded / insufficient / violations
    counts for the stderr aggregate line.
    """
    raise NotImplementedError("analyze_run is a Wave-0 contract stub; Plan 02 implements it.")


# --- Grounding pure functions (run in CI eval AND at runtime as guardrails) ---
# These three are the deterministic grounding invariants. Plan 02 implements the
# bodies; the names/signatures are fixed here so the Wave-0 eval tests import a
# resolvable symbol and fail RED on NotImplementedError, not ImportError.


def check_no_bare_inp(text: str) -> bool:
    """True iff ``text`` contains no bare-INP claim (D-15 / mirrors ``_no_bare_inp``).

    CONTRACT (Plan 02 implements): PASS (True) when any INP mention is adjacent
    to a TBT / "lab proxy" label; FAIL (False) on a bare "INP is 480 ms"-style
    assertion of a real field-INP value the headless pass cannot measure.
    """
    raise NotImplementedError("check_no_bare_inp is a Wave-0 contract stub; Plan 02 implements it.")


def find_fabricated_numbers(text: str, digest_text: str) -> list[str]:
    """Return numeric tokens in ``text`` absent from ``digest_text`` (AI-01 anti-hallucination).

    CONTRACT (Plan 02 implements): extract numerics from the analysis text and
    return those that do not match a value present in the digest (with unit /
    format normalization). Empty list = fully grounded.
    """
    raise NotImplementedError(
        "find_fabricated_numbers is a Wave-0 contract stub; Plan 02 implements it."
    )


def find_unsupported_entities(text: str, digest_text: str) -> list[str]:
    """Return framework/server/CDN/etc. entities in ``text`` absent from ``digest_text`` (AI-02).

    CONTRACT (Plan 02 implements): flag any named framework / server / CDN /
    third-party / specific render-blocking resource asserted in the analysis
    that does not appear in the digest evidence. Empty list = no guessed stack.
    """
    raise NotImplementedError(
        "find_unsupported_entities is a Wave-0 contract stub; Plan 02 implements it."
    )
