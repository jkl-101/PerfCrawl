# Requirements: PerfCrawl

**Defined:** 2026-05-25
**Core Value:** Replace the slow manual per-page performance audit with one command that crawls a site, gathers consistent statistics, and produces actionable analysis.

## v1 Requirements

Requirements for the initial release — a full frontend audit (crawl + metrics + AI + regression + outputs) proven on studyhalo.com, generalizable to any site. Each maps to a roadmap phase.

### Crawl & Discovery

- [ ] **CRAWL-01**: User can start a crawl from a seed URL and the tool auto-discovers pages by following same-origin internal links
- [ ] **CRAWL-02**: User can have discovery seeded/augmented from `sitemap.xml`, including nested sitemap indexes
- [ ] **CRAWL-03**: Crawler respects `robots.txt` by default, with an explicit override for owned sites
- [ ] **CRAWL-04**: User can cap a crawl with max-depth and max-pages limits
- [ ] **CRAWL-05**: User can include/exclude URLs by pattern and bound query-string explosion to avoid crawler traps

### Authentication

- [ ] **AUTH-01**: User can supply credentials so the crawler logs in once and reuses the authenticated session across all page audits
- [ ] **AUTH-02**: Crawler follows only safe GET links and honors a denylist (e.g. logout, delete) so it never mutates target-site state
- [ ] **AUTH-03**: Crawler detects session loss/expiry mid-crawl and reports it instead of silently capturing logged-out pages
- [ ] **AUTH-04**: Credentials are supplied without being written to any committed file

### Frontend Metrics

- [ ] **METRIC-01**: Tool captures Lighthouse category scores per page (Performance, Accessibility, SEO, Best Practices)
- [ ] **METRIC-02**: Tool captures Core Web Vitals per page — LCP, CLS, and a clearly-labeled lab INP proxy
- [ ] **METRIC-03**: Tool captures the per-page network waterfall (per request: URL, type, size, timing, status)
- [ ] **METRIC-04**: Tool captures per page: TTFB, total request count, total bytes transferred, response sizes, status codes, and the slowest request URL + time
- [ ] **METRIC-05**: Tool captures Lighthouse opportunities/diagnostics per page as raw material for analysis

### Run Conditions

- [ ] **RUN-01**: User can run audits with mobile (default) or desktop emulation
- [ ] **RUN-02**: Audits apply simulated network/CPU throttling
- [ ] **RUN-03**: Audits run against a cold cache (fresh browser context per page)
- [ ] **RUN-04**: User can set sample count N; the tool runs N times per page and reports the per-metric median

### AI Analysis

- [ ] **AI-01**: Tool generates a per-page Observation grounded only in that page's captured metrics
- [ ] **AI-02**: Tool generates a per-page Potential Cause grounded in the captured evidence
- [ ] **AI-03**: Tool generates a per-page Suggested Optimization grounded in the captured evidence

### Persistence & Regression

- [ ] **HIST-01**: Tool persists every run (run id, timestamp, per-page results) to a local store
- [ ] **HIST-02**: Tool compares the current run against the previous run per page per metric and flags regressions/improvements beyond a noise band

### Output

- [ ] **OUT-01**: User can select which output formats a run produces
- [ ] **OUT-02**: Tool writes results to Google Sheets using a rich schema (existing columns + CWV + regression deltas + AI columns), appending per run
- [ ] **OUT-03**: Tool saves raw Lighthouse artifacts (JSON + HTML) per page
- [ ] **OUT-04**: Tool writes a flat CSV (one row per page) and a full-fidelity JSON

### CLI

- [ ] **CLI-01**: Tool runs as an on-demand, automation-friendly CLI command (non-interactive auth, machine-readable output)

## v2 Requirements

Deferred to a future release. Tracked, not in the current roadmap.

### Backend Metrics (owned sites)

- **BACK-01**: For owned Django sites, tool captures SQL query counts, slow/duplicate queries, cache usage, and request timing, correlated to each page by URL + timestamp
- **BACK-02**: Backend metrics feed the AI analysis to produce stack-specific causes/optimizations
- **BACK-03**: Tool detects N+1 / duplicate-query patterns over captured backend queries

> Requires a dedicated, security-gated access-mechanism spike (django-silk vs exposed metrics endpoint vs `Server-Timing`) before planning. Owned-sites-only; never a hard requirement for a run. See Key Decisions in PROJECT.md.

### Reporting & Verdicts

- **OUT-05**: Tool produces a self-contained HTML report summarizing all pages, scores, AI notes, and deltas
- **BUDG-01**: User can define performance budgets/thresholds; tool emits pass-fail verdicts and CLI exit codes

