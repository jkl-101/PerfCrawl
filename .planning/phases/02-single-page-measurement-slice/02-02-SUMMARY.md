---
phase: 02-single-page-measurement-slice
plan: 02
subsystem: measurement
tags: [aggregation, median, pydantic, statistics, finite-guard, tdd]

# Dependency graph
requires:
  - phase: 01-data-model-persistence-foundation
    provides: PageResult / MetricSample model + _no_bare_inp validator + finite-guard pattern (delta.py::_safe_abs)
provides:
  - "aggregate_samples(list[float|None]) -> MetricSample — inner median-of-N reducer with D-16 honest-empty + finite guard"
  - "aggregate_page_samples(list[PageResult]) -> PageResult — per-page cross-sample reducer the orchestrator calls once per page"
  - "_METRIC_SAMPLE_FIELDS constant: the canonical (lcp_ms, cls, inp_proxy_tbt_ms, ttfb_ms) tuple aggregated across samples"
affects: [02-03-orchestrator, 02-04-cli, 06-regression-flagging]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Pure-function reducer over Phase 1 model (no I/O, no side effects)"
    - "TDD RED→GREEN per task — two commits per task, one for failing test then one for implementation"
    - "Pydantic v2 model_copy(update=...) to preserve validators while replacing select fields"
    - "Finite-guard defense-in-depth above model-layer allow_inf_nan=False (mirrors delta.py::_safe_abs from Phase 1 LEARNINGS)"
    - "Honest-empty contract on D-16: aggregate_samples([]) returns MetricSample(median=None, samples=[]) — never raises"

key-files:
  created:
    - src/perfcrawl/aggregator.py
    - tests/test_aggregator.py
  modified: []

key-decisions:
  - "aggregate_page_samples uses first-canonical-sample policy for scalar/list/dict fields (perf_score, request_count, status_code, slowest_request_*, waterfall, diagnostics). Documented in function docstring; Phase 6 may revisit if cross-sample variance becomes interesting."
  - "Pydantic model_copy(update=...) chosen over re-constructing PageResult from scratch — preserves _no_bare_inp validator and model_config more cheaply (Phase 1 D-15 invariant cannot regress here by construction)."
  - "url_key identity check raises ValueError on mismatch — mixing different pages into one aggregate is a load-bearing bug, not a behavior."
  - "Aggregator stores the honest-empty MetricSample (median=None, samples=[]) when every sample's field was None, rather than collapsing to None on the PageResult — keeps the model shape uniform across pages and downstream consumers always see a MetricSample slot."

patterns-established:
  - "Pure aggregator over Phase 1 model: depends only on perfcrawl.models, no I/O, no orchestrator/normalizer dependency — can ship in parallel with 02-01."
  - "Finite-guard list comprehension preserves insertion order: `clean = [v for v in xs if v is not None and math.isfinite(v)]` is the canonical pattern across Phase 1 delta.py + Phase 2 aggregator.py."
  - "Test factory helper (_make_sample) keeps cross-sample PageResult tests readable without pulling the heavy conftest sample_run RunRecord fixture."

requirements-completed:
  - RUN-04

# Metrics
duration: ~20min
completed: 2026-05-29
---

# Phase 02 Plan 02: Median-of-N Aggregator Summary

**Pure-function median-of-N aggregator (`aggregate_samples` + `aggregate_page_samples`) with D-16 honest-empty contract, finite-guard defense-in-depth above MetricSample.allow_inf_nan=False, and Pydantic v2 model_copy preserving the Phase 1 _no_bare_inp validator.**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-05-29T07:25Z (approx)
- **Completed:** 2026-05-29T07:45Z
- **Tasks:** 2 (each with TDD RED→GREEN)
- **Files modified:** 2 (1 created src, 1 created test)
- **Commits:** 4 (2× RED + 2× GREEN)

## Accomplishments
- `aggregate_samples(list[float|None]) -> MetricSample` — the inner reducer: drops None + non-finite, returns `MetricSample(median=statistics.median(clean), samples=clean)` or honest-empty when nothing remains. Guards Pitfall 3 (`statistics.median([])` would raise).
- `aggregate_page_samples(list[PageResult]) -> PageResult` — the per-page cross-sample reducer: aggregates the four MetricSample fields (lcp_ms, cls, inp_proxy_tbt_ms, ttfb_ms) across samples, scalar/list/dict fields inherited from the first canonical sample, url_key identity check raises on mismatch, empty input raises.
- 19 named test cases in `tests/test_aggregator.py` — covers RUN-04 happy path, D-16 (honest empty, drop None, drop non-finite), Pitfall 3 (empty list), Pitfall 7 (median-of-1 + single-sample page passthrough), URL-mismatch raise, scalar-from-first-sample policy.
- Full suite green: 86 tests (67 Phase 1 + 19 aggregator), no regression.

## Task Commits

Each task was committed atomically with TDD RED→GREEN sequence:

1. **Task 1 RED: failing aggregator tests** — `ad4e931` (test)
2. **Task 1 GREEN: aggregate_samples with D-16 honest-empty + finite guard** — `6580aa6` (feat)
3. **Task 2 RED: failing aggregate_page_samples tests** — `fed41f2` (test)
4. **Task 2 GREEN: aggregate_page_samples cross-sample reducer** — `a93cf4a` (feat)

_TDD gate compliance: each task has a `test(02-02)` commit followed by a `feat(02-02)` commit; both RED commits collected failing tests before the corresponding implementation landed._

