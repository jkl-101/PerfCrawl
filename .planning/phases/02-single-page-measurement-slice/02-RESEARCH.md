# Phase 2: Single-Page Measurement Slice - Research

**Researched:** 2026-05-28
**Domain:** Lighthouse-over-CDP measurement via a Playwright-launched Chrome, with a thin Node worker, normalized into the Phase 1 PageResult, persisted, and surfaced via a Typer CLI
**Confidence:** HIGH on Lighthouse audit shape and Playwright/CDP plumbing pattern; MEDIUM on the Playwright "read-back the debug port" pattern (a known API gap with a clean workaround)

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Chrome / CDP Plumbing Strategy**
- **D-01:** Playwright launches Chrome; Lighthouse attaches via debug port. Python orchestrator calls Playwright `launch_persistent_context(args=["--remote-debugging-port=0"])` (port chosen by Chrome to avoid collisions), captures the resolved port, and passes it to the Node worker. The Node worker calls `lighthouse(url, { port })` WITHOUT launching its own Chrome via `chrome-launcher`. Front-loads the Phase 4 auth-handoff spike.
- **D-02:** One-shot Node subprocess per measurement, JSON over stdout. Each of the N samples = `subprocess.run(["node","lighthouse-worker/run.mjs", "--port=N", "--url=X", "--emulation=mobile", "--throttling=..."])`. Worker writes the full Lighthouse JSON to stdout, exits non-zero on failure.
- **D-03:** Cold cache via fresh Playwright context per sample, Chrome process alive for the whole `measure` run. RUN-03 satisfied by the fresh context — NOT by Lighthouse internals — so the invariant survives any Lighthouse upgrade.
- **D-04:** `lighthouse-worker/` sibling dir with its own `package.json` + `package-lock.json`. Pinned: `lighthouse@13.3.0` (requires Node ≥22.19). Installed via `npm ci`. The `RunRecord.lighthouse_version` and `RunRecord.chrome_version` stamps are filled from the worker's response, not from a hardcoded constant.

**CLI Surface**
- **D-05:** `perfcrawl measure <url>` — subcommand verb. Locks the verb pattern for Phase 3 (`perfcrawl crawl`).
- **D-06:** Human-readable summary on stdout by default; `--json` flag emits full PageResult JSON instead. Progress/log output goes to stderr.
- **D-07:** Output layout: `output/<run_id>/result.json`, `output/<run_id>/result.csv`, `output/<run_id>/lighthouse/<page-slug>.{json,html}`. `<run_id>` is `RunRecord.id`. `<page-slug>` is a sanitized derivation of `url_key`. **Critical: the slug derivation MUST sanitize `..` and decoded percent-encoded dots before constructing any path** (IN-02 landmine).
- **D-08:** `--samples N` displays medians only in the human summary with a `(median of N)` footer; the raw distribution lives in the persisted JSON.

**Network Facts Source**
- **D-09:** Pure Lighthouse audits — one source for network-level facts. Normalizer reads from `audits["network-requests"].details.items[]`, `audits["server-response-time"].numericValue`, `audits["total-byte-weight"].numericValue`. NOT parallel-capturing via Playwright/CDP this phase.
- **D-10:** Strict, version-gated normalizer. Mismatch on Lighthouse major-minor is a HARD ERROR.
- **D-11:** INP-proxy mapping: TBT → `inp_proxy_tbt_ms`. Human summary column header reads `INP (lab proxy, TBT-based)`.
- **D-12:** `diagnostics` field gets every audit with `score < 1` (i.e., flagged opportunities and diagnostics) only.

**Per-URL Failure Handling**
- **D-13:** Non-2xx HTTP response → partial PageResult. `status_code` is recorded, network facts captured if available, but Lighthouse category scores and CWV fields stay null. **Exit 0.**
- **D-14:** Per-sample timeout + one retry per sample. On timeout OR non-zero exit, retry once. If retry fails, sample dropped; remaining samples continue. If ALL N samples fail, page recorded with null metrics; CLI exits 2.
- **D-15:** Three exit codes: 0 success / 1 user error / 2 measurement error.
- **D-16:** Median over successful samples; no padding, no minimum-sample floor.

### Claude's Discretion

- Exact Typer command tree layout, module split between `cli.py` / `orchestrator.py` / `normalizer.py` / `output.py`, and how Playwright is wrapped (sync vs async — sync is simpler for one URL; revisit at Phase 3).
- The 60 s per-sample timeout constant and the `--samples` default value (likely 3 — odd-N is friendlier for median; planner picks).
- Rich progress-bar density and column widths in the human summary table.
- File-naming details below the boundary D-07 sets: collision-suffix format (`__1` vs `-1`), slug truncation length, `mkdir -p` vs guard-then-create.
- Whether the `lighthouse-worker/` install is auto-invoked by `perfcrawl measure` on first run (preflight `npm ci`) or required as a documented setup step. Both are defensible; planner decides.
- Whether `output/` is `.gitignore`d by default (likely yes — runtime artifacts) and whether the CLI emits a hint pointing the user at the run dir after a successful measurement.

### Deferred Ideas (OUT OF SCOPE)

