---
phase: 02-single-page-measurement-slice
reviewed: 2026-05-29T00:00:00Z
depth: standard
files_reviewed: 21
files_reviewed_list:
  - .gitignore
  - lighthouse-worker/.gitignore
  - lighthouse-worker/package.json
  - lighthouse-worker/run.mjs
  - pyproject.toml
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
  critical: 3
  warning: 9
  info: 7
  total: 19
status: issues_found
---

# Phase 02: Code Review Report

**Reviewed:** 2026-05-29
**Depth:** standard
**Files Reviewed:** 21 (one extra file `tests/test_worker.py` reviewed beyond the 21 in `files_reviewed`)
**Status:** issues_found

## Summary

Phase 02 implements a single-URL end-to-end measurement slice: Python orchestrator (`measure_url`) launches Chrome via `subprocess.Popen --remote-debugging-port=0`, polls for `DevToolsActivePort`, then loops N times invoking a Node Lighthouse worker via `subprocess.run`, normalizes the LHR into `PageResult`, aggregates samples via median-of-N, writes JSON/CSV/raw-LH artifacts atomically, and persists to SQLite. The labeled-INP-proxy invariant (D-11/D-15) is well-defended in four layers.

Overall the slice is well-structured and the security/labeling invariants are visibly cared for. However, **three correctness defects can cause silent data loss, zombie processes, and disk leaks under realistic failure modes**, and several boundary/coverage gaps undermine the defense-in-depth claims the code asserts.

Standout concerns:

1. The Node worker calls `process.exit()` immediately after a synchronous `process.stdout.write(JSON.stringify(...))`. For typical LHRs (several hundred KB to >1 MB), the kernel pipe buffer (~64 KB on Linux) fills, the write becomes asynchronous, and `process.exit()` aborts before the buffer drains. Python then sees truncated stdout → `JSONDecodeError` → sample is silently dropped (D-14 retry/drop loop hides it). Hard to reproduce in unit tests because mocked `subprocess.run` returns full JSON; real runs against a real site will hit this for any non-trivial LHR.
2. The orchestrator never `chrome.wait()`s after `chrome.kill()`, so the killed Chrome stays a zombie until the Python process exits (T-02-03-Z prevention claim is incomplete).
3. `_launch_chrome_with_cdp_port()` raises `MeasurementError` on the DevToolsActivePort timeout path WITHOUT removing the freshly-created `user_data_dir` — every Chrome-launch failure leaks a tempdir of arbitrary size into `/tmp`. The outer try/finally in `measure_url` only cleans up dirs returned from a *successful* launch.

## Critical Issues

### CR-01: Node worker risks truncated JSON-over-stdout on real-size LHRs (silent data loss)

**File:** `lighthouse-worker/run.mjs:92-100`
**Issue:** The worker calls `process.stdout.write(JSON.stringify({lhr, reportJson, reportHtml}))` followed immediately by `clearTimeout(watchdog); process.exit(0);`. A realistic LHR + report bundle is hundreds of KB to several MB. On Linux the default pipe buffer is ~64 KB; when the buffer fills, `stdout.write` returns false and queues the remainder asynchronously. `process.exit(0)` then terminates the process before the kernel has drained the pipe, and Python's `subprocess.run(capture_output=True)` receives a truncated JSON payload. The Python wrapper catches `json.JSONDecodeError` and returns `None` (lighthouse_worker.py:90-94), which the orchestrator's D-14 loop treats as a sample failure — silently dropping the sample, potentially every sample for a heavy-page run. Three samples all failing this way is collapsed to `MeasurementError("all 3 samples failed")` with no breadcrumb pointing at the truncated-stdout root cause. None of the unit tests catch this because they all return small mocked payloads.

**Fix:** Force a drain before exit. Either pass a callback to `stdout.write` and exit from it, or use `process.stdout.end()`:

```javascript
const payload = JSON.stringify({ lhr: result.lhr, reportJson, reportHtml });
clearTimeout(watchdog);
process.stdout.write(payload, (err) => {
  if (err) {
    process.stderr.write(`worker error: stdout write failed: ${err.message}\n`);
    process.exit(1);
  }
  process.exit(0);
});
// Do NOT call process.exit synchronously after this point.
```

