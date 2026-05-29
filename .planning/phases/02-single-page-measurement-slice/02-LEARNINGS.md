---
phase: 2
phase_name: "single-page-measurement-slice"
project: "PerfCrawl"
generated: "2026-05-29"
counts:
  decisions: 17
  lessons: 9
  patterns: 12
  surprises: 7
missing_artifacts: []
---

# Phase 2 Learnings: single-page-measurement-slice

## Decisions

### D-13 partial-result 404 fixture synthesized, not raw-captured
Real LH-13 captures of true 404 URLs return a top-level `runtimeError: ERRORED_DOCUMENT_REQUEST` with no `network-requests.details.items`. The D-13 contract assumed a captured-but-non-2xx waterfall, so the 404 fixture was synthesized by overlaying `statusCode=404` + nulled categories on the real 200 capture.

**Rationale:** The normalizer tests the documented D-13 path (captured non-2xx). The "Lighthouse couldn't gather at all" path is the orchestrator's responsibility, layered in plan 02-03 (pre-flight `httpx.HEAD()` or `runtimeError.message` regex). Splitting the two failure shapes keeps each layer honest.
**Source:** 02-01-SUMMARY.md (Deviation 1)

---

### Watchdog timer in run.mjs at 55s, behind Python's 60s subprocess timeout
The Node worker self-terminates at 55s via `setTimeout`; Python's `subprocess.run(timeout=60)` is the backstop.

**Rationale:** Defense-in-depth — the worker's own timeout fires first so the killer is the worker itself (clean exit), not SIGTERM from Python. Mirrors Phase 1's "never trust the next layer to enforce your invariant" stance.
**Source:** 02-01-SUMMARY.md (decisions block, Assumption A5)

---

### MeasurementError defined in lighthouse_worker.py, re-exported by orchestrator
The exception lives in the leaf module (worker) and is re-exported from `orchestrator.__all__`.

**Rationale:** Both `preflight()` (in worker) and `measure_url` (in orchestrator) need to raise it. Defining it in the leaf module breaks the circular-import risk while the CLI still gets a one-line `from perfcrawl.orchestrator import …` for the public surface.
**Source:** 02-03-SUMMARY.md (key-decisions)

---

### Popen + connect_over_cdp instead of launch_persistent_context
Chromium is launched via `subprocess.Popen(--remote-debugging-port=0)` and Playwright attaches via `connect_over_cdp`.

**Rationale:** `launch_persistent_context` would force `browser.new_context()` to fail (`.browser is None` corner). The Popen + CDP attach gives a real `Browser` object with `.new_context()` available — required for D-03 cold-cache cycling. Pitfall 5 fix from RESEARCH.
**Source:** 02-03-SUMMARY.md (key-decisions)

---

### OUT-03 raw artifacts stashed from FIRST successful sample, not aggregated
The orchestrator returns `(RunRecord, dict[url_key, (reportJson, reportHtml)])` where the dict holds only one `.json` + `.html` per page.

**Rationale:** The existing Drive-archived workflow keeps one .json + one .html per page, not one per sample. HIGH-1 plan-check fix made this an orchestrator-side responsibility so 02-04 CLI doesn't back-edit the API.
**Source:** 02-03-SUMMARY.md (key-decisions)

---

### DB path = output_dir / 'perfcrawl.db' (colocated with artifacts)
The SQLite history store is colocated with the per-run artifact tree under `--output-dir`.

**Rationale:** Reuses the existing artifact-location flag; gives one cleanup point; a fresh `--output-dir` run starts with no history (helpful for reproducible smoke runs). Phase 1 LEARNINGS contemplated a global `~/.perfcrawl/store.db`; that's a Phase 6 decision and can be layered via a config flag.
**Source:** 02-04-SUMMARY.md (key-decisions)

---

### Subcommand-forcing via hidden `_internal` no-op alongside `measure`
A second hidden `@app.command()` is registered to force Typer to dispatch as `perfcrawl measure <url>` rather than the implicit-root form.

**Rationale:** D-05 requires the verb. Typer collapses single-`@app.command()` apps to implicit-root invocation. The hidden sibling restores verb-dispatching shape; Phase 3 `crawl` and Phase 6 `budget` will naturally replace it.
**Source:** 02-04-SUMMARY.md (key-decisions, Deviation 2)

