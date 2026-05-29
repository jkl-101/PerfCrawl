# Phase 2: Single-Page Measurement Slice - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-28
**Phase:** 2-single-page-measurement-slice
**Areas discussed:** Chrome/CDP plumbing strategy, CLI surface for `measure`, Network facts source, Per-URL failure handling

---

## Chrome/CDP plumbing strategy

### Q1: Who launches Chrome and how does Lighthouse attach in Phase 2?

| Option | Description | Selected |
|--------|-------------|----------|
| Playwright launches, Lighthouse attaches via debug port | Python uses Playwright `launch_persistent_context(args=['--remote-debugging-port=0'])`, captures the port, passes to Node worker; the worker calls `lighthouse(url, {port})` without launching its own Chrome. Front-loads the Phase 4 auth-handoff spike. | ✓ |
| Node worker owns Chrome via chrome-launcher | Node worker calls `chrome-launcher.launch()` + `lighthouse(url, {port})` directly; no Playwright in Phase 2. Simpler now; Phase 4 will tear it out. | |
| Use a wrapper library (playwright-lighthouse or similar) | Delegate handoff to an existing npm wrapper. Trades control for an external dependency. | |

**User's choice:** Playwright launches, Lighthouse attaches via debug port.
**Notes:** Front-loads the riskiest plumbing seam from STATE.md so Phase 4 inherits a proven shape and only adds `storage_state` + `disableStorageReset:true`.

---

### Q2: How does Python talk to the Node Lighthouse worker?

| Option | Description | Selected |
|--------|-------------|----------|
| One-shot subprocess per measurement, JSON over stdout | Each sample run = `subprocess.run(['node','lighthouse-worker/run.mjs',…])` returning a JSON blob on stdout. Stateless, isolated failures, ~150 ms process-start cost. | ✓ |
| Long-running Node worker, line-delimited JSON over stdin/stdout | Spawn Node once; JSON-RPC over stdin/stdout. Saves ~150 ms per sample; adds protocol design. | |
| Spawn Lighthouse via its CLI directly (no custom worker) | `subprocess.run(['npx','lighthouse',url,'--port=N',…])`. No worker dir; loses programmatic control. | |

**User's choice:** One-shot subprocess per measurement, JSON over stdout.

---

### Q3: How does each of the N samples get a cold cache?

| Option | Description | Selected |
|--------|-------------|----------|
| One Chrome process, fresh Playwright context per sample | Playwright launches Chrome once. For each sample, Python creates a fresh `browser_context`, the worker runs Lighthouse against it, Python closes the context. The exact shape Phase 4 will reuse. | ✓ |
| One Chrome process, rely on Lighthouse's built-in cache clearing | Lighthouse clears cache/cookies by default. Simpler but couples cold-cache invariant to a Lighthouse internal we don't control. | |
| Fresh Chrome process per sample | Kill and relaunch Chrome between samples. Slow (~1–2s per sample startup). Overkill. | |

**User's choice:** One Chrome process, fresh Playwright context per sample.

---

### Q4: Where does the Node worker live and how are its deps pinned?

| Option | Description | Selected |
|--------|-------------|----------|
| `lighthouse-worker/` sibling dir with own `package.json` + `package-lock.json` | Tiny Node project committed to the repo. Pinned `lighthouse@13.3.0`. `npm ci` for reproducibility. | ✓ |
| `lighthouse-worker/` with `package.json` only (no lockfile, latest-on-install) | Simpler repo state; possibly-different Lighthouse minor per machine. Bad for reproducibility. | |
| No worker dir — install Lighthouse globally via npm and call it | `npm i -g lighthouse@13.3.0`. Fragile CI/new-machine bootstrap. | |

**User's choice:** `lighthouse-worker/` sibling dir with own `package.json` + `package-lock.json`.

---

## CLI surface for `measure`

### Q5: Command shape for measuring a single URL?

| Option | Description | Selected |
|--------|-------------|----------|
| `perfcrawl measure <url>` | Subcommand verb; matches `perfcrawl crawl <url>` etc. Locks the verb pattern. | ✓ |
| `perfcrawl <url>` (positional, default verb) | Cleaner one-off use; ambiguous against Phase 3 (single-page vs whole-site crawl). | |
| `perfcrawl audit <url>` | More natural conversationally; conflicts with the `audit`/`review` workflow vocabulary the tech-stack doc uses for "measure". | |

