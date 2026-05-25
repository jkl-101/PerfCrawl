# Feature Research

**Domain:** Website performance auditing & crawling tool (CLI-first, multi-page, AI-assisted)
**Researched:** 2026-05-25
**Confidence:** HIGH (grounded in current documentation/behavior of Lighthouse, WebPageTest, GTmetrix, Screaming Frog, Sitebulb, SpeedCurve/Calibre, PageSpeed Insights, Unlighthouse, django-silk; CWV thresholds verified against web.dev)

## Orientation: What category is PerfCrawl in?

The named tools split into four archetypes. PerfCrawl is a **fusion** of three of them, which is the core insight for feature scoping:

| Archetype | Tools | What they do | PerfCrawl borrows |
|-----------|-------|--------------|-------------------|
| Single-page lab auditors | Lighthouse, PageSpeed Insights, GTmetrix, WebPageTest | Deep metrics for ONE URL at a time | The per-page metric depth |
| Site-wide crawlers | Screaming Frog, Sitebulb, **Unlighthouse** | Discover all pages, run lighter checks across the whole site | The crawl/discovery engine |
| Trend/regression monitors | SpeedCurve, Calibre | Track metrics over time, alert on regressions | The historical comparison |
| Continuous uptime/RUM monitors | Site24x7 | Always-on synthetic + real-user monitoring | **Nothing** — explicitly out of scope |

The closest single existing tool is **Unlighthouse** (open-source CLI that crawls a site via sitemap + internal links and runs Lighthouse on every page in parallel). PerfCrawl's differentiation lives in the three things Unlighthouse does NOT do: **AI Observation/Cause/Optimization per page**, **backend internals for owned Django sites**, and **persistent run-over-run regression tracking with a Google Sheets workflow**.

## Feature Landscape

### Table Stakes (Users Expect These)