---

### Default-deselect e2e marker via `addopts -m 'not e2e'`
`uv run pytest` excludes e2e-marked tests by default; opt-in with `uv run pytest -m e2e`.

**Rationale:** Marker REGISTRATION (Phase 2-01) silences warnings but does NOT deselect — pytest's default is to include all tests regardless of marker. Plan's `<done>` explicitly required the default run to skip the e2e (needs Node + Chrome + network). The `-m 'not e2e'` addopt is the configuration-level fix; explicit `-m e2e` overrides cleanly via argument priority.
**Source:** 02-04-SUMMARY.md (key-decisions, Deviation 4)

---

### CSV writer builds in-memory StringIO before atomic write
`csv.DictWriter` writes into `io.StringIO`, then the content is atomic-written via `tempfile.NamedTemporaryFile` + `os.replace`.

**Rationale:** `csv.DictWriter` against an opened file would hold a write window where `result.csv` exists but is incomplete. Building in memory + os.replace gives the same one-shot-or-nothing semantics as `result.json` (CR-01-style file-I/O atomicity).
**Source:** 02-04-SUMMARY.md (key-decisions)

---

### page-column = '' in Phase 2 (vs <title> or path-derived)
The `page` CSV column is left blank for Phase 2's single-URL audit.

**Rationale:** Open Q2 RESOLVED in RESEARCH — Phase 2 is single-URL; the human label adds no information the `url` column doesn't already carry. Phase 3's multi-page crawler will fill it from `<title>` when discovery surfaces it.
**Source:** 02-04-SUMMARY.md (key-decisions)

---

### `aggregate_samples([])` returns honest-empty MetricSample; `aggregate_page_samples([])` raises
The inner reducer returns `MetricSample(median=None, samples=[])` on empty input; the outer page-level reducer raises `ValueError`.

**Rationale:** D-16 expects `[]` to be a valid per-metric outcome when every sample's value failed (honest empty, never raise). But at the page level the orchestrator must never call with `[]` per D-14 (per-sample loop always produces at least one PageResult per page); a raise surfaces that orchestrator bug loudly.
**Source:** 02-02-SUMMARY.md (decisions made)

---

### `model_copy(update=...)` chosen over reconstructing PageResult
The aggregator uses Pydantic v2 `model_copy(update={...})` instead of building a fresh `PageResult(...)` from scratch.

**Rationale:** `model_copy` preserves `model_config` and the `_no_bare_inp` validator path more cheaply. The labeled-proxy invariant cannot regress by construction — no `inp` variable name appears in aggregator code, only the literal `"inp_proxy_tbt_ms"` field key. (WR-03 later flagged that `model_copy` does NOT re-run model_validators; the docstring claim was overstated but the by-construction guarantee still holds.)
**Source:** 02-02-SUMMARY.md (decisions made)

---

### Stored honest-empty MetricSample on all-None metric fields
When every sample's `lcp_ms` is None, the aggregated PageResult's `lcp_ms` becomes `MetricSample(median=None, samples=[])` rather than `None`.

**Rationale:** Keeps the model shape uniform — downstream consumers always see a MetricSample slot to inspect, not a sometimes-MetricSample-sometimes-None field.
**Source:** 02-02-SUMMARY.md (decisions made)

---

### CR-01 fixed with verbatim callback-form patch from 02-REVIEW.md
The Node worker uses `process.stdout.write(payload, (err) => process.exit(...))`; `clearTimeout(watchdog)` moves before the write.

**Rationale:** The verifier had already reproduced the exact failure on a real network run and the REVIEW.md patch was the single-source-of-truth fix. No alternative explored.
**Source:** 02-05-SUMMARY.md (decision 02-05-D1)

---

### CR-02 reap-on-kill applied at BOTH kill sites
`try: chrome.wait(timeout=5)` (with `subprocess.TimeoutExpired` catch) runs after `.kill()` in both `measure_url`'s finally AND `_launch_chrome_with_cdp_port`'s timeout path.

**Rationale:** The TimeoutExpired arm is dormant under the test fake but mandatory in code — a hung Chrome on the `wait()` could otherwise block the orchestrator's exit indefinitely.
**Source:** 02-05-SUMMARY.md (decision 02-05-D2)

---

