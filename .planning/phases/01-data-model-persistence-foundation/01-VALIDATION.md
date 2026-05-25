---
phase: 1
slug: data-model-persistence-foundation
status: planned
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-25
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Seeded from `01-RESEARCH.md` § Validation Architecture. Per-Task rows are
> populated once plans exist (the planner assigns task IDs); `gsd-nyquist-auditor`
> closes any gaps post-planning.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (pin `>=8,<10`) |
| **Config file** | none yet — Wave 0 adds `[tool.pytest.ini_options]` to `pyproject.toml` |
| **Quick run command** | `uv run pytest -x -q` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~few seconds (pure unit tests against fixtures, no I/O beyond local SQLite) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -x -q` (whole suite is tiny/fast — run it all)
- **After every plan wave:** Run `uv run pytest`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** ~10 seconds

---

## Per-Task Verification Map

> Task IDs are bound to plans below (format: PLAN.TASK). Plans 01-03 created 2026-05-25.

| Criterion / Req | Task ID | Behavior | Automated Command | Status |
|-----------------|---------|----------|-------------------|--------|
| #4 / D-02..D-05 | 01.3 | tracking params dropped, query sorted, trailing slash stripped, fragment dropped, %-case normalized | `pytest tests/test_canonical.py -x` | ⬜ pending |
| #4 | 01.3 | same logical page → same key across run variants (self-join works) | `pytest tests/test_canonical.py::test_variants_collapse -x` | ⬜ pending |
| #4 / D-03,D-04 | 01.3 | distinct pages NOT over-merged (`?page=2`≠`?page=3`, www≠apex) | `pytest tests/test_canonical.py::test_no_over_merge -x` | ⬜ pending |
| #3 | 02.1 | `schema_version` defaults correctly + persists | `pytest tests/test_models.py::test_schema_version_default -x` | ⬜ pending |
| D-15 | 02.1 | bare `inp` field rejected; only labeled TBT proxy allowed | `pytest tests/test_models.py::test_inp_proxy_naming -x` | ⬜ pending |
| #1 / HIST-01 | 02.2 | Write run → read back identical (model equality) | `pytest tests/test_store.py::test_round_trip_identity -x` | ⬜ pending |
| #1 | 02.2 | Exact bytes preserved in `record_json` TEXT column | `pytest tests/test_store.py::test_record_json_bytes_preserved -x` | ⬜ pending |
| #3 / D-06,D-08 | 02.2 | Old-schema blob loads under newer model (missing → None) | `pytest tests/test_store.py::test_old_schema_loads -x` | ⬜ pending |
| D-07 | 02.2 | promote a metric via VIRTUAL generated column (STORED ALTER rejected) | `pytest tests/test_store.py::test_promote_column_virtual -x` | ⬜ pending |
| #2 | 03.1 | Polarity-driven direction (lower vs higher is better) | `pytest tests/test_delta.py::test_direction_by_polarity -x` | ⬜ pending |
| #2 / D-10 | 03.1 | `deltaPct` is None when previous==0 (no inf/NaN) | `pytest tests/test_delta.py::test_deltapct_zero_guard -x` | ⬜ pending |
| #2 / D-11 | 03.1 | new / removed / not_comparable emitted (removed never dropped) | `pytest tests/test_delta.py::test_edge_status_enum -x` | ⬜ pending |
| #2 / D-12 | 03.1 | `unchanged` == literal equality (no noise band) | `pytest tests/test_delta.py::test_unchanged_is_literal -x` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] Project scaffolding — `pyproject.toml` (uv), `src/perfcrawl/` package, `[tool.pytest.ini_options]`, `[tool.ruff]` (nothing exists — greenfield)
- [ ] Install pytest + pydantic + w3lib (w3lib behind a verify checkpoint)
- [ ] `tests/conftest.py` — shared fixtures (two-run delta pair, sample RunRecord)
- [ ] `tests/fixtures/run_v1.json` — full RunRecord, ≥2 pages, metrics + `samples[]` + `analysis` block
- [ ] `tests/fixtures/run_v1_old_schema.json` — same run with later-phase fields absent (criterion #3)
- [ ] `tests/test_models.py`, `tests/test_store.py`, `tests/test_delta.py`, `tests/test_canonical.py` — test files for the map above

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| w3lib dependency vetting | D-04 canonicalization | New runtime dep flagged by researcher (slopcheck unavailable) | `checkpoint:human-verify` — confirm `w3lib` is the intended canonicalization lib before install |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
