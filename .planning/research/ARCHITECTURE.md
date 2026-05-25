# Architecture Research

**Domain:** General-purpose website performance auditing & crawling tool (CLI, headless-browser pipeline, multi-format output, run history)
**Researched:** 2026-05-25
**Confidence:** HIGH (structure, data flow, concurrency, auth, build order — backed by a near-identical reference implementation, Unlighthouse, plus official Lighthouse/Playwright/Crawlee docs). MEDIUM on exact persistence-schema specifics (synthesized from time-series-on-SQLite practice, not a single canonical source).

> **Stack note (read first):** This document is stack-aware but not stack-locked — STACK.md owns the final call. The single most architecture-shaping fact: **Lighthouse is a Node-only tool with no native Python port.** It connects to a browser over the Chrome DevTools Protocol (CDP) remote-debugging port. This makes a **Node runtime the lowest-friction host** (Crawlee + Playwright + Lighthouse all in one process, with Unlighthouse as a direct architectural blueprint). A Python runtime is viable but forces a process boundary: Python drives the crawl/auth/browser and shells out to Lighthouse over CDP as a subprocess. The component boundaries below are deliberately drawn so the **engine seam absorbs this difference** — the rest of the architecture is identical either way.

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                              CLI / ENTRYPOINT                          │
│   parse args · load config · pick outputs · resolve run profile       │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │ RunConfig
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│                            ORCHESTRATOR (Run)                          │
│   owns one Run · creates Run record · drives the pipeline · status     │
└───────┬───────────────┬────────────────┬───────────────┬─────────────┘
        │               │                │               │
        ▼               ▼                ▼               ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  DISCOVERY   │  │   AUTH /     │  │  WORK QUEUE  │  │   ENGINE     │
