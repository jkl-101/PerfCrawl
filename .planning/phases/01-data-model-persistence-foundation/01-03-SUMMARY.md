---
phase: 01-data-model-persistence-foundation
plan: 03
subsystem: regression delta engine (RunDelta)
tags: [delta, regression, polarity, pydantic, tdd, criterion-2]
requires:
  - "perfcrawl.registry.METRIC_POLARITY + Polarity (Plan 01) — derives direction (D-09)"
  - "perfcrawl.models.RunRecord / PageResult / MetricSample / DirectionStatus (Plan 02) — consumed/returned"
  - "tests/conftest.py delta_pair fixture (Plan 02) — the (previous, current) two-run pair"
provides:
  - "delta.compute_deltas(current_run, previous_run) -> list[RunDelta] — flat per-(page, metric) deltas (criterion #2)"
  - "delta.RunDelta (BaseModel: url_key, metric, current, previous, delta_abs, delta_pct, direction)"
  - "delta.classify(metric, current, previous, *, page_in_current, page_in_previous) -> DirectionStatus helper"
  - "delta.safe_pct(current, previous) -> float|None — deltaPct zero/None guard (D-10)"
affects:
  - "Phase 6 (exporters/regression) layers the variance gate ON TOP of this raw direction (D-12) and groups/thresholds the flat RunDelta list"
tech-stack:
  added: []
  patterns:
    - "polarity-driven direction: lower/higher-is-better read from the single METRIC_POLARITY registry, never hardcoded at the call site (D-09)"
    - "DirectionStatus reused from models, never redefined in delta.py (one enum, one source of truth)"
    - "deltaPct zero/None guard returns None (no inf/NaN/ZeroDivisionError); deltaAbs still computed (D-10)"
    - "page-presence flags distinguish whole-page new/removed from one-sided-metric not_comparable (D-11)"
    - "union-of-pages iteration so removed pages are emitted, never silently dropped (D-11/Pitfall 4)"
    - "MetricSample fields compared by their median scalar"
    - "raw direction only — unchanged is literal equality; no variance gate (Phase 6, D-12)"
    - "TDD RED (test commit) -> GREEN (impl commit)"
key-files:
  created:
    - "src/perfcrawl/delta.py"
    - "tests/test_delta.py"
  modified: []
decisions:
  - "classify() takes explicit page_in_current/page_in_previous keyword flags so a one-sided METRIC on a both-runs page (not_comparable) is distinguishable from a whole PAGE present in only one run (new/removed) — both surface as a None scalar otherwise (D-11)."
  - "compute_deltas skips metrics absent on BOTH sides of a page (no fabricated spurious rows); it emits a row only when at least one side has the metric — matches the fixture and the flat-list-shape test."
  - "Iteration order is deterministic: previous-run page order first (so removed pages keep position), then current-only pages — keeps RunDelta output stable for Phase 6 grouping."
  - "Doc text avoids the literal tokens noise/threshold/tolerance to keep delta.py grep-clean for the D-12 acceptance check while still documenting that the variance gate is Phase 6."
metrics:
  duration_min: 3
  completed: "2026-05-25"
  tasks_completed: 1
  files_created: 2
  tests_passing: 39
---

# Phase 1 Plan 03: Polarity-Driven RunDelta Engine Summary

The final Phase 1 capability slice is delivered: `compute_deltas(current_run, previous_run)` produces a flat `list[RunDelta]` (current / previous / delta_abs / delta_pct / direction) per page per metric from two stored `RunRecord`s, with `direction` derived from the central `METRIC_POLARITY` registry (D-09), `delta_pct` guarded against `previous == 0` (D-10), cross-run edge cases routed through the `DirectionStatus` enum (D-11), and `unchanged` as literal equality with no variance gate (D-12) — shipped TDD-first against the `delta_pair` fixture, with the full suite now at 39 green.

## What Was Built