Missing any of these makes the tool feel like a toy next to Screaming Frog / Lighthouse.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Crawl from a seed URL by following internal links | Core "point it at a site" promise; every crawler does this | MEDIUM | Need a frontier queue, visited-set, same-origin scoping, normalization (strip fragments, dedupe trailing slash). Don't follow off-domain links. |
| Sitemap.xml discovery | Screaming Frog, Sitebulb, Unlighthouse all seed/augment crawls from sitemaps; faster + more complete than link-following alone | LOW–MEDIUM | Fetch `/sitemap.xml`, follow sitemap-index nesting, also check `robots.txt` `Sitemap:` directives. Treat as a seed source, union with link discovery. |
| robots.txt awareness | Polite-crawling default; SEO tools obey by default. Owners expect a way to ignore it for their own sites | LOW | Default = respect; provide `--ignore-robots` for owned sites. Parse `Disallow` + crawl-delay + `User-agent`. |
| Depth limit + max-pages limit | Universal crawl-control knob (Screaming Frog "Limits"); prevents runaway crawls | LOW | `--max-depth`, `--max-pages`. Essential safety valve before first real crawl. |
| Include / exclude URL patterns | Screaming Frog include/exclude is a headline feature; needed to skip `/admin`, logout links, infinite filters, query-string explosions | LOW–MEDIUM | Regex or glob against URL path. Also limit query-string param count (Screaming Frog does this) to avoid faceted-nav explosions. |
| Authenticated crawling (form + cookie/session) | PROJECT requires it; highest-value pages (dashboards) sit behind login. Playwright `storage_state` is the standard mechanism | MEDIUM–HIGH | Log in once, persist `storage_state` (cookies + localStorage), reuse across all page audits. Also support HTTP basic/digest. Must keep credentials out of any committed file. |
| Core Web Vitals: LCP, CLS, INP | The 2024+ official CWV set (INP replaced FID in March 2024). Any perf tool without these is dated | LOW (collected) / MEDIUM (correct) | INP is interaction-driven and only fully meaningful as field data; lab tools report a **lab/synthetic INP proxy** (often TBT as a stand-in). Report lab values and label them as lab, not field. |
| Lighthouse category scores (Performance / Accessibility / SEO / Best Practices) | The de-facto scorecard users already paste into spreadsheets; PROJECT lists all four | LOW (if reusing Lighthouse) | Strongly favors driving Lighthouse itself rather than reimplementing. PWA category is deprecated/removed — don't bother. |
| Network waterfall capture (per-request) | WebPageTest/GTmetrix/Chrome Network-tab staple; the manual workflow PerfCrawl replaces reads this today | MEDIUM | From Lighthouse `network-requests` artifact or Playwright network events: URL, type, size, timing, status. |
| TTFB, request count, total bytes, response sizes, status codes, slowest request URL + time | These are literally the existing Google Sheet columns PerfCrawl must reproduce | LOW–MEDIUM | All derivable from the network/waterfall data + navigation timing. Slowest-request = max(request duration). |
| Mobile vs desktop emulation | Lighthouse defaults to mobile (mid-tier phone, 4G); every tool exposes a device toggle | LOW | Lighthouse `--preset=desktop` / form-factor config. Default to mobile to match Lighthouse/PSI norms; allow `--desktop`. |
| Network throttling control | WebPageTest/GTmetrix/Lighthouse all throttle; "true to life mobile connection" is expected | LOW–MEDIUM | Lighthouse simulated throttling is the cheap default. Note: WebPageTest's packet-level throttling is more accurate but far heavier — out of scope. |
| Cold vs warm cache control | WebPageTest "first view vs repeat view"; the existing sheet has a "Cache Disabled" column | LOW | Default cold (fresh context per page). Warm-cache repeat-view is a nice-to-have, not v1-critical. |
| Multiple runs + median selection | WebPageTest gospel: run odd N, take median, because single runs are noisy. Without this, regression flags will be false alarms | MEDIUM | `--samples N` (default 3). Take median of the primary metric (or per-metric median). Critical prerequisite for trustworthy regression detection. |
| Per-page opportunities / diagnostics | Lighthouse "Opportunities + Diagnostics" are the actionable core; GTmetrix Structure Score is literally a repackaging of them | LOW (if reusing Lighthouse) | Capture Lighthouse audit details (unused JS, image sizing, render-blocking, etc.) — these feed the AI analysis as raw material. |
| Raw Lighthouse artifact output | PROJECT lists it; lets users open the full HTML report; preserves auditability | LOW | Save Lighthouse JSON + HTML per page. Cheap and high-trust. |
| CSV / JSON local output | Screaming Frog exports CSV; data engineers expect machine-readable output | LOW | Flat, one-row-per-page CSV mirroring the sheet schema; nested JSON for full fidelity. |
| Per-run persistence + run-over-run comparison | SpeedCurve/Calibre/GTmetrix-history/Sitebulb-audit-comparison all store and diff runs; PROJECT requires regression flagging | MEDIUM | Persist each run (SQLite or JSON-on-disk keyed by run id + timestamp). Compare current vs previous per URL per metric. |
| Regression / improvement flagging vs prior run | The whole point of historical tracking; SpeedCurve's core feature | MEDIUM | Diff metric deltas; flag when a metric crosses a threshold OR worsens beyond a noise band. Requires median runs to be meaningful. |
| Google Sheets output (richer schema) | Hard PROJECT constraint — must integrate with the team's existing sheet workflow | MEDIUM | gspread/Sheets API. New schema = old columns + CWV + regression deltas + AI columns. Append-per-run for time series (Screaming Frog does append-to-sheet for scheduled crawls). |
| HTML report output | PROJECT lists it; expected self-contained shareable artifact | MEDIUM | Static HTML summarizing all pages + scores + AI notes + deltas. Can be a simple template render. |

### Differentiators (Competitive Advantage)

