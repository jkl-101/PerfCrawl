# PerfCrawl

*(Working name — change anytime.)*

## What This Is

PerfCrawl is a general-purpose website performance auditing tool. Point it at any
website and it crawls the site by following internal links, measures frontend
performance on every reachable page, uses AI to explain what it finds, and writes
the results to whatever output formats you choose. The first real-world target is
**studyhalo.com**, but the tool is built to work on any site. For sites the team
owns, it can additionally capture backend internals (database and cache behavior).

## Core Value

Replace the slow, manual per-page performance audit — open a page, run Lighthouse,
read the Network tab and Django Debug Toolbar, then hand-fill a spreadsheet — with a
single command that crawls a site, gathers consistent performance statistics, and
produces actionable analysis.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. All are hypotheses until shipped and validated. -->

- [ ] Crawl a website by auto-discovering pages — follow internal links from a seed URL
- [ ] Support authenticated crawling — log in to reach pages behind auth (e.g. dashboards)
- [ ] Capture external performance metrics per page: Core Web Vitals (LCP / CLS / INP), Lighthouse scores (Performance / SEO / Accessibility / Best Practices), network waterfall, TTFB, request count, total bytes transferred, slowest request URL + time, response sizes, status codes
- [ ] Capture backend internals for owned sites: SQL query counts, slow/duplicate queries, cache usage, request timing (access mechanism decided during research)
- [ ] AI-generated analysis per page: Observation, Potential Cause, Suggested Optimization
- [ ] Output to user-selectable formats per run: Google Sheets (new richer schema), HTML report, raw Lighthouse artifacts, local CSV/JSON
- [ ] Persist every run and compare against prior runs to flag regressions and improvements over time
- [ ] Run as a CLI tool, on demand

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Scheduled / CI automation — deferred; CLI-first now, automation added later once the core is stable
- Claude Chrome extension / paid-plan browser automation — rejected (paid-only, per Slack findings)
- Applying performance fixes to the target site — the tool audits and recommends; it never modifies the target
- Always-on / real-time monitoring dashboards (e.g. Site24x7-style) — this is on-demand auditing with historical comparison, not continuous monitoring
- Backend internals for sites the team does not own — impossible to read another site's DB/cache externally

## Context

- **Origin:** A team effort to speed up **studyhalo.com**, a Django web app. The current
  manual workflow collects frontend metrics (Lighthouse, Chrome Network tab) and backend
  metrics (Django Debug Toolbar) and records them in a Google Sheet, with human-written
  Observation / Potential Cause / Suggested Optimization notes.
- **Existing Google Sheet columns** (the manual baseline PerfCrawl supersedes): Page, URL,
  Test Date, Cache Disabled, Total Page Load Time, Number of Requests, Total Data
  Transferred, Slowest Request URL, Slowest Request Time, TTFB (ms), Response Size, Status
  Code, Lighthouse Report (Drive link), Observation, Potential Cause, Suggested Optimization.
  Reference sheet: https://docs.google.com/spreadsheets/d/1KNHxtSThjtKX-q-B0FQV4GLzh5cWO5oQjKm0e1DR5c8/
- **Backend stack of first target:** StudyHalo runs on Django — relevant to the Debug
  Toolbar / backend-metrics path.
- **Industry tools surfaced in Slack** (to learn from during research): PageSpeed Insights,
  GTmetrix, WebPageTest (Catchpoint), Site24x7, Elastic / Logstash.
- **Ruled out:** The Claude Chrome extension + MCP connector approach — the extension
  requires a paid plan, so it isn't a dependable foundation.

## Constraints

- **Tech stack**: Undecided — Python vs Node to be recommended during research. Tension: the
  team works in Django/Python, but Lighthouse is natively a Node tool.
- **AI provider**: Undecided — to be recommended during research (Claude / Anthropic is a
  likely default given the team's existing tooling).
- **Backend metrics access**: Mechanism to be recommended during research (Debug Toolbar
  parsing vs django-silk vs an exposed metrics endpoint). Available only for owned sites.
- **Generality**: Must work on any website using external-only metrics; backend metrics are
  an owned-site add-on, never a hard requirement for a run.
- **Compatibility**: Must integrate with the team's existing Google Sheets workflow.

## Key Decisions

<!-- Decisions that constrain future work. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Generalize beyond StudyHalo to any website | Reusable across projects; StudyHalo is the first target, not the only one | — Pending |
| Auto-discover pages via link crawling (not a fixed URL list) | Fuller coverage without hand-maintaining URL lists | — Pending |
| Support authenticated crawling | The highest-value pages (dashboards) sit behind login | — Pending |
| Backend internals only for owned sites | Cannot read another site's DB/cache internals externally | — Pending |
| AI auto-generates Observation / Cause / Optimization | Automates the slowest part of today's manual workflow | — Pending |
| Multi-format, user-selectable output | Different consumers need different artifacts | — Pending |
| Track runs over time and flag regressions | Performance work needs trend visibility, not just snapshots | — Pending |
| New richer Google Sheets schema (supersedes existing columns) | Existing columns miss Core Web Vitals and regression deltas | — Pending |
| CLI-first; automation later | Control and fast iteration first; automate once stable | — Pending |
| Defer tech stack + AI provider to research | Real tradeoffs (Python ecosystem fit vs native Lighthouse/Node) | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-25 after initialization*
