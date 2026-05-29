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
lives as a sibling of `src/` and is **not bundled into the wheel** — a `pip install
perfcrawl` would leave the worker resolution path broken (`__file__`'s
`parents[2]` becomes the Python `lib/` directory, where `lighthouse-worker/run.mjs`
does not exist).

Until Phase 3 makes the worker location configurable (`PERFCRAWL_WORKER_DIR` env
variable / CLI flag), run from a clone of the repository via `uv run`:

```bash
git clone <repo>
cd performance-statistics-gathering
uv sync                                     # Python deps
cd lighthouse-worker && npm ci && cd ..     # Node worker (requires Node >=22.19)
uv run playwright install chromium          # Browser binary

uv run python -m perfcrawl.cli measure https://example.com/
```

## Development

```bash
uv sync                 # install runtime + dev dependencies
uv run pytest -x -q     # run the test suite
uv run ruff check .     # lint
uv run ruff format .    # format
```

Requires Python 3.12+. See `CLAUDE.md` for the full technology stack rationale.
