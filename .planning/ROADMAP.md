# Roadmap: PerfCrawl

## Overview

PerfCrawl replaces the slow manual per-page performance audit with one CLI command. The build is risk-front-loaded and grows as a series of vertical slices: first define the canonical data model + persistence keystone that every later component depends on (Phase 1), then prove the riskiest plumbing — Playwright-authenticated Chrome + Lighthouse-over-CDP subprocess → normalized `PageResult` → SQLite → CSV/JSON — end-to-end on a single URL (Phase 2). From there each phase widens the proven slice: scale one URL to a whole site with a polite crawler (Phase 3), reach behind login safely (Phase 4), enrich every page with grounded AI analysis (Phase 5), and finally fan out to all output formats while flagging trustworthy run-over-run regressions (Phase 6). Median-of-N measurement ships in Phase 2 so regression flagging in Phase 6 stands on stable data from day one. Backend internals for owned Django sites are deferred to v2 behind a dedicated security spike.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Data Model & Persistence Foundation** - Define the canonical PageResult/RunRecord/RunDelta model and the SQLite run store every other component depends on
- [ ] **Phase 2: Single-Page Measurement Slice** - Prove the Lighthouse-over-CDP engine seam on one URL end-to-end: measure → normalize → persist → export CSV/JSON + raw artifacts via CLI, with median-of-N
- [ ] **Phase 3: Site-Wide Crawler** - Scale measurement from one URL to a whole site with link + sitemap discovery, robots.txt, caps, include/exclude, and per-host politeness
- [ ] **Phase 4: Authenticated Crawling** - Reach pages behind login safely: login once, reuse session, denylist destructive links, detect session loss
- [ ] **Phase 5: AI Analysis** - Generate per-page Observation / Potential Cause / Suggested Optimization grounded only in captured metrics
- [ ] **Phase 6: Output Suite & Regression Flagging** - User-selectable Google Sheets + CSV/JSON + raw-artifact outputs, plus variance-aware run-over-run regression/improvement flags

## Phase Details

### Phase 1: Data Model & Persistence Foundation

**Goal**: A stable, typed canonical result model and a SQLite run store exist, so every downstream component (measurement, AI, exporters, regression) targets one contract that never needs retrofitting.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: HIST-01
**Success Criteria** (what must be TRUE):

  1. A run (run id, timestamp, per-page results) can be written to a local SQLite store and read back identically
  2. Given two stored runs for the same site, RunDelta records (current/previous/deltaAbs/deltaPct/direction) can be computed per page per metric against fixture data
  3. The PageResult/RunRecord model carries a schemaVersion so runs stored under an older schema remain comparable after fields are added
  4. Page identity uses a canonical, normalized URL key so the same page matches across runs

**Plans**: 3 plans
Plans:
**Wave 1**

