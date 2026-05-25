"""The canonical typed record model — Phase 1 data contract (D-06/D-13/D-14/D-15/D-17).

This is the keystone every downstream component targets: Phase 2 normalizes
measurements into ``PageResult``, Phase 5 fills the ``analysis`` slot, Phase 6
reads ``RunRecord``, and Plan 03's RunDelta engine imports ``DirectionStatus``
from here. The whole reason this module exists is "one contract that never needs
retrofitting" — so the full known v1 superset is modeled NOW with later-phase
fields nullable (D-13), and schema evolution is additive-only (D-06).

Key forward-compat mechanics:

  - every model sets ``model_config = ConfigDict(extra="ignore")`` so a
    *newer*-schema blob loads under *older* code (unknown keys dropped), and the
    ``Optional[...] = None`` defaults let an *older*-schema blob load under
    *newer* code (missing fields default to ``None``) — D-06/D-08.
  - ``SCHEMA_VERSION`` is bumped only on an additive change; fields are never
    removed or renamed (D-06).
  - the INP field is the explicitly-labeled TBT lab proxy (``inp_proxy_tbt_ms``),
    never a bare ``inp`` that could be mistaken for real field INP — enforced by
    a model validator at the model layer (D-15).
  - v2 backend metrics (BACK-01..03) are deliberately NOT modeled (D-16);
    additive evolution adds them for free once the security spike lands.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Bump ONLY on an additive change; never remove or rename a field (D-06).
# Persisted on every RunRecord so old-schema runs stay comparable (criterion #3).
SCHEMA_VERSION = 1

# Field names that would be (or be mistaken for) a bare field-INP value. The
# only allowed INP-flavored field is the explicitly-labeled lab proxy
# ``inp_proxy_tbt_ms`` (a TBT-based proxy). Enforced on PageResult (D-15).
_FORBIDDEN_INP_FIELDS = frozenset({"inp", "inp_ms", "interaction_to_next_paint"})


class DirectionStatus(StrEnum):
    """Per-metric regression status (D-11). Defined here; consumed by Plan 03.

    ``new``/``removed``/``not_comparable`` cover the cross-run edge cases:
    a page present in only one run, or a metric present on only one side.
    """

    IMPROVEMENT = "improvement"
    REGRESSION = "regression"
    UNCHANGED = "unchanged"
    NEW = "new"
    REMOVED = "removed"
    NOT_COMPARABLE = "not_comparable"


class MetricSample(BaseModel):
    """A single metric's aggregated median plus its raw sample distribution (D-14).

    First-class median-of-N storage so Phase 2 (``--samples N``) fills the
    distribution without retrofitting the model. Both fields are nullable/empty
    now; Phase 1 only makes the shape storable.
    """

    model_config = ConfigDict(extra="ignore")

    median: float | None = None
    samples: list[float] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """The Phase-5 AI analysis slot — Observation / Cause / Optimization (D-13).

    Nullable now; the Anthropic structured-output step fills it in Phase 5.
    """

    model_config = ConfigDict(extra="ignore")

    observation: str | None = None
    potential_cause: str | None = None
    suggested_optimization: str | None = None


class WaterfallEntry(BaseModel):
    """One network request in the per-page waterfall (METRIC-03, D-13).

    Filled in Phase 2 from Playwright/CDP network capture. All fields nullable;
    extra keys are ignored so a richer Phase-2 shape still loads here.
    """

    model_config = ConfigDict(extra="ignore")

    url: str | None = None
    resource_type: str | None = None
    size_bytes: int | None = None
    timing_ms: float | None = None
    status_code: int | None = None


class PageResult(BaseModel):
    """A single page's measured result — the full nullable v1 superset (D-13).

    ``url`` is the URL as measured and is NEVER mutated (D-01); ``url_key`` is the
    derived canonical key (set via ``canonical_key()`` in the store/caller, D-01)
    used to self-join the same logical page across runs. Every later-phase metric
    field is nullable/defaulted so the contract never needs retrofitting.
    """

    model_config = ConfigDict(extra="ignore")  # newer-schema blobs load under older code

    # --- identity (set now; the only required fields) ---
    url: str  # D-01: as-measured, never mutated
    url_key: str  # D-01: derived canonical key (canonical_key())

    # --- Lighthouse category scores (METRIC-01, higher-is-better) ---
    perf_score: float | None = None
    a11y_score: float | None = None
    seo_score: float | None = None
    best_practices_score: float | None = None

    # --- Core Web Vitals (METRIC-02) — median + distribution per metric (D-14) ---
    lcp_ms: MetricSample | None = None
    cls: MetricSample | None = None
    # D-15: explicitly a TBT-based LAB PROXY for INP — NEVER a bare `inp` field
    # that could be mistaken for real (field) INP. Enforced by _no_bare_inp below.
    inp_proxy_tbt_ms: MetricSample | None = None

    # --- network facts (METRIC-04) → existing Google Sheet columns ---
    ttfb_ms: MetricSample | None = None
    request_count: int | None = None
    total_bytes: int | None = None
    status_code: int | None = None
    slowest_request_url: str | None = None
    slowest_request_ms: float | None = None

    # --- network waterfall (METRIC-03) + diagnostics blob (METRIC-05) ---
    waterfall: list[WaterfallEntry] = Field(default_factory=list)
    diagnostics: dict | None = None

    # --- Phase-5 AI analysis slot (D-13) ---
    analysis: AnalysisResult | None = None

    @model_validator(mode="after")
    def _no_bare_inp(self) -> "PageResult":
        """Reject any bare-INP field at the model layer (D-15).

        ``extra="ignore"`` already drops a stray ``inp`` key in an input blob;
        this guard additionally fails fast if a forbidden field is ever ADDED to
        the model in review, so the labeled-proxy invariant can't silently regress.
        """
        present = _FORBIDDEN_INP_FIELDS & set(type(self).model_fields)
        if present:
            raise ValueError(
                f"Field(s) {sorted(present)} forbidden — INP must be the labeled "
                "TBT lab proxy 'inp_proxy_tbt_ms', never a bare INP value (D-15)."
            )
        return self


class RunRecord(BaseModel):
    """One audit run: identity + metadata + the per-page results (D-17).

    Serialized via ``model_dump_json()`` into the store's ``record_json`` TEXT
    column and read back via ``model_validate_json()`` for round-trip identity
    (criterion #1 / HIST-01). The stamped-environment slot is defined now and
    nullable until Phase 2 populates it.
    """

    model_config = ConfigDict(extra="ignore")

    id: UUID = Field(default_factory=uuid4)  # D-17: run id
    started_at: datetime  # D-17: tz-aware ISO-8601 timestamp
    target: str  # D-17: site / seed URL
    schema_version: int = SCHEMA_VERSION  # D-06 / criterion #3
    auth_used: bool | None = None  # D-17: Phase 4

    # --- stamped-environment slot (D-17) — defined now, Phase 2 fills ---
    chrome_version: str | None = None
    lighthouse_version: str | None = None
    throttling: dict | None = None
    emulation: str | None = None

    pages: list[PageResult] = Field(default_factory=list)
