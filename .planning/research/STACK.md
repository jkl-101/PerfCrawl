# Stack Research

**Domain:** Website performance auditing & crawling CLI (frontend metrics + Lighthouse + AI analysis + multi-output)
**Researched:** 2026-05-25
**Confidence:** HIGH on the language decision and measurement engine; HIGH on Python-side libraries; MEDIUM on the cross-process orchestration detail and backend-metrics mechanism.

---

## Headline Decision: Python orchestrator + thin Node "measurement worker"

**Recommendation: Build the tool in Python, and shell out to a small, dedicated Node script for the one thing that genuinely requires Node — running Lighthouse.** Do the crawling and browser automation in **Playwright for Python**, and run Lighthouse through a **single-purpose Node subprocess** (`chrome-launcher` + `lighthouse` npm) that the Python orchestrator launches per page and reads JSON back from.

This is a "polyglot but Python-primary" stack. Everything the team cares about long-term — Django backend metrics, the AI analysis pipeline, Google Sheets, persistence, regression logic, the CLI — lives in Python where the team is fluent. Node is quarantined to a ~100-line worker that wraps Lighthouse.

**Why not pure Node?** Lighthouse and Crawlee/Playwright are first-class in Node, so a pure-Node tool is technically the path of least resistance for the *measurement* layer. But it is the wrong long-term home: the Django backend-metrics integration, the AI analysis, the team's maintenance fluency, and future reuse all point at Python. Forcing the whole tool into Node to satisfy one dependency (Lighthouse) inverts the cost structure — you'd pay a TypeScript tax on every feature to save a subprocess boundary on one.

