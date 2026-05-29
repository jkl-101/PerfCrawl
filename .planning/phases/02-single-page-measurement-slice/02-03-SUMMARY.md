---
phase: 02-single-page-measurement-slice
plan: 03
subsystem: measurement-orchestration
tags: [phase-2, orchestrator, playwright, cdp, lighthouse-worker, d-01, d-14, d-15, tdd]

# Dependency graph
requires:
  - phase: 01-data-model-persistence-foundation
    provides: PageResult / MetricSample / RunRecord + canonical_key + Phase 1 "atomic + finally" pattern
  - plan: 02-01
    provides: perfcrawl.constants (PER_SAMPLE_TIMEOUT_S, DEVTOOLS_PORT_FILE_TIMEOUT_S, DEVTOOLS_PORT_POLL_INTERVAL_S, ExitCode), perfcrawl.normalizer (normalize_lh), lighthouse-worker/run.mjs Node subproject
  - plan: 02-02
    provides: perfcrawl.aggregator (aggregate_page_samples — collapses N per-sample PageResults into one)
provides:
  - "perfcrawl.lighthouse_worker — run_one_sample (three-failure-modes-to-None subprocess wrapper), preflight (Open Q5), MeasurementError, WORKER_SCRIPT"
  - "perfcrawl.orchestrator — measure_url (Playwright + CDP + per-sample loop + aggregate + raw-artifact side-channel), UserError, MeasurementError re-export, _launch_chrome_with_cdp_port helper"
  - "playwright>=1.60,<2 dependency in pyproject.toml + uv.lock"
  - "tests/test_worker.py (16 tests) + tests/test_orchestrator.py (17 tests)"
affects:
  - "pyproject.toml — playwright dep added alphabetically before w3lib"
  - "uv.lock — regenerated with playwright/greenlet/pyee"
  - "02-04 CLI plan can now `from perfcrawl.orchestrator import measure_url, MeasurementError, UserError` directly"

# Tech tracking
tech-stack:
  added:
    - "playwright>=1.60,<2 (Python; Microsoft-maintained — RESEARCH § Package Legitimacy Audit)"
    - "(transitive) greenlet 3.5.1 + pyee 13.0.1 (playwright deps)"
  patterns:
    - "subprocess.Popen + connect_over_cdp (Pitfall 5 fix) instead of launch_persistent_context — gives a real Browser object with .new_context() available for D-03 cold-cache cycling"
    - "DevToolsActivePort file polling (Pitfall 1) — never socket.bind(0) TOCTOU; the file is Chrome's documented contract for resolved-port handoff"
    - "Three-failure-modes-to-None contract (D-14): TimeoutExpired / non-zero exit / JSONDecodeError all collapse to None; caller decides retry vs drop"
    - "Try/finally with kill+rmtree on every path (T-02-03-Z) — Chrome lifecycle wrapped so a crash never leaks a zombie process or tempdir"
    - "Side-channel for OUT-03: orchestrator returns tuple[RunRecord, dict[url_key, (reportJson, reportHtml)]] — 02-04 CLI destructures and forwards raw_artifacts to output.write_outputs"
    - "Defense-in-depth grep guards on forbidden source-level tokens (shell=True, launch_persistent_context, socket.bind(0)) — mirrors Phase 1's INP-token grep meta-test"

key-files:
  created:
    - src/perfcrawl/lighthouse_worker.py
    - src/perfcrawl/orchestrator.py
    - tests/test_worker.py
    - tests/test_orchestrator.py
  modified:
    - pyproject.toml
    - uv.lock

key-decisions:
  - "MeasurementError defined in lighthouse_worker.py (not orchestrator.py) and re-exported via orchestrator's __all__. Rationale: preflight() needs to raise it; orchestrator imports both run_one_sample and preflight from worker. Defining the exception in the leaf module breaks the circular-import risk cleanly. 02-04 CLI still gets one-line `from perfcrawl.orchestrator import …` for the public surface."
  - "_launch_chrome_with_cdp_port resolves Chromium via a brief `with sync_playwright() as p: chrome_path = p.chromium.executable_path` then launches via subprocess.Popen outside that context. Rationale: Pitfall 5 — launch_persistent_context blocks browser.new_context(); Popen + connect_over_cdp inherits Playwright's bundled binary and gives a real Browser later."
  - "OUT-03 raw artifacts are stashed from the FIRST successful sample (not aggregated/concatenated across samples). Rationale: the existing Drive-archived workflow keeps one .json + one .html per page; HIGH-1 plan-check fix made this an orchestrator-side responsibility so 02-04 CLI doesn't need to back-edit the orchestrator API."
  - "Docstring phrasing avoids the literal forbidden tokens (`shell=True`, `launch_persistent_context`, `socket.bind(0)`) to keep the plan-level grep guards empty. Same fix shape as Phase 2 plan 01's normalizer.py 'forbidden bare-INP token' rewrite — see Deviation 1 below."