Add a regression test that feeds a >1 MB synthetic `reportJson` through the worker (or shims it) and asserts Python parses the full payload.

---

### CR-02: Chrome is killed but never waited on — zombie process leak

**File:** `src/perfcrawl/orchestrator.py:249-253`
**Issue:** The cleanup finally block calls `chrome.kill()` (sends SIGKILL) but never calls `chrome.wait()` to reap the exit status. On POSIX, the killed Chromium remains a `<defunct>` zombie in the process table until the parent (the Python interpreter) exits or explicitly reaps it. For a long-lived caller (a future crawl that does many `measure_url` calls back-to-back, or a CI agent that runs perfcrawl many times in one Python session), this exhausts the PID table or shows up as resource leaks in `ps`. The T-02-03-Z claim in the docstring ("no zombie Chrome") is therefore incomplete — Chrome is killed, but not reaped. Same gap on the timeout-failure path inside `_launch_chrome_with_cdp_port` (line 120).

**Fix:** Always `wait()` after `kill()` with a short timeout, mirroring the standard subprocess cleanup recipe:

```python
try:
    chrome.kill()
    try:
        chrome.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Already SIGKILL'd; the kernel will reap eventually. Log it.
        pass
except Exception:
    pass
shutil.rmtree(user_data_dir, ignore_errors=True)
```

And mirror the same change inside `_launch_chrome_with_cdp_port` at the DevToolsActivePort-timeout path (after `proc.kill()` on line 120). The `_FakeChromeProc.wait` stub in `tests/test_orchestrator.py:128-129` already exists, suggesting the original author considered this but didn't wire it up — add a `chrome_proc.waited` assertion to `test_chrome_killed_on_success` / `test_chrome_killed_on_failure` to lock this in.

---

### CR-03: DevToolsActivePort timeout leaks the user_data_dir tempdir

**File:** `src/perfcrawl/orchestrator.py:118-123` (and `165-253` caller pairing)
**Issue:** `_launch_chrome_with_cdp_port` creates `user_data_dir = Path(tempfile.mkdtemp(prefix="perfcrawl-chrome-"))` (line 81), then on the timeout path calls `proc.kill()` and `raise MeasurementError(...)` (lines 120-123) without `shutil.rmtree(user_data_dir)`. The CALLER's cleanup is:

```python
chrome, port, user_data_dir = _launch_chrome_with_cdp_port()  # raises
try:
    ...
finally:
    chrome.kill()
    shutil.rmtree(user_data_dir, ignore_errors=True)
```

When the call to `_launch_chrome_with_cdp_port` itself raises, the assignment never completes, the `try` block is never entered, and the finally never runs. The tempdir is leaked into `/tmp` (or `$TMPDIR`) every time Chrome fails to launch. Chromium user-data-dirs accumulate hundreds of MB across cookies/cache scaffolding. Test coverage misses this: `test_devtools_port_timeout_raises` only asserts `fake_proc.killed is True` and never inspects the tempdir on disk.

**Fix:** Make the launcher self-contained on its failure path.

```python
# Timeout: never wrote the file. Kill Chrome + remove tempdir before raising.
proc.kill()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    pass
shutil.rmtree(user_data_dir, ignore_errors=True)
raise MeasurementError(
    f"Chrome did not write DevToolsActivePort within {DEVTOOLS_PORT_FILE_TIMEOUT_S}s"
)
```

Extend `test_devtools_port_timeout_raises` to assert `not user_data_dir.exists()` after the raise.

---

## Warnings

### WR-01: Missing `node` binary surfaces as uncaught `FileNotFoundError`, not `MeasurementError`

**File:** `src/perfcrawl/lighthouse_worker.py:73-80`
**Issue:** `subprocess.run(["node", ...])` raises `FileNotFoundError` (not `subprocess.SubprocessError`) when `node` is not on the user's PATH. Neither `run_one_sample` nor `preflight()` catches this; `preflight()` only checks for `node_modules/lighthouse/package.json`, not the `node` binary itself. The exception bubbles out of the orchestrator's per-sample loop and crashes `measure_url` with an unmapped traceback — the CLI's `except MeasurementError`/`except UserError` pair (cli.py:151-156) lets it escape and Typer prints a generic stack trace, violating the D-15 three-exit-code contract.

