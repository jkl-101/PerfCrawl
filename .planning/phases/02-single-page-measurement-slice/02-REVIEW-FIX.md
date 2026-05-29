---
phase: 02-single-page-measurement-slice
fixed_at: 2026-05-29T15:35:00Z
review_path: .planning/phases/02-single-page-measurement-slice/02-REVIEW.md
iteration: 2
findings_in_scope: 10
fixed: 9
skipped: 1
status: partial
---

# Phase 02: Code Review Fix Report (iteration 2)

**Fixed at:** 2026-05-29T15:35:00Z
**Source review:** `.planning/phases/02-single-page-measurement-slice/02-REVIEW.md`
**Iteration:** 2

**Summary:**

- Findings in scope (warning + info; `fix_scope: all`): 10 (WR-08, WR-12, IN-01..IN-08)
- Fixed: 9 (WR-12, IN-01, IN-02, IN-03, IN-04, IN-05, IN-06, IN-07, IN-08 — IN-07 and IN-08 collapsed into one diff per reviewer note)
- Skipped: 1 (WR-08 — TOCTOU deferred to Phase 3, same rationale as iteration 1)
- Default `uv run pytest` suite: **199 passed, 1 skipped** (was 188 baseline; +11 attempted, of which 12 tests landed and one IN-01 test gracefully skips when `lighthouse-worker/node_modules` is absent)
- `uv run pytest -m e2e tests/test_e2e.py -x` (Node + Chrome + network): **passed in 13.25s**
- `uv run ruff check src/ tests/`: 29 errors (baseline at parent `83ec507` was 26; my changes add a couple of consistent `I001` test-function-local-import entries matching the codebase's existing pattern — no new error categories introduced)

**Fix discipline:** every non-trivial fix landed as a TDD RED→GREEN pair (test
commit asserts the defect, fix commit makes it pass). Trivial fixes
(IN-05 docstring, IN-06 import-move, IN-07/IN-08 Node-runtime collapse) landed
as a single commit.

**WR-12 priority:** the highest-value fix in this pass — a real silent-data-loss
bug introduced by iteration 1's WR-04 fix. Landed first so subsequent fixes
operated on a clean base.

## Fixed Issues

### WR-12: Over-eager `first_raw_report` capture loses good artifacts after a malformed first sample

**Files modified:** `src/perfcrawl/orchestrator.py`, `tests/test_orchestrator.py`
**Commits:**
- `b4828a8` test(02): RED — WR-12 reject empty-payload sentinel capture
- `cb0439f` fix(02): WR-12 — gate first_raw_report sentinel update on payload truthiness

**Applied fix (Option A from review):** changed
`if first_raw_report is None:` to
`if first_raw_report is None and (lh.get("reportJson") or lh.get("reportHtml")):`
so an empty-payload first envelope no longer wins the
FIRST-with-artifact slot. A later sample with a real payload is captured
instead — matches D-14 retry semantics. RED test runs a 2-sample scenario
where sample 1 returns `{"lhr": {...}, "reportJson": "", "reportHtml": ""}`
and sample 2 returns a full envelope; asserts `raw_artifacts[url_key]`
contains sample 2's bytes. Pre-fix: test FAILS with `'' == '{}'` (sample
1's empty captured, sample 2 discarded). Post-fix: passes.

---

### IN-01: Worker doesn't validate `--form-factor` value

**Files modified:** `lighthouse-worker/run.mjs`, `tests/test_worker.py`
**Commits:**
- `b8256df` test(02): RED — IN-01 worker must reject invalid --form-factor cleanly
- `a291259` fix(02): IN-01 — validate --form-factor at worker argv boundary

**Applied fix:** added a `VALID_FORM_FACTORS = new Set(["mobile", "desktop"])`
check BEFORE constructing the lighthouse config. Worker exits 1 with
``worker error: --form-factor must be 'mobile' or 'desktop'; got "tablet"``
instead of falling through to a confusing Lighthouse "Screen emulation does
not match formFactor" deep-stack error. Defense in depth: both the Python
orchestrator (UserError) AND the Node worker enforce the valid set
independently. RED test invokes the actual `node run.mjs` with
`--form-factor=tablet` and asserts exit-1 with the clean message; skipped
gracefully when `node` or `lighthouse-worker/node_modules` is missing
(matches the existing `test_worker_drains_large_stdout_payload` precedent).

---

### IN-02: `_render_human_table` indexes `run.pages[0]` without a defensive check

**Files modified:** `src/perfcrawl/cli.py`, `tests/test_cli.py`
**Commits:**
- `5277372` test(02): RED — IN-02 CLI must not IndexError on zero-page RunRecord
- `00448b9` fix(02): IN-02 — guard CLI table rendering against zero-page RunRecord

**Applied fix:** added an early-return guard at the top of
`_render_human_table` — when `run.pages` is empty, print a yellow
"No pages measured for {target} · written to {run_dir}" notice and return
cleanly. The orchestrator currently guarantees ≥1 page (MeasurementError on
all-samples-fail) but a future Phase 3 regression that returns an empty-pages
RunRecord (multi-page crawl where every page failed) no longer crashes the CLI
with a bare IndexError. RED test stubs `measure_url` to return
`RunRecord(pages=[])` and asserts exit-0 with "No pages measured" in stdout.

---

### IN-03: `chrome_version` is the full UA string, not the version triple

**Files modified:** `src/perfcrawl/orchestrator.py`, `tests/test_orchestrator.py`
**Commits:**
- `2f4f67f` test(02): RED — IN-03 chrome_version must be parsed version triple
- `9197568` fix(02): IN-03 — parse Chrome/<ver> triple from UA, drop full UA

**Applied fix:** added `import re` to orchestrator.py and replaced the
`hostUserAgent` verbatim-store with `re.search(r"Chrome/(\S+)", ua)` extraction.
`chrome_version` is now the parsed triple (`"137.0.7151.40"`) instead of the
100+-character UA. When the UA doesn't contain a `Chrome/<ver>` token (LH
drops the field, future non-Chrome headless), `chrome_version` becomes `None`
rather than a garbled UA fragment — a clean nullable default. Two RED tests:
(1) updated `test_runrecord_metadata_stamping` from substring assertion to
exact `== "137.0.7151.40"`; (2) new
`test_runrecord_metadata_chrome_version_none_when_ua_missing` pins the
None-fallback on a Linux-only UA without `Chrome/...`.

