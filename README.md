# PerfCrawl

General-purpose website performance auditing tool. The end goal is one command
that crawls a site, measures frontend performance on every reachable page, lets
AI explain what it finds, and writes results to the output formats you choose.

> **Replace the slow manual per-page audit** — open a page, run Lighthouse, read
> the Network tab and Django Debug Toolbar, hand-fill a spreadsheet — with a
> single CLI invocation that produces consistent, machine-readable data.

## Current capabilities (v0.1.0)

PerfCrawl can audit **a single URL**, **crawl a whole site end-to-end**, and
**crawl behind a login** today. AI analysis and Google Sheets output are
scheduled for later milestones (see [Roadmap](#roadmap)).

What works **right now**:

- **`perfcrawl crawl <url>`** — discovers same-origin pages (BFS over `<a href>`
  links, augmented from `sitemap.xml`), respects `robots.txt` and per-host
  politeness, provably terminates against crawler traps, then measures every
  in-scope page into one multi-page run. See [Crawl a whole site](#crawl-a-whole-site).
- **Authenticated crawling** — log in once (driven form login or a saved
  session from `perfcrawl login`) and audit pages behind authentication;
  Lighthouse inherits the session without resetting storage. The crawler is
  *structurally incapable* of logging itself out or mutating the target: an
  always-on denylist blocks destructive/session-ending links before every fetch,
  and it aborts (never silently captures logged-out pages) on mid-crawl session
  loss. Credentials enter via env only, never argv, and never reach any log,
  artifact, or committed file. See [Crawl behind a login](#crawl-behind-a-login).
- **`perfcrawl measure <url>`** — runs a real Lighthouse 13.3.0 audit against a
  Playwright-launched Chrome attached over CDP, takes N samples, and reports the
  median.
- **Frontend metrics captured per page:** Lighthouse Performance / A11y / SEO /
  Best-Practices scores, lab CWV (LCP, CLS, **TBT as the labeled INP proxy** —
  see note), TTFB, request count, total bytes, slowest request URL + time, and
  HTTP status code.
- **Outputs:** Rich terminal table (default), machine-readable JSON (`--json`),
  per-run `result.json` + `result.csv` + raw Lighthouse `report.json` /
  `report.html`, plus an SQLite history store (`perfcrawl.db`).
- **Median of N samples** with a finite-guard and honest-empty handling — every
  measurement reports both the median and the full sample distribution.
- **Mobile or desktop emulation** (`--emulation mobile|desktop`) with the
  matching simulated throttling profile.
- **Predictable exit codes**: `0` success, `1` user error, `2` measurement
  error, `3` auth error (login/session failure). No stack traces leak to the
  terminal under normal failure modes.

> **INP caveat (important):** INP is a *field* metric requiring real user
> interaction; it is **not** reliable in a headless lab pass. PerfCrawl reports
> Lighthouse's **Total Blocking Time as a labeled lab proxy** (`INP (lab proxy,
> TBT-based)` in the table; `inp_proxy_tbt_ms` in JSON / CSV) — never as a bare
> `inp` field. Real CrUX field INP for public pages is planned for the output
> phase (Phase 6).

## Prerequisites

| Requirement     | Version  | Why                                                  |
| --------------- | -------- | ---------------------------------------------------- |
| Python          | ≥ 3.12   | Orchestrator + CLI                                   |
| Node.js         | ≥ 22.19  | Hard requirement of `lighthouse@13.3.0`              |
| `uv`            | latest   | Python dep + venv management                         |
| `npm`           | bundled  | One-time worker install                              |
| Playwright Chromium | auto | Installed via `playwright install chromium`          |

Check Node first — Lighthouse 13 will refuse to start on Node 22.18 or older:

```bash
node --version    # must print v22.19.x or newer
```

## Install

PerfCrawl is currently a **repo-checkout-only tool**. The Node
`lighthouse-worker/` sibling is not bundled into the Python wheel, so
`pip install perfcrawl` would not be able to find it. `preflight()` raises an
actionable `MeasurementError` if you hit that path.

```bash
git clone git@github.com:jkl-101/PerfCrawl.git
cd PerfCrawl

uv sync                                      # Python deps
cd lighthouse-worker && npm ci && cd ..      # Node worker (Lighthouse 13.3.0)
uv run playwright install chromium           # Browser binary
```

A later phase will lift the repo-checkout-only restriction (bundling or a
configurable worker directory), at which point an installable wheel becomes a
supported deployment.

## Quickstart

Measure any public URL — three samples, mobile emulation, defaults:

```bash
uv run perfcrawl measure https://example.com/
```

You'll see a Rich table titled `perfcrawl: https://example.com/` with rows
for Performance, Accessibility, SEO, Best Practices, LCP (ms), CLS,
`INP (lab proxy, TBT-based)`, TTFB (ms), Requests, Total bytes, Slowest
request, and Status code, followed by:

```
(median of 3) · written to output/<run_id>
```

Inside `output/<run_id>/` you'll find:

```
output/<run_id>/
├── result.json                        # Full-fidelity RunRecord (round-trips byte-identically)
├── result.csv                         # Flat-row export with locked column schema
├── perfcrawl.db                       # SQLite run store (history)
└── lighthouse/
    └── <page-slug>.json               # Raw Lighthouse JSON artifact
    └── <page-slug>.html               # Raw Lighthouse HTML report (the one you'd archive)
```

### Flags

```bash
perfcrawl measure URL \
  --samples 3 \
  --emulation mobile \
  --json \
  --output-dir ./audits
```

| Flag                | Default  | Notes                                                                  |
| ------------------- | -------- | ---------------------------------------------------------------------- |
| `--samples / -n`    | `3`      | Number of Lighthouse runs; median is reported, distribution preserved. |
| `--emulation`       | `mobile` | `mobile` or `desktop` (changes throttling + screen emulation).         |
| `--json`            | off      | Emit machine-readable `RunRecord` JSON to stdout (no Rich box chars).  |
| `--output-dir`      | `output` | Per-run artifacts land under `<output-dir>/<run_id>/`.                 |

### Scripting against the JSON output

`--json` is safe to pipe — there's nothing else on stdout in that mode:

```bash
uv run perfcrawl measure https://example.com/ --samples 5 --json \
  | jq '.pages[0] | {perf_score, lcp: .lcp_ms.median, ttfb: .ttfb_ms.median, requests, total_bytes}'
```

### Exit codes

- `0` — successful measurement.
- `1` — user error (empty URL, bad `--emulation` value, etc.). Actionable
  message on stderr.
- `2` — measurement error (worker crash, Chrome launch timeout, all samples
  failed). Actionable message on stderr, no raw traceback.

## Crawl a whole site

`perfcrawl crawl <url>` discovers and measures an entire site from a single seed:

```bash
uv run perfcrawl crawl https://example.com/
```

It follows same-origin `<a href>` links breadth-first (seeded/augmented from
`sitemap.xml` and `robots.txt` `Sitemap:` entries), drops out-of-scope and
cross-domain links, obeys `robots.txt`, throttles per host, and measures each
in-scope page through the same Lighthouse seam as `measure`. The result is **one
multi-page run** — an aggregated one-row-per-page `result.csv` / `result.json`
plus per-page raw Lighthouse artifacts under `output/<run_id>/`.

Three independent bounds guarantee termination even on calendar/facet traps:
`--max-pages`, `--max-depth`, and a per-base-path query-variant cap.

> **Always dry-run first** to preview scope without measuring anything:
>
> ```bash
> uv run perfcrawl crawl https://example.com/ --dry-run
> ```
>
> This prints the in-scope URLs (and any error-tagged ones) and exits — no Chrome
> launched, nothing written.

A typical bounded, pattern-filtered crawl:

```bash
uv run perfcrawl crawl https://example.com/ \
  --max-pages 50 \
  --max-depth 2 \
  --concurrency 4 \
  --include '*/blog/*' \
  --exclude '*/tag/*' \
  --json --output-dir ./audits
```

### Crawl flags

| Flag                   | Default  | Notes                                                                          |
| ---------------------- | -------- | ------------------------------------------------------------------------------ |
| `--max-pages`          | `100`    | Stop after this many in-scope pages (enqueue bound).                            |
| `--max-depth`          | `3`      | BFS depth bound; sitemap seeds are depth 0.                                     |
| `--concurrency`        | `2`      | Worker-pool size — one independent Chrome per worker.                           |
| `--delay`              | `0.5`    | Minimum inter-request delay (s) per host; robots `Crawl-delay` wins if stricter. |
| `--samples / -n`       | `1`      | Lighthouse samples per page (crawl defaults to 1; median reported).             |
| `--include`            | (all)    | Glob to include, repeatable; no `--include` means all in-scope.                 |
| `--exclude`            | (none)   | Glob to exclude, repeatable; exclude wins over include.                         |
| `--include-subdomains` | off      | Treat sibling subdomains of the seed host as in-scope.                          |
| `--no-sitemap`         | off      | Skip `sitemap.xml` / robots `Sitemap:` seeding.                                 |
| `--ignore-robots`      | off      | **Owned sites only** — bypass `robots.txt` (emits a loud stderr warning).       |
| `--dry-run`            | off      | Discover only: print in-scope URLs + error tags, measure nothing.              |
| `--emulation`          | `mobile` | `mobile` or `desktop` form factor.                                             |
| `--output-dir`         | `output` | Per-run artifacts land under `<output-dir>/<run_id>/`.                          |
| `--json`               | off      | Emit the full multi-page `RunRecord` JSON to stdout.                            |

> **Discovery limitation (D-02):** only static `<a href>` links are followed —
> JavaScript-rendered navigation and `javascript:`-scheme links are **not**
> discovered. Seed such pages directly or via `sitemap.xml`.

### Exit codes (crawl)

Same contract as `measure`: `0` success, `1` user error, `2` measurement error.
A crawl that discovers pages but measures **zero** of them (all errored, or
nothing in scope) exits `2` — a silent zero-data crawl is never reported as
success.

## Crawl behind a login

PerfCrawl can audit pages behind authentication — dashboards, account pages,
anything that requires a session. It logs in **once**, captures the session, and
reuses it for every page audit; Lighthouse inherits the authenticated session
over the shared CDP context without resetting storage.

Two safety invariants hold no matter what you point it at:

1. **It cannot mutate the target.** An always-on denylist blocks
   destructive/session-ending links (`logout`, `delete`, `destroy`, `remove`,
   `admin`, `archive`, `disable`, …) **before every fetch** — a denied URL never
   even consumes a crawl slot. The crawler only ever follows safe `GET` links.
2. **It never silently captures logged-out pages.** It watches for session loss
   mid-crawl (a `401`/`403`, or a redirect back to the login page) and **aborts**
   — flushing the pages it had already measured as a tagged partial run with a
   loud warning, rather than recording logged-out content as if it were real.

### Credentials never touch argv

Credentials are read from the **environment only** — never a flag (argv is
visible in `ps` and shell history):

```bash
export PERFCRAWL_USERNAME='you@example.com'
export PERFCRAWL_PASSWORD='…'        # or put both in a gitignored .env
```

The username/password (and any captured session) are scrubbed from **every**
sink — stderr, error traces, `result.json`, `result.csv`, and the raw Lighthouse
artifacts — so no credential ever lands in a log, an output file, or a committed
file.

### Option A — driven form login

Point the crawler at the login form and give it CSS selectors for the fields. It
fills them from the env credentials, submits, and crawls with the resulting
session:

```bash
uv run perfcrawl crawl https://app.example.com/ \
  --login-url   https://app.example.com/login/ \
  --user-sel    'input[name="username"]' \
  --pass-sel    'input[name="password"]' \
  --submit-sel  'button[type="submit"]'
```

If a successful login returns `200` on the login page instead of redirecting
(so the URL alone can't confirm success), add a marker:
`--success-text 'Dashboard'` or `--success-url '/dashboard/'`.

### Option B — saved session (SSO / MFA escape hatch)

Some logins can't be scripted — OAuth popups, 2FA prompts, CAPTCHAs. For those,
log in **by hand once** in a visible browser and save the session:

```bash
uv run perfcrawl login https://app.example.com/login/ --out app.authstate.json
# A real Chrome window opens — log in manually, then press Enter to capture.
```

Then replay that session on as many crawls as you like:

```bash
uv run perfcrawl crawl https://app.example.com/ --auth-state app.authstate.json
```

> The saved `*.authstate.json` **is a live logged-in session** — a
> credential-equivalent secret. `*.authstate.json` is gitignored; keep it out of
> version control. `--auth-state` and `--login-url` are mutually exclusive
> (supply a saved session **or** drive a form login, not both).

### Extending the denylist

The built-in denylist is always on; `--deny` *adds* to it (it never replaces
it), repeatable:

```bash
uv run perfcrawl crawl https://app.example.com/ \
  --auth-state app.authstate.json \
  --deny purchase --deny cancel-subscription
```

### Auth flags

| Flag             | Notes                                                                                  |
| ---------------- | -------------------------------------------------------------------------------------- |
| `--login-url`    | Form-login URL; drives a login with `--user-sel`/`--pass-sel`/`--submit-sel`.          |
| `--user-sel`     | CSS selector for the username field.                                                   |
| `--pass-sel`     | CSS selector for the password field.                                                   |
| `--submit-sel`   | CSS selector for the submit control.                                                   |
| `--auth-state`   | Path to a saved session JSON from `perfcrawl login`. Mutually exclusive with `--login-url`. |
| `--success-text` | Login-success marker text (for the `200`-logged-in edge case).                         |
| `--success-url`  | Login-success landing-URL fragment.                                                    |
| `--deny`         | Extra destructive-link deny substring; repeatable; **extends** the built-in denylist.  |

> Credentials are **not** flags — they come from `PERFCRAWL_USERNAME` /
> `PERFCRAWL_PASSWORD` in the environment (or a gitignored `.env`).

### Exit codes (auth)

In addition to the `measure`/`crawl` contract, authenticated crawls add `3` —
**auth error**: login failed, the session couldn't be resolved, or session loss
aborted the crawl. As everywhere, the message on stderr is scrubbed and carries
no raw traceback.

## Roadmap

PerfCrawl is being built in vertical slices — each phase is a real,
shippable increment.

| Phase | Status | What it adds                                                              |
| ----- | ------ | -------------------------------------------------------------------------- |
| 1     | ✓      | Data model + SQLite store + RunDelta engine + canonical URL key            |
| 2     | ✓      | `perfcrawl measure <url>` — single-URL end-to-end                          |
| 3     | ✓      | `perfcrawl crawl <url>` — link + sitemap discovery, robots.txt, politeness |
| 4     | ✓      | Authenticated crawls — login once, reuse session, denylist destructive links (you are here) |
| 5     |        | AI analysis — per-page Observation / Cause / Suggested Optimization        |
| 6     |        | Google Sheets output, run-over-run regression flagging, all output formats |

Phase 2 also delivered median-of-N early so Phase 6's regression flagging
stands on stable data from day one. Backend internals for owned Django sites
are deferred to v2 behind a dedicated security spike.

## Development

```bash
uv sync                  # install runtime + dev dependencies
uv run pytest -x -q      # run the default suite (excludes e2e marker)
uv run pytest -m e2e     # run the real-network smoke (needs Node + Chrome + internet)
uv run ruff check .      # lint
uv run ruff format .     # format
```

Requires Python 3.12+. See `CLAUDE.md` for the full technology stack rationale
and ADRs.