**Fix:** Either catch the exception in `run_one_sample` and surface it as `None` with a stderr breadcrumb, or extend `preflight()` to also verify `shutil.which("node") is not None`. The latter is preferable because the message can be actionable:

```python
def preflight(worker_dir: Path | None = None) -> None:
    if shutil.which("node") is None:
        raise MeasurementError(
            "node binary not found on PATH — install Node >=22.19 "
            "(see CLAUDE.md § 'Installation')."
        )
    ...
```

Add `test_worker_preflight_raises_when_node_binary_missing` (mock `shutil.which` → None).

---

### WR-02: D-10 version gate ignores the `.minor` portion of `EXPECTED_LIGHTHOUSE_MAJOR_MINOR`

**File:** `src/perfcrawl/normalizer.py:34-42` / `src/perfcrawl/constants.py:44`
**Issue:** The constant is named `EXPECTED_LIGHTHOUSE_MAJOR_MINOR: str = "13.x"`, but the gate only enforces the major number:

```python
expected_major = EXPECTED_LIGHTHOUSE_MAJOR_MINOR.split(".")[0]  # "13"
if not actual.startswith(expected_major + "."):                 # "13."
```

So `13.4.0`, `13.99.0`, and `13.0.0` all pass. The constant name advertises minor-level pinning the code doesn't deliver, and the docstring (constants.py:43) says "Bumped only when ``lighthouse-worker/package-lock.json`` bumps the pin" implying any pin change is reflected here. Real audit-shape drift between LH 13.3 and a hypothetical 13.4 (renamed audits, new numericValue scaling) will pass silently and produce wrong PageResults — the exact failure mode D-10 was supposed to prevent.

**Fix:** Either (a) rename the constant to `EXPECTED_LIGHTHOUSE_MAJOR: str = "13"` and align the docstring, or (b) actually enforce the minor band:

```python
# Option (b) — keep the name, enforce the actual band:
expected = EXPECTED_LIGHTHOUSE_MAJOR_MINOR  # "13.x"
expected_major = expected.split(".")[0]
if not actual.startswith(expected_major + "."):
    raise ValueError(...)
# Don't claim minor pinning in the constant name if you don't enforce it.
```

I recommend (a) — minor-level audit-shape pinning is brittle (LH minor releases routinely add new audits) and the project's de facto contract is major-version pinning.

---

### WR-03: Aggregator docstring claims `model_copy(update=...)` re-runs `_no_bare_inp` validator — it does not

**File:** `src/perfcrawl/aggregator.py:78-80, 110-114`
**Issue:** Both the function docstring and the inline comment assert that `model_copy(update=...)` "preserves the validator path" and that this defends the labeled-proxy invariant. Pydantic v2 `model_copy` does NOT re-run model_validator(mode='after') hooks by default — it copies field values into a new instance without revalidating. The defense-in-depth claim ("the labeled-proxy invariant cannot regress here") is therefore overstated; the aggregator survives only because `samples[0]` was already validated at construction. If a future refactor lets the aggregator construct a `PageResult` from scratch with bare-INP fields, this layer would not catch it and the comment misleads the reader into thinking it would.

**Fix:** Either (a) actually re-validate after copy:

```python
return samples[0].model_copy(update=updates).model_validate(
    samples[0].model_copy(update=updates).model_dump()
)
# or pass deep=True if Pydantic exposes a re-validate flag in your version.
```

or (b) correct the docstring to match reality:

```
# model_copy preserves field types but does NOT re-run model validators.
# Defense-in-depth here relies on samples[0] having passed _no_bare_inp at
# construction; the labeled-proxy floor is the model layer (Phase 1), not
# this aggregator.
```

I recommend (b) — re-validation is wasted CPU per page, and the model floor is genuinely the right layer.

---

### WR-04: Empty `reportJson` / `reportHtml` strings are written as zero-byte artifact files