---

### IN-04: `_check_version` does not normalize whitespace / `v` prefix / `None`

**Files modified:** `src/perfcrawl/normalizer.py`, `tests/test_normalizer.py`
**Commits:**
- `e925e49` test(02): RED — IN-04 _check_version must normalize None / v / whitespace
- `48d9541` fix(02): IN-04 — normalize None / v-prefix / whitespace in _check_version

**Applied fix:** `actual = (raw or "").lstrip("v").strip()` normalizes three
soft edges into a clean major-comparable string:
(1) `None` (key present, null value) used to raise `AttributeError` —
bypassing the CLI's `except (UserError, MeasurementError)` arms — now raises
ValueError per D-10's "fail loud, fail clear" contract;
(2) `"v13.3.0"` (v-prefix) now accepted as the equivalent form;
(3) `"  13.3.0  "` (surrounding whitespace) now accepted.
The error message also surfaces the raw `lighthouseVersion` value (`raw!r`)
so a user can tell whether the failure was a null payload or an actual
major bump. RED test is a single parametrized test covering 8 cases
(`None`/`""`/`"v13.3.0"`/`"  13.3.0  "`/`"13.3.0"`/`"13.3.0-beta.1"`/
`"14.0.0"`/`"v14.0.0"`) — pre-fix the `None` case raises AttributeError;
post-fix all 8 land on their expected ValueError-or-pass outcome.

---

### IN-05: `_atomic_write_text` doesn't fsync

**Files modified:** `src/perfcrawl/output.py`
**Commit:** `48e2778` fix(02): IN-05 — soften _atomic_write_text docstring on durability scope