These are where PerfCrawl earns its existence. They map directly to PROJECT's Core Value (automate the slow manual audit) and Key Decisions.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **AI Observation / Potential Cause / Suggested Optimization per page** | Automates the slowest, most human part of today's workflow. Lighthouse/GTmetrix give generic "resize this image"; an LLM fed THIS page's metrics + waterfall + (for owned sites) SQL profile gives stack-specific, prioritized prose. This is the headline differentiator | MEDIUM–HIGH | Feed the LLM structured per-page evidence (scores, failing audits, slowest requests, byte/request counts, backend SQL summary). Constrain to the 3 fixed fields. Risk: hallucinated/generic advice — mitigate with strict grounding ("only reference metrics provided") + cite the evidence row. Emerging trend: DebugBear and others now do LLM-on-Lighthouse-results, so this is validated but still rare. |
| **Backend internals for owned Django sites (SQL counts, slow/duplicate queries, cache usage, request timing)** | External tools (Lighthouse, WPT, GTmetrix) physically cannot see another site's DB — they're black-box. Correlating frontend TTFB with backend SQL on the SAME run is something no external tool does. Directly replaces Django Debug Toolbar manual reading | HIGH | Mechanism is a separate research item (Debug Toolbar parsing vs django-silk vs exposed metrics endpoint). django-silk is the strongest candidate: it profiles API + HTML endpoints, intercepts SQL (counts, joins, time), has a queryable store — unlike Debug Toolbar which needs an HTML body and can't profile JSON APIs. Owned-sites only; never a hard requirement for a run. N+1/duplicate-query detection is high-value but neither silk nor DDT detects N+1 natively — would need custom logic over silk's captured queries. |
| **Run-over-run regression tracking built into an on-demand CLI (no SaaS, no continuous monitoring)** | SpeedCurve/Calibre deliver regression tracking only as paid always-on SaaS. PerfCrawl gives the regression-detection value in a self-hosted, on-demand, no-subscription CLI tuned to the team's own sheet | MEDIUM | Store runs locally; compute deltas + threshold crossings. Differentiation is the *packaging* (CLI + own data) not the algorithm. |
| **Site-wide depth on EVERY page (not 1 URL at a time)** | Lighthouse/PSI/GTmetrix/WPT are single-URL. Sitebulb/Unlighthouse close this gap but Sitebulb samples (~10% of pages for Web Vitals) and is GUI/SEO-oriented. PerfCrawl can run full Lighthouse on every discovered page | MEDIUM | This is table-stakes-for-the-category but a differentiator vs the single-page incumbents the team uses today. Cost: time — full Lighthouse × N pages × M samples is slow; parallelism + page caps matter. |
| **Performance budgets / thresholds with pass-fail** | SpeedCurve/Lighthouse-CI/Calibre headline feature; lets the tool say "this page BLEW the budget" not just "here's a number" | LOW–MEDIUM | Config file of metric → threshold (e.g. LCP ≤ 2.5s, total bytes ≤ X, SQL queries ≤ Y). Reuse CWV good/needs-improvement/poor bands (LCP 2.5/4s, INP 200/500ms, CLS 0.1/0.25) as sensible defaults. Pairs naturally with regression flags. |
| **User-selectable multi-format output per run** | Most tools lock you to one output (GTmetrix=web UI, Screaming Frog=CSV). PerfCrawl lets one run target Sheets + HTML + JSON + raw Lighthouse simultaneously or selectively | LOW–MEDIUM | `--output sheets,html,json,lighthouse`. Pluggable writer interface; each reads from one canonical in-memory result model. |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Always-on / real-time monitoring dashboard (Site24x7 / SpeedCurve-style) | "Wouldn't it be great to watch perf live?" | Explicitly out of scope in PROJECT. Requires hosting, schedulers, alert infra, uptime — a different product class. Massive scope inflation | On-demand CLI runs + historical comparison. Add scheduling later only once core is stable. |
| Real-user monitoring (RUM) / field data via CrUX | "Lab data isn't real users" — and it's true (only field data is the official CWV verdict) | Requires JS beacon injection into the target site, or CrUX API which only has data for high-traffic URLs (~1k loads/28d) and Google is deprecating CrUX-in-PSI. studyhalo pages likely lack CrUX coverage | Be explicit that PerfCrawl is a **lab/synthetic** tool. Optionally pull CrUX API as a labeled "field reference" later — never as the primary metric. |
| Chrome extension / browser-automation via paid Claude plan | Was the original idea | Already rejected in PROJECT (paid-only, undependable foundation) | Headless browser automation (Playwright) the tool fully controls. |
| Applying performance fixes to the target site | "It found the problem, why not fix it?" | PROJECT out-of-scope. Auto-editing a live site is dangerous and far beyond an audit tool's remit | Audit + recommend only. The AI Suggested-Optimization field is the deliverable. |
| Backend internals for sites the team does NOT own | "Can we get DB stats for any site?" | Physically impossible externally; PROJECT out-of-scope | External-only metrics for unowned sites; backend internals are an owned-site add-on. |
| Packet-level network throttling (WebPageTest-grade) | "More accurate throttling" | Requires a controlled network namespace / dedicated agents; huge infra for marginal gain over Lighthouse simulated throttling | Lighthouse simulated/DevTools throttling is good enough for relative comparison and regression detection. |
| Multi-location / multi-region testing (GTmetrix/WPT) | "Test from Sydney vs London" | Needs distributed runners or third-party infra; not relevant to a single-team owned-site audit | Single local runner. Document the test location as fixed/local. |
| Reimplementing Lighthouse metrics from scratch | "Stay pure-Python / avoid Node" | Lighthouse is the trusted standard; reimplementing = years of work + loss of credibility | Drive the real Lighthouse (subprocess CLI or Node lib). Accept the Node dependency for the audit engine even in a Python tool — this is the central STACK tension flagged in PROJECT. |
| CI / scheduled automation in v1 | "Run it nightly in CI" | PROJECT defers automation; CLI-first. Building CI hooks before the core is stable wastes effort | Design the CLI to be CI-friendly (exit codes on budget failure, JSON output, non-interactive auth) so automation is trivial to bolt on later — but don't build the scheduler/CI integration now. |

