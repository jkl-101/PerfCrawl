---
status: complete
phase: 01-data-model-persistence-foundation
source:
  - 01-01-SUMMARY.md
  - 01-02-SUMMARY.md
  - 01-03-SUMMARY.md
started: 2026-05-26T08:25:17Z
updated: 2026-05-26T08:58:00Z
---

## Current Test
<!-- OVERWRITE each test - shows where we are -->

[testing complete]

## Tests

### 1. Fresh Install Smoke Test
expected: |
  `uv sync` succeeds; `uv run python -c "import perfcrawl"` exits 0
  with no ModuleNotFoundError. No `perfcrawl` console script is
  installed (library-only by design — the auto-generated CLI was
  removed in 5aa4222).
result: pass
observed: |
  `uv sync` resolved 14 packages, checked 13 (clean); then
  `uv run python -c "import perfcrawl; print(perfcrawl.__version__)"`
  printed `0.1.0` with no traceback.

### 2. Full Test Suite Green
expected: |
  `uv run pytest -x -q` prints "67 passed" and exits 0 (17 canonical
  + 16 model/store + 6 delta cases reported by per-plan SUMMARYs;
  CLAUDE.md status table claims 67 total green).
result: pass
observed: |
  `67 passed in 0.05s` — full suite green; -x didn't trip (no failures).

### 3. Lint Clean
expected: |
  `uv run ruff check src/ tests/` prints "All checks passed!" and
  exits 0. No warnings, no auto-fixable findings.
result: pass
observed: |
  `All checks passed!` — ruff clean on src/ and tests/.

### 4. Canonical URL Key Collapses Tracking Variants
expected: |
  Run: `uv run python -c "from perfcrawl.canonical import canonical_key; print(canonical_key('https://Example.COM:443/p/?utm_source=fb&a=1'))"`
  Expected output: `https://example.com/p?a=1` — host lowercased, default :443 stripped (D-02), trailing slash dropped (D-03), `utm_source` removed from the query (D-04 via TRACKING_PARAM_DENYLIST), legitimate `a=1` preserved.
result: pass
observed: |
  Stdout: `https://example.com/p?a=1` — exact match. All four D-02..D-05 transforms confirmed in a single round-trip.

### 5. Canonical Key Never Raises on Garbage Input
expected: |
  Run: `uv run python -c "from perfcrawl.canonical import canonical_key; print(repr(canonical_key('not a url'))); print(repr(canonical_key('')))"`
  Expected: two repr strings printed and exit 0 — no Python traceback, no ValueError, no ZeroDivisionError (T-01-01 DoS guard).
result: pass
observed: |
  Stdout: `'not%20a%20url'` then `''` — two repr strings, no traceback. Garbage input percent-encoded by w3lib, empty input returned as empty (deterministic, no raise).