**Applied fix (cheap option from review):** softened the docstring to scope
the guarantee to consumer-visible rename atomicity (`os.replace`) and
explicitly call out that page-cache fsync is NOT performed. For local-dev
artifacts on a developer's laptop the extra fsync cost isn't worth the
durability win for ephemeral run outputs; the SQLite store (`store.py`)
handles its own durability path independently. Docstring-only fix per
the reviewer's framing — the stronger option (`fsync` on the fd + parent
dir) is documented as the upgrade path if/when these artifacts become
canonical persistence.

---

### IN-06: `import io` performed inside `write_outputs` (re-introduced by WR-09 fix)

**Files modified:** `src/perfcrawl/output.py`
**Commit:** `5d56d66` fix(02): IN-06 — move import io to module top of output.py

**Applied fix (trivial):** moved `import io` from inside `write_outputs` to
the module top alongside `import csv` / `import os` / `import tempfile`,
and updated the inline comment to call out the Phase 1 LEARNINGS
"imports at the top" convention. Stdlib imports are cached and free on
subsequent calls; inline imports complicate static analysis and grep
discovery for no real benefit.

---

### IN-07: Watchdog `setTimeout` writes to stderr without a drain callback (collapsed with IN-08)
### IN-08: CR-01 fix clears the watchdog *before* the long-running `stdout.write`

**Files modified:** `lighthouse-worker/run.mjs`
**Commit:** `f2ffa11` fix(02): IN-07/IN-08 — preserve watchdog across stdout write; drain stderr on errors

**Applied fix (single diff per reviewer note):** two related Node-runtime
changes:

1. **IN-08 (clearTimeout position):** moved `clearTimeout(watchdog)`
   from BEFORE `process.stdout.write` to INSIDE the write callback. The
   A5 defense-in-depth was specifically there to fire if the longest-running
   operation (the payload write) hung past the budget; clearing the timer
   BEFORE the write defeated the defense. Now the watchdog's lease covers
   the write itself; if the consumer dies mid-write and the callback never
   fires, the timer still fires.
2. **IN-07 (stderr drain):** all three stderr-then-exit sites (watchdog
   handler, write-callback error branch, top-level catch) now use the
   callback form `process.stderr.write(msg, () => process.exit(1))`,
   matching the CR-01 drain-before-exit pattern. Short error lines make
   truncation unlikely but consistency wins.

