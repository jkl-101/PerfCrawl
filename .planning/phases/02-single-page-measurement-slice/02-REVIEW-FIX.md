---
phase: 02-single-page-measurement-slice
fixed_at: 2026-05-29T14:36:17Z
review_path: .planning/phases/02-single-page-measurement-slice/02-REVIEW.md
iteration: 1
findings_in_scope: 11
fixed: 10
skipped: 1
status: partial
---

# Phase 02: Code Review Fix Report

**Fixed at:** 2026-05-29T14:36:17Z
**Source review:** `.planning/phases/02-single-page-measurement-slice/02-REVIEW.md`
**Iteration:** 1

**Summary:**

- Findings in scope (critical + warning): 11 (CR: 0, WR-01..11)
- Fixed: 10
- Skipped: 1
- Default `uv run pytest -x` suite: 188 passed, 1 deselected (was 178 → +10 new
  regression tests)
- `uv run pytest -m e2e tests/test_e2e.py -x` (Node + Chrome + network): passed
  in 13.40s
- `uv run ruff check src/ tests/`: 26 pre-existing errors, no new errors
  introduced by the fixes

**Fix discipline:** every non-trivial fix landed as a TDD RED→GREEN pair (test
commit asserts the defect, fix commit makes it pass). Trivial fixes
(WR-03 docstring, WR-02 rename) landed as a single commit.

## Fixed Issues

### WR-01: Missing `node` binary surfaces as uncaught `FileNotFoundError`, not `MeasurementError`

**Files modified:** `src/perfcrawl/lighthouse_worker.py`, `tests/test_worker.py`
**Commits:**
- `159fce3` test(02): RED — preflight raises MeasurementError on missing node binary
- `b4a63ea` fix(02): WR-01 — verify node binary on PATH in preflight()

**Applied fix:** Added `shutil.which("node")` check at the top of `preflight()`,
before the existing `node_modules/lighthouse/package.json` marker check. A
missing `node` binary now raises `MeasurementError` with an actionable
"install Node >=22.19" hint (CLAUDE.md § Installation), which the CLI's
existing D-15 mapping turns into `ExitCode.MEASUREMENT_ERROR`. The regression
test monkeypatches `shutil.which` to return `None` for `"node"` and asserts
the `MeasurementError` shape.

---

### WR-02: D-10 version gate ignores `.minor` portion of `EXPECTED_LIGHTHOUSE_MAJOR_MINOR`

**Files modified:** `src/perfcrawl/constants.py`, `src/perfcrawl/normalizer.py`, `tests/test_slug.py`
**Commit:** `b3f0c9d` fix(02): WR-02 — rename EXPECTED_LIGHTHOUSE_MAJOR_MINOR to EXPECTED_LIGHTHOUSE_MAJOR

**Applied fix:** Renamed the constant to `EXPECTED_LIGHTHOUSE_MAJOR = "13"` so
the name matches the gate's actual behavior (major-only). Simplified
`_check_version` to `.startswith(EXPECTED_LIGHTHOUSE_MAJOR + ".")` instead of
the `.split(".")[0]` indirection. Updated `tests/test_slug.py::test_constants_module_declares_phase2_tunables`
to import and assert the new name. Module docstring + per-constant comment
explain that minor-level pinning is intentionally not enforced because per-
minor LH releases routinely keep audit shape backward-compatible — the
proper escalation is bumping the major.

---

### WR-03: Aggregator docstring claims `model_copy(update=...)` re-runs `_no_bare_inp`

**Files modified:** `src/perfcrawl/aggregator.py`
**Commit:** `803f63b` fix(02): WR-03 — correct aggregator docstring on model_copy validator semantics

**Applied fix:** Corrected both the function docstring and the inline comment
to match Pydantic v2's actual `model_copy` semantics — it copies field values
into a new instance WITHOUT re-running `@model_validator(mode='after')`
hooks. The defense-in-depth claim is reframed honestly: the model-layer
`_no_bare_inp` floor enforces the invariant at `samples[0]`'s construction
time, and the aggregator carries that already-validated shape forward.

---

### WR-04: Empty `reportJson` / `reportHtml` strings written as zero-byte artifact files

**Files modified:** `src/perfcrawl/output.py`, `src/perfcrawl/orchestrator.py`, `tests/test_output.py`
**Commits:**
- `6bd5f96` test(02): RED — skip zero-byte LH artifact files for empty payloads
- `a52455d` fix(02): WR-04 — skip zero-byte LH artifact writes for empty payloads