**User's choice:** `perfcrawl measure <url>`.

---

### Q6: What does `perfcrawl measure <url>` write to stdout by default?

| Option | Description | Selected |
|--------|-------------|----------|
| Human-readable summary by default; `--json` flag for machine output | Rich-rendered table; `--json` opts into machine output; progress to stderr. | ✓ |
| Machine JSON by default; `--pretty` for human | Pipes cleanly; bad UX for interactive use. | |
| Always both: human summary on stderr, JSON on stdout | Two streams to reason about; conflicts with Rich progress bars on stderr. | |

**User's choice:** Human-readable summary by default; `--json` flag for machine output.

---

### Q7: Where do raw Lighthouse JSON+HTML artifacts, the flat CSV, and the full-fidelity JSON land on disk?

| Option | Description | Selected |
|--------|-------------|----------|
| `output/<run_id>/` with `result.{json,csv}` + `lighthouse/<page-slug>.{json,html}` | UUID-named run dir; one join key with SQLite store; sanitized page-slug. | ✓ |
| `output/<timestamp>-<host>/` | Human-sortable but collision-prone; loses run_id join key. | |
| Configurable via `--output-dir` flag, default `./perfcrawl-output/` | Most flexible; more plumbing surface area. | |

**User's choice:** `output/<run_id>/` with `result.{json,csv}` + `lighthouse/<page-slug>.{json,html}`.
**Notes:** Page-slug derivation must sanitize `..` and decoded percent-encoded dots (Phase 1 IN-02 landmine).

---

### Q8: How does `--samples N` surface in the human summary and the persisted record?

| Option | Description | Selected |
|--------|-------------|----------|
| Summary shows medians only with a `(median of N)` footer; raw distribution lives in JSON/SQLite | Compact stdout; samples[] in persisted record. | ✓ |
| Summary shows median ± stdev (or min–max) for every metric | Derivable from samples[]; noisier stdout. | |
| With `--verbose`, print all N rows of raw samples in a stacked table | Power-user toggle; captured as deferred idea. | |

**User's choice:** Summary shows medians only with a `(median of N)` footer; raw distribution lives in JSON/SQLite.

---

## Network facts source

### Q9: Where do network-level facts come from?

| Option | Description | Selected |
|--------|-------------|----------|
| Pure Lighthouse audits (single source) | Parse `audits['network-requests']`, `audits['server-response-time']`, etc. One source, one failure mode. | ✓ |
| Lighthouse for scores+CWV, Playwright/CDP `Network` events for facts (research-recommended) | Double-handles the page load; pays off on multi-page crawls. | |
| Hybrid — Lighthouse for facts in Phase 2, refactor to CDP in Phase 3 | Inconsistent with Area 1's "front-load the spike" logic. | |

**User's choice:** Pure Lighthouse audits (single source).
**Notes:** CDP-direct network capture deferred to Phase 3.

---

### Q10: How defensive is the Lighthouse-JSON normalizer about audit-shape drift?

| Option | Description | Selected |
|--------|-------------|----------|
| Strict + version-gated: bail with a clear error if Lighthouse-reported version != pinned | Worker stamps version; Python compares against expected major-minor; mismatch is hard error. | ✓ |
| Lenient: best-effort parse, default missing fields to None, log a warning | Silent data loss on shape drift. | |
| Strict but unversioned: fail on any missing required field, don't check the version | Late warning instead of early. | |

**User's choice:** Strict + version-gated.

---

### Q11: INP-proxy mapping and human-output labeling?

| Option | Description | Selected |
|--------|-------------|----------|
| Normalizer maps TBT → `inp_proxy_tbt_ms`; human summary labels it `INP (lab proxy, TBT-based)` | Belt-and-suspenders: model + normalizer + display all enforce the labeling. | ✓ |
| Normalizer maps the field; human summary just shows `INP-proxy` | Shorter but easier to misread as field INP. | |
| Don't surface it in the human summary at all; only persist it | Hides a headline metric. | |

**User's choice:** Normalizer maps TBT → `inp_proxy_tbt_ms`; human summary labels it `INP (lab proxy, TBT-based)`.

---

### Q12: What goes into the `diagnostics` field for Phase 2?

