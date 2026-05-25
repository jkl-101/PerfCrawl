---
phase: 01-data-model-persistence-foundation
plan: 02
subsystem: canonical record model + hybrid SQLite store
tags: [models, pydantic, sqlite, generated-columns, schema-version, round-trip, tdd]
requires:
  - "perfcrawl.canonical.canonical_key (Plan 01) — derives url_key on write (D-01)"
provides:
  - "models.PageResult / RunRecord / MetricSample / AnalysisResult / WaterfallEntry — the canonical record contract (D-13/14/17)"
  - "models.DirectionStatus StrEnum (six members) — consumed by Plan 03 RunDelta (D-11)"
  - "models.SCHEMA_VERSION constant — additive-only schema evolution (D-06)"
  - "store.init_db / write_run / read_run — hybrid TEXT-blob + generated-column SQLite store (criterion #1, D-07)"
  - "tests/conftest.py shared fixtures: sample_run, run_v1, run_v1_old_schema_json, delta_pair (Plan 03)"
  - "tests/fixtures/run_v1.json + run_v1_old_schema.json — full + old-schema RunRecord fixtures"
affects:
  - "Plan 03 (RunDelta engine) imports DirectionStatus + uses the delta_pair fixture"
  - "Phase 2 (measurement) normalizes into PageResult; fills MetricSample.samples[] + env slot"
  - "Phase 5 (AI) fills PageResult.analysis (AnalysisResult)"
  - "Phase 6 (exporters/regression) reads RunRecord via read_run()"
tech-stack:
  added: []
  patterns:
    - "hybrid store: full-fidelity JSON-TEXT blob (byte-identical) + GENERATED columns computed from the blob via json_extract (cannot drift)"
    - "forward-compat models: extra=ignore (newer blob -> older code) + Optional defaults (older blob -> newer code)"
    - "labeled-proxy invariant enforced at the model layer (model_validator rejects bare inp, D-15)"
    - "TDD RED (test commit) -> GREEN (impl commit) per task"
    - "url_key derived in the store via canonical_key() when caller leaves it blank (D-01)"
key-files:
  created:
    - "src/perfcrawl/models.py"
    - "src/perfcrawl/store.py"
    - "tests/conftest.py"
    - "tests/fixtures/run_v1.json"
    - "tests/fixtures/run_v1_old_schema.json"
    - "tests/test_models.py"
    - "tests/test_store.py"
  modified: []
decisions:
  - "Round-trip identity (A3) implemented as MODEL equality (read.model_dump() == original.model_dump()) AND byte preservation (record_json TEXT == model_dump_json()); both asserted, so either interpretation of criterion #1 holds."
  - "STORED-via-ALTER (D-07/Pitfall 2) only raises when the table holds rows on SQLite 3.50.4 — the test writes a run first (the realistic 'promote later' case); VIRTUAL is the supported promote path."
  - "Modeled a WaterfallEntry submodel (url/resource_type/size_bytes/timing_ms/status_code) for the METRIC-03 waterfall instead of list[dict] — typed + extra=ignore keeps it forward-compatible (plan left this to discretion)."
  - "v2 backend metrics (BACK-01..03) deliberately NOT modeled (D-16)."
metrics:
  duration_min: 3
  completed: "2026-05-25"
  tasks_completed: 2
  files_created: 7
  tests_passing: 33
---

# Phase 1 Plan 02: Canonical Record Model + Hybrid SQLite Store Summary