## Feature Dependencies

```
[Crawl + discovery (links + sitemap + robots)]
    └──requires──> [URL normalization + frontier/visited queue]

[Authenticated crawling]
    └──requires──> [Headless browser w/ persistent storage_state]
                       └──enables──> [Auditing pages behind login]

[Per-page metric collection (CWV, Lighthouse scores, waterfall, TTFB...)]
    └──requires──> [Headless browser / Lighthouse runner]
    └──requires──> [Crawl + discovery]  (need URLs first)

[Multiple runs + median]
    └──requires──> [Per-page metric collection]
    └──enables───> [Trustworthy regression detection]   (CRITICAL: noise kills regression without this)

[Regression / improvement flagging]
    └──requires──> [Per-run persistence]
    └──requires──> [Multiple runs + median]
    └──enhanced-by──> [Budgets / thresholds]

[AI Observation/Cause/Optimization]
    └──requires──> [Per-page metric collection]   (evidence to ground on)
    └──enhanced-by──> [Backend internals]          (richest for owned Django sites)
    └──enhanced-by──> [Waterfall + failing Lighthouse audits]

[Backend internals (SQL/cache/timing)]
    └──requires──> [Owned-site access mechanism (silk endpoint / DDT / metrics API)]
    └──independent of──> [frontend crawl]  (correlated by URL+timestamp, runs in parallel)

[Output writers (Sheets / HTML / JSON / Lighthouse artifacts)]
    └──requires──> [Canonical result model]
    └──enhanced-by──> [AI analysis]  (fills Observation/Cause/Optimization columns)
    └──enhanced-by──> [Regression flags]  (fills delta columns)

[AI analysis] ──conflicts/tension──> [run speed & cost]   (1 LLM call per page × N pages)
```

### Dependency Notes