### Efficiency

- **AI-04**: Tool re-runs AI analysis only on pages whose metrics changed since the last run (incremental, cost control)
- **RUN-05**: User can run warm-cache / repeat-view audits

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Always-on / real-time monitoring dashboard | Different product class (hosting, schedulers, alerting); this is on-demand auditing with historical comparison |
| Real-user monitoring (RUM) / CrUX as primary metric | Needs JS beacon injection or high-traffic CrUX coverage studyhalo lacks; this is a lab/synthetic tool (CrUX overlay only as a labeled reference, later) |
| Claude Chrome extension / paid-plan browser automation | Paid-only, undependable foundation (per Slack findings); Playwright drives a browser we fully control |
| Applying performance fixes to the target site | Auditing tool only; auto-editing a live site is dangerous and out of remit |
| Backend internals for sites the team does not own | Physically impossible to read another site's DB/cache externally |
| Packet-level network throttling (WebPageTest-grade) | Requires controlled network namespaces/agents; marginal gain over simulated throttling |
| Multi-location / multi-region testing | Needs distributed runners; not relevant to a single-team owned-site audit |
| Reimplementing Lighthouse metrics from scratch | Lighthouse is the trusted standard; drive the real engine instead |
| Scheduled / CI automation (in v1) | Deferred; CLI is built to be CI-friendly so automation bolts on later |

## Traceability

Each v1 requirement maps to exactly one phase. See ROADMAP.md for phase detail.

| Requirement | Phase | Status |
|-------------|-------|--------|
| HIST-01 | Phase 1 — Data Model & Persistence Foundation | Pending |
| METRIC-01 | Phase 2 — Single-Page Measurement Slice | Pending |
| METRIC-02 | Phase 2 — Single-Page Measurement Slice | Pending |
| METRIC-03 | Phase 2 — Single-Page Measurement Slice | Pending |
| METRIC-04 | Phase 2 — Single-Page Measurement Slice | Pending |
| METRIC-05 | Phase 2 — Single-Page Measurement Slice | Pending |
| RUN-01 | Phase 2 — Single-Page Measurement Slice | Pending |
| RUN-02 | Phase 2 — Single-Page Measurement Slice | Pending |
| RUN-03 | Phase 2 — Single-Page Measurement Slice | Pending |
| RUN-04 | Phase 2 — Single-Page Measurement Slice | Pending |
| OUT-03 | Phase 2 — Single-Page Measurement Slice | Pending |
| OUT-04 | Phase 2 — Single-Page Measurement Slice | Pending |
| CLI-01 | Phase 2 — Single-Page Measurement Slice | Pending |
| CRAWL-01 | Phase 3 — Site-Wide Crawler | Pending |
| CRAWL-02 | Phase 3 — Site-Wide Crawler | Pending |
| CRAWL-03 | Phase 3 — Site-Wide Crawler | Pending |
| CRAWL-04 | Phase 3 — Site-Wide Crawler | Pending |
| CRAWL-05 | Phase 3 — Site-Wide Crawler | Pending |
| AUTH-01 | Phase 4 — Authenticated Crawling | Pending |
| AUTH-02 | Phase 4 — Authenticated Crawling | Pending |
| AUTH-03 | Phase 4 — Authenticated Crawling | Pending |
| AUTH-04 | Phase 4 — Authenticated Crawling | Pending |
| AI-01 | Phase 5 — AI Analysis | Pending |
| AI-02 | Phase 5 — AI Analysis | Pending |
| AI-03 | Phase 5 — AI Analysis | Pending |
| OUT-01 | Phase 6 — Output Suite & Regression Flagging | Pending |
| OUT-02 | Phase 6 — Output Suite & Regression Flagging | Pending |
| HIST-02 | Phase 6 — Output Suite & Regression Flagging | Pending |

**Coverage:**
- v1 requirements: 28 total (CRAWL 5 + AUTH 4 + METRIC 5 + RUN 4 + AI 3 + HIST 2 + OUT 4 + CLI 1)
- Mapped to phases: 28 ✓
- Unmapped: 0 ✓

**Per-phase counts:** Phase 1 = 1 · Phase 2 = 12 · Phase 3 = 5 · Phase 4 = 4 · Phase 5 = 3 · Phase 6 = 3 (= 28 unique requirements; each requirement appears exactly once, no duplicates)

---
*Requirements defined: 2026-05-25*
*Last updated: 2026-05-25 after roadmap creation (traceability populated, 28/28 v1 mapped)*