patterns-established:
  - "Pure-function subprocess wrapper above a Node worker: argv as list[str], three failure modes → None, defensive try/except in canonical.py's shape — pattern can be reused for any future Python ⇄ Node bridge (e.g. a Node-side `web-vitals` interaction shim in Phase 3)."
  - "Side-channel return value for orchestrator → output boundary: returning `(record, side_data)` tuple keeps OUT-03 a measurement-time responsibility and avoids back-editing the orchestrator API in later plans. Pattern carries through to Phase 5 if AI analysis needs to surface intermediate prompts."

requirements-completed:
  - RUN-03
  - RUN-04

# Metrics
duration: ~25 minutes
completed: 2026-05-29
tasks: 2 (each with TDD RED→GREEN; 4 commits total)
tests_added: 33 (16 worker + 17 orchestrator)
tests_total: 150 (117 prior + 33 new)
files_created: 4
files_modified: 2
---

# Phase 2 Plan 03: Playwright + CDP Orchestrator Summary

**One-liner:** Python-owned Chrome via `subprocess.Popen`, Lighthouse-attached-via-CDP `connect_over_cdp`, per-sample fresh `BrowserContext` for D-03 cold cache, D-14 retry-once-then-drop, OUT-03 raw-artifact side-channel — the riskiest plumbing seam STATE.md flagged is now implemented and mocked-tested.

## What Got Built

Two tasks, both `type="auto" tdd="true"`, executed in sequence per the plan's task order:

### Task 1: lighthouse_worker.py (Python-side subprocess wrapper)

- **`src/perfcrawl/lighthouse_worker.py`** — `run_one_sample(*, port, url, emulation, timeout_s) -> dict | None` collapses three failure modes (`subprocess.TimeoutExpired`, `proc.returncode != 0`, `json.JSONDecodeError`) into a single `Optional[dict]` return per D-14. On non-zero exit the worker's `proc.stderr` is forwarded to `sys.stderr` so the CLI's stderr passthrough surfaces an actionable breadcrumb (D-15). `preflight(worker_dir=None)` raises `MeasurementError` citing `cd lighthouse-worker && npm ci` (Open Q5) when `node_modules/lighthouse/package.json` is absent. `WORKER_SCRIPT = Path(__file__).resolve().parents[2] / "lighthouse-worker" / "run.mjs"` so the worker is found regardless of cwd (important for `tmp_path`-based tests). `MeasurementError` is defined here (not in `orchestrator.py`) and re-exported by `orchestrator` to avoid a circular import.
- **`pyproject.toml`** — `playwright>=1.60,<2` added under `[project] dependencies`, alphabetized before `w3lib`. No `lighthouse` line (Pitfall 8 + the PyPI-decoy ban from CLAUDE.md "What NOT to Use" — grep guard count = 0).
- **`uv.lock`** — regenerated; `playwright==1.60.0`, `greenlet==3.5.1`, `pyee==13.0.1` added as direct + transitive deps.
- **`tests/test_worker.py`** — 16 named tests: D-02 happy path, D-14 three-failure-modes-to-None (timeout, non-zero exit, JSONDecodeError), argv passthrough (mobile + desktop form-factor), T-02-03-SH shell-metacharacter safety (6 parametrized vectors: `;`, `&`, `$(...)`, backticks, `|`, `>`), timeout passthrough, Open Q5 preflight raise + success cases, `WORKER_SCRIPT` path-resolution sanity. Uses `monkeypatch.setattr("subprocess.run", ...)` per the 02-PATTERNS § test_worker.py shape.

**Commits:**
- `d4c50f0` — `test(02-03): RED — failing worker subprocess tests + playwright dep` (16 tests + pyproject + uv.lock; fails with `ModuleNotFoundError: No module named 'perfcrawl.lighthouse_worker'`)
- `a4ba0e0` — `feat(02-03): lighthouse_worker.py Python-side subprocess wrapper + preflight` (implementation; 16 tests green, full suite 133 tests green)

### Task 2: orchestrator.py (Playwright + CDP + per-sample loop + aggregate)