- **CDP-direct network capture** (Playwright `Network` events for the waterfall) — deferred to Phase 3.
- **`--verbose` rendering of all N raw samples in a stacked Rich table** — captured for the planner.
- **Minimum-sample-N floor for median publication** — deferred until real failure rates justify it.
- **Auto-invoked `npm ci` on first run** vs documented manual setup — Claude's discretion.
- **`output/` gitignore default + post-run hint** — Claude's discretion polish.
- **Dedicated exit codes for Phase 6 budget verdicts (BUDG-01)** — out of Phase 2 scope.

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| METRIC-01 | Lighthouse category scores per page (Perf, A11y, SEO, Best Practices) | LH 13.x exposes `lhr.categories.{performance,accessibility,seo,'best-practices'}.score` (0–1, `number \| null`) — see "LH 13.x JSON Shape" |
| METRIC-02 | Core Web Vitals per page — LCP, CLS, and a clearly-labeled lab INP proxy | LCP: `audits['largest-contentful-paint'].numericValue`; CLS: `audits['cumulative-layout-shift'].numericValue`; INP proxy: `audits['total-blocking-time'].numericValue` → `inp_proxy_tbt_ms` (D-11) |
| METRIC-03 | Per-page network waterfall (URL, type, size, timing, status per request) | `audits['network-requests'].details.items[]` carries url, statusCode, transferSize, resourceType, mimeType, rendererStartTime, networkRequestTime, networkEndTime, finished, priority, protocol, sessionTargetType — map to `WaterfallEntry` |
| METRIC-04 | TTFB, request count, total bytes, response sizes, status codes, slowest request URL+time | TTFB: `audits['server-response-time'].numericValue`; total bytes: `audits['total-byte-weight'].numericValue`; request_count, slowest_request_url, slowest_request_ms: computed in Python from waterfall list; status_code: from waterfall main-document item |
| METRIC-05 | Lighthouse opportunities/diagnostics per page | D-12: `diagnostics` = subset of `lhr.audits` where `score < 1` |
| RUN-01 | Mobile (default) or desktop emulation | LH config: `settings.formFactor: 'mobile' \| 'desktop'`; default config emulates mobile (`lighthouse:default`). Desktop preset = `lighthouse:default` with `formFactor='desktop'` + desktop screenEmulation + desktop throttling |
| RUN-02 | Simulated network/CPU throttling | LH config: `settings.throttlingMethod: 'simulate'` (default) + `settings.throttling.{rttMs, throughputKbps, cpuSlowdownMultiplier}` |
| RUN-03 | Cold cache (fresh browser context per page) | D-03: fresh Playwright `BrowserContext` per sample (NOT relying on LH's storage reset) — Chrome process stays alive, contexts cycle |
| RUN-04 | `--samples N` with per-metric median | `MetricSample(median, samples[])` from Phase 1; `statistics.median()` over successful samples (D-16); empty list → `median=None`, do NOT call statistics.median (raises) |
| OUT-03 | Raw Lighthouse artifacts (JSON + HTML) per page | LH programmatic `output: ['json', 'html']` → `runnerResult.report` is a 2-tuple; write both to `output/<run_id>/lighthouse/<page-slug>.{json,html}` |
| OUT-04 | Flat CSV (one row per page) + full-fidelity JSON | CSV: stdlib `csv.DictWriter` with locked column order (see Standard Stack); JSON: `RunRecord.model_dump_json(indent=2)` |
| CLI-01 | On-demand, automation-friendly CLI (non-interactive, machine-readable) | Typer subcommand `perfcrawl measure`; progress on stderr; `--json` for machine output; exit codes 0/1/2 (D-15) |

</phase_requirements>

## Summary

Phase 2 is dominated by **one new integration seam**: a Playwright-launched, Lighthouse-attached Chrome session that Python orchestrates and a Node subprocess drives. The seam itself is well-trodden — `playwright-lighthouse`, `lighthouse-ci`, and many published guides use the same shape — but it has two non-obvious snags that the planner must build around from day one:

1. **Playwright Python does NOT expose the resolved CDP port back to the caller** after `launch_persistent_context(args=["--remote-debugging-port=0"])`. The documented API surface ([BrowserType docs](https://playwright.dev/python/docs/api/class-browsertype)) offers no `browser.endpoint` / `context.cdp_port` accessor, and the closed [playwright-python#2789](https://github.com/microsoft/playwright-python/issues/2789) confirms this is a long-standing gap. The robust workaround is to **let Chrome write `DevToolsActivePort` into the user-data-dir** (its documented behavior when launched with `--remote-debugging-port=0`) and read line 1 of that file in Python after the launch returns. A *less* robust workaround — pre-pick a free port via `socket.bind((host, 0))` then pass it explicitly — has a TOCTOU race against the kernel re-issuing the port. Use the `DevToolsActivePort` file.

2. **`audits['network-requests'].details.items[]` timing fields were renamed** in LH 12+ from `startTime`/`endTime` to `rendererStartTime` / `networkRequestTime` / `networkEndTime` (verified against [core/audits/network-requests.js](https://github.com/GoogleChrome/lighthouse/blob/main/core/audits/network-requests.js) on `main`, which the 13.3.0 release tracks). Any normalizer that assumes the old name silently nulls every `timing_ms`. The fixture-driven test pattern (canned LH 13.3.0 JSON in `tests/fixtures/lighthouse/`) is the only way to lock this down before the live worker exists.

Everything else (median-of-N over `MetricSample`, the Typer/Rich CLI shape, the CSV column order, the `<page-slug>` sanitization that closes the IN-02 path-traversal landmine, the 0/1/2 exit codes) is "apply the established pattern" rather than open research.

**Primary recommendation:** Build inside-out — write the Lighthouse normalizer first against a canned LH 13.3.0 JSON fixture (no Node required), then the slug sanitizer + output writers (no Chrome required), then the per-sample orchestrator (one Node call), then the Typer CLI on top. The Chrome-port-discovery seam is the riskiest piece; do it third, after the cheap things are green, so it's the focused-attention task.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Subcommand parsing, `--json`/`--samples` flags, exit codes | CLI (Python / Typer) | — | D-05/D-06/D-15 lock the surface; Typer owns argv → command dispatch |
| Per-sample subprocess orchestration, retry, timeout, median aggregation | Python orchestrator | Node worker (executor) | Python owns the loop and the median math (D-14/D-16); the Node worker is one-shot per sample (D-02) |
| Browser lifecycle (launch persistent context, cycle context per sample, capture chrome_version) | Python orchestrator (Playwright sync API) | — | D-01/D-03: Playwright owns Chrome so Phase 4 can layer `storage_state` on the same shape |
| Lighthouse audit execution against a given port | Node worker (`lighthouse-worker/run.mjs`) | — | D-01/D-02: Lighthouse is Node-native; the worker is the only Node surface area |
| Lighthouse JSON → `PageResult` normalization, version gate, INP-proxy mapping | Python normalizer | — | D-09/D-10/D-11: model layer in Python; the worker stays dumb |
| Run persistence (SQLite) | Phase 1 `store.write_run` | — | Already in place; Phase 2 calls it unchanged |
| On-disk artifact writing (CSV / JSON / raw LH JSON+HTML) | Python output module | — | OUT-03/OUT-04; stdlib `csv` + Pydantic `.model_dump_json()` + raw file writes |
| Page-slug sanitization (IN-02 boundary) | Python (deterministic-fallback function) | — | D-07: filesystem boundary, MUST sanitize `..` / decoded `%2e%2e` — pattern mirrors `canonical_key` |
| Human-readable summary table | Rich (via Typer) | — | D-06: stdout for the table, stderr for progress |
| Machine-readable output | stdout JSON (`--json`) + SQLite + flat CSV | — | CLI-01; three independent surfaces so a CI consumer picks the cheapest |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.12+ | Primary language — orchestrator, normalizer, CLI, output | Project floor (`pyproject.toml`). `[VERIFIED: pyproject.toml]` |
| Node.js | ≥22.19 | Runtime for the Lighthouse worker only | Hard requirement of lighthouse@13.3.0. `[VERIFIED: npm view lighthouse engines → { node: '>=22.19' }]` (local: v23.11.0, satisfies) |
| playwright (Python) | 1.60.0 | Launch + manage Chrome with `--remote-debugging-port`; cycle fresh contexts for cold cache | `[VERIFIED: uv pip install --dry-run playwright → 1.60.0]`; CLAUDE.md "Recommended Stack" already locks Playwright as the browser driver |
| lighthouse (npm) | 13.3.0 | Audit engine, attached to Playwright's Chrome via `port` option | `[VERIFIED: npm view lighthouse version → 13.3.0]`, repo `git+https://github.com/GoogleChrome/lighthouse.git`, maintainers include paulirish (Chrome team). Confirmed NOT the abandoned PyPI `lighthouse` decoy |
| typer | latest (>=0.15) | CLI framework — `perfcrawl measure <url>` subcommand, `--samples`, `--json`, `--emulation` | CLAUDE.md "Recommended Stack" lock; `[VERIFIED: slopcheck OK on pypi]` |
| rich | latest (>=13) | Human-readable summary table + progress on stderr (D-06) | CLAUDE.md "Recommended Stack" lock; `[VERIFIED: slopcheck OK on pypi]` |
| pydantic | 2.10+ (already installed) | Model layer (Phase 1 contract — `PageResult`, `MetricSample`, `RunRecord`) | Already in `pyproject.toml` |

### Supporting (stdlib — no new deps)

| Module | Purpose | When to Use |
|---------|---------|-------------|
| `subprocess` | One-shot Node worker invocation per sample (D-02) | `subprocess.run([...], capture_output=True, text=True, encoding='utf-8', timeout=60)` |
| `socket` | NOT used (rejected — race condition vs `DevToolsActivePort` file approach) | See Pitfall 2 |
| `statistics` | `statistics.median()` over successful samples (D-16) | Wrap in `try/except StatisticsError` for empty list, or check `if samples: ...` |
| `csv` | OUT-04 flat CSV row | `csv.DictWriter` with locked fieldname list |
| `json` | Worker stdout parsing; full-fidelity result JSON write | `json.loads(stdout)` from worker; `RunRecord.model_dump_json()` for OUT-04 JSON |
| `uuid` | `RunRecord.id` (Phase 1 default_factory uses `uuid4`) | Already in model |
| `pathlib` | Output dir construction | `Path("output") / str(run_id) / "lighthouse"` |
| `re` | Slug sanitization (`re.sub(r"[^A-Za-z0-9._-]+", "_", ...)`) | D-07 sanitizer |
| `time.perf_counter` | Per-sample wall-clock timing if needed for stderr progress | — |

### Alternatives Considered

| Instead of | Could Use | Tradeoff (why rejected for Phase 2) |
|------------|-----------|--------------------------------------|
| Playwright launching Chrome | `chrome-launcher` (npm) launching Chrome inside the Node worker | Loses the Python-owned browser lifecycle that Phase 4 (`storage_state` reuse) needs. D-01 explicitly rejects. |
| `DevToolsActivePort` file read | Pre-pick free port via `socket.bind(0)` + `getsockname()[1]` | TOCTOU: between socket close and Chrome bind, the kernel can re-issue the port to another process. Documented at [The port 0 trick](https://www.dnorth.net/2012/03/17/the-port-0-trick/) as "mostly works" — not good enough for a tool that orchestrates Chrome 1000+ times across a crawl. |
| One-shot subprocess per sample (D-02) | Long-lived Node REPL with JSON-over-stdin | Higher engineering cost; the ~150ms process-start is dwarfed by the 5–15s Lighthouse run. Subprocess-per-sample also gives clean retry semantics (D-14): kill + restart the worker is trivial. |
| stdlib `csv` | `pandas.to_csv` | New ~30MB dependency for one CSV row. Stdlib is sufficient. |
| sync Playwright | async Playwright | Sync is simpler for one URL. Phase 3 may need async for parallelism — re-evaluate then. |
| `playwright-lighthouse` (npm wrapper) | hand-roll the port handoff | The npm wrapper is a Node-only convenience; we still need the Python ↔ Node boundary. Hand-rolled handoff is shorter to read and review. |

**Installation:**

```bash
# Python side (top-level pyproject.toml additions)
uv add playwright typer 'rich>=13'
uv run playwright install chromium

# Node side — sibling dir under repo root, version-pinned with lockfile
mkdir lighthouse-worker
cd lighthouse-worker
npm init -y
npm install --save-exact lighthouse@13.3.0
# Then commit package-lock.json and use `npm ci` for reproducible installs.
```

**Version verification (run before locking in):**

```bash
uv pip install --dry-run playwright       # 1.60.0 confirmed 2026-05-28
uv pip install --dry-run typer             # latest minor
uv pip install --dry-run rich              # latest minor
npm view lighthouse version                # 13.3.0 confirmed 2026-05-28
npm view lighthouse engines                # { node: '>=22.19' } confirmed
```

## Package Legitimacy Audit

slopcheck was available locally and was run on the candidate set. The PyPI `lighthouse` warning is expected — CLAUDE.md "What NOT to Use" already documents that the PyPI `lighthouse` package is the abandoned 2016 service-discovery decoy. The Phase 2 worker uses the **npm** `lighthouse` package (a completely separate registry namespace), which is the legitimate Google Lighthouse.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| playwright (Python) | PyPI | mature (Microsoft) | high | github.com/microsoft/playwright-python | [OK] | Approved |
| typer | PyPI | mature (tiangolo) | very high | github.com/fastapi/typer | [OK] | Approved |
| rich | PyPI | mature (Textualize) | very high | github.com/Textualize/rich | [OK] | Approved |
| lighthouse | **npm** | mature (GoogleChrome) | very high | github.com/GoogleChrome/lighthouse | (manually verified — not in slopcheck PyPI scan) | Approved — verified via `npm view lighthouse repository` → GoogleChrome/lighthouse + maintainers paulirish/brendankenny/hoten (Chrome team) |
| lighthouse (PyPI) | PyPI | abandoned 2016 | 346 (slopcheck noted "Not exactly popular") | none active | [OK on existence] but **WRONG PACKAGE** | **REMOVED — never install. CLAUDE.md "What NOT to Use" explicitly warns: this is a 2016 service-discovery tool, NOT Google Lighthouse.** |

**Packages removed due to wrong-namespace risk:** PyPI `lighthouse` (explicitly never to be installed — use npm `lighthouse` instead).
**Packages flagged as suspicious [SUS]:** none.
**Postinstall script check:** `npm view lighthouse scripts.postinstall` returns nothing — no surprise postinstall. (Verified during research.)

## Architecture Patterns

### System Architecture Diagram

```
                ┌─────────────────────────────────────────────────────┐
                │  USER  →  perfcrawl measure <url> --samples 3 ...   │
                └──────────────────────┬──────────────────────────────┘
                                       ▼
              ┌───────────────────────────────────────────────────┐
              │  CLI (typer)  cli.py                              │
              │  • parse argv, validate URL, build RunConfig      │
              │  • exit-code mapping (0/1/2)                      │
              └──────────────────────┬────────────────────────────┘
                                     ▼
              ┌───────────────────────────────────────────────────┐
              │  Orchestrator  orchestrator.py                     │
              │  • Playwright launch_persistent_context            │
              │    (args=["--remote-debugging-port=0", ...],       │
              │     user_data_dir=<tmpdir>)                        │
              │  • Read DevToolsActivePort line 1 → port           │
              │  • Capture chrome_version via                      │
              │    page.context.browser().version()                │
              │  • For sample in 1..N:                             │
              │     a) browser.new_context() (RUN-03 cold cache)   │
              │     b) subprocess.run(worker, --port=…, --url=…,   │
              │        timeout=60) + 1 retry on fail               │
              │     c) parse JSON stdout → LH result               │
              │     d) context.close()                             │
              │  • Aggregate medians, build RunRecord              │
              └──────────────────────┬────────────────────────────┘
                                     │ JSON over stdout
                                     ▼
              ┌───────────────────────────────────────────────────┐
              │  Node worker  lighthouse-worker/run.mjs            │
              │  • argv parse → port, url, formFactor, throttling  │
              │  • import lighthouse from 'lighthouse'             │
              │  • lighthouse(url, { port, output: ['json','html'],│
              │      logLevel: 'error' }, configOverrides)         │
              │  • print JSON.stringify({                          │
              │      lhr: runnerResult.lhr,                        │
              │      reportHtml: runnerResult.report[1],           │
              │      reportJson: runnerResult.report[0],           │
              │      lighthouseVersion: lhr.lighthouseVersion,     │
              │      chromeVersion: lhr.environment.hostUserAgent  │
              │    })                                              │
              │  • exit 0 on success, non-zero on failure          │
              └──────────────────────┬────────────────────────────┘
                                     ▼
              ┌───────────────────────────────────────────────────┐
              │  Normalizer  normalizer.py                         │
              │  • version gate: hard-error if                     │
              │    lhr.lighthouseVersion's major.minor != "13.x"   │
              │  • map lhr.categories.* → perf/a11y/seo/bp scores  │
              │  • map LCP/CLS/TBT.numericValue → MetricSample     │
              │    (TBT goes to inp_proxy_tbt_ms, NEVER 'inp')     │
              │  • build waterfall from audits['network-requests'] │
              │    .details.items[] (note: timing fields are       │
              │    rendererStartTime / networkEndTime in LH 13)    │
              │  • TTFB ← audits['server-response-time']           │
              │    .numericValue                                   │
              │  • total_bytes ← audits['total-byte-weight']       │
              │    .numericValue                                   │
              │  • request_count = len(waterfall)                  │
              │  • slowest_request_url/_ms = max(waterfall,        │
              │    key=lambda r: r.timing_ms)                      │
              │  • status_code from main-document waterfall row    │
              │  • diagnostics = {id: audit                        │
              │    for id, audit in audits.items()                 │
              │    if audit.get('score') is not None               │
              │    and audit['score'] < 1}                         │
              └──────────────────────┬────────────────────────────┘
                                     ▼
                   ┌─────────────────┴────────────────┐
                   ▼                                  ▼
        ┌──────────────────────┐         ┌─────────────────────────┐
        │  store.write_run     │         │  output writer (output.py)│
        │  (Phase 1, unchanged)│         │  • mkdir output/<run_id> │
        │  • atomic with conn  │         │  • write result.json     │
        │  • PRAGMA FK=ON      │         │    (model_dump_json)     │
        │  • SQLite TEXT blob  │         │  • write result.csv      │
        └──────────────────────┘         │    (locked column order) │
                                         │  • write lighthouse/     │
                                         │    <slug>.{json,html}    │
                                         │    (per sample? per page?│
                                         │     — see Open Q1)       │
                                         └──────────────────────────┘
                                                      │
                                                      ▼
                                         ┌─────────────────────────┐
                                         │  stdout (D-06)           │
                                         │  default: Rich table     │
                                         │  --json: PageResult JSON │
                                         └─────────────────────────┘

    NOTE: Chrome process stays alive for the whole `measure` run.
          Per-sample isolation comes from cycling browser_context
          (close + new_context()), NOT from killing Chrome (D-03).
```

### Recommended Project Structure

```
performance-statistics-gathering/
├── lighthouse-worker/              # NEW — D-04
│   ├── package.json                # engines.node ">=22.19", lighthouse@13.3.0 exact
│   ├── package-lock.json           # committed; `npm ci` for byte-identical installs
│   └── run.mjs                     # one-shot worker, JSON-over-stdout
├── src/perfcrawl/
│   ├── __init__.py
│   ├── canonical.py                # Phase 1 — unchanged
│   ├── delta.py                    # Phase 1 — unchanged
│   ├── models.py                   # Phase 1 — unchanged
│   ├── registry.py                 # Phase 1 — unchanged
│   ├── store.py                    # Phase 1 — unchanged
│   ├── cli.py                      # NEW — Typer app + measure command
│   ├── orchestrator.py             # NEW — Playwright lifecycle + sample loop
│   ├── worker.py                   # NEW — subprocess wrapper + version gate
│   ├── normalizer.py               # NEW — LH JSON → PageResult
│   ├── output.py                   # NEW — CSV/JSON/raw artifact writers
│   ├── slug.py                     # NEW — IN-02-safe slug derivation
│   └── constants.py                # NEW — one editable place for timeouts, defaults, version pin
└── tests/
    ├── fixtures/
    │   └── lighthouse/             # NEW — canned LH 13.x JSON for normalizer tests
    │       ├── studyhalo-home-200.json
    │       ├── studyhalo-404.json
    │       └── …
    ├── test_normalizer.py          # NEW — runs without Node
    ├── test_slug.py                # NEW — IN-02 sanitization
    ├── test_output.py              # NEW — CSV row + JSON file shape
    ├── test_worker.py              # NEW — subprocess contract (mocked)
    ├── test_cli.py                 # NEW — Typer CliRunner against the command tree
    └── test_e2e.py                 # NEW — @pytest.mark.e2e, needs Node + network
```

### Pattern 1: Playwright launches Chrome → Lighthouse attaches via port (D-01)

**What:** Python owns Chrome's lifecycle (so Phase 4 can layer `storage_state` on the same seam). The Node worker is dumb: given a port and a URL, run Lighthouse, dump JSON.

**When to use:** Whenever the browser session needs to be reused across measurement engines, or when the orchestrator needs to control teardown.

**Example (Python orchestrator side):**

```python
# Source: this research, synthesized from Playwright Python BrowserType docs
# (https://playwright.dev/python/docs/api/class-browsertype) + the closed
# playwright-python#2789 workaround pattern.

import tempfile, json, subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright

def measure_url(url: str, samples: int, emulation: str) -> list[dict]:
    with sync_playwright() as p:
        # Per-run tempdir: cleaned up automatically when block exits.
        with tempfile.TemporaryDirectory() as user_data_dir:
            # --remote-debugging-port=0 → Chrome picks a free port and writes
            # it to DevToolsActivePort in user-data-dir. This is Chrome's
            # documented contract; verified across DevTools/Selenium/WDIO.
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                args=[
                    "--remote-debugging-port=0",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                headless=True,
            )
            # Read line 1 of DevToolsActivePort to discover the resolved port.
            port_file = Path(user_data_dir) / "DevToolsActivePort"
            # NOTE: file is written shortly after launch returns; a tiny retry
            # loop (e.g. 10 attempts × 100ms) is robust on slow CI.
            port = int(port_file.read_text().splitlines()[0])

            # Capture chrome_version for RunRecord.chrome_version.
            # context.browser is None for persistent contexts (Playwright
            # quirk); use context.pages[0].evaluate via UA, or parse
            # lhr.environment.hostUserAgent from the worker output (cleaner).

            sample_results = []
            for i in range(samples):
                # D-03: fresh context per sample = cold cache.
                # (For a persistent context launched above, "new context"
                # means a fresh BrowserContext on the same Chrome process —
                # Phase 4 will swap user_data_dir per-context for auth.)
                fresh = context.browser.new_context() if context.browser else context
                try:
                    proc = subprocess.run(
                        [
                            "node", "lighthouse-worker/run.mjs",
                            f"--port={port}",
                            f"--url={url}",
                            f"--form-factor={emulation}",
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=60,  # constants.PER_SAMPLE_TIMEOUT_S
                    )
                    if proc.returncode == 0:
                        sample_results.append(json.loads(proc.stdout))
                    else:
                        # one retry per sample (D-14)
                        proc2 = subprocess.run(
                            ["node", "lighthouse-worker/run.mjs", ...],
                            capture_output=True, text=True, timeout=60,
                        )
                        if proc2.returncode == 0:
                            sample_results.append(json.loads(proc2.stdout))
                        # else: drop this sample silently; sample_results
                        # shorter than `samples` is honest (D-16).
                except subprocess.TimeoutExpired:
                    # retry once (D-14) — same shape as non-zero exit branch.
                    ...
                finally:
                    if fresh is not context:
                        fresh.close()

            context.close()
            return sample_results
```

### Pattern 2: Node Lighthouse worker (one-shot, JSON-over-stdout) — D-02

**What:** A standalone Node script that knows nothing except how to run Lighthouse against a port and print JSON.

**Example (`lighthouse-worker/run.mjs`):**

```javascript
// Source: synthesized from https://github.com/GoogleChrome/lighthouse/blob/main/docs/readme.md
// (programmatic-usage example) + the disableStorageReset pattern verified
// against https://dev.to/jamescryer/programmatically-audit-with-lighthouse-and-performance-budgets-9kb

import lighthouse from "lighthouse";
import { parseArgs } from "node:util";

const { values } = parseArgs({
  options: {
    port: { type: "string" },
    url: { type: "string" },
    "form-factor": { type: "string", default: "mobile" },
  },
});

const flags = {
  port: Number(values.port),
  output: ["json", "html"],   // returns [jsonStr, htmlStr] as report
  logLevel: "error",          // keep stderr quiet; Python parses stdout
  // disableStorageReset: false  // ← Phase 4 flips this to true for auth.
};

const config = {
  extends: "lighthouse:default",
  settings: {
    formFactor: values["form-factor"], // 'mobile' | 'desktop'  (RUN-01)
    // For desktop runs, also override screenEmulation + throttling to
    // match LH's built-in desktop preset (the default config emulates
    // mobile). See LH 13 docs/emulation.md.
    ...(values["form-factor"] === "desktop" ? {
      screenEmulation: {
        mobile: false,
        width: 1350, height: 940, deviceScaleFactor: 1, disabled: false,
      },
      throttling: {                       // LH desktop preset
        rttMs: 40, throughputKbps: 10240,
        cpuSlowdownMultiplier: 1,
        requestLatencyMs: 0, downloadThroughputKbps: 0, uploadThroughputKbps: 0,
      },
    } : {}),
    // throttlingMethod: 'simulate'  ← LH default, satisfies RUN-02.
  },
};

try {
  const result = await lighthouse(values.url, flags, config);
  // result.report is a 2-element array because flags.output is ['json','html']
  const [reportJson, reportHtml] = result.report;
  process.stdout.write(JSON.stringify({
    lhr: result.lhr,
    reportJson,
    reportHtml,
  }));
  process.exit(0);
} catch (err) {
  process.stderr.write(`worker error: ${err.message}\n`);
  process.exit(1);
}
```

### Pattern 3: Version-gated normalizer (D-10)

**What:** Hard-error on any Lighthouse major-minor drift. Prevents silent audit-shape changes from corrupting the data model.

**Example:**

```python
# Source: this research, synthesized from Phase 1 LEARNINGS "Pydantic v2 accepts
# inf/nan by default" pattern (fail-loud at the model boundary).

from perfcrawl.constants import EXPECTED_LIGHTHOUSE_MAJOR_MINOR  # "13.x"

def _check_version(lhr: dict) -> None:
    actual = lhr.get("lighthouseVersion", "")
    actual_mm = ".".join(actual.split(".")[:2])  # "13.3.0" → "13.3"
    expected_major = EXPECTED_LIGHTHOUSE_MAJOR_MINOR.split(".")[0]  # "13"
    if not actual.startswith(expected_major + "."):
        raise ValueError(
            f"Lighthouse version mismatch: expected major {expected_major}.x, "
            f"got {actual!r}. Normalizer is locked to LH "
            f"{EXPECTED_LIGHTHOUSE_MAJOR_MINOR} audit shape; refusing to "
            f"silently produce a corrupted PageResult."
        )
```

### Pattern 4: Slug sanitization (D-07, IN-02 boundary)

**What:** Take an arbitrary `url_key` (which may contain `..` from decoded `%2e%2e`), produce a filesystem-safe stem that never escapes `output/<run_id>/lighthouse/`.

**Example:**

```python
# Source: this research; mirrors the canonical_key try/except + deterministic
# fallback pattern from src/perfcrawl/canonical.py (the Phase 1 "Defensive
# try/except + deterministic fallback" pattern from LEARNINGS).

import re
from urllib.parse import urlsplit

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_DOTRUN = re.compile(r"\.{2,}")  # collapse '..' runs to '__'

def page_slug(url_key: str, max_len: int = 80) -> str:
    """Derive a filesystem-safe slug from a canonical URL key.

    The url_key is an OPAQUE cross-run identity string (canonical.py warns
    that it can contain literal `..` segments because w3lib decodes %2e%2e
    without resolving them). Treating url_key as a path component would be
    a path-traversal vector — sanitize at this boundary.

    Empty input → "_" (no valid slug is empty; collision-suffix appender
    handles uniqueness).
    """
    if not (url_key or "").strip():
        return "_"
    try:
        parts = urlsplit(url_key)
        # Drop scheme; combine netloc + path with '_' replacing '/'.
        stem = (parts.netloc + parts.path).replace("/", "_")
        # Collapse '..' (path-traversal protection — even though we don't
        # use the raw string as a path, future renames might).
        stem = _DOTRUN.sub("__", stem)
        # Strip everything not in [A-Za-z0-9._-]
        stem = _SAFE.sub("_", stem)
        stem = stem.strip("._-") or "_"
        return stem[:max_len]
    except Exception:
        # Deterministic fallback — never raise.
        return "_"
```

### Anti-Patterns to Avoid

- **DO NOT** pre-pick a free port via `socket.bind(("", 0))` and pass it via `--remote-debugging-port=N`. The TOCTOU race (between socket close and Chrome bind) is rare but real, and it's the *exact kind of intermittent failure* that wastes hours to diagnose. Use the `DevToolsActivePort` file pattern.
- **DO NOT** pass a logged-in Playwright `Page` directly to Lighthouse (this is the CLAUDE.md "What NOT to Use" anti-pattern). Lighthouse opens its own page and loses the session. The fix is the persistent-context + port pattern Phase 2 is already implementing.
- **DO NOT** install the PyPI `lighthouse` package. CLAUDE.md explicitly warns: it's an abandoned 2016 service-discovery tool, NOT Google Lighthouse. The npm `lighthouse@13.3.0` is the only real one.
- **DO NOT** call `statistics.median(samples)` on a possibly-empty list — it raises `StatisticsError`. Guard with `statistics.median(samples) if samples else None` (D-16).
- **DO NOT** treat `audits['network-requests'].details.items[].startTime` as the timing — in LH 13 the keys are `rendererStartTime` / `networkRequestTime` / `networkEndTime`. The old `startTime` key was removed.
- **DO NOT** call `statistics.fmean` or `statistics.mean` — D-16 mandates median.
- **DO NOT** use shell=True with subprocess.run on user-controlled URL strings. Pass argv as a list.
- **DO NOT** add CWV "INP" as a top-level field of any kind that isn't the labeled `inp_proxy_tbt_ms` — Phase 1's `_no_bare_inp` model validator catches it at the model layer, but the normalizer and CLI display layer must enforce the labeling too (defense in depth).
- **DO NOT** stamp a hardcoded `chrome_version` or `lighthouse_version` — read them from `lhr.environment.hostUserAgent` (Chrome UA string) and `lhr.lighthouseVersion` so the worker is the source of truth (D-04).
- **DO NOT** write to `output/<page-slug>.json` directly (no `<run_id>` subdir) — the planner may be tempted by the simpler one-level layout, but D-07 requires `output/<run_id>/`. Without the run_id directory, two `perfcrawl measure` calls in the same minute would clobber each other's CSV.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Run Lighthouse from Python | A Python wrapper around chrome-launcher + CDP messages | The Node `lighthouse@13.3.0` package via subprocess (D-02) | Lighthouse is ~50k lines; the audit ruleset evolves with Chrome. Hand-rolling = perpetual catch-up. |
| Launch + control headless Chrome | Manually shell out to `chromium --headless` and parse stderr | Playwright `launch_persistent_context` | Playwright has 10k+ closed bugs around Chrome's quirks; you'll re-encounter every single one. |
| CLI flag parsing + subcommand routing | argparse with manual help text | Typer | Auto-help, auto-completion, type hints → flags, exit codes via `typer.Exit(code)` — solved problem. |
| Pretty terminal output (tables, progress) | print() + ANSI escape codes | Rich `Table` + `Console(stderr=True)` for progress | Manual ANSI is fragile; Rich handles Windows, no-color env, terminal width detection. |
| CSV row writing | f-string + escaping | stdlib `csv.DictWriter` | Field escaping (quotes inside cells, newlines, commas) is the kind of thing that silently corrupts the team's existing Sheets import. |
| URL → safe filename | Naive `url.replace("/", "_")` | The `page_slug()` function in Pattern 4 (with `..` + percent-decoded-dot protection) | IN-02 path-traversal landmine; Phase 1 documented it. |
| Median over N | Hand-roll sort + mid-index | `statistics.median` | The hand-roll skips even-N midpoint averaging. Use stdlib. |
| Lighthouse audit JSON parsing | hand-walk the dict everywhere | A single `normalizer.py` module that returns a `PageResult`; every other module reads only `PageResult` | LH 14 will rename more fields; if 30 modules parse LH JSON, the v13→v14 cost is 30× the work. |

**Key insight:** This phase has exactly two pieces of real engineering — the **Python ↔ Node port handoff** and the **LH JSON → PageResult normalizer**. Everything else is glue and should be library calls.

## Runtime State Inventory

> Not applicable — Phase 2 is a greenfield additive phase (new modules, new commands, new files). No renames, refactors, or migrations. No existing stored data, live service config, OS-registered state, secrets, or build artifacts are affected.
>
> Phase 1 created a SQLite store at the path the caller chooses (typically `perfcrawl.db` in cwd) — Phase 2 writes new rows to it via the unchanged `store.write_run` API. No schema migration: the model is purely additive within Phase 1's nullable superset.

## Common Pitfalls

### Pitfall 1: Playwright Python doesn't expose the resolved CDP port

**What goes wrong:** The natural shape — call `launch_persistent_context(args=["--remote-debugging-port=0"])`, then ask the returned object for the port — doesn't work. There is no `context.cdp_port`, no `browser.endpoint`. The closed issue [playwright-python#2789](https://github.com/microsoft/playwright-python/issues/2789) shows this is a long-standing API gap.

**Why it happens:** Playwright's primary surface is the high-level API (Pages, Locators, Contexts) — the CDP layer is treated as an implementation detail. Reading "what port did Chrome end up on" requires going around Playwright.

**How to avoid:** Use Chrome's documented `DevToolsActivePort` file. When Chrome launches with `--remote-debugging-port=0`, it writes the chosen port to `<user_data_dir>/DevToolsActivePort` (line 1 = port, line 2 = WebSocket path). Pass a fresh `tempfile.TemporaryDirectory()` as `user_data_dir`, wait briefly for the file to appear, read line 1.

**Warning signs:** A hand-picked-port pattern (`socket.bind(("",0))` then close then pass) "works in dev, fails on CI" — that's the TOCTOU race against the kernel re-issuing the port. Symptom: occasional `Connection refused` from the Node worker.

### Pitfall 2: LH 13 renamed waterfall timing keys

**What goes wrong:** Normalizer reads `audits['network-requests'].details.items[i].startTime`, gets `undefined` / `None`, every `WaterfallEntry.timing_ms` is null. Tests pass against an older LH 11.x fixture; production produces null timings.

**Why it happens:** LH 12 split `startTime`/`endTime` into three fields: `rendererStartTime` (when the renderer first knew about the request), `networkRequestTime` (when the network service handled it), and `networkEndTime` (last byte of response body). Verified against [core/audits/network-requests.js](https://github.com/GoogleChrome/lighthouse/blob/main/core/audits/network-requests.js).

**How to avoid:** (a) D-10's version gate catches the major mismatch; (b) write the fixture from a real LH 13.3.0 run, not from documentation memory; (c) compute `timing_ms = networkEndTime - networkRequestTime` (or `rendererStartTime` if you want the "from the page's POV" timing — pick one, document the choice).

**Warning signs:** All waterfall timing_ms = null but the audit clearly ran; "slowest request" = always the first one (because they're all None and Python's `max` returns the first).

### Pitfall 3: `statistics.median([])` raises `StatisticsError`

**What goes wrong:** All N samples fail for a metric (rare but possible — e.g. CLS often misses on simple pages). The code path computes `MetricSample(median=statistics.median(samples), samples=[])` and crashes the whole orchestrator.

**Why it happens:** Python's `statistics` module declines to invent a median for empty data.

**How to avoid:** `median = statistics.median(samples) if samples else None`. Phase 1's `MetricSample` model already accepts `median: float | None = None` and the empty `samples=[]` default.

**Warning signs:** A measure run that mostly succeeds but throws `StatisticsError: no median for empty data` on rare pages.

### Pitfall 4: Lighthouse default config emulates mobile — desktop needs a config override

**What goes wrong:** `--emulation desktop` is silently ignored; LH still runs as mobile because `extends: 'lighthouse:default'` includes mobile screenEmulation + 4× CPU + slow-3G network.

**Why it happens:** `lighthouse:default` is calibrated for mobile audits. Setting `formFactor: 'desktop'` alone doesn't change the screenEmulation/throttling values — those have to be overridden too (per [LH emulation docs](https://github.com/GoogleChrome/lighthouse/blob/main/docs/emulation.md)).

**How to avoid:** When `--emulation=desktop`, override the full desktop preset (1350×940 viewport, mobile=false, cpuSlowdownMultiplier=1, 10240 Kbps, 40ms RTT) — see Pattern 2 worker code. Or use the CLI preset by invoking `lighthouse:default-desktop` config, if LH 13 exposes one (verify).

**Warning signs:** Desktop runs produce identical perf scores to mobile runs.

### Pitfall 5: persistent-context `BrowserContext` doesn't have a `.browser`

**What goes wrong:** Code does `context.browser.new_context()` to cycle for cold cache, gets `AttributeError: 'NoneType' object has no attribute 'new_context'`. The whole orchestrator falls over after sample 1.

**Why it happens:** `launch_persistent_context` returns a `BrowserContext` whose `.browser` is `None` — the context IS the browser surface in persistent mode. There is no separate Browser object you can call `new_context` on.

**How to avoid:** For Phase 2's RUN-03 (cold cache), the cleanest pattern is NOT `new_context` on the same Chrome process — it's: kill the persistent context, launch a fresh one per sample on the same Chrome via the same port. OR: use a NON-persistent `launch()` + `connect_over_cdp` after Chrome is up, which gives you `browser.new_context()`. The non-persistent route is simpler for Phase 2 (no auth yet) and Phase 4 can swap in persistent context when storage_state lands. **Recommended:** launch Chrome via subprocess with `--remote-debugging-port=0 --user-data-dir=<tmp>`, then `playwright.chromium.connect_over_cdp(f"http://localhost:{port}")` — this gives a real Browser object with `new_context()` available.

**Warning signs:** AttributeError on `.browser.new_context()`; or sample 2 sees sample 1's cache because `BrowserContext` was reused.

### Pitfall 6: `result.report` shape changes with `flags.output`

**What goes wrong:** Worker code does `fs.writeFileSync('lhreport.html', runnerResult.report)` but `flags.output=['json','html']`, so `report` is an array, not a string. The file contains `[object Array]` or `null`.

**Why it happens:** `result.report` is a **string** when `flags.output` is a single string (`'html'` or `'json'`), and an **array** when `flags.output` is an array. Verified against LH docs/readme.md.

**How to avoid:** Always destructure: `const [reportJson, reportHtml] = result.report;` when `flags.output=['json','html']`. Test the worker shape with the JSON fixture (the canned fixture should be the same shape the worker produces).

**Warning signs:** Either the JSON output is the literal string `[object Object]` or the HTML report is broken/empty.

### Pitfall 7: `--samples 1` median-of-1 must work end-to-end

**What goes wrong:** Edge case where `--samples 1` produces `MetricSample(median=v, samples=[v])` — the planner forgets to test this, the orchestrator special-cases N>1, the developer's first invocation crashes.

**Why it happens:** Median-of-1 looks redundant ("isn't the value itself the median?") and gets skipped in tests.

**How to avoid:** Explicit unit test: `samples=[42.0]` → `MetricSample(median=42.0, samples=[42.0])`. `statistics.median([42.0]) == 42.0` is well-defined.

**Warning signs:** `pytest` passes with `--samples=3` fixtures; first dev invocation crashes on `--samples 1`.

### Pitfall 8: Mistakenly installing the PyPI `lighthouse` package

**What goes wrong:** A future contributor reads RESEARCH.md, sees "lighthouse", runs `uv add lighthouse` (Python ecosystem), gets the 2016 abandoned service-discovery tool. Build does not error; nothing works.

**Why it happens:** Cross-ecosystem name collision. CLAUDE.md flags this explicitly under "What NOT to Use" but the warning is easy to miss.

**How to avoid:** RESEARCH.md and PLAN.md both name the package as **`lighthouse@13.3.0` from npm**, never bare "lighthouse". The Python `pyproject.toml` MUST NOT list `lighthouse` as a dependency. Add a comment in `lighthouse-worker/package.json` if the planner thinks it helps.

**Warning signs:** `import lighthouse` in Python code (should never exist); a `lighthouse` entry under `[project.dependencies]` in `pyproject.toml`.

## Code Examples

### Reading the resolved CDP port (D-01 plumbing)

```python
# Source: this research, synthesized from Chrome DevTools docs (DevToolsActivePort
# behavior) and the playwright-python#2789 community workaround.

import tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright

def launch_chrome_with_cdp_port() -> tuple["Browser", "Process", int, str]:
    """Returns (browser, port, tmpdir) — caller owns cleanup of the tmpdir."""
    user_data_dir = tempfile.mkdtemp(prefix="perfcrawl-chrome-")
    # NOTE: Using subprocess.Popen for Chrome here (not launch_persistent_context)
    # so we can get a real Browser object via connect_over_cdp afterwards —
    # see Pitfall 5. This sidesteps the persistent-context AttributeError.
    import subprocess
    chrome = subprocess.Popen([
        "chromium",  # or use playwright's bundled chromium path
        f"--user-data-dir={user_data_dir}",
        "--remote-debugging-port=0",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    port_file = Path(user_data_dir) / "DevToolsActivePort"
    for _ in range(50):           # ~5s budget — fail fast on broken Chrome
        if port_file.exists():
            text = port_file.read_text().strip()
            if text:              # non-empty file means Chrome wrote it
                return chrome, int(text.splitlines()[0]), user_data_dir
        time.sleep(0.1)
    chrome.kill()
    raise RuntimeError("Chrome did not write DevToolsActivePort within 5s")
```

### Normalizer skeleton

```python
# Source: this research; uses Pydantic model boundaries from src/perfcrawl/models.py

from perfcrawl.models import PageResult, MetricSample, WaterfallEntry
from perfcrawl.canonical import canonical_key

def normalize_lh(lhr: dict, *, url_as_measured: str) -> PageResult:
    _check_version(lhr)  # D-10 — hard error on major mismatch

    audits = lhr["audits"]
    cats = lhr["categories"]

    def _cat_score(key: str) -> float | None:
        score = cats.get(key, {}).get("score")
        return float(score * 100) if score is not None else None

    def _numeric(audit_id: str) -> float | None:
        v = audits.get(audit_id, {}).get("numericValue")
        return float(v) if v is not None else None

    # METRIC-03 waterfall — note LH 13 field names!
    waterfall = []
    main_doc_status = None
    for item in audits.get("network-requests", {}).get("details", {}).get("items", []):
        start = item.get("networkRequestTime")
        end = item.get("networkEndTime")
        timing = (end - start) if (start is not None and end is not None) else None
        entry = WaterfallEntry(
            url=item.get("url"),
            resource_type=item.get("resourceType"),
            size_bytes=item.get("transferSize"),
            timing_ms=timing,
            status_code=item.get("statusCode"),
        )
        waterfall.append(entry)
        # The first item (or the one whose url matches finalDisplayedUrl) is
        # the main document — use its status_code for D-13.
        if main_doc_status is None and item.get("url") == lhr.get("finalDisplayedUrl"):
            main_doc_status = item.get("statusCode")

    slowest = max(
        (w for w in waterfall if w.timing_ms is not None),
        key=lambda w: w.timing_ms,
        default=None,
    )

    # D-12: curated diagnostics — only failing audits.
    diagnostics = {
        aid: a for aid, a in audits.items()
        if a.get("score") is not None and a["score"] < 1
    }

    return PageResult(
        url=url_as_measured,
        url_key=canonical_key(url_as_measured),
        perf_score=_cat_score("performance"),
        a11y_score=_cat_score("accessibility"),
        seo_score=_cat_score("seo"),
        best_practices_score=_cat_score("best-practices"),
        lcp_ms=MetricSample(median=_numeric("largest-contentful-paint"),
                            samples=[_numeric("largest-contentful-paint")] if _numeric("largest-contentful-paint") is not None else []),
        cls=MetricSample(median=_numeric("cumulative-layout-shift"),
                         samples=[_numeric("cumulative-layout-shift")] if _numeric("cumulative-layout-shift") is not None else []),
        # D-11/D-15: TBT → inp_proxy_tbt_ms — NEVER named 'inp'.
        inp_proxy_tbt_ms=MetricSample(median=_numeric("total-blocking-time"),
                                       samples=[_numeric("total-blocking-time")] if _numeric("total-blocking-time") is not None else []),
        ttfb_ms=MetricSample(median=_numeric("server-response-time"),
                             samples=[_numeric("server-response-time")] if _numeric("server-response-time") is not None else []),
        request_count=len(waterfall),
        total_bytes=int(_numeric("total-byte-weight")) if _numeric("total-byte-weight") is not None else None,
        status_code=main_doc_status,
        slowest_request_url=slowest.url if slowest else None,
        slowest_request_ms=slowest.timing_ms if slowest else None,
        waterfall=waterfall,
        diagnostics=diagnostics or None,
        analysis=None,  # Phase 5 fills.
    )
```

NOTE: the per-sample MetricSample assembly above is illustrative for one sample. The Phase 2 aggregator collects across N samples, then constructs `MetricSample(median=median(non_nulls), samples=non_nulls)`. The single-sample shape above is what each worker call yields; the orchestrator zips them.

### Median aggregation across N samples (D-14, D-16)

```python
# Source: this research; pattern matches the safe_pct/safe_abs finite-guard
# from Phase 1's LEARNINGS "Finite-guard pattern".

import statistics
import math

def aggregate_samples(per_sample_values: list[float | None]) -> "MetricSample":
    """D-14/D-16: median over successful samples; empty → median=None, samples=[]."""
    # Drop None (failed sample for this metric) AND non-finite (defense in depth
    # — the LH JSON shouldn't produce them, but PageResult's allow_inf_nan=False
    # would reject the model on the way out anyway).
    clean = [v for v in per_sample_values if v is not None and math.isfinite(v)]
    if not clean:
        # D-16: no padding, no fabricated median. Honestly empty.
        return MetricSample(median=None, samples=[])
    return MetricSample(median=statistics.median(clean), samples=clean)
```

### Typer CLI shape

```python
# Source: this research, https://typer.tiangolo.com/tutorial/commands/

import sys, json
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, add_completion=False)
err_console = Console(stderr=True)
out_console = Console()  # stdout

@app.command()
def measure(
    url: str = typer.Argument(..., help="URL to audit"),
    samples: int = typer.Option(3, "--samples", "-n", min=1, help="Sample count"),
    emulation: str = typer.Option("mobile", "--emulation", help="mobile|desktop"),
    output_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON to stdout"),
    output_dir: str = typer.Option("output", "--output-dir"),
) -> None:
    """Measure one URL end-to-end."""
    try:
        # ... orchestrator.run(...) returns RunRecord
        run_record = ...
    except UserError as e:
        err_console.print(f"[red]error:[/red] {e}", style="bold")
        raise typer.Exit(code=1)  # D-15
    except MeasurementError as e:
        err_console.print(f"[red]measurement failed:[/red] {e}", style="bold")
        raise typer.Exit(code=2)  # D-15

    if output_json:
        sys.stdout.write(run_record.model_dump_json(indent=2))
    else:
        _render_table(run_record, samples)

# Then expose for [project.scripts] in pyproject.toml:
#   perfcrawl = "perfcrawl.cli:app"
```

### CSV column order (OUT-04)

The flat CSV is a **superset** of the existing studyhalo Google Sheet (per `.planning/PROJECT.md` "Context"), plus Phase 1's added fields. Locked column order (one place — `output.py`):

```python
CSV_COLUMNS: list[str] = [
    # --- existing Google Sheet columns (preserve order, names match team's spreadsheet) ---
    "page",                  # human label — empty for now; Phase 3 fills from <title> or path
    "url",                   # PageResult.url (as measured)
    "test_date",             # RunRecord.started_at.isoformat()
    "cache_disabled",        # always "TRUE" — RUN-03 cold cache
    "total_page_load_time",  # ms — derived from waterfall (max networkEndTime - min rendererStartTime)
    "request_count",         # PageResult.request_count
    "total_bytes",           # PageResult.total_bytes
    "slowest_request_url",   # PageResult.slowest_request_url
    "slowest_request_ms",    # PageResult.slowest_request_ms
    "ttfb_ms",               # PageResult.ttfb_ms.median
    "status_code",           # PageResult.status_code
    # --- Phase 1 / Phase 2 additions ---
    "perf_score",
    "a11y_score",
    "seo_score",
    "best_practices_score",
    "lcp_ms",                # PageResult.lcp_ms.median
    "cls",                   # PageResult.cls.median
    "inp_proxy_tbt_ms",      # PageResult.inp_proxy_tbt_ms.median   ← LABELED. D-11/D-15.
    "schema_version",        # RunRecord.schema_version
    "run_id",                # RunRecord.id
    "chrome_version",        # RunRecord.chrome_version
    "lighthouse_version",    # RunRecord.lighthouse_version
    "emulation",             # RunRecord.emulation
]
```

The column header for `inp_proxy_tbt_ms` in the Rich human table is `"INP (lab proxy, TBT-based)"` per D-11. In the CSV the column NAME is the field name (`inp_proxy_tbt_ms`) — that's the labeled-proxy invariant: the column name itself signals it's a proxy.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Lighthouse 11.x `audits['network-requests'].details.items[].startTime/endTime` | LH 12+ split into `rendererStartTime` / `networkRequestTime` / `networkEndTime` | LH 12 (2024) | Normalizer MUST use the new keys (Pitfall 2). Tests against old fixtures pass silently while producing null timing_ms. |
| Lighthouse `chrome-launcher` programmatic launch | Browser-driven launch with port handoff (Playwright + `lighthouse({port})`) | Standard since LH 7+ (auth-aware measurement) | D-01 picks this. CLAUDE.md "What NOT to Use" already flags the "pass logged-in Page" anti-pattern. |
| Lighthouse `output: 'json'` returning a string | `output: ['json','html']` returns a 2-array | LH 6+ | Pitfall 6. Always destructure when array. |
| `messages.create` + free-text parsing (irrelevant here but Phase 5) | `messages.parse` + Pydantic models | Anthropic SDK 0.30+ | Out of scope for Phase 2; noted for cross-phase consistency. |

**Deprecated/outdated:**
- PyPI `lighthouse` — 2016 abandoned service-discovery tool. Never install. (CLAUDE.md flag.)
- Pre-LH-12 `startTime/endTime` waterfall keys. Migrate to `rendererStartTime`/`networkEndTime` parsing.
- Hand-rolling Chrome lifecycle via `chromium --headless` + shell — use Playwright.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Reading line 1 of `DevToolsActivePort` reliably yields the CDP port within ~5s of launch on the developer's typical hardware | Pitfall 1 / Code Examples | Orchestrator fails before measurement on slow CI; bump the 50×100ms retry to a configurable constant; observed reliable across published Selenium/WDIO/Playwright community patterns |
| A2 | LH 13.3.0's `lhr.environment.hostUserAgent` carries the Chrome version in a parseable form for `RunRecord.chrome_version` | Pattern 2 worker code | If absent or non-parseable, fall back to capturing Chrome version via `subprocess.run(["chromium", "--version"])` once at launch time |
| A3 | LH 13.3.0's `audits['network-requests'].details.items[]` includes the main document request, with its `url == lhr.finalDisplayedUrl`, so its `statusCode` is the page-level status_code for D-13 | Normalizer skeleton | If not, status_code is null on every page; the planner adds a fallback that reads it from `lhr.runWarnings` or skips the field. The existing fixture from a real LH 13 run will catch this. |
| A4 | The `formFactor: 'desktop'` + screenEmulation/throttling override pattern in Pattern 2 produces scores comparable to the LH CLI's `--preset=desktop`. | RUN-01 / Pattern 2 | If scores drift, Phase 2 smoke test against a stable URL with both produces different perf_scores; planner may need to invoke LH with `--preset=desktop` instead via worker argv |
| A5 | `subprocess.run(timeout=60)` reliably terminates the Node process even when Lighthouse is mid-network-stall. | D-14 / orchestrator | If the Node process doesn't respond to SIGTERM (Lighthouse's chromium subprocess hangs), the timeout exception fires but a zombie chromium remains. Mitigation: the worker should set up a process-level timeout inside Node too (`setTimeout(() => process.exit(1), 55_000)`); planner decides. |
| A6 | Two concurrent `perfcrawl measure` invocations on the same machine don't collide in their tmpdirs or Chrome processes. | Pattern 1 | `tempfile.mkdtemp(prefix="perfcrawl-")` is process-unique; Chrome's `--remote-debugging-port=0` picks a free port. Multi-invocation safety is by-construction. Worth a one-line test. |
| A7 | The Phase 1 store's `write_run` accepts a `RunRecord` with empty `url_key` on pages because canonical_key is derived inside write_run (per Phase 1 LEARNINGS). | Normalizer skeleton | We set url_key explicitly in the normalizer, so this is belt-and-suspenders; the planner can choose to leave it blank and let write_run derive. |
| A8 | The page slug `page_slug()` function in Pattern 4 is sufficient to satisfy the IN-02 sanitization invariant — no other `url_key` → path component sites exist in Phase 2. | D-07 / Pattern 4 | If any other code path constructs a filesystem path from url_key without going through page_slug(), the IN-02 protection is bypassed. Planner: add a grep-asserted invariant in tests that `url_key` is not concatenated into a `pathlib.Path` outside `slug.py`. |
| A9 | The "Lighthouse default config = mobile" claim holds in 13.3.0 (verified against docs but not against a live run in this research). | Pitfall 4 | The first end-to-end smoke test will verify; if 13.3.0 changed the default, Pattern 2's `formFactor: 'desktop'` branch becomes the unconditional explicit-config branch |
| A10 | The PyPI lighthouse warning ("Not exactly popular — 346 downloads") confirms it's the abandoned 2016 package, not a different legitimate one. | Package Legitimacy Audit | Cross-checked against CLAUDE.md's explicit warning — same story. Safe. |
| A11 | `connect_over_cdp(f"http://localhost:{port}")` reliably attaches to the Chrome launched via subprocess + `--remote-debugging-port=0`. | Pitfall 5 fix | Standard pattern across community examples; verify with a one-line smoke test ahead of Phase 4. |

## Open Questions (RESOLVED)

1. **Per-sample raw LH artifact files vs. just-the-final-sample?**
   - What we know: D-07 says `output/<run_id>/lighthouse/<page-slug>.{json,html}` (singular per page).
   - What's unclear: When `--samples 3`, do we write 3 sets of raw artifacts (`<slug>__1.json`, `<slug>__2.json`, ...) or just the last sample's?
   - Recommendation: Write **just the final successful sample's** raw artifacts to disk. The persisted JSON (`result.json`) still carries all N samples in `MetricSample.samples`, so forensic detail is preserved without 3× the disk usage. If a future user needs all N artifacts, that's a `--keep-all-samples` flag — Phase 2 ships the single-artifact default.

2. **`page` column in the CSV — fill it with what for Phase 2?**
   - What we know: The existing studyhalo Google Sheet has a human-readable `Page` label (e.g. "Homepage", "Dashboard"). Phase 3 (the crawler) can derive it from `<title>` or path.
   - What's unclear: For Phase 2's single-URL run, what goes in `page`?
   - Recommendation: Leave it empty (`""`) for Phase 2. Document in the column header comment that Phase 3 will populate it. No need to scrape `<title>` in Phase 2 — that's measurement-engine creep.

3. **`storage_state` plumbing dry-run in Phase 2?**
   - What we know: D-01/D-03 are explicitly chosen to make Phase 4 incremental (just add `storage_state` + `disableStorageReset:true`).
   - What's unclear: Should Phase 2 plumb a `--storage-state` flag (no-op except passing it through) so Phase 4 only changes Lighthouse-side, not orchestrator-side?
   - Recommendation: Don't pre-plumb. YAGNI. Phase 4 adds the flag; Phase 2 just doesn't have it. The architectural shape is preserved.

4. **Total page load time CSV column — definition?**
   - What we know: The existing Google Sheet has "Total Page Load Time".
   - What's unclear: Is that `audits['interactive'].numericValue` (Time to Interactive)? Or `audits['speed-index'].numericValue`? Or `max(networkEndTime) - min(rendererStartTime)` across waterfall?
   - Recommendation: Use `lhr.audits['interactive'].numericValue` (TTI) — closest match to "page load time" as the team historically used it. Document the choice. Phase 6 may revisit when the Sheets exporter lands.

5. **Auto-`npm ci` on first run, or documented setup?**
   - What we know: Claude's-discretion item per CONTEXT.md.
   - Recommendation: **Documented setup** for v1. Auto-invoking `npm ci` from Python is a permission/security/UX wart (silent network call, can fail in offline dev). A clear `README.md` step "before first run: `cd lighthouse-worker && npm ci`" is honest. Add a preflight check in `orchestrator.py` that verifies `lighthouse-worker/node_modules/lighthouse/package.json` exists and bails with exit-code 2 + actionable message if not.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js | lighthouse-worker (D-04) | ✓ | v23.11.0 (≥22.19 LTS satisfied) | — |
| npm | lighthouse-worker install | ✓ | 10.9.2 | — |
| Python 3.12+ | All Python modules | ✓ | 3.14.0 (≥3.12 satisfied) | — |
| uv | Python deps | ✓ | 0.11.16 | — |
| Chrome / Chromium binary | Playwright headless launch | TBD — needs `playwright install chromium` | — | Playwright installs its own bundled chromium under `~/Library/Caches/ms-playwright/` |
| pytest | Test suite | ✓ (already installed) | 8+ | — |
| ruff | Lint | ✓ (already installed) | 0.15 | — |
| slopcheck | Package-legitimacy verification | ✓ | (system-installed) | manual cross-check via `npm view` |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** Playwright's bundled chromium is acquired with `uv run playwright install chromium` — must be in the Phase 2 setup steps.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8+ (already installed via `pyproject.toml` dev group) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` — testpaths=tests, pythonpath=src, addopts=-ra |
| Quick run command | `uv run pytest -x` |
| Full suite command | `uv run pytest` |
| E2E suite command | `uv run pytest -m e2e` (separate marker, needs Node + Chrome + network) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| METRIC-01 | `lhr.categories.{performance,accessibility,seo,best-practices}.score` map to perf/a11y/seo/bp on PageResult | unit (normalizer + fixture) | `uv run pytest tests/test_normalizer.py::test_category_scores_mapped -x` | Wave 0 |
| METRIC-02 | LCP, CLS, TBT pulled from correct audit IDs into respective MetricSample fields; TBT is labeled `inp_proxy_tbt_ms` (never bare `inp`) | unit + property | `uv run pytest tests/test_normalizer.py::test_cwv_mapping -x` | Wave 0 |
| METRIC-03 | `audits['network-requests'].details.items[]` builds WaterfallEntry list with correct timing computation (LH 13 field names) | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_waterfall_timing_uses_lh13_keys -x` | Wave 0 |
| METRIC-04 | TTFB, request_count, total_bytes, status_code, slowest_request_url/ms derived correctly | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_network_facts -x` | Wave 0 |
| METRIC-05 | `diagnostics` dict contains only `score < 1` audits, excludes passing/meta audits | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_diagnostics_curated -x` | Wave 0 |
| RUN-01 | Worker's argv `--form-factor=mobile\|desktop` produces correct LH config | unit (worker argv contract — Python side mocks subprocess; Node side smoke) | `uv run pytest tests/test_worker.py::test_form_factor_passthrough -x` | Wave 0 |
| RUN-02 | Throttling appears in `RunRecord.throttling` stamp from worker output | unit (fixture for stamp) | `uv run pytest tests/test_normalizer.py::test_throttling_stamp -x` | Wave 0 |
| RUN-03 | Cold cache — each sample uses a fresh BrowserContext | integration (mocked Playwright) | `uv run pytest tests/test_orchestrator.py::test_fresh_context_per_sample -x` | Wave 0 |
| RUN-04 | `--samples N` → MetricSample.median over successful samples, samples[] preserved | unit | `uv run pytest tests/test_aggregator.py::test_median_of_n -x`; `test_median_of_one`; `test_empty_samples_median_none` | Wave 0 |
| OUT-03 | Raw LH JSON + HTML written to `output/<run_id>/lighthouse/<page-slug>.{json,html}` | integration | `uv run pytest tests/test_output.py::test_raw_artifacts_on_disk -x` | Wave 0 |
| OUT-04 | Flat CSV + full JSON written to `output/<run_id>/result.{csv,json}` with locked column order | integration | `uv run pytest tests/test_output.py::test_csv_column_order` + `test_json_round_trip` -x | Wave 0 |
| CLI-01 | `perfcrawl measure URL` exits 0 on success, 1 on bad input, 2 on measurement failure; `--json` emits valid JSON to stdout; progress on stderr only | integration (Typer CliRunner) | `uv run pytest tests/test_cli.py -x` | Wave 0 |
| D-07 IN-02 | `page_slug("https://x.com/a/%2e%2e/b")` returns sanitized stem with no `..`, no slash | unit (property test) | `uv run pytest tests/test_slug.py::test_no_path_traversal -x` | Wave 0 |
| D-10 version gate | LH JSON with `lighthouseVersion="14.0.0"` raises ValueError in normalizer | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_version_gate_rejects_major_drift -x` | Wave 0 |
| D-13 non-2xx | LH JSON with main-document statusCode=404 produces PageResult with status_code=404 and metric fields null | unit (fixture) | `uv run pytest tests/test_normalizer.py::test_partial_result_on_non_2xx -x` | Wave 0 |
| D-14 timeout+retry | Worker timeout triggers exactly one retry; double-timeout drops the sample | integration (subprocess mocked) | `uv run pytest tests/test_orchestrator.py::test_timeout_retry_then_drop -x` | Wave 0 |
| D-15 exit codes | exit 0 on success-or-partial, 1 on Typer/usage error, 2 on all-samples-failed | integration (CliRunner) | `uv run pytest tests/test_cli.py::test_exit_codes -x` | Wave 0 |
| D-16 empty median | MetricSample(median=None, samples=[]) when all samples fail for a metric | unit | `uv run pytest tests/test_aggregator.py::test_empty_samples_median_none -x` | Wave 0 |
| E2E smoke | `perfcrawl measure https://example.com --samples 1` produces a valid RunRecord, writes outputs, exits 0 | e2e | `uv run pytest -m e2e tests/test_e2e.py -x` | Wave 0 (marker only — actual test optional gated on Node + network) |

### Sampling Rate

- **Per task commit:** `uv run pytest -x` (full unit + integration; e2e excluded by default since `-m e2e` is required to include it). Sub-second for unit-only; ~5–10s once integration tests stub subprocess.
- **Per wave merge:** `uv run pytest` (unit + integration + non-e2e; same as task commit unless markers added).
- **Phase gate:** `uv run pytest` AND a one-shot manual e2e: `perfcrawl measure https://example.com --samples 1 --json`.

### Wave 0 Gaps

- [ ] `tests/fixtures/lighthouse/studyhalo-home-200.json` — real LH 13.3.0 JSON capture from a known stable URL; THE source-of-truth fixture for normalizer tests. Capture once, commit, never regenerate without bumping `EXPECTED_LIGHTHOUSE_MAJOR_MINOR`.
- [ ] `tests/fixtures/lighthouse/studyhalo-404.json` — non-2xx fixture for D-13.
- [ ] `tests/fixtures/lighthouse/version-drift-14.json` — synthetic fixture with `lighthouseVersion="14.0.0"` for D-10 gate.
- [ ] `tests/conftest.py` — pytest e2e marker registration: `pytest.ini_options.markers = ["e2e: end-to-end test requiring Node + Chrome + network"]`.
- [ ] `tests/test_normalizer.py` — covers METRIC-01..05, D-10, D-11, D-13.
- [ ] `tests/test_slug.py` — covers D-07 IN-02 sanitization + edge cases (empty key, very long path, all-special-chars).
- [ ] `tests/test_aggregator.py` — covers RUN-04, D-14, D-16 (median-of-N, median-of-1, empty median).
- [ ] `tests/test_worker.py` — covers worker subprocess contract (Python-side mocked; argv passthrough; stdout JSON shape; exit-code semantics).
- [ ] `tests/test_orchestrator.py` — covers RUN-03 fresh-context-per-sample, D-14 timeout+retry, D-15 measurement-error path. Mocks Playwright + subprocess.
- [ ] `tests/test_output.py` — covers OUT-03 raw artifact layout, OUT-04 CSV column order + JSON round-trip.
- [ ] `tests/test_cli.py` — covers CLI-01 + D-15 exit codes + `--json` machine output. Uses Typer's CliRunner.
- [ ] `tests/test_e2e.py` — optional, marked `@pytest.mark.e2e`. Runs real `perfcrawl measure` against `https://example.com --samples 1`.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Phase 4 (AUTH-01..04) — not Phase 2 |
| V3 Session Management | no | Phase 4 |
| V4 Access Control | no | The tool is a local CLI; no multi-tenant access surface |
| V5 Input Validation | yes | URL validation in CLI; subprocess argv is a list (no shell=True); slug sanitization (IN-02); LH version gate |
| V6 Cryptography | no | No keys, no secrets stored in Phase 2 |
| V7 Error Handling | yes | Typer exit codes 0/1/2; structured error messages; no stack-trace leakage to end users unless `--debug` is passed (planner discretion) |
| V8 Data Protection | yes | The SQLite DB and output dir contain measured URLs and HTML reports — gitignore them by default (already in `.gitignore` per Phase 1: `*.db`, `*.sqlite`). Phase 2 should also gitignore `output/` |
| V12 Files and Resources | yes | The IN-02 boundary: page_slug() prevents path traversal when writing `output/<run_id>/lighthouse/<slug>.html` |
| V13 API and Web Service | no | No API exposed |
| V14 Configuration | yes | The `lighthouse-worker/package.json` engines field hard-enforces Node ≥22.19; `package-lock.json` is committed for reproducible installs |

### Known Threat Patterns for {stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Path traversal via crafted URL (page_slug from `url_key` containing `..`) | Tampering | Slug sanitizer (Pattern 4) — collapses `..`, restricts charset to `[A-Za-z0-9._-]` |
| Shell injection via URL containing metacharacters | Tampering | `subprocess.run(argv_list)` — never `shell=True`; URL is one argv element, not interpolated |
| Slopsquatting via PyPI `lighthouse` (wrong-namespace install) | Spoofing | Documented warning in CLAUDE.md "What NOT to Use"; RESEARCH.md repeats; never list `lighthouse` in `pyproject.toml`; package.json in `lighthouse-worker/` is the only `lighthouse` reference |
| Zombie Chrome processes after orchestrator crash | Denial of Service (resource) | `try/finally: chrome.kill()`; orchestrator owns the Chrome lifecycle deterministically (D-01/D-03) |
| Subprocess hang despite Python timeout (Node holds chromium) | Denial of Service | Belt-and-suspenders: worker sets its own internal `setTimeout(() => process.exit(1), 55000)` so Node exits even if Lighthouse hangs |
| Untrusted LH JSON shape (compromised lighthouse-worker output) | Tampering | The hybrid TEXT-blob store (Phase 1) bytes-preserves; the version gate (D-10) hard-errors on major drift; Pydantic model boundaries (`extra="ignore"`, `allow_inf_nan=False`) reject inf/nan/typed-mismatch |
| Disk-fill via unbounded `output/<run_id>/` accumulation across many runs | Denial of Service (resource) | Document `output/` cleanup as user-owned for v1; planner-discretion to add a `--prune-older-than` flag in v2 |
| Sensitive URL leakage in committed `output/` | Disclosure | Add `output/` to `.gitignore` (planner-discretion per CONTEXT.md; recommend YES) |

## Sources

### Primary (HIGH confidence)
- `npm view lighthouse version` → 13.3.0; `npm view lighthouse engines` → `{ node: '>=22.19' }`; `npm view lighthouse repository` → `git+https://github.com/GoogleChrome/lighthouse.git`; `npm view lighthouse maintainers` → Chrome team incl. paulirish. [VERIFIED via local npm registry query, 2026-05-28]
- [GoogleChrome/lighthouse README — programmatic usage](https://github.com/GoogleChrome/lighthouse/blob/main/docs/readme.md) — the canonical `import lighthouse from "lighthouse"` programmatic shape, `port`+`flags`+`config` triple, `result.report`/`result.lhr`/`result.artifacts` return shape
- [GoogleChrome/lighthouse `core/audits/network-requests.js`](https://github.com/GoogleChrome/lighthouse/blob/main/core/audits/network-requests.js) — verified verbatim the LH 13 waterfall field names (rendererStartTime / networkRequestTime / networkEndTime, transferSize, statusCode, resourceType, mimeType, priority, protocol, sessionTargetType)
- [GoogleChrome/lighthouse `core/lib/network-request.js`](https://github.com/GoogleChrome/lighthouse/blob/main/core/lib/network-request.js) — confirmed NetworkRequest class field set
- [GoogleChrome/lighthouse `types/lhr/settings.d.ts`](https://github.com/GoogleChrome/lighthouse/blob/main/types/lhr/settings.d.ts) — verified ConfigSettings / ScreenEmulationSettings / ThrottlingSettings TypeScript shapes
- [GoogleChrome/lighthouse `docs/understanding-results.md`](https://github.com/GoogleChrome/lighthouse/blob/main/docs/understanding-results.md) — top-level LHR keys (lighthouseVersion, fetchTime, requestedUrl, mainDocumentUrl, finalDisplayedUrl, audits, configSettings, timing, categories, categoryGroups, runtimeError, runWarnings)
- [GoogleChrome/lighthouse `docs/emulation.md`](https://github.com/GoogleChrome/lighthouse/blob/main/docs/emulation.md) — formFactor / screenEmulation / throttling / throttlingMethod semantics
- [Playwright Python BrowserType class docs](https://playwright.dev/python/docs/api/class-browsertype) — `launch_persistent_context(user_data_dir, args, headless, …)` and `connect_over_cdp(endpoint_url, …)` signatures
- `src/perfcrawl/models.py` — Phase 1 model contract (PageResult, MetricSample, WaterfallEntry, RunRecord) with `_no_bare_inp` validator and `allow_inf_nan=False`
- `src/perfcrawl/store.py` — Phase 1 `write_run` atomic `with conn:` + per-connection `PRAGMA foreign_keys = ON`
- `src/perfcrawl/canonical.py` — Phase 1 IN-02 documentation in the canonical_key docstring
- `src/perfcrawl/registry.py` — METRIC_POLARITY registry (Phase 2 doesn't extend, must respect)
- `.planning/phases/01-data-model-persistence-foundation/01-LEARNINGS.md` — IN-02 path-traversal landmine, labeled-INP-proxy invariant, finite-guard pattern, atomic write_run pattern, defensive try/except + deterministic fallback pattern
- `.planning/phases/02-single-page-measurement-slice/02-CONTEXT.md` — D-01..D-16 locked decisions

### Secondary (MEDIUM confidence)
- [playwright-python issue #2789 (closed) — getting the CDP websocket URL](https://github.com/microsoft/playwright-python/issues/2789) — confirms the missing-API status; no in-thread resolution but cross-referenced with Chrome's documented DevToolsActivePort behavior
- [DEV "Programmatically audit with Lighthouse and performance budgets"](https://dev.to/jamescryer/programmatically-audit-with-lighthouse-and-performance-budgets-9kb) — the `disableStorageReset` flag programmatic pattern (relevant for Phase 4; useful here as plumbing reference)
- [BrowserStack — Connecting Playwright to an Existing Browser](https://www.browserstack.com/guide/playwright-connect-to-existing-browser) — the `connect_over_cdp(f"http://localhost:{port}/")` pattern
- [The port 0 trick — David North](https://www.dnorth.net/2012/03/17/the-port-0-trick/) — documents the TOCTOU race in the "pick a free port via socket bind 0" pattern, motivating the DevToolsActivePort approach instead
- [Python docs — `statistics` module](https://docs.python.org/3/library/statistics.html) — `median([])` raises `StatisticsError`
- [Python docs — `subprocess` module](https://docs.python.org/3/library/subprocess.html) — `run(..., timeout=, capture_output=True, text=True, encoding="utf-8")` shape
- [Typer commands docs](https://typer.tiangolo.com/tutorial/commands/) + [Typer typer command docs](https://typer.tiangolo.com/tutorial/typer-command/) — subcommand + exit-code patterns
- [Rich Progress Display docs](https://rich.readthedocs.io/en/stable/progress.html) — `Console(stderr=True)` for stderr progress; standard pattern
- CLI-best-practices reference: [hackmd.io artur tamborski CLI best practices](https://hackmd.io/@arturtamborski/cli-best-practices) — "stderr for errors/progress, stdout for output" standard

### Tertiary (LOW confidence)
- [smashingmagazine.com Lighthouse programmatic intro](https://www.smashingmagazine.com/2020/09/introduction-running-lighthouse-programmatically/) — illustrative but dated (LH version)
- [izifortune.com Lighthouse architecture demystified](https://izifortune.com/lighthouse-architecture-demystified/) — architectural overview, not version-current
- [playwright-lighthouse npm](https://www.npmjs.com/package/playwright-lighthouse) — package homepage was inaccessible (HTTP 403) during this research; the equivalent pattern is documented from primary sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every package version verified against its registry on 2026-05-28; npm `lighthouse@13.3.0` confirmed as GoogleChrome/lighthouse (legitimate); PyPI decoy explicitly avoided.
- Architecture (Python-Node port handoff): MEDIUM-HIGH — pattern is well-trodden but Playwright's missing port-readback API forced a workaround (DevToolsActivePort file). The workaround is well-documented across Selenium/WDIO/Playwright communities, but Phase 2 will be the first time it's exercised in this codebase — first integration run is the validation.
- LH JSON normalizer shape: HIGH — source-of-truth verified against `core/audits/network-requests.js` and `core/lib/network-request.js` on `main` (the branch 13.3.0 was cut from); the LH 12 key-rename to `rendererStartTime`/`networkEndTime` is real, documented, and the canned fixture will lock it in.
- Pitfalls: HIGH — all eight pitfalls have known-cause documentation (CLAUDE.md, LH docs, Playwright issue tracker, or Phase 1 LEARNINGS).
- Median / sampling math: HIGH — stdlib semantics, no novelty.
- CSV column order: MEDIUM — assumed the existing studyhalo Sheet column names from `.planning/PROJECT.md` "Context"; the planner should cross-check with the actual sheet header row if available.

**Research date:** 2026-05-28
**Valid until:** 2026-06-27 (~30 days; Lighthouse releases are roughly monthly — if a new LH 13.x or 14.x lands during planning, re-verify the audit shape before normalizer code-freeze)

## RESEARCH COMPLETE

The single highest-value finding for this phase is the **port-discovery seam**: Playwright Python does not expose the resolved CDP port after `--remote-debugging-port=0`, so the orchestrator must read `DevToolsActivePort` from the per-run tempdir (Chrome's documented behavior) rather than try to coax the port back out of Playwright. This shape also avoids the TOCTOU race of the alternative "pre-pick a free port via socket.bind(0)" pattern. The second-highest finding is the LH 13 waterfall key rename (`rendererStartTime` / `networkEndTime` instead of the pre-LH-12 `startTime`/`endTime`), which would silently null every `WaterfallEntry.timing_ms` in a normalizer copied from a stale tutorial — a canned LH 13.3.0 JSON fixture in `tests/fixtures/lighthouse/` is the only durable defense. Everything else (the D-16 empty-median guard, the IN-02 slug sanitizer, the 0/1/2 exit-code mapping, the CSV column order) is "apply the established pattern" rather than open research, and the Phase 1 LEARNINGS provide direct templates for each one.