### CR-03 closed by making launcher self-contained on failure
`shutil.rmtree(user_data_dir, ignore_errors=True)` runs BEFORE `raise MeasurementError(...)` inside `_launch_chrome_with_cdp_port`.

**Rationale:** The caller's finally cannot help because the `chrome, port, user_data_dir = ...` tuple-assignment never completes when the launcher raises — that's the precise reason the leak existed. The order `kill → wait → rmtree → raise` is enforced by a grep guard (`rmtree_idx < raise_idx`).
**Source:** 02-05-SUMMARY.md (decision 02-05-D3)

---

### Phase 2 plan 05 regression test uses a self-written Node shim, not real run.mjs
The CR-01 regression test in `tests/test_worker.py` spawns a tiny shim Node script that emits a 1.5MB JSON envelope, instead of invoking the real `lighthouse-worker/run.mjs`.

**Rationale:** No network/Chrome dependency keeps the test in the default suite at 0.08s. The shim mirrors run.mjs's drain pattern exactly, so it tests the language/runtime-level contract — not just the Lighthouse-specific surface.
**Source:** 02-05-SUMMARY.md (decision 02-05-D4)

---

## Lessons

### Pipe-buffer truncation is invisible to a fully-mocked subprocess test suite
Every `tests/test_worker.py` case mocked `subprocess.run` with small synthetic JSON returns. The kernel pipe-buffer (~64KB Linux / ~16KB macOS) truncation path was never exercised. The unit suite was 100% green while the real CLI was 100% broken on every real LH payload (200KB–2MB).

