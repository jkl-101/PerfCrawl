"""Stdlib-only judge calibration math (Phase 05.1).

The meta-eval that proves the LLM judge is trustworthy BEFORE any verdict it emits
is believed (Critical FM #1 — "an untested grader is noise dressed as signal").
Pure functions, no I/O, NO third-party deps — Pearson + Spearman via stdlib
``statistics.correlation`` (``method="ranked"`` for Spearman, Python 3.12+) and a
~6-line Cohen's kappa for chance-corrected PASS/FAIL agreement.

The trust gate is ``trusted = min(spearman, kappa) >= 0.70``: BOTH the rank
correlation on the 1-5 scores AND the chance-corrected agreement on the PASS/FAIL
calls must clear 0.70. Kappa is the half that exposes RUBBER-STAMPING (FM-10) — a
judge that stamps PASS on everything has high raw agreement but ~0 kappa, so the
gate stays False. Until both clear the bar, every verdict is advisory-only (D-03).
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import correlation

# The calibration bar: a judge verdict is trusted only when BOTH the score
# correlation and the chance-corrected agreement clear this (AI-SPEC §5 / D-03).
TRUST_THRESHOLD: float = 0.70


def cohen_kappa(a: Sequence, b: Sequence) -> float:
    """Chance-corrected agreement between two PASS/FAIL call sequences (stdlib only).

    ``po`` is the observed agreement; ``pe`` is the agreement expected by chance over
    the union of labels. Returns ``1.0`` when ``pe == 1`` (every call the same single
    label — perfect-and-certain agreement) to avoid a ``ZeroDivisionError``; this is
    the standard kappa convention for the degenerate all-agree case.
    """
    n = len(a)
    if n == 0:
        return 1.0
    po = sum(x == y for x, y in zip(a, b)) / n
    labels = set(a) | set(b)
    pe = sum((list(a).count(label) / n) * (list(b).count(label) / n) for label in labels)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def calibrate(
    judge_scores: Sequence[float],
    human_scores: Sequence[float],
    judge_calls: Sequence,
    human_calls: Sequence,
) -> dict:
    """Calibrate the judge against the human gold labels; return the trust verdict.

    Returns ``{"pearson", "spearman", "kappa", "trusted"}`` where ``trusted`` is
    ``min(spearman, kappa) >= 0.70`` — BOTH the ranked-score correlation and the
    chance-corrected PASS/FAIL agreement must clear the bar (kappa is what exposes
    rubber-stamping, FM-10). ``pearson`` is reported for context; the gate uses the
    rank correlation (robust to non-linear-but-monotone agreement) and kappa.
    """
    pearson = correlation(judge_scores, human_scores)
    spearman = correlation(judge_scores, human_scores, method="ranked")
    kappa = cohen_kappa(judge_calls, human_calls)
    return {
        "pearson": pearson,
        "spearman": spearman,
        "kappa": kappa,
        "trusted": min(spearman, kappa) >= TRUST_THRESHOLD,
    }
