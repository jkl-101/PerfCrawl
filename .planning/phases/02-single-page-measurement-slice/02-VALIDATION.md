---
phase: 2
slug: single-page-measurement-slice
status: approved
nyquist_compliant: true
wave_0_complete: true
created: 2026-05-28
audited: 2026-05-29
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution. Derived from `02-RESEARCH.md` § "Validation Architecture".

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8+ (already installed via `pyproject.toml` dev group; Phase 1 used the same) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` — `testpaths=tests`, `pythonpath=src`, `addopts=-ra` |
| **Quick run command** | `uv run pytest -x` (unit + integration; e2e excluded by marker) |
| **Full suite command** | `uv run pytest` |
| **E2E suite command** | `uv run pytest -m e2e` (requires Node 22.19+, Chrome, network) |
| **Estimated runtime** | ~5–10 seconds for unit+integration once subprocess is stubbed |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest -x` (the per-task quick gate)
- **After every plan wave:** Run `uv run pytest` (full non-e2e suite)
- **Before `/gsd-verify-work`:** Full suite green AND one-shot manual e2e: `perfcrawl measure https://example.com --samples 1 --json`
- **Max feedback latency:** ~10 seconds for non-e2e

---

## Per-Requirement Verification Map

Plans will assign tasks; this table maps each phase requirement to its
automated proof, derived from the RESEARCH.md mapping. Plan-level
task IDs (`02-MM-NN`) are filled in by the planner.

| Req ID | Behavior | Test Type | Automated Command | File Exists |
|--------|----------|-----------|-------------------|-------------|
| METRIC-01 | LH category scores (perf/a11y/seo/bp) map onto `PageResult` | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_category_scores_mapped -x` | ✅ COVERED |
| METRIC-02 | LCP/CLS/TBT map onto `MetricSample` fields; TBT writes `inp_proxy_tbt_ms` (never bare `inp`) | unit + property | `uv run pytest tests/test_normalizer.py::test_cwv_mapping -x` | ✅ COVERED |
| METRIC-03 | LH 13 waterfall keys (`rendererStartTime`/`networkRequestTime`/`networkEndTime`) build `WaterfallEntry` list correctly | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_waterfall_timing_uses_lh13_keys -x` | ✅ COVERED |
| METRIC-04 | TTFB, request_count, total_bytes, status_code, slowest_request_url/ms derived correctly | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_network_facts -x` | ✅ COVERED |
| METRIC-05 | `diagnostics` contains only `score < 1` audits; passing/meta audits excluded | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_diagnostics_curated -x` | ✅ COVERED |
| RUN-01 | Worker `--form-factor=mobile\|desktop` produces correct LH config | unit | `uv run pytest tests/test_worker.py -k "worker_argv_passthrough" -x` | ✅ COVERED |
| RUN-02 | Throttling config recorded in `RunRecord.throttling` from worker stamp | integration (fixture) | `uv run pytest tests/test_orchestrator.py::test_runrecord_metadata_stamping -x` | ✅ COVERED |
| RUN-03 | Cold cache — each sample uses a fresh `BrowserContext` | integration (mocked Playwright) | `uv run pytest tests/test_orchestrator.py::test_fresh_context_per_sample -x` | ✅ COVERED |
| RUN-04 | `--samples N` → `MetricSample.median` over successful samples, raw `samples[]` preserved | unit | `uv run pytest tests/test_aggregator.py -k "median_of_n or median_of_one or empty_samples_median_none" -x` | ✅ COVERED |
| OUT-03 | Raw LH JSON + HTML written to `output/<run_id>/lighthouse/<page-slug>.{json,html}` | integration | `uv run pytest tests/test_output.py::test_raw_lh_artifacts_on_disk -x` | ✅ COVERED |
| OUT-04 | Flat CSV (locked column order) + full JSON written to `output/<run_id>/result.{csv,json}` | integration | `uv run pytest tests/test_output.py -k "csv_column_order or json_round_trip" -x` | ✅ COVERED |
| CLI-01 | `perfcrawl measure URL` exits 0/1/2 correctly; `--json` → stdout valid JSON; progress on stderr | integration (Typer CliRunner) | `uv run pytest tests/test_cli.py -x` | ✅ COVERED |

### Locked-Decision Coverage (must also be tested)

