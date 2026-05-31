# Phase 2: Single-Page Measurement Slice - Context

**Gathered:** 2026-05-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Running `perfcrawl measure <url>` against ONE URL audits it end-to-end: launches Playwright-owned Chrome → runs Lighthouse-over-CDP through a Node worker → normalizes the Lighthouse JSON into a `PageResult` filled against the Phase 1 model → persists a `RunRecord` to the SQLite store → writes the flat one-row CSV, the full-fidelity JSON, and the raw Lighthouse JSON+HTML artifacts to `output/<run_id>/`. Median-of-N sampling (`--samples N`, default TBD by planner) ships in this phase so Phase 6 regression flagging stands on stable distributions from day one.

**In scope:** the `perfcrawl measure <url>` CLI command (Typer), the Playwright orchestration layer (launch Chrome with `--remote-debugging-port`, manage per-sample browser contexts for cold cache), the `lighthouse-worker/` Node subproject (one-shot subprocess per sample, JSON over stdout), the Lighthouse-JSON-to-`PageResult` normalizer, run persistence via the existing Phase 1 store, output-on-disk layout (`output/<run_id>/{result.json,result.csv,lighthouse/<slug>.{json,html}}`), human-vs-`--json` stdout rendering, exit-code policy, and the single-URL failure-handling regime that every later phase inherits.