- **`src/perfcrawl/orchestrator.py`** — `measure_url(*, url, samples=1, emulation="mobile") -> tuple[RunRecord, dict[str, tuple[str, str]]]`. The public entry point. Input validation (`UserError` on empty URL, `samples < 1`, unknown emulation) runs BEFORE any subprocess so bad input never launches Chrome. `preflight()` runs next (worker install check). Then `_launch_chrome_with_cdp_port()` launches Chromium via `subprocess.Popen` with `--remote-debugging-port=0` + `--headless=new`, polls `<user_data_dir>/DevToolsActivePort` for up to `DEVTOOLS_PORT_FILE_TIMEOUT_S` at `DEVTOOLS_PORT_POLL_INTERVAL_S` granularity (Pitfall 1 — never `socket.bind(0)` TOCTOU), reads the port from line 1 of the file. The per-sample loop uses `connect_over_cdp(f"http://localhost:{port}")` (Pitfall 5 — gives a real Browser with `.new_context()`), creates a fresh `BrowserContext` per sample (D-03 cold cache), invokes `run_one_sample` with initial attempt + one retry on `None` (D-14), normalizes the lhr via `normalize_lh`, stashes the first successful sample's `(reportJson, reportHtml)` as the OUT-03 side-channel. After the loop, `aggregate_page_samples(per_sample_results)` collapses N samples into one PageResult; the RunRecord is stamped with `chrome_version` (from `lhr.environment.hostUserAgent`), `lighthouse_version` (from `lhr.lighthouseVersion`), `throttling` (from `lhr.configSettings.throttling`), `emulation` (from the argument) per D-04 + RUN-02. The outer try/finally always `chrome.kill()`-s and `shutil.rmtree(user_data_dir, ignore_errors=True)`-s (T-02-03-Z — no zombie Chrome, no leaked tempdirs).
- **`tests/test_orchestrator.py`** — 17 named tests, mocking at three layers (sync_playwright, _launch_chrome_with_cdp_port, run_one_sample): RUN-04 + OUT-03 happy path (aggregated lcp_ms.samples length 3 + raw_artifacts has one key mapping to `("{}", "<html></html>")`), RUN-03 fresh-context-per-sample assertion (`browser.new_context()` called 3 times; all closed), D-14 retry-then-drop (calls 1-2 return None → sample 1 dropped; calls 3-4 succeed; final samples length = 2; total worker calls = 4), D-14 one-retry recovery (samples=1, call 1 fails + call 2 succeeds, final length = 1), D-14 all-samples-fail raises `MeasurementError("all 3 samples failed")`, security (chrome.kill on success + on failure paths, tempdir cleanup on failure via marker-file check), Pitfall 1 (DevToolsActivePort polling — file appears on attempt 3, port `54321` read; timeout raises `MeasurementError` mentioning "DevToolsActivePort" and kills Chrome), D-15 (3 UserError parametrized variants: empty URL, bad samples, bad emulation), RUN-02/D-04 metadata stamping, source-level shell-invocation grep guard. The Pitfall 1 tests bypass the standard stubs and exercise the real `_launch_chrome_with_cdp_port` polling logic by mocking `subprocess.Popen`, `tempfile.mkdtemp`, `sync_playwright`, and `time.sleep` (sleep callback writes the port file on attempt 3).

**Commits:**
- `5e89b68` — `test(02-03): RED — failing orchestrator integration tests` (17 tests; fails with `ModuleNotFoundError: No module named 'perfcrawl.orchestrator'`)
- `636b6c7` — `feat(02-03): Playwright + CDP orchestrator with D-14 retry + D-03 cold cache + D-15 exit-mapping` (implementation; 17 tests green, full suite 150 tests green)

## How to Verify

