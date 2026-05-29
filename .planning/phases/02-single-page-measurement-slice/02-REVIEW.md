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
  warning: 11
  info: 7
  total: 18
status: issues_found
---

# Phase 02: Code Review Report (re-review)

**Reviewed:** 2026-05-29
**Depth:** standard
**Files Reviewed:** 19
**Status:** issues_found

## Summary

This is the post-plan-02-05 re-review of the Phase 02 single-page measurement
slice. The three Critical defects from the prior 02-REVIEW.md (CR-01 worker
stdout truncation, CR-02 Chrome zombie on kill-without-wait, CR-03 launcher
tempdir leak on DevToolsActivePort timeout) are **closed in code** and have
real regression tests pinning them:

- **CR-01 (closed)** — `lighthouse-worker/run.mjs:92-105` now uses the
  callback form `process.stdout.write(payload, (err) => { ... process.exit })`
  so the kernel pipe drains before the worker terminates.
  `tests/test_worker.py::test_worker_drains_large_stdout_payload` spawns a
  real Node shim with a 1.5 MB payload and asserts every byte round-trips.
- **CR-02 (closed)** — Both `_launch_chrome_with_cdp_port`'s timeout path
  (`orchestrator.py:123-128`) and the outer `measure_url` finally
  (`orchestrator.py:261-267`) now `proc.kill()` → `proc.wait(timeout=5)`
  with a `TimeoutExpired` fallback so the killed Chromium is reaped.
  `tests/test_orchestrator.py::test_chrome_killed_on_{success,failure}`
  assert `chrome_proc.waited is True`, and the `_FakeChromeProc.wait` stub
  flips the flag.
- **CR-03 (closed)** — `_launch_chrome_with_cdp_port` calls
  `shutil.rmtree(user_data_dir, ignore_errors=True)` on
  `orchestrator.py:129` **before** raising `MeasurementError`, so the
  launcher's failure path is self-contained.
  `tests/test_orchestrator.py::test_devtools_port_timeout_raises` asserts
  `not user_data_dir.exists()` after the raise.

The unit suite is green (178 tests, e2e separately green against
`https://example.com/`). The CR fixes are well-defended.

What this re-review surfaces:

1. **All nine WR-* warnings from the prior review are still open** —
   plan 02-05's scope was narrowly limited to CR-01/02/03. They're
   re-numbered here for traceability but each one's defect and fix are
   unchanged from the prior review.
2. **Two new Warning-tier findings** caught on a second-pass read:
   - `WR-10` — `WORKER_SCRIPT = Path(__file__).resolve().parents[2] / "lighthouse-worker" / "run.mjs"`
     hard-codes a repo-layout assumption that breaks the second `pyproject.toml`'s
     declared `[project.scripts]` entry point is installed as a wheel.
   - `WR-11` — `_launch_chrome_with_cdp_port` creates `user_data_dir` BEFORE
     `subprocess.Popen` and `sync_playwright()`; if either of those raises
     (Chrome not installed, Playwright browser not downloaded), the tempdir
     leaks the same way CR-03 used to leak on the timeout path. The CR-03
     fix closed one path; this is the structurally equivalent uncovered path.
3. **One narrative finding on the CR-01 fix itself** — the watchdog timer is
   cleared on `run.mjs:97` BEFORE the large `stdout.write` begins, so if the
   write hangs (consumer dies) the worker has no self-terminate safety net.
   The watchdog was supposed to be defense-in-depth; clearing it before the
   longest-running operation defeats the intent. Logged as Info (`IN-08`)
   because the consumer dying mid-write is a tiny edge case, but worth
   noting in case CR-01's fix needs a follow-up.

**No Critical-tier (BLOCKER) findings remain.** Ship-blocking gates have
been satisfied; the open Warnings should be triaged into Phase 3 backlog
or a fast-follow patch but do not warrant holding the Phase 02 PR.

## Critical Issues

None remain open. CR-01, CR-02, CR-03 from the prior 02-REVIEW.md are
closed in code and pinned by regression tests (see Summary).

## Warnings

### WR-01: Missing `node` binary surfaces as uncaught `FileNotFoundError`, not `MeasurementError` (carry-over)

