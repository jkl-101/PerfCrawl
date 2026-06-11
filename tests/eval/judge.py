"""LLM-as-judge engine for eval dims 6-9 (Phase 05.1).

This module is the *judge* — the twin of the already-GREEN Phase-05 generator in
``src/perfcrawl/analysis.py``. It copies the proven ``client.messages.parse(
output_format=...)`` SDK shape verbatim (``analysis.py:291-304``) but swaps in a
stronger independent grader (``claude-opus-4-8``, D-04 — opus judging the
sonnet-4-6 generator), a per-dimension ``JudgeVerdict`` schema, a frozen
reference-guided ``JUDGE_RUBRIC``, and the same degrade-to-None error handling.

It lives in ``tests/eval/`` (no ``__init__.py``) so pytest's default prepend
import mode puts ``tests/eval`` on ``sys.path`` and Plan 04's harness can
``import judge`` as a top-level module — the same way ``test_digest`` /
``test_grounding`` coexist.

Layering: like ``analysis.py:56-58``, this module owns its own stderr ``Console``
and NEVER imports ``cli.py`` (that would be a layering cycle).

Contract (the binding shape Plan 04 wires against):
  - ``DimensionVerdict``  — PASS/FAIL + 1-5 score + <=400-char rationale
  - ``JudgeVerdict``      — one sub-verdict per dim 6-9 (all four required)
  - ``JUDGE_RUBRIC``      — frozen, cacheable, reference-guided system prefix
  - ``judge_pair(...)``   — one structured-output call; degrades to ``None`` (D-09)
"""

from __future__ import annotations

from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console

# Single-source-of-truth for every Phase-5 AI literal (constants.py:182-215): the
# judge model id and its output bound live THERE, never inlined here — bumping
# AI_OPUS_MODEL must move the judge with it (the 4-7 -> 4-8 drift already bit once).
from perfcrawl.constants import AI_OPUS_MODEL, JUDGE_MAX_TOKENS

# Module-owned stderr console (mirrors analysis._err_console / measure_pass).
# A library/eval module never imports cli.py's console — that is a layering cycle.
_err_console = Console(stderr=True)


class DimensionVerdict(BaseModel):
    """One subjective-dimension verdict: a PASS/FAIL call + a bounded score + reason.

    ``score`` is bounded 1-5 (Pydantic rejects 0 or 6 as a ``ValidationError``);
    ``rationale`` is capped at 400 chars to keep every verdict terse and auditable
    and to honor the anti-verbosity discipline (never reward length).
    """

    model_config = ConfigDict(extra="ignore")

    verdict: Literal["PASS", "FAIL"]
    score: int = Field(ge=1, le=5)
    rationale: str = Field(max_length=400)


class JudgeVerdict(BaseModel):
    """The structured per-pair verdict over dims 6-9.

    All four sub-verdicts are REQUIRED — a missing dimension is a Pydantic
    ``ValidationError``, not a silent gap (the schema is the contract, AI-SPEC §3).
    """

    model_config = ConfigDict(extra="ignore")

    causal_plausibility: DimensionVerdict  # dim 6 (Critical / FM-1 — the #1 risk)
    threshold_correctness: DimensionVerdict  # dim 7 (High) — judge half of the band check
    actionability: DimensionVerdict  # dim 8 (High)
    prioritization: DimensionVerdict  # dim 9 (Medium)