## Files Created/Modified
- `src/perfcrawl/aggregator.py` (114 lines) — Two public functions + a private `_METRIC_SAMPLE_FIELDS` constant; pure-function module depending only on `perfcrawl.models` + stdlib `math`/`statistics`.
- `tests/test_aggregator.py` (314 lines) — 19 named tests (parametrized inf/-inf/nan counts as 3 cases for the non-finite-drop test); private `_make_sample` factory at top of file for readable cross-sample PageResult construction.

## Decisions Made

- **First-canonical-sample policy for scalar fields**: `perf_score`, `request_count`, `total_bytes`, `status_code`, `slowest_request_*`, `waterfall`, `diagnostics` taken from `samples[0]`. Cold-cache cross-sample drift on these is negligible for Phase 2; Phase 6 may revisit if variance becomes interesting. Documented in `aggregate_page_samples` docstring.
- **`model_copy(update=...)` over reconstruction**: Pydantic v2's `model_copy` preserves `model_config` and validators (including `_no_bare_inp` from D-15) more cheaply than building a fresh PageResult from scratch. The labeled-proxy invariant cannot regress here by construction — no `inp` variable name appears in the aggregator code, only the literal `"inp_proxy_tbt_ms"` field key.
- **`url_key` mismatch raises**: Mixing different pages into one aggregate is a load-bearing bug (would silently merge two pages' metrics). Surfaced as an explicit `ValueError` rather than letting one page's metrics quietly overwrite another.
- **Empty input raises (not honest-empty)**: At the page level the orchestrator must never call with `[]` per D-14 (the per-sample loop always produces at least one PageResult per page); a raise here surfaces that orchestrator bug loudly. Contrast with `aggregate_samples([])` which returns honest-empty because D-16 expects `[]` to be a valid per-metric outcome when every sample's value for that metric failed.
- **Stored honest-empty MetricSample on all-None metric fields**: When every sample's `lcp_ms` was None, the aggregated PageResult's `lcp_ms` becomes `MetricSample(median=None, samples=[])` rather than `None`. Keeps the model shape uniform — downstream consumers (delta engine, future Sheets exporter) always see a MetricSample slot to inspect, not a sometimes-MetricSample-sometimes-None field.

## Deviations from Plan

None - plan executed exactly as written. The plan's verbatim function bodies, test naming, and TDD RED→GREEN commit sequence were followed without auto-fix.

## Issues Encountered

None. One observation worth recording for the next executor:
- The worktree was spawned at base `2c34e59` (Phase 1 ship commit) which predates the Phase 2 planning artifacts (`02-CONTEXT.md`, `02-RESEARCH.md`, `02-PATTERNS.md`, `02-02-PLAN.md`). Since `.planning/` is gitignored (per global CLAUDE.md `commit_docs: false` policy), those artifacts live only in the main repo working tree and were read from the main repo path during plan ingestion. The plan body was sufficient context to execute Task 1 + Task 2 without needing the surrounding artifacts inside the worktree.

## User Setup Required

None - no external service configuration required.

## Threat Model — Mitigation Verification

All four STRIDE threats in the plan's threat register are mitigated and test-covered:

| Threat ID | Mitigation | Test |
|-----------|------------|------|
| T-02-02-A (inf/nan input) | `math.isfinite()` drop before `statistics.median()` | `test_aggregator_drops_non_finite_samples` (parametrized over `[math.inf, -math.inf, math.nan]`) |
| T-02-02-B (empty list DoS) | `if not clean: return MetricSample(median=None, samples=[])` guard | `test_empty_samples_median_none` |
| T-02-02-C (URL mismatch) | `if len({s.url_key for s in samples}) > 1: raise ValueError` | `test_url_mismatch_raises` |
| T-02-02-D (INP labeling) | `model_copy(update={...})` preserves Phase 1 `_no_bare_inp` validator path; no `inp` variable name appears in aggregator code | Inherited from Phase 1 `test_inp_proxy_naming` + by-construction (literal `"inp_proxy_tbt_ms"` is the only INP-flavored string in the aggregator) |

ASVS coverage achieved: V5 (Input Validation — None/inf/nan filtering, empty-list guard, URL mismatch raise), V7 (Error Handling — deterministic empty rather than crash on Pitfall 3).

## Next Phase Readiness

- 02-03 (orchestrator) can now import `aggregate_page_samples` from `perfcrawl.aggregator` to collapse N per-sample PageResults into one aggregated PageResult after the per-sample measurement loop.
- The `_METRIC_SAMPLE_FIELDS` constant `(lcp_ms, cls, inp_proxy_tbt_ms, ttfb_ms)` is intentionally module-private; if 02-03 needs to enumerate aggregated metric fields it should import via `_METRIC_SAMPLE_FIELDS` (Python conventional private — accessible) or the aggregator can expose it under a public alias if usage justifies. Not blocking.
- No blockers for 02-03 or 02-04 — this plan is independent of the normalizer and CLI (per its `wave: 1`, `depends_on: []` frontmatter).

## Self-Check: PASSED

- src/perfcrawl/aggregator.py: FOUND (114 lines, exports `aggregate_samples` + `aggregate_page_samples`)
- tests/test_aggregator.py: FOUND (19 named tests, all green)
- ad4e931 (RED 1): FOUND in git log
- 6580aa6 (GREEN 1): FOUND in git log
- fed41f2 (RED 2): FOUND in git log
- a93cf4a (GREEN 2): FOUND in git log
- Full suite (`uv run pytest`): 86 passed, 0 failed
- grep guard `statistics\.(mean|fmean)` in aggregator.py: empty (D-16 invariant intact)

---
*Phase: 02-single-page-measurement-slice*
*Completed: 2026-05-29*
