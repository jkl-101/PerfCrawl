"""Env-gated LLM-judge calibration harness (Phase 05.1 — the paid Lane-2 test).

This is the *build-and-run-once* meta-eval (AI-SPEC §5, D-03): for every
human-labeled fixture it has the opus judge grade TWO reference analyses — the
``gold`` (the senior-engineer-accepted answer, labeled PASS / high score) and the
``anti_gold`` (a deliberately-wrong answer, labeled FAIL / low score) — then
calibrates the judge's 1-5 scores and PASS/FAIL calls against those human labels
(``calibrate``) and emits a per-dimension ``{spearman, kappa, trusted}`` report for
dims 6-9. It is the ONLY place in the suite that spends real tokens, so it is
double-gated:

  - ``pytest.mark.llm``  — default ``addopts = -m 'not e2e and not llm'`` deselects
    it on a bare ``uv run pytest`` (D-02: the judge NEVER fires on a normal
    commit/PR — denial-of-wallet mitigation T-05.1-11).
  - ``skipif(no ANTHROPIC_API_KEY)`` — belt-and-braces, so an explicit ``-m llm``
    WITHOUT a key SKIPS cleanly instead of erroring on a missing key.

CR-01 FIX — grade the LABELED text, against a calibratable label set. The earlier
design re-generated an analysis with ``analyze_page`` and then correlated the
judge's grade of THAT text against the human's grade of the *gold* text — two
different objects — over an all-PASS, near-constant gold set. That made the trust
gate structurally incapable of ever reaching ``trusted=True`` and blind to
rubber-stamping (kappa could only be 1.0 or 0). The harness now judges the SAME
texts the human labeled (gold + anti_gold), so judge and human grade the same
object, and the gold/anti_gold pairing guarantees both PASS and FAIL human calls
and a 1-5 score spread per dimension — the precondition ``calibrate`` needs.

ADVISORY-UNTIL-CALIBRATED (D-03 / T-05.1-12): a dimension whose
``min(spearman, kappa) < 0.70`` (or one whose labels are uncalibratable) is
reported but NEVER fails the test — an uncalibrated judge must not gate a merge.
The harness's only hard assertion is that it ran and emitted a calibration report
for all four judged dimensions, NOT that all four clear the 0.70 bar.

Every printed line is routed through the AUTH-04 ``make_scrubber`` seeded with the
key (T-05.1-10): the judge prompt and the calibration report could otherwise leak
``ANTHROPIC_API_KEY`` into pytest's captured output.

Independence: the graded texts are human-authored references (gold) and
human-reviewed deliberately-bad references (anti_gold), never the judge's own
output, so the grader is independent of what it grades (AI-SPEC §3).

A ``None`` verdict (``judge_pair`` degraded) is a DROPPED calibration point — never
a fabricated verdict (§3 Pitfall 6 / T-05.1-13).
"""

from __future__ import annotations

import os
from pathlib import Path
from statistics import StatisticsError

import anthropic
import calibrate as calibrate_mod  # tests/eval on sys.path (pytest prepend mode)
import judge as judge_mod
import pytest

from perfcrawl import analysis
from perfcrawl.auth import make_scrubber
from perfcrawl.constants import AI_MAX_RETRIES, AI_REQUEST_TIMEOUT_S, ANTHROPIC_API_KEY_ENV

# Double gate: the marker deselects by default; skipif is the no-key safety net so
# an explicit `-m llm` without a key skips rather than erroring (AI-SPEC §3 P1).
pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="judge needs ANTHROPIC_API_KEY",
    ),
]

# The four subjective dimensions the judge grades (dims 6-9). The keys are the
# JudgeVerdict attribute names AND the gold-label `dimensions` keys (they match by
# construction — Plan 02 authored the gold labels against this exact schema).
_JUDGED_DIMS = (
    "causal_plausibility",  # dim 6 (Critical / FM-1)
    "threshold_correctness",  # dim 7 (High)
    "actionability",  # dim 8 (High)
    "prioritization",  # dim 9 (Medium)
)

_DIGESTS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "digests"


def _labeled_fixture_names() -> list[str]:
    """Every digest fixture name, sorted — the harness drops the unlabeled ones."""
    return sorted(p.stem for p in _DIGESTS_DIR.glob("*.json"))


def _render_analysis(observation, cause, optimization) -> str:
    """Render an O/C/O triple (gold dict OR generated AnalysisResult) to judge text."""
    return (
        f"Observation: {observation or ''}\n"
        f"Potential cause: {cause or ''}\n"
        f"Suggested optimization: {optimization or ''}"
    )