**File:** `src/perfcrawl/lighthouse_worker.py:73-80`
**Issue:** `subprocess.run(["node", ...])` raises `FileNotFoundError` (a
subclass of `OSError`, not `subprocess.SubprocessError`) when `node` is not
on PATH. The `try` block in `run_one_sample` catches only
`subprocess.TimeoutExpired`, so a missing-node environment produces an
unmapped traceback instead of the D-15 `ExitCode.MEASUREMENT_ERROR` path.
`preflight()` checks for `node_modules/lighthouse/package.json` but never
verifies the `node` binary itself.

**Fix:** Extend `preflight()` to also verify the binary:

```python
import shutil
def preflight(worker_dir: Path | None = None) -> None:
    if shutil.which("node") is None:
        raise MeasurementError(
            "node binary not found on PATH — install Node >=22.19 "
            "(see CLAUDE.md § 'Installation')."
        )
    if worker_dir is None:
        worker_dir = WORKER_SCRIPT.parent
    marker = worker_dir / "node_modules" / "lighthouse" / "package.json"
    if not marker.exists():
        raise MeasurementError(...)  # existing message
```

Add `test_worker_preflight_raises_when_node_binary_missing` (monkeypatch
`shutil.which` → None).

---

### WR-02: D-10 version gate ignores the `.minor` portion of `EXPECTED_LIGHTHOUSE_MAJOR_MINOR` (carry-over)

**File:** `src/perfcrawl/normalizer.py:34-42` / `src/perfcrawl/constants.py:44`
**Issue:** The constant is named `EXPECTED_LIGHTHOUSE_MAJOR_MINOR: str = "13.x"`
but the gate enforces only the major:

```python
expected_major = EXPECTED_LIGHTHOUSE_MAJOR_MINOR.split(".")[0]  # "13"
if not actual.startswith(expected_major + "."):                 # "13."
```

`13.4.0`, `13.99.0`, and `13.0.0` all pass. The name and the constants.py
docstring ("Bumped only when ``lighthouse-worker/package-lock.json`` bumps
the pin") imply minor-band pinning that doesn't exist. Audit-shape drift
between 13.3 and a hypothetical 13.4 silently produces wrong PageResults —
the exact failure mode D-10 was supposed to prevent.

**Fix:** Rename the constant to `EXPECTED_LIGHTHOUSE_MAJOR: str = "13"` and
align the docstring (recommended — minor-level pinning is brittle), or
enforce the minor band in code.

---

### WR-03: Aggregator docstring claims `model_copy(update=...)` re-runs `_no_bare_inp` — it does not (carry-over)

**File:** `src/perfcrawl/aggregator.py:78-80, 110-114`
**Issue:** Both the docstring and the inline comment assert that
`model_copy(update=...)` "preserves model_config + validators (including
_no_bare_inp)". Pydantic v2 `model_copy` copies field values into a new
instance **without re-running** `@model_validator(mode='after')` hooks. The
defense-in-depth claim ("the labeled-proxy invariant cannot regress here")
is overstated; the aggregator is safe only because `samples[0]` passed
validation at construction.

**Fix (recommended):** Correct the comment to match reality:

```python
# model_copy preserves field types but does NOT re-run model validators.
# The labeled-proxy floor is the model layer (Phase 1); this aggregator
# carries forward samples[0]'s already-validated shape.
```

---

### WR-04: Empty `reportJson` / `reportHtml` strings are written as zero-byte artifact files (carry-over)

**File:** `src/perfcrawl/output.py:234-239` (and orchestrator.py:213-216)
**Issue:** Orchestrator stashes `lh.get("reportJson", "")` and
`lh.get("reportHtml", "")` (empty-string default when the key is missing).
output.py guards with `if report_json is not None:` and `if report_html
is not None:`, which is True for an empty string. Result: zero-byte
`lighthouse/<slug>.json` and `<slug>.html` files appear on disk when the
worker envelope is malformed. Downstream tooling reading the artifacts
will fail with a confusing "empty file" rather than a missing file.

**Fix:** Tighten the guard:

```python
if report_json:
    json_path = _unique_slug_path(lh_dir, base_slug, ".json")
    _atomic_write_text(json_path, report_json)
if report_html:
    html_path = _unique_slug_path(lh_dir, base_slug, ".html")
    _atomic_write_text(html_path, report_html)
```

Tighten the orchestrator side too — return `None` rather than `""` when
the key is missing, so the type matches the documented contract
`dict[str, tuple[str, str]]`.

---

### WR-05: `_launch_chrome_with_cdp_port` polling sleeps before the first existence check (carry-over)