# --- The frozen reference-guided judge rubric (same discipline as analysis.RUBRIC) ---
# A byte-stable module constant sent on EVERY judge call in ``system`` with
# ``cache_control: ephemeral`` so the prompt cache hits (AI-SPEC §4b / generator
# Pitfall 1). It MUST clear the ~1,024-token Opus-4.8 cache floor, so it is bulked
# with the §1b Good/Bad criteria for dims 6-9, the CWV bands (verbatim with
# analysis.RUBRIC / constants.py — the Task-2 freeze guard pins them), the
# grade-against-the-GOLD-reference rule, the anti-verbosity + anti-stack-guess
# clauses, and one worked PASS + one worked FAIL example. NEVER interpolate
# per-pair data here — the variable triple goes in ``messages`` (would drift cache).
JUDGE_RUBRIC: str = """\
You are an EVALUATION JUDGE for an automated web-performance analysis tool. You
grade ONE generated performance analysis of ONE web page against (a) the DIGEST of
that page's measured metrics and (b) a human GOLD REFERENCE analysis written by a
senior performance engineer. You return a structured per-dimension verdict.

You will be given, in this fixed field order:
  <digest>            the deterministic metric digest the generator saw (numbers only)
  <generated_analysis>  the analysis to grade (observation / cause / optimization)
  <gold_reference>    the human gold-label analysis — the acceptable answer

For EACH of the four dimensions below, return PASS or FAIL, a 1-5 score (5 = fully
meets the bar, 1 = badly fails it), and a terse rationale (one or two sentences,
hard-capped). Judge ONLY against the digest and the gold reference — never against
your own taste, your own preferred prose, or outside knowledge of the page.

THE OVERRIDING RULE — GRADE AGAINST THE GOLD REFERENCE, NOT YOUR OWN TASTE. The
gold reference is what a trusted human expert accepted for this page. If the
generated analysis reaches the same substantive conclusion as the gold reference
using the digest's own evidence, it PASSES even if the wording differs. Do not
invent a higher bar than the gold reference sets. Do not penalize a correct,
grounded analysis for omitting something the gold reference also omits.

ANTI-VERBOSITY CLAUSE. A terse, correct note beats a verbose one — NEVER reward
length. A longer, more elaborate analysis is not a better analysis. If the
generated text is wordier than the gold reference but says no more of substance,
that is not a point in its favor; if it pads a correct point with filler, prefer
the terse version. Length is never evidence of quality.

ANTI-STACK-GUESS CLAUSE. Do NOT reward the generated analysis for naming a
framework, library, server, CDN, or specific third-party that the digest does not
contain. The digest carries metric numbers and request URLs ONLY — never the
page's HTML, framework, or server software. A plausible-but-fabricated mechanism
("React hydration is slow", "nginx is misconfigured", "your CDN is cold") is the
project's #1 failure mode: it sends an engineer chasing a phantom. Naming such a
thing is a FAIL on causal plausibility even when it sounds reasonable, UNLESS that
exact token already appears in the digest (e.g. as a waterfall request URL).

THE CORE WEB VITALS BANDS (use these EXACT cutoffs; they match the generator's
rubric and the shared constants — do not drift them):
  - LCP (Largest Contentful Paint): GOOD <= 2500 ms, needs-improvement
    2500-4000 ms, POOR > 4000 ms. Lower is better.
  - CLS (Cumulative Layout Shift): GOOD <= 0.1, needs-improvement 0.1-0.25,
    POOR > 0.25. Lower is better.
  - INP (real field interactivity): GOOD <= 200 ms, POOR > 500 ms. NOTE: the lab
    pass measures TBT (Total Blocking Time) as the PROXY for INP — a bare "INP is
    N ms" claim is wrong; TBT is the only interactivity number the digest carries.
  - The four category scores (Performance / Accessibility / SEO / Best-practices)
    are 0-100 where HIGHER IS BETTER. A 98 is excellent, not a concern. A score of
    90+ with CWV in the good band means the page is HEALTHY — do not reward an
    analysis that invents a problem on it.

THE FOUR DIMENSIONS YOU GRADE:

Dimension 6 — CAUSAL PLAUSIBILITY FROM THE EVIDENCE ALONE (Critical, the #1 risk).
  PASS: the Potential Cause connects a NAMED, PRESENT digest metric to a plausible
    mechanism — e.g. "the slowest request is a 2410000-byte asset at 1820 ms, which
    likely is (or blocks) the LCP element." Derivable from THIS page's digest alone.
  FAIL: a stack/architecture guess the metrics cannot prove — naming a framework,
    server, CDN, or render-blocking resource absent from the captured waterfall.
    This is the plausible-but-fabricated mechanism the code wordlist cannot see.

Dimension 7 — THRESHOLD-CORRECT INTERPRETATION / NO PROBLEM ON A GREEN METRIC (High).
  PASS: a metric is called a problem only when it is actually in needs-improvement
    or poor against the CWV bands above; higher-is-better scores read correctly (a
    0.98 / 98 perf score is excellent, not a concern).
  FAIL: a threshold inversion — "LCP needs work" at 1200 ms (which is <= 2500 ms,
    GOOD), recommending work on an already-green metric, or reading a high score as
    bad. A metric in the GOOD band described with problem/optimize language fails.

Dimension 8 — ACTIONABILITY & SPECIFICITY OF THE OPTIMIZATION (High).
  PASS: the Suggested Optimization names the flagged metric AND the actual evidence
    — the specific slow request, the real byte weight, the concrete number — so an
    engineer knows exactly what to do on THIS page.
  FAIL: generic boilerplate uncoupled from the numbers — "optimize images", "reduce
    JavaScript", "improve caching" — advice that could apply to any page regardless
    of what this digest shows.

Dimension 9 — METRIC PRIORITIZATION / NO TUNNEL VISION (Medium).
  PASS: the worst metric on the page gets the attention — a 12000 ms TTFB or a
    6000000-byte payload is not ignored in favor of a tidy narrative about a
    mediocre CLS.
  FAIL: a clean story about the LESSER problem while the more severe metric on the
    same digest goes unmentioned. "Not wrong", but it misallocates attention.

WORKED EXAMPLE — A PASS:
  Digest: LCP 4800 ms (poor, > 4000 ms), slowest request 2410000 bytes at 1820 ms,
  total bytes 3100000. Gold reference says the large hero asset is the likely LCP
  driver and recommends compressing it. Generated analysis: "LCP is 4800 ms (poor);
  the slowest request is 2410000 bytes at 1820 ms and is the likely LCP element —
  compress/resize it." Verdict: causal_plausibility PASS 5 (names a present metric +
  plausible mechanism, no stack guess); threshold_correctness PASS 5 (4800 ms is
  correctly read as poor); actionability PASS 5 (names the specific slow request and
  its byte weight); prioritization PASS 5 (addresses the worst metric). Matches the
  gold reference's substance with the digest's own numbers — terse and grounded.

WORKED EXAMPLE — A FAIL:
  Digest: Performance 98, LCP 1200 ms (good, <= 2500 ms), CLS 0.02, TBT 90 ms — a
  healthy page. Gold reference: "This page is healthy; no action needed." Generated
  analysis: "LCP needs work and React hydration is slowing the page; reduce
  JavaScript and optimize images." Verdict: causal_plausibility FAIL 1 (names
  "React" — a framework absent from the digest, a fabricated mechanism);
  threshold_correctness FAIL 1 (says "LCP needs work" at 1200 ms which is in the
  GOOD band, <= 2500 ms — a threshold inversion); actionability FAIL 2 ("reduce
  JavaScript" / "optimize images" is generic boilerplate uncoupled from the
  numbers); prioritization FAIL 2 (invents problems on a healthy page). It diverges
  from the gold reference and fabricates against the evidence.

Stay terse. Cite the digest's own numbers in your rationale where relevant. When
the generated analysis matches the gold reference's substance on the digest's
evidence, PASS it; when it fabricates, inverts a threshold, rambles generically, or
chases the lesser problem, FAIL it.
"""