**Out of scope (belongs to later phases):** site-wide crawling, sitemap/robots discovery, per-host politeness, depth/page caps (Phase 3); authenticated sessions and the destructive-link denylist (Phase 4 — Phase 2's CDP plumbing is chosen so Phase 4 only adds `storage_state` + `disableStorageReset:true`); AI-generated Observation/Cause/Optimization (Phase 5 — the `analysis` slot stays null); Google Sheets export, variance-aware regression gating, output-format selection flags (Phase 6); v2 backend metrics (BACK-01..03 — still gated behind the security spike); INP as a field metric from PSI/CrUX (lab proxy only, model layer enforces this).

</domain>

<decisions>
## Implementation Decisions

### Chrome / CDP Plumbing Strategy
- **D-01:** **Playwright launches Chrome; Lighthouse attaches via debug port.** Python orchestrator calls Playwright `launch_persistent_context(args=["--remote-debugging-port=0"])` (port chosen by Chrome to avoid collisions), captures the resolved port, and passes it to the Node worker. The Node worker calls `lighthouse(url, { port })` WITHOUT launching its own Chrome via `chrome-launcher`. **Front-loads the Phase 4 auth-handoff spike**: Phase 4 then only adds `storage_state` reuse and `disableStorageReset: true` on the Lighthouse config, no architectural change.
- **D-02:** **One-shot Node subprocess per measurement, JSON over stdout.** Each of the N samples = `subprocess.run(["node","lighthouse-worker/run.mjs", "--port=N", "--url=X", "--emulation=mobile", "--throttling=..."])`. Worker writes the full Lighthouse JSON to stdout, exits non-zero on failure. Stateless boundary; one sample's failure can't poison the next. The ~150 ms process-start cost is dwarfed by the seconds-long Lighthouse run.
- **D-03:** **Cold cache via fresh Playwright context per sample, Chrome process alive for the whole `measure` run.** For each sample: Python creates a new `browser_context`, the worker runs Lighthouse against that context's debug-port-exposed page, Python closes the context. RUN-03 ("cold cache") satisfied by the fresh context — NOT by Lighthouse internals — so the invariant survives any Lighthouse upgrade. This is the exact shape Phase 4 reuses (re-hydrate from `storage_state` into a fresh context after login).
- **D-04:** **`lighthouse-worker/` sibling dir with its own `package.json` + `package-lock.json`.** Pinned: `lighthouse@13.3.0` (the version recommended in CLAUDE.md; requires Node ≥22.19 — codify in repo docs). Installed via `npm ci` so two developers get byte-identical worker installs. The `RunRecord.lighthouse_version` and `RunRecord.chrome_version` stamps are filled from the worker's response, not from a hardcoded constant.

### CLI Surface
- **D-05:** **`perfcrawl measure <url>`** — subcommand verb. Locks the verb pattern for Phase 3 (`perfcrawl crawl`) and any later capabilities. Avoids the ambiguity of a positional default verb.
- **D-06:** **Human-readable summary on stdout by default; `--json` flag emits full PageResult JSON instead.** A Rich-rendered table for interactive use (one row per metric: Perf/A11y/SEO/BP scores, LCP/CLS/INP-proxy medians, TTFB, request count, total bytes, slowest request URL+ms, status code). `--json` opts into machine output. Progress/log output goes to **stderr** so it never contaminates stdout when piped under `--json`. CLI-01 (machine-readable + non-interactive) is satisfied by `--json` plus the on-disk SQLite/CSV/JSON artifacts.
- **D-07:** **Output layout: `output/<run_id>/result.json`, `output/<run_id>/result.csv`, `output/<run_id>/lighthouse/<page-slug>.{json,html}`.** `<run_id>` is the `RunRecord.id` UUID — same key the SQLite store uses, so artifacts and DB rows share one join key. `<page-slug>` is a sanitized derivation of `url_key` (drop the scheme, replace `/` with `_`, strip to `[A-Za-z0-9._-]`, then truncate to a filesystem-safe length; collisions get `__1`, `__2`, …). **Critical: the slug derivation MUST sanitize `..` and decoded percent-encoded dots before constructing any path** — Phase 1 IN-02 documented that `canonical_key("…/a/%2e%2e/b")` returns the literal `../` in the key, so a naive `f"{slug}.json"` join would be a path-traversal vector. The slug derivation is the boundary IN-02 flagged as needing sanitization.
- **D-08:** **`--samples N` displays medians only in the human summary with a `(median of N)` footer; the raw distribution lives in the persisted JSON (`MetricSample.samples`) and the SQLite blob.** Matches D-14 from Phase 1 (medians for headline, distribution for forensics). Anyone wanting the raw samples reads the persisted RunRecord. Stdout stays scannable for one URL.

### Network Facts Source
- **D-09:** **Pure Lighthouse audits — one source for network-level facts.** The normalizer reads from `audits["network-requests"].details.items[]` (waterfall: url, resourceType, transferSize, statusCode, startTime/endTime → derive timing_ms), `audits["server-response-time"].numericValue` (TTFB), `audits["total-byte-weight"].numericValue` (total bytes), and computes `request_count`/`slowest_request_*` in Python from the waterfall list. **Not** parallel-capturing via Playwright/CDP `Network` events in Phase 2 — that lift pays off in Phase 3 where the crawler hits many pages and the parallel capture amortizes; for a single-URL slice it's double-handling. Phase 3 may revisit.
- **D-10:** **Strict, version-gated normalizer.** The Node worker stamps Lighthouse's self-reported version into its response. Python's normalizer compares against an expected major-minor (`"13.x"`); a mismatch is a HARD ERROR with the actual vs expected version logged. Prevents silent audit-shape drift on lockfile bumps (the realistic failure mode where someone upgrades to 14.0 without updating the parser). Matches Phase 1's bias for fail-loud schema invariants (WR-01/-02 patterns).
- **D-11:** **INP-proxy mapping: TBT → `inp_proxy_tbt_ms`.** Normalizer reads `audits["total-blocking-time"].numericValue` and writes it to `PageResult.inp_proxy_tbt_ms` (the only INP-flavored field the Phase-1 model accepts — bare `inp` is forbidden at the model layer per D-15). Human summary column header reads **`INP (lab proxy, TBT-based)`** — fully labeled, no chance of mistake for field INP. Belt-and-suspenders: model layer + normalizer + display layer all enforce the labeling.
- **D-12:** **`diagnostics` field gets a curated subset of Lighthouse `audits`: every audit with `score < 1` (i.e. opportunities and diagnostics that flagged an issue) only.** Passing audits and meta audits are dropped. Keeps the persisted JSON blob bounded (tens of KB per page vs hundreds). The full Lighthouse JSON is still on disk via OUT-03 if anyone needs the complete dump, and Phase 5 AI prompts will pull from this curated subset to stay grounded in actual problems.

### Per-URL Failure Handling
- **D-13:** **Non-2xx HTTP response → partial PageResult.** `status_code` is recorded, network facts are captured if Lighthouse got them (TTFB to first byte, request_count), but Lighthouse category scores and CWV fields stay null. **Exit 0** — the tool did its job; the page was measured-or-tagged. Callers read `status_code` to filter. This anticipates Phase 3 success criterion #5 (non-2xx tagged as errors and excluded from metrics) — Phase 2 establishes the "store the status_code, null the metrics" pattern that Phase 3's crawler-level error tagging builds on.
- **D-14:** **Per-sample timeout + one retry per sample.** Each Lighthouse subprocess call is wrapped in `subprocess.run(timeout=60)` (the 60 s default lives in a single constant; the planner can adjust). On timeout OR non-zero exit, retry that sample once. If the retry also fails, that sample is dropped; remaining samples continue. If ALL N samples fail, the page is recorded with null metrics and the CLI exits 2.
- **D-15:** **Three exit codes: 0 success / 1 user error / 2 measurement error.** `0` = page was measured or tagged (including non-2xx). `1` = bad input (malformed URL, missing/invalid args, can't write to output dir, Typer usage errors). `2` = the tool couldn't measure (all N samples failed, Chrome won't launch, Lighthouse missing or wrong version, network unreachable, persistence write failed). Callers can `case $? in 0) parse JSON ;; 1) fix invocation ;; 2) investigate environment ;; esac`. Phase 6's budget verdicts (BUDG-01, deferred) will carve out their own dedicated codes (e.g. 10 = budget exceeded) when they land.
- **D-16:** **Median over successful samples; no padding, no minimum-sample floor.** If 3 of 5 samples produced an LCP, `MetricSample.samples = [v1,v2,v3]` (length 3, honestly reflecting what was measured) and `MetricSample.median = statistics.median(samples)`. The user sees in the persisted distribution that two samples failed for that metric. Don't fabricate samples; don't null an entire metric for one bad run. A "minimum-N floor" knob is deferred until real failure rates justify it.

### Claude's Discretion (left to planner/executor)
- Exact Typer command tree layout, module split between `cli.py` / `orchestrator.py` / `normalizer.py` / `output.py`, and how Playwright is wrapped (sync vs async — sync is simpler for one URL; revisit at Phase 3).
- The 60 s per-sample timeout constant and the `--samples` default value (likely 3 — odd-N is friendlier for median; planner picks).
- Rich progress-bar density and column widths in the human summary table.
- File-naming details below the boundary D-07 sets: collision-suffix format (`__1` vs `-1`), slug truncation length, `mkdir -p` vs guard-then-create.
- Whether the `lighthouse-worker/` install is auto-invoked by `perfcrawl measure` on first run (preflight `npm ci`) or required as a documented setup step. Both are defensible; planner decides.
- Whether `output/` is `.gitignore`d by default (likely yes — runtime artifacts) and whether the CLI emits a hint pointing the user at the run dir after a successful measurement.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope & requirements (MUST READ FIRST)
- `.planning/ROADMAP.md` § "Phase 2: Single-Page Measurement Slice" — the 5 success criteria this phase is verified against (single command end-to-end measurement, mobile/desktop+throttling+cold-cache, `--samples N` with median and stamped versions, SQLite persistence + CSV/JSON + raw artifacts, non-interactive machine-readable CLI).
- `.planning/REQUIREMENTS.md` — the 12 v1 requirements mapped to Phase 2: **METRIC-01..05** (LH scores, CWV, waterfall, network facts, opportunities/diagnostics), **RUN-01..04** (mobile-default, throttling, cold cache, median-of-N), **OUT-03** (raw LH artifacts), **OUT-04** (flat CSV + full JSON), **CLI-01** (on-demand automation-friendly CLI).

### Phase 1 contract (the data model and store this phase fills)
- `.planning/phases/01-data-model-persistence-foundation/01-CONTEXT.md` — D-01..D-17 of the Phase 1 contract: canonical URL key rules, schema-evolution semantics, RunDelta polarity registry, the **D-14 median-of-N storage shape** Phase 2 fills, and the **D-15 labeled-INP-proxy invariant** Phase 2's normalizer and display layer must honor.
- `.planning/phases/01-data-model-persistence-foundation/01-LEARNINGS.md` — **especially the IN-02 landmine** (`%2e%2e` decodes to literal `../` in `url_key` — any code deriving a filename or path from `url_key` MUST sanitize at the boundary; D-07 above is where this kicks in) and the "labeled-proxy invariant enforced at the model layer" pattern (`_no_bare_inp` validator on `PageResult`).
- `src/perfcrawl/models.py` — `PageResult`, `MetricSample(median, samples[])`, `WaterfallEntry`, `RunRecord` (including the `chrome_version` / `lighthouse_version` / `throttling` / `emulation` slots Phase 2 stamps), and the `_no_bare_inp` model validator.
- `src/perfcrawl/store.py` — the hybrid TEXT-blob + GENERATED-columns store Phase 2 writes to via `write_run`. Atomic `with conn:` write pattern (Phase 1 CR-01) must be preserved.
- `src/perfcrawl/registry.py` — `METRIC_POLARITY`. Any new Phase-2 metric added to the model must be registered here so Phase 6's RunDelta engine knows its direction.

### Stack & architecture decisions (research)
- `CLAUDE.md` (project root) § "Headline Decision: Python orchestrator + thin Node 'measurement worker'", § "Measurement Engine: when to use which", § "Crawler & Authentication" (the persistent-context + debug-port pattern that D-01 implements), and § "What NOT to Use" (the **PyPI `lighthouse` package is abandoned 2016 service-discovery, NOT Google Lighthouse** trap; the **passing a logged-in Playwright `page` directly to Lighthouse** anti-pattern that D-01/D-03 explicitly avoid).
- `CLAUDE.md` § "Version Compatibility" — Lighthouse 13.3.0 requires Node ≥22.19 (codified in `lighthouse-worker/package.json` engines field per D-04).

### Compatibility target (informs which fields land in the CSV row)
- `.planning/PROJECT.md` § "Context" — the existing Google Sheet schema PerfCrawl supersedes (Page, URL, Total Page Load Time, Number of Requests, Total Data Transferred, Slowest Request URL/Time, TTFB, Response Size, Status Code, …) and the reference sheet URL. The Phase-2 flat CSV (OUT-04) shape should be a superset of this so the team's existing workflow translates 1:1.

### Pre-existing project state
- `.planning/STATE.md` — the **"Phase 2 readiness" blocker**: "Spike the Playwright `launchPersistentContext` + `--remote-debugging-port` + `disableStorageReset:true` auth handoff on a real authenticated Django page before planning Phase 2 — the single riskiest plumbing seam." D-01 elects to pay this spike cost in Phase 2 (without auth; auth lands in Phase 4 on the proven seam) rather than defer.
- `.planning/STATE.md` § "Blockers/Concerns" — the **INP-must-always-be-reported-as-TBT-proxy-not-field-INP** invariant. Phase 2's normalizer (D-11) and human-display layer (D-06/D-11) are the layers that enforce it; D-15 enforces it at the model.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **`src/perfcrawl/models.py`** — Already provides `PageResult` (nullable-superset, all Phase-2 fields present), `MetricSample(median, samples[])` (the median-of-N storage shape D-08/D-16 fill), `WaterfallEntry` (METRIC-03), `RunRecord` (with `chrome_version`/`lighthouse_version`/`throttling`/`emulation` slots ready for D-04's worker stamp), and the `_no_bare_inp` model validator (D-15/D-11 enforcement floor).
- **`src/perfcrawl/store.py`** — Already provides `write_run(conn, run_record)` with the atomic `with conn:` block pattern (CR-01) and the per-connection `PRAGMA foreign_keys = ON` re-assert (WR-05). Phase 2 writes its `RunRecord` to this store unchanged.
- **`src/perfcrawl/canonical.py`** — `canonical_key(url)` already provides the `url_key` Phase 2 stamps into `PageResult.url_key`. Defensive try/except + deterministic fallback for malformed URLs (WR-03) means the CLI input layer can pass arbitrary user URLs without preflight validation.
- **`src/perfcrawl/registry.py`** — `METRIC_POLARITY`, `TRACKING_PARAM_DENYLIST`. Phase 2 doesn't extend either, but any new metric promoted to a queryable column in this phase must add its polarity here.

### Established Patterns (Phase 1 LEARNINGS)
- **One-editable-place registry tables consumed by call sites** — Phase 2's per-sample timeout default, `--samples` default, normalizer's expected Lighthouse major-minor version, INP-proxy column label, exit-code constants — all land in named constants in one config/constants module, never inlined at call sites.
- **Hybrid TEXT-blob + GENERATED-column store** — already in place; Phase 2 just writes to it.
- **Forward-compat models: `extra="ignore"` + `Optional[…] = None`** — Phase 2's normalizer fills the Phase-1 nullable superset; never bumps `SCHEMA_VERSION` (no new fields are added — every field Phase 2 fills was modeled in Phase 1).
- **Labeled-proxy invariant enforced at the model layer** — D-11 reinforces this at the normalizer + display layers; the `_no_bare_inp` validator is the floor that catches any regression.
- **TDD RED → GREEN commit pair per task** — Phase 1 used this for every task; Phase 2 plans should follow the same pattern (write failing tests against fixture Lighthouse JSON first, then the normalizer/orchestrator that makes them green).
- **Defensive try/except + deterministic fallback for untrusted input** — `canonical_key` already follows this; the Phase 2 slug-derivation function (D-07) should too, especially given the IN-02 landmine.
- **Finite-guard pattern: `isfinite(result)` after arithmetic** — applies to any computed metric (e.g. derived slowest-request timing) before assigning to a `MetricSample.median` field, since the `MetricSample` model has `allow_inf_nan=False`.

### Integration Points
- **Python-to-Node boundary** is the new integration point this phase establishes (`src/perfcrawl/lighthouse_worker.py` or similar, owning the subprocess invocation contract: argv shape, JSON-on-stdout, exit-code semantics, the version-stamp).
- **Playwright orchestration** is the second new integration point (per-sample context creation/teardown, debug-port lifecycle, Chrome-version capture via CDP).
- **Existing store integration** — `store.write_run` is the seam; the orchestrator builds a complete `RunRecord` in memory and writes it atomically. No store-layer changes needed.
- **CLI entry-point integration** — `pyproject.toml`'s `[project.scripts]` adds `perfcrawl = "perfcrawl.cli:app"` (the entry point Phase 1 deliberately removed; Phase 2 restores it now that there's a real CLI behind it).

</code_context>

<specifics>
## Specific Ideas

- The flat CSV (OUT-04) shape should be a **superset of the existing studyhalo.com Google Sheet** columns (per PROJECT.md § "Context": Page, URL, Test Date, Cache Disabled, Total Page Load Time, Number of Requests, Total Data Transferred, Slowest Request URL/Time, TTFB, Response Size, Status Code) **plus** Phase 1's added fields (Lighthouse scores, CWV medians, `inp_proxy_tbt_ms` labeled column, `schema_version`, `run_id`, `chrome_version`, `lighthouse_version`). Phase 6 (Sheets exporter) reads the same shape — no second schema definition.
- **The riskiest plumbing seam (per STATE.md) is validated by Phase 2** by virtue of D-01: launching Chrome via Playwright with the debug port and attaching Lighthouse to it. Without auth in Phase 2, the spike is the simpler one; Phase 4 then incrementally adds `storage_state` + `disableStorageReset: true` to an already-proven shape.
- **A canned Lighthouse JSON fixture lives alongside the normalizer tests** (per CLAUDE.md research § "Development Tools": "Mock the Node worker by feeding it canned Lighthouse JSON fixtures"). The Python test suite hits the normalizer with a real LH-13.x JSON sample — no Node required for unit tests of the normalizer. End-to-end tests (Playwright + Node + a known target) are a smaller, slower tier.
- **`--samples 1` must work end-to-end**, not just as a degenerate case — it's the first thing a developer will try. Default likely 3 (odd-N is friendlier for median statistics); planner makes the final call.

</specifics>

<deferred>
## Deferred Ideas

- **CDP-direct network capture** (Playwright `Network` events for the waterfall) — deferred to Phase 3 where it amortizes across many pages. Phase 2 uses pure Lighthouse parsing (D-09); Phase 3 may revisit if Lighthouse audit-shape drift becomes painful or if per-host rate visibility is needed.
- **`--verbose` rendering of all N raw samples in a stacked Rich table** — captured here so the planner doesn't have to re-discover it. Cheap UX add; lands when someone needs forensic visibility on a noisy run.
- **Minimum-sample-N floor for median publication** (e.g. require ⌈N/2⌉ successful samples) — deferred until real-world failure rates justify the knob. D-16's "honest empty median over successful samples" is the Phase 2 default.
- **Auto-invoked `npm ci` on `perfcrawl measure` first run** vs documented manual setup — Claude's-discretion item left for the planner.
- **`output/` gitignore default + post-run hint pointing the user at `output/<run_id>/`** — Claude's-discretion UX polish for the planner.
- **Dedicated exit codes for Phase 6 budget verdicts (BUDG-01)** — out of Phase 2 scope; the 0/1/2 base reserves room for 10+ when budgets land.

None of these are scope creep introduced in discussion — they are explicit boundaries of Phase 2 deliberately documented so the planner can act inside them.

</deferred>

---

*Phase: 2-Single-Page Measurement Slice*
*Context gathered: 2026-05-28*