**File:** `src/perfcrawl/orchestrator.py:104-116`
**Issue:** The loop is `for _ in range(max_attempts): time.sleep(...); if
port_file.exists(): ...`. Chrome may write `DevToolsActivePort` in
milliseconds, but every launch pays at least one 100 ms wait. Plus,
`max_attempts = int(DEVTOOLS_PORT_FILE_TIMEOUT_S /
DEVTOOLS_PORT_POLL_INTERVAL_S)` uses `int()` truncation, so e.g. `5.0 /
0.15 = 33.33` becomes 33 attempts — fewer than the timeout implies.

**Fix:** Monotonic-deadline loop, check first, sleep second:

```python
deadline = time.monotonic() + DEVTOOLS_PORT_FILE_TIMEOUT_S
while time.monotonic() < deadline:
    if port_file.exists():
        text = port_file.read_text().strip()
        if text:
            try:
                return proc, int(text.splitlines()[0]), user_data_dir
            except ValueError:
                pass
    time.sleep(DEVTOOLS_PORT_POLL_INTERVAL_S)
```

---

### WR-06: `subprocess.run(text=True, encoding="utf-8")` raises uncaught `UnicodeDecodeError` (carry-over)

**File:** `src/perfcrawl/lighthouse_worker.py:73-80`
**Issue:** If the Node worker emits non-UTF-8 bytes on stdout (rare, but
possible if a future Lighthouse version leaks a binary trace payload or a
UTF-16 BOM), `subprocess.run` raises `UnicodeDecodeError`, which the
`try` block does not catch. The exception bubbles up and violates the
D-15 three-exit-code contract.

**Fix:** Capture raw bytes and decode defensively:

```python
try:
    proc = subprocess.run(argv, capture_output=True, timeout=timeout_s)
except subprocess.TimeoutExpired:
    return None
stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
if proc.returncode != 0:
    sys.stderr.write(f"worker error (exit {proc.returncode}): {stderr}\n")
    return None
try:
    return json.loads(stdout)
except json.JSONDecodeError:
    return None
```

---

### WR-07: `page_slug` truncation can produce a trailing-dot filename (Windows-invalid) (carry-over)

**File:** `src/perfcrawl/slug.py:67-70`
**Issue:** After `stem = stem.strip("._-") or "_"`, the strip ensures no
trailing dot — but `return stem[:max_len]` can re-introduce one if the
80th character is a dot. Windows rejects filenames ending in `.` or
whitespace.

**Fix:**

```python
return (stem[:max_len].rstrip("._-")) or "_"
```

Add a regression test:

```python
def test_truncation_does_not_leave_trailing_dot():
    slug = page_slug("a" * 78 + "..", max_len=80)
    assert not slug.endswith(".")
```

---

### WR-08: `_unique_slug_path` has a TOCTOU race (carry-over, latent for Phase 3)

**File:** `src/perfcrawl/output.py:157-172`
**Issue:** The `if not candidate.exists(): return candidate` pattern is
textbook check-then-act TOCTOU. Phase 2 is single-URL-single-run so this
can't fire in practice, but Phase 3 (multi-page concurrent writes) will
ship the race unless this is fixed at the boundary.

**Fix:** Exclusive-creation pattern — open with `O_EXCL`
(`open(path, 'x')` or `os.O_CREAT | os.O_EXCL | os.O_WRONLY`), catch
`FileExistsError`, increment, retry. File a Phase 3 backlog item.

---

### WR-09: `csv.DictWriter` emits `\r\n` line endings because the in-memory buffer doesn't opt out (carry-over)

**File:** `src/perfcrawl/output.py:214-219`
**Issue:** Python's `csv` module always emits `\r\n` terminators per RFC
4180. `StringIO` doesn't apply newline translation, so `result.csv` ends
up with `\r\n` on every platform. Naive consumers (jq, awk, naive
`open(..., newline="\n")` readers) will treat the `\r` as a literal cell
character. `gspread` may upload the `\r` into cells.

**Fix:** Strip explicitly or use binary mode:

```python
buf = io.StringIO()
writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="raise")
writer.writeheader()
for page in run_record.pages:
    writer.writerow(_build_csv_row(run_record, page))
content = buf.getvalue().replace("\r\n", "\n")
_atomic_write_text(run_dir / "result.csv", content)
```

Regression: read `result.csv` in binary mode and assert no `\r` bytes.

---

