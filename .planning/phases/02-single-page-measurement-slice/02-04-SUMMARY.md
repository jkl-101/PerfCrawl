---
phase: 02-single-page-measurement-slice
plan: 04
subsystem: cli-and-outputs
tags: [phase-2, cli, typer, rich, output-writers, csv, json, sqlite-persistence, d-05, d-06, d-07, d-15, in-02-boundary]

# Dependency graph
requires:
  - phase: 01-data-model-persistence-foundation
    provides: RunRecord / PageResult / MetricSample model_dump_json round-trip + store.init_db + store.write_run
  - plan: 02-01
    provides: perfcrawl.constants (DEFAULT_SAMPLES_N, INP_PROXY_DISPLAY_LABEL, ExitCode, ALWAYS_INCLUDE_AUDITS) + perfcrawl.slug (page_slug IN-02 boundary)
  - plan: 02-03
    provides: perfcrawl.orchestrator (measure_url, UserError, MeasurementError) — the tuple-return contract (RunRecord, dict[url_key, (reportJson, reportHtml)])
provides:
  - "perfcrawl.output — write_outputs(run_record, *, output_dir, raw_artifacts=None) -> Path; CSV_COLUMNS locked column list"
  - "perfcrawl.cli — app (Typer); measure subcommand wires orchestrator → output → store with D-15 exit-code mapping"
  - "[project.scripts] perfcrawl = 'perfcrawl.cli:app' entry point (restored from Phase 1's deliberate removal)"
  - "typer>=0.15 + rich>=13 dependencies in pyproject.toml + uv.lock"
  - "tests/test_output.py (13 tests) + tests/test_cli.py (14 tests) + tests/test_e2e.py (1 gated smoke test)"
affects:
  - "pyproject.toml — typer + rich added alphabetically; [project.scripts] restored; addopts default-deselects e2e"
  - ".gitignore — output/ added (RESEARCH § Security Domain: prevent committed URL+HTML leakage)"
  - "uv.lock — regenerated with typer/rich + transitive deps"

# Tech tracking
tech-stack:
  added:
    - "typer>=0.15 (tiangolo — RESEARCH Package Legitimacy Audit verified; mature; click-based)"
    - "rich>=13 (Textualize — RESEARCH Package Legitimacy Audit verified; mature)"
    - "(transitive) click, markdown-it-py, mdurl, pygments, rich-toolkit, shellingham, typing-inspection"
  patterns:
    - "Atomic file writes via tempfile.NamedTemporaryFile + os.replace (file-I/O analog of store.py's `with conn:` transaction)"
    - "Locked column list in module-level constant (one-editable-place — mirrors registry.py / constants.py pattern)"
    - "IN-02 boundary applied at the output writer: every page_slug-derived path is reasserted at the consumer (defense in depth above 02-01 Task 1's sanitization floor)"
    - "Typer subcommand-forcing pattern: hidden no-op sibling command alongside the real command so single-@app.command apps still dispatch as 'verb' (avoids Typer's implicit-root collapse)"
    - "Default-deselect a pytest marker via addopts `-m 'not <marker>'` so opt-in tests are excluded from CI without per-call -m flags"
    - "Docstring-paraphrase pattern for forbidden-token grep guards: never quote the forbidden literal in source comments (3rd occurrence in Phase 2; documented in this plan's Deviation 3 and previously in 02-01 dev 2 + 02-03 dev 1)"

key-files:
  created:
    - src/perfcrawl/output.py
    - src/perfcrawl/cli.py
    - tests/test_output.py
    - tests/test_cli.py
    - tests/test_e2e.py
  modified:
    - pyproject.toml
    - .gitignore
    - uv.lock

key-decisions:
  - "DB path = output_dir / 'perfcrawl.db' — colocate the SQLite history store with its artifacts. Rationale: per-developer artifact location is already configurable via --output-dir; reusing it for the DB keeps a single cleanup point and means a fresh --output-dir run starts with no history (helpful for reproducible smoke runs). Trade-off: the Phase 1 LEARNINGS contemplated a global ~/.perfcrawl/store.db; that's a Phase 6 decision and can be layered on top via a config flag."
  - "Subcommand-forcing via hidden _internal command. Rationale: D-05 mandates 'perfcrawl measure <url>' (the verb), not bare 'perfcrawl <url>'. Typer collapses a single-@app.command app to the implicit-root form; a hidden no-op sibling restores the verb-dispatching shape. Forward-compat: Phase 3 'crawl' / Phase 6 'budget' siblings will naturally replace the hidden _internal entry."
  - "addopts `-m 'not e2e'` for default-deselection of the e2e marker. Rationale: plan's <done> criteria explicitly requires the default `uv run pytest` to exclude the e2e test (it needs Node + Chrome + network). Marker REGISTRATION (Phase 2 plan 01) is necessary but not sufficient — pytest treats unregistered markers as warnings, not deselections. The `-m 'not e2e'` addopt is the configuration-level fix; `-m e2e` overrides cleanly when opted in."
  - "raw_artifacts parameter shape on write_outputs: dict[url_key, (reportJson, reportHtml)] matches the orchestrator's tuple-return side-channel exactly. Rationale: avoids any transformation between producer and consumer; the CLI is a literal forwarder of the dict. This is the HIGH-1 plan-check fix's natural continuation — the contract locked in 02-03 flows straight into 02-04's signature."
  - "CSV writer builds in-memory StringIO then atomic-writes. Rationale: csv.DictWriter against an opened file would hold a write window where result.csv exists but is incomplete; building in memory + os.replace gives the same one-shot-or-nothing semantics as result.json. Same pattern; same guarantee."
  - "page-column = '' in Phase 2 (vs <title> or path-derived). Rationale: Open Q2 RESOLVED in RESEARCH — Phase 2 is single-URL, the human label adds no information the 'url' column doesn't already carry. Phase 3's multi-page crawler fills this from <title> when discovery surfaces it."