```bash
cd /Users/sneaky/JKL101/performance-statistics-gathering
# Unit + integration suite all green:
uv run pytest tests/test_worker.py tests/test_orchestrator.py -x
# Full Phase 1 + Phase 2-01 + Phase 2-02 + Phase 2-03 — no regression:
uv run pytest -x   # 150 passed
# Sanity import of the public API the 02-04 CLI will consume:
uv run python -c "
from perfcrawl.orchestrator import measure_url, MeasurementError, UserError
from perfcrawl.lighthouse_worker import run_one_sample, preflight
print('public API ready:', measure_url, MeasurementError, UserError, run_one_sample, preflight)
"

# Grep guards (5 total — all pass):
! grep -nE "shell\s*=\s*True" src/perfcrawl/lighthouse_worker.py 2>/dev/null | grep -v '^[[:space:]]*#' | grep .
! grep -nE "shell\s*=\s*True" src/perfcrawl/orchestrator.py 2>/dev/null | grep -v '^[[:space:]]*#' | grep .
grep -cE "^[[:space:]]+\"lighthouse[\"']?[[:space:]]*[>=~^]" pyproject.toml   # 0 (no PyPI lighthouse decoy)
uv run python -c "
import re
src = open('src/perfcrawl/orchestrator.py').read()
assert 'connect_over_cdp' in src
assert 'launch_persistent_context' not in src
assert 'DevToolsActivePort' in src
assert re.search(r'socket\.bind\([\'\"]?\s*0', src) is None
print('plumbing OK')
"

# Optional end-to-end smoke (requires `cd lighthouse-worker && npm ci`
# + `uv run playwright install chromium` — see user_setup):
# uv run python -c "from perfcrawl.orchestrator import measure_url; r, raw = measure_url(url='https://example.com/', samples=1, emulation='mobile'); print(r.id, r.pages[0].perf_score, r.pages[0].lcp_ms.median, list(raw.keys()))"
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Module docstrings tripped the plan's literal-token grep guards**

- **Found during:** Task 1 GREEN (initial `lighthouse_worker.py` build) and Task 2 GREEN (initial `orchestrator.py` build).
- **Issue:** The plan's `<verify>` block defines source-level grep guards that look for forbidden tokens as literal strings: `! grep -nE "shell\s*=\s*True" …` for Task 1, and `assert 'launch_persistent_context' not in src` for Task 2. My initial docstrings explained the security stance using the literal forbidden tokens (e.g. `"shell=True is NEVER passed"`, `"launch_persistent_context would force the .browser is None corner"`), which made the grep guards trip even though the code itself never invokes either.
- **Fix:** Rewrote each docstring to reference the forbidden behavior without naming the literal token (`"the shell-invocation kwarg is NEVER passed"`, `"the persistent-context launcher would force the .browser is None corner"`, `"never pre-pick a port via socket bind"`). Same documentation intent; the literal forbidden tokens appear ONLY in code-level grep guards' negative regex, not in source.
- **Files modified:** `src/perfcrawl/lighthouse_worker.py`, `src/perfcrawl/orchestrator.py`.
- **Commits:** Folded into the Task 1 GREEN (`a4ba0e0`) and Task 2 GREEN (`636b6c7`) commits — the fix landed before either commit was finalized.
- **Pattern lineage:** This is the same fix shape Phase 2 plan 01 used for `normalizer.py`'s "forbidden bare-INP token" grep meta-test (Deviation 2 in `02-01-SUMMARY.md`). The cross-plan lesson: when the plan ships a textual grep guard against a forbidden token, the docstring must paraphrase the token rather than quote it. Phase 2 plan 04 (CLI) should expect the same with any new grep guards it introduces.

## Authentication Gates

None. The orchestrator does not authenticate to anything — Phase 4 is when `storage_state` + `disableStorageReset:true` layer on this same seam. The Phase 2 measurement is anonymous; the public test fixtures use `example.com` (IANA-reserved test domain).

The `playwright install chromium` step in the plan's `user_setup` (one-time per developer machine, downloads the bundled Chromium under `~/Library/Caches/ms-playwright/`) is **not** required for this plan's tests because every test mocks Playwright. Developer-side setup is documented and surfaced via `preflight()` and the CLI errors in 02-04.

## Known Stubs

None. Every file created in this plan is a complete, tested implementation:

- `lighthouse_worker.py`: 16 tests cover all three failure modes, argv passthrough for both emulations, 6 shell-metacharacter parametrize vectors, timeout passthrough, preflight raise + success, and the WORKER_SCRIPT path resolution.
- `orchestrator.py`: 17 tests cover RUN-03 cold-cache cycling, RUN-04 + OUT-03 happy path, D-14 three branches (retry-then-drop, one-retry recovery, all-fail raise), D-15 three UserError branches, T-02-03-Z chrome kill on success + on failure + tempdir cleanup on failure, Pitfall 1 polling + timeout raise, RUN-02/D-04 metadata stamping, source-level shell-invocation grep guard.

The orchestrator's "first successful sample only" policy for the raw `(reportJson, reportHtml)` side-channel is intentional, not a stub — the existing Drive-archived workflow keeps one .json + one .html per page, not one per sample. Documented in `measure_url` docstring and pinned in `test_measure_url_returns_run_record_and_raw_artifacts`.

## Threat Flags

None. The threat register in the plan's `<threat_model>` block (T-02-03-SH, T-02-03-Z, T-02-03-HANG, T-02-03-RACE, T-02-03-CONCURRENT, T-02-03-SLOPSQUAT, T-02-03-PARTIAL) is fully mitigated by this plan:

| Threat ID | Mitigation | Test |
|-----------|------------|------|
| T-02-03-SH (URL shell-injection) | `subprocess.run` argv is always `list[str]`; no shell-invocation kwarg ever passed. Grep guard returns empty. | `test_worker_argv_is_list_no_shell_expansion` (6 parametrized URL vectors) + `test_orchestrator_source_has_no_shell_invocation` (source-level scan) |
| T-02-03-Z (zombie Chrome / leaked tempdir) | `chrome.kill()` + `shutil.rmtree(user_data_dir, ignore_errors=True)` in the outer try/finally that wraps the whole `with sync_playwright()` block | `test_chrome_killed_on_success`, `test_chrome_killed_on_failure`, `test_tempdir_cleaned_on_failure` |
| T-02-03-HANG (LH hangs Chrome) | Belt-and-suspenders: `subprocess.run(timeout=PER_SAMPLE_TIMEOUT_S=60)` here + the Node worker's internal 55s `setTimeout` watchdog from 02-01. Worker's own timeout fires first; Python's is the backstop. | `test_worker_returns_none_on_timeout` (Python side) + 02-01 Task 2 watchdog (Node side, already in run.mjs) |
| T-02-03-RACE (DevToolsActivePort TOCTOU) | Read the file Chrome writes (per Chrome's documented contract), NOT `socket.bind(0)`. Grep guard asserts no `socket.bind(0)` in orchestrator.py. | `test_devtools_port_polling` (positive — file appears on attempt 3) + `test_devtools_port_timeout_raises` (negative — file never appears, MeasurementError raised, Chrome killed) |
| T-02-03-CONCURRENT (multi-invocation collision) | `tempfile.mkdtemp(prefix="perfcrawl-chrome-")` is per-process unique; `--remote-debugging-port=0` is kernel-picked. Two concurrent `measure` runs cannot collide on user-data-dir or port by construction. | By-construction (tempfile.mkdtemp + port 0) + module docstring documents the invariant. |
| T-02-03-SLOPSQUAT (PyPI lighthouse decoy) | `playwright>=1.60,<2` only; lighthouse is NOT a Python dep. Grep guard `^[[:space:]]+"lighthouse[…]` returns count = 0. Reaffirms Phase 2 plan 01 Pitfall 8 stance. | Grep guard in plan `<verify>` block; passes. |
| T-02-03-PARTIAL (silent zero-data run) | All-N-fail raises `MeasurementError(f"all {samples} samples failed")` — never returns an empty PageResult. CLI in 02-04 maps to `ExitCode.MEASUREMENT_ERROR`. | `test_all_samples_fail_raises_measurement_error` (samples=3, all None) |

