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


def is_calibratable(human_scores: Sequence[float], human_calls: Sequence) -> bool:
    """Can this human label set even *produce* a meaningful trust verdict? (CR-01).

    Returns False for a DEGENERATE label set — one that defeats the math before any
    judge quality can be measured:

      - ``human_calls`` carries a single label (e.g. every gold dimension is PASS):
        Cohen's kappa cannot expose rubber-stamping, because a judge that stamps
        that same label everywhere lands in the ``pe == 1`` branch and scores 1.0.
      - ``human_scores`` is constant: ``statistics.correlation(method="ranked")``
        raises ``StatisticsError`` on a constant input, so spearman is undefined.

    Either way the trust gate would be MEANINGLESS rather than merely failed — it
    must report "uncalibratable", never a confident ``trusted=False`` (or, worse, a
    fake ``trusted=True``). This is exactly the bug CR-01 caught: an all-PASS,
    near-constant gold set silently made the gate a permanent always-deny dressed up
    as a real check. The Phase-05.1 dataset pairs each gold (PASS) with an anti-gold
    (FAIL) precisely so this returns True; ``test_judge_unit`` /
    ``test_gold_labels`` assert that invariant for free, offline.
    """
    return len(set(human_calls)) >= 2 and len(set(human_scores)) >= 2


def cohen_kappa(a: Sequence, b: Sequence) -> float:
    """Chance-corrected agreement between two PASS/FAIL call sequences (stdlib only).

    ``po`` is the observed agreement; ``pe`` is the agreement expected by chance over
    the union of labels. Returns ``1.0`` when ``pe == 1`` (every call the same single
    label — perfect-and-certain agreement) to avoid a ``ZeroDivisionError``; this is
    the standard kappa convention for the degenerate all-agree case.
    """
    if len(a) != len(b):
        # zip() would truncate to the shorter sequence while the divisor below stays
        # len(a) and the .count() terms range over all of b — silently producing a
        # garbage kappa rather than an error. A length mismatch means the caller's
        # parallel arrays drifted (e.g. a partial-drop bug upstream); fail loud.
        raise ValueError(f"cohen_kappa: a and b must be equal length ({len(a)} != {len(b)})")
    n = len(a)
    if n == 0:
        return 1.0
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
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

    DEGENERATE GUARD (CR-01): if the human labels are uncalibratable — a single
    PASS/FAIL label or a constant score array — the kappa/spearman math cannot yield
    a meaningful verdict, so this returns ``trusted=False`` with
    ``uncalibratable=True`` and ``pearson/spearman/kappa = None`` rather than a
    misleading number. That stops the gate from masquerading as a working check on a
    label set that can't exercise it. Calibratable inputs return the four-key dict
    unchanged (no ``uncalibratable`` key), so existing callers are unaffected.
    """
    if not is_calibratable(human_scores, human_calls):
        return {
            "pearson": None,
            "spearman": None,
            "kappa": None,
            "trusted": False,
            "uncalibratable": True,
        }
    pearson = correlation(judge_scores, human_scores)
    spearman = correlation(judge_scores, human_scores, method="ranked")
    kappa = cohen_kappa(judge_calls, human_calls)
    return {
        "pearson": pearson,
        "spearman": spearman,
        "kappa": kappa,
        "trusted": min(spearman, kappa) >= TRUST_THRESHOLD,
    }