**File:** `src/perfcrawl/output.py:234-239`
**Issue:** The orchestrator stashes `lh.get("reportJson", "")` and `lh.get("reportHtml", "")` (orchestrator.py:204-207) — empty strings when the keys are missing. The output writer then guards with `if report_json is not None:` and `if report_html is not None:`, which is True for an empty string. Result: zero-byte `lighthouse/<slug>.json` and `<slug>.html` files appear on disk when the worker's envelope is malformed. Downstream tooling reading the artifacts will fail with a confusing "empty file" rather than a missing file. Also, the empty-string `_atomic_write_text` call performs a useless tempfile+rename roundtrip.

**Fix:** Tighten the guard:

```python
if report_json:
    json_path = _unique_slug_path(lh_dir, base_slug, ".json")
    _atomic_write_text(json_path, report_json)
if report_html:
    html_path = _unique_slug_path(lh_dir, base_slug, ".html")
    _atomic_write_text(html_path, report_html)
```

— and consider tightening the orchestrator side too (return `None` rather than `""` when the key is missing, so the type matches the documented contract `dict[str, tuple[str, str]]`).

---

### WR-05: `_launch_chrome_with_cdp_port` polling sleeps before the first existence check, never checks at t≈0

**File:** `src/perfcrawl/orchestrator.py:104-116`
**Issue:** The loop is

```python
for _ in range(max_attempts):
    time.sleep(DEVTOOLS_PORT_POLL_INTERVAL_S)  # ALWAYS sleeps first
    if port_file.exists():
        ...
```

Chrome may write `DevToolsActivePort` within milliseconds. The current code guarantees at least one 100ms wait before the first check, adding constant overhead to every successful launch. More importantly, `max_attempts = int(5.0 / 0.1) = 50` iterations — but each iteration includes one full sleep AND the file read, so the effective timeout is 50 × (0.1 + read_time) > 5s. With `int()` truncation on the division, anything that rounds down (e.g. `5.0 / 0.15`) gives fewer attempts than expected too.

**Fix:** Check first, then sleep:

```python
deadline = time.monotonic() + DEVTOOLS_PORT_FILE_TIMEOUT_S
while time.monotonic() < deadline:
    if port_file.exists():
        text = port_file.read_text().strip()
        if text:
            first_line = text.splitlines()[0]
            try:
                port = int(first_line)
            except ValueError:
                pass
            else:
                return proc, port, user_data_dir
    time.sleep(DEVTOOLS_PORT_POLL_INTERVAL_S)
```

A monotonic-deadline loop is robust to clock skew and the `int()`-truncation arithmetic problem.

---

### WR-06: `subprocess.run(..., text=True, encoding="utf-8")` will raise `UnicodeDecodeError` on malformed worker stdout