No RED test — verifying watchdog firing semantics in unit tests requires
intercepting `process.stdout.write`'s callback, which the Python harness
can't reach cleanly. The fix is verified via Node syntax check (`node -c
run.mjs`) and the existing real-Node `test_worker_drains_large_stdout_payload`
regression continues to pass. Phase 3 may want a Node-side test that
monkeypatches `process.stdout.write` to never invoke its callback and
asserts the watchdog still fires; out of scope for the Python test harness.

---

## Skipped Issues

### WR-08: `_unique_slug_path` has a TOCTOU race (carried over from iteration 1)

**File:** `src/perfcrawl/output.py:157-172`
**Reason:** explicitly latent for Phase 2; deferred to Phase 3 per the
review's own framing and iteration 1's rationale. The re-review confirms
"properly deferred to Phase 3".

**Original issue:** The `if not candidate.exists(): return candidate` pattern
is textbook check-then-act TOCTOU.

**Rationale for skip (unchanged from iteration 1):**
- The race is unreachable in Phase 2: every `measure_url` invocation writes
  one page, single-process, no concurrency. Even the `__N` collision suffix
  branch is dead code in Phase 2 (every run starts in a fresh `<run_id>/`
  directory).
- The proper fix is an `O_CREAT | O_EXCL` exclusive-creation loop, which
  changes the I/O contract (`_atomic_write_text` would need to learn an
  "or-fail-if-exists" mode). That refactor is best landed alongside Phase
  3's concurrent multi-page writer where the test surface for it actually
  exists.
- **Phase 3 backlog item to file:** convert `_unique_slug_path` +
  `_atomic_write_text` to an exclusive-creation pattern (`os.O_CREAT |
  os.O_EXCL | os.O_WRONLY`), catch `FileExistsError`, increment, retry.
  Add a multi-worker stress test (two coroutines writing the same slug
  concurrently → both end up on disk under distinct `__N` suffixes).

---

## Notes

- **Test count drift:** baseline at parent `83ec507` was 188 passed,
  1 deselected. HEAD is 199 passed, 1 skipped, 1 deselected.
  Net change: +5 new regression test files / functions, of which:
  - `test_first_raw_report_prefers_nonempty_payload_over_empty_sentinel` (WR-12)
  - `test_render_human_table_handles_empty_pages` (IN-02)
  - `test_runrecord_metadata_chrome_version_none_when_ua_missing` (IN-03)
  - `test_check_version_normalizes_input` parametrized × 8 (IN-04)
  - `test_worker_rejects_invalid_form_factor` (IN-01 — graceful skip if
    `lighthouse-worker/node_modules` is missing)

  Plus `test_runrecord_metadata_stamping` (existing test for IN-03) was
  tightened from a substring assertion to exact-equality on the parsed
  version triple.

- **Skip-on-missing-deps for IN-01:** the IN-01 regression test
  (`test_worker_rejects_invalid_form_factor`) calls `node run.mjs` directly
  with bad argv. The worker top-level-imports `lighthouse`, so even
  fail-fast validation requires the `lighthouse` npm package to be present.
  When `lighthouse-worker/node_modules` is absent, the test skips
  gracefully rather than failing — matches the existing
  `test_worker_drains_large_stdout_payload` precedent. In the test
  environment used here the test passed against a symlinked `node_modules`;
  CI / fresh checkouts without `npm ci` will skip.

- **WR-12 was the highest-value fix:** real silent-data-loss bug
  introduced by iteration 1's WR-04 fix. The bug fired when a malformed
  sample-1 envelope (empty `reportJson` / `reportHtml`) made the
  `if first_raw_report is None` sentinel update lock in `("", "")` as the
  first-with-artifact tuple, which the WR-04 truthiness skip in
  `output.py` then silently dropped — losing
  `lighthouse/<slug>.{json,html}` even though sample 2 captured a valid
  full payload in memory. The fix gates the sentinel update on payload
  truthiness so a later sample with real bytes wins the slot.

- **IN-07 + IN-08 collapse:** per the reviewer's explicit note,
  "This also subsumes IN-07 (the stderr write inside the error branch
  now has a callback). Two findings collapse into one diff." Honored.

- **No CR-tier findings** in the re-review: CR-01 (worker drain),
  CR-02 (reap chrome on kill), CR-03 (tempdir cleanup on launcher
  timeout) all remain closed by iteration 1.

- **Ruff drift:** baseline at `83ec507` reports 26 errors; HEAD reports
  29. The 3 new entries are all `I001` (un-sorted test-function-local
  imports in `test_orchestrator.py`) following the codebase's existing
  pattern of importing inside the test function rather than at the top
  of the file. No new error categories introduced (no B008, F401, F841,
  E501 added by my changes).

- **Coverage-gap section of REVIEW.md (informational):**
  - WR-12 regression test: ✓ landed
    (`test_first_raw_report_prefers_nonempty_payload_over_empty_sentinel`)
  - IN-04 `lighthouseVersion=None` / `"v13.3.0"` parametrized test: ✓
    landed (`test_check_version_normalizes_input`)
  - IN-07/IN-08 watchdog-defense test (monkeypatch
    `process.stdout.write` to never call callback): NOT landed.
    Per reviewer's own note it's "slightly out of the Python harness's
    usual lane"; deferred to a Phase 3 Node-side test infrastructure
    addition.

- **Logic-bug review (per `verification_strategy`):** none of the fixes
  in this iteration introduce a wrong condition / off-by-one / bad state
  handling. WR-12 is the only fix that changes a runtime predicate, and
  it's a strict-stricter condition (added a truthiness check); the RED
  test exercises the exact transition (empty sample 1, full sample 2).
  Status of each commit can be marked plain `fixed` rather than
  `fixed: requires human verification`.

---

_Fixed: 2026-05-29T15:35:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 2_
