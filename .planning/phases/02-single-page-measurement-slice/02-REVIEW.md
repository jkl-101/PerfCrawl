---
phase: 02-single-page-measurement-slice
reviewed: 2026-05-29T00:00:00Z
depth: standard
files_reviewed: 19
files_reviewed_list:
  - lighthouse-worker/package.json
  - lighthouse-worker/run.mjs
  - src/perfcrawl/aggregator.py
  - src/perfcrawl/cli.py
  - src/perfcrawl/constants.py
  - src/perfcrawl/lighthouse_worker.py
  - src/perfcrawl/normalizer.py
  - src/perfcrawl/orchestrator.py
  - src/perfcrawl/output.py
  - src/perfcrawl/slug.py
  - tests/conftest.py
  - tests/test_aggregator.py
  - tests/test_cli.py
  - tests/test_e2e.py
  - tests/test_normalizer.py
  - tests/test_orchestrator.py
  - tests/test_output.py
  - tests/test_slug.py
  - tests/test_worker.py
findings:
  critical: 0
  warning: 2
  info: 8
  total: 10
status: issues_found
---

# Phase 02: Code Review Report (post-WR-fix re-review)

**Reviewed:** 2026-05-29
**Depth:** standard
**Files Reviewed:** 19
**Status:** issues_found (no BLOCKERs)

## Summary

This is the second re-review of Phase 02, run after the WR-01..11 fixer pass
(18 commits on top of `da8ec40`, ending at `cae53f4`). The full default
suite is green (188 passed, 1 deselected) and the e2e suite (`-m e2e`) is
green against `https://example.com/`.

**Critical findings:** zero remain. CR-01 / CR-02 / CR-03 from the prior
review were already closed by 02-05 (callback-form `stdout.write`,
`kill()`+`wait(timeout=5)` reap pattern in both launcher-timeout and outer
finally, `shutil.rmtree` before raise). The regression tests
(`test_worker_drains_large_stdout_payload`,
`test_chrome_killed_on_{success,failure}` with `chrome_proc.waited`,
`test_devtools_port_timeout_raises` asserting `not user_data_dir.exists()`)
are intact in this commit.

**Warning findings closed by the fix pass (10 of 11 previous):**

| ID | File / Line | Status | Pin |
|----|-------------|--------|-----|
| WR-01 | `lighthouse_worker.py:144-148` | **closed** | `test_worker_preflight_raises_when_node_binary_missing` |
| WR-02 | `constants.py:52` + `normalizer.py:22,35` | **closed** | `test_constants_module_declares_phase2_tunables` asserts `"13"` (no `.x`); rename consistent across imports |
| WR-03 | `aggregator.py:78-82, 113-118` | **closed** | Docstring + inline comment now both say "does NOT re-run model validators"; safety claim correctly attributed to `samples[0]`'s prior validation |
| WR-04 | `output.py:245,248` + `orchestrator.py:244-247` | **closed (with one residual bug — see WR-12)** | `test_empty_raw_artifact_strings_skip_file_writes`, `test_partial_raw_artifact_writes_only_present_payload` |
| WR-05 | `orchestrator.py:128-143` | **closed** | `test_devtools_port_polling_checks_before_sleep` asserts zero sleeps when file present at t=0 |
| WR-06 | `lighthouse_worker.py:75-103` | **closed** | `test_worker_returns_none_on_non_utf8_stdout`, `test_worker_decodes_stderr_defensively_on_nonzero_exit` |
| WR-07 | `slug.py:75` | **closed** | `test_truncation_does_not_leave_trailing_dot` |
| WR-09 | `output.py:225` | **closed** | `test_csv_has_lf_only_line_endings` asserts `b"\r" not in raw` |
| WR-10 | `lighthouse_worker.py:149-165` | **closed (documentation route)** | Now distinguishes "worker dir missing" (wheel install) from "node_modules missing" (forgot `npm ci`); both messages name the install command |
| WR-11 | `orchestrator.py:93-116` | **closed** | `test_launcher_cleans_tempdir_on_sync_playwright_failure`, `test_launcher_cleans_tempdir_on_popen_failure` |

**WR-08 (TOCTOU on `_unique_slug_path`):** properly deferred to Phase 3.
Phase 2 is single-URL-single-page so the race cannot fire; the
exclusive-creation pattern fix belongs alongside the concurrent-writes work
in Phase 3. Re-skipping is correct.

**New findings surfaced by this re-review:**

