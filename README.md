# PerfCrawl

General-purpose website performance auditing tool. Point it at any website and it
crawls the site by following internal links, measures frontend performance on every
reachable page, uses AI to explain what it finds, and writes the results to whatever
output formats you choose.

## Status

Early development. Phase 1 establishes the canonical data contract (typed result
model, SQLite run store, RunDelta engine, canonical URL key) that every later
component targets.

## Development

```bash
uv sync                 # install runtime + dev dependencies
uv run pytest -x -q     # run the test suite
uv run ruff check .     # lint
uv run ruff format .    # format
```

Requires Python 3.12+. See `CLAUDE.md` for the full technology stack rationale.
