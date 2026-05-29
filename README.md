# PerfCrawl

General-purpose website performance auditing tool. Point it at any website and it
crawls the site by following internal links, measures frontend performance on every
reachable page, uses AI to explain what it finds, and writes the results to whatever
output formats you choose.

## Status

Early development. Phase 1 establishes the canonical data contract (typed result
model, SQLite run store, RunDelta engine, canonical URL key) that every later
component targets.

## Install / run

**PerfCrawl is currently a repo-checkout-only tool.** The Node `lighthouse-worker/`
lives as a sibling of `src/` and is **not bundled into the wheel** — a future
`pip install perfcrawl` would not ship the worker, so the runtime path resolution
(`__file__`'s `parents[2]` → repo root) would point at a directory that does not
exist. `preflight()` raises an actionable `MeasurementError` if you hit this path.

Run from a clone of the repository via `uv run`:

```bash
git clone <repo>
cd performance-statistics-gathering
uv sync                                     # Python deps
cd lighthouse-worker && npm ci && cd ..     # Node worker (requires Node >=22.19)
uv run playwright install chromium          # Browser binary

uv run perfcrawl measure https://example.com/
```

Phase 3 will make the worker location configurable (`PERFCRAWL_WORKER_DIR` env
variable / CLI flag), at which point `pip install perfcrawl` + a separate worker
install will be a supported deployment.

## Development

```bash
uv sync                 # install runtime + dev dependencies
uv run pytest -x -q     # run the test suite
uv run ruff check .     # lint
uv run ruff format .    # format
```

Requires Python 3.12+. See `CLAUDE.md` for the full technology stack rationale.