No new threat surface introduced. ASVS coverage achieved: V5 (UserError input gates on URL/samples/emulation), V7 (UserError/MeasurementError two-arm exhaustive error handling), V12 (per-run tempdir cleaned via shutil.rmtree in finally), V14 (playwright>=1.60,<2 pinned + uv.lock committed for byte-identical installs).

## TDD Gate Compliance

Plan-level tasks were `type="auto" tdd="true"` per the plan frontmatter. Each task's RED → GREEN pair is visible in `git log`:

| Task | RED commit | GREEN commit |
|------|------------|--------------|
| Task 1 (lighthouse_worker) | `d4c50f0` `test(02-03): RED — failing worker subprocess tests + playwright dep` | `a4ba0e0` `feat(02-03): lighthouse_worker.py Python-side subprocess wrapper + preflight` |
| Task 2 (orchestrator) | `5e89b68` `test(02-03): RED — failing orchestrator integration tests` | `636b6c7` `feat(02-03): Playwright + CDP orchestrator with D-14 retry + D-03 cold cache + D-15 exit-mapping` |

Both RED commits show `ModuleNotFoundError` (the explicit RED gate per the plan's `<action>` discipline): Task 1's RED ran `pytest tests/test_worker.py` and got `No module named 'perfcrawl.lighthouse_worker'`; Task 2's RED ran `pytest tests/test_orchestrator.py` and got `No module named 'perfcrawl.orchestrator'`.

The `pyproject.toml` + `uv.lock` changes ship in the RED commit alongside the test file because the test file needs to be importable AND the test environment must have `playwright` installable to run them later (though Task 1's tests do not directly import playwright — only orchestrator's would). The dep is the precondition for the entire plan, so it lives in the first commit.

## Performance

- **Started:** 2026-05-29 (approx 11:05 local)
- **Completed:** 2026-05-29 (approx 11:30 local)
- **Duration:** ~25 minutes (single executor, sequential tasks)
- **Tasks:** 2 (4 commits total — 2 RED + 2 GREEN)
- **Files created:** 4 (`lighthouse_worker.py`, `orchestrator.py`, `test_worker.py`, `test_orchestrator.py`)
- **Files modified:** 2 (`pyproject.toml`, `uv.lock`)
- **Tests added:** 33 (16 worker + 17 orchestrator)
- **Tests total:** 150 (117 prior + 33 new), all green, ~0.11s

## Next Phase Readiness

- **02-04 CLI** can now import the full public surface:
  ```python
  from perfcrawl.orchestrator import measure_url, MeasurementError, UserError
  from perfcrawl.constants import ExitCode, DEFAULT_SAMPLES_N
  ```
  The two-arm `except UserError → ExitCode.USER_ERROR; except MeasurementError → ExitCode.MEASUREMENT_ERROR` pattern (D-15) is exactly the shape the CLI's catch arms need.
- **OUT-03 side-channel:** `measure_url` returns `(RunRecord, dict[url_key, (reportJson, reportHtml)])`. The CLI destructures and forwards the dict to `output.write_outputs(run_record, output_dir, raw_artifacts=raw_artifacts)`. No back-edit to the orchestrator API needed in 02-04 (the HIGH-1 fix from plan-check is now landed).
- **End-to-end smoke testing** requires two one-time developer-machine setup steps which are documented (not auto-installed; this is the planner's discretion + Open Q5 acceptance):
  1. `cd lighthouse-worker && npm ci` — installs `lighthouse@13.3.0` via the committed `package-lock.json` (already required since 02-01 Task 2).
  2. `uv run playwright install chromium` — downloads Playwright's bundled headless Chrome to `~/Library/Caches/ms-playwright/` (new in this plan; documented in the plan's `user_setup`).
- **Phase 4 auth handoff:** the `connect_over_cdp` + per-sample `BrowserContext` pattern is now the proven seam. Phase 4 will layer `storage_state=` on `browser.new_context()` and `disableStorageReset:true` on Lighthouse's config — both are incremental edits on this code, not architectural changes (front-loading-the-risk thesis from the plan's `<objective>` validated).

