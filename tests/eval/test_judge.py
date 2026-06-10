"""Env-gated LLM-judge calibration harness (Phase 05.1 — the paid Lane-2 test).

This is the *build-and-run-once* meta-eval (AI-SPEC §5, D-03): it builds a judge
verdict for every human-labeled fixture, calibrates the judge's 1-5 scores and
PASS/FAIL calls against the gold labels (``calibrate``), and emits a per-dimension
``{spearman, kappa, trusted}`` report for dims 6-9. It is the ONLY place in the
suite that spends real tokens, so it is double-gated:

  - ``pytest.mark.llm``  — default ``addopts = -m 'not e2e and not llm'`` deselects
    it on a bare ``uv run pytest`` (D-02: the judge NEVER fires on a normal
    commit/PR — denial-of-wallet mitigation T-05.1-11).
  - ``skipif(no ANTHROPIC_API_KEY)`` — belt-and-braces, so an explicit ``-m llm``
    WITHOUT a key SKIPS cleanly instead of erroring on a missing key.

ADVISORY-UNTIL-CALIBRATED (D-03 / T-05.1-12): a dimension whose
``min(spearman, kappa) < 0.70`` is reported but NEVER fails the test — an
uncalibrated judge must not gate a merge. The harness's only hard assertion is
that it ran and emitted a calibration report for all four judged dimensions, NOT
that all four clear the 0.70 bar.

Every printed line is routed through the AUTH-04 ``make_scrubber`` seeded with the
key (T-05.1-10): the judge prompt, the generated analysis, and the calibration
report could otherwise leak ``ANTHROPIC_API_KEY`` into pytest's captured output.

Independence: the generator is the sonnet ``analyze_page`` path and the grader is
the opus ``judge_pair`` path on two distinct clients, so the judge never grades
its own output (AI-SPEC §3 — a grader must be independent of the generator).

A ``None`` verdict (``judge_pair`` degraded) or a ``None`` generated analysis is a
DROPPED calibration pair — never a fabricated verdict (§3 Pitfall 6 / T-05.1-13).
"""

from __future__ import annotations

import os
from pathlib import Path
from statistics import StatisticsError

import anthropic
import pytest

import calibrate as calibrate_mod  # noqa: E402 — tests/eval on sys.path (prepend mode)
import judge as judge_mod  # noqa: E402

from perfcrawl import analysis
from perfcrawl.auth import make_scrubber
from perfcrawl.constants import ANTHROPIC_API_KEY_ENV

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


def test_judge_calibration_build_and_run_once(digest_page, load_gold) -> None:
    """Build a judge verdict per labeled fixture, then calibrate per dimension (D-03).

    For each human-labeled fixture: ``build_digest`` (the SAME digest the generator
    saw) → RE-GENERATE the analysis via ``analysis.analyze_page`` on a sonnet
    generator client (the 12 fixtures ship ``analysis=None`` per D-06, so there is
    no captured analysis to load — the harness must produce one) → render the gold
    label → ``judge_pair`` on a DISTINCT opus judge client. Collect each
    ``JudgeVerdict``; drop any pair where the generator OR the judge degraded to
    ``None`` (a missing calibration point, never a fabricated verdict). Then per
    judged dim, ``calibrate`` the judge vs human score/verdict arrays and emit a
    scrubbed ``{dim}: spearman=.. kappa=.. trusted=..`` line.

    The harness stays ADVISORY: a dim below ``min(spearman, kappa) >= 0.70`` is
    reported but never fails the test. The only assertion is that a calibration
    report was emitted for all four judged dimensions.
    """
    key = os.environ[ANTHROPIC_API_KEY_ENV]
    scrub = make_scrubber(key)

    def emit(line: str) -> None:
        # Every judge-lane sink is scrubbed (AUTH-04 / T-05.1-10): the key must not
        # survive into pytest's captured stdout.
        print(scrub(line))

    # Independent clients: sonnet generates, opus judges (a grader must not grade its
    # own output, AI-SPEC §3). The SDK is thread-safe but we run sequentially so the
    # JUDGE_RUBRIC prompt cache warms on pair 1.
    generator_client = anthropic.Anthropic(api_key=key)
    judge_client = anthropic.Anthropic(api_key=key)

    # Per-dimension parallel arrays of (judge_score, human_score, judge_call, human_call),
    # accumulated only over pairs that survived both the generator and the judge.
    judge_scores: dict[str, list[float]] = {d: [] for d in _JUDGED_DIMS}
    human_scores: dict[str, list[float]] = {d: [] for d in _JUDGED_DIMS}
    judge_calls: dict[str, list[str]] = {d: [] for d in _JUDGED_DIMS}
    human_calls: dict[str, list[str]] = {d: [] for d in _JUDGED_DIMS}

    judged_pairs = 0
    for name in _labeled_fixture_names():
        gold = load_gold(name)
        if gold is None:
            continue  # the fully-null-error-row carries no gold (D-06) — not a pair

        page = digest_page(name)
        digest_text = analysis.build_digest(page)  # the SAME digest the generator saw

        # RE-GENERATE the analysis (fixtures ship analysis=None — nothing to load).
        generated = analysis.analyze_page(generator_client, digest_text)
        if generated is None:
            emit(f"[drop] {name}: generator degraded to None")
            continue
        analysis_text = _render_analysis(
            generated.observation, generated.potential_cause, generated.suggested_optimization
        )

        gold_text = _render_analysis(
            gold.get("observation"),
            gold.get("potential_cause"),
            gold.get("suggested_optimization"),
        )

        verdict = judge_mod.judge_pair(
            judge_client,
            digest_text=digest_text,
            analysis_text=analysis_text,
            gold_label_text=gold_text,
        )
        if verdict is None:
            # A degraded judge call is a dropped pair, never a fabricated verdict.
            emit(f"[drop] {name}: judge_pair degraded to None")
            continue

        gold_dims = gold.get("dimensions") or {}
        for dim in _JUDGED_DIMS:
            jv = getattr(verdict, dim)
            hv = gold_dims.get(dim) or {}
            judge_scores[dim].append(float(jv.score))
            judge_calls[dim].append(jv.verdict)
            human_scores[dim].append(float(hv["score"]))
            human_calls[dim].append(hv["verdict"])
        judged_pairs += 1

    emit(f"calibration: {judged_pairs} judged pairs over {len(_JUDGED_DIMS)} dimensions")

    reports: dict[str, dict] = {}
    for dim in _JUDGED_DIMS:
        try:
            result = calibrate_mod.calibrate(
                judge_scores[dim], human_scores[dim], judge_calls[dim], human_calls[dim]
            )
            emit(
                f"{dim}: spearman={result['spearman']:.3f} "
                f"kappa={result['kappa']:.3f} trusted={result['trusted']}"
            )
        except StatisticsError as e:
            # Constant arrays (e.g. judge stamped one score) or <2 points have no
            # defined rank correlation. That is an UNCALIBRATED dim, not a pass —
            # report it advisory-only with trusted=False; it still counts as an
            # emitted report line for this dimension.
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
    assert judged_pairs > 0, "no calibration pairs survived — the judge harness produced no signal"
