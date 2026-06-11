"""Lane-1 offline unit tests for the judge + calibration engine (Phase 05.1).

Free, no-key, no-network (NOT ``@pytest.mark.llm``): these pin the *contracts* the
paid Plan-04 harness wires against — the JUDGE_RUBRIC freeze/floor + band-number
freeze guard, the JudgeVerdict/DimensionVerdict schema invariants, judge_pair's
degrade-to-None, and the stdlib calibration math (Cohen's kappa edges + the
``min(spearman, kappa) >= 0.70`` trust gate that exposes rubber-stamping).

``judge`` and ``calibrate`` import as top-level modules: ``tests/eval`` has no
``__init__.py``, so pytest's default prepend import mode puts it on ``sys.path``
(the same seam ``test_digest`` / ``test_grounding`` rely on).
"""

from __future__ import annotations

import anthropic
import calibrate
import httpx
import judge
import pytest
from judge import DimensionVerdict, JudgeVerdict

from perfcrawl.provider import AnthropicProvider


# --- A tiny, key-free, offline judge-client double (FakeAnthropic-style) ---------
class _FakeJudgeParsed:
    def __init__(self, parsed_output: JudgeVerdict | None) -> None:
        self.parsed_output = parsed_output


class _FakeJudgeMessages:
    def __init__(self, result: JudgeVerdict | None, error: BaseException | None) -> None:
        self._result = result
        self._error = error
        self.call_count = 0

    def parse(self, **kwargs):
        self.call_count += 1
        if self._error is not None:
            raise self._error
        return _FakeJudgeParsed(self._result)


class _FakeJudgeClient:
    """A canned ``anthropic.Anthropic`` double — never touches the network or a key."""

    def __init__(
        self, *, result: JudgeVerdict | None = None, error: BaseException | None = None
    ) -> None:
        self.messages = _FakeJudgeMessages(result, error)


def _verdict() -> JudgeVerdict:
    dv = DimensionVerdict(verdict="PASS", score=5, rationale="grounded in the digest numbers")
    return JudgeVerdict(
        causal_plausibility=dv,
        threshold_correctness=dv,
        actionability=dv,
        prioritization=dv,
    )


# --- judge.py: JUDGE_RUBRIC freeze / floor ---------------------------------------
def test_judge_rubric_frozen_and_clears_cache_floor() -> None:
    """JUDGE_RUBRIC is a module-level str past the ~1024-token cache floor (Pitfall 2)."""
    assert isinstance(judge.JUDGE_RUBRIC, str), "JUDGE_RUBRIC must be a module-level str"
    # ~1024-token Opus-4.8 cache floor at ~4 chars/token => ~4096 chars.
    assert len(judge.JUDGE_RUBRIC) >= 4096, (
        f"JUDGE_RUBRIC is {len(judge.JUDGE_RUBRIC)} chars (< 4096); a too-short rubric "
        "silently never caches — bulk it past the floor."
    )


def test_judge_rubric_band_numbers_frozen_verbatim() -> None:
    """The CWV band substrings appear verbatim — the judge-rubric sibling of the
    constants<->analysis.RUBRIC freeze, so a band drift in this third copy fails Lane-1.
    """
    for band in ("<= 2500 ms", "> 4000 ms", "<= 0.1", "> 0.25"):
        assert band in judge.JUDGE_RUBRIC, (
            f"CWV band substring {band!r} missing from JUDGE_RUBRIC — band drift would let "
            "the judge and the constants/pre-flag disagree on a threshold (FM-5)."
        )


# --- judge.py: schema invariants -------------------------------------------------
def test_judge_verdict_requires_all_four_subverdicts() -> None:
    """A missing dimension is a Pydantic ValidationError, not a silent gap."""
    dv = DimensionVerdict(verdict="PASS", score=4, rationale="ok")
    with pytest.raises(pydantic_validation_error()):
        JudgeVerdict(  # type: ignore[call-arg]
            causal_plausibility=dv,
            threshold_correctness=dv,
            actionability=dv,
            # prioritization intentionally omitted
        )


def test_dimension_verdict_rejects_out_of_range_score() -> None:
    """score is bounded 1-5; 0 and 6 are ValidationErrors."""
    err = pydantic_validation_error()
    with pytest.raises(err):
        DimensionVerdict(verdict="PASS", score=6, rationale="too high")
    with pytest.raises(err):
        DimensionVerdict(verdict="FAIL", score=0, rationale="too low")


def test_dimension_verdict_rejects_overlong_rationale() -> None:
    """rationale is capped at 400 chars (anti-verbosity, auditable)."""
    with pytest.raises(pydantic_validation_error()):
        DimensionVerdict(verdict="PASS", score=3, rationale="x" * 401)


# --- judge.py: judge_pair success + degrade-to-None ------------------------------
def test_judge_pair_returns_verdict_on_success() -> None:
    client = _FakeJudgeClient(result=_verdict())
    out = judge.judge_pair(
        AnthropicProvider(client), digest_text="d", analysis_text="a", gold_label_text="g"
    )
    assert isinstance(out, JudgeVerdict)
    assert out.causal_plausibility.verdict == "PASS"
    assert client.messages.call_count == 1