│  / CRAWLER   │─▶│ STORAGE STATE│  │ (URL frontier│  │  REGISTRY    │
│ seed→links,  │  │ login once,  │  │ + per-host   │  │ (Lighthouse  │
│ sitemap,     │  │ share cookies│  │ politeness)  │  │  | PSI | …)  │
│ robots.txt   │  │ /localStorage│  │              │  │              │
└──────────────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                         │                 │                 │
                         └────────┬────────┴────────┬────────┘
                                  ▼                 ▼
                    ┌──────────────────────────────────────────┐
                    │        BROWSER POOL + PAGE WORKERS        │
                    │  N concurrent Chrome contexts (autoscale) │
                    │  each worker, per URL:                    │
                    │   1. navigate (with shared auth state)    │
                    │   2. capture network metrics (CDP/HAR)    │
                    │   3. run measurement ENGINE (Lighthouse)  │
                    │   4. [owned site] collect BACKEND metrics │
                    └────────────────────┬─────────────────────┘
                                         │ raw per-page captures
                                         ▼
                    ┌──────────────────────────────────────────┐
                    │              NORMALIZER                    │
                    │  raw captures → PageResult (canonical)     │
                    └────────────────────┬─────────────────────┘
                                         │ PageResult[]
                          ┌──────────────┼──────────────┐
                          ▼              ▼              ▼
                  ┌──────────────┐ ┌───────────┐ ┌──────────────┐
                  │  AI ANALYSIS │ │ HISTORY   │ │  EXPORTERS    │
                  │ per-page     │ │ STORE     │ │ Sheets · HTML │
                  │ Observation/ │ │ (SQLite)  │ │ CSV/JSON ·    │
                  │ Cause/Optim. │ │ persist + │ │ LH artifacts  │
                  │ (provider    │ │ compute   │ │ (all consume  │
                  │  adapter)    │ │ deltas    │ │  same model)  │
                  └──────┬───────┘ └─────┬─────┘ └───────┬───────┘
                         │               │               │
                         └───────────────┴───────────────┘
                            enriched PageResult + RunDelta
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **CLI / Entrypoint** | Parse args, load config file, resolve which outputs/engine/auth are active for this run, build a `RunConfig`. No domain logic. | `commander`/`yargs` (Node) or `typer`/`click` (Python); config via `c12`/cosmiconfig or a TOML/YAML file. |
| **Orchestrator** | Owns the lifecycle of a single Run. Creates the Run record, wires components, drives crawl→measure→analyze→persist→export, tracks progress/status, handles partial-failure isolation (one bad page ≠ dead run). | Plain coordinator class. The "main loop." |
| **Discovery / Crawler** | Turn a seed URL into a stream of in-scope URLs: follow internal links, parse sitemap.xml, read robots.txt, dedupe, respect scope rules (same-origin, include/exclude globs, max depth/pages). | Crawlee `PlaywrightCrawler` (JS or Python) — gives queue + enqueue-links + robots handling for free. |
| **Auth / Storage State** | Log in once (scripted flow or pre-saved session), produce a reusable auth artifact (cookies + localStorage), inject it into every browser context so all workers crawl authenticated. | Playwright `storageState()` JSON saved once, loaded into each `BrowserContext`. |
| **Work Queue (URL frontier)** | Hold pending URLs, hand them to workers, enforce **per-host politeness** (concurrency cap + min-delay per domain) and global parallelism bound. This is *the* concurrency control point. | Crawlee's internal `RequestQueue` + `AutoscaledPool`; or a custom bounded async queue with a per-host token bucket. |
| **Engine Registry (pluggable)** | Resolve the active measurement engine behind a stable interface (`measure(page|url) → RawAudit`). Swappable: local Lighthouse-over-CDP vs PageSpeed Insights API vs a stub. | Strategy/adapter pattern; engines registered by key. |
| **Browser Pool + Page Workers** | The execution muscle: N concurrent browser contexts. Per URL — navigate with shared auth, capture network metrics, run the engine, optionally trigger backend collection. | Crawlee `BrowserPool` / `puppeteer-cluster` (what Unlighthouse uses); each worker owns one context. |
| **Backend-Metrics Collector (optional, owned sites)** | For owned sites only: capture SQL query count, slow/duplicate queries, cache hits, server timing — *without* coupling to the generic path. Plugs in as an optional per-page enrichment, behind a feature flag. | HTTP read of an app-exposed metrics endpoint or `Server-Timing` header; django-silk API; never required for a run. |
| **Normalizer** | Collapse heterogeneous raw captures (Lighthouse JSON, CDP network data, backend payload) into the **single canonical `PageResult`**. The funnel point: everything downstream sees only this shape. | Pure mapping functions per engine/source → `PageResult`. |
| **AI Analysis (pluggable)** | Per page, produce Observation / Potential Cause / Suggested Optimization from the normalized metrics. Provider-swappable, returns structured JSON. | LLM adapter (Anthropic default) using tool-use / JSON-schema structured output; LiteLLM-style interface if multi-provider. |
| **History Store** | Persist every Run + its PageResults; on each run, locate the prior run for the same site and compute per-metric **RunDelta**s (regressions/improvements). | SQLite (single file, zero infra, queryable, ideal for time-series-of-runs). |
| **Exporters (pluggable, fan-out)** | Render the *same* normalized model into each selected output: Google Sheets, HTML report, CSV/JSON, raw Lighthouse artifacts. Each exporter is independent and additive. | One module per format implementing a common `export(run, results, deltas)` interface. |

## Recommended Project Structure

```
src/
├── cli/                    # arg parsing, config load, run-profile resolution
│   └── index.ts            # entrypoint; builds RunConfig, calls orchestrator
├── core/
│   ├── orchestrator.ts     # drives one Run end-to-end; partial-failure isolation
│   ├── pipeline.ts         # crawl→measure→analyze→persist→export wiring
│   └── types.ts            # PageResult, RunRecord, RunDelta, RawAudit (the model)
├── crawl/
│   ├── discovery.ts        # seed→links, sitemap.xml, robots.txt, scope rules
│   ├── frontier.ts         # URL queue + dedupe + per-host politeness
│   └── browser-pool.ts     # context pool, autoscaled concurrency
├── auth/
│   └── storage-state.ts    # login flow + storageState capture/inject
├── engines/                # PLUGGABLE measurement engines
│   ├── engine.ts           # MeasurementEngine interface: measure() → RawAudit
│   ├── lighthouse.ts       # local Lighthouse over CDP (default)
│   ├── psi.ts              # PageSpeed Insights API engine
│   └── network.ts          # CDP/HAR network-metric capture (always-on)
├── backend/                # OPTIONAL, owned-site only
│   ├── collector.ts        # BackendCollector interface (no-op default)
│   └── django-metrics.ts   # endpoint/Server-Timing/silk adapter
├── analysis/               # PLUGGABLE AI providers
│   ├── analyzer.ts         # AIProvider interface: analyze(PageResult) → Insight
│   └── providers/
│       ├── anthropic.ts
│       └── openai.ts
├── normalize/
│   └── normalize.ts        # raw captures → PageResult (the funnel)
├── store/
│   ├── db.ts               # SQLite schema + migrations
│   ├── runs.ts             # write run + results
│   └── deltas.ts           # find prior run, compute RunDelta
└── export/                 # PLUGGABLE outputs (fan-out)
    ├── exporter.ts         # Exporter interface
    ├── sheets.ts
    ├── html.ts
    ├── csv-json.ts
    └── lighthouse-artifacts.ts
```

