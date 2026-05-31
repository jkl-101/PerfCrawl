# PerfCrawl

General-purpose website performance auditing tool. The end goal is one command
that crawls a site, measures frontend performance on every reachable page, lets
AI explain what it finds, and writes results to the output formats you choose.

> **Replace the slow manual per-page audit** — open a page, run Lighthouse, read
> the Network tab and Django Debug Toolbar, hand-fill a spreadsheet — with a
> single CLI invocation that produces consistent, machine-readable data.

## Current capabilities (v0.1.0)

PerfCrawl can audit **one URL at a time, end-to-end** today. Site-wide crawl,
authentication, AI analysis, and Google Sheets output are scheduled for later
milestones (see [Roadmap](#roadmap)).

What works **right now**:

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
  error. No stack traces leak to the terminal under normal failure modes.

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

Phase 3 will lift the repo-checkout-only restriction via a configurable
`PERFCRAWL_WORKER_DIR` env / CLI flag, at which point an installable wheel
becomes a supported deployment.

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

### Looping over many URLs (today's workaround for the missing crawler)

```bash
while read url; do
  uv run perfcrawl measure "$url" --samples 3 --json --output-dir audits >> all.jsonl
done < urls.txt
```

This is the manual stopgap until Phase 3 lands.

## Roadmap

PerfCrawl is being built in vertical slices — each phase is a real,
shippable increment.

| Phase | Status | What it adds                                                              |
| ----- | ------ | -------------------------------------------------------------------------- |
| 1     | ✓      | Data model + SQLite store + RunDelta engine + canonical URL key            |
| 2     | ✓      | `perfcrawl measure <url>` — single-URL end-to-end (you are here)           |
| 3     |        | `perfcrawl crawl <url>` — link + sitemap discovery, robots.txt, politeness |
| 4     |        | Authenticated crawls — login once, reuse session, denylist destructive links |
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