def test_judge_pair_degrades_to_none_on_none_parse() -> None:
    client = _FakeJudgeClient(result=None)
    assert (
        judge.judge_pair(
            AnthropicProvider(client), digest_text="d", analysis_text="a", gold_label_text="g"
        )
        is None
    )


def test_judge_pair_degrades_to_none_on_apierror() -> None:
    err = anthropic.APIError("boom", httpx.Request("POST", "http://x"), body=None)
    client = _FakeJudgeClient(error=err)
    assert (
        judge.judge_pair(
            AnthropicProvider(client), digest_text="d", analysis_text="a", gold_label_text="g"
        )
        is None
    )
    # No app-level retry: the SDK already exhausted max_retries before raising.
    assert client.messages.call_count == 1


# --- calibrate.py: Cohen's kappa edges -------------------------------------------
def test_cohen_kappa_identical_is_one() -> None:
    assert calibrate.cohen_kappa(["PASS", "PASS", "FAIL"], ["PASS", "PASS", "FAIL"]) == 1.0


def test_cohen_kappa_all_agree_single_label_no_zero_division() -> None:
    """pe == 1 (every call the same single label) returns 1.0, never ZeroDivisionError."""
    assert calibrate.cohen_kappa(["PASS", "PASS", "PASS"], ["PASS", "PASS", "PASS"]) == 1.0


def test_cohen_kappa_partial_agreement_below_one() -> None:
    k = calibrate.cohen_kappa(["PASS", "PASS", "FAIL", "FAIL"], ["PASS", "FAIL", "FAIL", "FAIL"])
    assert 0.0 < k < 1.0


# --- calibrate.py: the trust gate ------------------------------------------------
def test_calibrate_returns_full_dict_and_trusts_perfect_input() -> None:
    out = calibrate.calibrate(
        judge_scores=[1, 2, 3, 4, 5],
        human_scores=[1, 2, 3, 4, 5],
        judge_calls=["FAIL", "FAIL", "PASS", "PASS", "PASS"],
        human_calls=["FAIL", "FAIL", "PASS", "PASS", "PASS"],
    )
    assert set(out) == {"pearson", "spearman", "kappa", "trusted"}
    assert out["pearson"] == pytest.approx(1.0)
    assert out["spearman"] == pytest.approx(1.0)
    assert out["kappa"] == pytest.approx(1.0)
    assert out["trusted"] is True


def test_calibrate_untrusts_when_correlation_below_bar() -> None:
    """Negatively-correlated scores fail the gate even if PASS/FAIL agreement is perfect."""
    out = calibrate.calibrate(
        judge_scores=[1, 2, 3, 4, 5],
        human_scores=[5, 4, 3, 2, 1],
        judge_calls=["FAIL", "FAIL", "PASS", "PASS", "PASS"],
        human_calls=["FAIL", "FAIL", "PASS", "PASS", "PASS"],
    )
    assert out["spearman"] < 0.70
    assert out["trusted"] is False


def test_calibrate_kappa_exposes_rubber_stamp() -> None:
    """A judge that stamps PASS on everything has high raw agreement but ~0 kappa, so
    trusted is False — kappa is what catches rubber-stamping (FM-10), not raw agreement.
    """
    out = calibrate.calibrate(
        judge_scores=[5, 5, 5, 4, 3],
        human_scores=[5, 5, 5, 4, 1],
        judge_calls=["PASS", "PASS", "PASS", "PASS", "PASS"],  # rubber stamp
        human_calls=["PASS", "PASS", "PASS", "PASS", "FAIL"],  # gold has a FAIL
    )
    # Raw agreement is 4/5 = 0.8 (looks high), but kappa collapses to ~0.
    assert out["kappa"] < 0.70
    assert out["trusted"] is False


# --- calibrate.py: the degenerate-label guard (CR-01) ----------------------------
def test_is_calibratable_rejects_single_call_label() -> None:
    """All-PASS human calls can't exercise kappa — uncalibratable even with score spread."""
    assert calibrate.is_calibratable([5, 4, 3], ["PASS", "PASS", "PASS"]) is False


def test_is_calibratable_rejects_constant_scores() -> None:
    """A constant score array has no defined rank correlation — uncalibratable."""
    assert calibrate.is_calibratable([5, 5, 5], ["PASS", "FAIL", "PASS"]) is False


def test_is_calibratable_accepts_both_labels_and_score_spread() -> None:
    assert calibrate.is_calibratable([5, 1, 4], ["PASS", "FAIL", "PASS"]) is True


def test_calibrate_short_circuits_on_uncalibratable_all_pass() -> None:
    """The exact CR-01 shape — all-PASS gold, near-constant scores — must NOT return a
    confident verdict. It reports uncalibratable=True + trusted=False, never a fake
    kappa=1.0 trusted=True (the bug) nor a misleading trusted=False from a crash.
    """
    out = calibrate.calibrate(
        judge_scores=[5, 5, 5, 5],
        human_scores=[5, 5, 5, 5],
        judge_calls=["PASS", "PASS", "PASS", "PASS"],
        human_calls=["PASS", "PASS", "PASS", "PASS"],
    )
    assert out["uncalibratable"] is True
    assert out["trusted"] is False
    assert out["spearman"] is None and out["kappa"] is None


def pydantic_validation_error():
    """The Pydantic ValidationError class (imported lazily so the helper reads cleanly)."""
    import pydantic

    return pydantic.ValidationError