### Structure Rationale

- **`core/types.ts` is the keystone.** Every other folder imports the data model from here and nothing imports *up* into `core`. The canonical `PageResult`/`RunRecord` is defined once and is the only contract between the capture half (crawl/engines/backend) and the consume half (analysis/store/export).
- **`engines/`, `analysis/`, `backend/`, `export/` are all "plugin" folders** with a single interface file + concrete implementations. This is where every swappability requirement lives, isolated so adding PSI, swapping Claude→GPT, or adding a new exporter never touches the pipeline.
- **`backend/` is physically separate and optional** so the owned-vs-any-site distinction is a structural boundary, not an `if` scattered through the crawler. The default `BackendCollector` is a no-op; it only activates when a run targets an owned site with a configured collector.
- **`crawl/` separates discovery (what to visit) from frontier (when/how fast).** Politeness and concurrency are one cohesive concern in `frontier.ts`, not smeared across the crawler.

## Architectural Patterns

### Pattern 1: Canonical model funnel (Normalizer between capture and consumers)

**What:** Every heterogeneous raw capture (Lighthouse JSON, CDP network data, backend payload, PSI response) is mapped into a single `PageResult` shape *before* anything downstream touches it. AI, history, and all exporters consume only `PageResult` — never raw engine output.
**When to use:** Always, here. It is the load-bearing decision of the whole system — the same model must feed Sheets + HTML + CSV/JSON *and* power regression deltas. One shape = one source of truth.
**Trade-offs:** (+) Adding an exporter or swapping an engine is local and cheap; regression logic is engine-agnostic. (−) The model must be designed up front and versioned (`schemaVersion`) so old runs stay comparable when fields are added.

**The model (concrete):**
```typescript
// One record per page per run — drives ALL outputs and history.
interface PageResult {
  // identity / join keys
  runId: string;
  url: string;
  pageLabel: string;          // human name ("Dashboard"), for Sheets/HTML
  capturedAt: string;         // ISO timestamp
  status: 'ok' | 'error';
  error?: string;             // page failed but run continues

  // frontend / external metrics (the generic, any-site path)
  lighthouse: {
    performance: number; seo: number;
    accessibility: number; bestPractices: number;
  };
  webVitals: { lcpMs: number; cls: number; inpMs: number;
               fcpMs: number; tbtMs: number; };
  network: {
    ttfbMs: number; totalLoadMs: number;
    requestCount: number; totalBytes: number;
    slowestRequestUrl: string; slowestRequestMs: number;
    responseSizeBytes: number; httpStatus: number;
  };

  // OPTIONAL backend metrics — present only for owned sites
  backend?: {
    sqlQueryCount: number; sqlDurationMs: number;
    duplicateQueries: number; slowQueries: { sql: string; ms: number }[];
    cacheHits: number; cacheMisses: number; serverTimeMs: number;
  };

  // AI enrichment — added after measurement/normalization
  analysis?: { observation: string; potentialCause: string;
               suggestedOptimization: string; };

  // artifact pointers (kept out of the row itself)
  artifacts?: { lighthouseHtml?: string; lighthouseJson?: string; harPath?: string; };

  schemaVersion: number;      // bump when fields change; keeps history comparable
}

// One per crawl run — the comparison/grouping anchor.
interface RunRecord {
  runId: string;
  siteOrigin: string;         // join key for "prior run on same site"
  owned: boolean;             // gates backend collection
  startedAt: string; finishedAt?: string;
  engine: 'lighthouse' | 'psi';
  aiProvider?: string;
  config: RunConfig;          // outputs chosen, scope, auth used (sanitized)
  pageCount: number; errorCount: number;
  status: 'running' | 'complete' | 'failed';
}

// Computed history → drives regression flags in every output.
interface RunDelta {
  url: string;
  metric: string;             // e.g. "lighthouse.performance" | "webVitals.lcpMs"
  current: number; previous: number;
  deltaAbs: number; deltaPct: number;
  direction: 'improved' | 'regressed' | 'unchanged';
  priorRunId: string;
}
```