def judge_pair(
    client: anthropic.Anthropic,
    *,
    digest_text: str,
    analysis_text: str,
    gold_label_text: str,
    model: str = AI_OPUS_MODEL,
) -> JudgeVerdict | None:
    """Run one reference-guided judge call for a triple; degrade to ``None`` (D-09).

    Copies the proven-GREEN ``analyze_page`` body verbatim (``analysis.py:291-304``):
    ``client.messages.parse(model, max_tokens=JUDGE_MAX_TOKENS, temperature=0, system=[{
    JUDGE_RUBRIC, cache_control: ephemeral}], messages=[{user, 3-part triple}],
    output_format=JudgeVerdict)`` → ``resp.parsed_output``. The static JUDGE_RUBRIC
    is the ONLY thing in ``system`` (byte-stable cache prefix); the variable triple
    goes in ``messages`` in the FIXED field order (digest, analysis, gold) so the
    contract is pinned. A grader MUST be reproducible → ``temperature=0``.

    Degrades to ``None`` — never crashes, never loop-retries — on
    ``anthropic.APIError``, any broad ``Exception`` (defense-in-depth), and a
    ``parsed_output is None`` (a refusal / max_tokens truncation). On the judge lane
    a ``None`` means a single dropped calibration pair, not a failed run. No
    app-level retry (D-11): the SDK already exhausted ``max_retries`` before raising.
    """
    try:
        resp = client.messages.parse(
            model=model,
            max_tokens=JUDGE_MAX_TOKENS,
            temperature=0,
            system=[
                {"type": "text", "text": JUDGE_RUBRIC, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"<digest>\n{digest_text}\n</digest>\n"
                        f"<generated_analysis>\n{analysis_text}\n</generated_analysis>\n"
                        f"<gold_reference>\n{gold_label_text}\n</gold_reference>"
                    ),
                }
            ],
            output_format=JudgeVerdict,
        )
        return resp.parsed_output
    except anthropic.APIError:
        return None
    except Exception:
        return None