| Option | Description | Selected |
|--------|-------------|----------|
| A curated subset: Lighthouse `audits` IDs with `score < 1` (opportunities + diagnostics) only | Bounded JSON blob; Phase 5 AI input scoped to real problems. | ✓ |
| The full Lighthouse `audits` dict, verbatim | Large blob; raw JSON is on disk anyway via OUT-03. | |
| Empty/null in Phase 2; populate in Phase 5 from the raw Lighthouse file on disk | Defers; couples Phase 5 to OUT-03's file layout. | |

**User's choice:** A curated subset (audits with `score < 1`).

---

## Per-URL failure handling

### Q13: The URL returns a non-2xx status (e.g. 404, 500). What happens?

| Option | Description | Selected |
|--------|-------------|----------|
| Record a partial PageResult: status_code set, network facts captured, score/CWV fields null; exit 0 | Foundation for Phase 3's non-2xx tagging; the data point is preserved. | ✓ |
| Hard-fail: exit non-zero, no PageResult written | Loses data; bad for Phase 3 crawl ergonomics. | |
| Record a full PageResult but tag it with an `is_error` flag | Requires a Phase 1-style additive evolution; premature. | |

**User's choice:** Record a partial PageResult; exit 0.

---

### Q14: Lighthouse subprocess crashes or hangs (timeout). What happens?

| Option | Description | Selected |
|--------|-------------|----------|
| Per-sample timeout (default 60s), retry once per sample, then fail that sample | Resilient to first-sample flakiness; ALL-fail → exit 2. | ✓ |
| No retry; one crash fails the whole `measure` command (exit 2) | Strictest; flaky on cold-start. | |
| Retry on timeout, fail on non-timeout crash, no per-sample timeout | Assumes failures are distinguishable from exit code; without timeout a hang blocks indefinitely. | |

**User's choice:** Per-sample timeout (default 60s), retry once per sample, then fail that sample.

---

### Q15: What exit codes does `perfcrawl measure` use?

| Option | Description | Selected |
|--------|-------------|----------|
| 0 success (page measured, even if non-2xx); 1 user error; 2 measurement error | Three buckets; reserves room for Phase 6 budget codes (10+). | ✓ |
| Binary: 0 success, 1 anything else | Simplest; loses triage signal. | |
| Granular: 0/1/2/3/4 with separate codes for partial success, persistence error, etc. | Diminishing returns past 3 buckets. | |

**User's choice:** 0 success / 1 user error / 2 measurement error.

---

### Q16: When some-but-not-all samples succeed, how is the median computed?

| Option | Description | Selected |
|--------|-------------|----------|
| Median over successful samples, store the raw distribution as-is, no padding | Honest data; matches MetricSample's variable-length `samples` list. | ✓ |
| Require all N to succeed; if any fails, the metric is null | Brittle; defeats the point of median-of-N. | |
| Require a minimum (e.g. ⌈N/2⌉) successful samples; below that, metric is null | Reasonable middle ground; deferred until real failure rates justify the knob. | |

**User's choice:** Median over successful samples, no padding, no floor.
**Notes:** Minimum-N floor captured as a deferred idea.

---

## Claude's Discretion

User chose recommended options throughout the discussion. The following are explicitly left for the planner/executor:

- Exact module split between `cli.py` / `orchestrator.py` / `normalizer.py` / `output.py` (and similar internal layout).
- The 60s per-sample timeout constant value and the `--samples` default value (likely 3).
- Rich progress-bar density and exact summary-table column widths.
- File-naming details below D-07: collision-suffix format (`__1` vs `-1`), slug truncation length.
- Whether `lighthouse-worker/` install is auto-invoked via preflight `npm ci` or required as a documented setup step.
- Whether `output/` is `.gitignore`d by default and whether the CLI emits a hint pointing the user at the run dir after a successful measurement.
- Sync vs async Playwright wrapping (sync simpler for one URL; revisit at Phase 3).

## Deferred Ideas

- CDP-direct network capture (Playwright `Network` events for the waterfall) — Phase 3 candidate.
- `--verbose` rendering of all N raw samples in a stacked Rich table — UX polish, no phase blocker.
- Minimum-sample-N floor for median publication (e.g. ⌈N/2⌉) — pending real-world failure rates.
- Auto-invoked `npm ci` on first `perfcrawl measure` run — Claude's-discretion item.
- `output/` gitignore default + post-run hint — Claude's-discretion UX polish.
- Dedicated exit codes for Phase 6 budget verdicts (BUDG-01) — out of Phase 2 scope.