- [ ] 01-01-PLAN.md — Scaffold uv project + registry tables + canonical URL key slice (criterion #4)

**Wave 2** *(blocked on Wave 1 completion)*

- [ ] 01-02-PLAN.md — Canonical record model + hybrid SQLite store: round-trip + old-schema load (criteria #1, #3, HIST-01)

**Wave 3** *(blocked on Wave 2 completion)*

- [ ] 01-03-PLAN.md — Polarity-driven RunDelta engine on fixtures (criterion #2)

### Phase 2: Single-Page Measurement Slice

**Goal**: Running `perfcrawl measure <url>` audits a single URL end-to-end — captures all frontend metrics, normalizes to PageResult, persists the run, and emits CSV/JSON plus raw Lighthouse artifacts — proving the Python-orchestrator / Node-Lighthouse-over-CDP seam and median-of-N before any crawling exists.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: METRIC-01, METRIC-02, METRIC-03, METRIC-04, METRIC-05, RUN-01, RUN-02, RUN-03, RUN-04, OUT-03, OUT-04, CLI-01
**Success Criteria** (what must be TRUE):

  1. User runs one command against a single URL and gets Lighthouse category scores (Performance/Accessibility/SEO/Best Practices), Core Web Vitals (LCP, CLS, and a TBT value explicitly labeled as a lab INP proxy — never as field INP), the network waterfall, and TTFB / request count / total bytes / response sizes / status codes / slowest-request URL+time
  2. User can choose mobile (default) or desktop emulation, audits apply simulated throttling, and each page is measured against a cold cache
  3. User sets `--samples N` and the tool reports the per-metric median across N runs (not the mean), storing the raw distribution; Chrome and Lighthouse versions plus throttling config are stamped into run metadata
  4. The completed run is persisted to SQLite and written out as a flat one-row CSV and a full-fidelity JSON, and the raw Lighthouse JSON + HTML artifacts are saved per page
  5. The command is non-interactive and machine-readable (exit code + JSON output), runnable as an on-demand CLI

**Plans**: TBD
**UI hint**: yes

### Phase 3: Site-Wide Crawler

**Goal**: Running `perfcrawl crawl <url>` discovers every in-scope page of a site and feeds each discovered URL through the proven Phase 2 measurement path, terminating safely without hammering or trapping itself.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: CRAWL-01, CRAWL-02, CRAWL-03, CRAWL-04, CRAWL-05
**Success Criteria** (what must be TRUE):

  1. User starts a crawl from a seed URL and the tool auto-discovers pages by following same-origin internal links, additionally seeded/augmented from sitemap.xml including nested sitemap indexes
  2. The crawler respects robots.txt by default with an explicit override for owned sites, and honors per-host politeness (concurrency cap + minimum inter-request delay + backoff on 429/503)
  3. User can cap a crawl with max-depth and max-pages limits and the crawl provably terminates on a site with a calendar or faceted navigation
  4. User can include/exclude URLs by pattern and query-string explosion is bounded via URL canonicalization so near-duplicate URLs do not pollute results
  5. Non-2xx/3xx responses are tagged as errors and excluded from metrics rather than recorded as performance data

**Plans**: TBD

### Phase 4: Authenticated Crawling

**Goal**: The crawler can log in once and audit pages behind authentication (dashboards) while being structurally incapable of logging itself out or mutating the target site's data.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04
**Success Criteria** (what must be TRUE):

  1. User supplies credentials, the crawler logs in once (handling CSRF/cookie round-trip via the headless browser), captures the session, and reuses it across all page audits — Lighthouse inherits the authenticated session without resetting storage
  2. The crawler follows only safe GET links and enforces a denylist of destructive/session-ending patterns (logout, delete, admin, destroy, archive) checked before every fetch, so a crawl on an owned site cannot mutate state
  3. The crawler periodically checks session liveness and, on detecting session loss/expiry mid-crawl, reports it and aborts rather than silently capturing logged-out pages
  4. Credentials are supplied via env/gitignored secrets and never written to any committed file, log, or output artifact

**Plans**: TBD

### Phase 5: AI Analysis

**Goal**: Every audited page gains an AI-generated Observation / Potential Cause / Suggested Optimization that cites the page's own captured metrics, automating the slowest part of the manual workflow.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: AI-01, AI-02, AI-03
**Success Criteria** (what must be TRUE):

  1. With AI enabled, each page gets an Observation grounded only in that page's captured metrics — generic advice that does not reference a specific measured value is rejected
  2. Each page gets a Potential Cause and a Suggested Optimization, each tied to the captured evidence (scores, slowest request, byte/request counts, failing Lighthouse audits)
  3. The AI provider sits behind a swappable interface (Anthropic default) returning structured, schema-validated output, and the prompt sends distilled metrics only — never full Lighthouse JSON
  4. An estimated token/cost figure is shown before a large run so the user can opt in knowingly

**Plans**: TBD

### Phase 6: Output Suite & Regression Flagging

**Goal**: A run can emit any selected combination of outputs (Google Sheets rich schema, raw Lighthouse artifacts, CSV/JSON) and surface trustworthy run-over-run regressions and improvements built on the median-of-N data from Phase 2.
**Mode:** mvp
**Depends on**: Phase 5
**Requirements**: OUT-01, OUT-02, HIST-02
**Success Criteria** (what must be TRUE):

  1. User selects which output formats a run produces (e.g. `--output sheets,json,lighthouse`) and only those are written
  2. Results are written to Google Sheets using a rich schema (existing columns + CWV + regression deltas + AI columns), appended per run to a tool-owned tab mapped by header name — never clobbering the historical manual baseline — using batched writes with backoff on 429
  3. The current run is compared against the previous run per page per metric, and regressions/improvements are flagged only when the change exceeds the metric's known noise band — two runs of an unchanged site produce no false flags

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Data Model & Persistence Foundation | 0/3 | Not started | - |
| 2. Single-Page Measurement Slice | 0/TBD | Not started | - |
| 3. Site-Wide Crawler | 0/TBD | Not started | - |
| 4. Authenticated Crawling | 0/TBD | Not started | - |
| 5. AI Analysis | 0/TBD | Not started | - |
| 6. Output Suite & Regression Flagging | 0/TBD | Not started | - |

## Deferred to v2

These requirements are tracked in REQUIREMENTS.md but intentionally excluded from the v1 roadmap:

- **BACK-01, BACK-02, BACK-03** — Backend internals for owned Django sites. Requires a dedicated, security-gated access-mechanism research spike (django-silk vs exposed metrics endpoint vs hybrid; must be production-safe with `DEBUG=False`) before it can be planned. The generic any-site pipeline must be fully proven first; the BackendCollector seam keeps this a clean structural add-on.
- **OUT-05** — Self-contained multi-page HTML summary report (deferred from v1; the v1 output suite covers Sheets + CSV/JSON + raw Lighthouse artifacts).
- **BUDG-01** — Performance budgets/thresholds with pass-fail verdicts and CLI exit codes.
- **AI-04** — Incremental AI re-analysis (only re-run AI on pages whose metrics changed), a cost-control optimization that depends on regression diffing existing.
- **RUN-05** — Warm-cache / repeat-view audits.
