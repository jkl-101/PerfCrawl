"""Gold-label round-trip + per-dimension coverage (Phase 5.1 reference dataset).

The reference dataset's `tests/fixtures/digests/*.json` ship inputs-only
(`analysis: null`). Phase 5.1 authors a human **gold label** onto each prose-bearing
fixture — the expected/acceptable Observation / Cause / Optimization plus a per-judged-
dimension (6-9) PASS/FAIL + 1-5 score that the LLM-judge is calibrated against.

The gold label rides as a NEW top-level ``gold`` key. Because ``PageResult`` carries
``model_config = ConfigDict(extra="ignore")`` (models.py:118), that key is dropped on
validation — so ``digest_page`` / ``build_digest`` never see it and the existing
byte-stability contract (``test_digest.py``) is preserved. ``load_gold(name)``
(conftest) reads the raw fixture JSON to recover the dropped ``gold`` object.

Authoring workflow (D-01 hybrid): Claude drafts the 6 straightforward fixtures
(healthy-all-green, slow-lcp, high-cls, high-tbt, high-ttfb, heavy); the developer
HAND-AUTHORS the 5 calibration-critical trap fixtures (adversarial-number, green-trap,
stack-bait, partial-null, multi-problem) so the model never leads on the boundary /
phantom-cause / prioritization cases (Critical FM #2). Those 5 trap params therefore
stay RED until the Task-2 developer checkpoint is satisfied — that is the intended
state at the end of Task 1, not a regression.
"""

import json
from pathlib import Path

import pytest

from perfcrawl import analysis
from perfcrawl.models import PageResult

DIGEST_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "digests"

ALL_FIXTURES = sorted(p.stem for p in DIGEST_DIR.glob("*.json"))

# The single deterministic null-error row stays ``analysis=None`` with NO authored
# prose (D-06): ``null`` IS the honest "no data" signal, so it carries no gold label.
NULL_ROW = "fully-null-error-row"
PROSE_FIXTURES = [name for name in ALL_FIXTURES if name != NULL_ROW]

# The four subjective dimensions the LLM-judge scores and the gold label encodes
# (AI-SPEC §5 dims 6-9 / JudgeVerdict).
JUDGED_DIMENSIONS = (
    "causal_plausibility",   # dim 6 (Critical / FM-1)
    "threshold_correctness",  # dim 7 (High)
    "actionability",          # dim 8 (High)
    "prioritization",         # dim 9 (Medium)
)


def _fixture_text(name: str) -> str:
    return (DIGEST_DIR / f"{name}.json").read_text()


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_fixture_round_trips_through_digest_page(name: str) -> None:
    """Every fixture still parses into a valid inputs-only ``PageResult``.

    The extra top-level ``gold`` key is dropped by ``extra="ignore"``, so the
    ``analysis`` slot stays ``None`` and ``build_digest`` consumes the page with no
    error — the gold label never reaches the digest (byte-stability preserved).
    """
    page = PageResult.model_validate_json(_fixture_text(name))
    assert page.analysis is None, "fixtures are inputs-only; gold rides beside, not in, analysis"

    digest = analysis.build_digest(page)
    assert isinstance(digest, str) and digest, "build_digest must return non-empty text"
    assert "gold" not in digest, "the gold label must never leak into the digest/prompt"


@pytest.mark.parametrize("name", PROSE_FIXTURES)
def test_prose_fixture_has_valid_gold(name: str, load_gold) -> None:
    """Each prose-bearing fixture exposes a gold label: O/C/O text + dims 6-9.

    The 5 trap fixtures are developer-hand-authored in Task 2 (Critical FM #2); their
    params stay RED until that checkpoint is satisfied.
    """
    gold = load_gold(name)
    assert gold is not None, f"{name} must carry an authored gold label"

    for field in ("observation", "potential_cause", "suggested_optimization"):
        value = gold.get(field)
        assert isinstance(value, str) and value.strip(), f"{name}.{field} must be non-empty prose"

    dims = gold.get("dimensions")
    assert isinstance(dims, dict), f"{name}.dimensions must be a map of the 4 judged dims"
    for dim in JUDGED_DIMENSIONS:
        sub = dims.get(dim)
        assert isinstance(sub, dict), f"{name}.dimensions.{dim} missing"
        assert sub.get("verdict") in {"PASS", "FAIL"}, f"{name}.{dim}.verdict must be PASS|FAIL"
        score = sub.get("score")
        assert isinstance(score, int) and not isinstance(score, bool), (
            f"{name}.{dim}.score must be an int"
        )
        assert 1 <= score <= 5, f"{name}.{dim}.score must be 1-5"


def test_null_error_row_unlabeled(load_gold) -> None:
    """fully-null-error-row stays analysis=None with no authored prose (D-06)."""
    page = PageResult.model_validate_json(_fixture_text(NULL_ROW))
    assert page.analysis is None
    assert load_gold(NULL_ROW) is None, "the null error row carries no gold label (D-06)"