**Applied fix:** Tightened the guards in `output.write_outputs` from
`if report_json is not None:` to `if report_json:` (truthiness), so empty
strings no longer slip through and produce zero-byte files. Also tightened
the orchestrator side (`orchestrator.py:213-216`) from
`lh.get("reportJson", "")` to `lh.get("reportJson") or ""` so a `None`
payload from a malformed envelope normalizes to falsy. Two new regression
tests cover: (1) both fields empty → no files written; (2) JSON present
but HTML empty → only `.json` written, `.html` skipped.

---

### WR-05: `_launch_chrome_with_cdp_port` polling sleeps before first existence check

**Files modified:** `src/perfcrawl/orchestrator.py`, `tests/test_orchestrator.py`
**Commits:**
- `fc80f4e` test(02): RED — port poller must check existence before sleeping
- `b0afaaa` fix(02): WR-05 — monotonic-deadline port polling, check before sleep

**Applied fix:** Replaced the `for _ in range(int(timeout/interval)):` loop
with a monotonic-clock deadline loop that checks `port_file.exists()` BEFORE
sleeping. Two improvements: (1) a file present at t=0 (fast machines) returns
without paying one `DEVTOOLS_PORT_POLL_INTERVAL_S` wait; (2) drops the
`int(...)` truncation that turned a 5.0s budget at 0.15s interval (33.33
attempts) into 33. The existing `test_devtools_port_timeout_raises` test
needed a `time.monotonic` monkeypatch added (fake clock jumps past deadline)
so the test doesn't wait real wall-clock seconds.

---

### WR-06: `subprocess.run(text=True, encoding="utf-8")` raises uncaught `UnicodeDecodeError`

**Files modified:** `src/perfcrawl/lighthouse_worker.py`, `tests/test_worker.py`
**Commits:**
- `d957a79` test(02): RED — handle non-UTF-8 stdout/stderr without raise
- `b6ca0d4` fix(02): WR-06 — capture stdout/stderr as bytes and decode with replacement

**Applied fix:** Dropped `text=True, encoding="utf-8"` from the
`subprocess.run` call so non-UTF-8 bytes on stdout/stderr no longer raise
`UnicodeDecodeError` inside `subprocess.run` (an exception the `except
TimeoutExpired` block did not catch). Decode the captured bytes defensively
afterwards with `errors="replace"`. The existing test mocks that pass `str`
via `SimpleNamespace` still work because the decode branch tolerates both
`bytes` and pre-decoded `str` inputs. Two new regression tests cover
non-UTF-8 stdout (returns None via the JSONDecodeError arm) and non-UTF-8
stderr on a non-zero exit (still logged to sys.stderr without raising).

---

### WR-07: `page_slug` truncation can produce trailing-dot filename (Windows-invalid)

**Files modified:** `src/perfcrawl/slug.py`, `tests/test_slug.py`
**Commits:**
- `102fd39` test(02): RED — regression for WR-07 trailing-dot truncation
- `6e43aaa` fix(02): WR-07 — rstrip trailing separators after page_slug truncation

**Applied fix:** Replaced `return stem[:max_len]` with
`return stem[:max_len].rstrip("._-") or "_"`. The truncation can re-introduce
a trailing separator if the character at position `max_len-1` happens to be
one (e.g., the URL `https://x.com/aaa...a.bb` produces stem
`x.com_aaa...a.bb` of length 82; slicing to 80 leaves a trailing `.`).
The concrete RED test pins exactly this repro.

---

### WR-09: `csv.DictWriter` emits `\r\n` line endings

**Files modified:** `src/perfcrawl/output.py`, `tests/test_output.py`
**Commits:**
- `6774526` test(02): RED — assert LF-only line endings in result.csv
- `03bdcdd` fix(02): WR-09 — normalize result.csv to LF-only line endings

**Applied fix:** After building the CSV in a `StringIO` buffer, the assembled
content is now normalized via `buf.getvalue().replace("\r\n", "\n")` before
being passed to `_atomic_write_text`. RFC-4180 CRLF terminators no longer
reach disk; downstream consumers (jq, awk, gspread cell-upload, naive
`open(..., newline="\n")` readers) see LF-only line endings. The regression
test reads `result.csv` in binary mode and asserts no `\r` byte appears.

---

### WR-10: `WORKER_SCRIPT` path resolution breaks when perfcrawl is installed as a wheel

**Files modified:** `pyproject.toml`, `src/perfcrawl/lighthouse_worker.py`, `README.md`
**Commits:**
- `5064f3c` fix(02): WR-10 — document repo-checkout-only, drop project.scripts, improve preflight message
- `cae53f4` fix(02): WR-10 follow-up — restore project.scripts to keep editable install working

**Applied fix:** Two-stage fix because the first stage broke the e2e test:

1. **First commit** dropped `[project.scripts]` from pyproject.toml and added
   a structured `MeasurementError` to `preflight()` that explicitly names the
   wheel-install failure (`"lighthouse-worker directory not found at {path}
   — PerfCrawl is currently a repo-checkout-only tool..."`) before falling
   through to the generic `node_modules` error. README rewritten to make the
   repo-checkout-only constraint explicit.