### Pattern 2: Strategy/adapter behind a stable interface (engine, AI, backend, exporters)

**What:** Each swappable concern is a single interface with interchangeable implementations selected at runtime by config key.
**When to use:** For all four pluggable seams. The measurement engine (Lighthouse vs PSI) and AI provider are explicit requirements; backend collector and exporters benefit equally.
**Trade-offs:** (+) Swaps are config-only; new providers don't ripple. (−) Interface must be the *narrowest common denominator* — e.g. PSI returns no raw network waterfall, so the engine interface should expose Lighthouse scores + vitals as the contract and treat HAR/network capture as a separate always-on step (CDP) that runs regardless of engine.

**Example:**
```typescript
interface MeasurementEngine {
  readonly key: string;
  // For Lighthouse: connects to the live authenticated browser via CDP port.
  // For PSI: ignores the page, calls Google's API by URL.
  measure(input: { url: string; cdpPort?: number }): Promise<RawAudit>;
}

interface AIProvider {
  analyze(p: PageResult): Promise<{ observation: string;
    potentialCause: string; suggestedOptimization: string }>;
}

interface Exporter {            // every output format implements this
  export(run: RunRecord, results: PageResult[], deltas: RunDelta[]): Promise<void>;
}
```

### Pattern 3: Bounded producer/consumer with per-host politeness (concurrency lives in the frontier)

**What:** Concurrency is owned by *one* component — the frontier/queue — not by the orchestrator and not by individual workers. A bounded pool of page workers pulls from the queue; the queue enforces (a) a global max concurrency, and (b) a per-host cap + minimum inter-request delay so a single real site is never hammered.
**When to use:** Any time you crawl a live, possibly third-party site. Politeness is non-negotiable for a tool that points at arbitrary domains.
**Trade-offs:** (+) Single, testable concurrency policy; autoscaling can ramp parallelism up/down based on CPU/memory and target responsiveness. (−) Headless-browser + Lighthouse is heavy (one Chrome per worker); realistic concurrency is small (≈2–5 contexts on a laptop), so throughput is bounded by RAM, not queue logic. Crawlee's `AutoscaledPool` handles the ramp; for a hand-rolled queue, use a per-host token bucket + a global semaphore.

**Politeness defaults (from crawler practice):** 1–4 concurrent requests per host, ≥1s between requests to the same host, honor `robots.txt`, set a clear User-Agent. Lighthouse's own page-load cost naturally spaces requests further.

## Data Flow

### Request Flow (one full run)

```
CLI: perfcrawl run --url studyhalo.com --auth session.json
                    --engine lighthouse --out sheets,html,json
    ↓ RunConfig
Orchestrator: create RunRecord (siteOrigin, owned, engine) → store as "running"
    ↓
Discovery: seed → sitemap/robots/links → in-scope URLs → Frontier queue
    ↓ (Auth state loaded once, injected into every context)
Frontier → Browser Pool (bounded, per-host polite) → Page Worker (per URL):
      navigate(authed) → capture network (CDP/HAR)
                       → run Engine (Lighthouse over CDP, disableStorageReset)
                       → [owned] BackendCollector.collect(url)
    ↓ raw captures
Normalizer: raw → PageResult (status ok/error per page)
    ↓ PageResult
AI Analysis: PageResult → + analysis {observation, cause, optimization}
    ↓ enriched PageResult[]
History Store: write Run + PageResults → find prior run (same siteOrigin)
             → compute RunDelta[] (regressed/improved per metric)
    ↓ PageResult[] + RunDelta[]
Exporters (fan-out, parallel): Sheets · HTML · CSV/JSON · LH artifacts
    ↓
Orchestrator: mark RunRecord "complete"
```

### Key Data Flows

