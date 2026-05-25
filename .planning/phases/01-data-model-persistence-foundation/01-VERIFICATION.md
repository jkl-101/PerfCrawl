---
phase: 01-data-model-persistence-foundation
verified: 2026-05-25T00:00:00Z
status: passed
score: 4/4 must-haves verified
overrides_applied: 0
re_verification: null
gaps: []
deferred: []
human_verification: []
---

# Phase 1: Data Model & Persistence Foundation — Verification Report

**Phase Goal:** A stable, typed canonical result model and a SQLite run store exist, so every downstream component (measurement, AI, exporters, regression) targets one contract that never needs retrofitting.
**Verified:** 2026-05-25
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A run (run id, timestamp, per-page results) can be written to a local SQLite store and read back identically. | VERIFIED | `test_round_trip_identity` passes; spot-check of `write_run`/`read_run` confirms `back.model_dump() == run.model_dump()` in a real `:memory:`-equivalent tempfile DB. |
| 2 | Given two stored runs for the same site, RunDelta records (current/previous/deltaAbs/deltaPct/direction) can be computed per page per metric against fixture data. | VERIFIED | `test_direction_by_polarity`, `test_deltapct_zero_guard`, `test_edge_status_enum`, `test_unchanged_is_literal`, `test_flat_list_shape` all pass. Inline spot-check confirmed improvement, regression, removed, new, and deltaPct-zero-guard paths all produce correct output. |
| 3 | The PageResult/RunRecord model carries a schemaVersion so runs stored under an older schema remain comparable after fields are added. | VERIFIED | `SCHEMA_VERSION = 1` in `models.py`; `test_schema_version_default` + `test_old_schema_loads` pass. `run_v1_old_schema.json` has only `url`/`url_key` per page — loads cleanly with all metric fields defaulting to `None`. Spot-check confirmed. |
| 4 | Page identity uses a canonical, normalized URL key so the same page matches across runs. | VERIFIED | `test_variants_collapse`, `test_no_over_merge`, and 17 total canonical tests all pass. Spot-check confirmed `canonical_key("https://Example.com/Path/?utm_source=x&b=2&a=1#frag") == canonical_key("https://example.com/Path?a=1&b=2")` and `?page=2 != ?page=3`. |

**Score:** 4/4 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/perfcrawl/canonical.py` | `canonical_key(url)` — w3lib wrapper + D-02..D-05 rules | VERIFIED | 67 lines; imports `TRACKING_PARAM_DENYLIST` from registry; `_strip_default_port` wrapper; malformed-input guard. |
| `src/perfcrawl/registry.py` | `TRACKING_PARAM_DENYLIST` (D-04) + `Polarity` enum + `METRIC_POLARITY` dict (D-09) | VERIFIED | 63 lines; 14-entry denylist; 11-entry polarity dict; `Polarity` StrEnum. |
| `src/perfcrawl/models.py` | `PageResult`, `RunRecord`, `MetricSample`, `AnalysisResult`, `DirectionStatus`, `SCHEMA_VERSION`, INP-proxy validator | VERIFIED | 183 lines; all six `DirectionStatus` members present; `SCHEMA_VERSION = 1`; `model_validator` rejects bare `inp`; `extra="ignore"` on all models; `WaterfallEntry` submodel included. |
| `src/perfcrawl/store.py` | `init_db`, `write_run`, `read_run`; `GENERATED ALWAYS AS` columns; TEXT blob | VERIFIED | 117 lines; `GENERATED ALWAYS AS (json_extract(record_json, '$.url_key')) STORED`; `GENERATED ALWAYS AS (json_extract(record_json, '$.perf_score')) STORED`; parameterized queries only; `conn.commit()` after write. |
| `src/perfcrawl/delta.py` | `compute_deltas`, `RunDelta`, `classify`, `safe_pct` | VERIFIED | 183 lines; `from perfcrawl.registry import METRIC_POLARITY`; `from perfcrawl.models import DirectionStatus` (not redefined); no noise/threshold literal. |
| `tests/conftest.py` | Shared fixtures: `sample_run`, `run_v1`, `run_v1_old_schema_json`, `delta_pair` | VERIFIED | 158 lines; `delta_pair` pre-covers improvement/regression/unchanged/previous==0/new/removed/not_comparable. |
| `tests/fixtures/run_v1.json` | Full `RunRecord`, >=2 pages, metrics + `samples[]` + analysis block | VERIFIED | 2 pages with full Lighthouse scores, CWV, waterfall, diagnostics, and analysis objects. |
| `tests/fixtures/run_v1_old_schema.json` | Same run shape, later-phase fields absent | VERIFIED | Contains only `id`, `started_at`, `target`, `schema_version`, and bare `url`/`url_key` per page — no metric fields. |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/perfcrawl/canonical.py` | `src/perfcrawl/registry.py` | `from perfcrawl.registry import TRACKING_PARAM_DENYLIST` | WIRED | Line 27 of canonical.py — confirmed present. |
| `src/perfcrawl/canonical.py` | `w3lib.url` | `canonicalize_url`, `url_query_cleaner` | WIRED | Line 25 of canonical.py. |
| `src/perfcrawl/store.py` | `src/perfcrawl/models.py` | `RunRecord.model_dump_json()` on write; `RunRecord.model_validate_json()` on read | WIRED | Lines 95–96 and 116 of store.py — both paths confirmed. |
| `src/perfcrawl/models.py` | `src/perfcrawl/canonical.py` | `canonical_key()` sets `url_key` when blank (D-01) | WIRED | In `store.py` `write_run()` at line 85: `page.url_key = canonical_key(page.url)`. (Derivation is in the store, not the model, which is correct per D-01.) |
| `src/perfcrawl/store.py` | `page_results.url_key` | `GENERATED ALWAYS AS (json_extract(record_json,'$.url_key'))` | WIRED | DDL at line 55; `test_url_key_generated` passes. |
| `src/perfcrawl/delta.py` | `src/perfcrawl/registry.py` | `from perfcrawl.registry import METRIC_POLARITY` | WIRED | Line 36 of delta.py — confirmed. |
| `src/perfcrawl/delta.py` | `src/perfcrawl/models.py` | `DirectionStatus`, `RunRecord`, `PageResult`, `MetricSample` | WIRED | Line 35 of delta.py. |

