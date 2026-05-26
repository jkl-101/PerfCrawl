<!-- GSD:project-start source:PROJECT.md -->
## Project

**PerfCrawl**

PerfCrawl is a general-purpose website performance auditing tool. Point it at any
website and it crawls the site by following internal links, measures frontend
performance on every reachable page, uses AI to explain what it finds, and writes
the results to whatever output formats you choose. The first real-world target is
**studyhalo.com**, but the tool is built to work on any site. For sites the team
owns, it can additionally capture backend internals (database and cache behavior).

**Core Value:** Replace the slow, manual per-page performance audit — open a page, run Lighthouse,
read the Network tab and Django Debug Toolbar, then hand-fill a spreadsheet — with a
single command that crawls a site, gathers consistent performance statistics, and
produces actionable analysis.

### Constraints

- **Tech stack**: Undecided — Python vs Node to be recommended during research. Tension: the
  team works in Django/Python, but Lighthouse is natively a Node tool.
- **AI provider**: Undecided — to be recommended during research (Claude / Anthropic is a
  likely default given the team's existing tooling).
- **Backend metrics access**: Mechanism to be recommended during research (Debug Toolbar
  parsing vs django-silk vs an exposed metrics endpoint). Available only for owned sites.
- **Generality**: Must work on any website using external-only metrics; backend metrics are
  an owned-site add-on, never a hard requirement for a run.
- **Compatibility**: Must integrate with the team's existing Google Sheets workflow.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Headline Decision: Python orchestrator + thin Node "measurement worker"
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
### Development Tools
| Tool | Purpose | Notes |
|------|---------|-------|
| **uv** | Python dependency & venv management | Fast, modern, lockfile-based. The 2025/2026 default for new Python projects. |
| **Ruff** | Lint + format | One tool for both; the current standard. |
| **npm** (for the worker) | Install/pin `lighthouse` + `chrome-launcher` | Keep a tiny `package.json` in a `lighthouse-worker/` subdir. The only Node surface area. |
| **pytest** | Test the orchestrator, parsers, regression math | Mock the Node worker by feeding it canned Lighthouse JSON fixtures. |
## Installation
# --- Python side (primary app) ---
# Dev
# --- Node side (Lighthouse worker only) ---
# Requires Node >= 22.19 on the machine running audits
## Measurement Engine: when to use which
| Engine | Gives you | Crawls behind auth? | Use it for |
|--------|-----------|---------------------|------------|
| **lighthouse npm (local, recommended core)** | Lighthouse category scores, **lab** CWV proxies (LCP, CLS, Total Blocking Time as INP proxy), server-response-time (TTFB), total byte weight, network-requests audit (request count, sizes, slowest request), status codes, the raw JSON + HTML artifact | **Yes** — via a Playwright-authenticated Chrome on a debugging port | The core per-page measurement on every page, including authenticated pages. |
| **Raw Playwright + CDP / `web-vitals` JS** | Precise control of navigation, network waterfall (via CDP `Network` events), full request list, response sizes/status, TTFB; can inject `web-vitals` for LCP/CLS | **Yes** | Capturing the **network waterfall, request count, total bytes, slowest request, status codes** directly — these are easier and more reliable to pull from Playwright/CDP than to dig out of Lighthouse's audit JSON. Complement to Lighthouse, not a replacement. |
| **PageSpeed Insights API v5** | Lighthouse lab data **plus real-user CrUX field data** (real INP/LCP/CLS distributions) — without running anything locally | **No** (public URLs only) | **Supplementary** field data for public pages only. Adds the one thing lab tools can't give: real-user CWV. Rate-limited (~25k/day, 400/100s). |
| **WebPageTest API** | Filmstrips, multi-location/multi-device runs, deep waterfall | No (hosted) | Out of scope for v1 — paid/hosted, overlaps Lighthouse. Note as a future option, don't build on it. |
- **Core engine = local `lighthouse` npm** (for scores + the JSON artifact the existing workflow archives to Drive) **run against a Playwright-authenticated browser.**
- **Network-level facts** (waterfall, request count, total bytes, slowest request URL+time, response sizes, status codes, TTFB) — capture these **directly from Playwright/CDP** during the crawl pass, because they're cleaner there than parsed out of Lighthouse audits. (TTFB in Lighthouse is the "server-response-time" audit; request data lives in `audits['network-requests']` — usable, but CDP is more direct.)
- **INP caveat — call this out in the report:** INP is fundamentally a **field** metric requiring real user interaction; it is *not* reliably measured in a headless lab pass. Report Lighthouse's **Total Blocking Time** as the lab proxy, and pull **real INP from the PSI/CrUX field data when the page is public.** Do not claim a lab "INP" number as if it were the real thing.
## Crawler & Authentication
- **Discovery:** seed URL → BFS over same-origin `<a href>` links, normalize/dedupe, respect a configurable max depth/page cap and (optionally) `robots.txt`/`sitemap.xml`. Keep an in-memory visited set + a queue persisted to the run store so a crash can resume.
- **Authentication (the critical part):** perform a scripted form login once with Playwright, then **persist the session** via either `storage_state` (cookies + localStorage JSON) or, when handing off to Lighthouse, a **persistent browser context launched with `--remote-debugging-port`**. Lighthouse opens its *own* page, so it must inherit auth from the shared context/port — passing a single logged-in `page` to Lighthouse does **not** carry the session. This is the well-documented `launchPersistentContext` + port pattern.
- **Auth approaches, in order of robustness:** (1) Playwright `storage_state` reused across the crawl; (2) persistent context + debugging port shared with Lighthouse for authenticated audits; (3) raw cookie injection for simple session-cookie sites.
## Backend Metrics for owned Django sites (e.g. StudyHalo)
| Option | Verdict | Why |
|--------|---------|-----|
| **Custom perf middleware + JSON/header contract** | **Recommended** | Deterministic, machine-readable, no HTML scraping, you control the schema, trivially gated to owned/staging sites. Tiny code. |
| **django-silk 5.5.0** | **Strong alternative** | Already stores requests/SQL in its **own DB tables** and has a UI; you can query its models/DB directly for query counts, durations, duplicate queries. Best if the team wants persistent server-side profiling anyway and is OK adding a dependency + migrations to the target. More invasive than middleware, less invasive than parsing DjDT. |
| **django-debug-toolbar (parse injected panel)** | **Avoid for automation** | DjDT renders an **HTML panel** meant for human eyes; it doesn't render on API responses, and scraping its injected markup is brittle. It's a dev UI, not a data source. |
| **Full APM (Scout/Elastic/etc.)** | **Out of scope** | Heavyweight, hosted/continuous-monitoring posture — the project is explicitly on-demand, not always-on monitoring. |
## AI Provider + SDK
- **SDK:** official `anthropic` Python package — same language as the orchestrator, no extra service boundary.
- **Model:** `claude-opus-4-7` gives the strongest reasoning for the Observation/Cause/Optimization synthesis. For large crawls where cost/latency matters, make the model configurable and default bulk runs to `claude-sonnet-4-6`.
- **Structured output:** use `client.messages.parse()` with a **Pydantic model** (`Observation`, `PotentialCause`, `SuggestedOptimization`) so the AI response validates against your schema and flows straight into Sheets/HTML/persistence. No fragile free-text parsing.
- **Prompt caching:** put the **static rubric/instructions** (how to analyze a page, what each metric means, output format, examples) in a `cache_control` system prefix, and send only the **per-page metrics** as the variable suffix. Across a multi-page crawl this is a textbook caching win — cached input drops to ~10% of base price (note Opus 4.7's ~4,096-token cache minimum; if the rubric is smaller than that, Sonnet's 2,048 minimum or a bulked rubric applies).
## Output Integrations
| Output | Recommendation | Notes |
|--------|----------------|-------|
| **Google Sheets** | **gspread 6.2.x + service account** | Purpose-built, minimal boilerplate vs. `google-api-python-client`. Write the new richer schema (existing columns + CWV + regression deltas). Use `gspread-formatting` for conditional formatting on regressions (red = worse, green = better). Service-account auth (headless), not OAuth user flow. |
| **HTML report** | **Jinja2 single-file template** | Self-contained HTML with embedded JSON + a small JS chart for trends. One portable artifact; no build step. |
| **Raw Lighthouse artifacts** | **Write the Node worker's JSON + HTML straight to disk** | These are exactly the Drive-archived artifacts the existing workflow keeps. The worker already produces both. |
| **CSV/JSON** | **stdlib `csv` + `json`** (Pydantic `.model_dump()` → JSON) | No dependency needed. Pydantic gives clean JSON serialization for free. |
## Run Persistence / History Store
- **Why SQLite over DuckDB:** the workload is **transactional, write-heavy small inserts** (a row per page per run) and **point lookups** ("get the previous run for URL X to compute deltas") — exactly SQLite's OLTP sweet spot. SQLite does ~30–40k inserts/s vs DuckDB's ~4k naive inserts/s for this insert pattern; DuckDB only wins on big columnar analytical scans you won't be doing here. SQLite is also stdlib (zero deps) and trivially diffable/portable.
- **Why not JSON files:** fine for a single run's dump, but computing regression deltas across runs means loading and joining many files by hand. SQL (`JOIN` previous run, `WHERE url = ?`) is dramatically simpler and correct. Keep JSON as an *export format*, not the *store*.
- **Schema sketch:** `runs(id, started_at, target, git_sha?, auth_used)` + `page_results(run_id, url, lcp, cls, tbt, perf_score, ttfb, request_count, total_bytes, slowest_request_url, slowest_request_ms, status_code, backend_query_count, backend_query_ms, ...)`. Regression = self-join `page_results` on `url` against the prior `run_id`.
- **DuckDB note:** if trend dashboards over *hundreds* of runs become a real feature later, DuckDB can read the same data for analytics — but don't start there.
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
## Stack Patterns by Variant
- Local Lighthouse against headless Chrome for scores + artifacts.
- Optionally enrich with PSI API for real CrUX field CWV (incl. real INP).
- Skip backend metrics.
- Playwright logs in once → persistent context with debugging port.
- Lighthouse audits each authenticated page via that port.
- Crawler also reads the Django perf-metrics endpoint/header per page for SQL/cache/timing internals.
- All of it joined into one `page_result` row.
- Default the AI model to `claude-sonnet-4-6` with prompt-cached rubric.
- Consider Crawlee-Python for managed queue + resumable state.
- Run a small pool of Lighthouse workers (separate Chrome/port each — concurrent audits on the *same* port conflict).
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
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

## GSD Workflow — PerfCrawl Status

The canonical execution order lives in `~/.claude/CLAUDE.md` § "GSD Workflow — Skill Execution Order".
Follow those rules. This section is the project-specific status snapshot.

### Phase 1 — data-model-persistence-foundation: WHERE WE ARE

| Stage | Skill | Artifact | Status |
|-------|-------|----------|--------|
| WHY/HOW | discuss-phase | `01-CONTEXT.md`, `01-DISCUSSION-LOG.md` | ✓ |
| DO | plan-phase | `01-RESEARCH.md`, `01-{01,02,03}-PLAN.md` | ✓ |
| DO | execute-phase | `01-{01,02,03}-SUMMARY.md` | ✓ (67 tests green) |
| PROVE — code | code-review | `01-REVIEW.md` (closed `244d1e5`) | ✓ |
| PROVE — threat | secure-phase | `01-SECURITY.md` (9/9 closed `c0f2572`) | ✓ |
| PROVE — Nyquist | validate-phase | `01-VALIDATION.md` (13/13 covered, `0d84439`) | ✓ |
| PROVE — user | verify-work | `01-UAT.md` (12/12 passed `0f407d6`) | ✓ |
| (capture) | extract-learnings | `01-LEARNINGS.md` | ⬜ optional |
| SHIP | **ship** | (admin-only, no PR — see ship note below) | ✓ |

**Ship note (Phase 01):** Work was committed directly to `main` and pushed to
`origin/main` while `git.branching_strategy` was `"none"`, so by the time
`/gsd-ship 1` ran there was no diff to PR (`main == origin/main`). Phase 01
was closed administratively (this CLAUDE.md + `.planning/STATE.md` update)
and `git.branching_strategy` was flipped to `"phase"` in
`.planning/config.json`. From Phase 02 forward, work happens on
`gsd/phase-{N}-{slug}` branches and ships via real `gh pr create`.

**Next command:** `/gsd-discuss-phase 2` (optionally `/gsd-extract-learnings 1`
first to capture Phase 1 lessons before moving on).

**Do NOT propose:** `audit-milestone` (milestone has phases 2-6 remaining),
re-running any ✓ gate (their artifacts are already closed on disk),
`/gsd-ship 1` again (phase 01 is closed), or trying to retroactively open a
PR for Phase 01 (work is already on `main`; that history is set).

Update this table as later phases reach the same stages.

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
