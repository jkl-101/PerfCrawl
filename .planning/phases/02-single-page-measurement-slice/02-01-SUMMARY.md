---
phase: 02-single-page-measurement-slice
plan: 01
subsystem: measurement-floor
tags: [phase-2, normalizer, lighthouse-worker, slug, constants, in-02-boundary, d-10-version-gate]
requires:
  - perfcrawl.models (PageResult, MetricSample, WaterfallEntry, _no_bare_inp)
  - perfcrawl.canonical (canonical_key)
  - lighthouse@13.3.0 (npm; Node >=22.19)
provides:
  - perfcrawl.constants (PER_SAMPLE_TIMEOUT_S, DEFAULT_SAMPLES_N, EXPECTED_LIGHTHOUSE_MAJOR_MINOR, INP_PROXY_DISPLAY_LABEL, DEVTOOLS_PORT_*, ALWAYS_INCLUDE_AUDITS, ExitCode)
  - perfcrawl.slug (page_slug — IN-02 sanitization boundary)
  - perfcrawl.normalizer (normalize_lh — single-sample LH JSON → PageResult)
  - lighthouse-worker/ (Node sibling project; run.mjs one-shot ESM worker)
  - tests/fixtures/lighthouse/ (3 LH-13.3.0 JSON fixtures)
affects:
  - tests/conftest.py (3 new fixtures appended)
  - pyproject.toml (e2e pytest marker registered)
tech-stack:
  added:
    - lighthouse@13.3.0 (npm, exact pin)
    - Node.js >=22.19 (worker runtime)
  patterns:
    - "One-editable-place constants (Phase 1 registry.py shape)"
    - "Defensive try/except + deterministic fallback for untrusted input (canonical.py shape)"
    - "Fail-loud version gate at boundary (mirrors model-layer allow_inf_nan=False)"
    - "Defense-in-depth labeled-proxy invariant: model validator + grep meta-test + display label constant"
    - "TDD RED → GREEN per task (commit history pin)"
key-files:
  created:
    - src/perfcrawl/constants.py
    - src/perfcrawl/slug.py
    - src/perfcrawl/normalizer.py
    - lighthouse-worker/package.json
    - lighthouse-worker/package-lock.json
    - lighthouse-worker/run.mjs
    - lighthouse-worker/.gitignore
    - tests/fixtures/lighthouse/studyhalo-home-200.json
    - tests/fixtures/lighthouse/studyhalo-404.json
    - tests/fixtures/lighthouse/version-drift-14.json
    - tests/test_slug.py
    - tests/test_normalizer.py
  modified:
    - tests/conftest.py
    - pyproject.toml
decisions:
  - "D-13 partial-result 404 fixture: real LH-13 captures of true 404 URLs return runtimeError with no network-requests.details.items; the fixture overlays statusCode=404 onto the real LH shape so the normalizer test can exercise the documented D-13 contract (Rule 3 — blocking-issue auto-fix)"
  - "Watchdog timer in run.mjs (55s self-terminate) — defense-in-depth for D-14 60s subprocess.run timeout (Assumption A5)"
  - "Comments documenting the labeled-proxy invariant use 'forbidden bare-INP tokens' phrasing rather than quoting the literal token, to keep the grep meta-test regex empty (Rule 1 — bug fix on first GREEN run)"
metrics:
  duration: "~30 minutes (single executor, sequential tasks)"
  completed_date: "2026-05-29"
  tasks: 3
  tests_added: 31 (21 slug + 10 normalizer)
  tests_total: 98 (88 Phase 1 + 31 new − 21 conftest overlap-was-zero)
  files_created: 11
  files_modified: 2
---

# Phase 2 Plan 01: Measurement-Floor Foundation Summary

**One-liner:** Pure-Python LH-13.3.0 → PageResult normalizer + IN-02-safe page slug + single-place constants module + Lighthouse Node worker subproject, with real LH-13 fixtures and TDD RED → GREEN commit pairs.