**Task 1 — Polarity-driven RunDelta engine (TDD, criterion #2, D-09/D-10/D-11/D-12):**

- RED: `tests/test_delta.py` (6 tests) written first, confirmed failing with `ModuleNotFoundError: No module named 'perfcrawl.delta'` (commit d20f6bb).
- GREEN: `src/perfcrawl/delta.py` (commit 7c94ec6) implements:
  - `RunDelta(BaseModel)` with `url_key`, `metric`, `current`, `previous`, `delta_abs`, `delta_pct`, `direction` — the exact D-10 field set; `direction` typed as the `DirectionStatus` **imported from `perfcrawl.models`** (never redefined here).
  - `classify(metric, current, previous, *, page_in_current, page_in_previous)` — derives the status from `METRIC_POLARITY` (imported from `perfcrawl.registry`, never hardcoded — D-09). Order: whole-page presence (`new`/`removed`) → unknown/one-sided metric (`not_comparable`) → literal equality (`unchanged`) → polarity (`improvement`/`regression`).
  - `safe_pct(current, previous)` — returns `None` when `previous in (None, 0)` or `current is None` (guards inf/NaN/ZeroDivisionError, D-10); else the signed percentage.
  - `_scalar(page, metric)` — extracts the comparable scalar; `MetricSample` fields are compared by their `median`, plain scalars used directly, missing page/field → `None`.
  - `compute_deltas(current_run, previous_run)` — builds `url_key`→`PageResult` maps for both runs, iterates the **union** of `url_key`s (previous order first so removed pages keep position, then current-only pages), and for each page iterates the comparable metric names (keys of `METRIC_POLARITY`) present on at least one side, emitting one `RunDelta` per `(url_key, metric)`. Returns a **flat list** (Open Q2). RAW direction only — no variance gate.

The test suite asserts every must-have truth against the pre-built `delta_pair` fixture (no conftest changes were needed — the fixture already covered improvement / regression / unchanged / `previous==0` / new / removed / one-sided-metric):
- `test_direction_by_polarity` — perf_score (higher-is-better) 0.70→0.85 = improvement; lcp_ms (lower-is-better) 2000→2600 = regression; plus a constructed **mirror** case proving direction tracks polarity, not the sign of the delta.
- `test_deltapct_zero_guard` + `test_deltapct_normal_case_computes` — `previous==0` → `delta_pct is None` with `delta_abs == 1024`; a non-zero baseline yields the real percentage (guard isn't blanket).
- `test_edge_status_enum` — `/new` → new (previous=None); `/removed` → removed (current=None, **present in output**); `request_count` on `/` (current-only metric on a both-runs page) → not_comparable.
- `test_unchanged_is_literal` — ttfb 300→300 = unchanged (delta_abs 0.0); 100.0→100.1 on a lower-is-better metric = regression, NOT unchanged (Phase 6 variance gate not pre-empted).
- `test_flat_list_shape` — flat `list[RunDelta]`, no duplicate `(url_key, metric)` rows, all four fixture pages represented, every emitted metric known to `METRIC_POLARITY`.

## Verification Evidence

| Check | Command | Result |
|-------|---------|--------|
| Full suite (Phase 1 gate) | `uv run pytest -x -q` | **39 passed** |
| Polarity-derived direction (criterion #2 / D-09) | `pytest tests/test_delta.py::test_direction_by_polarity -x` | exit 0 |
| deltaPct zero guard (D-10) | `pytest tests/test_delta.py::test_deltapct_zero_guard -x` | exit 0 |
| new/removed/not_comparable (D-11) | `pytest tests/test_delta.py::test_edge_status_enum -x` | exit 0 |
| unchanged literal, no variance gate (D-12) | `pytest tests/test_delta.py::test_unchanged_is_literal -x` | exit 0 |
| Polarity NOT hardcoded (D-09) | `grep -q "from perfcrawl.registry import METRIC_POLARITY" src/perfcrawl/delta.py` | found |
| Reuses models import | `grep -q "from perfcrawl.models import" src/perfcrawl/delta.py` | found |
| DirectionStatus NOT redefined | `grep -q "class DirectionStatus" src/perfcrawl/delta.py` | not found (correct) |
| No variance-gate literal (D-12) | `grep -Eiq "noise|threshold|tolerance|noise_band" src/perfcrawl/delta.py` | not found (correct) |
| Lint | `uv run ruff check src/ tests/` | All checks passed |

## TDD Gate Compliance

- Task 1 RED: `test(01-03): add failing tests for RunDelta engine` (d20f6bb) — confirmed failing (ModuleNotFoundError) before implementation.
- Task 1 GREEN: `feat(01-03): implement polarity-driven RunDelta engine` (7c94ec6) — after RED.
- No REFACTOR commit needed (implementation was minimal and clean; ruff passed after docstring line-length cleanup, which was folded into the GREEN commit).

## Threat Model Compliance

The plan's STRIDE register (T-01-D, T-01-N) is satisfied:
- **T-01-D (DoS on malformed numerics):** `safe_pct` guards division (`previous in (None, 0)` → `None`); `classify` handles `None` on either side. Covered by `test_deltapct_zero_guard` + `test_edge_status_enum`.
- **T-01-N (regression mislabeled as improvement):** direction is derived from the single `METRIC_POLARITY` registry, never hardcoded at the call site (grep-asserted); `test_direction_by_polarity` asserts both polarities and a mirror case.

No new security surface introduced — `compute_deltas` is pure in-memory computation over already-validated typed models (no I/O, no network, no untrusted external input).

## Deviations from Plan

None — plan executed as written. The plan allowed extending `conftest.py` "if the pair needs the specific cases"; the existing `delta_pair` fixture already exercised every D-09..D-12 case, so no fixture change was necessary. One within-latitude implementation choice is recorded in frontmatter `decisions`: `classify()` takes explicit `page_in_current`/`page_in_previous` flags to distinguish whole-page new/removed from a one-sided metric (not_comparable), since both otherwise present as a `None` scalar.

## Authentication Gates

None — Phase 1 is an offline library layer; no auth, network, or secrets (auth is Phase 4).

## Interfaces Delivered (for Phase 6)

```python
# perfcrawl.delta
class RunDelta(BaseModel):
    url_key: str; metric: str
    current: float | None; previous: float | None
    delta_abs: float | None; delta_pct: float | None
    direction: DirectionStatus            # imported from perfcrawl.models

def compute_deltas(current_run: RunRecord, previous_run: RunRecord) -> list[RunDelta]
def classify(metric, current, previous, *, page_in_current=True, page_in_previous=True) -> DirectionStatus
def safe_pct(current: float | None, previous: float | None) -> float | None
```

## Known Stubs

None. `delta.py` is fully wired against the real registry + models; the flat `RunDelta` list is consumed directly by tests and is the stable seam Phase 6 will group/threshold.

## Self-Check: PASSED

- Artifacts verified on disk: `src/perfcrawl/delta.py`, `tests/test_delta.py` — both created this plan.
- Commits verified in git log: d20f6bb (Task 1 RED), 7c94ec6 (Task 1 GREEN).
- Source assertions: `from perfcrawl.registry import METRIC_POLARITY` and `from perfcrawl.models import` FOUND in delta.py; `class DirectionStatus` and `noise|threshold|tolerance|noise_band` NOT found (correct).
- Full suite green: 39 passed; ruff clean.
