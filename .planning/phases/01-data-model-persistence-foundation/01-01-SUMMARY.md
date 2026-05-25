---
phase: 01-data-model-persistence-foundation
plan: 01
subsystem: scaffold + registry + canonicalization
tags: [scaffold, uv, registry, canonicalization, url-key, tdd]
requires: []
provides:
  - "uv project scaffold (src/perfcrawl/ package, pytest + ruff configured)"
  - "registry.TRACKING_PARAM_DENYLIST (D-04) — one editable denylist"
  - "registry.Polarity + registry.METRIC_POLARITY (D-09) — one editable polarity table"
  - "canonical.canonical_key(url) — cross-run page-identity key (D-01..D-05)"
affects:
  - "Plan 02 (model + store) imports canonical_key to set PageResult.url_key on write"
  - "Plan 03 (RunDelta engine) imports METRIC_POLARITY/Polarity to derive direction"
tech-stack:
  added:
    - "uv 0.11.16 (dependency/venv manager)"
    - "pydantic 2.13.4 (runtime dep, model layer for Plan 02)"
    - "w3lib 2.4.1 (runtime dep — URL canonicalization; human-verify gate APPROVED)"
    - "pytest 9.0.3 (dev)"
    - "ruff 0.15.14 (dev)"
  patterns:
    - "src/ layout with pythonpath=[src] in [tool.pytest.ini_options]"
    - "one-editable-place registry tables (denylist + polarity) consumed by call sites"
    - "thin w3lib wrapper: library does RFC-3986; wrapper adds the rules it omits"
    - "TDD RED (test commit) -> GREEN (impl commit) for the canonical slice"
key-files:
  created:
    - "pyproject.toml"
    - "uv.lock"
    - ".python-version"
    - ".gitignore"
    - "README.md"
    - "src/perfcrawl/__init__.py"
    - "src/perfcrawl/registry.py"
    - "src/perfcrawl/canonical.py"
    - "tests/test_canonical.py"
    - "tests/fixtures/.gitkeep"
  modified: []
decisions:
  - "w3lib human-verify supply-chain gate (Task 1, threat T-01-SC) APPROVED by human before install; w3lib>=2.3,<3 added as runtime dep (NOT the stdlib fallback)."
  - "Removed the uv-generated CLI entry-point stub (perfcrawl:main) — Phase 1 is library-only; no Typer/CLI scaffolding per phase boundary."
  - "requires-python pinned to >=3.12 (CLAUDE.md 'Python 3.12+'), not the auto-generated >=3.14."
  - "Round-trip identity semantics (A3) deferred to Plan 02 — not in scope for this plan."
metrics:
  duration_min: 2
  completed: "2026-05-25"
  tasks_completed: 3
  files_created: 10
  tests_passing: 17
---

# Phase 1 Plan 01: Scaffold + Registry + Canonical URL Key Summary