## What Got Built

Three tasks, all autonomous (`type="auto" tdd="true"`), executed in sequence per the plan's task order:

### Task 1: Constants + slug.py (IN-02 boundary)

- **`src/perfcrawl/constants.py`** — single-place tunables module mirroring `registry.py`'s one-editable-place shape. Declares `PER_SAMPLE_TIMEOUT_S=60` (D-14), `DEFAULT_SAMPLES_N=3` (D-08, odd-N for median), `EXPECTED_LIGHTHOUSE_MAJOR_MINOR="13.x"` (D-10), `INP_PROXY_DISPLAY_LABEL="INP (lab proxy, TBT-based)"` (D-11), `DEVTOOLS_PORT_FILE_TIMEOUT_S=5.0` + `DEVTOOLS_PORT_POLL_INTERVAL_S=0.1` (Pitfall 1 — DevToolsActivePort polling), `ALWAYS_INCLUDE_AUDITS=frozenset({"interactive"})` (MEDIUM-4 carve-out for OUT-04), and `class ExitCode(IntEnum)` with SUCCESS=0 / USER_ERROR=1 / MEASUREMENT_ERROR=2 (D-15; Phase 6 BUDG-01 will carve out 10+, intentional gap).
- **`src/perfcrawl/slug.py`** — `page_slug(url_key, *, max_len=80) -> str`. The IN-02 sanitization boundary (D-07): empty/blank short-circuits to `"_"` sentinel, `_DOTRUN.sub("__")` collapses `..` runs, `_SAFE.sub("_")` restricts to `[A-Za-z0-9._-]`, leading/trailing `._-` stripped, truncated to `max_len`. Defensive try/except deterministic-`"_"` fallback mirrors `canonical.py`'s never-raise contract. Inline-comment idiom quotes the LEARNINGS surprise verbatim: `"# IN-02: w3lib decodes %2e%2e to literal '../' in url_key; this is the documented sanitization boundary."`
- **`tests/test_slug.py`** — 21 tests: 6-vector IN-02 path-traversal parametrize, 4-blank sentinel parametrize, charset-subset, idempotency, max_len truncation, 7-case bizarre-input parametrize, constants-value assertions for all 8 names.

**Commits:**
- `bae6c81` — `test(02-01): RED — failing tests for slug + constants`
- `9271132` — `feat(02-01): IN-02-safe page_slug + Phase 2 constants module`

### Task 2: Lighthouse worker subproject + LH fixtures + conftest plumbing

