# Project Research Summary

**Project:** PerfCrawl
**Domain:** Website performance auditing & crawling CLI (frontend metrics + Lighthouse + AI analysis + multi-format output + regression tracking)
**Researched:** 2026-05-25
**Confidence:** HIGH overall; MEDIUM on backend-metrics access mechanism (flagged for dedicated spike)

## Executive Summary

PerfCrawl is a fusion of three established tool archetypes — single-page lab auditor (Lighthouse, GTmetrix), site-wide crawler (Screaming Frog, Unlighthouse), and regression monitor (SpeedCurve, Calibre) — plus a fourth layer no competitor offers: AI-generated per-page analysis grounded in actual captured metrics. The closest reference implementation is **Unlighthouse**, an open-source CLI that crawls a site via sitemap + internal links and runs Lighthouse on every page in parallel. Unlighthouse validates the core pattern end-to-end and is explicitly cited by the architecture researcher as the architectural blueprint. PerfCrawl's three differentiators over Unlighthouse are: (1) **AI Observation/Potential Cause/Suggested Optimization** per page grounded in the tool's own captured metrics, (2) **backend internals** for owned Django sites (SQL query counts, slow/duplicate queries, cache stats, server timing) — something no external tool can provide, and (3) **persistent cross-run regression tracking** in a self-hosted, on-demand CLI without a SaaS subscription.