- **Multiple runs + median is a hard prerequisite for regression detection.** Single-run metrics vary 10–15% run-to-run (the same reason Lighthouse and PSI scores differ). Flagging regressions off single runs produces constant false positives. This is the single most important ordering constraint: ship median-of-N before, or together with, regression flagging.
- **Crawl/discovery must precede metric collection** — there are no pages to audit until discovery produces a URL set. Build discovery + limits + include/exclude first as a standalone, inspectable step (let the user see the discovered URL list before auditing).
- **Authenticated crawling depends on the headless browser layer**, which is also what runs Lighthouse — so the browser/Lighthouse runner is foundational and shared. Establish `storage_state` once; reuse for both crawl link-discovery behind auth and per-page audits.
- **Backend internals run in parallel with the frontend crawl, joined by URL + timestamp.** They don't block frontend metrics and aren't required for a run — keep them decoupled so unowned-site runs simply skip them.
- **AI analysis depends on metric collection for grounding** and is *dramatically* better when fed backend internals. Order it after metrics + (for owned sites) backend internals exist, so the LLM has real evidence rather than just scores.
- **Output writers depend on a canonical result model.** Define that model early (it's also the persistence schema and the Sheets schema) so every writer and the regression engine read the same shape.
- **AI analysis conflicts with run speed/cost.** One LLM call per page × N pages × possibly per-run adds latency and token cost. Mitigate: make AI opt-in/cacheable, batch, or only re-analyze pages whose metrics changed since last run.

## MVP Definition

### Launch With (v1)

The minimum that beats the current manual workflow on a single Django site (studyhalo).

- [ ] **Crawl from seed URL via internal links + sitemap, with robots.txt respect, depth/max-page limits, include/exclude patterns** — the "point it at a site" core; nothing works without URLs.
- [ ] **Authenticated crawling (form login → persisted session)** — PROJECT-mandated; the valuable dashboard pages need it.
- [ ] **Per-page metric collection: Lighthouse scores (Perf/SEO/A11y/Best Practices), CWV (LCP/CLS/lab-INP), waterfall, TTFB, request count, total bytes, slowest request, response sizes, status codes** — reproduces every existing sheet column + CWV.
- [ ] **Mobile (default) + desktop emulation, simulated throttling, cold cache, `--samples N` with median** — the credibility floor; median is non-negotiable for later regression trust.
- [ ] **AI Observation / Potential Cause / Suggested Optimization per page** — the headline differentiator and biggest manual-time saver.
- [ ] **Per-run persistence + regression/improvement flags vs prior run** — PROJECT-required trend visibility.
- [ ] **Output: Google Sheets (rich schema) + raw Lighthouse artifacts + CSV/JSON, user-selectable** — Sheets integration is a hard constraint; raw artifacts + CSV/JSON are cheap.
- [ ] **Runs as an on-demand CLI** — PROJECT-mandated form factor.

### Add After Validation (v1.x)

- [ ] **Backend internals for owned Django sites (SQL counts, slow/duplicate queries, cache, timing) feeding the AI analysis** — highest-value differentiator but highest-complexity + needs its own access-mechanism research; ship once frontend core is proven. Trigger: frontend audit validated on studyhalo and team wants the backend correlation.
- [ ] **Performance budgets / thresholds with pass-fail + CLI exit codes** — turns numbers into verdicts. Trigger: enough runs to know sane budget values.
- [ ] **HTML report output** — shareable artifact. Trigger: stakeholders want a non-Sheets summary.
- [ ] **N+1 / duplicate-query detection over captured backend queries** — depends on backend internals existing first.
- [ ] **Warm-cache / repeat-view runs** — Trigger: caching-strategy questions arise.
- [ ] **Incremental AI re-analysis (only re-run AI on pages whose metrics changed)** — cost/latency optimization. Trigger: AI token cost or run time becomes a pain point.

### Future Consideration (v2+)

- [ ] **Scheduled / CI automation (nightly runs, CI gate on budget failure)** — PROJECT explicitly defers; only after core is stable. Design CLI to make this trivial later.
- [ ] **CrUX field-data overlay (labeled as reference)** — only if owned URLs gain enough traffic for CrUX coverage; never the primary metric.
- [ ] **Multi-tool metric source abstraction (e.g. WebPageTest backend)** — only if Lighthouse-only proves insufficient.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Crawl + discovery (links/sitemap/robots/limits/include-exclude) | HIGH | MEDIUM | P1 |
| Authenticated crawling | HIGH | HIGH | P1 |
| Per-page metric collection (Lighthouse + CWV + waterfall + sheet columns) | HIGH | MEDIUM | P1 |
| Mobile/desktop + throttling + cold cache + samples/median | HIGH | MEDIUM | P1 |
| AI Observation/Cause/Optimization | HIGH | MEDIUM-HIGH | P1 |
| Per-run persistence + regression flags | HIGH | MEDIUM | P1 |
| Google Sheets output (rich schema) | HIGH | MEDIUM | P1 |
| Raw Lighthouse + CSV/JSON output | MEDIUM | LOW | P1 |
| Backend internals (owned Django) | HIGH | HIGH | P2 |
| Performance budgets / thresholds | MEDIUM | LOW-MEDIUM | P2 |
| HTML report output | MEDIUM | MEDIUM | P2 |
| N+1 / duplicate-query detection | MEDIUM | MEDIUM | P2 |
| Warm-cache / repeat-view | LOW | LOW | P3 |
| Incremental AI re-analysis | MEDIUM | MEDIUM | P3 |
| Scheduled / CI automation | MEDIUM | MEDIUM | P3 |
| CrUX field overlay | LOW | MEDIUM | P3 |
| Multi-region / packet-level throttling | LOW | HIGH | P3 (likely never) |

**Priority key:** P1 = must have for launch · P2 = should have, add when possible · P3 = nice to have / future.

## Competitor Feature Analysis

| Feature | Lighthouse / PSI | Screaming Frog / Sitebulb | Unlighthouse | SpeedCurve / Calibre | WebPageTest / GTmetrix | Our Approach |
|---------|------------------|---------------------------|--------------|----------------------|------------------------|--------------|
| Site-wide crawl + discovery | No (1 URL) | Yes (links + sitemap, include/exclude, depth, auth) | Yes (sitemap + links) | Configured URL list | No (1 URL) | Yes — links + sitemap + robots + limits + include/exclude + auth |
| Authenticated pages | Manual (Puppeteer hack) | Yes (form + basic/digest) | Limited | Yes | Limited | Yes — Playwright storage_state, persisted once |
| Full Lighthouse on every page | No | Sitebulb: yes (samples Web Vitals ~10%) | Yes (parallel) | Per monitored URL | No | Yes — full Lighthouse per discovered page |
| CWV (LCP/INP/CLS) | Yes (lab; PSI adds field) | Sitebulb: lab sample | Yes (lab) | Yes (lab + RUM) | Yes (lab) | Yes (lab, clearly labeled; INP as lab proxy) |
| Multiple runs / median | CLI: manual | Limited | `--samples` | Yes | Yes (odd N → median) | Yes — `--samples N`, per-metric median |
| Opportunities / diagnostics | Yes | Sitebulb: yes (Lighthouse ruleset) | Yes | Yes | GTmetrix: Structure Score | Yes — captured + fed to AI |
| AI Observation/Cause/Optimization | No (generic audit text) | No | No | No | No (DebugBear emerging) | **Yes — grounded LLM, 3 fixed fields** |
| Backend internals (SQL/cache) | No (impossible externally) | No | No | No | No | **Yes — owned Django sites only** |
| Historical tracking / regression | No | Sitebulb: audit comparison | No (ephemeral) | Yes (core feature, SaaS) | History tab (per URL) | **Yes — local persistence, on-demand CLI** |
| Budgets / thresholds | Lighthouse-CI: yes | No | Via config | Yes (core) | No | Yes (P2; CWV bands as defaults) |
| Google Sheets output | No | Screaming Frog: yes (append) | No | No | No | **Yes — rich schema, append per run** |
| Multi-format selectable output | HTML/JSON | CSV/Excel/Sheets | Web UI | SaaS UI | Web UI | Yes — Sheets/HTML/JSON/Lighthouse, selectable |
| Always-on monitoring | No | No | No | Yes | WPT: no / GTmetrix: scheduled | No (deliberate anti-feature) |

## Key risks & cross-cutting notes for downstream (requirements/roadmap)

- **The Node/Python tension is real and feature-shaping.** Lighthouse is Node; the team is Python/Django. Reimplementing Lighthouse is an anti-feature. Expect to drive Lighthouse via subprocess CLI or the Node lib regardless of the host language — flag for STACK research. Unlighthouse proves the crawl-every-page-with-Lighthouse pattern works; consider whether to wrap/learn-from it vs build fresh.
- **Lab INP is a known sharp edge.** INP is fundamentally a field metric; lab tools approximate it (often via TBT). Collect and report it, but label it lab/synthetic to avoid implying it's the official CWV verdict.
- **Median-of-N gates regression credibility.** Sequence/scope so regression flagging never ships on single-run data.
- **AI grounding + cost are the two AI risks.** Constrain the LLM to provided evidence (cite the metric row) to avoid generic/hallucinated advice; make AI opt-in/cacheable/incremental to control token cost and run time.
- **Backend-internals access mechanism is unresolved** (django-silk vs Debug Toolbar vs exposed endpoint). Current evidence favors **django-silk** (profiles API + HTML endpoints, captures SQL counts/joins/time, has a queryable store; Debug Toolbar can't profile JSON APIs and needs an HTML body). Neither detects N+1 natively — that's custom logic. Defer to a dedicated research item; keep it owned-sites-only and fully decoupled from the frontend path.

## Sources

- Screaming Frog — [SEO Spider Configuration](https://www.screamingfrog.co.uk/seo-spider/user-guide/configuration/), [General](https://www.screamingfrog.co.uk/seo-spider/user-guide/general/), [List/scan modes](https://screamingfrog.club/en/list-scan-mode/) (crawl modes, robots, sitemap, include/exclude, depth, auth, JS rendering, Sheets export)
- WebPageTest — [Getting Started docs](https://docs.webpagetest.org/getting-started/), [DebugBear WPT guide](https://www.debugbear.com/software/webpagetest), [packet vs DevTools throttling](https://blog.webpagetest.org/posts/full-throttle-comparing-packet-level-and-dev-tools-throttling/) (throttling, cold cache, multiple runs/median, filmstrip, TTFB)
- Google Lighthouse — [GitHub](https://github.com/GoogleChrome/lighthouse), [Complete Guide](https://agencyanalytics.com/blog/google-lighthouse-guide), [scoring](https://graphite.com/guides/lighthouse-scoring) (categories, opportunities/diagnostics, mobile default emulation)
- Lighthouse CI — [Configuration docs](https://googlechrome.github.io/lighthouse-ci/docs/configuration.html), [config.md](https://github.com/GoogleChrome/lighthouse-ci/blob/main/docs/configuration.md) (assertions, budgets.json, presets)
- Unlighthouse — [Home](https://unlighthouse.dev/), [CLI](https://unlighthouse.dev/integrations/cli), [bulk testing/--samples](https://unlighthouse.dev/learn-lighthouse/bulk-lighthouse-testing) (site-wide crawl + parallel Lighthouse)
- GTmetrix — [Features](https://gtmetrix.com/features.html), [report guide](https://gtmetrix.com/blog/everything-you-need-to-know-about-the-new-gtmetrix-report-powered-by-lighthouse/), [DebugBear review](https://www.debugbear.com/software/gtmetrix-speed-test) (waterfall, Structure Score, locations, throttling, video, history)
- Sitebulb — [Performance product](https://sitebulb.com/product/performance/), [Performance report docs](https://support.sitebulb.com/en/articles/9857360-performance-report), [Accessibility (axe)](https://sitebulb.com/product/accessibility/), [vs Screaming Frog](https://searchatlas.com/blog/sitebulb-vs-screaming-frog/) (Lighthouse ruleset per URL, Web Vitals sampling, audit comparison)
- SpeedCurve / Calibre — [Performance budgets & alerts](https://support.speedcurve.com/docs/performance-budgets-and-alerts), [Regressions](https://www.speedcurve.com/features/regressions/), [continuous monitoring](https://www.speedcurve.com/web-performance-guide/continuous-performance-monitoring/) (budgets, regression detection, trend charts, alerting; Calibre acquisition)
- PageSpeed Insights / lab vs field — [Unlighthouse PSI vs Lighthouse](https://unlighthouse.dev/learn-lighthouse/pagespeed-insights-vs-lighthouse), [DebugBear lab vs field](https://www.debugbear.com/blog/lighthouse-lab-data-not-matching-field-data), [Google PSI about](https://developers.google.com/speed/docs/insights/v5/about) (lab vs CrUX field, score gap, CrUX deprecation in PSI)
- Core Web Vitals — [web.dev thresholds](https://web.dev/articles/defining-core-web-vitals-thresholds), [corewebvitals.io](https://www.corewebvitals.io/core-web-vitals) (INP replaced FID Mar 2024; LCP 2.5/4s, INP 200/500ms, CLS 0.1/0.25; P75 field-based official verdict)
- django-silk vs django-debug-toolbar — [django-silk GitHub](https://github.com/jazzband/django-silk), [Scout APM profilers](https://www.scoutapm.com/blog/python-profilers/), [Open Zaak profiling](https://open-zaak.readthedocs.io/en/stable/development/performance/profiling.html) (silk profiles APIs + captures SQL; DDT can't profile JSON endpoints; neither detects N+1 natively)
- Playwright auth — [Playwright Python auth docs](https://playwright.dev/python/docs/auth) (storage_state for cookies/localStorage; persist + reuse session)
- AI-on-Lighthouse trend — [DebugBear performance insights](https://www.debugbear.com/blog/performance-insights-devtools-lighthouse) (emerging LLM-analyzes-audit-results pattern; still rare)

---
*Feature research for: website performance auditing & crawling tool (CLI-first, AI-assisted, owned-site backend profiling)*
*Researched: 2026-05-25*