Greenfield PerfCrawl scaffolded with uv (src/perfcrawl/ package, pytest + ruff), the two "one editable place" registry tables (D-04 tracking-param denylist, D-09 metric polarity) established, and the canonical URL key slice (`canonical_key`, criterion #4 / D-01..D-05) shipped TDD-first with 17 green tests — including same-page collapse, no-over-merge of distinct pages, and never-raising malformed-input safety.

## What Was Built

**Task 1 — w3lib supply-chain gate (checkpoint:human-verify, blocking-human):**
The w3lib legitimacy gate (threat T-01-SC) was **APPROVED by the human before install**. w3lib 2.4.1 (official Scrapy org, on PyPI since 2010, provides `canonicalize_url` + `url_query_cleaner`) was added as the runtime canonicalization engine via `w3lib>=2.3,<3`. The documented stdlib `urllib.parse` fallback was therefore NOT used. Install never happened before the approval.

**Task 2 — uv project scaffold + registry tables:**
- `uv init --package` produced the `src/perfcrawl/` layout; `pyproject.toml` rewritten with runtime deps (`pydantic>=2.10,<3`, `w3lib>=2.3,<3`), dev deps (`pytest>=8,<10`, `ruff>=0.15,<0.16`), `[tool.pytest.ini_options]` (testpaths, `pythonpath=[src]`) and `[tool.ruff]`.
- `uv sync` resolved + installed all 13 packages and wrote `uv.lock` (exact versions: pydantic 2.13.4, w3lib 2.4.1, pytest 9.0.3, ruff 0.15.14).
- `registry.py` exports the two locked tables: `TRACKING_PARAM_DENYLIST` (14 tracking keys, D-04) and `Polarity` StrEnum + `METRIC_POLARITY` dict (lower-is-better metrics + higher-is-better scores, D-09).

**Task 3 — canonical URL key slice (TDD, criterion #4):**
- RED: `tests/test_canonical.py` (12 test functions / 17 cases) written first and confirmed failing (`ModuleNotFoundError`).
- GREEN: `canonical.py` implements `canonical_key(url)` = `url_query_cleaner` (drop denylisted tracking params, D-04) -> `canonicalize_url` (lowercase scheme+host, uppercase %-hex, sort query, drop fragment, D-02/D-04/D-05) -> wrapper rules w3lib omits (strip default ports `:80`/`:443`, D-02; strip trailing slash except root, D-03). Malformed/non-URL input returns deterministically without raising (T-01-01). The denylist is imported from `registry.py` (one editable place — never inlined).

## Verification Evidence

| Check | Command | Result |
|-------|---------|--------|
| Full suite | `uv run pytest -x -q` | 17 passed |
| Variants collapse | `pytest tests/test_canonical.py::test_variants_collapse -x` | passed |
| No over-merge | `pytest tests/test_canonical.py::test_no_over_merge -x` | passed |
| Registry constants | inline import + polarity asserts | `ok` |
| Denylist not inlined | `grep "from perfcrawl.registry import TRACKING_PARAM_DENYLIST"` | found |
| Malformed input safe | `canonical_key('not a url'); canonical_key('')` | exit 0 (no raise) |
| Package importable | `python -c "import perfcrawl"` | ok |
| pyproject tables | `grep tool.pytest.ini_options` / `grep tool.ruff` | both present |
| Lint | `uv run ruff check src/ tests/` | All checks passed |

## TDD Gate Compliance

- RED gate: `test(01-01): add failing tests for canonical_key` (13ef4b9) — confirmed failing before implementation.
- GREEN gate: `feat(01-01): implement canonical_key URL derivation` (3a30e92) — after RED.
- No REFACTOR commit needed (implementation was minimal and clean; ruff passed).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 / A2 - Missing sub-rule] w3lib does NOT strip default ports**
- **Found during:** Task 3 (execution-time probe of w3lib per Assumption A2).
- **Issue:** Research flagged A2 — `canonicalize_url` was *assumed* to satisfy all D-02 sub-rules. A direct probe showed it leaves `:80`/`:443` in the netloc (e.g. `http://x.com:80/p` unchanged).
- **Fix:** Added `_strip_default_port()` to the canonical wrapper so `:80` (http) / `:443` (https) are removed, while non-default ports (`:8443`) are preserved as identity.
- **Files modified:** `src/perfcrawl/canonical.py`
- **Commit:** 3a30e92
- **Note:** The probe also confirmed w3lib DOES uppercase percent-hex and sort the query (those needed no wrapper code), and does NOT raise on the malformed inputs — but the defensive try/except wrapper was kept anyway (T-01-01 belt-and-suspenders).

### Auto-added critical functionality

**2. [Rule 2 - Security/correctness] Malformed-input safety wrapper (T-01-01)**
- **Issue:** `canonical_key` parses untrusted URL strings (future crawl/fixture origin). The threat register (T-01-01, Denial of Service) requires it never crash on non-URL/empty input.
- **Fix:** Wrapped the transform in try/except returning a deterministic `(url or "").strip()`; added parametrized malformed-input tests.
- **Files modified:** `src/perfcrawl/canonical.py`, `tests/test_canonical.py`
- **Commit:** 13ef4b9 (test), 3a30e92 (impl)

**3. [Rule 2 - Correctness] .gitignore added**
- **Issue:** `uv init` did not emit a `.gitignore`; without one, `.venv/`, `__pycache__/`, and the future SQLite `*.db` store (Security Domain V7/V8 — will hold real data in Phase 2+) would be committable.
- **Fix:** Added `.gitignore` covering Python/uv/tooling caches and `*.db`/`*.sqlite`.
- **Commit:** 5aa4222

### Scope reductions (kept inside Phase 1 library-only boundary)

**4. [Rule 3 - Blocking/scope] Removed uv-generated CLI entry point**
- **Issue:** `uv init --package` generated `[project.scripts] perfcrawl = "perfcrawl:main"` plus a `main()` stub. The plan forbids CLI/Typer scaffolding (Phase 1 is library-only).
- **Fix:** Removed the `[project.scripts]` table and the `main()` stub; `__init__.py` is now a clean library docstring + `__version__`.
- **Commit:** 5aa4222

**5. [Rule 3 - Blocking] requires-python corrected**
- **Issue:** `uv init` auto-set `requires-python = ">=3.14"` from the local interpreter; CLAUDE.md and research target "Python 3.12+".
- **Fix:** Set `requires-python = ">=3.12"` and `target-version = "py312"` for ruff.
- **Commit:** 5aa4222

## Environment Notes (not deviations)

- **uv was not installed** at start. Per the plan's documented install path, uv 0.11.16 (the CLAUDE.md-locked version) was installed via the official Astral installer (`astral.sh/uv/install.sh`) to `~/.local/bin` after `pip install` was blocked by the Homebrew externally-managed-environment guard (PEP 668). This was a documented prerequisite, not a package substitution.

## Interfaces Delivered (for Plans 02 / 03)

```python
# perfcrawl.registry
TRACKING_PARAM_DENYLIST: list[str]            # D-04
class Polarity(StrEnum): LOWER_IS_BETTER; HIGHER_IS_BETTER
METRIC_POLARITY: dict[str, Polarity]          # D-09

# perfcrawl.canonical
def canonical_key(url: str) -> str            # D-01..D-05
```

## Known Stubs

None. `tests/fixtures/.gitkeep` is an intentional empty-directory placeholder; fixture JSON is populated in Plan 02 (per the plan and VALIDATION.md Wave 0 list), not a stub in shipped code.

## Self-Check: PASSED

- Artifacts verified on disk: pyproject.toml, registry.py, canonical.py, test_canonical.py, __init__.py, uv.lock, .gitignore, README.md — all FOUND.
- Commits verified in git log: 5aa4222 (scaffold), 13ef4b9 (RED test), 3a30e92 (GREEN impl) — all FOUND.
- Contains checks: `tool.pytest.ini_options`, `TRACKING_PARAM_DENYLIST`, `def canonical_key`, `test_variants_collapse` — all FOUND.