**File:** `src/perfcrawl/lighthouse_worker.py:74-80`
**Issue:** If the Node worker (or a Lighthouse internal that the worker doesn't suppress) emits non-UTF-8 bytes on stdout — possible if a future Lighthouse version logs a UTF-16 BOM, or a binary trace payload leaks through — `subprocess.run` will raise `UnicodeDecodeError`, which is NOT caught by the `try` block (which catches only `subprocess.TimeoutExpired`). The exception bubbles up and crashes the orchestrator with an unmapped traceback, again violating the D-15 three-exit-code contract. Same issue if the worker crashes such that mixed text/binary lands on stdout.

**Fix:** Either capture raw bytes and decode defensively, or broaden the exception handler:

```python
try:
    proc = subprocess.run(
        argv,
        capture_output=True,
        timeout=timeout_s,
    )  # bytes mode; decode below
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

### WR-07: `page_slug` truncation can produce a trailing-dot filename (Windows-invalid)

**File:** `src/perfcrawl/slug.py:67-70`
**Issue:** After `stem = stem.strip("._-") or "_"`, the strip ensures no leading/trailing dot — but then `return stem[:max_len]` can re-introduce a trailing dot if the 80th character of the input lands on one. Windows rejects filenames ending in `.` or whitespace (and silently strips them on creation, causing collision with the no-suffix variant). The project's de facto target is POSIX, but the worker's HTML report is meant to be openable on any developer's laptop, including Windows ones.

**Fix:** Strip again after truncation:

```python
return (stem[:max_len].rstrip("._-")) or "_"
```

Add a test:

```python
def test_truncation_does_not_leave_trailing_dot():
    long = "a" * 78 + ".."  # 80 chars total, ends in dots
    slug = page_slug(long, max_len=80)
    assert not slug.endswith(".")
```

---

### WR-08: `_unique_slug_path` has a TOCTOU race that survives within concurrent runs

**File:** `src/perfcrawl/output.py:157-172`
**Issue:** The `if not candidate.exists(): return candidate` pattern is the textbook check-then-act TOCTOU. Phase 2 is single-URL-single-run so collisions can't happen *within* a run; but two concurrent `perfcrawl measure` invocations sharing an `--output-dir` and the same `<run_id>` cannot collide because run_id is a fresh UUID. The race surfaces only in Phase 3 (multi-page) or if a user replays a run. Not a load-bearing bug today, but a latent landmine — the comment ("forward-compat for Phase 3") sets up the next phase to ship with a known race rather than fixing it at the boundary now.

**Fix:** Use exclusive-creation atomically — open with `O_EXCL` (`open(path, 'x')`) or `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)`, catch `FileExistsError`, increment, retry. Defer to Phase 3 if scope is the concern, but file a backlog item linking back to this finding.

---

### WR-09: `csv.DictWriter` emits `\r\n` line endings because the in-memory buffer doesn't open with `newline=""`

**File:** `src/perfcrawl/output.py:214-219`
**Issue:** The CSV is built in `io.StringIO()`, then `_atomic_write_text` writes the buffer's `.getvalue()` to disk. The Python `csv` module always emits `\r\n` line terminators (per RFC 4180), and `StringIO` doesn't apply newline translation. The result file ends up with `\r\n` line endings on every platform. Many downstream tools (jq, awk, naive Python `open(..., newline="\n")` readers) will silently treat the `\r` as a literal cell character at end-of-row. The CSV's Google-Sheets-import target tolerates it, but local tooling may not — and `gspread` may upload the `\r` into cells.

**Fix:** Wrap the buffer to apply Python's text-mode newline translation, or strip `\r` explicitly:

```python
buf = io.StringIO(newline="")  # match csv module's expectation
writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="raise")
writer.writeheader()
for page in run_record.pages:
    writer.writerow(_build_csv_row(run_record, page))
content = buf.getvalue().replace("\r\n", "\n")  # canonical LF on disk
_atomic_write_text(run_dir / "result.csv", content)
```

— or open the atomic-write target in `newline=""` mode. Add a regression test that reads `result.csv` in binary mode and asserts no `\r` bytes.

---

## Info

### IN-01: Worker doesn't validate `--form-factor` value before forwarding to Lighthouse

**File:** `lighthouse-worker/run.mjs:34-45`
**Issue:** `parseArgs` defaults `form-factor` to `"mobile"` but accepts any string. The Python orchestrator validates the allowed set (`{"mobile", "desktop"}`), but the worker accepts e.g. `--form-factor=tablet` and forwards it to Lighthouse, which then silently uses its default. Defense-in-depth at the boundary is missing — a direct `node run.mjs` invocation can pass garbage.

**Fix:**

```javascript
if (!["mobile", "desktop"].includes(values["form-factor"])) {
  process.stderr.write(`worker error: --form-factor must be mobile|desktop\n`);
  process.exit(1);
}
```

---

### IN-02: `_render_human_table` indexes `run.pages[0]` without a defensive check

**File:** `src/perfcrawl/cli.py:84`
**Issue:** `page = run.pages[0]` raises `IndexError` if `run_record.pages` is empty. The orchestrator's contract guarantees ≥1 (it raises `MeasurementError` otherwise), but a future regression that returns an empty-pages RunRecord (e.g. a Phase 3 crawl with zero reachable pages) would crash here with a bare IndexError instead of a friendly message.

**Fix:** Early-return with a clean message:

```python
if not run.pages:
    out_console.print(f"[yellow]No pages measured for {run.target}[/yellow]")
    return