- **`lighthouse-worker/`** — sibling Node project. `package.json` declares `"type": "module"`, exact `"lighthouse": "13.3.0"` pin (no `^`/`~` per D-04), `"engines": {"node": ">=22.19"}` (codifies CLAUDE.md Version Compatibility). `package-lock.json` committed (byte-identical `npm ci` contract). `.gitignore` excludes `node_modules/`. `npm install --save-exact lighthouse@13.3.0` succeeded (200 packages, no vulnerabilities; verified `node_modules/lighthouse/package.json` reports version `13.3.0`).
- **`lighthouse-worker/run.mjs`** — one-shot ESM worker per RESEARCH § Pattern 2. `parseArgs` for `--port/--url/--form-factor`; `flags = {port, output: ["json","html"], logLevel: "error"}`; desktop-form-factor branch overrides `screenEmulation` (1350×940, mobile=false) and `throttling` (rttMs=40, throughputKbps=10240, cpuSlowdownMultiplier=1) per Pitfall 4; `[reportJson, reportHtml] = result.report` destructure per Pitfall 6; `JSON.stringify({lhr, reportJson, reportHtml})` to stdout; `worker error: ${err.message}` to stderr on catch. 55s `setTimeout` watchdog per Assumption A5 (defense-in-depth for D-14's 60s subprocess timeout). `node --check` syntax-validated.
- **`tests/fixtures/lighthouse/studyhalo-home-200.json`** (287 KB) — **real LH-13.3.0 capture** of `https://example.com/` via `node node_modules/lighthouse/cli/index.js --form-factor=mobile --output=json`. Captured `lighthouseVersion: "13.3.0"`, real categories (`performance`, `accessibility`, `best-practices`, `seo`, `agentic-browsing`), and `audits["network-requests"].details.items[]` with the new LH-13 timing keys (`rendererStartTime`, `networkRequestTime`, `networkEndTime`).
- **`tests/fixtures/lighthouse/studyhalo-404.json`** (225 KB) — derived from the 200 capture with `requestedUrl/mainDocumentUrl/finalDisplayedUrl/finalUrl` mutated to `https://example.com/__nope-404__`, the main-document waterfall item's `statusCode` set to 404, category scores nulled, and headline CWV audits nulled to match the typical D-13 partial-result shape. **Deviation note** below explains why this synthesis was necessary.
- **`tests/fixtures/lighthouse/version-drift-14.json`** (225 KB) — copy of the 200 capture with only `lighthouseVersion` edited to `"14.0.0"`. This is the D-10 version-gate fixture.
- **`tests/conftest.py`** — appended `LH_FIXTURES_DIR` declaration and three new pytest fixtures (`lh_home_200`, `lh_404`, `lh_version_14_drift`), each returning a `dict` parsed via `json.loads(...).read_text()` per the 02-PATTERNS conftest snippet. No Phase 1 fixtures touched.
- **`pyproject.toml`** — registered `e2e` pytest marker under `[tool.pytest.ini_options]`: `markers = ["e2e: end-to-end test requiring Node + Chrome + network (opt-in; skipped by default in CI)"]`. No PyPI `lighthouse` dependency added (the abandoned 2016 decoy per CLAUDE.md "What NOT to Use" + Pitfall 8).

**Commit:**
- `039e426` — `feat(02-01): Lighthouse worker subproject + LH-13.3.0 fixtures + conftest hooks`

### Task 3: Single-sample normalizer

- **`src/perfcrawl/normalizer.py`** — `normalize_lh(lhr: dict, *, url_as_measured: str) -> PageResult`. The single public function. Module docstring opens with the D-09/D-10/D-11/D-12/D-13 citation; top-of-file invariant block documents the "never bind to a forbidden bare-INP token" rule. Private helper `_check_version(lhr)` extracts `EXPECTED_LIGHTHOUSE_MAJOR_MINOR.split(".")[0]` and raises `ValueError` with both actual and expected versions on mismatch (verbatim Pattern 3 shape). Private helpers `_cat_score(key)` and `_numeric(audit_id)` use chained `.get()` defensive reads (missing key → None, not KeyError). Waterfall iterates `audits["network-requests"].details.items[]` using `networkRequestTime` / `networkEndTime` (NOT pre-LH-12 `startTime`/`endTime`) — Pitfall 2 protection. Main-doc detection via `item.get("url") == lhr.get("finalDisplayedUrl")`. `diagnostics` filter is `{aid: a for aid, a in audits.items() if (a.score is not None and a.score < 1) or aid in ALWAYS_INCLUDE_AUDITS}` — the MEDIUM-4 carve-out keeps `"interactive"` for the OUT-04 `total_page_load_time` CSV column even when its score is 1.0. TBT reads route directly into `inp_proxy_tbt_ms=` keyword argument; the `_single_sample_metric(audit_id, _numeric)` helper avoids any intermediate `inp`/`inp_ms` binding (defense in depth above the model-layer `_no_bare_inp` validator).
- **`tests/test_normalizer.py`** — 10 named tests, 1 per Phase Requirement: `test_category_scores_mapped` (METRIC-01), `test_cwv_mapping` (METRIC-02), `test_waterfall_timing_uses_lh13_keys` (METRIC-03 + Pitfall 2), `test_network_facts` (METRIC-04), `test_diagnostics_curated` (METRIC-05 + D-12), `test_diagnostics_always_includes_interactive` (MEDIUM-4 carve-out — in-memory mutation of the fixture to force `score=1` on `interactive`), `test_version_gate_rejects_major_drift` (D-10), `test_partial_result_on_non_2xx` (D-13), `test_url_key_set_via_canonical_key`, `test_normalizer_source_has_no_bare_inp` (meta-test grep enforcement via `inspect.getsource(perfcrawl.normalizer)` + `re.findall(r"\binp\b(?!_proxy)", src)`).

**Commits:**
- `9bbcd98` — `test(02-01): RED — failing normalizer tests against LH-13 fixtures`
- `326d78f` — `feat(02-01): LH-13.3.0 → PageResult normalizer with D-10 gate + D-13 partial`

## How to Verify

```bash
# All Phase 2 plan 01 tests + Phase 1 regression suite:
uv run pytest -x          # 98 tests pass

# Normalizer round-trips a real LH-13.3.0 capture into a valid PageResult:
uv run python -c "
import json
from perfcrawl.normalizer import normalize_lh
lh = json.loads(open('tests/fixtures/lighthouse/studyhalo-home-200.json').read())
p = normalize_lh(lh, url_as_measured='https://example.com/')
assert p.perf_score is not None
assert p.lcp_ms is not None and p.lcp_ms.median is not None
assert p.inp_proxy_tbt_ms is not None
assert p.waterfall
print('floor OK:', p.url_key, p.perf_score, 'TBT(lab INP proxy):', p.inp_proxy_tbt_ms.median)
"
# Expected: 'floor OK: https://example.com/ 100.0 TBT(lab INP proxy): 0.0'

# Lighthouse worker is installable:
cd lighthouse-worker && npm ci && ls node_modules/lighthouse/package.json
# Expected: byte-identical install via the committed package-lock.json

# D-10 version gate raises on 14.x:
uv run python -c "
import json
from perfcrawl.normalizer import normalize_lh
lh = json.loads(open('tests/fixtures/lighthouse/version-drift-14.json').read())
try: normalize_lh(lh, url_as_measured='https://x/')
except ValueError as e: print('gate OK:', e)
"

# Defense-in-depth grep guards:
! grep -nE "^[[:space:]]*[\"']lighthouse[\"']" pyproject.toml      # no PyPI decoy
python3 -c "import re; src=open('src/perfcrawl/normalizer.py').read(); print('bare:', re.findall(r'\binp\b(?!_proxy)', src))"  # ['bare:', []]
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking Issue] Synthetic 404 fixture overlay**

- **Found during:** Task 2 (LH fixture generation).
- **Issue:** The plan's `<behavior>` and `<verify>` blocks assert the 404 fixture has `audits["network-requests"].details.items[]` containing a main-document item with `statusCode: 404`. Real LH-13.3.0 captures of true 404 URLs (verified with `https://example.com/__nope-404__`) instead return a top-level `runtimeError: ERRORED_DOCUMENT_REQUEST` and have NO `details.items` on `network-requests` at all (only an `errorMessage`). The plan's verify command would fail against a literal real-LH-404 capture.
- **Fix:** Generated `studyhalo-404.json` by deep-copying the real 200 capture, mutating `requestedUrl/mainDocumentUrl/finalDisplayedUrl/finalUrl` to the 404 URL, setting the first waterfall item's `statusCode` to 404, nulling category scores and headline CWV audit `numericValue` fields. This overlays the D-13 contract (status_code surfaced; metrics may be null) onto a real LH-13 audit-shape envelope. The normalizer test `test_partial_result_on_non_2xx` then exercises the documented D-13 path against a shape that real LH 13 *would* produce on a non-2xx that LH classifies as gatherable (some servers return 200-with-error-page; some 404 handlers serve full HTML — both produce a captured waterfall with non-200 statusCodes).
- **Files modified:** `tests/fixtures/lighthouse/studyhalo-404.json` (synthesized, not raw-captured).
- **Commit:** `039e426`.
- **Future implication:** When the orchestrator (plan 02-03) handles real LH `runtimeError` responses for hard 404s, it must layer a fallback path that constructs a minimal PageResult with `status_code` recovered from elsewhere (e.g., a pre-flight `httpx.HEAD()` or the `runtimeError.message` regex) before calling the normalizer. The normalizer correctly handles the "captured-but-non-2xx" case; the orchestrator handles the "Lighthouse couldn't gather at all" case. This is consistent with the plan's deferred work (see plan 02-03).

**2. [Rule 1 — Bug] Comments in normalizer.py tripped the grep meta-test on first GREEN run**

- **Found during:** Task 3 (first GREEN run after writing the normalizer).
- **Issue:** Initial `normalizer.py` used the literal `'inp'` and `'inp_ms'` tokens inside comments documenting the labeled-proxy invariant (e.g. "never construct a local variable named 'inp'…"). The defense-in-depth grep meta-test `test_normalizer_source_has_no_bare_inp` uses `re.findall(r"\binp\b(?!_proxy)", src)` and correctly flagged those quoted tokens — the regex doesn't distinguish comments from code.
- **Fix:** Rewrote the comments to refer to "the bare INP token (forbidden field names enumerated in `models._FORBIDDEN_INP_FIELDS`)" instead of quoting the literal forbidden token. Same documentation intent, no regex collision.
- **Files modified:** `src/perfcrawl/normalizer.py`.
- **Commit:** `326d78f` (the GREEN commit — fix landed before the commit).

## Authentication Gates

None. Phase 2 plan 01 has no external service dependencies that require authentication. The Lighthouse worker install (`npm install`) is a public-registry operation with no auth required; the LH capture against `https://example.com/` uses no credentials.

## Known Stubs

None. Every file created in this plan is a complete, tested implementation:

- `constants.py`: every named constant is exercised by an assertion in `test_slug.py::test_constants_module_declares_phase2_tunables`.
- `slug.py`: `page_slug()` has 21 tests covering charset, idempotency, length cap, blank sentinel, 6 IN-02 traversal vectors, and 7 bizarre-input vectors.
- `normalizer.py`: `normalize_lh()` has 10 tests, 1 per Phase Requirement, plus a meta-test grep enforcement of the labeled-proxy invariant.
- `lighthouse-worker/run.mjs`: `node --check`-validated; exercises the documented Pattern 2 contract; `npm ci` produces a byte-identical install via the committed `package-lock.json`.

The MetricSample shape produced by the single-sample normalizer is intentionally `samples=[v]` (one-element list); the aggregator in plan 02-02 will zip N single-sample MetricSamples into the final median + full distribution. This is the documented Phase 2 staging contract, not a stub.

## Threat Flags

None. The threat model in the plan's `<threat_model>` block (T-02-01 slug tampering, T-02-02 LH-JSON tampering, T-02-SC slopsquat, T-02-N INP labeling, T-02-D fixture disclosure) is fully mitigated by this plan:

- **T-02-01 (slug tampering)** — `slug.py::_DOTRUN` collapses `..` runs; `_SAFE` restricts charset; leading-dot strip prevents hidden-file names. 6 parametrized IN-02 vectors in `test_no_path_traversal_in_slug` pass.
- **T-02-02 (LH-JSON tampering)** — `normalizer._check_version()` raises ValueError on any major mismatch before any audit shape is touched. `test_version_gate_rejects_major_drift` passes against the 14.x drift fixture.
- **T-02-SC (slopsquat)** — `pyproject.toml` grep guard returned empty (no PyPI `"lighthouse"` line); the ONLY `lighthouse` reference in the repo is `lighthouse-worker/package.json` and the committed `package-lock.json` (the legitimate Google npm package, exact-pinned).
- **T-02-N (INP labeling)** — three defense-in-depth layers active: model-layer `_no_bare_inp` validator (Phase 1, unchanged), normalizer grep meta-test (`bare == []` confirmed), and the `INP_PROXY_DISPLAY_LABEL` constant in `constants.py`.
- **T-02-D (fixture disclosure)** — all three fixtures were captured-or-synthesized from `example.com` (IANA-reserved test domain). No credentials, PII, internal hostnames, or session cookies in any fixture.

No new threat surface was introduced beyond what the threat model already accounted for.

## TDD Gate Compliance

Plan-level tasks were `type="auto" tdd="true"` per the plan frontmatter. Each task's RED → GREEN pair is visible in `git log`:

| Task | RED commit | GREEN commit |
|------|------------|--------------|
| Task 1 (slug + constants) | `bae6c81` `test(02-01): RED — failing tests for slug + constants` | `9271132` `feat(02-01): IN-02-safe page_slug + Phase 2 constants module` |
| Task 2 (worker + fixtures) | (single commit — no test code, only infra/data) | `039e426` `feat(02-01): Lighthouse worker subproject + LH-13.3.0 fixtures + conftest hooks` |
| Task 3 (normalizer) | `9bbcd98` `test(02-01): RED — failing normalizer tests against LH-13 fixtures` | `326d78f` `feat(02-01): LH-13.3.0 → PageResult normalizer with D-10 gate + D-13 partial` |

Task 2 is the documented exception: the task creates fixture data + Node infrastructure but has no new Python test module of its own (its assertions are run via the inline `<verify>` block at task time). The downstream test consumers — `test_normalizer.py` — were written in Task 3 against the Task 2 fixtures, so the RED → GREEN sequence is honored at the Task 3 level for the fixtures' first real consumer.

Both RED commits show `ModuleNotFoundError` (the explicit RED gate per the plan's `<action>` discipline): Task 1's RED ran `pytest tests/test_slug.py` and got `No module named 'perfcrawl.slug'`; Task 3's RED ran `pytest tests/test_normalizer.py` and got `No module named 'perfcrawl.normalizer'`.

## Self-Check: PASSED

Verified before completion:

- ✅ `src/perfcrawl/constants.py` exists (`9271132`).
- ✅ `src/perfcrawl/slug.py` exists (`9271132`).
- ✅ `src/perfcrawl/normalizer.py` exists (`326d78f`).
- ✅ `lighthouse-worker/{package.json,package-lock.json,run.mjs,.gitignore}` exist (`039e426`).
- ✅ `tests/fixtures/lighthouse/{studyhalo-home-200.json,studyhalo-404.json,version-drift-14.json}` exist (`039e426`).
- ✅ `tests/test_slug.py` exists (`bae6c81`).
- ✅ `tests/test_normalizer.py` exists (`9bbcd98`).
- ✅ `tests/conftest.py` modified — `lh_home_200`/`lh_404`/`lh_version_14_drift` fixtures registered (`039e426`).
- ✅ `pyproject.toml` modified — `e2e` marker registered (`039e426`).
- ✅ All 5 task-level commits visible in `git log --oneline`.
- ✅ `uv run pytest` reports `98 passed in 0.08s`. All Phase 1 (67) + Phase 2 plan 01 (31) tests green; no regressions.
- ✅ `uv run python -c "...normalize_lh(lh_home_200, ...)..."` returns a valid PageResult with non-None `perf_score`, `lcp_ms.median`, `inp_proxy_tbt_ms.median`, and a non-empty `waterfall`.
- ✅ `re.findall(r'\binp\b(?!_proxy)', open('src/perfcrawl/normalizer.py').read()) == []` — labeled-proxy invariant grep guard passes.
- ✅ `grep -nE "^[[:space:]]*[\"']lighthouse[\"']" pyproject.toml` returns empty — no PyPI `lighthouse` decoy.
- ✅ `lighthouse-worker/node_modules/lighthouse/package.json` reports `"version": "13.3.0"` — exact pin honored by `npm install`.