**Context:** Verifier ran `uv run perfcrawl measure https://example.com --samples 1` and got exit 2 / "all 1 samples failed". CR-01 had been flagged in 02-REVIEW.md but skipped because unit tests passed. CR-01 → real-subprocess >1MB regression test became Phase 2 plan 05 Task 2.
**Source:** 02-VERIFICATION.md (Gap #1), 02-05-SUMMARY.md (Task 2)

---

### `process.exit(0)` after `process.stdout.write(payload)` truncates large payloads
Node's `process.stdout.write` returns false and queues the remainder asynchronously when the kernel pipe buffer fills. A bare `process.exit(0)` immediately after kills the worker before the kernel drains, leaving the consumer with truncated bytes.

**Context:** The callback form `write(payload, (err) => process.exit(...))` is the documented fix; the synchronous form is a foot-gun specifically when stdout is a pipe (vs a TTY which is line-buffered). Manual file-redirect of the same payload produced 956,709 bytes of valid JSON, confirming the worker logic was right and only the stdout-to-pipe handoff was broken.
**Source:** 02-VERIFICATION.md (Gap #1 root cause), 02-05-SUMMARY.md (Gap #1)

---

### `process.kill()` without `process.wait()` leaks defunct (zombie) processes on POSIX
Killed Chromium stays a `<defunct>` process until the Python interpreter exits. The Chrome lifecycle invariant T-02-03-Z was unit-tested via mocks but the real-process reap was missing in code.

**Context:** A long-running session (multiple `measure_url` calls in one interpreter) would accumulate zombies. Fix is `try: chrome.wait(timeout=5); except subprocess.TimeoutExpired: pass` after every `.kill()`. The `_FakeChromeProc.waited` contract was added to make the missing reap detectable in unit tests too.
**Source:** 02-VERIFICATION.md (Gap #2), 02-05-SUMMARY.md (Task 3)

---

### Launcher failures cannot rely on caller `finally` to clean up
`chrome, port, user_data_dir = _launch_chrome_with_cdp_port()` never completes when the launcher raises, so the caller's finally never sees any of those names — `user_data_dir` is undefined at the `try:` block scope. Self-cleanup must happen inside the launcher BEFORE the raise.

**Context:** Every DevToolsActivePort timeout leaked hundreds of MB of Chromium scaffolding into `$TMPDIR`. Pre-fix verifier observation: one pre-existing `perfcrawl-chrome-md8xa574` tempdir from an earlier session, still 0 bytes, dated 4 hours before our 14:57 runs.
**Source:** 02-VERIFICATION.md (Gap #2), 02-05-SUMMARY.md (decision 02-05-D3)

---

### Pytest marker REGISTRATION ≠ default DESELECTION
Registering a marker in `[tool.pytest.ini_options].markers` only silences unknown-marker warnings. Pytest's default behavior is to include all tests regardless of marker. To skip e2e by default, `addopts` must add `-m 'not e2e'`.

**Context:** The plan's `<done>` required the default `uv run pytest` to skip `test_e2e_measure_example_com`, but Phase 2-01 only registered the marker; the default run still ran the e2e and failed because Chrome/Node weren't installed for the unit-suite run. Fix landed in the Task 3 commit alongside the e2e test file.
**Source:** 02-04-SUMMARY.md (Deviation 4)

---

### `CliRunner(mix_stderr=False)` was removed in Typer 0.26 / Click 8.2+
The kwarg crashes with `TypeError: CliRunner.__init__() got an unexpected keyword argument 'mix_stderr'`. The current default splits stdout/stderr automatically into `result.stdout` and `result.stderr`.

**Context:** Plan body specified `CliRunner(mix_stderr=False)` to split streams for the `--json` test. The current SDK already does what the kwarg used to opt into. Switched to `CliRunner()` with a docstring comment so a future reader doesn't try to re-add it.
**Source:** 02-04-SUMMARY.md (Deviation 1)

---

### Typer with a single `@app.command()` dispatches as the implicit-root command
With one command registered, `perfcrawl measure <url>` collapses to `perfcrawl <url>` and the verb becomes an "unexpected extra argument". Registering a second (hidden) command restores verb dispatch.

**Context:** D-05 required the verb-form invocation so future `crawl` / `budget` siblings live in the same namespace. Hidden `_internal` no-op alongside `measure` is the documented workaround; gets naturally replaced by real sibling verbs in later phases.
**Source:** 02-04-SUMMARY.md (Deviation 2)

---

### Source-level grep guards trip on docstrings that quote the forbidden token
A `re.findall(r"\binp\b(?!_proxy)", src)` meta-test on `normalizer.py` source flagged comments like `"never construct a local variable named 'inp'…"` — the regex doesn't distinguish comments from code. Same pattern bit `lighthouse_worker.py` (`shell=True` in docstring), `orchestrator.py` (`launch_persistent_context`, `socket.bind(0)` in docstring), and `cli.py` (`Inp` / `inp` in docstring) across plans 01, 03, and 04.

**Context:** The cross-plan fix is paraphrase, never quote: rewrite the docstring to reference "the bare INP token (forbidden field names enumerated in `models._FORBIDDEN_INP_FIELDS`)" or "the shell-invocation kwarg" instead of the literal forbidden tokens. Pattern recurred three times in Phase 2 — Phase 3+ plans that introduce new grep guards should pre-bake a banner comment noting this constraint.
**Source:** 02-01-SUMMARY.md (Deviation 2), 02-03-SUMMARY.md (Deviation 1), 02-04-SUMMARY.md (Deviation 3)

---

### `EXPECTED_LIGHTHOUSE_MAJOR_MINOR = "13.x"` advertises minor-pinning the gate doesn't enforce
The constant name implies minor-version pinning but `_check_version` only compares the major segment. `13.99.0` and `13.4.0-beta.1` pass silently.

**Context:** Flagged as WR-02 by the verifier. Doesn't block Phase 2 goal but the constant-name vs gate-behavior mismatch is a misleading contract. Rename to `EXPECTED_LIGHTHOUSE_MAJOR` or extend the gate to honor the minor segment.
**Source:** 02-VERIFICATION.md (Anti-Patterns table — WR-02)

---

## Patterns

### One-shot ESM Node worker invoked as `subprocess.run(["node", ...])` returning JSON envelope on stdout
A sibling Node subproject (`lighthouse-worker/`) with `"type": "module"`, exact-pinned `lighthouse@13.3.0`, ESM `run.mjs` that takes `--port`/`--url`/`--form-factor` via `parseArgs`, runs Lighthouse, and writes `JSON.stringify({lhr, reportJson, reportHtml})` to stdout. Python wraps it as `run_one_sample(*, port, url, emulation, timeout_s) -> dict | None` collapsing three failure modes (`TimeoutExpired`, non-zero exit, `JSONDecodeError`) into None.

**When to use:** Any time a Python project needs to call a native-Node tool. The pattern keeps the Node surface area to one file + one Python wrapper; tests mock at the wrapper. Reusable for a future Node-side `web-vitals` interaction shim in Phase 3.
**Source:** 02-01-SUMMARY.md (run.mjs), 02-03-SUMMARY.md (lighthouse_worker.py)

---

### Three-failure-modes-to-None subprocess wrapper (D-14)
Any subprocess invocation has three orthogonal failures: timeout, non-zero exit, malformed output. Collapsing all three into a single `Optional[dict]` lets the caller make the retry-vs-drop decision in one branch.

**When to use:** Any Python-wraps-foreign-binary subprocess seam. Forward `proc.stderr` to `sys.stderr` on non-zero exit so an actionable breadcrumb reaches the CLI's stderr passthrough.
**Source:** 02-03-SUMMARY.md (patterns)

---

### DevToolsActivePort file polling instead of `socket.bind(0)`
Chrome with `--remote-debugging-port=0` writes the resolved port to `<user_data_dir>/DevToolsActivePort` (line 1). Polling that file for up to `DEVTOOLS_PORT_FILE_TIMEOUT_S` is the documented contract.

**When to use:** Whenever you need a kernel-picked Chrome debugging port. NEVER pre-pick a port via `socket.bind(0)` then pass it to Chrome — TOCTOU race; Chrome may bind a different port. Grep guard `assert re.search(r'socket\.bind\([\'\"]?\s*0', src) is None` enforces this.
**Source:** 02-03-SUMMARY.md (patterns, Pitfall 1)

---

### Side-channel return value `(record, side_data)` for measurement → output boundary
`measure_url` returns `tuple[RunRecord, dict[url_key, (reportJson, reportHtml)]]`. The CLI destructures and forwards the dict to `output.write_outputs(record, output_dir=, raw_artifacts=)`.

**When to use:** When a measurement-time-only resource (raw bytes, intermediate prompts, debug captures) must survive to the output writer without polluting the model. Keeps OUT-03 a measurement-time responsibility and avoids back-editing the orchestrator API in later plans. Carries through to Phase 5 if AI analysis needs to surface intermediate prompts.
**Source:** 02-03-SUMMARY.md (patterns)

---

### Reap-on-kill recipe: `kill() + try/wait(timeout=5)/except subprocess.TimeoutExpired`
After every `proc.kill()`, immediately call `proc.wait(timeout=5)` inside a try/except for `subprocess.TimeoutExpired`. The TimeoutExpired arm is dormant under tests but mandatory in code so a hung process can't block exit.

**When to use:** Every spawned long-running subprocess on POSIX. Without the wait, killed processes stay `<defunct>` until the interpreter exits. T-02-05-LEAK-PROC mitigation.
**Source:** 02-05-SUMMARY.md (patterns, decision 02-05-D2)

---

### Self-cleaning launcher on failure path
A function that allocates resources (tempdir, port, subprocess) and may raise must `rmtree`/`cleanup` BEFORE the raise — the caller's `try:` block never sees the partial assignment when the function raises during the tuple `chrome, port, user_data_dir = launcher(...)`.

**When to use:** Any resource-allocating helper whose return is consumed via tuple/multi-target assignment in a `try:` block. The caller's `finally` cannot reach undefined names. Enforced by grep guard `rmtree_idx < raise_idx`.
**Source:** 02-05-SUMMARY.md (patterns, decision 02-05-D3)

---

### Atomic file write via `tempfile.NamedTemporaryFile + os.replace`
Build the file's content in memory (StringIO / bytes), open a NamedTemporaryFile in the destination directory, write+flush, then `os.replace(tmp.name, final.path)`. The consumer-visible path either contains the complete new content or the old content — never a half-written file.

**When to use:** Any module that emits coupled files (JSON + CSV here; would extend to a Sheets+HTML+JSON triple in Phase 6). File-I/O analog of store.py's `with conn:` transaction. Cross-file consistency is enforced by the run_id directory being the unit of consumption — a partial directory missing one file is unambiguously a crash state, and a downstream reader fails loud rather than reading half a run.
**Source:** 02-04-SUMMARY.md (patterns established)

---

### Locked column list in module-level constant (`CSV_COLUMNS`)
The CSV schema lives in one constant at module scope; the writer reads from it; tests assert column-order equality against it.

**When to use:** Any flat-row output with a shape consumers depend on. Mirrors `registry.py` (Phase 1) and `constants.py` (Phase 2) "one-editable-place" pattern. The locked list becomes the single source of truth for downstream consumers (Phase 6 Sheets exporter will read the same constant).
**Source:** 02-04-SUMMARY.md (patterns established)

---

### Defense-in-depth labeled-proxy invariant: 4 layers
For any "forbidden synonym" field (real-INP vs lab-INP-proxy): (1) model validator (`_no_bare_inp` in Phase 1); (2) source-level grep meta-test on each module that touches the field (`\binp\b(?!_proxy)`); (3) constant for any display label (`INP_PROXY_DISPLAY_LABEL`); (4) explicit column header name (`inp_proxy_tbt_ms`).

**When to use:** Any field with a misleading common-knowledge synonym. Pattern: any future field with a "forbidden synonym" problem (e.g. real-INP vs lab-INP-proxy; real-FCP vs delayed-FCP) gets the same defense-in-depth structure. Each layer has its own meta-test asserting the bare-form tokens never appear.
**Source:** 02-04-SUMMARY.md (patterns established), 02-01-SUMMARY.md

---

### IN-02 sanitization boundary applied at every output writer
`page_slug()` is called at the consumer (`output.py`), not just at the producer. Defense-in-depth above 02-01 Task 1's sanitization floor — every user-derived path is reasserted at the consumer regardless of where it came from.

**When to use:** Any file write whose name is derived from user input or third-party data. The boundary belongs at every layer that constructs a path, not only at the entry point — the cost is one function call, the value is no chain-of-trust audit needed.
**Source:** 02-04-SUMMARY.md (patterns established)

---

### Test split: unit-level (mocked subprocess) + gated e2e (real subprocess)
`tests/test_*.py` mocks subprocess for speed (0.21s for 177 tests); `tests/test_e2e.py` runs the real subprocess with `pytest.mark.e2e` + `addopts -m 'not e2e'` default-exclude. Opt-in via `pytest -m e2e`.

**When to use:** Whenever a subprocess seam exists. Default suite stays fast; the e2e is the pre-`/gsd-verify-work` smoke check. CRITICALLY: the unit mocks must use payloads representative of real-world size, or supplement with a real-subprocess regression test for buffer/IPC contracts (CR-01 lesson).
**Source:** 02-04-SUMMARY.md (patterns established), 02-05-SUMMARY.md (Gap #1 lesson)

---

### Pure-function reducer over the Phase 1 model
`aggregate_samples` and `aggregate_page_samples` depend only on `perfcrawl.models` and stdlib `math`/`statistics`. No I/O, no orchestrator/normalizer dependency.

**When to use:** Anything that transforms model instances. Lets the module ship in parallel with sibling plans (the aggregator was independent of the normalizer; both shipped in wave 1). Test factories (`_make_sample`) keep cross-sample tests readable without pulling the heavy conftest `sample_run` RunRecord fixture.
**Source:** 02-02-SUMMARY.md (patterns established)

---

## Surprises

### Real LH-13 captures of true 404 URLs return `runtimeError`, NOT a 404 waterfall
The plan's `<behavior>` block assumed the 404 fixture would have `audits["network-requests"].details.items[]` with a main-document `statusCode: 404`. Real captures of `https://example.com/__nope-404__` instead returned a top-level `runtimeError: ERRORED_DOCUMENT_REQUEST` and had NO `details.items` on `network-requests` at all (only an `errorMessage`).

**Impact:** Forced the 404 fixture to be synthesized (overlay statusCode=404 + nulled categories onto the 200 capture) rather than raw-captured. Created a documented two-tier failure model: the normalizer handles "captured-but-non-2xx" (some servers return 200-with-error-page, some 404 handlers serve full HTML); the orchestrator (later, plan 03) handles the "Lighthouse couldn't gather at all" case via a pre-flight HEAD or a `runtimeError.message` regex fallback.
**Source:** 02-01-SUMMARY.md (Deviation 1)

---

### 177 green unit tests, 100% broken real CLI — pipe-buffer truncation hidden by mocks
The full unit suite passed in 0.20s with 177 tests. The verifier ran `uv run perfcrawl measure https://example.com --samples 1` and got exit 2 / "all 1 samples failed" — the phase goal was unattainable. The test-suite blind spot: every `tests/test_worker.py` case mocked `subprocess.run` with small synthetic returns, so the real pipe-buffer truncation path was never exercised. CR-01 had been flagged in 02-REVIEW.md but skipped because the test suite was green.

**Impact:** The phase shipped 02-01..04 with the wrong confidence signal. Forced a fifth gap-closure plan (02-05) to land the CR-01 callback-form patch in `run.mjs` AND a real-subprocess >1MB regression test. Cross-cutting lesson: any subprocess-IPC contract needs a real-binary test with a payload representative of production sizes; the mock isn't sufficient.
**Source:** 02-VERIFICATION.md (Gap #1), 02-05-SUMMARY.md (Gap #1)

---

### One pre-existing `perfcrawl-chrome-md8xa574` tempdir survived from a pre-CR-03 run
While verifying that the 02-05 fixes worked, `ls -d $TMPDIR/perfcrawl-chrome-*` showed one 0-byte leftover from 10:35 (4 hours before the 14:57 runs). That predated the fix and was archaeological — not a regression — but a visible reminder that pre-fix every Chrome-launch failure leaked a tempdir.

**Impact:** Confirmed the verifier's CR-03 hypothesis (DevToolsActivePort timeout was leaking tempdirs). Provided a concrete pre/post artifact: post-fix runs left no new tempdirs after success or failure.
**Source:** 02-05-SUMMARY.md (Gap #2 verification)

---

### The grep guard recurrence — same docstring-vs-token bug hit three times in Phase 2
Phase 2 plan 01 (`normalizer.py` with `'inp'`/`'inp_ms'` in docstring), plan 03 (`lighthouse_worker.py`/`orchestrator.py` with `shell=True`/`launch_persistent_context`/`socket.bind(0)` in docstrings), and plan 04 (`cli.py` with `Inp`/`inp` in docstrings) all hit the same bug on first GREEN run.

**Impact:** Same docstring-paraphrase fix applied three times. By plan 04 it was clear this is a structural pattern — any grep-guard ships with a banner comment in the plan's `read_first` block noting the constraint up-front. Phase 3+ plans that introduce new grep guards should pre-bake this.
**Source:** 02-04-SUMMARY.md (Deviation 3 — "now thrice-confirmed")

---

### Pytest marker registration didn't deselect — the e2e test ran in the default suite
Phase 2-01 registered the `e2e` marker in `[tool.pytest.ini_options].markers`, assuming that was sufficient to keep `pytest` from running e2e-marked tests by default. It wasn't — pytest's default behavior includes all tests regardless of marker; registration only silences unknown-marker warnings.

**Impact:** The first `uv run pytest -x` after committing `tests/test_e2e.py` ran the e2e and crashed (Chrome/Node not pre-installed for unit-suite runs). Fixed by adding `-m 'not e2e'` to `addopts`. Explicit `-m e2e` overrides the addopts via argument priority.
**Source:** 02-04-SUMMARY.md (Deviation 4)

---

### Typer collapses single-`@app.command()` apps to implicit root, breaking the verb form
Plan body assumed `perfcrawl measure <url>` would work with `@app.command(name="measure")`. First test run hit `Got unexpected extra argument(s) (https://example.com)` — Typer treats a single-command app as the implicit root command, so the verb becomes an unexpected argument.

**Impact:** Required registering a hidden `_internal` no-op alongside `measure` to force verb dispatch. The hidden command never surfaces in help output and gets naturally replaced by Phase 3 `crawl` / Phase 6 `budget` siblings.
**Source:** 02-04-SUMMARY.md (Deviation 2)

---

### Click 8.2+ removed `CliRunner(mix_stderr=False)` — stream-split is now the default
Plan body specified `CliRunner(mix_stderr=False)`. Test runs crashed with `TypeError: CliRunner.__init__() got an unexpected keyword argument 'mix_stderr'`.

**Impact:** The current SDK splits stdout/stderr automatically into `result.stdout` and `result.stderr` — the kwarg used to opt into this behavior, now it IS the behavior. Switched to `CliRunner()` with a docstring comment so a future reader doesn't try to re-add the removed kwarg.
**Source:** 02-04-SUMMARY.md (Deviation 1)