1. **WR-12 (NEW, Warning) — Over-eager `first_raw_report` capture**: the
   WR-04 fix tightened output.py's *consumption* of empty strings, but
   left orchestrator.py's *production* sentinel unchanged. If sample 1
   returns an envelope with missing/empty `reportJson` + `reportHtml`,
   `first_raw_report` is set to `("", "")` and never replaced by a later
   successful sample with a real payload. The on-disk artifact then goes
   missing for the page even though a valid Lighthouse capture exists in
   memory. Detail below.

2. **IN-06 (CARRY-OVER) — `import io` still local to `write_outputs`**:
   the WR-09 fix added `import io` *inside* `write_outputs` (output.py:218)
   rather than at module top. The same finding from the prior review is
   unchanged.

3. **IN-08 (CARRY-OVER) — Watchdog cleared before the long write in run.mjs**:
   `clearTimeout(watchdog)` runs on line 97 *before* `process.stdout.write`
   on line 98. The watchdog was supposed to be defense-in-depth against a
   hung subprocess; clearing it before the longest-running operation
   defeats the intent. Suggested fix: move `clearTimeout(watchdog)` inside
   the callback. The prior review flagged this as IN-07/IN-08; it remains
   open.

4. **IN-01 through IN-05, IN-07 (CARRY-OVER)** — none were in the WR-fix
   scope; they remain open. Re-enumerated below for traceability.

**No Critical-tier (BLOCKER) findings.** WR-12 is a real regression class
(silent on-disk artifact loss when sample 1 is malformed) but only fires
on a worker-envelope edge case and is a Warning, not a BLOCKER. The Phase
02 ship gate is not held by these findings.

## Structural Findings (fallow)

_No `<structural_findings>` payload was provided with this re-review._

## Narrative Findings (AI reviewer)

### Warnings

#### WR-12: Over-eager `first_raw_report` capture loses good artifacts after a malformed first sample (NEW)

**File:** `src/perfcrawl/orchestrator.py:236-247`

**Issue:** WR-04's fix correctly tightened `output.py` to skip zero-byte
file writes when the report string is falsy. But the orchestrator's
sentinel-vs-payload logic was not updated alongside it. Current shape:

```python
if first_raw_report is None:
    first_raw_report = (
        lh.get("reportJson") or "",
        lh.get("reportHtml") or "",
    )
```

The `is None` check means once `first_raw_report` is *any* tuple — including
`("", "")` from a malformed first envelope — no subsequent successful sample
will ever replace it. Trace:

- Sample 1 returns `{"lhr": {...}, "reportJson": "", "reportHtml": ""}`
  (the worker exited 0 with a stripped-down envelope — possible if a future
  Lighthouse version changes the report shape, or a worker bug truncates
  the payload before `JSON.stringify`).
- `first_raw_report` becomes `("", "")`.
- Sample 2 returns the full envelope.
- The `if first_raw_report is None` check fails; sample 2's good payload is
  discarded.
- `write_outputs` receives `{url_key: ("", "")}`; the WR-04 truthiness
  guard skips both file writes.
- The user gets `result.json` + `result.csv` + SQLite row but **no
  `lighthouse/<slug>.{json,html}`** even though Lighthouse captured a
  perfectly valid report. The CR-01 plumbing is intact; only the
  orchestrator's selection logic loses the artifact.

The WR-04 contract widening (orchestrator stores `""` instead of `None`
for missing keys, output.py uses truthiness to skip) is internally
inconsistent — they only line up when every successful sample either has
both strings or has neither. A mixed envelope across samples (sample 1
empty, sample 2 full) is exactly what this code path is supposed to
handle (D-14 retry semantics).

**Fix:** Gate the sentinel update on the *payload*, not just on the
sentinel state. Two equivalent shapes:

```python
# Option A: only capture when at least one string is non-empty
if first_raw_report is None and (lh.get("reportJson") or lh.get("reportHtml")):
    first_raw_report = (
        lh.get("reportJson") or "",
        lh.get("reportHtml") or "",
    )
```

```python
# Option B: prefer non-empty payload over empty sentinel
candidate = (lh.get("reportJson") or "", lh.get("reportHtml") or "")
if any(candidate) and (first_raw_report is None or not any(first_raw_report)):
    first_raw_report = candidate
```

Option A is simpler and matches the FIRST-successful-sample-with-artifact
intent. Add a regression test that runs two samples where sample 1's
envelope has empty strings and sample 2's has real payloads; assert
`raw_artifacts[url_key]` contains sample 2's bytes.

---

### Info

#### IN-01: Worker doesn't validate `--form-factor` value (CARRY-OVER)

**File:** `lighthouse-worker/run.mjs:34-45`