---

## Data-Flow Trace (Level 4)

Not applicable — Phase 1 is a pure library layer with no web server, UI rendering, or live data pipeline. All data flows are exercised through the test suite against fixture files, which is the correct data source for this phase.

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Round-trip write/read returns identical model | `write_run(conn, run); read_run(conn, id).model_dump() == run.model_dump()` | True | PASS |
| Old-schema fixture (bare url/url_key only) loads with None metric fields | `RunRecord.model_validate_json(old_schema_json).pages[0].perf_score is None` | True | PASS |
| SC-4 same-page URL variants collapse | `canonical_key("https://Example.com/Path/?utm_source=x&b=2&a=1#frag") == canonical_key("https://example.com/Path?a=1&b=2")` | True | PASS |
| SC-4 distinct pages kept distinct | `canonical_key("https://example.com/?page=2") != canonical_key("https://example.com/?page=3")` | True | PASS |
| SC-2 perf_score 0.70->0.85 = improvement | `compute_deltas(current, previous)` → `direction == IMPROVEMENT` | True | PASS |
| SC-2 lcp_ms 2000->2600 = regression (lower-is-better) | `compute_deltas(current, previous)` → `direction == REGRESSION` | True | PASS |
| SC-2 removed page emitted (not dropped) | `direction == REMOVED` present in flat list | True | PASS |
| SC-2 new page emitted | `direction == NEW` present in flat list | True | PASS |
| SC-2 `delta_pct` when previous==0 | `safe_pct(1024.0, 0) is None` | True | PASS |
| Full test suite | `uv run pytest -x -q` | 39 passed in 0.03s | PASS |

---

## Probe Execution

No conventional probe scripts (`scripts/*/tests/probe-*.sh`) declared or found for this phase. The test suite (`uv run pytest`) serves as the functional proof. Exit 0, 39 passed.

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|---------|
| HIST-01 | 01-02-PLAN.md | Tool persists every run (run id, timestamp, per-page results) to a local store | SATISFIED | `write_run`/`read_run` in `store.py`; `test_round_trip_identity` passes; REQUIREMENTS.md marks it `[x] Complete`. |

Plans 01-01 and 01-03 carry `requirements: []` — no additional requirement IDs to check. No orphaned HIST-01 mapping exists (only Phase 1 is mapped to it in REQUIREMENTS.md).

---

## Anti-Patterns Found

None. Full scan of `src/perfcrawl/` produced:

- Zero TBD / FIXME / XXX markers
- Zero TODO / HACK / PLACEHOLDER markers
- Zero stub return patterns (`return null`, `return {}`, `return []`)
- Zero f-string or `%`/`.format` SQL in `store.py`

---

### Advisory Note: CR-01 Transaction Wrapper (from code review)

The code review flagged that `write_run` lacks an explicit `with conn:` transaction wrapper — it does two `conn.execute()` blocks (run row + N page rows) followed by a single `conn.commit()`. Under Python's `sqlite3` module with default `isolation_level = ""`, the module auto-issues a `BEGIN` before the first DML statement and the single `conn.commit()` at line 103 commits the entire batch atomically. A process crash between the run insert and the page inserts would result in a rolled-back implicit transaction, leaving the DB clean. This is functionally correct for the current single-threaded, local-CLI use case.

The review finding is accurate as a quality concern: an explicit `with conn:` context manager would make the atomicity intent visible and would automatically roll back on exception without relying on implicit behavior. This is a code-quality warning, not a success-criterion blocker — SC-1 round-trip integrity holds under normal operation and is proven by the test suite.

---

## Human Verification Required

None. Phase 1 is a pure offline Python library layer with no UI, no network calls, no external services, and no visual output. All success criteria are verifiable programmatically and have been verified.

---

## Gaps Summary

No gaps. All four phase success criteria are satisfied by substantive, wired, fully-tested implementations. HIST-01 is covered. No orphaned requirements exist.

---

_Verified: 2026-05-25_
_Verifier: Claude (gsd-verifier)_