### 6. SQLite Round-Trip Identity
expected: |
  Run: `uv run pytest tests/test_store.py::test_round_trip_identity tests/test_store.py::test_record_json_bytes_preserved -x -q`
  Expected: `2 passed` — proves `write_run` → `read_run` returns a model-equal RunRecord AND the on-disk TEXT blob is byte-identical to `model_dump_json()` (criterion #1 / HIST-01).
result: pass
observed: |
  `2 passed in 0.03s` — model-equality round-trip and byte-identical TEXT blob both green.

### 7. Old-Schema Fixture Loads on New Code
expected: |
  Run: `uv run pytest tests/test_store.py::test_old_schema_loads -x -q`
  Expected: `1 passed` — confirms `tests/fixtures/run_v1_old_schema.json` (a RunRecord with later-phase fields absent) parses into a current `RunRecord` via `extra="ignore"` + Optional defaults (criterion #3 / D-06/D-08 forward-compat).
result: pass
observed: |
  `1 passed in 0.03s` — old-schema fixture parses cleanly under the current model (forward-compat seam holds).

### 8. RunDelta Direction Tracks Polarity, Not Delta Sign
expected: |
  Run: `uv run pytest tests/test_delta.py::test_direction_by_polarity -x -q`
  Expected: `1 passed` — proves `perf_score` 0.70→0.85 classifies as IMPROVEMENT (higher-is-better) while `lcp_ms` 2000→2600 classifies as REGRESSION (lower-is-better); mirror cases confirm direction is read from `METRIC_POLARITY`, never hardcoded (D-09).
result: pass
observed: |
  `1 passed in 0.01s` — polarity-driven direction holds for both signed cases and the mirror.

### 9. RunDelta Edge Cases (zero-prev / new / removed / not_comparable)
expected: |
  Run: `uv run pytest tests/test_delta.py::test_deltapct_zero_guard tests/test_delta.py::test_edge_status_enum tests/test_delta.py::test_unchanged_is_literal -x -q`
  Expected: `3 passed` — `safe_pct(_, 0)` returns None with `delta_abs` still computed (D-10); `/new` emits NEW, `/removed` is PRESENT in output as REMOVED (not silently dropped — D-11/Pitfall 4); ttfb 300→300 = UNCHANGED but 100.0→100.1 = REGRESSION (no Phase-6 variance gate pre-empted, D-12).
result: pass
observed: |
  `3 passed in 0.01s` — zero-prev guard, new/removed/not_comparable enum coverage, and literal-unchanged-not-variance-gated all green.
verified_by: claude (ran in sandbox)

### 10. Registry Is the Single Source of Truth
expected: |
  Run (a): `uv run python -c "from perfcrawl.registry import TRACKING_PARAM_DENYLIST, METRIC_POLARITY, Polarity; print(len(TRACKING_PARAM_DENYLIST), len(METRIC_POLARITY), list(Polarity))"`
  Expected (a): `14 <N> [<Polarity.LOWER_IS_BETTER: 'lower_is_better'>, <Polarity.HIGHER_IS_BETTER: 'higher_is_better'>]` (14 tracking params; non-empty polarity dict; both enum members).
  Run (b): `grep -R "utm_source" src/perfcrawl/ | grep -v registry.py`
  Expected (b): no output — denylist never inlined at a call site (D-04 one-editable-place).
result: pass
observed: |
  (a) Stdout: `14 11 [<Polarity.LOWER_IS_BETTER: 'lower'>, <Polarity.HIGHER_IS_BETTER: 'higher'>]` — 14 tracking params (✓), 11 polarity entries (non-empty ✓), both enum members present (✓). Minor expected-string drift: actual StrEnum values are `'lower'`/`'higher'` (not `'lower_is_better'`/`'higher_is_better'` as the expected text guessed) — structural truth holds; only the documented value-string in the expected was a guess.
  (b) Grep returned no matches (exit=1) — utm_source not referenced outside registry.py. Denylist is genuinely one-editable-place.
verified_by: claude (ran in sandbox)

### 11. No Bare INP Field (D-15 Labeled-Proxy Guard)
expected: |
  Run (a): `uv run pytest tests/test_models.py::test_inp_proxy_naming -x -q`
  Expected (a): `1 passed` — `@model_validator(mode="after")` rejects any bare-INP field.
  Run (b): `grep -nE '\binp\b\s*:' src/perfcrawl/models.py`
  Expected (b): no output — the lab proxy is named `inp_proxy_tbt_ms`, never bare `inp`.
result: pass
observed: |
  (a) `1 passed in 0.00s` — bare-INP rejection holds.
  (b) Grep returned no matches (exit=1) — no bare `inp:` field declaration in models.py.
verified_by: claude (ran in sandbox)

### 12. Parameterized SQL Only (T-01-T Injection Guard)
expected: |
  Run: `grep -nE 'execute\(f"|\.execute\("[^"]*%|\.format\(' src/perfcrawl/store.py`
  Expected: no output — no f-string SQL, no `%`-formatting, no `.format()` building SQL. All SQL uses `?` placeholders only.
result: pass
observed: |
  Grep returned no matches (exit=1) — store.py has no f-string SQL, no `%`-formatted SQL, no `.format()`-built SQL. T-01-T injection surface clean.
verified_by: claude (ran in sandbox)

## Summary

total: 12
passed: 12
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none yet]