**Why not pure Python (PSI API only, no local Lighthouse)?** The PageSpeed Insights API removes the Node dependency entirely, but it **cannot crawl behind authentication** (it only fetches publicly reachable URLs from Google's servers) and it is rate-limited. Authenticated crawling of dashboards is an explicit, high-value requirement, so PSI cannot be the primary engine. It is a useful *supplementary* source for public pages (it adds real-user CrUX field data), not the core.

**Why the subprocess boundary is cheap here:** Lighthouse is already a per-page, run-to-completion process (it only allows one audit per Node process anyway), and it natively emits a complete JSON report. So the natural integration is exactly a subprocess that takes a URL (+ a Chrome debugging port for the authenticated session) and prints JSON. There is no chatty IPC, no shared state, no serialization pain — just `subprocess.run(...)` and `json.loads(stdout)`. This is the same pattern Python SEO/perf tooling has used for years.

Confidence: **HIGH.**

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **Python** | 3.12+ | Primary language: orchestration, crawl logic, AI, outputs, persistence, CLI | Team is a Django/Python shop; every non-Lighthouse concern (Django metrics, Anthropic SDK, gspread, SQLite) is cleanest in Python. Long-term maintainability for *this* team. |
| **Node.js** | 22.19+ (LTS) | Runtime for the Lighthouse measurement worker only | Lighthouse 13.x **requires Node >=22.19**. Quarantined to one subprocess; not the app language. |
| **Playwright (Python)** | 1.60.x | Browser automation: crawl, link discovery, authenticated login, network capture | Cross-language API parity with Node, native `storage_state` for auth reuse, robust against SPAs, can launch a persistent context with a remote-debugging port that Lighthouse attaches to. The crawler and the Lighthouse worker share one authenticated browser. |
| **lighthouse** (npm) | 13.3.0 | The measurement engine — Lighthouse category scores + lab Core Web Vitals + network/timing audits | Authoritative source for Lighthouse Performance/SEO/A11y/Best-Practices scores and the diagnostic audits (TTFB/server-response, total byte weight, request counts, network requests). No Python equivalent produces real Lighthouse scores. |
| **chrome-launcher** (npm) | latest (ships with Lighthouse workflows) | Launch/locate Chrome for the Node worker, OR connect Lighthouse to the port Playwright opened | Standard companion to programmatic Lighthouse; also the mechanism for pointing Lighthouse at an already-authenticated Chrome session. |
| **anthropic** (Python SDK) | 0.104.x | AI analysis: Observation / Cause / Optimization per page | Official Anthropic SDK; native structured-output (`messages.parse()`) and prompt caching. Team's likely default AI provider. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **Typer** | 0.15.x+ | CLI framework (commands, flags, `--output sheets,html,json`) | Always — it's the CLI entry point. Built on Click, type-hint driven, great help output, pairs with Rich. |
| **Rich** | 13.x+ | Pretty terminal output: progress bars during crawl, result tables | Always — long crawls need live progress; Rich is the standard. |
| **gspread** | 6.2.x | Write results to Google Sheets via service account | Always (Sheets is a required output). Purpose-built for Sheets; far less boilerplate than `google-api-python-client`. Use `gspread-formatting` if you need cell styling/conditional formats for the regression deltas. |
| **google-auth** | 2.x | Service-account credentials for gspread | Always (transitive but pin it). Use a service account JSON, not OAuth user flow, for a headless CLI. |
| **Jinja2** | 3.1.x | HTML report generation from a template | Always (HTML report is a required output). Self-contained single-file HTML template with embedded data is the cleanest artifact. |
| **Pydantic** | 2.x | Typed models for a "page result" record; validates AI structured output | Always — gives you one schema that flows from Lighthouse JSON → persistence → Sheets/HTML, and validates the Anthropic structured response. |
| **httpx** | 0.27.x+ | HTTP client for the PSI API (supplementary public-page field data) and Django metrics endpoint | When PSI supplementary data or an owned-site metrics endpoint is in play. |
| **stdlib `sqlite3`** | (stdlib) | Run persistence + regression history store | Always (history is a required feature). See persistence section — SQLite over DuckDB/JSON. |

> Note: **Crawlee for Python** (v1.7.0, Playwright-backed) is a strong alternative crawler if you want batteries-included request queuing, retries, and state persistence out of the box. See "Crawler" decision below — for this project a **direct Playwright crawl** is recommended for control over the shared authenticated session, with Crawlee as the fallback if the bespoke queue logic grows painful.

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| **uv** | Python dependency & venv management | Fast, modern, lockfile-based. The 2025/2026 default for new Python projects. |
| **Ruff** | Lint + format | One tool for both; the current standard. |
| **npm** (for the worker) | Install/pin `lighthouse` + `chrome-launcher` | Keep a tiny `package.json` in a `lighthouse-worker/` subdir. The only Node surface area. |
| **pytest** | Test the orchestrator, parsers, regression math | Mock the Node worker by feeding it canned Lighthouse JSON fixtures. |

---

## Installation

```bash
# --- Python side (primary app) ---
uv init perfcrawl && cd perfcrawl
uv add typer rich gspread google-auth jinja2 pydantic httpx anthropic
uv add playwright
uv run playwright install chromium

# Dev
uv add --dev pytest ruff

# --- Node side (Lighthouse worker only) ---
mkdir lighthouse-worker && cd lighthouse-worker
npm init -y
npm install lighthouse@13 chrome-launcher
# Requires Node >= 22.19 on the machine running audits
```

---

## Measurement Engine: when to use which

This is the most important technical nuance in the whole project. **No single engine gives everything**, and lab vs. field is a real distinction.

| Engine | Gives you | Crawls behind auth? | Use it for |
|--------|-----------|---------------------|------------|
| **lighthouse npm (local, recommended core)** | Lighthouse category scores, **lab** CWV proxies (LCP, CLS, Total Blocking Time as INP proxy), server-response-time (TTFB), total byte weight, network-requests audit (request count, sizes, slowest request), status codes, the raw JSON + HTML artifact | **Yes** — via a Playwright-authenticated Chrome on a debugging port | The core per-page measurement on every page, including authenticated pages. |
| **Raw Playwright + CDP / `web-vitals` JS** | Precise control of navigation, network waterfall (via CDP `Network` events), full request list, response sizes/status, TTFB; can inject `web-vitals` for LCP/CLS | **Yes** | Capturing the **network waterfall, request count, total bytes, slowest request, status codes** directly — these are easier and more reliable to pull from Playwright/CDP than to dig out of Lighthouse's audit JSON. Complement to Lighthouse, not a replacement. |
| **PageSpeed Insights API v5** | Lighthouse lab data **plus real-user CrUX field data** (real INP/LCP/CLS distributions) — without running anything locally | **No** (public URLs only) | **Supplementary** field data for public pages only. Adds the one thing lab tools can't give: real-user CWV. Rate-limited (~25k/day, 400/100s). |
| **WebPageTest API** | Filmstrips, multi-location/multi-device runs, deep waterfall | No (hosted) | Out of scope for v1 — paid/hosted, overlaps Lighthouse. Note as a future option, don't build on it. |

**Prescription:**
- **Core engine = local `lighthouse` npm** (for scores + the JSON artifact the existing workflow archives to Drive) **run against a Playwright-authenticated browser.**
- **Network-level facts** (waterfall, request count, total bytes, slowest request URL+time, response sizes, status codes, TTFB) — capture these **directly from Playwright/CDP** during the crawl pass, because they're cleaner there than parsed out of Lighthouse audits. (TTFB in Lighthouse is the "server-response-time" audit; request data lives in `audits['network-requests']` — usable, but CDP is more direct.)
- **INP caveat — call this out in the report:** INP is fundamentally a **field** metric requiring real user interaction; it is *not* reliably measured in a headless lab pass. Report Lighthouse's **Total Blocking Time** as the lab proxy, and pull **real INP from the PSI/CrUX field data when the page is public.** Do not claim a lab "INP" number as if it were the real thing.

Confidence: **HIGH** on engine roles; the lab-vs-field INP point is well-established.

---

## Crawler & Authentication

**Recommendation: direct Playwright-for-Python crawl with a custom BFS link-discovery loop, sharing one authenticated browser context with the Lighthouse worker.**

- **Discovery:** seed URL → BFS over same-origin `<a href>` links, normalize/dedupe, respect a configurable max depth/page cap and (optionally) `robots.txt`/`sitemap.xml`. Keep an in-memory visited set + a queue persisted to the run store so a crash can resume.
- **Authentication (the critical part):** perform a scripted form login once with Playwright, then **persist the session** via either `storage_state` (cookies + localStorage JSON) or, when handing off to Lighthouse, a **persistent browser context launched with `--remote-debugging-port`**. Lighthouse opens its *own* page, so it must inherit auth from the shared context/port — passing a single logged-in `page` to Lighthouse does **not** carry the session. This is the well-documented `launchPersistentContext` + port pattern.
- **Auth approaches, in order of robustness:** (1) Playwright `storage_state` reused across the crawl; (2) persistent context + debugging port shared with Lighthouse for authenticated audits; (3) raw cookie injection for simple session-cookie sites.

**Alternative: Crawlee for Python (v1.7.0).** If the bespoke queue/retry/state logic becomes a maintenance burden, Crawlee's `PlaywrightCrawler` gives request queuing, retries, and disk-persisted state for free, and still uses Playwright underneath. Trade-off: less direct control over the exact shared-context handoff to Lighthouse. **Start direct; adopt Crawlee only if you outgrow the hand-rolled loop.**

**What NOT to use:** **Scrapy** for this. Scrapy is excellent for large-scale *static HTML* scraping but has no native headless browser, weak SPA support, and an awkward fit for "drive a logged-in browser and run Lighthouse." The whole project is browser-centric, so a browser-native crawler (Playwright) is the right base.

Confidence: **HIGH** on Playwright + the persistent-context auth handoff; **MEDIUM** on direct-vs-Crawlee (both work; it's a control-vs-batteries trade-off).

---

## Backend Metrics for owned Django sites (e.g. StudyHalo)

**Recommendation: a small custom Django "perf metrics" middleware that emits per-request timing + DB stats, surfaced two ways: a `Server-Timing` response header for at-a-glance numbers, plus an opt-in authenticated JSON endpoint (or `X-Perf-*` headers) the crawler reads.** This is the cleanest, least-invasive, most controllable option.

Mechanism: in middleware, time the request with `time.monotonic()`, wrap DB access with `connection.execute_wrapper` (or read `connection.queries` under `DEBUG`/instrumentation) to count queries, total query time, and detect duplicate queries; read cache hit/miss via a thin cache wrapper or signals. Expose the numbers the crawler can read deterministically. This keeps PerfCrawl's coupling to the target at a stable, versioned contract (a JSON shape / header set) rather than scraping a UI.

| Option | Verdict | Why |
|--------|---------|-----|
| **Custom perf middleware + JSON/header contract** | **Recommended** | Deterministic, machine-readable, no HTML scraping, you control the schema, trivially gated to owned/staging sites. Tiny code. |
| **django-silk 5.5.0** | **Strong alternative** | Already stores requests/SQL in its **own DB tables** and has a UI; you can query its models/DB directly for query counts, durations, duplicate queries. Best if the team wants persistent server-side profiling anyway and is OK adding a dependency + migrations to the target. More invasive than middleware, less invasive than parsing DjDT. |
| **django-debug-toolbar (parse injected panel)** | **Avoid for automation** | DjDT renders an **HTML panel** meant for human eyes; it doesn't render on API responses, and scraping its injected markup is brittle. It's a dev UI, not a data source. |
| **Full APM (Scout/Elastic/etc.)** | **Out of scope** | Heavyweight, hosted/continuous-monitoring posture — the project is explicitly on-demand, not always-on monitoring. |

**Prescription:** ship a tiny reusable Django middleware (a `Server-Timing` header + an opt-in `/__perf__` JSON endpoint guarded by a shared token) as the owned-site contract. Recommend **django-silk** only if the team already wants standing server-side profiling. Explicitly do **not** parse Debug Toolbar HTML.

Confidence: **MEDIUM-HIGH.** Middleware is clearly the cleanest; silk is a legitimate, slightly heavier alternative.

---

## AI Provider + SDK

**Recommendation: Anthropic `anthropic` Python SDK (0.104.x), model `claude-opus-4-7` for analysis quality (or `claude-sonnet-4-6` for cheaper/faster bulk runs), using native structured output + prompt caching.**

- **SDK:** official `anthropic` Python package — same language as the orchestrator, no extra service boundary.
- **Model:** `claude-opus-4-7` gives the strongest reasoning for the Observation/Cause/Optimization synthesis. For large crawls where cost/latency matters, make the model configurable and default bulk runs to `claude-sonnet-4-6`.
- **Structured output:** use `client.messages.parse()` with a **Pydantic model** (`Observation`, `PotentialCause`, `SuggestedOptimization`) so the AI response validates against your schema and flows straight into Sheets/HTML/persistence. No fragile free-text parsing.
- **Prompt caching:** put the **static rubric/instructions** (how to analyze a page, what each metric means, output format, examples) in a `cache_control` system prefix, and send only the **per-page metrics** as the variable suffix. Across a multi-page crawl this is a textbook caching win — cached input drops to ~10% of base price (note Opus 4.7's ~4,096-token cache minimum; if the rubric is smaller than that, Sonnet's 2,048 minimum or a bulked rubric applies).

Confidence: **HIGH** on SDK/approach; model naming verified against current SDK guidance.

---

## Output Integrations

| Output | Recommendation | Notes |
|--------|----------------|-------|
| **Google Sheets** | **gspread 6.2.x + service account** | Purpose-built, minimal boilerplate vs. `google-api-python-client`. Write the new richer schema (existing columns + CWV + regression deltas). Use `gspread-formatting` for conditional formatting on regressions (red = worse, green = better). Service-account auth (headless), not OAuth user flow. |
| **HTML report** | **Jinja2 single-file template** | Self-contained HTML with embedded JSON + a small JS chart for trends. One portable artifact; no build step. |
| **Raw Lighthouse artifacts** | **Write the Node worker's JSON + HTML straight to disk** | These are exactly the Drive-archived artifacts the existing workflow keeps. The worker already produces both. |
| **CSV/JSON** | **stdlib `csv` + `json`** (Pydantic `.model_dump()` → JSON) | No dependency needed. Pydantic gives clean JSON serialization for free. |

**What NOT to use:** `google-api-python-client` as the primary Sheets path (more boilerplate for no gain here); a Node `googleapis` client (would re-introduce Node into the output layer for no reason — keep outputs in Python).

Confidence: **HIGH.**

---

## Run Persistence / History Store

**Recommendation: SQLite (stdlib `sqlite3`), one DB file in the project, one row per (run, page).**

- **Why SQLite over DuckDB:** the workload is **transactional, write-heavy small inserts** (a row per page per run) and **point lookups** ("get the previous run for URL X to compute deltas") — exactly SQLite's OLTP sweet spot. SQLite does ~30–40k inserts/s vs DuckDB's ~4k naive inserts/s for this insert pattern; DuckDB only wins on big columnar analytical scans you won't be doing here. SQLite is also stdlib (zero deps) and trivially diffable/portable.
- **Why not JSON files:** fine for a single run's dump, but computing regression deltas across runs means loading and joining many files by hand. SQL (`JOIN` previous run, `WHERE url = ?`) is dramatically simpler and correct. Keep JSON as an *export format*, not the *store*.
- **Schema sketch:** `runs(id, started_at, target, git_sha?, auth_used)` + `page_results(run_id, url, lcp, cls, tbt, perf_score, ttfb, request_count, total_bytes, slowest_request_url, slowest_request_ms, status_code, backend_query_count, backend_query_ms, ...)`. Regression = self-join `page_results` on `url` against the prior `run_id`.
- **DuckDB note:** if trend dashboards over *hundreds* of runs become a real feature later, DuckDB can read the same data for analytics — but don't start there.

Confidence: **HIGH.**

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Python + Node Lighthouse worker | **Pure Node/TypeScript** | If the team were Node-native, or if the tool's *only* job were measurement (then build on Unlighthouse). Not this team. |
| Local `lighthouse` npm | **PageSpeed Insights API** | For **public** pages where you also want real CrUX field data and want to avoid local Chrome. Cannot do auth; rate-limited. Use as supplement. |
| Direct Playwright crawl | **Crawlee for Python 1.7.0** | When you want built-in request queue/retry/state persistence and don't need fine control over the shared-auth handoff to Lighthouse. |
| Custom Django perf middleware | **django-silk 5.5.0** | When the team wants standing server-side profiling with a UI and a queryable DB anyway. |
| gspread | **google-api-python-client** | Only if you need Sheets API features gspread doesn't wrap (rare here). |
| SQLite | **DuckDB** | Only if/when long-horizon analytical trend queries across many runs become a first-class feature. |
| Build the orchestrator | **Unlighthouse (off-the-shelf)** | If you only needed site-wide Lighthouse scores in a dashboard. It does crawl+parallel-Lighthouse beautifully — but it's Node, and it doesn't do Django backend metrics, custom AI analysis, the team's Sheets schema, or cross-run regression persistence. Borrow its *ideas* (sitemap/link discovery, parallel workers), don't build on it. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| **PyPI `lighthouse` package** | It's an abandoned (2016) service-discovery tool, **not** Google Lighthouse. There is no maintained Python Lighthouse port that produces real scores. | The Node `lighthouse` npm package via subprocess. |
| **Parsing django-debug-toolbar's injected HTML panel** | Brittle, human-facing UI, doesn't render on API responses. | Custom perf middleware (JSON/`Server-Timing`) or django-silk's DB. |
| **Scrapy as the crawler** | No native headless browser, poor SPA/JS and auth-flow fit for a browser-driven perf tool. | Playwright (direct) or Crawlee-Python. |
| **Treating a headless lab pass as real INP** | INP is a field metric requiring real interaction; a lab "INP" is misleading. | Report TBT as the lab proxy + real INP from PSI/CrUX for public pages. |
| **JSON files as the regression store** | Cross-run delta computation becomes manual file-joining. | SQLite; export JSON if needed. |
| **Passing a logged-in Playwright `page` directly to Lighthouse** | Lighthouse opens its own page; the session is lost. | Persistent context + `--remote-debugging-port`, or shared `storage_state`. |
| **WebPageTest/APM as a v1 foundation** | Hosted/paid, continuous-monitoring posture; out of scope for on-demand CLI. | Local Lighthouse + Playwright; revisit later if needed. |

---

## Stack Patterns by Variant

**If auditing a public site (no auth, generality mode):**
- Local Lighthouse against headless Chrome for scores + artifacts.
- Optionally enrich with PSI API for real CrUX field CWV (incl. real INP).
- Skip backend metrics.

**If auditing an owned Django site (StudyHalo, full mode):**
- Playwright logs in once → persistent context with debugging port.
- Lighthouse audits each authenticated page via that port.
- Crawler also reads the Django perf-metrics endpoint/header per page for SQL/cache/timing internals.
- All of it joined into one `page_result` row.

**If the crawl is large (hundreds of pages):**
- Default the AI model to `claude-sonnet-4-6` with prompt-cached rubric.
- Consider Crawlee-Python for managed queue + resumable state.
- Run a small pool of Lighthouse workers (separate Chrome/port each — concurrent audits on the *same* port conflict).

---

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| lighthouse@13.3.0 | Node >=22.19 | Hard requirement — provision Node 22 LTS on any audit machine. |
| playwright (Python) 1.60.0 | Python >=3.9 | Run `playwright install chromium` after pip/uv install. |
| anthropic 0.104.x | Python >=3.9 | `messages.parse()` + `cache_control` available in current SDK. |
| django-silk 5.5.0 | Django 4.2–6.0, Python 3.10–3.14 | Only relevant on the *target* Django app, not PerfCrawl itself. |
| crawlee (Python) 1.7.0 | Python >=3.10 | Optional crawler alternative; Playwright-backed. |
| gspread 6.2.x | google-auth 2.x | Service-account JSON for headless auth. |
| Playwright-authed Chrome ↔ lighthouse | via `--remote-debugging-port` | The auth handoff is the integration seam — test it early; it's the riskiest plumbing. |

---

## Sources

- GoogleChrome/lighthouse `package.json` (main) — verified **lighthouse 13.3.0**, **engines.node >=22.19**, programmatic `chrome-launcher` + port usage. HIGH.
- npmjs.com/package/lighthouse; lighthouse docs/readme — programmatic run pattern, one-audit-per-process. HIGH.
- PyPI `lighthouse` — confirmed it's an unrelated, abandoned (2016) service-discovery package, not Google Lighthouse. HIGH.
- PyPI: playwright 1.60.0 (2026-05-18), anthropic 0.104.1 (2026-05-22), django-silk 5.5.0 (2026-03-08), crawlee 1.7.0 (2026-05-12). HIGH (official PyPI).
- Unlighthouse docs (unlighthouse.dev) — site-wide Lighthouse crawl, URL discovery via sitemap/robots/links, PSI-API bulk mode. MEDIUM-HIGH (official project docs).
- playwright-lighthouse (npm/GitHub) + Lighthouse discussion #13326 — authenticated-page handling requires persistent context + debugging port; passing a logged-in page is insufficient. HIGH.
- Google PageSpeed Insights API v5 docs + community reports — returns Lighthouse lab + CrUX field data without local Chrome; ~25k/day, 400/100s; public URLs only. MEDIUM (limits are community-reported, not officially published).
- web-vitals (GoogleChrome) docs/issues — INP requires real interaction; not reliably captured headless. HIGH on the field-vs-lab point.
- gspread docs (docs.gspread.org, v6.2.1) — service-account auth, simpler than google-api-python-client for Sheets. HIGH.
- DuckDB-vs-SQLite comparisons (DataCamp, Better Stack, MotherDuck) — SQLite for OLTP small-insert/point-lookup; DuckDB for columnar analytics; insert-rate figures. MEDIUM-HIGH (multiple independent sources agree).
- django-silk (jazzband/django-silk) + Django middleware perf patterns (Server-Timing, connection.execute_wrapper) — backend-metrics options. MEDIUM-HIGH.
- Anthropic SDK / Claude API docs — `messages.parse()` structured output, prompt caching `cache_control`, per-model cache minimums, model guidance. HIGH.

---
*Stack research for: website performance auditing & crawling CLI*
*Researched: 2026-05-25*