The **resolved stack decision** is Python-primary orchestrator + thin Node Lighthouse worker. All team-relevant concerns — Django backend metrics, Anthropic SDK, gspread, SQLite persistence, regression logic, CLI — live in Python. Node is quarantined to a ~100-line subprocess worker that wraps Lighthouse (npm `lighthouse@13.3.0` + `chrome-launcher`), connected to a Playwright-authenticated Chrome session via `--remote-debugging-port`. Pure Python via PageSpeed Insights API was explicitly rejected: PSI cannot crawl behind authentication (it fetches public URLs from Google's servers only), and authenticated crawling is a first-class requirement. Pure Node was rejected because it inverts the team's long-term maintenance costs across every non-Lighthouse feature. The subprocess boundary is cheap here because Lighthouse already operates as a per-page, run-to-completion process that emits complete JSON — there is no chatty IPC, just `subprocess.run()` and `json.loads(stdout)`.

The #1 implementation risk is **measurement variance**: a single Lighthouse run on the same unchanged page can swing 5–20+ performance points, making regression detection produce constant false alarms. Median-of-N runs (default 3) is a non-negotiable prerequisite for trustworthy regression detection and must ship together with or before regression flagging — never after. The second major risk cluster is **authenticated-crawl safety**: an auto-discovery crawler with an authenticated session will encounter logout links and destructive GET action links that can end the session mid-crawl or mutate the target site's data. A URL denylist, read-only crawl account, and session-liveness checks are acceptance criteria for the auth phase, not optional hardening. The third open risk is the **backend-metrics access mechanism** for owned Django sites: the custom-middleware/endpoint approach and django-silk are both viable, but the exact security-gated contract has not been spiked and must be resolved in a dedicated research spike before Phase 7 begins.

---

## Key Findings

### Resolved Stack Decision

The Python-primary + Node-worker approach is correct for this team's long-term. This decision is HIGH confidence and fully resolved.

**Pure Python (PSI API only) was rejected because:**
- PSI cannot crawl behind authentication — only fetches publicly reachable URLs from Google's servers
- PSI is rate-limited (~25k/day, 400/100s)
- Authenticated crawling of dashboards is an explicit, high-value requirement

**Pure Node was rejected because:**
- Every non-Lighthouse feature (Django metrics, Anthropic SDK, gspread, SQLite) is cleaner in Python
- The team is a Django/Python shop — maintenance fluency matters long-term
- Forcing the whole tool into Node inverts the cost structure to save a subprocess boundary on one concern

**The subprocess boundary is cheap because:**
- Lighthouse requires one process per audit anyway (one audit per Node process is a hard constraint)
- It natively emits complete JSON from stdout
- The natural integration is exactly `subprocess.run()` + `json.loads(stdout)` — no chatty IPC

**Core technologies:**
- **Python 3.12+**: Primary language — orchestration, crawl logic, AI, outputs, persistence, CLI
- **Node.js 22.19+ LTS**: Runtime for the Lighthouse measurement worker only (Lighthouse 13.x hard requirement)
- **Playwright for Python 1.60.x**: Browser automation — crawl, link discovery, authenticated login, network capture via CDP; native `storage_state` for auth reuse
- **lighthouse npm 13.3.0**: The measurement engine — Lighthouse scores, lab CWV, network/timing audits; no Python equivalent exists
- **chrome-launcher npm**: Launch Chrome for the Node worker and/or connect Lighthouse to the Playwright-opened CDP port
- **anthropic Python SDK 0.104.x**: AI analysis — structured output via `messages.parse()` with Pydantic models, prompt caching for the per-crawl rubric
- **Typer 0.15.x + Rich 13.x**: CLI framework and live progress display
- **gspread 6.2.x + google-auth 2.x**: Google Sheets output via service account
- **Jinja2 3.1.x**: HTML report generation (single-file self-contained templates)
- **Pydantic 2.x**: Typed canonical result model; validates AI structured output
- **stdlib sqlite3**: Run persistence and regression history store
- **uv**: Python dependency management; **npm** (isolated to `lighthouse-worker/` subdir) for the Node worker only

**Key stack notes:**
- PSI API remains useful as a **supplementary** source for public pages (adds real-user CrUX field data including real INP), but is not the core engine
- INP is a **field metric** requiring real user interaction; lab tools cannot measure it reliably. Report Lighthouse's Total Blocking Time as the lab proxy; pull real INP from PSI/CrUX for public pages only. Never label a headless lab pass as "INP"
- SQLite beats DuckDB for this workload: transactional small inserts (one row per page per run) and point lookups ("get prior run for URL X") are SQLite's OLTP sweet spot. DuckDB only wins on big columnar analytical scans that this tool will not perform in v1

### Canonical Data Model (The Load-Bearing Architectural Decision)

The `PageResult` / `RunRecord` / `RunDelta` model is the single most important decision in the entire system. Everything downstream of the measurement layer — AI analysis, history store, regression deltas, and all four output formats — consumes only this canonical model. No exporter ever touches raw Lighthouse JSON. This is what enables one schema to simultaneously drive Sheets columns, HTML report, CSV/JSON, regression diffs, and AI grounding.

The model is defined once in `core/types` and nothing imports up into it. Key fields:

- **PageResult** (one row per page per run): identity (runId, url, capturedAt, status/error), lighthouse scores (performance/seo/accessibility/bestPractices), webVitals (lcpMs, cls, inpMs, fcpMs, tbtMs), network (ttfbMs, totalLoadMs, requestCount, totalBytes, slowestRequestUrl, slowestRequestMs, responseSizeBytes, httpStatus), optional `backend` block (sqlQueryCount, sqlDurationMs, duplicateQueries, cacheHits/Misses, serverTimeMs — present only for owned sites), optional `analysis` block (observation, potentialCause, suggestedOptimization), artifact pointers, and a `schemaVersion`
- **RunRecord** (one per crawl run): runId, siteOrigin (join key for prior-run lookup), owned flag (gates backend collection), engine, aiProvider, config (sanitized — no credentials), pageCount, errorCount, status
- **RunDelta** (computed at persist time, shared to all exporters): url, metric name, current + previous values, deltaAbs, deltaPct, direction (improved/regressed/unchanged), priorRunId

The `schemaVersion` field is load-bearing: it keeps historical runs comparable when fields are added in later phases.

### Expected Features

**Must have for v1 launch (P1 — all required):**
- Crawl from seed URL via internal links + sitemap.xml, with robots.txt awareness, depth/max-page limits, include/exclude URL patterns
- Authenticated crawling (form login → persisted Playwright `storage_state`) — required for the high-value dashboard pages
- Per-page metric collection: Lighthouse scores (Performance/SEO/Accessibility/Best Practices), CWV (LCP/CLS/lab TBT as INP proxy), network waterfall, TTFB, request count, total bytes, slowest request URL+time, response sizes, status codes
- Mobile (default) and desktop emulation, simulated throttling, cold cache, `--samples N` with per-metric median selection — median-of-N is non-negotiable prerequisite for regression trustworthiness
- AI Observation / Potential Cause / Suggested Optimization per page, grounded in captured metrics (the headline differentiator)
- Per-run persistence (SQLite) + regression/improvement flagging vs prior run with variance-aware thresholds
- Output: Google Sheets (rich schema with CWV + regression delta columns) + raw Lighthouse artifacts + CSV/JSON, user-selectable per run

**Should have for v1.x after frontend core is validated (P2):**
- Backend internals for owned Django sites (SQL counts, slow/duplicate queries, cache, server timing) feeding into AI analysis — highest-value differentiator but highest complexity; requires dedicated access-mechanism research spike first
- Performance budgets/thresholds with pass-fail + CLI exit codes
- HTML report output
- N+1/duplicate-query detection over captured backend queries (depends on backend internals existing first)
- Warm-cache / repeat-view runs

**Defer to v2+ (P3):**
- Scheduled/CI automation — design CLI with clean exit codes so this becomes a thin wrapper; do not build the scheduler now
- CrUX field data overlay (labeled as reference only; verify studyhalo URLs have CrUX coverage first)
- Multi-region/packet-level throttling (likely never)

**Explicit anti-features (do not build):**
- Always-on monitoring dashboard — a fundamentally different product class requiring hosting/scheduler/alert infra
- RUM/real-user monitoring — requires beacon injection; studyhalo pages likely lack CrUX coverage
- Applying performance fixes to the target site — audit and recommend only
- Backend internals for unowned sites — physically impossible externally

### Architecture Approach

PerfCrawl follows a **canonical model funnel** architecture: everything left of the Normalizer is heterogeneous and engine-specific (Lighthouse JSON, CDP network data, optional backend payload); everything right of it sees only `PageResult`/`RunDelta`. Four pluggable seams — measurement engine, AI provider, backend collector, output exporters — are each defined behind a stable interface with interchangeable implementations selected at runtime by config key. Concurrency lives entirely in the frontier/queue component; workers are dumb consumers. The backend collector is a no-op by default and only activates when `RunRecord.owned` is true, making the owned-vs-any-site distinction a structural boundary rather than scattered conditional checks throughout the crawler.

Unlighthouse (unlighthouse.dev) is the validated reference implementation for the overall crawl + parallel-Lighthouse pattern. PerfCrawl intentionally does not build on Unlighthouse directly (it is Node-only, lacks Django metrics, lacks AI analysis, and lacks regression persistence) but borrows its ideas: sitemap/link discovery, browser pool for parallel workers, and per-page Lighthouse execution.

**Major components:**
1. **CLI / Entrypoint** — parse args, load config, build `RunConfig`; no domain logic (Typer + Rich)
2. **Orchestrator** — owns one Run lifecycle end-to-end; partial-failure isolation so one bad page never kills the run
3. **Discovery / Crawler** — seed URL → BFS over same-origin links + sitemap.xml, robots.txt, URL deduplication with canonicalization, depth/page/time caps, include/exclude globs
4. **Auth / Storage State** — log in once via scripted Playwright flow, capture `storage_state` (cookies + localStorage), inject into every browser context; Lighthouse inherits via `disableStorageReset: true`
5. **Work Queue / Frontier** — sole owner of global max-concurrency + per-host politeness (cap + minimum inter-request delay + backoff on 429/503); workers never implement their own delays
6. **Browser Pool + Page Workers** — N bounded Chrome contexts; per URL: navigate (authed) → capture network via CDP → run Lighthouse subprocess worker → optionally trigger backend collection
7. **Engine Registry** — pluggable `MeasurementEngine` interface: local Lighthouse-over-CDP (default) or PSI API; swap is config-only
8. **Backend Collector** — optional side-channel behind `BackendCollector` interface (no-op default); writes `PageResult.backend` only when `RunRecord.owned`; never required for a run
9. **Normalizer** — raw heterogeneous captures → canonical `PageResult`; the funnel point between capture and consume halves; all consumers see only this shape
10. **AI Analysis** — pluggable `AIProvider` interface (Anthropic default); structured output via Pydantic; per-page Observation/Cause/Optimization grounded in `PageResult` metrics
11. **History Store** — SQLite; write Run + PageResults; read prior run for same `siteOrigin`; compute `RunDelta[]` at persist time (once, shared to all exporters so regression verdicts are consistent)
12. **Exporters (fan-out)** — pluggable `Exporter` interface; independent modules for Sheets, HTML, CSV/JSON, raw Lighthouse artifacts; all consume the same `PageResult[]` + `RunDelta[]`

### Critical Pitfalls

1. **Measurement variance / single-run trap (the #1 risk)** — Lighthouse metrics can swing 5–20+ points on the same unchanged page between runs. Regression detection built on single runs produces constant false alarms. Prevention: `--samples N` (default 3, allow 5) with per-metric median (not mean); store the raw distribution across all N runs; apply variance-aware delta thresholds that only flag changes exceeding the metric's known noise band. These two mechanisms must ship together in the same phase; building regression flagging before median-of-N is in place is actively harmful.

2. **Authenticated crawl safety hazards** — An auto-discovery crawler with an authenticated session will hit logout links (ending the session mid-crawl, so every subsequent page is captured as the logged-out version) and destructive GET action links (`/delete`, `/archive`, GET-based admin actions) that can mutate the owned target site's data. For StudyHalo, this means real record destruction. Prevention: default URL denylist covering `/logout`, `/signout`, `/delete`, `/remove`, `/admin`, `/destroy`, `?action=delete`, and similar patterns — checked before every fetch; always crawl with a dedicated read-only/low-privilege account (document this requirement explicitly); GET-only crawl contract; periodic session-liveness check that detects session loss and aborts rather than recording garbage. These are non-negotiable acceptance criteria for the authenticated-crawl phase.

3. **INP mislabeling** — INP is a pure field metric; headless lab tools cannot measure it (no real user interactions occur). Lighthouse substitutes TBT. If the tool labels a TBT number as "INP" it will conflict with Google's CrUX/PSI field values and mislead users. Prevention: always report TBT with an explicit "lab proxy" label; if real INP is wanted for public pages, pull it from PSI/CrUX and label it as field data. Enforced at the Normalizer and output layers from day one.

4. **Debug Toolbar / django-silk enabled in production** — The backend-metrics path is tempting to implement by enabling Debug Toolbar on the live site. DjDT has a known high-severity SQL execution vulnerability and requires `DEBUG=True`. django-silk adds heavy overhead and can store plaintext passwords in its profiling data. Prevention: the backend-metrics access mechanism must be production-safe by design, working with `DEBUG=False`. This is a security-reviewed architectural decision requiring a dedicated spike before Phase 7 begins.

5. **Infinite/exploding URL spaces (crawler traps)** — Calendar "next month" links, faceted navigation (color+size+price), session IDs or tracking params appended to every URL, and unbounded pagination can cause non-terminating crawls that hammer the target site and fill the dataset with useless duplicates that pollute regression comparisons. Prevention: hard caps (max-pages, max-depth, max-time) enforced in the crawler from day one; URL canonicalization stripping volatile query params before the visited-set check; `<link rel="canonical">` honored; per-host politeness with exponential backoff on 429/503.

---

## Implications for Roadmap

The architecture researcher proposed a concrete 9-step build order grounded in dependency analysis. Each step yields something runnable. The suggested phases below translate that sequence into deliverable-oriented phases.

### Phase 1: Data Model + Persistence Foundation
**Rationale:** The canonical `PageResult` / `RunRecord` / `RunDelta` model is the keystone that every other component imports. Defining it first means no later component ever retrofits its interface. The schemaVersion field that keeps historical runs comparable starts here.
**Delivers:** Stable typed data model, SQLite store with read/write operations, `RunDelta` computation logic (testable in isolation with fixture data), schema migration approach
**Addresses:** Regression history requirement; exporters-reach-into-raw-engine anti-pattern prevented by design
**Avoids:** Page-identity mismatch in regressions (canonical URL key defined here)
**Research flag:** Standard patterns; skip research phase. SQLite time-series-of-runs is well-documented. The model schema is specified in ARCHITECTURE.md.

### Phase 2: Single-Page Measurement Slice (the Engine Seam)
**Rationale:** Prove the hardest integration first — Playwright-authenticated Chrome + Lighthouse-over-CDP subprocess + Normalizer → `PageResult` — on a single URL before any crawling logic. This validates the Python/Node boundary, the `--remote-debugging-port` auth handoff, and the canonical model simultaneously. Risk is front-loaded where it belongs.
**Delivers:** CLI command `perfcrawl measure <url>` that runs Lighthouse on one URL (authenticated or not), normalizes to `PageResult`, persists to SQLite, emits CSV/JSON; `--samples N` with per-metric median implemented from the start; Chrome + Lighthouse version stamped in run metadata
**Addresses:** Full Lighthouse scores, CWV (with honest TBT-as-INP-proxy labeling), TTFB, request count, total bytes, slowest request, response sizes, status codes; `--desktop` flag; simulated throttling
**Avoids:** Measurement variance (median-of-N implemented here, not retrofitted); Chrome resource exhaustion (bounded concurrency, version pinning, per-audit timeout); INP mislabeling
**Research flag:** Spike the Playwright `launchPersistentContext` + `--remote-debugging-port` + `disableStorageReset:true` auth handoff on a real authenticated Django page before the phase begins. Both STACK.md and ARCHITECTURE.md identify this as the single riskiest plumbing seam.

### Phase 3: Site-Wide Crawler
**Rationale:** Scale Phase 2 from one URL to a whole site. Politeness and concurrency land here as a single, cohesive concern in the frontier component. Crawler traps (pitfall 5) are addressed structurally at this phase rather than bolted on later.
**Delivers:** `perfcrawl crawl <url>` that discovers all in-scope pages via internal links + sitemap.xml + robots.txt, enforces depth/max-page/time caps, URL canonicalization, include/exclude patterns, and per-host politeness; hands each discovered URL to the Phase 2 measurement path
**Addresses:** Site-wide discovery, robots.txt awareness, include/exclude URL patterns, sitemap discovery
**Avoids:** Infinite URL spaces (caps + canonicalization from day one); hammering the target (per-host cap + minimum delay + backoff on 429/503); recording non-2xx responses as performance data
**Research flag:** Standard crawler patterns; skip research phase. Crawlee for Python is a documented fallback if the hand-rolled frontier becomes painful.

### Phase 4: Authenticated Crawling
**Rationale:** Auth is a prerequisite for the highest-value pages (dashboards). Layer it onto the existing crawl+measure path so the auth handoff to Lighthouse is proven on the real pipeline, not in isolation.
**Delivers:** `--auth` flag (pre-saved session JSON or scripted login); Playwright form-login flow capturing `storage_state`; session injected into every browser context; Lighthouse receives auth via `disableStorageReset:true`; URL denylist enforced (logout/delete/admin patterns); read-only account recommendation documented; session-liveness checks active; CSRF/cookie handling via headless browser natively
**Addresses:** Authenticated crawling requirement; entire dashboard coverage
**Avoids:** Logout/destructive-link crawl hazard (denylist + GET-only contract are acceptance criteria before marking this phase done); per-page login waste; silent anonymous crawl on session loss
**Research flag:** The URL denylist needs to be calibrated against StudyHalo's actual URL patterns during development. Include a user-extensible pattern list.

### Phase 5: AI Analysis
**Rationale:** Pure enrichment on the existing `PageResult` — no new capture path needed. Having the stable model and a working crawl run provides the evidence to ground the AI on. Introducing the Anthropic adapter behind its interface from day one means provider-swapping is config-only from the start.
**Delivers:** `--ai` flag that runs per-page Anthropic analysis after metric collection; structured output (Pydantic model → validated Observation/Cause/Optimization); prompt caching of the static rubric across a multi-page crawl; AI columns written to all active outputs; cost estimation shown before large runs; model configurable (default claude-opus-4-7, allow claude-sonnet-4-6 for bulk runs)
**Addresses:** AI Observation/Cause/Optimization requirement — the headline differentiator and biggest manual-time saver
**Avoids:** Ungrounded AI advice (model instructed to reference specific captured metric values; suggestions that don't cite evidence are rejected); AI token/cost blowup (distilled metrics only in prompt — not full Lighthouse JSON; opt-in per run; unchanged pages skipped after regression phase ships)
**Research flag:** Prompt design needs iteration on real StudyHalo data. Plan for 1–2 prompt-tuning cycles before the phase is done. The grounding constraint ("cite metric evidence") is the most important prompt engineering decision.

### Phase 6: Full Output Suite + Regression Flagging
**Rationale:** With ≥2 stored runs now possible, regression deltas are meaningful. All exporters depend on the stable `PageResult` model being in place, and they are all independent and parallelizable to build. The existing-Sheets-compatibility requirement and schema supersession live here.
**Delivers:** Google Sheets exporter (rich schema with CWV columns + regression delta columns, batch `batchUpdate` writes, exponential backoff on 429, writes to a tool-owned tab by header name — never clobbers the historical manual baseline); HTML report exporter (single-file Jinja2 with embedded data); raw Lighthouse artifact exporter (JSON + HTML per page); regression delta computation surfaced in all outputs (direction: improved/regressed/unchanged with variance-aware thresholds); `--output sheets,html,json,lighthouse` selection
**Addresses:** All output format requirements; regression tracking requirement; the existing Google Sheets workflow compatibility requirement; performance budget thresholds (P2)
**Avoids:** Sheets rate limit (batch writes + backoff); schema drift/clobbering baseline (write to new tab, map by header name); missing environment context in regressions (run metadata stamped with Chrome/LH versions and throttling config)
**Research flag:** Sheets API quota behavior (service-account shares one quota bucket) should be verified against the team's actual crawl + run frequency once the tool is running real audits. Note: Google plans to bill for excess quota requests later in 2026.

### Phase 7: Backend Metrics for Owned Django Sites
**Rationale:** Deliberately last on the critical path. The generic any-site pipeline must be fully proven before the owned-site special case is added. The BackendCollector interface keeps the owned-vs-any-site distinction a structural boundary. This phase also requires its own dedicated research spike before any implementation begins.
**Delivers:** `BackendCollector` implementation for owned Django sites (custom perf middleware or django-silk — decided by the spike); `PageResult.backend` fields populated (sqlQueryCount, sqlDurationMs, duplicateQueries, cacheHits/Misses, serverTimeMs); backend data surfaced in AI analysis grounding (richest possible input), Sheets columns, and HTML report; fully decoupled — unowned-site runs never touch this code path
**Addresses:** Backend internals requirement for owned sites; Django SQL/cache/timing correlation with frontend TTFB
**Avoids:** Debug Toolbar/Silk in production (access mechanism is production-safe by design, works with `DEBUG=False`); backend metrics coupled into the generic crawl path (BackendCollector is a no-op default behind an interface)
**Research flag:** REQUIRES DEDICATED RESEARCH SPIKE before this phase begins. The security-gated access mechanism is unresolved. Three candidates: (1) custom Django perf middleware emitting `Server-Timing` header + opt-in authenticated `/__perf__` JSON endpoint, (2) django-silk 5.5.0 queried directly (team adds dependency + migrations to StudyHalo), (3) hybrid. The decision has security, Django-version, deployment, and maintenance implications. It must be spiked and its outcome documented in PROJECT.md Key Decisions.

### Phase Ordering Rationale

The order is: model → single-page engine slice → site-wide crawl → auth → AI → outputs+regression → backend. Risk is front-loaded (the Lighthouse/CDP/normalizer seam in Phases 1–2 is the hardest integration). The generic any-site pipeline is fully working before the owned-site special case is added (Phase 7). Every pluggable seam (engine, AI provider, backend collector, exporters) is introduced behind its interface at first use so swappability is never retrofitted. Critically, the median-of-N measurement capability is introduced in Phase 2 (not Phase 6) so regression flagging in Phase 6 is built on trustworthy data from day one — building regression flagging before multi-run median exists is an active trap.

### Research Flags

**Phases requiring dedicated research or spikes before planning:**

- **Phase 2 spike (before planning):** Validate the Playwright `launchPersistentContext` + `--remote-debugging-port` + `disableStorageReset:true` auth handoff on a real authenticated Django page. This is the riskiest plumbing seam in the entire stack. Must succeed before the rest of Phase 2 is planned.
- **Phase 7 spike (before planning):** Full dedicated research/spike for the backend-metrics access mechanism. The security-gated contract (custom middleware/endpoint vs django-silk vs hybrid) is the one genuinely unresolved architectural decision remaining. Has security, deployment, and Django-version implications. Outcome must be documented in PROJECT.md Key Decisions.

**Phases with well-documented patterns (skip research phase):**

- **Phase 1:** SQLite time-series patterns and Pydantic modeling are well-documented. The model schema is fully specified in ARCHITECTURE.md.
- **Phase 3:** Playwright BFS crawl, sitemap/robots parsing, and URL canonicalization are standard. Crawlee for Python is a documented fallback.
- **Phase 5:** Anthropic structured output, prompt caching, and Pydantic integration are covered in the SDK docs. Iteration on prompt grounding is needed but not research.
- **Phase 6:** gspread batch writes and Jinja2 HTML generation are standard patterns.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack — language decision (Python + Node worker) | HIGH | Grounded in official Lighthouse Node requirements; PSI rejection is documented; consistent across all four research files |
| Stack — library choices and versions | HIGH | Official PyPI + npm version data verified; gspread, Playwright, Pydantic, Anthropic SDK all current |
| Stack — backend metrics access mechanism | MEDIUM | Custom middleware and django-silk both viable; the security-gated endpoint contract is not yet spiked |
| Features — table stakes + MVP scope | HIGH | Cross-referenced against Unlighthouse, Screaming Frog, WebPageTest, SpeedCurve; current Lighthouse/CWV docs |
| Features — backend internals specifics | MEDIUM | django-silk capability verified; N+1 detection is custom logic not provided natively; access mechanism unresolved |
| Architecture — component structure + data model | HIGH | Near-identical reference implementation (Unlighthouse) validates the overall pattern; Lighthouse/Playwright/Crawlee docs are authoritative |
| Architecture — persistence schema specifics | MEDIUM | Synthesized from SQLite time-series practice across multiple sources; not from a single canonical source |
| Pitfalls — measurement variance | HIGH | Official Lighthouse variability.md + DebugBear; median-of-5 stability claim is from Google's own docs |
| Pitfalls — authenticated crawl hazards | HIGH | Multiple consistent sources; Django security guidance and crawler safety practices agree |
| Pitfalls — Django/Debug Toolbar security | HIGH | Official Django security release (2021 SQL exec vuln) cited |
| Pitfalls — Sheets API limits | HIGH | Official Google Workspace API docs; 2026 billing note is from official source |

**Overall confidence:** HIGH — with the explicit carve-out that the backend-metrics access mechanism needs a dedicated spike before Phase 7 planning.

### Gaps to Address

- **Backend-metrics security contract (P-critical for Phase 7):** The exact mechanism for how PerfCrawl reads per-request SQL/cache data from a Django site in a production-safe, authentication-gated way is the one genuinely unresolved architectural decision. Candidates: custom perf middleware (`Server-Timing` + authenticated JSON endpoint) vs django-silk (adds persistent profiling dependency to StudyHalo) vs hybrid. Resolve with a `/gsd-research-phase` before Phase 7 begins. The outcome must be documented in PROJECT.md Key Decisions before any Phase 7 implementation.

- **Playwright `--remote-debugging-port` auth handoff (spike before Phase 2):** The integration between Playwright's persistent browser context and Lighthouse's CDP connection is called out by both STACK.md and ARCHITECTURE.md as the single riskiest plumbing seam. A small spike (Playwright launches authenticated Chrome with `--remote-debugging-port`; Lighthouse subprocess connects with `disableStorageReset:true`; verify the session is preserved through an actual Lighthouse audit) should de-risk Phase 2 before detailed planning.

- **INP field data availability for StudyHalo:** Real INP requires CrUX field data, which requires ~1000 page loads per 28-day period. StudyHalo pages may not have CrUX coverage. Verify CrUX coverage for key StudyHalo URLs before promising real INP in the output schema.

- **Sheets API quota at actual run frequency:** The service-account quota bucket behavior at the team's expected crawl size and run frequency should be verified once the tool is running real audits. Google's 2026 billing note means exceeding quota will eventually have a cost.

- **PSI rate limits:** The ~25k/day, 400/100s limits are community-reported, not officially published. Treat as approximate. Verify empirically if PSI supplementary mode is used frequently.

---

## Sources

### Primary (HIGH confidence)
- GoogleChrome/lighthouse GitHub (package.json, variability.md, docs/recipes/auth/README.md) — Lighthouse 13.3.0 Node >=22.19 requirement; CDP auth handoff pattern (`disableStorageReset`); median-of-5 variance guidance
- Unlighthouse docs (unlighthouse.dev) — reference architecture for site-wide crawl + parallel Lighthouse; PSI vs local Lighthouse comparison; `--samples` flag; large-site guidance
- Playwright Python docs (playwright.dev/python) — `storage_state`, `launchPersistentContext`, `--remote-debugging-port`, network/CDP capture, authenticated page handling
- Anthropic SDK docs — `messages.parse()` structured output, `cache_control` prompt caching, per-model cache minimums, model guidance
- Google Workspace Sheets API docs — per-minute quotas, service-account bucket, `batchUpdate`, 2026 billing note
- Django security release 2021 — Debug Toolbar SQL execution vulnerability; never enable in production
- web.dev Core Web Vitals thresholds — LCP 2.5/4s, INP 200/500ms, CLS 0.1/0.25; INP replaced FID March 2024
- PyPI (playwright 1.60.0, anthropic 0.104.1, django-silk 5.5.0, crawlee 1.7.0, gspread 6.2.x) — version verification
- Lighthouse docs/overview (Chrome for Developers) — lab-only vs PSI field data; Node module / CDP operation

### Secondary (MEDIUM confidence)
- DebugBear (reduce-lighthouse-variance, lab-vs-field) — variance reduction practices; lab TBT as INP proxy explanation
- jazzband/django-silk GitHub — capability verification (SQL counts, request timing, queryable store); production overhead warnings
- DuckDB vs SQLite comparisons (DataCamp, Better Stack, MotherDuck) — SQLite OLTP insert/lookup advantage; DuckDB columnar analytics comparison
- Crawler politeness patterns (Firecrawl glossary, web crawler system design sources) — 1–4 connections per host, minimum spacing, Retry-After handling
- Screaming Frog / WebPageTest / Sitebulb / SpeedCurve / GTmetrix documentation — feature landscape benchmarking for differentiator analysis
- Masterofcode (LLM hallucination/grounding) — grounding practices to prevent generic AI advice; input token cost dominance
- playwright-lighthouse npm / testingplus.me Lighthouse+Playwright guide — consistent confirmation that Lighthouse is Node-only; Python must shell out

### Tertiary (LOW confidence — verify empirically)
- PSI rate limits (~25k/day, 400/100s) — community-reported, not officially published
- CrUX coverage threshold (~1000 loads/28d) — community-reported; verify for StudyHalo URLs

---
*Research completed: 2026-05-25*
*Ready for roadmap: yes*
