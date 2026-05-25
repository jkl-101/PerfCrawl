# Pitfalls Research

**Domain:** Website performance auditing & crawling tool (Lighthouse/CWV + authenticated crawl + Django backend metrics + AI analysis + Sheets export + regression tracking)
**Researched:** 2026-05-25
**Confidence:** HIGH (measurement variance, crawler, Django, Sheets grounded in official docs; AI grounding MEDIUM)

> Phase numbers below are *suggested topic buckets*, not a committed roadmap. They map a pitfall to the kind of work that must prevent it (e.g. "Crawler core", "Metrics capture", "Regression tracking", "AI analysis", "Export"). The roadmap author should align them with the real phase list.

---

## Critical Pitfalls

These cause silent wrong conclusions, data loss, or damage to the target site. They are the ones most likely to make the tool actively harmful rather than merely incomplete.

### Pitfall 1: Treating a single Lighthouse run as ground truth (measurement variance)

**What goes wrong:**
Lighthouse/lab metrics are non-deterministic. The same page, unchanged, can swing 5-20+ performance points and large LCP/TBT deltas between runs. If the tool records one run per page and the regression tracker diffs run-to-run, it will constantly cry wolf — flagging "regressions" and "improvements" that are pure noise. This is the #1 trap for this project because regression detection is a core requirement.

**Why it happens:**
Variance comes from page nondeterminism (A/B tests, ads, dynamic content), local network jitter, client hardware, resource contention from other processes, browser task-scheduling nondeterminism, and server response variability. Google's own docs say "the median Lighthouse score of 5 runs is twice as stable as 1 run." Single-run capture is the path of least resistance, so it gets built first and the variance only becomes visible once trend lines exist.