2. **Follow-up commit** restored `[project.scripts]` because dropping it
   broke the e2e test (`uv run perfcrawl ...`). Editable installs (`uv sync`
   / `uv run`) DO resolve `WORKER_SCRIPT.parents[2]` correctly because the
   working tree is on disk at the expected layout; the wheel failure case
   remains documented in README + surfaced as the new `preflight()` error
   message. Pyproject comment explains the rationale.

**Note:** the full structural fix (option (b) in the review — make worker
location configurable via `PERFCRAWL_WORKER_DIR` env / CLI flag) is
deliberately deferred to Phase 3 per the review's recommendation. This
phase's fix is the documentation + actionable preflight error route.

---

### WR-11: Tempdir leak on Popen/Playwright failure inside `_launch_chrome_with_cdp_port`

**Files modified:** `src/perfcrawl/orchestrator.py`, `tests/test_orchestrator.py`
**Commits:**
- `6dd04cb` test(02): RED — launcher tempdir cleanup on Popen / sync_playwright failure
- `9801bba` fix(02): WR-11 — cleanup user_data_dir on Popen / sync_playwright failure

**Applied fix:** Mirrors CR-03's self-contained cleanup pattern. Wrapped the
section between `tempfile.mkdtemp` and the polling loop in a try/except that
calls `shutil.rmtree(user_data_dir, ignore_errors=True)` before re-raising.
Two new regression tests cover both uncovered paths: (1) `sync_playwright()`
raising (Playwright not installed / chromium binary not downloaded); (2)
`subprocess.Popen` raising (chrome binary missing / not executable). Both
assert `not user_data_dir.exists()` after the raise.

---

## Skipped Issues

### WR-08: `_unique_slug_path` has a TOCTOU race

**File:** `src/perfcrawl/output.py:157-172`
**Reason:** explicitly latent for Phase 2; deferred to Phase 3 per the
review's own framing and the orchestrator context note.

**Original issue:** The `if not candidate.exists(): return candidate` pattern
is textbook check-then-act TOCTOU. The review itself states: "Phase 2 is
single-URL-single-run so this can't fire in practice, but Phase 3 (multi-page
concurrent writes) will ship the race unless this is fixed at the boundary."

**Rationale for skip:**
- The race is unreachable in Phase 2: every measure invocation writes one
  page, single-process, no concurrency, no retry-with-different-slug. Even
  the `__N` collision suffix branch is dead code in Phase 2 (every run
  starts in a fresh `<run_id>/` directory).
- The proper fix is an `O_CREAT | O_EXCL` exclusive-creation loop, which
  changes the I/O contract (`_atomic_write_text` would need to learn an
  "or-fail-if-exists" mode). That refactor is best landed alongside Phase 3's
  concurrent multi-page writer where the test surface for it actually
  exists.
- **Phase 3 backlog item to file:** convert `_unique_slug_path` +
  `_atomic_write_text` to an exclusive-creation pattern (`os.O_CREAT |
  os.O_EXCL | os.O_WRONLY`), catch `FileExistsError`, increment, retry. Add
  a multi-worker stress test (two coroutines writing the same slug
  concurrently → both end up on disk under distinct `__N` suffixes).

---

## Notes

- **Test count drift:** baseline default suite was 178 passed; HEAD is 188
  passed. Net add: WR-01 (×1), WR-04 (×2), WR-05 (×1), WR-06 (×2), WR-07 (×1),
  WR-09 (×1), WR-11 (×2) = 10 new regression tests. No tests removed.
- **Carry-over status:** WR-01, WR-02, WR-04, WR-05, WR-06, WR-07, WR-09 were
  carry-over from the prior review; WR-10 and WR-11 are new findings; WR-03
  is a documentation-only carry-over. All resolved in this iteration except
  WR-08 (skipped with rationale above).
- **Coverage-gap section of REVIEW.md (informational, not a finding):** the
  reviewer's bulleted list at the end of REVIEW.md is now closed by the
  regression tests landed in this fix pass — `node` FileNotFoundError, tempdir
  cleanup on Popen + sync_playwright failure, trailing-dot truncation, and
  CRLF line endings all have pinning tests.
- **Out of scope** (info-tier; not addressed): IN-01..IN-08 remain open per
  the `critical_warning` fix scope. The CLI defensive-page-access (IN-02),
  chrome_version parsing (IN-03), `_check_version` whitespace/v-prefix
  normalization (IN-04), and the watchdog-clear-position issue (IN-08) are
  the highest-signal info findings the team may want to triage into a
  follow-up before phase ship.

---

_Fixed: 2026-05-29T14:36:17Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