def test_judge_calibration_build_and_run_once(digest_page, load_gold, load_anti_gold) -> None:
    """Judge gold + anti_gold per labeled fixture, then calibrate per dimension (D-03).

    For each human-labeled fixture: ``build_digest`` → for EACH labeled reference
    (the ``gold`` PASS answer and the ``anti_gold`` FAIL answer) render its O/C/O
    and ``judge_pair`` it on the opus judge client, always against the gold text as
    the ``<gold_reference>``. The judge therefore grades the SAME text the human
    labeled (CR-01 fix), and every dimension accumulates both a PASS point (gold)
    and a FAIL point (anti_gold) with a 1-5 score spread — the calibratable shape.
    Drop any reference where ``judge_pair`` degraded to ``None`` (a missing point,
    never a fabricated verdict). Then per judged dim, ``calibrate`` the judge vs
    human score/verdict arrays and emit a scrubbed
    ``{dim}: spearman=.. kappa=.. trusted=..`` line.

    The harness stays ADVISORY: a dim below ``min(spearman, kappa) >= 0.70`` (or one
    whose labels are uncalibratable) is reported but never fails the test. The only
    assertion is that a calibration report was emitted for all four judged dimensions.
    """
    key = os.environ[ANTHROPIC_API_KEY_ENV]
    scrub = make_scrubber(key)

    def emit(line: str) -> None:
        # Every judge-lane sink is scrubbed (AUTH-04 / T-05.1-10): the key must not
        # survive into pytest's captured stdout.
        print(scrub(line))

    # One opus judge client. Mirror the production client tuning
    # (cli._run_ai_post_pass): a bounded retry budget + a per-request timeout below
    # the SDK's 10-min default so a hung judge call degrades that point promptly
    # instead of stalling the whole harness. Sequential calls warm the JUDGE_RUBRIC
    # prompt cache on the first pair.
    judge_client = anthropic.Anthropic(
        api_key=key, max_retries=AI_MAX_RETRIES, timeout=AI_REQUEST_TIMEOUT_S
    )

    # Per-dimension parallel arrays of (judge_score, human_score, judge_call, human_call),
    # accumulated over every labeled reference (gold + anti_gold) the judge graded.
    judge_scores: dict[str, list[float]] = {d: [] for d in _JUDGED_DIMS}
    human_scores: dict[str, list[float]] = {d: [] for d in _JUDGED_DIMS}
    judge_calls: dict[str, list[str]] = {d: [] for d in _JUDGED_DIMS}
    human_calls: dict[str, list[str]] = {d: [] for d in _JUDGED_DIMS}

    judged_points = 0
    for name in _labeled_fixture_names():
        gold = load_gold(name)
        if gold is None:
            continue  # the fully-null-error-row carries no gold (D-06) — not a point

        page = digest_page(name)
        digest_text = analysis.build_digest(page)

        gold_text = _render_analysis(
            gold.get("observation"),
            gold.get("potential_cause"),
            gold.get("suggested_optimization"),
        )

        # Grade BOTH labeled references against the gold reference. The gold answer
        # should grade PASS/high; the anti_gold (phantom cause, threshold inversion,
        # boilerplate, tunnel vision) should grade FAIL/low — that contrast is the
        # signal the trust gate measures.
        references = [("gold", gold)]
        anti_gold = load_anti_gold(name)
        if anti_gold is not None:
            references.append(("anti_gold", anti_gold))

        for label, ref in references:
            ref_text = _render_analysis(
                ref.get("observation"),
                ref.get("potential_cause"),
                ref.get("suggested_optimization"),
            )
            verdict = judge_mod.judge_pair(
                judge_client,
                digest_text=digest_text,
                analysis_text=ref_text,
                gold_label_text=gold_text,
            )
            if verdict is None:
                # A degraded judge call is a dropped point, never a fabricated verdict.
                emit(f"[drop] {name}/{label}: judge_pair degraded to None")
                continue

            ref_dims = ref.get("dimensions") or {}
            for dim in _JUDGED_DIMS:
                jv = getattr(verdict, dim)
                hv = ref_dims.get(dim) or {}
                judge_scores[dim].append(float(jv.score))
                judge_calls[dim].append(jv.verdict)
                human_scores[dim].append(float(hv["score"]))
                human_calls[dim].append(hv["verdict"])
            judged_points += 1

    emit(f"calibration: {judged_points} judged points over {len(_JUDGED_DIMS)} dimensions")

    reports: dict[str, dict] = {}
    for dim in _JUDGED_DIMS:
        try:
            result = calibrate_mod.calibrate(
                judge_scores[dim], human_scores[dim], judge_calls[dim], human_calls[dim]
            )
            if result.get("uncalibratable"):
                # Defense-in-depth: the gold/anti_gold dataset makes this unreachable,
                # but a future label edit that collapses a dim to one call or a
                # constant score is reported honestly, never as a confident verdict.
                emit(f"{dim}: spearman=n/a kappa=n/a trusted=False (uncalibratable labels)")
            else:
                emit(
                    f"{dim}: spearman={result['spearman']:.3f} "
                    f"kappa={result['kappa']:.3f} trusted={result['trusted']}"
                )
        except StatisticsError as e:
            # A constant JUDGE score array (e.g. the judge stamped one score on every
            # reference — a rubber stamp) has no defined rank correlation. That is an
            # UNCALIBRATED dim, not a pass — report advisory-only with trusted=False;
            # it still counts as an emitted report line for this dimension.
            result = {"spearman": None, "kappa": None, "trusted": False, "error": str(e)}
            emit(f"{dim}: spearman=n/a kappa=n/a trusted=False ({e})")
        reports[dim] = result

    # ADVISORY-UNTIL-CALIBRATED (D-03): the ONLY hard assertion is that the harness
    # ran and emitted a calibration report for all four judged dims. A sub-0.70
    # (or uncalibrated) dim is reported, never failed — an untrusted judge must not
    # gate a merge (T-05.1-12).
    assert set(reports) == set(_JUDGED_DIMS), (
        "the harness must emit a calibration report for all four judged dimensions"
    )
    assert judged_points > 0, (
        "no calibration points survived — the judge harness produced no signal"
    )