patterns-established:
  - "File-I/O atomic-write idiom for any module that has to emit multiple coupled files (JSON + CSV here; would extend to a Sheets+HTML+JSON triple in Phase 6): build each file's content in-memory, then per-file tempfile.NamedTemporaryFile + os.replace. The transaction guarantee is local to each file (not cross-file); cross-file consistency is enforced by the run_id directory itself being the unit of consumption — a partial directory missing result.csv is unambiguously a crash state and a downstream reader fails loud rather than reading half a run."
  - "Test split between unit-level (mocked subprocess; tests/test_output.py + tests/test_cli.py) and gated e2e (real subprocess; tests/test_e2e.py with pytest.mark.e2e + addopts default-exclude). The default `uv run pytest` runs in 0.21s with 177 tests; the e2e is opt-in for pre-/gsd-verify-work smoke."
  - "Threading the labeled-proxy invariant across 4 layers (Phase 1 model validator + 02-01 normalizer source grep + 02-04 CSV column name + 02-04 Rich row label constant) — each layer has its own meta-test asserting the bare-form tokens never appear. Pattern: any future field with a 'forbidden synonym' problem (e.g. real-INP vs lab-INP-proxy; real-FCP vs delayed-FCP) gets the same defense-in-depth structure."

requirements-completed:
  - CLI-01  # machine-readable + non-interactive: --json on stdout, exit codes 0/1/2
  - OUT-03  # raw LH JSON+HTML per page written under lighthouse/<page-slug>.{json,html}
  - OUT-04  # flat CSV one-row-per-page (locked CSV_COLUMNS) + full-fidelity JSON

# Metrics
duration: ~35 minutes
completed: 2026-05-29
tasks: 3 (each with TDD RED→GREEN where applicable; 5 commits total)
tests_added: 28 (13 output + 14 cli + 1 e2e gated)
tests_total: 177 default-selected, 178 with e2e (150 prior + 28 new)
files_created: 5
files_modified: 3
---

# Phase 2 Plan 04: CLI + Outputs Vertical-Slice Summary

**One-liner:** Seals the Phase 2 vertical slice — `perfcrawl measure <url> [--samples N] [--emulation mobile|desktop] [--json] [--output-dir output]` now wires the proven plumbing of 02-01..03 into a single command that writes locked-column CSV + full-fidelity JSON + raw LH artifacts to disk, persists to the Phase 1 SQLite store, renders a Rich human table (or JSON on `--json`), and exits 0/1/2 per D-15. After this plan a developer types one command and gets a real measurement on disk, in the DB, and on screen.

## What Got Built

Three tasks, executed in sequence per the plan's task order. Tasks 1 + 2 are `type="auto" tdd="true"` (RED → GREEN commit pairs); Task 3 is a single test-creation commit.

### Task 1: output.py — CSV + JSON + raw-LH artifact writers (OUT-03 / OUT-04 / D-07)