### WR-10: `WORKER_SCRIPT` path resolution breaks when perfcrawl is installed as a wheel (NEW)

**File:** `src/perfcrawl/lighthouse_worker.py:30`
**Issue:**

```python
WORKER_SCRIPT: Path = Path(__file__).resolve().parents[2] / "lighthouse-worker" / "run.mjs"
```

`parents[2]` resolves to the repo root in editable/uv-run mode
(`<repo>/src/perfcrawl/lighthouse_worker.py` → `<repo>`), which is how
this slice was tested. But `pyproject.toml` declares
`[project.scripts] perfcrawl = "perfcrawl.cli:app"` — perfcrawl is meant
to be installable as a wheel. In that mode `__file__` is
`.../site-packages/perfcrawl/lighthouse_worker.py`, `parents[2]` is the
Python `lib/` directory, and `lighthouse-worker/run.mjs` does not exist
there. `preflight()` will fail with the "node_modules" message even when
the user did install everything correctly, because the path it builds is
nonsense.

The wheel build also doesn't include `lighthouse-worker/` (it's a sibling
of `src/`, not under `src/perfcrawl/`), so the Node worker is not shipped
with the Python package at all. The slice ships a tight `uv run` workflow
that works today; the install-via-pip story is broken.

**Fix:** Two options:

(a) **Short-term** — document explicitly in README/CLAUDE.md that the
project is run from a repo checkout via `uv run perfcrawl` and that
`pip install perfcrawl` is not yet supported. Drop or rename
`[project.scripts]` to reduce false expectations.

(b) **Better** — make the worker location configurable, defaulting to a
repo-relative search but allowing override via
`PERFCRAWL_WORKER_DIR` env var or a CLI flag. Phase 3 likely needs this
anyway for CI environments.

(a) is sufficient for this phase; file (b) as Phase 3 backlog.

---

### WR-11: Tempdir leak on Popen/Playwright failure inside `_launch_chrome_with_cdp_port` (NEW — structural sibling of CR-03)

**File:** `src/perfcrawl/orchestrator.py:81-101`
**Issue:** CR-03 (now closed) was about the tempdir leak on the
DevToolsActivePort-timeout path. There are two more uncovered failure
paths in the same launcher that leak the same tempdir the same way:

```python
def _launch_chrome_with_cdp_port():
    user_data_dir = Path(tempfile.mkdtemp(prefix="perfcrawl-chrome-"))  # line 81

    with sync_playwright() as p:                                        # line 86 — raises if PW broken
        chrome_path = p.chromium.executable_path                        # line 87 — raises if chromium not installed

    argv = [chrome_path, f"--user-data-dir={user_data_dir}", ...]
    proc = subprocess.Popen(argv, ...)                                  # line 97 — raises FileNotFoundError if chrome_path bad
```

If `sync_playwright()` raises (Playwright not installed, browser binary
not downloaded), or `subprocess.Popen` raises (executable not
executable), `user_data_dir` is leaked. Same disk-leak class as CR-03 —
chrome user-data-dirs accumulate hundreds of MB.

**Fix:** Wrap the section after `mkdtemp` in a try/except that
`shutil.rmtree`s the dir before re-raising:

```python
user_data_dir = Path(tempfile.mkdtemp(prefix="perfcrawl-chrome-"))
try:
    with sync_playwright() as p:
        chrome_path = p.chromium.executable_path
    argv = [chrome_path, ...]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except Exception:
    shutil.rmtree(user_data_dir, ignore_errors=True)
    raise
```

Add regression tests that monkeypatch `subprocess.Popen` and
`sync_playwright` to raise and assert `not user_data_dir.exists()`.

---

## Info

### IN-01: Worker doesn't validate `--form-factor` value (carry-over)

**File:** `lighthouse-worker/run.mjs:34-45`
**Issue:** `parseArgs` accepts any string for `--form-factor`. Python
validates `{"mobile", "desktop"}`, but a direct `node run.mjs
--form-factor=tablet` invocation forwards garbage to Lighthouse, which
silently uses its default.

**Fix:**

```javascript
if (!["mobile", "desktop"].includes(values["form-factor"])) {
  process.stderr.write(`worker error: --form-factor must be mobile|desktop\n`);
  process.exit(1);
}
```

---

### IN-02: `_render_human_table` indexes `run.pages[0]` without a defensive check (carry-over)