| Decision | Behavior | Automated Command |
|----------|----------|-------------------|
| D-07 / IN-02 | `page_slug("https://x.com/a/%2e%2e/b")` returns sanitized stem with no `..`, no slash | `uv run pytest tests/test_slug.py::test_no_path_traversal_in_slug -x` |
| D-10 | LH JSON with `lighthouseVersion="14.0.0"` raises `ValueError` in normalizer | `uv run pytest tests/test_normalizer.py::test_version_gate_rejects_major_drift -x` |
| D-13 | LH JSON with main-document `statusCode=404` → `PageResult` with `status_code=404`, metric fields null | `uv run pytest tests/test_normalizer.py::test_partial_result_on_non_2xx -x` |
| D-14 | Worker timeout triggers exactly one retry; double-timeout drops the sample | `uv run pytest tests/test_orchestrator.py::test_timeout_retry_then_drop -x` |
| D-15 | Exit 0 on success-or-partial; 1 on Typer/usage error; 2 on all-samples-failed | `uv run pytest tests/test_cli.py -k "exit_zero or exit_one or exit_two" -x` |
| D-16 | `MetricSample(median=None, samples=[])` when all samples fail for a metric | `uv run pytest tests/test_aggregator.py::test_empty_samples_median_none -x` |

---

## Wave 0 Requirements

- [x] `tests/fixtures/lighthouse/studyhalo-home-200.json` — real LH 13.3.0 JSON capture from a stable URL (e.g. `https://example.com`). THE source-of-truth fixture for normalizer tests.
- [x] `tests/fixtures/lighthouse/studyhalo-404.json` — non-2xx fixture for D-13.
- [x] `tests/fixtures/lighthouse/version-drift-14.json` — synthetic fixture with `lighthouseVersion="14.0.0"` to exercise D-10 gate.
- [x] `tests/conftest.py` — e2e marker registered in `pyproject.toml [tool.pytest.ini_options]` (`addopts = "-ra -m 'not e2e'"`, `markers = ["e2e: …"]`); functionally equivalent to conftest registration.
- [x] `tests/test_normalizer.py` — covers METRIC-01..05, D-10, D-11, D-13 (11 tests, all green).
- [x] `tests/test_slug.py` — covers D-07 IN-02 path-traversal sanitization + edge cases (8 tests, all green).
- [x] `tests/test_aggregator.py` — covers RUN-04, D-16 median-of-N math (17 tests, all green).
- [x] `tests/test_worker.py` — Python-side worker subprocess contract (argv passthrough, stdout JSON shape, exit-code semantics) (16 tests, all green).
- [x] `tests/test_orchestrator.py` — covers RUN-03 fresh-context-per-sample, D-14 timeout+retry, D-15 measurement-error path. Mocks Playwright + subprocess (20 tests, all green).
- [x] `tests/test_output.py` — covers OUT-03 raw artifact layout, OUT-04 CSV column order + JSON round-trip (14 tests, all green).
- [x] `tests/test_cli.py` — covers CLI-01 + D-15 exit codes + `--json` machine output via Typer's `CliRunner` (15 tests, all green).
- [x] `tests/test_e2e.py` — `@pytest.mark.e2e`. Deselected from default suite (1 test, 1 deselected). Runs real `perfcrawl measure https://example.com --samples 1`.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real end-to-end measurement against a live URL | All (smoke) | Requires Node 22.19+, Chrome, network — flaky in CI | `uv run perfcrawl measure https://example.com --samples 1 --json` then inspect `output/<run_id>/` |
| Visual sanity of Rich human summary table | CLI-01 / D-06 | Terminal rendering quality is judgment | Run without `--json`; confirm columns readable, INP column labeled `INP (lab proxy, TBT-based)` |
| Lighthouse HTML report opens correctly | OUT-03 | Browser rendering of LH report is out-of-band | Open `output/<run_id>/lighthouse/<slug>.html` in a browser |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s for non-e2e (full suite: **~0.88s** for 200 tests)
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-05-29

---

## Validation Audit 2026-05-29

| Metric | Count |
|--------|-------|
| Requirements audited | 18 (12 phase + 6 locked-decision) |
| COVERED | 18 |
| PARTIAL | 0 |
| MISSING | 0 |
| Gaps found | 0 |
| Resolved by auditor | 0 |
| Escalated to manual-only | 0 |
| Total tests in suite | 201 (200 selected + 1 e2e deselected) |
| Suite pass rate | 200/200 (100%) |
| Wall time | 0.88s |

**Test-name corrections applied during audit:**

- OUT-03: map referenced `test_raw_artifacts_on_disk`; actual is `test_raw_lh_artifacts_on_disk` — map updated.
- D-07 / IN-02: map referenced `test_no_path_traversal`; actual is `test_no_path_traversal_in_slug` — map updated.

No test generation was needed — the executed phase shipped tests for every requirement and locked decision. `gsd-nyquist-auditor` was not spawned (zero gaps).