- **`src/perfcrawl/output.py`** — `write_outputs(run_record, *, output_dir, raw_artifacts=None) -> Path`. Writes the per-run artifact tree under `<output_dir>/<run_id>/`: `result.json` (full-fidelity RunRecord JSON; round-trip-identical to the SQLite blob — same hybrid-store contract as Phase 1), `result.csv` (locked-column CSV per the verbatim `CSV_COLUMNS` list from 02-RESEARCH), and an optional `lighthouse/<page-slug>.{json,html}` raw-artifact pair per page. All writes are atomic via `tempfile.NamedTemporaryFile` + `os.replace` (file-I/O analog of store.py's `with conn:` transaction; CR-01 — a crash mid-write never leaves a half-written file at the consumer-visible path). Every per-page artifact path goes through `page_slug()` — the IN-02 sanitization boundary established in 02-01 Task 1. The `inp_proxy_tbt_ms` CSV column header IS the labeling signal (D-11/D-15) — no bare `inp` appears anywhere in the header list.
- **`CSV_COLUMNS`** — locked at module scope per 02-RESEARCH § "CSV column order" lines 848-881 verbatim (23 columns; preserves the existing studyhalo Google Sheet column order in the first 11 then layers Phase 1/2 additions). Per-column inline comments document the source field and call out the D-11/D-15 labeled-proxy invariant on the `inp_proxy_tbt_ms` column.
- **`tests/test_output.py`** — 13 tests covering: JSON round-trip identity (model_dump equality — same contract Phase 1 proves for the SQLite store), CSV column order locked to CSV_COLUMNS, 3-way parametrized labeled-proxy column-header assertion (`inp` / `inp_ms` / `interaction_to_next_paint` all absent), CSV cell value spot-check (run_id / url / test_date / cache_disabled / lcp_ms / inp_proxy_tbt_ms route correctly), raw LH artifact on-disk layout, IN-02 traversal protection (literal `..` in url_key → safe charset stem, no `..` in filename), `mkdir(parents=True, exist_ok=True)` for missing intermediate dirs, OSError on unwriteable output dir (CLI maps to ExitCode.USER_ERROR), atomic-write hygiene (no stray `.tmp` files), valid JSON top-level shape.

**Commits:**
- `9a47718` — `test(02-04): RED — failing output writer tests` (13 tests + tests/test_output.py; fails with `ModuleNotFoundError: No module named 'perfcrawl.output'`)
- `fe489e3` — `feat(02-04): CSV + JSON + raw-LH writers with IN-02-safe slug + locked CSV_COLUMNS` (implementation; 13 tests green, full suite 163 tests green)

### Task 2: cli.py — Typer entry point + Rich table + --json + persistence + exit codes (D-05 / D-06 / D-15 / CLI-01)

- **`src/perfcrawl/cli.py`** — `app = typer.Typer(no_args_is_help=True, add_completion=False)` + `@app.command(name="measure")` subcommand. Parses argv → invokes `orchestrator.measure_url(url=, samples=, emulation=)` (the 02-03 tuple-return signature) → catches `UserError → ExitCode.USER_ERROR` and `MeasurementError → ExitCode.MEASUREMENT_ERROR` (D-15) → writes outputs via `output.write_outputs(run_record, output_dir=, raw_artifacts=)` (catching `OSError → ExitCode.USER_ERROR`) → persists to `output_dir / "perfcrawl.db"` via Phase 1 `store.init_db` + `store.write_run` → renders either a Rich human table (default) or `RunRecord.model_dump_json(indent=2)` to stdout (`--json`). Progress and errors route to a `Console(stderr=True)` so machine-readable stdout under `--json` is never contaminated (D-06).
- **Subcommand-forcing pattern** — registered a hidden `_internal` no-op alongside `measure` so Typer dispatches as `perfcrawl measure <url>` (D-05) rather than collapsing to the implicit-root `perfcrawl <url>` (its single-@app.command default). Phase 3's `crawl` and Phase 6's `budget` siblings will naturally take that hidden slot.
- **Rich human table** — `_render_human_table(run, *, samples, run_dir)` builds a `Table(title=f"perfcrawl: {run.target}")` with Metric/Value columns. The TBT-proxy row label is `INP_PROXY_DISPLAY_LABEL` from constants.py — NEVER a hand-coded literal of the forbidden bare-form tokens (the ones enumerated in `models._FORBIDDEN_INP_FIELDS`). The displayed value is `page.inp_proxy_tbt_ms.median`. D-08 footer: `caption = f"(median of {samples}) · written to {run_dir}"`.
- **`pyproject.toml`** — added `typer>=0.15` + `rich>=13` (alphabetized; placed between `pydantic` and `w3lib`). Restored `[project.scripts] perfcrawl = "perfcrawl.cli:app"` (Phase 1 deliberately removed it per 01-LEARNINGS commit 5aa4222; Phase 2 brings it back pointing at the real CLI). Added `addopts = "-ra -m 'not e2e'"` so default `uv run pytest` excludes the e2e marker. Preserved `playwright>=1.60,<2` (MEDIUM-3 plan-check fix). No PyPI `lighthouse` entry (Pitfall 8 reaffirmed — grep guard count = 0).
- **`.gitignore`** — `output/` added per RESEARCH § Security Domain (prevents committed URL+HTML report leakage). `*.db` / `*.sqlite` already present from Phase 1.
- **`uv.lock`** — regenerated; pulls `typer==0.26.3`, `rich==15.0.0`, plus transitive `click`, `markdown-it-py`, `mdurl`, `pygments`, `rich-toolkit`, `shellingham`, `typing-inspection`.
- **`tests/test_cli.py`** — 14 tests covering: `--help` / `measure --help` / no-args-shows-help (CLI-01 surface), D-15 exit code mapping in all 4 arms (0 success, 1 UserError, 2 MeasurementError, 1 OSError from unwriteable output dir), D-06 `--json` flag emits parseable JSON on stdout, D-06 default mode emits Rich table with `INP_PROXY_DISPLAY_LABEL` row label, HIST-01 SQLite persistence at `<output_dir>/perfcrawl.db`, D-07 on-disk layout (`result.json` + `result.csv` + `lighthouse/*.{json,html}`), defense-in-depth grep meta-test (no bare `\binp\b` in cli.py source — same shape as Phase 2 plan 01 Task 3's normalizer meta-test).

**Commits:**
- `0c2a0f6` — `test(02-04): RED — failing Typer CLI tests + typer/rich deps + output/ gitignore` (14 tests + pyproject.toml + .gitignore + uv.lock; fails with `ModuleNotFoundError: No module named 'perfcrawl.cli'`)
- `ebbd43d` — `feat(02-04): Typer CLI + Rich human table + --json + SQLite persistence + exit codes` (implementation; 14 tests green, full suite 177 tests passed + 1 deselected)

### Task 3: tests/test_e2e.py — gated end-to-end smoke

- **`tests/test_e2e.py`** — `pytestmark = pytest.mark.e2e`; one test `test_e2e_measure_example_com` that spawns the real CLI via `subprocess.run(["uv", "run", "perfcrawl", "measure", "https://example.com/", "--samples", "1", "--json", "--output-dir", str(tmp_path)])` and asserts: exit code 0, `json.loads(stdout)` parses as a RunRecord with one page, the page's perf_score / lcp_ms.median / inp_proxy_tbt_ms are non-None, `lighthouse_version` starts with `"13."`, the on-disk layout (`result.json` + `result.csv` + `lighthouse/<slug>.{json,html}`) is complete, and `<tmp_path>/perfcrawl.db` contains one row in `runs` matching the RunRecord id.
- **`pyproject.toml`** — `addopts = "-ra -m 'not e2e'"` (added in Task 2) means the default `uv run pytest` deselects this test (`177 passed, 1 deselected`). Opt-in via `uv run pytest -m e2e tests/test_e2e.py -x` (which the developer runs once Node + Chromium are installed; surfaced via CLI errors from `preflight()`).

**Commit:**
- `8776bd5` — `test(02-04): e2e smoke test for measure command (gated by pytest -m e2e)`

## How to Verify

```bash
cd /Users/sneaky/JKL101/performance-statistics-gathering

# Full suite green (Phase 1 + Phase 2-01..04, excluding e2e):
uv run pytest -x
# Expected: 177 passed, 1 deselected in ~0.21s

# CLI surface works:
uv run perfcrawl --help
uv run perfcrawl measure --help

# Plan-level inline verify commands:
uv run python -c "
import csv, tempfile
from pathlib import Path
from datetime import datetime, UTC
from uuid import UUID
from perfcrawl.models import RunRecord, PageResult, MetricSample
from perfcrawl.output import write_outputs, CSV_COLUMNS
sr = RunRecord(
    id=UUID('3f1c2b9a-0000-4000-8000-0000000000c3'),
    started_at=datetime(2026,5,25,12,0,0,tzinfo=UTC),
    target='https://x/',
    pages=[PageResult(url='https://x/', url_key='https://x/', perf_score=80.0,
                      lcp_ms=MetricSample(median=2400.0, samples=[2300.0,2400.0,2500.0]))]
)
with tempfile.TemporaryDirectory() as td:
    run_dir = write_outputs(sr, output_dir=Path(td))
    loaded = RunRecord.model_validate_json((run_dir / 'result.json').read_text())
    assert loaded.model_dump() == sr.model_dump(), 'JSON round-trip failed'
    with open(run_dir / 'result.csv') as f:
        rows = list(csv.reader(f))
    assert rows[0] == CSV_COLUMNS
    print('output OK', run_dir)
"
# Expected: 'output OK /tmp/.../3f1c2b9a-…'

uv run python -c "
from perfcrawl.cli import app
from typer.testing import CliRunner
r = CliRunner().invoke(app, ['--help'])
assert r.exit_code == 0 and 'measure' in r.stdout
print('CLI help OK')
"

# Labeled-proxy grep guards (all should pass):
uv run python -c "
import inspect, re
src = inspect.getsource(__import__('perfcrawl.cli', fromlist=['_x']))
assert 'INP_PROXY_DISPLAY_LABEL' in src
assert re.findall(r'\binp\b(?!_proxy)', src) == []
print('labeling invariant OK')
"

# IN-02 boundary guard:
uv run python -c "
import re
src = open('src/perfcrawl/output.py').read()
assert 'page_slug' in src
assert re.findall(r'f[\"\\']{[^}]*url_key[^}]*}\\.(?:json|html)', src) == []
print('IN-02 boundary OK')
"

# Pitfall 8 reaffirmation (no PyPI lighthouse decoy):
! grep -nE "^[[:space:]]*[\"']lighthouse[\"']" pyproject.toml

# Optional end-to-end smoke (requires `cd lighthouse-worker && npm ci` + `uv run playwright install chromium`):
# uv run pytest -m e2e tests/test_e2e.py -x
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] `CliRunner(mix_stderr=False)` raises TypeError on Typer 0.26 / Click 8.2+**

- **Found during:** Task 2 GREEN (first test run after writing cli.py).
- **Issue:** The plan specifies `runner = CliRunner(mix_stderr=False)` to split stdout/stderr (needed for the `--json` test where the whole stdout must parse as JSON). The current `typer.testing.CliRunner` (Typer 0.26.3 / Click 8.2+) dropped the `mix_stderr` kwarg — stdout/stderr are now split by default, with `result.stdout` and `result.stderr` as separate attributes. The plan's literal invocation crashed with `TypeError: CliRunner.__init__() got an unexpected keyword argument 'mix_stderr'`.
- **Fix:** Switched to `runner = CliRunner()` and added a docstring comment explaining the split-stream default (so a future reader doesn't try to re-add the kwarg). All `result.stdout` / `result.stderr` assertions in the tests work unchanged.
- **Files modified:** `tests/test_cli.py`.
- **Commit:** Folded into the Task 2 GREEN commit (`ebbd43d`).
- **Future implication:** The plan's `read_first` list for Task 2 should be updated (in a meta-pass) to note Click 8.2's stdout/stderr split removed the kwarg. Any Phase 3 / Phase 6 CLI plan that copy-pastes this pattern should know to write `CliRunner()` from the start.

**2. [Rule 1 — Bug] Single-`@app.command()` apps dispatch as implicit root (collapses `perfcrawl measure <url>` → `perfcrawl <url>`)**

- **Found during:** Task 2 GREEN (test_exit_zero_on_success failed with `Got unexpected extra argument(s) (https://example.com)`).
- **Issue:** Typer's documented behavior: a Typer app with exactly one `@app.command()` treats that command as the implicit root command — you invoke it with `app [OPTIONS]` rather than `app <verb> [OPTIONS]`. D-05 explicitly requires the `measure` verb in the invocation (so future Phase 3 `crawl` and Phase 6 `budget` siblings live in the same namespace), so the implicit-root collapse breaks the contract.
- **Fix:** Registered a hidden no-op sibling command `_internal` alongside `measure`. With two `@app.command()`s registered, Typer dispatches as `perfcrawl <verb>`; the hidden one never surfaces in help output and gets naturally replaced by real sibling verbs in later phases.
- **Files modified:** `src/perfcrawl/cli.py`.
- **Commit:** Folded into the Task 2 GREEN commit (`ebbd43d`).

**3. [Rule 1 — Bug] Initial cli.py docstring tripped the labeled-proxy grep meta-test (3rd recurrence in Phase 2)**

- **Found during:** Task 2 GREEN (test_cli_source_has_no_bare_inp failed with `bare-INP token in cli.py source: ['Inp', 'inp']`).
- **Issue:** Initial cli.py module docstring explained the labeling invariant using the literal forbidden bare-form tokens (e.g. "never a hand-coded `Inp` or similar literal", "never a `page.inp` field"). The defense-in-depth grep meta-test (`test_cli_source_has_no_bare_inp`) uses `re.findall(r"\binp\b(?!_proxy)", src, flags=re.IGNORECASE)` and correctly flagged those quoted tokens — the regex doesn't distinguish docs from code.
- **Fix:** Rewrote the docstring + comments to reference "forbidden bare-form tokens" / "the ones enumerated in `models._FORBIDDEN_INP_FIELDS`" / "any forbidden bare-form attribute that doesn't exist on the model" instead of quoting the literal forbidden tokens. Same documentation intent; no regex collision.
- **Files modified:** `src/perfcrawl/cli.py`.
- **Commit:** Folded into the Task 2 GREEN commit (`ebbd43d`).
- **Pattern lineage:** Same fix shape as Phase 2 plan 01 deviation 2 (`normalizer.py` `'inp'` / `'inp_ms'` in comments) and plan 02-03 deviation 1 (`lighthouse_worker.py` / `orchestrator.py` quoting `shell=True` / `launch_persistent_context` / `socket.bind(0)` in docstrings). **Cross-plan lesson — now thrice-confirmed:** when a plan ships a textual grep guard against a forbidden token, source-level docstrings/comments must paraphrase the token rather than quote it. Any future plan that introduces a new grep guard should add a banner comment in the plan's `read_first` block noting this constraint up-front.

**4. [Rule 1 — Bug] Default `uv run pytest` ran the e2e test (marker registration was insufficient)**

- **Found during:** Task 3 (`uv run pytest -x` after committing tests/test_e2e.py — the e2e test ran and failed because Chrome/Node aren't pre-installed for the unit-suite run).
- **Issue:** The plan's `<done>` requires `uv run pytest -x` (default, no `-m e2e`) to NOT run `test_e2e_measure_example_com`. The Phase 2 plan 01 Task 2 step only *registered* the `e2e` marker in `[tool.pytest.ini_options].markers` — marker registration silences unknown-marker warnings but does NOT deselect marked tests by default. Pytest's default behavior is to include all tests regardless of marker.
- **Fix:** Updated `pyproject.toml` `addopts` from `"-ra"` to `"-ra -m 'not e2e'"`. This default-deselects e2e-marked tests; the opt-in `uv run pytest -m e2e` overrides cleanly (Click/pytest's `-m` argument-priority means the explicit `-m e2e` wins over `addopts`'s `-m 'not e2e'`).
- **Files modified:** `pyproject.toml`.
- **Commit:** Landed in the Task 3 commit (`8776bd5`).
- **Future implication:** Any new opt-in marker (Phase 6 might add `slow` for full-crawl tests) should follow the same `-m 'not e2e' and not slow'` pattern in `addopts`. Single-marker case: `-m 'not e2e'`. Multi-marker: `"-ra -m 'not e2e and not slow'"`.

## Authentication Gates

None. Phase 2 plan 04 has no authenticated paths — it's a pure CLI + outputs vertical slice on top of the Phase 1 store and the 02-01..03 orchestration plumbing. The Lighthouse worker (`cd lighthouse-worker && npm ci`) and Playwright Chromium download (`uv run playwright install chromium`) are public-registry / public-binary operations with no credentials. The e2e smoke test runs against `https://example.com/` (IANA-reserved test domain).

The CLI's `preflight()` call (from 02-03 — invoked transitively via `measure_url`) emits an actionable `MeasurementError` if the worker `node_modules` aren't installed, citing the `cd lighthouse-worker && npm ci` step. The CLI maps that to `ExitCode.MEASUREMENT_ERROR` (D-15.2) so the user sees an actionable stderr message and a 2 exit code.

## Known Stubs

None. Every file in this plan is a complete, tested implementation:

- `src/perfcrawl/output.py`: `write_outputs` has 13 tests covering JSON round-trip identity, locked CSV column order, labeled-proxy invariant, raw LH artifact layout, IN-02 traversal protection, intermediate-dir creation, OSError propagation, atomic-write hygiene, and valid-JSON output.
- `src/perfcrawl/cli.py`: `measure` subcommand has 14 tests covering all four D-15 exit-code arms, both stdout-mode branches (Rich table + JSON), HIST-01 SQLite persistence, D-07 on-disk layout, the labeled-proxy grep meta-test, and the ExitCode constant import.
- `tests/test_e2e.py`: the one gated test exercises the full vertical slice (subprocess → orchestrator → output → store → stdout); excluded from default runs via `addopts -m 'not e2e'` and explicitly documented as the developer-side pre-`/gsd-verify-work` smoke check.

The Phase 2 single-URL `page` column being `""` is the documented Open Q2 RESOLVED design — not a stub. Phase 3's multi-page discovery fills it from `<title>` or path slug.

The `total_page_load_time` column being empty on a D-13 partial-result run (LH `runtimeError` with no `audits["interactive"]`) is the documented D-13 contract — empty cell, not the literal string `"None"`, never crashes.

## Threat Flags

None. The plan's `<threat_model>` (T-02-04-PATH / T-02-04-DISCLOSURE / T-02-04-LABEL / T-02-04-SLOPSQUAT / T-02-04-WRITE / T-02-04-DB) is fully mitigated:

| Threat ID | Mitigation | Test |
|-----------|------------|------|
| T-02-04-PATH (URL → filesystem path-traversal) | Every per-page artifact path goes through `page_slug(page.url_key)` (the IN-02 boundary established in 02-01 Task 1). The plan's grep guard `re.findall(r'f"{[^}]*url_key[^}]*}\.(?:json\|html)', src)` returns `[]`. | `test_slug_in_artifact_path_never_traverses` exercises the literal-`..` IN-02 vector with `url_key="https://x.com/a/../b"`; assertion proves no `..` substring in the written file's name and the file lives strictly under `<run_dir>/lighthouse/`. |
| T-02-04-DISCLOSURE (committed `output/` with URLs + HTML reports) | `output/` added to `.gitignore` per RESEARCH § Security Domain. Documented in plan as "Claude's discretion — recommended YES". | `grep -c "^output/$" .gitignore` returns `1`. |
| T-02-04-LABEL (Rich table INP labeling drift) | 4-layer defense-in-depth: (1) model-layer `_no_bare_inp` validator (Phase 1, unchanged); (2) normalizer source-level grep meta-test (02-01); (3) CSV column header `inp_proxy_tbt_ms` (this plan Task 1); (4) Rich row label `INP_PROXY_DISPLAY_LABEL` constant (this plan Task 2). Each layer has its own assertion. | `test_csv_inp_proxy_column_is_labeled` (parametrized 3 forbidden variants) + `test_inp_label_visible_in_rich_table` + `test_cli_source_has_no_bare_inp` (source-level grep meta-test on cli.py). |
| T-02-04-SLOPSQUAT (PyPI `lighthouse` decoy reaffirmation, 3rd time in Phase 2) | Phase 2-04 adds `typer>=0.15` (tiangolo) and `rich>=13` (Textualize) — both verified mature per RESEARCH § Package Legitimacy Audit. No PyPI `lighthouse` entry. | `grep -nE "^[[:space:]]*[\"']lighthouse[\"']" pyproject.toml` returns no matches. |
| T-02-04-WRITE (output dir unwriteable) | `write_outputs` propagates `OSError` (and `NotADirectoryError` / `FileExistsError` subclasses); CLI catches as `ExitCode.USER_ERROR` per D-15. | `test_output_dir_unwriteable_raises_oserror` (output.py test) + `test_exit_one_when_output_dir_unwriteable` (cli.py test). |
| T-02-04-DB (SQLite tampering) | Reuses Phase 1 `store.init_db` + `store.write_run` unchanged — `with conn:` atomic transaction, parameterized SQL, `PRAGMA foreign_keys = ON` per-connection. Zero new query construction. | Inherited from Phase 1 test_store.py (14 tests, all green). |

ASVS coverage: V5 (Input Validation — URL/flags via Typer + UserError arm); V7 (Error Handling — four-way exit-code mapping per D-15 with no stack-trace leakage to stdout); V8 (Data Protection — `output/` in `.gitignore` prevents URL/HTML leakage); V12 (Files and Resources — IN-02 slug boundary applied at output writers + atomic write to prevent half-CSV); V14 (Configuration — typer/rich pinned with semver bounds; uv.lock committed for byte-identical installs).

## TDD Gate Compliance

Plan-level tasks 1 + 2 are `type="auto" tdd="true"` per the plan frontmatter. Task 3 is a single test-creation commit (no implementation to gate). Each TDD task's RED → GREEN pair is visible in `git log`:

| Task | RED commit | GREEN commit |
|------|------------|--------------|
| Task 1 (output.py) | `9a47718` `test(02-04): RED — failing output writer tests` | `fe489e3` `feat(02-04): CSV + JSON + raw-LH writers with IN-02-safe slug + locked CSV_COLUMNS` |
| Task 2 (cli.py) | `0c2a0f6` `test(02-04): RED — failing Typer CLI tests + typer/rich deps + output/ gitignore` | `ebbd43d` `feat(02-04): Typer CLI + Rich human table + --json + SQLite persistence + exit codes` |
| Task 3 (e2e) | (single commit — test-only, no implementation) | `8776bd5` `test(02-04): e2e smoke test for measure command (gated by pytest -m e2e)` |

Both Task 1 + 2 RED commits show `ModuleNotFoundError` (the explicit RED gate per the plan's `<action>` discipline): Task 1's RED ran `pytest tests/test_output.py` and got `No module named 'perfcrawl.output'`; Task 2's RED ran `pytest tests/test_cli.py` and got `No module named 'perfcrawl.cli'`.

The Task 2 RED commit ships `pyproject.toml` + `uv.lock` + `.gitignore` alongside the test file because: (a) the test environment must have `typer` installable to import the test module; (b) the `.gitignore` change for `output/` is a one-line edit that belongs with the CLI's introduction of artifact-writing on disk. Same staging discipline as 02-03 Task 1's `playwright` dep landing alongside the worker RED commit.

Task 3 is the documented TDD exception (per the plan): the e2e test is integration-level and has no implementation pair — its "implementation" is the entire CLI vertical slice already built by Tasks 1 + 2. The single commit creates `tests/test_e2e.py` AND adds the `addopts -m 'not e2e'` default-deselection in `pyproject.toml`.

## Performance

- **Started:** 2026-05-29 (approx 11:35 local)
- **Completed:** 2026-05-29 (approx 12:10 local)
- **Duration:** ~35 minutes (single executor, sequential tasks)
- **Tasks:** 3 (5 commits total — 2 RED + 2 GREEN + 1 test-only)
- **Files created:** 5 (`src/perfcrawl/output.py`, `src/perfcrawl/cli.py`, `tests/test_output.py`, `tests/test_cli.py`, `tests/test_e2e.py`)
- **Files modified:** 3 (`pyproject.toml`, `.gitignore`, `uv.lock`)
- **Tests added:** 28 (13 output + 14 cli + 1 e2e gated)
- **Tests total:** 177 default-selected (1 deselected = e2e), all green, ~0.21s

## Next Phase Readiness

- **Phase 2 vertical slice is sealed.** A developer with Node + Chromium installed can now type:
  ```bash
  uv run perfcrawl measure https://example.com/ --samples 1 --json --output-dir /tmp/perfcrawl-smoke
  ```
  and get a valid RunRecord JSON on stdout, full-fidelity artifacts on disk at `/tmp/perfcrawl-smoke/<run_id>/`, and a persisted row in `/tmp/perfcrawl-smoke/perfcrawl.db`. Exit code 0 on success; 1 on user errors (bad URL/flags/output-dir); 2 on measurement errors (all samples failed, Chrome won't launch, worker not installed).
- **Phase 2 PROVE gates** can now run:
  - `/gsd-verify-work 2` — UAT against the docs above + manual e2e via `uv run pytest -m e2e tests/test_e2e.py -x` (one-time-per-machine prerequisite: Node + Chromium installation).
  - `/gsd-code-review 2` — has 4 plans worth of new code to review (`02-01-SUMMARY` + `02-02-SUMMARY` + `02-03-SUMMARY` + this one).
  - `/gsd-secure-phase 2` — has 4 plans' worth of threat models, with this plan's labeled-proxy-as-defense-in-depth being a notable extension of 02-01's normalizer-source meta-test.
  - `/gsd-validate-phase 2` — Nyquist coverage check across CLI-01, OUT-03, OUT-04, plus the cross-plan METRIC-01..05, RUN-01..04 from 02-01..03.
- **Phase 3 (multi-page crawl) builds on this seam unchanged.** The `measure` subcommand stays; a new `crawl` sibling replaces the hidden `_internal` slot. `output.write_outputs` already supports multi-page (the `for page in run_record.pages:` loop); the only addition Phase 3 needs is filling the `page` CSV column from discovered `<title>` text. The collision suffix mechanism (`__N`) on `lighthouse/<slug>` paths is already in place for Phase 3's multi-page artifact set.
- **Phase 4 (auth) layers `storage_state` on `browser.new_context()` in `orchestrator.py` — no CLI/output changes.** The 02-03 plumbing already supports it; 02-04's CLI just passes flags through.
- **Phase 5 (AI analysis) writes into the `PageResult.analysis` slot already modeled in Phase 1 — no CLI/output changes** (the JSON round-trip + CSV column list already include it; the Rich table will gain new rows in Phase 5).
- **Phase 6 (budgets + Sheets exporter) reads the same `CSV_COLUMNS` list to build the Google Sheets schema.** The locked column order in this plan is the single source of truth.

## Self-Check: PASSED

Verified before completion:

- ✅ `src/perfcrawl/output.py` exists (`fe489e3`).
- ✅ `src/perfcrawl/cli.py` exists (`ebbd43d`).
- ✅ `tests/test_output.py` exists (`9a47718`).
- ✅ `tests/test_cli.py` exists (`0c2a0f6`).
- ✅ `tests/test_e2e.py` exists (`8776bd5`).
- ✅ `pyproject.toml` modified — `typer>=0.15` + `rich>=13` deps + `[project.scripts] perfcrawl = "perfcrawl.cli:app"` + `addopts -m 'not e2e'` (`0c2a0f6` + `8776bd5`).
- ✅ `.gitignore` modified — `output/` added (`0c2a0f6`).
- ✅ `uv.lock` regenerated (`0c2a0f6`).
- ✅ All 5 task-level commits visible in `git log --oneline f15d810..HEAD`: `9a47718`, `fe489e3`, `0c2a0f6`, `ebbd43d`, `8776bd5`.
- ✅ `uv run pytest -x` reports `177 passed, 1 deselected in 0.21s`. All Phase 1 (67) + Phase 2 plan 01 (31) + Phase 2 plan 02 (19) + Phase 2 plan 03 (33) + Phase 2 plan 04 (27 default-selected) tests green; no regression. The 1 deselected is the e2e gated test (correct).
- ✅ Plan verify commands all pass: pytest combined target, inline write_outputs round-trip Python check, CLI help check, labeling invariant grep guard on cli.py, IN-02 boundary grep guard on output.py, Pitfall 8 lighthouse-decoy check.
- ✅ Public CLI surface works: `uv run perfcrawl --help` shows `measure` subcommand; `uv run perfcrawl measure --help` lists URL + `--samples` + `--emulation` + `--json` + `--output-dir`.
- ✅ Test counts meet plan minima: 13 output tests (≥7 required) + 14 cli tests (≥10 required) + 1 e2e test (≥1 required).
- ✅ All 4 plan-level grep guards pass: `INP_PROXY_DISPLAY_LABEL` in cli.py source; no bare `\binp\b` in cli.py source; `page_slug` in output.py; no `f"{url_key}.json"`-style raw-key-as-path pattern in output.py; no PyPI `lighthouse` decoy in pyproject.toml.

---
*Phase: 02-single-page-measurement-slice*
*Completed: 2026-05-29*