## Self-Check: PASSED

- ✅ `src/perfcrawl/lighthouse_worker.py` exists (`a4ba0e0`).
- ✅ `src/perfcrawl/orchestrator.py` exists (`636b6c7`).
- ✅ `tests/test_worker.py` exists (`d4c50f0`).
- ✅ `tests/test_orchestrator.py` exists (`5e89b68`).
- ✅ `pyproject.toml` lists `playwright>=1.60,<2` under `[project] dependencies` (`d4c50f0`).
- ✅ `uv.lock` regenerated with playwright/greenlet/pyee (`d4c50f0`).
- ✅ All 4 task-level commits visible in `git log --oneline 22207c1..HEAD`: `d4c50f0`, `a4ba0e0`, `5e89b68`, `636b6c7`.
- ✅ `uv run pytest` reports `150 passed in 0.11s`. All Phase 1 (67) + Phase 2 plan 01 (31) + Phase 2 plan 02 (19) + Phase 2 plan 03 (33) tests green; no regression.
- ✅ Plan verify commands all pass: pytest combined target, source-level sanity import, grep-guard for connect_over_cdp + DevToolsActivePort + no-launch_persistent_context + no-socket.bind(0), full-suite regression.
- ✅ Public API import test passes — `measure_url`, `MeasurementError`, `UserError`, `run_one_sample`, `preflight` all importable.
- ✅ Test counts meet plan minima: 16 worker tests (≥8 required) + 17 orchestrator tests (≥12 required).
- ✅ All 5 grep guards pass: no `shell=True` in either module (code-level), no `launch_persistent_context` in orchestrator source, no `socket.bind(0)` in orchestrator source, `connect_over_cdp` present, `DevToolsActivePort` present.

---
*Phase: 02-single-page-measurement-slice*
*Completed: 2026-05-29*