page = run.pages[0]
```

---

### IN-03: `chrome_version` field is populated with full UA string, not just version

**File:** `src/perfcrawl/orchestrator.py:222-224`
**Issue:** `chrome_version = lhr_for_metadata.get("environment", {}).get("hostUserAgent")` stores something like `Mozilla/5.0 (...) Chrome/137.0.7151.40 ...` in a field called `chrome_version`. The field name suggests the version triple alone. Downstream CSV/Sheets consumers will see a 100+ char UA in a "version" column. The test `test_runrecord_metadata_stamping` even encodes this oddity: `assert "Chrome/137.0.7151.40" in run_record.chrome_version`.

**Fix:** Parse the version with a regex at stamping time:

```python
import re
ua = lhr_for_metadata.get("environment", {}).get("hostUserAgent", "") or ""
m = re.search(r"Chrome/(\S+)", ua)
chrome_version = m.group(1) if m else None
```

---

### IN-04: `_check_version` doesn't normalize whitespace / prerelease tags

**File:** `src/perfcrawl/normalizer.py:34-42`
**Issue:** `actual = lhr.get("lighthouseVersion", "")` then `actual.startswith("13.")`. An LH JSON with a prerelease tag (`"13.3.0-beta.1"`) passes; an LH JSON with leading whitespace or a `v` prefix (`"v13.3.0"`) would fail. Not a realistic regression today, but the boundary is sloppy.

**Fix:** Strip + compare numerically:

```python
actual = (lhr.get("lighthouseVersion") or "").lstrip("v").strip()
if not actual.split(".")[0].isdigit() or actual.split(".")[0] != expected_major:
    raise ValueError(...)
```

---

### IN-05: `_atomic_write_text` doesn't fsync the file or its parent directory

**File:** `src/perfcrawl/output.py:135-154`
**Issue:** The docstring claims "either lands whole at target or it doesn't appear at all." That's true for FS-level rename atomicity. But on power loss or hard crash between `os.replace` and the OS flushing pages, the file may exist with stale content, or the directory entry may show neither name. For local-dev artifacts this is fine; the docstring just slightly overstates the guarantee.

**Fix:** If real crash-durability is needed:

```python
with tempfile.NamedTemporaryFile(...) as tmp:
    tmp.write(content)
    tmp.flush()
    os.fsync(tmp.fileno())
    tmp_path = tmp.name
os.replace(tmp_path, target)
# Optional: fsync the parent dir for full atomic-durability.
dir_fd = os.open(target.parent, os.O_DIRECTORY)
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
```

Otherwise, soften the docstring claim.

---

### IN-06: `import io` is performed inside `write_outputs` instead of at module top

**File:** `src/perfcrawl/output.py:212`
**Issue:** `import io  # local: only needed here, keeps the module surface tight` — inline imports add 5-10µs per call and complicate static analysis. `io.StringIO` is stdlib and very cheap to import once. The "keeps the module surface tight" justification is weak — `io` is not a heavy import.

**Fix:** Move `import io` to the file's top with the other stdlib imports.

---

### IN-07: Watchdog timer in `run.mjs` writes to stderr but doesn't drain it before exit

**File:** `lighthouse-worker/run.mjs:27-30`
**Issue:** Same pattern as CR-01: `process.stderr.write(...)` then `process.exit(1)` without a callback. stderr buffer is small (and the message is also small), so truncation is unlikely in practice — but the same drain pattern should be applied for consistency. If a future maintainer expands the stderr breadcrumb to include the LHR-so-far or a stack trace, this becomes a real loss.

**Fix:**

```javascript
const watchdog = setTimeout(() => {
  process.stderr.write(
    `worker error: self-terminated after ${WATCHDOG_MS}ms watchdog\n`,
    () => process.exit(1),
  );
}, WATCHDOG_MS);
```

---

## Coverage gaps (not findings — for the verifier)

- No test exercises the `subprocess.run(["node", ...])` `FileNotFoundError` path (related to WR-01).
- No test for `_launch_chrome_with_cdp_port` cleaning up `user_data_dir` on the timeout-raise path (CR-03).
- No test for Chrome being `wait()`'d after `kill()` (CR-02). The `_FakeChromeProc.wait` stub exists but no assertion uses it.
- `test_no_tmp_files_left_after_write` only checks the happy path. No coverage for a crash mid-`_atomic_write_text` leaving stray `*.tmp.*` files.
- No regression test for a multi-megabyte worker payload round-trip (CR-01).

---

_Reviewed: 2026-05-29_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