**Issue:** `parseArgs` accepts any string for `--form-factor`. Python
validates `{"mobile", "desktop"}` (orchestrator.py:192-195), but a direct
`node run.mjs --form-factor=tablet ...` invocation forwards garbage to
Lighthouse, which silently uses its default mobile preset. The desktop
override block (`run.mjs:57-76`) only fires on the exact string
`"desktop"`; anything else falls through to mobile without complaint.

**Fix:**

```javascript
const VALID_FORM_FACTORS = new Set(["mobile", "desktop"]);
if (!VALID_FORM_FACTORS.has(values["form-factor"])) {
  process.stderr.write(
    `worker error: --form-factor must be 'mobile' or 'desktop'; got ${values["form-factor"]!r}\n`,
  );
  process.exit(1);
}
```

The Python orchestrator's guard makes this redundant for normal use, but
the worker is an exported argv contract — defense in depth at both layers
is consistent with the labeled-proxy invariant approach.

---

#### IN-02: `_render_human_table` indexes `run.pages[0]` without a defensive check (CARRY-OVER)

**File:** `src/perfcrawl/cli.py:84`

**Issue:** `page = run.pages[0]` raises `IndexError` if `run_record.pages`
is empty. The orchestrator currently guarantees ≥1 (it raises
`MeasurementError` on all-samples-fail), but a future Phase 3 regression
that returns an empty-pages `RunRecord` crashes the CLI with a bare
`IndexError` instead of a clean message. Cheap to add a guard.

**Fix:**

```python
if not run.pages:
    out_console.print(f"[yellow]No pages measured for {run.target}[/yellow]")
    return
page = run.pages[0]
```

---

#### IN-03: `chrome_version` is the full UA string, not the version triple (CARRY-OVER)

**File:** `src/perfcrawl/orchestrator.py:262-264`

**Issue:**

```python
chrome_version = (
    lhr_for_metadata.get("environment", {}).get("hostUserAgent")
)
```

stores a 100+-character UA string in a field labelled `chrome_version`.
`test_runrecord_metadata_stamping` even normalizes around the oddity with
`assert "Chrome/137.0.7151.40" in run_record.chrome_version` rather than
equality. Downstream (CSV column `chrome_version`, the Sheets exporter in
Phase 6) will surface the full UA in a column expected to hold `137.0.7151.40`.

**Fix:**

```python
import re
ua = lhr_for_metadata.get("environment", {}).get("hostUserAgent") or ""
m = re.search(r"Chrome/(\S+)", ua)
chrome_version = m.group(1) if m else None
```

Update the test to `assert run_record.chrome_version == "137.0.7151.40"`.

---

#### IN-04: `_check_version` does not normalize whitespace / `v` prefix / `None` (CARRY-OVER)

**File:** `src/perfcrawl/normalizer.py:26-41`

**Issue:** `actual = lhr.get("lighthouseVersion", "")` followed by
`actual.startswith(...)`. Three soft edges:

1. A pre-release tag `"13.3.0-beta.1"` passes the gate — probably fine but
   undocumented.
2. A `v` prefix (`"v13.3.0"`) fails the gate even though it's a valid
   version. Not in scope today (worker pins `13.3.0`) but cheap to handle.
3. If `lighthouseVersion` is present with `None`, `.startswith` raises
   `AttributeError`, not `ValueError` — the "fail loud, fail clear"
   contract D-10 advertises is broken on a null worker payload.

**Fix:**

```python
def _check_version(lhr: dict) -> None:
    actual = (lhr.get("lighthouseVersion") or "").lstrip("v").strip()
    major = actual.split(".")[0] if "." in actual else ""
    if major != EXPECTED_LIGHTHOUSE_MAJOR:
        raise ValueError(
            f"Lighthouse version mismatch: expected major "
            f"{EXPECTED_LIGHTHOUSE_MAJOR}.x, got {actual!r} (from "
            f"lighthouseVersion={lhr.get('lighthouseVersion')!r}). ..."
        )
```

Add a parametrized test covering `None`, `""`, `"v13.3.0"`, `"  13.3.0"`,
`"14.0.0"`, `"13.3.0-beta.1"`.

---

#### IN-05: `_atomic_write_text` doesn't fsync (CARRY-OVER)

**File:** `src/perfcrawl/output.py:135-154`

**Issue:** The docstring promises "a write either lands whole or not at
all" but only guarantees that *visibility* of the rename is atomic at the
filesystem level. Across a power loss between `os.replace` and the page
cache flushing, the on-disk content of `result.csv` / `result.json` can
be the new metadata pointing at an old or zero-length data extent.

For local dev artifacts on a developer's laptop this is fine; the
docstring overstates the guarantee slightly. Two options:

- Soften the docstring to "the consumer-visible path either points at the
  new content or the old, never a half-written tmp" (cheap).
- Add `os.fsync(tmp.fileno())` before close and `os.fsync(dir_fd)` on the
  parent directory (stronger; mostly matters for SQLite-adjacent data
  paths, which `store.py` handles separately).

The cheap option is correct for Phase 2.

---

#### IN-06: `import io` performed inside `write_outputs` (CARRY-OVER — re-introduced by WR-09 fix)

**File:** `src/perfcrawl/output.py:218`

**Issue:** The WR-09 fix added `import io` *inside* `write_outputs` rather
than at the top of the module:

```python
csv_content = buf.getvalue().replace("\r\n", "\n")
_atomic_write_text(run_dir / "result.csv", csv_content)

# --- lighthouse/<slug>.{json,html} ---
...

# (inside the function:)
import io  # local: only needed here, keeps the module surface tight
buf = io.StringIO()
```

The comment "only needed here, keeps the module surface tight" doesn't
reflect a real cost — stdlib imports are cached and free on subsequent
calls. The downside is real: inline imports complicate static analysis
(`mypy`, `ruff`'s `I001`), are harder to find with grep, and add a
microsecond-scale runtime cost on every call. Per the Phase 1 LEARNINGS
"imports at the top" convention.

**Fix:** Move `import io` to the top of the file alongside `import csv`,
`import os`, `import tempfile`.

---

#### IN-07: Watchdog `setTimeout` writes to stderr without a drain callback (CARRY-OVER)

**File:** `lighthouse-worker/run.mjs:27-30`

**Issue:** The watchdog handler does
`process.stderr.write(msg); process.exit(1)` synchronously. stderr's
payload is short (one error line), so truncation is unlikely in practice,
but the CR-01 drain-before-exit pattern should apply to stderr too for
consistency.

**Fix:** Use the callback form:

```javascript
const watchdog = setTimeout(() => {
  process.stderr.write(
    `worker error: self-terminated after ${WATCHDOG_MS}ms watchdog\n`,
    () => process.exit(1),
  );
}, WATCHDOG_MS);
```

Same idiom as CR-01 applied at the watchdog site.

---

#### IN-08: CR-01 fix clears the watchdog *before* the long-running `stdout.write` (CARRY-OVER — open)

**File:** `lighthouse-worker/run.mjs:97-104`

**Issue:** Current shape:

```javascript
const payload = JSON.stringify({ lhr: result.lhr, reportJson, reportHtml });
clearTimeout(watchdog);                              // <-- line 97: timer cleared FIRST
process.stdout.write(payload, (err) => {             // <-- then the long write
  if (err) { ... process.exit(1); }
  process.exit(0);
});
```

The watchdog was Assumption A5's defense-in-depth against the worker
hanging past the Python subprocess timeout. Clearing it *before* the
largest write defeats the defense — if the consumer dies mid-write and
the callback never fires (extreme edge case, but the exact failure mode
A5 exists to guard against), the worker hangs with no internal timer
left to fire. The Python-side `subprocess.run(timeout=60)` still
backstops eventually, so this is not a BLOCKER — but it's a defense
weakened against its stated intent.

**Fix:** Move `clearTimeout` inside the callback so the timer's lease
covers the write:

```javascript
const payload = JSON.stringify({ lhr: result.lhr, reportJson, reportHtml });
process.stdout.write(payload, (err) => {
  clearTimeout(watchdog);
  if (err) {
    process.stderr.write(
      `worker error: stdout write failed: ${err.message}\n`,
      () => process.exit(1),
    );
    return;
  }
  process.exit(0);
});
```

This also subsumes IN-07 (the stderr write inside the error branch now
has a callback). Two findings collapse into one diff.

---

## Coverage gaps (not findings — for the verifier)

- No regression test for WR-12: two-sample run where sample 1's envelope
  has empty strings and sample 2's has real payloads. Assert
  `raw_artifacts[url_key]` contains sample 2's bytes (not sample 1's
  empties).
- No test that probes the watchdog defense (IN-07 + IN-08). A test that
  monkeypatches `process.stdout.write` to never call its callback and
  asserts the watchdog still fires would pin the intended behavior — but
  it's a Node-side test, slightly out of the Python harness's usual lane.
  A `tests/test_worker.py` shim using the same idiom as
  `test_worker_drains_large_stdout_payload` could express it.
- No regression for the `lighthouseVersion=None` / `lighthouseVersion="v13.3.0"`
  edge cases in `_check_version` (IN-04).

---

_Reviewed: 2026-05-29_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