**How to avoid:**
- Run each page N times (default 3, allow 5) and store the **median** per metric — not mean (mean is outlier-sensitive). Median per metric, not median by overall score, so each metric is individually robust.
- Prefer **simulated throttling** (Lighthouse default) for stability, OR pin to a specific throttling method and never mix methods across runs. Record which method was used in run metadata.
- Pin and record CPU throttling multiplier, network profile, screen emulation, Chrome version, and Lighthouse version with every run.
- Store the raw distribution (all N runs), not just the median, so confidence/spread can be shown later.
- When comparing runs, require a **delta threshold** (e.g. only flag if change exceeds the metric's known variance band) instead of flagging any non-zero diff.

**Warning signs:**
Regression report is noisy/contradictory between consecutive runs of an unchanged site; the same page flips between "regressed" and "improved"; metric deltas are within ~5 points / tens of ms.

**Phase to address:** Metrics capture phase (multi-run + median) AND regression-tracking phase (variance-aware thresholds). Both must cooperate — building either alone leaves the trap open.

---

### Pitfall 2: Crawling a logout / delete / state-mutating link during an authenticated crawl

**What goes wrong:**
An auto-discovery crawler follows every internal link. On an authenticated session it will eventually hit a logout link (ending the session mid-crawl, so every subsequent page is captured as the logged-out version) or, worse, a destructive action link ("Delete", "Archive", "Publish", "Unsubscribe", GET-based admin actions) — mutating the target site's data. For StudyHalo, an owned site, this could destroy real records.

**Why it happens:**
Crawlers click everything; auth gives them access to action links that don't exist for anonymous visitors. Many apps still expose state changes via GET links (anti-pattern, but common). The crawler author tests anonymously first, where these links are invisible, so the hazard ships undetected.

**How to avoid:**
- Maintain a **default denylist of dangerous URL patterns** (`/logout`, `/signout`, `/delete`, `/remove`, `/admin`, `/wp-admin/`, `/destroy`, `?action=delete`, etc.) and skip them before fetching.
- Strongly recommend (and document) crawling with a **dedicated read-only / low-privilege account**, never an admin account.
- Treat the crawl as **read-only by contract**: default to GET only, never submit forms or follow links with mutation-suggesting query params.
- Detect session loss: periodically assert an "am I still logged in?" signal (presence of a known authenticated-only element / non-redirect to login). If lost, stop and re-authenticate or abort rather than silently recording garbage.
- Honor `rel="nofollow"` and let users supply an explicit allow/deny pattern list.

**Warning signs:**
Crawl results show login page content for pages that require auth; data missing/changed on the target after a crawl; sudden drop in page count mid-crawl; pages returning 302→login.

**Phase to address:** Authenticated-crawl phase (denylist + read-only contract + session-liveness check are non-negotiable acceptance criteria).

---

### Pitfall 3: Infinite / exploding URL space (crawler traps)

**What goes wrong:**
The crawler never terminates or explodes to millions of near-duplicate URLs. Classic sources: calendar "next month" links (infinite by definition), faceted navigation / filter combinations (color+size+price → combinatorial explosion), session IDs or tracking params appended to every link (each link looks "new"), and pagination with no bound. The run hangs, the host site gets hammered, and the dataset is full of useless duplicate pages that pollute regression comparisons.

**Why it happens:**
URL-space infinity is invisible on small test sites. Time-based and parameter-based spaces are unbounded or astronomically large but look like ordinary links to a naive BFS/DFS crawler.

**How to avoid:**
- Hard caps: **max pages**, **max depth**, **max time** — all configurable, all enforced.
- **URL canonicalization + dedup**: normalize and strip/ignore volatile query params (session IDs, tracking, sort/filter) before deciding a URL is new; honor `<link rel="canonical">`.
- Pattern-based trap detection: detect repeating path segments, monotonically incrementing date params, and query-param fan-out; configurable param allowlist/denylist.
- Respect `robots.txt` Disallow as trap hints (sites often disallow exactly these spaces).
- Per-host **visited set** keyed on canonical URL, not raw URL.

**Warning signs:**
Page count climbs without converging; many URLs differ only by a query param or date; crawl exceeds expected page count by an order of magnitude; same content hash appears under many URLs.

**Phase to address:** Crawler core phase (caps + canonicalization + trap heuristics built in from the start, not bolted on).

---

### Pitfall 4: Hammering the target site / getting rate-limited or IP-banned

**What goes wrong:**
Running many concurrent fetches plus many headless Lighthouse audits generates a burst of load that looks like a DoS. The target starts returning 429/503, which the tool may record as "slow page" performance data (garbage), or the crawler's IP gets banned — locking the team out of their own site or a client's site.

**Why it happens:**
Concurrency is added for speed; politeness is an afterthought. Lab audits are heavy (full page load + scripted CPU work) so even modest concurrency multiplies real load. Error responses get silently captured as metrics.

**How to avoid:**
- Default to **polite, low concurrency** (e.g. 1-2 in-flight per host) with a configurable inter-request delay; honor `Crawl-delay` from robots.txt.
- **Exponential backoff** with respect for the `Retry-After` header on 429/503; pause or abort if the site keeps rate-limiting.
- **Never record non-2xx/non-3xx responses as performance data** — tag them as errors and exclude from metrics/regression diffs.
- Set a descriptive, identifiable **User-Agent** so site owners can recognize the tool.
- Throttle headless-audit concurrency *separately and lower* than crawl-discovery concurrency.

**Warning signs:**
Rising 429/503 counts; TTFB suddenly inflating across all pages; abrupt connection resets; the site becomes unreachable from the crawl host.

**Phase to address:** Crawler core phase (politeness + backoff) and Metrics capture phase (error-vs-metric separation).

---

### Pitfall 5: Headless Chrome resource exhaustion / version drift

**What goes wrong:**
Lighthouse runs one audit per Node process and each spins up a heavy Chrome instance. Running many in parallel causes memory exhaustion, zombie Chrome processes, crashes mid-run, AND skews the very numbers being measured (resource contention is itself a variance source — see Pitfall 1). Separately, Chrome/Chromium/driver/Lighthouse version mismatches produce `Protocol Error ... wasn't found` failures or silently change results between machines.

**Why it happens:**
Parallelism is the obvious way to speed up auditing a large site. Chrome auto-updates and the team's local Chrome drifts from CI/other machines. Slow pages blow past default timeouts and the run dies.

**How to avoid:**
- **Bounded audit concurrency** (small pool, e.g. 1-3 depending on host cores; Lighthouse docs recommend ≥2 dedicated cores per instance). Never run audits concurrently *on the same machine* if absolute number stability matters.
- Reap Chrome processes after each audit; guard against leaks; cap per-audit timeout and record timeouts as errors, not zeros.
- **Pin a known Chrome/Chromium build** (e.g. bundled Chromium via the browser-automation lib) rather than the system Chrome, and **record the exact browser + Lighthouse versions** in run metadata so cross-run comparisons are valid.
- Run audits sequentially per page-set when collecting numbers for regression comparison; reserve parallelism for throughput-only modes that are explicitly marked "less stable."

**Warning signs:**
OOM kills / Chrome crashes during large crawls; orphaned `chrome` processes after a run; `Protocol Error` or version-mismatch errors; numbers that differ between two machines for the same site.

**Phase to address:** Metrics capture phase (concurrency pool, version pinning, timeout handling).

---

### Pitfall 6: Django Debug Toolbar / Silk enabled or exposed in production

**What goes wrong:**
The backend-metrics path for owned sites is tempting to implement by turning on Django Debug Toolbar or django-silk on the live site and scraping it. Both are **explicitly unsafe in production**: Debug Toolbar requires `DEBUG=True`, exposes settings/SQL/request internals, and has a known high-severity vuln allowing arbitrary SQL execution via the SQL explain forms. django-silk adds heavy space/time overhead under load and can store plaintext passwords. Enabling either on StudyHalo to feed PerfCrawl would create a real security hole.

**Why it happens:**
Debug Toolbar is the team's *current manual* backend-metrics source, so the instinct is to automate scraping it. The leap from "I use it locally" to "I'll enable it in prod so the tool can read it" is short and dangerous.

**How to avoid:**
- **Never require `DEBUG=True` or a debug UI in production.** Choose a backend-metrics mechanism designed for safe collection: a dedicated, authenticated **metrics endpoint** the team adds to their own app, or per-request instrumentation written to logs/a store, or running the audit against a **staging environment** that mirrors prod.
- If profiling middleware is used at all, gate it behind auth + a feature flag + non-prod environment, and accept its overhead taints timing numbers (don't report instrumented timings as the user-facing latency).
- Treat backend metrics as **owned-site, opt-in, decoupled** from the frontend crawl (PROJECT.md already scopes this) — the tool must work fully without them.

**Warning signs:**
Plan calls for enabling Debug Toolbar/Silk on a live domain; backend metrics require `DEBUG=True`; the metrics path exposes settings or raw SQL UIs publicly.

**Phase to address:** Backend-metrics phase (security-reviewed access mechanism is the gating decision; document the chosen approach in PROJECT.md Key Decisions).

---

## Moderate Pitfalls

### Pitfall 7: Unstable page identity across runs (apples-to-oranges comparison)

**What goes wrong:**
Regression tracking requires matching "the same page" across runs. If page identity is keyed on raw URL, then changed query params, trailing slashes, locale prefixes, pagination, or a re-crawl that discovers a different page set will break the join — comparing different pages or dropping pages from the diff. Worse, if the crawl set itself changes (new pages appear, old ones 404), a naive "site average" comparison is meaningless.

**How to avoid:** Define a **canonical, stable page key** (normalized URL + optional logical-page label) decoupled from run-specific params. Diff **per-page** on matched keys; explicitly report added/removed pages instead of folding them into aggregates. Let users pin a fixed URL set for strict trend tracking, separate from open-ended discovery.

**Phase to address:** Regression-tracking phase (page-identity model is foundational to it).

---

### Pitfall 8: AI advice that is generic, hallucinated, or ungrounded

**What goes wrong:**
"AI analysis per page" (Observation / Cause / Optimization) easily degrades into boilerplate ("minify your JavaScript", "use a CDN") that ignores the actual measured data, or hallucinates problems the metrics don't support. This erodes trust fast and makes the AI column noise.

**How to avoid:** **Ground every suggestion in the captured metrics** — feed the model the concrete numbers (LCP, slowest request, query counts, byte sizes) and instruct it to reference specific evidence; reject/flag suggestions that don't cite a metric. Use Lighthouse's own audit/opportunity output as structured grounding rather than asking the model to guess. Keep temperature low for consistency. Consider a deterministic rules layer for obvious findings and reserve the LLM for synthesis.

**Phase to address:** AI-analysis phase.

---

### Pitfall 9: AI token/cost blowup across many pages

**What goes wrong:**
Calling an LLM per page across a multi-hundred-page crawl, possibly with full HTML or full Lighthouse JSON in the prompt, multiplies token cost and latency. Costs scale with crawl size and run frequency; a single regression run could become expensive. Most cost is *input* tokens.

**How to avoid:** Send **only distilled metrics** (not raw HTML / full Lighthouse artifact) to the model; cap per-page input size. **Cache/skip AI** for pages whose metrics are unchanged vs the prior run (tie into regression diff — only analyze what changed). Make AI analysis **opt-in per run** and batch where possible. Surface estimated token/cost before a large run.

**Phase to address:** AI-analysis phase (and integrate with regression diff to avoid re-analyzing unchanged pages).

---

### Pitfall 10: Google Sheets API rate limits / quota (429)

**What goes wrong:**
Writing one row per page to Sheets in a loop blows the per-minute quota (≈60 read + 60 write requests per minute per user/project; service-account calls all count as one user). A large crawl export hits 429 and the export fails or partially writes.

**How to avoid:** **Batch writes** via `batchUpdate` / `values.batchUpdate` (one call for many rows) instead of per-row calls; implement **exponential backoff** on 429; respect that service-account usage shares one quota bucket. Note Google plans to bill for excess quota requests later in 2026.

**Phase to address:** Export phase (Sheets integration).

---

### Pitfall 11: Sheets schema drift / clobbering the manual baseline

**What goes wrong:**
PROJECT.md says the new schema *supersedes* the existing manual sheet. If the tool writes by absolute column position into the existing sheet, any manual column reorder/insert silently misaligns data; or the tool overwrites the team's historical manual rows.

**How to avoid:** Write to a **new sheet/tab** with a tool-owned schema; **map by header name, not column index**; detect header drift and fail loudly rather than writing misaligned data; never overwrite the historical manual baseline sheet (treat it as read-only reference).

**Phase to address:** Export phase.

---

### Pitfall 12: INP/CLS lab limitations misrepresented as field reality

**What goes wrong:**
INP is a **pure field metric** — Lighthouse cannot measure it because it doesn't perform real user interactions; lab tools substitute **TBT** as a proxy. CLS measured in-lab only captures shifts during the scripted load, missing interaction-driven shifts. If the tool labels a lab TBT-derived number as "INP" or presents lab CLS as the full story, it reports a metric users will compare against Google's field CrUX data and find wrong.

**How to avoid:** Label lab metrics honestly: report **TBT** (and optionally note it as the INP lab proxy), report CLS as **load-time CLS**. If real INP/field data is wanted, that requires CrUX/RUM — out of scope for a lab crawler; say so rather than faking it. Drive interactions only if you explicitly script them, and document that.

**Phase to address:** Metrics capture phase (metric definitions + honest labeling); surface in FEATURES/output schema.

---

## Minor Pitfalls

### Pitfall 13: Credential / secret handling for authenticated crawl & service accounts

**What goes wrong:** Login credentials, session cookies, and the Google service-account JSON key get committed to the repo, logged, or saved in plaintext config/artifacts. Session cookies are live credentials.

**How to avoid:** Load secrets from env / a secrets file that is gitignored; never log credentials or cookies; scrub them from HTML/JSON artifacts and run metadata; document rotating the crawl account password if cookies were exposed; least-privilege service account scoped to the target sheet only.

**Phase to address:** Auth phase + Export phase (cross-cutting; establish secret-handling convention early).

---

### Pitfall 14: CSRF / login-form mechanics breaking authenticated login

**What goes wrong:** Django login (and form posts) require CSRF tokens and the session cookie round-trip. A crawler that POSTs credentials without first fetching the form's CSRF token / cookies fails to authenticate, then silently crawls as anonymous.

**How to avoid:** Fetch the login page, extract the CSRF token + cookies, then POST; verify login succeeded (Pitfall 2's liveness check) before proceeding. Prefer driving login through the headless browser so cookies/CSRF are handled natively.

**Phase to address:** Auth phase.

---

### Pitfall 15: Lab numbers presented without environment context

**What goes wrong:** Numbers captured on a developer laptop vs a CI runner vs another team member's machine differ due to hardware/network. Without recorded environment, cross-machine comparisons are invalid and "regressions" may just be "different machine."

**How to avoid:** Stamp every run with environment metadata (host, CPU cores, Chrome/Lighthouse versions, throttling config) and warn/exclude when comparing runs from different environments.

**Phase to address:** Metrics capture + Regression-tracking phases.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Single Lighthouse run per page | 3-5x faster runs, simpler code | Noisy regression tracking that can't be trusted — undermines a core feature | Only in a throwaway smoke-test mode, explicitly labeled "non-comparable" |
| Per-row Sheets writes in a loop | Trivial to implement | Hits 429 on large crawls; partial writes | Tiny sites (<~30 pages) only; migrate to batch before real use |
| Raw URL as page identity | No canonicalization code | Breaks regression joins on param/slug changes | Never for regression tracking; OK for a single-snapshot-only mode |
| Feeding full Lighthouse JSON / HTML to the LLM | Less prompt engineering | Token cost blowup; slower; more hallucination surface | Never at scale; only when debugging the AI prompt on one page |
| Unbounded crawl (no max-pages/depth) | Simpler crawler | Trap-induced runaway, site hammering | Never on unknown sites; only on a known small fixed URL list |
| Scraping Debug Toolbar on prod | Reuses existing manual workflow | Security hole (SQL exec vuln, DEBUG=True) | Never in production; staging-only with auth gating |
| System Chrome (auto-updating) | Zero setup | Silent result drift + version-mismatch crashes | Acceptable for casual local use; pin a build for any comparable trend data |

---

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Lighthouse / headless Chrome | Running many audits in parallel on one host | Bounded pool; sequential for comparable numbers; pin browser+LH versions; record them |
| Google Sheets API | Per-row writes; ignoring 429 | `batchUpdate`; exponential backoff; map by header name; write to new tab |
| Google service account | Broad scopes; committed key file | Least-privilege scope to one sheet; gitignored key; rotate on exposure |
| Django backend metrics | Enabling Debug Toolbar/Silk in prod | Dedicated auth'd metrics endpoint or staging; never DEBUG=True in prod |
| robots.txt | Ignoring it, or ignoring Crawl-delay | Parse and honor Disallow + Crawl-delay; treat disallowed paths as trap hints |
| Target site (auth'd) | Crawling logout/delete links; admin account | URL denylist; read-only account; GET-only; session-liveness checks |
| Anthropic/LLM API | Per-page calls with bloated prompts | Distilled-metric prompts; skip unchanged pages; cost estimate up front |

---

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Headless Chrome concurrency | OOM, zombie processes, crashes, *and* skewed numbers | Small bounded pool; reap processes; sequential for comparable runs | A few dozen+ pages with high parallelism on a modest host |
| Crawl-time AI per page | Slow runs, large bill | Distilled prompts; analyze only changed pages; opt-in | Hundreds of pages × frequent runs |
| Per-row Sheets export | 429, partial export | Batch writes + backoff | >~60 rows in under a minute |
| URL-space explosion | Crawl never converges; duplicate URLs | Caps + canonicalization + trap heuristics | Any site with calendars, facets, or session-param links |
| Storing full artifacts per run | Disk/quota bloat over many runs | Retention policy; store distilled metrics + linked artifacts | Many runs × many pages × full Lighthouse JSON/HTML |

---

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Enabling Debug Toolbar/Silk in prod for metrics | Arbitrary SQL exec, settings/secret exposure | Never; use auth'd endpoint or staging |
| Crawling with an admin/destructive account | Data deletion/mutation on target | Read-only least-privilege account; GET-only; denylist |
| Committing/logging credentials, cookies, service-account key | Account/site compromise | Gitignored secrets; scrub artifacts; never log; rotate on exposure |
| Following destructive GET action links | Unintended state changes on owned site | URL pattern denylist before fetch |
| Saving session cookies in plaintext artifacts | Live-credential leak | Treat cookies as passwords; exclude from outputs; delete after run |
| Over-scoped Google service account | Broad Drive/Sheets access if key leaks | Scope to the single target spreadsheet |

---

## "Looks Done But Isn't" Checklist

- [ ] **Regression tracking:** Often missing multi-run/median + variance thresholds — verify it does NOT flag changes on an unchanged site across two runs.
- [ ] **Authenticated crawl:** Often missing logout/destructive-link denylist + session-liveness check — verify a crawl can't log itself out or delete data, and detects session loss.
- [ ] **Crawler:** Often missing max-pages/depth/time caps + URL canonicalization — verify it terminates and dedups on a site with a calendar or faceted nav.
- [ ] **Metrics capture:** Often missing version/environment stamping — verify each run records Chrome + Lighthouse versions and throttling config.
- [ ] **INP/CLS:** Often mislabeled — verify lab numbers are honestly labeled (TBT, load-time CLS), not presented as field INP.
- [ ] **Error handling:** Often missing — verify 429/503/timeout pages are tagged as errors and excluded from metrics, not recorded as zeros/slow.
- [ ] **Sheets export:** Often missing batch + backoff + header mapping — verify a large export doesn't 429 and doesn't clobber the manual baseline sheet.
- [ ] **AI analysis:** Often ungrounded — verify suggestions cite specific captured metrics, and that unchanged pages are skipped to control cost.
- [ ] **Backend metrics:** Often requires DEBUG/prod debug UI — verify the chosen mechanism is production-safe and fully optional.
- [ ] **Secrets:** Often leaked into artifacts/logs — verify HTML/JSON/CSV outputs and logs contain no credentials, cookies, or keys.

---

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Noisy regressions (single-run) | MEDIUM | Add multi-run/median + variance thresholds; re-baseline; historical single-run data is suspect — flag it |
| Crawled a destructive link on owned site | HIGH | Restore from backup; add denylist; rotate account; audit what was mutated |
| IP-banned / rate-limited | MEDIUM | Wait out / request unban; add backoff + politeness + identifiable UA before retrying |
| Debug Toolbar left on in prod | HIGH | Disable immediately; rotate exposed secrets; audit access logs; redesign metrics access |
| Sheets quota exhausted / partial write | LOW | Backoff and resume; switch to batched writes; idempotent re-run |
| AI cost blowup | LOW | Make AI opt-in; distill prompts; skip unchanged pages going forward |
| Page-identity mismatch in trends | MEDIUM | Introduce canonical page key; re-key historical runs where possible; mark un-rekeyable runs |
| Headless Chrome crashes/leaks | LOW-MEDIUM | Lower concurrency; reap processes; pin browser version; add per-audit timeouts |

---

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase (topic) | Verification |
|---------|--------------------------|--------------|
| 1. Measurement variance | Metrics capture + Regression tracking | Two runs of an unchanged site produce no false regressions |
| 2. Logout/destructive link crawl | Authenticated crawl | Crawl on owned site can't log out or mutate data; session loss detected |
| 3. Infinite URL space | Crawler core | Crawl of a calendar/facet site terminates and dedups |
| 4. Hammering / IP ban | Crawler core + Metrics capture | Backoff on 429/503; errors excluded from metrics; polite defaults |
| 5. Headless resource/version drift | Metrics capture | No orphan Chrome; versions stamped; bounded concurrency |
| 6. Debug Toolbar/Silk in prod | Backend metrics | Metrics path works with DEBUG=False; no debug UI exposed |
| 7. Unstable page identity | Regression tracking | Per-page diff survives param/slug changes; added/removed pages reported |
| 8. Ungrounded AI advice | AI analysis | Each suggestion references a captured metric |
| 9. AI cost blowup | AI analysis | Unchanged pages skipped; distilled prompts; cost estimate shown |
| 10. Sheets rate limit | Export | Large export uses batch + backoff, no 429 |
| 11. Sheets schema drift | Export | Writes by header name to a tool-owned tab; baseline untouched |
| 12. INP/CLS lab limits | Metrics capture | Lab metrics honestly labeled (TBT / load-time CLS) |
| 13. Secret handling | Auth + Export (cross-cutting) | No secrets in repo, logs, or artifacts |
| 14. CSRF/login mechanics | Auth | Login verified before crawl; CSRF/cookies handled |
| 15. Missing environment context | Metrics capture + Regression tracking | Env metadata stamped; cross-env comparisons warned |

---

## Sources

- Lighthouse variability (official): https://github.com/GoogleChrome/lighthouse/blob/main/docs/variability.md — HIGH (median of 5 runs 2x more stable than 1; simulated throttling; ≥2 cores; no concurrent runs on same machine)
- DebugBear, reducing Lighthouse variance: https://www.debugbear.com/docs/reduce-lighthouse-variance — MEDIUM
- DebugBear, lab vs field data: https://www.debugbear.com/blog/lighthouse-lab-data-not-matching-field-data — MEDIUM
- Search Engine Journal, why Lighthouse doesn't include INP: https://www.searchenginejournal.com/why-google-lighthouse-doesnt-include-inp-a-core-web-vital/528734/ — MEDIUM (INP is field-only; TBT is lab proxy)
- Search Engine Journal, crawler traps: https://www.searchenginejournal.com/crawler-traps-causes-solutions-prevention/305781/ — MEDIUM
- Google Search Central, faceted navigation crawling: https://developers.google.com/search/docs/crawling-indexing/crawling-managing-faceted-navigation — HIGH
- Screaming Frog / SEO Spider authenticated-crawl guidance on logout & destructive links (via Inbound Found / Microsys): https://inboundfound.com/crawl-and-scrape-sites-that-require-your-login/ , https://www.microsystools.com/products/website-scraper/help/website-scan-login/ — MEDIUM (read-only account; block logout/admin; treat cookies as live credentials)
- Django security release re: Debug Toolbar (SQL exec vuln): https://www.djangoproject.com/weblog/2021/apr/14/debug-toolbar-security-releases/ — HIGH
- django-silk (overhead/prod warnings): https://github.com/jazzband/django-silk — MEDIUM
- ADHDecode, Debug Toolbar production-safe: https://adhdecode.com/articles/django/django-debug-toolbar-production-safe/ — LOW/MEDIUM
- Google Sheets API usage limits (official): https://developers.google.com/workspace/sheets/api/limits — HIGH (per-minute quotas, service-account shares one bucket, backoff, 2026 billing note)
- Firecrawl glossary, polite crawling & 429: https://www.firecrawl.dev/glossary/web-crawling-apis/what-is-polite-crawling , https://www.firecrawl.dev/glossary/web-scraping-apis/what-is-429-error-web-scraping — MEDIUM (Crawl-delay, Retry-After, backoff)
- Browserless, parallel Lighthouse / headless at scale: https://www.browserless.io/blog/parallel-lighthouse-tests , https://www.browserless.io/blog/headless-chrome — MEDIUM (one audit per process; memory leaks, zombies, version drift)
- lighthouse-ci troubleshooting (version mismatch): https://googlechrome.github.io/lighthouse-ci/docs/troubleshooting.html — MEDIUM
- Masterofcode, reducing LLM hallucinations via grounding: https://masterofcode.com/blog/hallucinations-in-llms-what-you-need-to-know-before-integration — MEDIUM (grounding reduces hallucination; input tokens dominate cost)
- OWASP Session Management Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html — HIGH (session/cookie handling)

---
*Pitfalls research for: website performance auditing & crawling tool*
*Researched: 2026-05-25*