1. **The capture→consume seam:** Everything left of the Normalizer is heterogeneous and engine/source-specific; everything right of it sees only `PageResult`/`RunDelta`. This is the single most important invariant — it is what lets one model feed four outputs and the history store simultaneously.
2. **Auth fan-in:** Storage state is produced *once* (before the crawl) and fans into every browser context. Workers never log in individually; they inherit the session. Lighthouse preserves it via `disableStorageReset: true` when connecting to the already-authenticated browser.
3. **Owned-site enrichment branch:** The backend collector is a *conditional side-channel* on the worker path, gated by `RunRecord.owned`. It writes into the optional `PageResult.backend` field and never appears on the generic any-site path — the external pipeline runs identically with or without it.
4. **History as a read-then-write:** Persisting a run first reads the previous run for the same `siteOrigin` to compute deltas, then writes the new run. Deltas are computed once at persist time and stored/passed to exporters so each output shows the same regression verdicts.

## Scaling Considerations

This is a CLI batch tool, not a multi-tenant service — "scale" here means *site size* and *run frequency*, not concurrent users.

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Small site (≤ ~50 pages), occasional runs | Single process, in-memory frontier, 2–4 browser contexts, SQLite. Default everything. No changes needed. |
| Large site (hundreds–thousands of pages) | Prefer sitemap-driven discovery over link-crawl; cap max-pages; persist the frontier (Crawlee's `RequestQueue` is resumable) so an interrupted run can resume; consider sampling representative pages rather than exhaustive crawl. |
| Frequent/automated runs (later milestone) | Run history grows — index SQLite by `siteOrigin, capturedAt`; prune or archive old runs; move artifact files (LH HTML/JSON, HAR) out of the DB to a content-addressed folder. The CDP/PSI engine choice matters: PSI offloads compute to Google (rate-limited) vs local Lighthouse (CPU/RAM-bound). |

### Scaling Priorities

1. **First bottleneck — host memory, not code.** Each worker = one Chrome + a Lighthouse run = hundreds of MB. The realistic concurrency ceiling on a dev laptop is ~2–5 contexts. Fix: autoscale concurrency to available RAM (Crawlee does this); don't try to "go wider," go *resumable*.
2. **Second bottleneck — run history volume + artifacts.** Lighthouse artifacts are large. Fix: store metric rows in SQLite, store binary/HTML artifacts on disk with pointers in `PageResult.artifacts`. Keep the fact table (PageResult) narrow.

## Anti-Patterns

### Anti-Pattern 1: Exporters reaching into raw Lighthouse JSON

**What people do:** Have the Sheets exporter parse the Lighthouse report directly, the HTML exporter parse it differently, etc.
**Why it's wrong:** N exporters × M engines = combinatorial coupling; swapping Lighthouse→PSI breaks every exporter; regression deltas can't be computed because there's no common shape.
**Do this instead:** Force everything through the Normalizer into `PageResult`. Exporters and the history store consume only the canonical model.

### Anti-Pattern 2: Scattering concurrency control across workers

**What people do:** Each worker decides its own delays / spins up browsers ad hoc; politeness implemented with random `sleep`s.
**Why it's wrong:** No global bound (OOM risk), no per-host guarantee (you DoS a real site), impossible to reason about or test.
**Do this instead:** One frontier owns global max-concurrency + per-host token bucket + min-delay. Workers are dumb consumers.

### Anti-Pattern 3: Coupling backend metrics into the generic crawl path

**What people do:** Add `if (isDjango) { ...query DB... }` branches inside the crawler/worker, or make the pipeline assume backend data exists.
**Why it's wrong:** Breaks the "works on any site" requirement, leaks owned-site assumptions everywhere, makes `PageResult.backend` implicitly required.
**Do this instead:** A `BackendCollector` interface with a no-op default, activated only when `RunRecord.owned`. It writes the optional `backend` field; nothing downstream may *require* it.

### Anti-Pattern 4: Logging in per page

**What people do:** Run the auth flow inside each worker before measuring.
**Why it's wrong:** 5–15s wasted per page, login-rate-limit risk, flakiness, and Lighthouse may reset storage and drop the session mid-audit.
**Do this instead:** Capture `storageState` once, inject into every context, and pass `disableStorageReset: true` to Lighthouse so it inherits the authenticated session.

### Anti-Pattern 5: One bad page kills the run

**What people do:** Let an exception on a single URL (timeout, 500, crashed render) abort the whole crawl.
**Why it's wrong:** Real sites have broken pages; an audit tool must survive them.
**Do this instead:** Per-page try/catch in the worker → `PageResult{status:'error', error}`. The run completes; exporters/history record the failure as data.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| **Lighthouse** | Node module connecting to a live browser via CDP `--remote-debugging-port`; pass `{port, disableStorageReset:true}`. | Node-only — no Python port. From Python, run as a subprocess that connects to the Playwright-launched browser's CDP port. This is *the* stack-defining integration. |
| **PageSpeed Insights API** | HTTPS call by URL; returns Lighthouse-equivalent scores + CrUX field data. | Alternative engine. No raw network waterfall and one page at a time + API quota — so it can't replace local capture for network metrics; treat as a scores/field-data engine, keep CDP network capture separate. |
| **Google Sheets** | Sheets API v4 (service account); one exporter writes the richer schema + delta columns. | Must coexist with the team's existing sheet workflow — new schema supersedes old columns. |
| **AI provider (Anthropic default)** | Adapter using structured output (tool-use / JSON schema) so analysis returns typed Observation/Cause/Optimization. | LiteLLM-style abstraction if multi-provider desired; Anthropic is the team default. |
| **Owned-site backend (Django)** | Read-only: app-exposed metrics endpoint, `Server-Timing` response header, or django-silk's stored data — chosen in STACK/feasibility research. | Owned sites only. Never required for a run. Decoupled behind `BackendCollector`. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Crawler ↔ Frontier | Enqueue URLs; frontier hands back work | Frontier is the sole owner of concurrency + politeness. |
| Frontier ↔ Browser Pool/Workers | Bounded pull (semaphore + per-host token bucket) | Global parallelism + per-host cap enforced here, nowhere else. |
| Engine ↔ Worker | `measure()` interface | Engine swap (Lighthouse/PSI) invisible to worker. |
| Worker ↔ Normalizer | Raw captures in, `PageResult` out | The capture→consume seam. |
| Normalizer ↔ (AI / Store / Exporters) | `PageResult` / `RunDelta` only | No consumer ever sees raw engine output. |
| Store ↔ Exporters | `RunRecord` + `PageResult[]` + `RunDelta[]` | Deltas computed once at persist, shared to all outputs. |
| Backend Collector ↔ Worker | Optional `collect()` hook, gated by `owned` | No-op default; writes optional `backend` field only. |

## Build Order Implications (for phase sequencing)

Dependencies dictate a "thin vertical slice first, then widen" sequence. Each step yields something runnable.

1. **Data model + SQLite store (`core/types.ts`, `store/`).** The keystone everything depends on. Define `PageResult`/`RunRecord`/`RunDelta` and the persistence schema first so every later component targets a stable contract. *Build first — everything imports it.*
2. **Single-page measurement: browser launch + network capture + Lighthouse-over-CDP → Normalizer → PageResult.** Prove the hardest integration (engine seam + canonical model) on *one* URL before any crawling. Validates the stack tension immediately.
3. **One exporter (CSV/JSON) + persist a run.** Closes the smallest end-to-end loop: measure one page → normalize → store → emit a file. Now you have a working tool.
4. **Crawler/discovery + frontier (concurrency + per-host politeness) + browser pool.** Scale step 2 from one URL to a whole site safely. Concurrency/politeness lands here as a single concern.
5. **Authenticated crawling (storage state).** Layer auth onto the existing crawl+measure path; unlocks the high-value behind-login pages.
6. **AI analysis stage.** Pure enrichment on `PageResult`; additive, depends only on the model. Provider adapter from day one.
7. **Remaining exporters (Google Sheets, HTML, Lighthouse artifacts) — fan-out.** Each is independent and parallelizable now that the model is stable; the existing-Sheet-compatibility requirement lives here.
8. **Regression deltas across runs.** Needs ≥2 stored runs to be meaningful, so it follows persistence + crawl. Compute at persist time, surface in all exporters.
9. **Backend-metrics collector (owned sites).** Optional side-channel; deliberately last on the critical path so the generic any-site pipeline is proven and the owned-vs-any boundary stays clean. Slots into the worker via the `BackendCollector` hook.
10. **(Later milestone) Scheduling / CI automation.** Explicitly out of scope now; the orchestrator/CLI boundary already makes it a thin wrapper later.

**Ordering rationale:** model → single-page slice → loop-closing exporter → crawl → auth → AI → outputs → history → backend. Risk is front-loaded (the Lighthouse/CDP/normalizer seam in steps 1–3); the generic external path is fully working before the owned-site special case is added; pluggable seams (engine, AI, exporter, backend) are introduced behind their interfaces from their first use so swappability is never retrofitted.

## Sources

- [How Unlighthouse Works](https://unlighthouse.dev/guide/getting-started/how-it-works.md) — near-identical reference architecture: URL discovery (routes/robots/sitemap/crawl), puppeteer-cluster browser pool for parallel Lighthouse, per-page HTML-check-then-Lighthouse flow, real-time result aggregation. HIGH.
- [Unlighthouse — Handling large sites](https://next.unlighthouse.dev/guide/large-sites.html) & [URL Discovery](https://next.unlighthouse.dev/guide/url-discovery) — sitemap-first for large sites, crawl fallback. HIGH.
- [Lighthouse: Running on Authenticated Pages with Puppeteer](https://github.com/GoogleChrome/lighthouse/blob/main/docs/recipes/auth/README.md) — CDP `--remote-debugging-port` + `{port, disableStorageReset:true}` to reuse an authenticated session. HIGH.
- [Lighthouse overview (Chrome for Developers)](https://developer.chrome.com/docs/lighthouse/overview/) — Lighthouse as Node module / CDP; lab-only vs PSI field data. HIGH.
- [Unlighthouse — PageSpeed Insights vs Lighthouse](https://unlighthouse.dev/learn-lighthouse/pagespeed-insights-vs-lighthouse) & [PSI API Node example](https://unlighthouse.dev/learn-lighthouse/pagespeed-insights-api/node-example) — PSI runs Lighthouse server-side + CrUX, one page at a time, quota-limited; engine-swap tradeoffs. HIGH.
- [Crawlee PlaywrightCrawler (JS)](https://crawlee.dev/js/api/playwright-crawler/class/PlaywrightCrawler) & [Crawlee for Python](https://github.com/apify/crawlee-python) — BrowserPool + AutoscaledPool autoscaling on CPU/memory, RequestQueue, enqueue_links, robots.txt handling; exists for both runtimes. HIGH.
- [Web Crawler System Design](https://grokkingthesystemdesign.com/guides/web-crawler-system-design/) — back-queue per-host politeness, 1–4 conns/host, ≥1s spacing. MEDIUM.
- [Playwright Authentication / storageState](https://playwright.dev/docs/auth) & [BrowserStack: Playwright storageState](https://www.browserstack.com/guide/playwright-storage-state) — capture cookies+localStorage once, inject into every context. HIGH.
- [Playwright Network](https://playwright.dev/docs/network) & [playwright-performance-metrics](https://github.com/Valiantsin2021/playwright-performance-metrics) — HAR/network waterfall, TTFB, request counts, bytes via CDP. HIGH (network capture), MEDIUM (exact metric APIs).
- [No native Python Lighthouse — subprocess/CDP approach](https://testingplus.me/how-to-integrate-lighthouse-playwright-performance-testing-2025-guide/) & [playwright-lighthouse](https://github.com/abhinaba-ghosh/playwright-lighthouse) — Lighthouse is Node-only; Python must shell out and connect over CDP. MEDIUM-HIGH (consistent across multiple sources).
- [jazzband/django-silk](https://github.com/jazzband/django-silk) — stored per-request SQL counts/timings/duplicates, queryable API; DDT not suitable for non-HTML/API responses. HIGH (existence/capability), MEDIUM (best access mechanism — defer to feasibility research).
- [Interoperability Patterns to Abstract LLM Providers](https://brics-econ.org/interoperability-patterns-to-abstract-large-language-model-providers) & [LiteLLM via Latitude: LLM integration patterns](https://latitude.so/blog/5-patterns-for-scalable-llm-service-integration) — adapter interface, structured JSON output via tool-use/schema, one-line provider swap. MEDIUM.
- [Simon Willison — SQLite JSON audit log](https://til.simonwillison.net/sqlite/json-audit-log) & [Handling Time Series Data in SQLite](https://moldstud.com/articles/p-handling-time-series-data-in-sqlite-best-practices) — narrow fact table + metadata, integer timestamps, run-over-run history in a single file. MEDIUM.

---
*Architecture research for: website performance auditing & crawling tool (PerfCrawl)*
*Researched: 2026-05-25*