The keystone data contract is delivered: a typed `PageResult`/`RunRecord` model (with nested `MetricSample`, `AnalysisResult`, `WaterfallEntry`, the `DirectionStatus` enum, and a `SCHEMA_VERSION` constant) plus a hybrid SQLite store that writes a run and reads it back identically by model equality (criterion #1 / HIST-01), keeps old-schema runs comparable (criterion #3 / D-06/D-08), and exposes the canonical URL key + queried metrics as generated columns for the cross-run self-join — shipped TDD-first with 33 green tests.

## What Was Built

**Task 1 — Canonical record model + fixtures (TDD, D-13/14/15/17):**
- RED: `tests/test_models.py` (8 tests) written first, confirmed failing with `ModuleNotFoundError: No module named 'perfcrawl.models'` (commit f41b7b2).
- GREEN: `src/perfcrawl/models.py` (commit 336de4a) implements:
  - `SCHEMA_VERSION = 1` constant — additive-only evolution; bump only on additive change, never remove/rename (D-06).
  - `MetricSample(median: float|None, samples: list[float])` — first-class median + raw distribution so Phase 2 fills it without retrofitting (D-14).
  - `AnalysisResult(observation/potential_cause/suggested_optimization: str|None)` — the Phase-5 AI slot, nullable now (D-13).
  - `WaterfallEntry(url/resource_type/size_bytes/timing_ms/status_code)` — typed per-request waterfall row (METRIC-03).
  - `PageResult` — `url` (raw, never mutated, D-01) + `url_key` (derived canonical key) + the FULL nullable v1 superset: Lighthouse scores (perf/a11y/seo/best_practices), CWV (`lcp_ms`, `cls`, and the **labeled** `inp_proxy_tbt_ms` — explicitly a TBT-based lab proxy, **never a bare `inp`**, D-15), network facts (ttfb/request_count/total_bytes/status_code/slowest_request_url+ms), `waterfall` list, `diagnostics` blob, and `analysis`. A `@model_validator(mode="after")` rejects any bare-INP field name ever added in review (D-15 guard).
  - `RunRecord` — UUID `id`, tz-aware `started_at`, `target`, `schema_version`, `auth_used` (Phase 4), the stamped-environment slot (`chrome_version`/`lighthouse_version`/`throttling`/`emulation`, Phase 2 fills), and `pages` (D-17).
  - `DirectionStatus` StrEnum with all six members (improvement/regression/unchanged/new/removed/not_comparable) for Plan 03 (D-11).
  - `model_config = ConfigDict(extra="ignore")` on every model for forward-compat blob loads (D-06/D-08).
  - v2 backend metrics deliberately omitted (D-16).
- Fixtures + `tests/conftest.py`: `run_v1.json` (full RunRecord, 2 pages, populated metrics + `samples[]` + `analysis`), `run_v1_old_schema.json` (same run, later-phase fields absent — criterion #3), and shared fixtures `sample_run`, `run_v1`, `run_v1_old_schema_json`, plus a `delta_pair` two-run pair pre-built to exercise every D-09..D-12 edge case (improvement/regression/unchanged/previous==0/new/removed/not_comparable) so Plan 03's `test_delta` consumes it directly.

**Task 2 — Hybrid SQLite store (TDD, criteria #1/#3, D-06/07/08):**
- RED: `tests/test_store.py` (8 tests) written first, confirmed failing with `ModuleNotFoundError: No module named 'perfcrawl.store'` (commit f215d8f).
- GREEN: `src/perfcrawl/store.py` (commit 91a394f) implements:
  - `init_db(conn)` — STRICT `runs` + `page_results` tables, FK enforcement on, idempotent `IF NOT EXISTS` DDL, indexes `idx_pr_urlkey` + `idx_pr_run`.
  - `record_json` is RAW **TEXT** (the exact `model_dump_json()` bytes) — NOT JSONB — so the round-trip is byte-identical.
  - Promoted columns `url_key` + `perf_score` are `GENERATED ALWAYS AS (json_extract(record_json, '$...')) STORED` — computed from the blob, so they can never drift (D-07).
  - `write_run(conn, run)` — derives `url_key` via `canonical_key(page.url)` when the caller left it blank (D-01, raw `url` untouched); inserts the run blob + each page blob; parameterized `?` placeholders only.
  - `read_run(conn, run_id)` — `RunRecord.model_validate_json(...)` round-trip; `KeyError` on a missing id.
  - All SQL parameterized; explicit path; no dynamic table names (T-01-T/P).

## Verification Evidence

| Check | Command | Result |
|-------|---------|--------|
| Full suite (success criterion) | `uv run pytest -x -q` | **33 passed** |
| Round-trip identity (criterion #1 / HIST-01) | `pytest tests/test_store.py::test_round_trip_identity` | passed |
| Exact bytes preserved (TEXT) | `pytest tests/test_store.py::test_record_json_bytes_preserved` | passed |
| Old-schema load (criterion #3 / D-08) | `pytest tests/test_store.py::test_old_schema_loads` | passed |
| VIRTUAL-promote, STORED-ALTER rejected (D-07) | `pytest tests/test_store.py::test_promote_column_virtual` | passed |
| schema_version default + persist (D-06) | `pytest tests/test_models.py::test_schema_version_default` | passed |
| INP-proxy naming guard (D-15) | `pytest tests/test_models.py::test_inp_proxy_naming` | passed |
| Fixtures parse | `python -c "...RunRecord.model_validate_json(...)"` | `ok` |
| DirectionStatus six members (D-11) | `python -c "...len(list(DirectionStatus))==6"` | `ok` |
| Generated columns from blob | `grep "json_extract(record_json" store.py` | found |
| Parameterized SQL only (T-01-T) | `grep -nE 'execute\(f"|%|\.format\(' store.py` | none (clean) |
| No bare inp field (D-15) | `grep -Eq '\binp\b *:' models.py` | not found (clean) |
| forward-compat config (D-06/D-08) | `grep 'extra="ignore"' models.py` | found |
| Lint | `uv run ruff check src/ tests/` | All checks passed |

## TDD Gate Compliance

- Task 1 RED: `test(01-02): add failing tests for canonical record model` (f41b7b2) — confirmed failing before implementation.
- Task 1 GREEN: `feat(01-02): implement canonical record model + fixtures` (336de4a) — after RED.
- Task 2 RED: `test(01-02): add failing tests for hybrid SQLite store` (f215d8f) — confirmed failing before implementation.
- Task 2 GREEN: `feat(01-02): implement hybrid SQLite store` (91a394f) — after RED.
- No REFACTOR commits needed (both implementations were minimal and clean; ruff passed on first formatting).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug/test correctness] STORED-via-ALTER only raises on a non-empty table (SQLite 3.50.4)**
- **Found during:** Task 2 (`test_promote_column_virtual` failed: `DID NOT RAISE sqlite3.OperationalError`).
- **Issue:** The plan/research (D-07/Pitfall 2) asserts `ALTER TABLE ... ADD COLUMN ... STORED` raises "cannot add a STORED column." A direct probe on SQLite 3.50.4 showed the restriction is enforced **only when the table holds at least one row** (SQLite must backfill existing rows); on an empty table the STORED ALTER no-ops and succeeds. The `conn` fixture's `page_results` table was empty, so nothing raised.
- **Fix:** Updated `test_promote_column_virtual` to `write_run(conn, sample_run)` first — the realistic "promote a metric after runs exist" case — which is exactly when SQLite enforces the restriction. The D-07 invariant (use VIRTUAL when promoting later; STORED only at CREATE) is unchanged and still proven. Also tightened the `store.py` module docstring to document the version-accurate behavior (restriction triggers once rows exist; VIRTUAL is the supported promote path).
- **Files modified:** `tests/test_store.py`, `src/perfcrawl/store.py`
- **Commit:** 91a394f

**2. [Rule 1 — Lint] `timezone.utc` → `datetime.UTC` in test_models.py**
- **Found during:** Task 1 (ruff UP017).
- **Issue:** ruff flagged two `timezone.utc` usages as `UP017` (prefer the `datetime.UTC` alias on Python 3.11+).
- **Fix:** Switched the import to `from datetime import UTC, datetime` and updated both call sites.
- **Files modified:** `tests/test_models.py`
- **Commit:** 336de4a

### Discretionary choices (within the plan's stated latitude)

- **WaterfallEntry submodel** instead of `list[dict]` for the METRIC-03 waterfall — the plan explicitly allowed "model a small WaterfallEntry submodel or list[dict]." A typed submodel with `extra="ignore"` keeps it forward-compatible while giving Phase 2 a clear shape to fill.
- **Round-trip semantics (A3):** implemented BOTH model-equality (`test_round_trip_identity`) and exact-byte preservation (`test_record_json_bytes_preserved`), so criterion #1 holds under either intended interpretation.

## Authentication Gates

None — Phase 1 is an offline library layer; no auth, network, or secrets (auth is Phase 4).

## Interfaces Delivered (for Plan 03 / Phases 2-6)

```python
# perfcrawl.models
SCHEMA_VERSION: int                                   # D-06
class MetricSample(BaseModel): median: float|None; samples: list[float]      # D-14
class AnalysisResult(BaseModel): observation/potential_cause/suggested_optimization: str|None  # D-13
class WaterfallEntry(BaseModel): url/resource_type/size_bytes/timing_ms/status_code  # METRIC-03
class PageResult(BaseModel): url; url_key; <nullable v1 superset>; analysis     # D-13/14/15
class RunRecord(BaseModel): id; started_at; target; schema_version; auth_used; <env slot>; pages  # D-17
class DirectionStatus(StrEnum): IMPROVEMENT/REGRESSION/UNCHANGED/NEW/REMOVED/NOT_COMPARABLE  # D-11

# perfcrawl.store
def init_db(conn) -> None
def write_run(conn, run: RunRecord) -> None           # criterion #1 / HIST-01
def read_run(conn, run_id: str) -> RunRecord          # KeyError if absent

# tests/conftest.py fixtures
sample_run, run_v1, run_v1_json, run_v1_old_schema_json, delta_pair (previous, current)
```

## Known Stubs

None. The nullable later-phase fields (CWV/network/analysis/env slot) are the intentional D-13 forward-compat superset, populated by Phases 2/5, not stubs. `delta_pair` in conftest is a ready-to-use fixture for Plan 03, not shipped code.

## Self-Check: PASSED

- Artifacts verified on disk: `src/perfcrawl/models.py`, `src/perfcrawl/store.py`, `tests/conftest.py`, `tests/fixtures/run_v1.json`, `tests/fixtures/run_v1_old_schema.json`, `tests/test_models.py`, `tests/test_store.py` — all created this plan.
- Commits verified in git log: f41b7b2 (Task 1 RED), 336de4a (Task 1 GREEN), f215d8f (Task 2 RED), 91a394f (Task 2 GREEN).
- Contains checks: `class PageResult` + `inp_proxy_tbt_ms` + `extra="ignore"` in models.py; `GENERATED ALWAYS AS` + `json_extract(record_json` in store.py — all FOUND.
- Full suite green: 33 passed; ruff clean.