**File:** `src/perfcrawl/cli.py:84`
**Issue:** `page = run.pages[0]` raises `IndexError` if `run_record.pages`
is empty. The orchestrator's contract guarantees ≥1 (raises
`MeasurementError` otherwise), but a future Phase 3 regression that
returns an empty-pages RunRecord crashes here with a bare IndexError.

**Fix:**

```python
if not run.pages:
    out_console.print(f"[yellow]No pages measured for {run.target}[/yellow]")
    return
page = run.pages[0]
```

---

### IN-03: `chrome_version` is the full UA string, not the version triple (carry-over)

**File:** `src/perfcrawl/orchestrator.py:231-233`
**Issue:**
`chrome_version = lhr_for_metadata.get("environment", {}).get("hostUserAgent")`
stores a 100+ char UA in a field called `chrome_version`. The
test `test_runrecord_metadata_stamping` even encodes this oddity:
`assert "Chrome/137.0.7151.40" in run_record.chrome_version`.

**Fix:** Parse with a regex:

```python
import re
ua = lhr_for_metadata.get("environment", {}).get("hostUserAgent") or ""
m = re.search(r"Chrome/(\S+)", ua)
chrome_version = m.group(1) if m else None
```

---

### IN-04: `_check_version` doesn't normalize whitespace / `v` prefix (carry-over)

**File:** `src/perfcrawl/normalizer.py:34-42`
**Issue:** `actual = lhr.get("lighthouseVersion", "")` then
`actual.startswith("13.")`. A pre-release tag (`"13.3.0-beta.1"`)
passes; a `v` prefix or leading whitespace fails. Also: if the key
is present with `None`, `.startswith` raises `AttributeError` (not
`ValueError`), violating the version-gate's "raise loud" contract.

**Fix:**

```python
actual = (lhr.get("lighthouseVersion") or "").lstrip("v").strip()
if not actual or not actual.split(".")[0].isdigit() \
   or actual.split(".")[0] != expected_major:
    raise ValueError(...)
```

---

### IN-05: `_atomic_write_text` doesn't fsync (carry-over)

**File:** `src/perfcrawl/output.py:135-154`
**Issue:** The docstring claims "either lands whole or doesn't appear at
all." True for FS-level rename atomicity, false across power loss
between `os.replace` and pages being flushed. For local-dev artifacts
this is fine; the docstring just slightly overstates the guarantee.

**Fix:** Either fsync the file + parent dir, or soften the docstring.

---

### IN-06: `import io` performed inside `write_outputs` (carry-over)

**File:** `src/perfcrawl/output.py:212`
**Issue:** Inline import adds 5–10 µs per call and complicates static
analysis. `io.StringIO` is stdlib and very cheap to import once.

**Fix:** Move `import io` to the top of the file.

---

### IN-07: Watchdog `setTimeout` writes to stderr without a drain callback (carry-over) + IN-08 (NEW): CR-01 fix clears the watchdog before the long-running write

**File:** `lighthouse-worker/run.mjs:27-30, 97-104`

**Issue (IN-07):** The watchdog handler `process.stderr.write(...);
process.exit(1)` writes synchronously then immediately exits. stderr's
small payload makes truncation unlikely today, but the same drain
pattern CR-01 enforces should apply for consistency.

**Issue (IN-08 — NEW, related to CR-01 fix):** Line 97 calls
`clearTimeout(watchdog)` **before** `process.stdout.write(payload, ...)`
on line 98. The watchdog was supposed to be the defense-in-depth
self-terminate timer. Clearing it before the largest write defeats the
defense — if the consumer dies mid-write and `stdout.write`'s callback
never fires (unlikely, but the failure mode the watchdog exists to
catch), the worker hangs indefinitely with no timer to fire.

**Fix:** Clear the watchdog inside the callback, so the timer's lease
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

This also addresses IN-07 by giving the stderr write a callback.

---

## Coverage gaps (not findings — for the verifier)

- No test exercises `subprocess.run(["node", ...])` raising
  `FileNotFoundError` (WR-01).
- No test for `_launch_chrome_with_cdp_port` cleaning up
  `user_data_dir` on Popen / sync_playwright failures (WR-11).
- No test for `lighthouse_worker.WORKER_SCRIPT` being resolvable when
  perfcrawl is installed as a wheel (WR-10).
- No regression for a trailing-dot truncation (WR-07).
- No regression for `\r\n` line endings in `result.csv` (WR-09).

---

_Reviewed: 2026-05-29_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
