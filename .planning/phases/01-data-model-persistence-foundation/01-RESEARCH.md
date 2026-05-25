# Phase 1: Data Model & Persistence Foundation - Research

**Researched:** 2026-05-25
**Domain:** Typed data-contract layer (Pydantic 2.x) + stdlib SQLite persistence + regression-delta computation + URL canonicalization (pure Python library, no runtime UI/CLI surface)
**Confidence:** HIGH

## Summary

This phase builds a **pure data-contract / library layer** — no CLI, no UI, no measurement, no network at runtime. The entire surface is verified by `pytest` against fixtures. Four observable behaviors define done: (1) write a run to SQLite and read it back **identically**, (2) compute `RunDelta` records on fixture runs, (3) carry an integer `schemaVersion` so old-schema runs still load, and (4) match pages across runs via a canonical, normalized URL key.

The stack is **already locked** by `CLAUDE.md` and `01-CONTEXT.md` (D-01..D-17): **Pydantic 2.x** as the model layer, **stdlib `sqlite3`** as the store (chosen over DuckDB/JSON-files), with project scaffolding via **uv + Ruff + pytest**. Research confirms these are correct and current, and resolves the four open design questions the planner asked: how to get round-trip-identical persistence, how schema evolution actually works at the SQLite level, the exact shape of a polarity-driven delta engine, and which URL library to use.

The two load-bearing technical findings the planner must encode precisely: **(A)** store the record as raw **JSON TEXT** (the bytes from `model_dump_json()`), not JSONB — TEXT preserves bytes for round-trip identity; promoted/queryable metric columns are **`GENERATED ALWAYS AS (json_extract(...))` columns** computed *from* the blob so they can never drift. **(B)** SQLite forbids adding **STORED** generated columns via `ALTER TABLE` — only **VIRTUAL** ones — so the "promote a field cheaply later" path (D-07) must use `ADD COLUMN ... GENERATED ALWAYS AS (...) VIRTUAL` (indexable, computed on read), while STORED columns may only be declared at `CREATE TABLE` time. This restriction is the single most likely thing a planner gets wrong.

**Primary recommendation:** Pydantic 2.x models with later-phase fields `Optional[...] = None` and `model_config(extra="ignore")`; serialize via `model_dump_json()` into a SQLite **TEXT** `record_json` column; expose queried metrics + the canonical URL key as **generated columns** (`STORED` at create time, `VIRTUAL` when added later) and index those; build a polarity-driven `RunDelta` engine off a single central metric registry; canonicalize URLs with **`w3lib.url.canonicalize_url` + a thin wrapper** (tracking-param denylist via `url_query_cleaner` + trailing-slash rule), with the denylist and polarity table each in one editable module.

## <user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Page Identity (canonical URL key) — success criterion #4**
- **D-01:** Persist **both** the URL as-measured **and** a separate **canonical key** for cross-run matching / `RunDelta` joins. The original is never mutated; the key is derived.
- **D-02:** Canonicalization = RFC 3986 syntax normalization + perf-tool conventions: lowercase scheme + host; strip default ports (`:80`/`:443`); resolve `.`/`..` dot-segments; uppercase percent-encoding hex; **preserve path case**.
- **D-03:** Strip the trailing slash except root `/`. Do **not** strip `www` or `index.html` by default.
- **D-04:** Query string: drop known tracking params (`utm_*`, `gclid`, `fbclid`, …), sort remaining params alphabetically, keep functional params (`?page=2`, `?id=5`). Keep the tracking-param denylist in a single editable place.
- **D-05:** Always drop the `#fragment`.

**Schema Evolution (schemaVersion) — success criterion #3**
- **D-06:** Additive-only ("expand") evolution. Never remove/rename fields; only add optional ones. Each `RunRecord`/`PageResult` carries an integer `schemaVersion`.
- **D-07:** Hybrid SQLite store: full-fidelity JSON blob per record **plus** promoted scalar columns for queried/joined metrics. Adding a model field requires **no** `ALTER TABLE`; promoting a field is a cheap additive `ADD COLUMN`.
- **D-08:** On read, missing fields default to `null` via Pydantic optionals so an older-schema run loads cleanly. A non-additive migration registry is **deferred until actually needed**.

**RunDelta Semantics — success criterion #2**
- **D-09:** Each metric declares **polarity** in a central registry: lower-is-better (LCP, CLS, TBT, TTFB, total bytes, request count, slowest-request time) vs higher-is-better (Lighthouse scores). `direction` is **derived from polarity**, never hardcoded.
- **D-10:** `RunDelta` fields: `current, previous, deltaAbs, deltaPct, direction`. `deltaPct` guarded against `previous == 0` (emit `null`, never inf/NaN).
- **D-11:** Status/direction enum `{improvement, regression, unchanged, new, removed, not_comparable}`: new page → `previous=null, direction=new`; disappeared page → `current=null, direction=removed` (**emitted, never silently dropped**); metric on only one side → `not_comparable`.
- **D-12:** Phase 1 computes **raw** direction only — `unchanged` means literally equal. Noise-band / variance gating stays in Phase 6.

**Model Field Scope — phase goal**
- **D-13:** Model the **full known v1 superset now**, later-phase fields **nullable**: Lighthouse category scores; CWV; network facts (TTFB, request count, total bytes, response sizes, status code, slowest-request URL + ms) → existing Google Sheet columns; the network waterfall list (per request: URL, type, size, timing, status); a Lighthouse opportunities/diagnostics blob (METRIC-05 raw material); an optional `analysis` sub-model for Phase 5 AI fields.
- **D-14:** **First-class median-of-N storage** now: per metric, store aggregated `median` **and** raw `samples[]`.
- **D-15:** The lab-INP-proxy field is **explicitly named as a TBT-based lab proxy** (e.g. `inp_proxy_tbt`) — never a bare `inp`. Enforce at the model layer.
- **D-16:** **Do NOT speculatively model v2 backend metrics** (BACK-01..03).

**RunRecord metadata**
- **D-17:** `RunRecord` carries: run id (UUID), `started_at` timestamp, `target` (site/seed), `schemaVersion`, an `auth_used` flag (Phase 4), and a stamped-environment slot (Chrome version, Lighthouse version, throttling config, mobile/desktop emulation) that Phase 2 fills. Defined now, nullable until Phase 2 populates.

### Claude's Discretion
User deferred all four discussed areas to best practices. Remaining latitude for planner/executor: **exact module layout, Pydantic field names and validator implementation, the precise SQLite DDL, the JSON-vs-column split per field, and how metric polarity is registered** — provided the observable contract (D-01..D-17) holds.

### Deferred Ideas (OUT OF SCOPE)
- **v2 backend metrics (BACK-01..03)** — not modeled now (D-16); additive evolution makes them a clean add-on later.
- **Noise-band / variance-aware regression gating** — `RunDelta` here is raw direction only; the threshold lives in **Phase 6** (HIST-02).
- **Non-additive schema migrations** — migration registry sketched (D-08) but not built until a real non-additive change forces it.
- **Everything user-visible/runtime** — CLI (Typer), web/UI, crawling, measurement, auth, AI generation, exporters belong to Phases 2-6. Do NOT scaffold them here.
</user_constraints>

## <phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HIST-01 | Tool persists every run (run id, timestamp, per-page results) to a local store | The Pydantic `RunRecord` (UUID id + `started_at` + `target` + `PageResult[]`, D-17) serialized via `model_dump_json()` into a SQLite **TEXT** `record_json` column; round-trip-identical read-back proven against fixtures (success criterion #1). SQLite chosen over DuckDB/JSON per `CLAUDE.md` § Run Persistence. |

**Field-shape requirements that ship in Phase 2 but constrain the model now (D-13/D-14/D-17):** METRIC-01 (Lighthouse category scores), METRIC-02 (LCP, CLS, labeled lab-INP proxy → D-15), METRIC-03 (network waterfall list), METRIC-04 (TTFB / request count / total bytes / response sizes / status codes / slowest-request URL+ms), METRIC-05 (opportunities/diagnostics blob), RUN-04 (median-of-N → store `median` + `samples[]`). These define the **nullable superset** the model must carry now so the contract "never needs retrofitting."
</phase_requirements>

## Architectural Responsibility Map

This phase is a single-tier **library / data layer** — no browser, frontend server, API, or CDN tiers exist yet. The "tiers" here are the internal module boundaries that become the stable seam every later phase consumes.

| Capability | Primary Tier (module) | Secondary Tier | Rationale |
|------------|----------------------|----------------|-----------|
| Canonical typed record (`PageResult`, `RunRecord`, nested `analysis`/`MetricSample`) | Model layer (Pydantic) | — | One schema flows from Lighthouse JSON → persistence → exporters; Pydantic validates + serializes. CLAUDE.md locks Pydantic 2.x. |
| Run persistence (write/read-back-identical) | Store layer (stdlib `sqlite3`) | Model layer (serialize) | OLTP small-insert/point-lookup workload = SQLite's sweet spot (CLAUDE.md). Store owns DDL + the JSON-TEXT-blob + generated-column split. |
| Cross-run page identity | Canonicalization layer (`w3lib` wrapper) | Model layer (stores both raw + key) | URL normalization is a pure string transform; isolating it keeps the denylist editable in one place (D-04) and the join key deterministic. |
| Regression delta computation | Delta layer | Registry layer (polarity) + Model | `direction` is derived from a central polarity registry (D-09); delta math reads two `RunRecord`s and emits `RunDelta[]`. |
| Metric polarity + tracking-param denylist | Registry layer (two small modules/constants) | — | D-04 and D-09 each demand "one editable place"; later phases extend without touching call sites. |

**Why this matters:** the planner must keep the **model + store public API as the stable seam** — Phase 2 normalizes measurements into `PageResult`, Phase 5 fills `analysis`, Phase 6's exporters/regression read `RunRecord`/`RunDelta`. Misplacing polarity logic at delta call sites, or putting canonicalization inside the model, would violate the "one editable place" decisions and leak across tiers.

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| **Python** | 3.12+ (3.14.0 present on this machine) | Primary language | Locked by CLAUDE.md. `[VERIFIED: local — python3 3.14.0]` |
| **Pydantic** | 2.13.4 (pin `>=2.10,<3`) | Typed models; `model_dump_json()` / `model_validate_json()`; validators; nested submodels | Locked by CLAUDE.md § Recommended Stack. Latest 2.x verified. `[VERIFIED: PyPI — 2.13.4]` `[CITED: docs.pydantic.dev]` |
| **stdlib `sqlite3`** | stdlib (links SQLite 3.50.4 here) | Run persistence + regression history store | Locked by CLAUDE.md § Run Persistence (SQLite over DuckDB/JSON). JSON1 + generated-columns + STRICT-tables all confirmed present. `[VERIFIED: local — sqlite 3.50.4]` |
| **stdlib `urllib.parse`** | stdlib | URL parsing primitive under the canonicalizer | Always available; the canonicalizer composes on top of it. `[VERIFIED: local]` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **w3lib** | 2.4.1 (pin `>=2.3,<3`) | `canonicalize_url` (RFC-3986 normalize + percent-case + sort query + drop fragment) and `url_query_cleaner` (drop tracking params) | **Recommended** for D-02/D-04/D-05 heavy lifting, wrapped by a thin project module that adds the D-03 trailing-slash rule + D-04 denylist. The canonicalization engine inside Scrapy — battle-tested. `[VERIFIED: PyPI — 2.4.1]` `[CITED: w3lib.readthedocs.io]` — see Package Legitimacy Audit. |
| **stdlib `uuid`** | stdlib | `RunRecord` id (UUID, D-17) | Always. |
| **stdlib `datetime`** | stdlib | `started_at` timestamp (timezone-aware, store ISO-8601) | Always. |
| **stdlib `statistics`** | stdlib | `statistics.median()` for median-of-N aggregation *if* aggregation happens here | Phase 1 only stores `median` + `samples[]` (D-14); actual median computation is Phase 2. Note it exists so Phase 2 needs no new dep. |

### Development Tools
| Tool | Version | Purpose | Notes |
|------|---------|---------|-------|
| **uv** | 0.11.16 | Dependency + venv management, lockfile | CLAUDE.md § Development Tools. **Not currently installed** — see Environment Availability. `[VERIFIED: PyPI — 0.11.16]` |
| **Ruff** | 0.15.14 | Lint + format (one tool) | CLAUDE.md § Development Tools. **Not currently installed.** `[VERIFIED: PyPI — 0.15.14]` |
| **pytest** | 9.0.3 (pin `>=8,<10`) | All four success criteria verified here | CLAUDE.md § Development Tools. **Not currently installed.** `[VERIFIED: PyPI — 9.0.3]` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| w3lib | **stdlib `urllib.parse` only** (hand-rolled canonicalizer) | Zero deps, but you reimplement percent-case normalization, query sorting, safe-encoding, and fragment handling — exactly the brittle edge cases w3lib already solves. Viable fallback if the team wants zero runtime deps; expect ~40-60 lines of fiddly code + its own test burden. See Don't Hand-Roll. |
| w3lib | **`url-normalize` 3.0.0** | Does RFC-3986 normalization but is narrower (no query-param dropping helper); you'd still wrap it for D-04. w3lib gives both `canonicalize_url` and `url_query_cleaner`. `[VERIFIED: PyPI — 3.0.0]` |
| w3lib | **`courlan` 1.3.2** | Crawl-oriented URL cleaner with built-in tracking-param stripping; heavier (pulls `tld`/`babel`-style deps, opinionated filtering). Overkill for a pure key-derivation function; revisit in Phase 3 crawler if its scope-filtering helps. `[VERIFIED: PyPI — 1.3.2]` |
| SQLite | **DuckDB** | Rejected by CLAUDE.md: workload is transactional small-insert + point-lookup (SQLite's strength); DuckDB only wins on big columnar scans not done here. |
| SQLite JSON blob | **JSON files as the store** | Rejected by CLAUDE.md: cross-run delta joins become manual file-joining; SQL self-join is correct + simpler. |
| Pydantic | **dataclasses + manual (de)serialization** | No validation, no `model_validate_json`, no nested-model parsing, no INP-proxy validator (D-15). Pydantic is locked anyway. |
| Promoted columns via Python | **SQLite generated columns** (recommended) | Python-side promotion (write the scalar at insert time) risks drift between blob and column; generated columns are computed *from* the blob so they can never disagree. See Architecture Patterns. |

**Installation (greenfield scaffolding):**
```bash
# Install uv first (CLAUDE.md-locked toolchain) — verify installer/version before running:
#   https://docs.astral.sh/uv/  (or: pip install uv==0.11.16)
uv init --package perfcrawl          # creates pyproject.toml + src/ layout
uv add "pydantic>=2.10,<3" "w3lib>=2.3,<3"
uv add --dev "pytest>=8,<10" "ruff>=0.15,<0.16"
# sqlite3, uuid, datetime, statistics, urllib.parse are stdlib — no install
```
> **Provenance note:** `uv`, `ruff`, `pytest` are locked by CLAUDE.md § Development Tools (authoritative). `pydantic` is locked by CLAUDE.md § Recommended Stack (authoritative). `w3lib` is a **new recommendation from this research** — see Package Legitimacy Audit; planner should gate its install behind a `checkpoint:human-verify` task (slopcheck was unavailable this session).

**Version verification (done this session, against PyPI):** pydantic 2.13.4, w3lib 2.4.1, uv 0.11.16, ruff 0.15.14, pytest 9.0.3, url-normalize 3.0.0, courlan 1.3.2 — all confirmed present on PyPI via `pip index versions`.

## Package Legitimacy Audit

> slopcheck could not be installed in this session (offline/sandboxed pip). Per protocol, packages are verified via PyPI registry + authoritative source repo + provenance, and the one **new** external runtime dependency (`w3lib`) is marked `[ASSUMED]` so the planner gates it behind a `checkpoint:human-verify` task before install. The locked-by-CLAUDE.md packages carry authoritative provenance.

| Package | Registry | Latest ver | Source Repo | slopcheck | Provenance | Disposition |
|---------|----------|-----------|-------------|-----------|------------|-------------|
| pydantic | PyPI | 2.13.4 | github.com/pydantic/pydantic | unavailable | Locked by CLAUDE.md (authoritative) | Approved |
| w3lib | PyPI | 2.4.1 | github.com/scrapy/w3lib (official Scrapy org) | unavailable | New research recommendation | **Flagged — planner adds checkpoint:human-verify** |
| uv | PyPI | 0.11.16 | github.com/astral-sh/uv | unavailable | Locked by CLAUDE.md (authoritative) | Approved |
| ruff | PyPI | 0.15.14 | github.com/astral-sh/ruff | unavailable | Locked by CLAUDE.md (authoritative) | Approved |
| pytest | PyPI | 9.0.3 | github.com/pytest-dev/pytest | unavailable | Locked by CLAUDE.md (authoritative) | Approved |

**Packages removed due to slopcheck [SLOP] verdict:** none (slopcheck unavailable).
**Packages flagged as suspicious [SUS]:** none. `w3lib` is flagged only because it is a *new* recommendation and slopcheck could not confirm it this session — it is the well-known Scrapy URL library (hosted under the official `scrapy` GitHub org, on PyPI since 2010). The zero-dep fallback (stdlib `urllib.parse`) is documented under Alternatives Considered should the team decline the dependency.

## Architecture Patterns

### System Architecture Diagram

```
                 ┌─────────────────────────────────────────────────────────┐
                 │  PHASE 1 LIBRARY SURFACE  (no CLI / no network / no UI)   │
                 └─────────────────────────────────────────────────────────┘

  fixture dict / future                       ┌──────────────────────────┐
  Phase-2 measurement   ───────────────────▶  │   MODEL LAYER (Pydantic) │
  (raw url + metrics)                          │  RunRecord               │
                                               │   ├─ id (UUID)           │
        ┌──────────────────────────┐           │   ├─ started_at         │
        │  CANONICALIZATION LAYER  │           │   ├─ target / schemaVer │
        │  canonical_key(url) ─────┼──────────▶│   ├─ env slot (D-17)    │
        │  (w3lib + denylist +     │  derives  │   └─ pages: PageResult[] │
        │   trailing-slash rule)   │  key      │        ├─ url (raw)      │
        └──────────┬───────────────┘           │        ├─ url_key (canon)│
                   │ reads denylist            │        ├─ metrics+samples│
                   ▼                           │        └─ analysis? (P5) │
        ┌──────────────────────────┐           └───────────┬──────────────┘
        │  REGISTRY LAYER          │                       │ model_dump_json()
        │  • tracking-param denylist│          model_validate_json()
        │  • metric polarity table  │◀────────┐            ▼
        └──────────┬───────────────┘          │  ┌──────────────────────────┐
                   │ polarity                  │  │   STORE LAYER (sqlite3)  │
                   ▼                           │  │  runs(id, started_at,    │
        ┌──────────────────────────┐          │  │       target, schema_ver,│
        │  DELTA LAYER             │           └──┤       record_json TEXT)  │
        │  compute_deltas(         │  read two    │   + GENERATED cols       │
        │    current_run,          │◀───runs──────│     (json_extract → idx) │
        │    previous_run) ──────▶ │              │  page_results view/cols  │
        │  → RunDelta[]            │              │     for url_key self-join│
        │  (current/previous/      │              └──────────────────────────┘
        │   deltaAbs/deltaPct/dir) │                         │
        └──────────┬───────────────┘                         ▼
                   ▼                                  local .db file on disk
        RunDelta[] returned to caller            (round-trip identity guarantee:
        (tested vs fixtures, criterion #2)        record_json read back == written)
```

The primary use case to trace: a `RunRecord` enters the model layer → `canonical_key()` derives each page's `url_key` (reading the denylist) → `model_dump_json()` produces TEXT written to the store's `record_json` column → generated columns expose `url_key` + metrics for indexing → a later read self-joins on `url_key` and the delta layer (reading polarity from the registry) emits `RunDelta[]`.

### Recommended Project Structure
```
perfcrawl/                       # repo root (greenfield)
├── pyproject.toml               # uv-managed; [project] deps + [tool.ruff] + [tool.pytest.ini_options]
├── uv.lock
├── src/
│   └── perfcrawl/
│       ├── __init__.py
│       ├── models.py            # PageResult, RunRecord, MetricSample, AnalysisResult, RunDelta + DirectionStatus enum + INP-proxy validator (D-13/14/15/17)
│       ├── canonical.py         # canonical_key(url) — w3lib wrapper + trailing-slash rule (D-01..D-05)
│       ├── registry.py          # METRIC_POLARITY table (D-09) + TRACKING_PARAM_DENYLIST (D-04) — the two "one editable place" tables
│       ├── delta.py             # compute_deltas(current, previous) -> list[RunDelta] (D-09..D-12)
│       └── store.py             # SQLite DDL + write_run() / read_run() (D-06/D-07/D-08, success #1)
└── tests/
    ├── conftest.py              # fixtures: sample RunRecord(s), two-runs-same-site pair, old-schema JSON blob
    ├── fixtures/                # canned JSON: run_v1.json, run_v1_old_schema.json (fewer fields), delta_pair_*.json
    ├── test_models.py           # INP-proxy naming rejection, nullable superset, schemaVersion default
    ├── test_canonical.py        # D-02..D-05 cases incl. tracking-param drop + trailing slash + fragment
    ├── test_store.py            # round-trip-identical (criterion #1), old-schema load (criterion #3)
    └── test_delta.py            # polarity-driven direction, deltaPct guard, new/removed/not_comparable (criterion #2)
```

### Pattern 1: Forward-compatible Pydantic model (nullable superset + schemaVersion)
**What:** Model the full v1 field superset now; later-phase fields are `Optional[...] = None`. `extra="ignore"` so a *newer*-schema blob loads under *older* code; `Optional` defaults so an *older* blob loads under *newer* code. Integer `schema_version` with a constant default (D-06).
**When to use:** All record models.
**Example:**
```python
# Source pattern: docs.pydantic.dev/latest/concepts/models + /concepts/fields  [CITED]
from enum import StrEnum
from uuid import UUID, uuid4
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1  # bump only on additive change; never remove/rename fields (D-06)

class MetricSample(BaseModel):           # D-14: median + raw distribution, fillable in Phase 2
    model_config = ConfigDict(extra="ignore")
    median: float | None = None
    samples: list[float] = Field(default_factory=list)

class AnalysisResult(BaseModel):         # D-13: Phase 5 AI fields, nullable now
    model_config = ConfigDict(extra="ignore")
    observation: str | None = None
    potential_cause: str | None = None
    suggested_optimization: str | None = None

class PageResult(BaseModel):
    model_config = ConfigDict(extra="ignore")  # newer-schema blobs load under older code (forward compat)
    url: str                              # D-01: as-measured, never mutated
    url_key: str                          # D-01: derived canonical key (set by canonical_key())
    # --- nullable v1 superset (D-13), filled in Phase 2+ ---
    perf_score: float | None = None       # higher-is-better (METRIC-01)
    lcp_ms: MetricSample | None = None    # CWV (METRIC-02)
    inp_proxy_tbt_ms: MetricSample | None = None  # D-15: explicitly a TBT-based lab proxy, NEVER bare `inp`
    # ttfb_ms, request_count, total_bytes, status_code, slowest_request_url/ms, waterfall[], diagnostics{} ...
    analysis: AnalysisResult | None = None

class RunRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID = Field(default_factory=uuid4)        # D-17
    started_at: datetime                           # D-17 (store tz-aware ISO-8601)
    target: str                                    # D-17
    schema_version: int = SCHEMA_VERSION           # D-06 / criterion #3
    auth_used: bool | None = None                  # D-17 (Phase 4)
    # environment slot (D-17): chrome_version, lighthouse_version, throttling, emulation — all None now
    pages: list[PageResult] = Field(default_factory=list)
```

### Pattern 2: INP-proxy naming guard (D-15) via model validator
**What:** Enforce at the model layer that no field is a bare `inp` masquerading as field INP; the proxy must be the explicitly-named TBT proxy.
**When to use:** On `PageResult`.
**Example:**
```python
# Source pattern: docs.pydantic.dev/latest/concepts/validators (model_validator mode="after")  [CITED]
    @model_validator(mode="after")
    def _no_bare_inp(self):
        # Defensive: if a dict with a bare 'inp' key ever reaches the model, reject it.
        # (With extra="ignore" a stray 'inp' is dropped; this guards explicit field additions in review.)
        forbidden = {"inp", "inp_ms", "interaction_to_next_paint"}
        present = forbidden & set(type(self).model_fields)
        if present:
            raise ValueError(f"Field(s) {present} forbidden — INP must be a labeled TBT lab proxy (D-15)")
        return self
```
> Note: `model_validator(mode="after")` re-runs on nested models during parent validation — keep nested validators idempotent. `[CITED: github.com/pydantic/pydantic#8452]`

### Pattern 3: Hybrid SQLite store — TEXT blob + generated columns (round-trip identity)
**What:** One row per run. Full record stored as **JSON TEXT** (`model_dump_json()` bytes) for byte-identical round-trip; queried metrics + the canonical key exposed as **generated columns** (`STORED` at create time) that `json_extract` *from* the blob — so they can never drift. Adding a model field = **no DDL**; promoting a field later = `ADD COLUMN ... GENERATED ... VIRTUAL` (D-07).
**When to use:** The store layer.
**Example:**
```sql
-- Source: sqlite.org/json1.html + sqlite.org/gencol.html  [CITED]
CREATE TABLE runs (
    id            TEXT PRIMARY KEY,        -- UUID
    started_at    TEXT NOT NULL,           -- ISO-8601
    target        TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    record_json   TEXT NOT NULL            -- model_dump_json() — RAW TEXT (not JSONB) => byte-identical read-back
) STRICT;

-- One row per page, also blob + generated columns, for the url_key self-join + metric lookups:
CREATE TABLE page_results (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    record_json TEXT NOT NULL,             -- the PageResult JSON
    -- promoted/queried columns are GENERATED from the blob (cannot drift):
    url_key     TEXT GENERATED ALWAYS AS (json_extract(record_json, '$.url_key')) STORED,
    perf_score  REAL GENERATED ALWAYS AS (json_extract(record_json, '$.perf_score')) STORED
) STRICT;
CREATE INDEX idx_pr_urlkey ON page_results(url_key);          -- the cross-run self-join key (D-07)
CREATE INDEX idx_pr_run    ON page_results(run_id);
```
```python
# write: store the exact bytes; read: parse identically
import json, sqlite3
def write_run(conn, run: RunRecord) -> None:
    conn.execute("INSERT INTO runs(id,started_at,target,schema_version,record_json) VALUES(?,?,?,?,?)",
                 (str(run.id), run.started_at.isoformat(), run.target, run.schema_version,
                  run.model_dump_json()))
    for p in run.pages:
        conn.execute("INSERT INTO page_results(run_id,record_json) VALUES(?,?)",
                     (str(run.id), p.model_dump_json()))
    conn.commit()

def read_run(conn, run_id: str) -> RunRecord:
    row = conn.execute("SELECT record_json FROM runs WHERE id=?", (run_id,)).fetchone()
    return RunRecord.model_validate_json(row[0])   # criterion #1: equals the written model
```
> **Round-trip identity is at the model level** (`read == written` as Pydantic objects), not necessarily byte-level after a re-serialize. To assert true byte-identity, store `model_dump_json()` and compare the re-read string; for object identity, compare `model_dump()` dicts. Decide which the success criterion means — **recommend model-equality** (`read_run(...).model_dump() == original.model_dump()`), which is robust to key-ordering and float-repr quirks.

### Pattern 4: Promote a field later WITHOUT rewriting the table (D-07 mechanism)
**What:** The "cheap additive ADD COLUMN" path. **Critical constraint:** SQLite cannot add a **STORED** generated column via `ALTER TABLE` (it would require rewriting every row) — only **VIRTUAL** ones. VIRTUAL columns are computed on read and **are indexable**.
**Example:**
```sql
-- Source: sqlite.org/lang_altertable.html + sqlite.org/gencol.html  [CITED]
-- WRONG (errors: "cannot add a STORED column"):
-- ALTER TABLE page_results ADD COLUMN lcp_median REAL GENERATED ALWAYS AS (json_extract(record_json,'$.lcp_ms.median')) STORED;
-- RIGHT:
ALTER TABLE page_results
  ADD COLUMN lcp_median REAL
  GENERATED ALWAYS AS (json_extract(record_json, '$.lcp_ms.median')) VIRTUAL;
CREATE INDEX idx_pr_lcp ON page_results(lcp_median);   -- VIRTUAL cols can be indexed
```

### Pattern 5: Polarity-driven RunDelta engine (single registry, derived direction)
**What:** `direction` is derived from a central polarity table, never hardcoded at call sites (D-09). Edge cases routed through the status enum (D-11). `deltaPct` guarded against `previous == 0` (D-10).
**Example:**
```python
# registry.py — the ONE editable place for polarity (D-09)
from enum import StrEnum
class Polarity(StrEnum):
    LOWER_IS_BETTER = "lower"     # LCP, CLS, TBT/inp_proxy, TTFB, total_bytes, request_count, slowest_ms
    HIGHER_IS_BETTER = "higher"   # perf/a11y/seo/best-practices scores
METRIC_POLARITY: dict[str, Polarity] = {
    "lcp_ms": Polarity.LOWER_IS_BETTER, "cls": Polarity.LOWER_IS_BETTER,
    "inp_proxy_tbt_ms": Polarity.LOWER_IS_BETTER, "ttfb_ms": Polarity.LOWER_IS_BETTER,
    "total_bytes": Polarity.LOWER_IS_BETTER, "request_count": Polarity.LOWER_IS_BETTER,
    "slowest_request_ms": Polarity.LOWER_IS_BETTER,
    "perf_score": Polarity.HIGHER_IS_BETTER, "a11y_score": Polarity.HIGHER_IS_BETTER,
    "seo_score": Polarity.HIGHER_IS_BETTER, "best_practices_score": Polarity.HIGHER_IS_BETTER,
}

# models.py
class DirectionStatus(StrEnum):
    IMPROVEMENT = "improvement"; REGRESSION = "regression"; UNCHANGED = "unchanged"
    NEW = "new"; REMOVED = "removed"; NOT_COMPARABLE = "not_comparable"

class RunDelta(BaseModel):
    url_key: str; metric: str
    current: float | None; previous: float | None
    delta_abs: float | None; delta_pct: float | None   # None when previous==0 (D-10) or one side missing
    direction: DirectionStatus

# delta.py — derive direction from polarity (D-09..D-12, RAW only — no noise band)
def classify(metric, current, previous):
    if previous is None and current is not None: return DirectionStatus.NEW
    if current is None and previous is not None: return DirectionStatus.REMOVED   # emitted, never dropped (D-11)
    if metric not in METRIC_POLARITY:           return DirectionStatus.NOT_COMPARABLE  # schema drift (D-11)
    if current == previous:                      return DirectionStatus.UNCHANGED  # literal equality (D-12)
    better = (current < previous) if METRIC_POLARITY[metric] is Polarity.LOWER_IS_BETTER else (current > previous)
    return DirectionStatus.IMPROVEMENT if better else DirectionStatus.REGRESSION

def safe_pct(current, previous):
    if previous in (None, 0) or current is None: return None   # guard inf/NaN (D-10)
    return (current - previous) / previous * 100.0
```
> `compute_deltas(current_run, previous_run)` joins both runs' pages on `url_key`, iterates the **union** of pages so removed pages are emitted with `direction=removed` (D-11), and the **union** of metrics so a metric on only one side yields `not_comparable`.

### Anti-Patterns to Avoid
- **Storing the record as JSONB for the round-trip column:** JSONB re-serializes to canonical form and won't match input bytes — use TEXT for `record_json`. `[CITED: sqlite.org/json1.html]`
- **Adding STORED generated columns via `ALTER TABLE`:** errors out ("cannot add a STORED column"); use VIRTUAL when adding later. `[CITED: sqlite.org/lang_altertable.html]`
- **Hardcoding lower/higher-is-better at delta call sites:** violates D-09; one registry only.
- **Silently dropping disappeared pages:** D-11 requires emitting `direction=removed`.
- **A bare `inp` field:** D-15 — always the explicitly-labeled TBT proxy.
- **Computing `deltaPct` without the `previous==0` guard:** yields inf/NaN; D-10 requires `null`.
- **Modeling v2 backend metrics now:** D-16 — additive evolution makes them free later.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| RFC-3986 URL normalization (percent-case, dot-segments, safe encoding, query sort, fragment drop) | A custom `urlparse`-based normalizer | `w3lib.url.canonicalize_url` | Percent-encoding case rules, IDN/host handling, query re-encoding, and "safe" character sets are a notorious source of subtle bugs; w3lib is the Scrapy-grade implementation. (D-03 trailing-slash + D-04 denylist are the only thin wrapper you write.) |
| Tracking-param removal | Regex over the query string | `w3lib.url.url_query_cleaner(url, denylist, remove=True)` | Correct re-parse/re-encode; you only maintain the denylist constant. |
| JSON (de)serialization of nested typed records | `json.dumps`/`loads` + manual dict→object mapping | Pydantic `model_dump_json()` / `model_validate_json()` | Handles nested submodels, enums, UUIDs, datetimes, and validation in one call; manual mapping reintroduces the retrofit risk this phase exists to prevent. |
| Keeping a promoted column in sync with the blob | Write the scalar twice (blob + column) at insert | SQLite generated columns (`json_extract`) | A computed column cannot drift from its source blob; double-writing can. |
| Median aggregation (Phase 2) | A hand-rolled median | stdlib `statistics.median` | Correct even-N handling; no dep. (Phase 2 concern — noted so the model's `samples[]` is ready.) |
| schemaVersion migration framework | A migration engine now | Pydantic optionals + `extra="ignore"` (D-08) | Additive-only evolution needs no migrations; build the registry only when a real non-additive change forces it. |

**Key insight:** This phase's entire reason to exist is "one contract that never needs retrofitting." Every hand-rolled serializer/normalizer is a future retrofit; lean on Pydantic + w3lib + SQLite generated columns so the contract evolves additively and the promoted columns are provably consistent with the blob.

## Runtime State Inventory

> This is a **greenfield create phase**, not a rename/refactor/migration. No prior runtime state exists. Included for completeness; all categories verified empty.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | **None** — no datastore exists yet; this phase *creates* the first SQLite store. Verified: no `*.db`/`*.sqlite` files in repo, no source dir. | none |
| Live service config | **None** — no external services; pure library. | none |
| OS-registered state | **None** — no scheduled tasks/daemons. | none |
| Secrets/env vars | **None** — Phase 1 reads no secrets (auth is Phase 4). | none |
| Build artifacts | **None** — no `pyproject.toml`, no `*.egg-info`, no venv yet. Verified: repo contains only `CLAUDE.md` + `.planning/`. | none (scaffolding creates them fresh) |

## Common Pitfalls

### Pitfall 1: Confusing model-equality with byte-equality for round-trip (criterion #1)
**What goes wrong:** A test asserts the re-read JSON string equals the written string and fails on float repr / key ordering, even though the data is identical.
**Why it happens:** JSON serialization is not canonical across re-encodes; floats and dict ordering can vary.
**How to avoid:** Define round-trip identity as **model equality** — `read_run(write_run(r)).model_dump() == r.model_dump()` (or `==` on the models). If true byte-identity is required, store and compare the exact `model_dump_json()` bytes (TEXT column preserves them) and never re-serialize before comparing.
**Warning signs:** Flaky equality assertions; differences only in `1.0` vs `1`.

### Pitfall 2: `ALTER TABLE ADD COLUMN ... STORED` fails
**What goes wrong:** Promoting a field later (D-07) with a STORED generated column throws "cannot add a STORED column."
**Why it happens:** STORED columns physically write per row; `ALTER TABLE` can't backfill existing rows.
**How to avoid:** Use `... GENERATED ALWAYS AS (...) VIRTUAL` when adding later; declare STORED columns only in the initial `CREATE TABLE`. VIRTUAL columns are still indexable. `[CITED: sqlite.org/lang_altertable.html]`
**Warning signs:** DDL error during a "promote a metric" migration test.

### Pitfall 3: `deltaPct` infinity/NaN when previous is 0 or null
**What goes wrong:** `(current - 0)/0` → `ZeroDivisionError` or `inf`; serializes to invalid JSON.
**Why it happens:** Missing the D-10 guard.
**How to avoid:** Return `None` whenever `previous in (None, 0)` (or `current is None`). Test the `previous=0` fixture explicitly.
**Warning signs:** `Infinity`/`NaN` tokens in output JSON; pydantic serialization errors.

### Pitfall 4: Disappeared pages silently dropped
**What goes wrong:** Delta only iterates current-run pages, so a page present last run but gone now never appears.
**Why it happens:** Joining on current instead of the **union** of url_keys.
**How to avoid:** Iterate `set(current_keys) | set(previous_keys)`; emit `direction=removed` for previous-only keys (D-11).
**Warning signs:** Regression report misses a deleted page.

### Pitfall 5: A page's canonical key changes between runs for the "same" page
**What goes wrong:** The same logical page fails to self-join because tracking params, trailing slash, or query order differ run-to-run.
**Why it happens:** Canonicalization not applied, or applied inconsistently (e.g., denylist drift).
**How to avoid:** Always derive `url_key` via the single `canonical_key()` function; keep the denylist in `registry.py` only; test that `?utm_source=x`, trailing-slash, and reordered-query variants of one URL collapse to one key.
**Warning signs:** Two delta rows for what should be one page; everything shows up as `new`/`removed`.

### Pitfall 6: Over-merging distinct pages during canonicalization
**What goes wrong:** Stripping `www`, `index.html`, or a functional param (`?page=2`) collapses genuinely distinct resources into one key.
**Why it happens:** Being too aggressive — D-03/D-04 explicitly forbid stripping `www`/`index.html` and require keeping functional params.
**How to avoid:** Only drop the documented tracking params + fragment + trailing slash; keep everything else. Test that `?page=2` ≠ `?page=3` and `www.` host ≠ apex host.
**Warning signs:** Paginated pages share one key; metrics look averaged across pages.

## Code Examples

### Canonical key derivation (D-01..D-05)
```python
# canonical.py — w3lib does RFC-3986 + query sort + fragment drop; wrapper adds D-03/D-04.
# Source: w3lib.readthedocs.io (canonicalize_url, url_query_cleaner)  [CITED]
from urllib.parse import urlsplit, urlunsplit
from w3lib.url import canonicalize_url, url_query_cleaner
from perfcrawl.registry import TRACKING_PARAM_DENYLIST   # the ONE editable denylist (D-04)

def canonical_key(url: str) -> str:
    # 1) drop tracking params (D-04) — remove=True drops the listed keys
    cleaned = url_query_cleaner(url, TRACKING_PARAM_DENYLIST, remove=True, keep_fragments=False)
    # 2) RFC-3986 normalize: lowercase scheme+host, %-case, sort remaining query, drop fragment (D-02/D-04/D-05)
    canon = canonicalize_url(cleaned, keep_fragments=False)
    # 3) strip trailing slash except root (D-03) — w3lib does NOT do this
    parts = urlsplit(canon)
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path or "/", parts.query, ""))
```
```python
# registry.py — TRACKING_PARAM_DENYLIST: the one editable place (D-04)
TRACKING_PARAM_DENYLIST = [
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "ref", "ref_src",
]
```
> Verify w3lib's default-port stripping and percent-case behavior against fixtures during execution (`canonicalize_url` is `[ASSUMED]` to fully satisfy D-02 sub-rules until asserted in `test_canonical.py` — see Assumptions Log A2). If a sub-rule is missing, add it in the wrapper.

### Loading an older-schema run (criterion #3, D-08)
```python
# An older blob lacks fields added later; Optional defaults fill None, so it loads + stays comparable.
older_json = '{"id":"...","started_at":"2026-01-01T00:00:00Z","target":"x","schema_version":1,"pages":[{"url":"https://a/","url_key":"https://a/"}]}'
run = RunRecord.model_validate_json(older_json)   # newer model, missing fields -> None
assert run.pages[0].lcp_ms is None                # forward/back compatible
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Pydantic v1 `.json()` / `.parse_raw()` | Pydantic v2 `model_dump_json()` / `model_validate_json()` | Pydantic 2.0 (2023) | Use v2 methods; v1 methods deprecated. `[CITED: docs.pydantic.dev/migration]` |
| JSON-in-SQLite as TEXT only | JSONB binary (3.45.0, 2024-01) + indexable generated columns | SQLite 3.45 (2024) | For this phase keep `record_json` as **TEXT** (byte round-trip); use generated columns for queries. `[CITED: sqlite.org/json1.html]` |
| pip + venv + setup.py | uv + pyproject.toml (lockfile) | 2024-2025 default | Greenfield scaffolding uses uv. `[CITED: CLAUDE.md]` |
| flake8 + black + isort | Ruff (lint+format, one tool) | 2023-2024 | Single dev dependency. `[CITED: CLAUDE.md]` |

**Deprecated/outdated:**
- PyPI `lighthouse` package (abandoned 2016) — irrelevant here but a known trap; no Lighthouse in Phase 1.
- Pydantic v1 `Config` inner class → v2 `model_config = ConfigDict(...)`.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `w3lib` is the right runtime canonicalization dep (vs zero-dep stdlib wrapper) | Standard Stack / Don't Hand-Roll | Low — stdlib fallback documented; if team declines the dep, write ~50-line `urllib.parse` wrapper. Planner should confirm via the slopcheck checkpoint. |
| A2 | `canonicalize_url` fully satisfies D-02 sub-rules (default-port strip, dot-segment resolution, %-hex uppercasing) without extra wrapper code | Code Examples / canonical.py | Low-Med — if a sub-rule is missing it surfaces immediately in `test_canonical.py`; fix in the wrapper. Verify during execution. |
| A3 | "Read back identically" (criterion #1) means **model equality**, not byte-identity | Patterns 3 / Pitfall 1 | Med — if the user means strict byte-identity, store+compare exact `model_dump_json()` bytes (TEXT preserves them). Planner/discuss should confirm the intended semantics. |
| A4 | Tracking-param denylist contents (the specific keys) | Code Examples / registry.py | Low — denylist is explicitly editable (D-04); starting set is conventional (`utm_*`, `gclid`, `fbclid`, …). Extend freely later. |
| A5 | slopcheck verdicts for all packages (tool unavailable this session) | Package Legitimacy Audit | Low — all verified via PyPI + authoritative source repos; `w3lib` gated behind a checkpoint as the one new dep. |

## Open Questions

1. **Round-trip identity semantics (criterion #1) — model-equality vs byte-identity.**
   - What we know: TEXT column preserves exact `model_dump_json()` bytes; model-equality is robust to JSON re-encode quirks.
   - What's unclear: which the success criterion intends.
   - Recommendation: implement **model-equality** as the assertion (`read.model_dump() == original.model_dump()`) and *additionally* keep the exact written bytes in `record_json` so byte-identity is available if needed. Flag in discuss-phase (A3).

2. **Does `compute_deltas` emit one `RunDelta` per (page, metric), or a nested per-page structure?**
   - What we know: D-10 names the delta fields; D-11 needs page-level `new`/`removed`.
   - What's unclear: flat list of `(url_key, metric, …)` rows vs a per-page object with a metrics map.
   - Recommendation: emit a **flat `list[RunDelta]` keyed by (url_key, metric)** — simplest to test on fixtures and trivial for Phase 6 to group/threshold. Left to planner's discretion per CONTEXT (delta implementation is discretionary).

3. **Where does median-of-N aggregation run?**
   - What we know: D-14 stores `median` + `samples[]`; Phase 2 fills them.
   - What's unclear: whether Phase 1 ships a `statistics.median` helper.
   - Recommendation: Phase 1 only defines the `MetricSample` shape; do **not** build aggregation (it's Phase 2). Note stdlib `statistics.median` exists so Phase 2 adds no dep.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|----------|
| Python 3.12+ | everything | ✓ | 3.14.0 | — |
| stdlib `sqlite3` (JSON1, generated columns, STRICT) | store layer | ✓ | sqlite 3.50.4 | — (all features confirmed present) |
| stdlib `urllib.parse` / `uuid` / `datetime` / `statistics` | models, canonical, delta | ✓ | stdlib | — |
| pip | bootstrap | ✓ | 25.3 | — |
| **uv** | dependency/venv mgmt (CLAUDE.md) | ✗ | — | `pip` + `venv` + a hand-written `pyproject.toml` (works; loses lockfile speed). **Install uv first.** |
| **Ruff** | lint/format (CLAUDE.md) | ✗ | — | Install via `uv add --dev ruff` (or pip). Non-blocking for tests. |
| **pytest** | all four success criteria | ✗ | — | Install via `uv add --dev pytest` (or pip). **Blocking for verification** — must be installed before tests run. |
| **Pydantic** | model layer | ✗ (not yet) | target 2.13.4 | None — required; install via uv/pip. |
| **w3lib** | canonicalization | ✗ (not yet) | target 2.4.1 | stdlib `urllib.parse` wrapper (Alternatives Considered). |

**Missing dependencies with no fallback:** pytest, Pydantic (both installed via the scaffolding step — first task of the plan).
**Missing dependencies with fallback:** uv (→ pip+venv), Ruff (deferrable), w3lib (→ stdlib wrapper).

> **Planner action:** the first plan task must be project scaffolding — install uv (verify installer), `uv init --package`, add pydantic + w3lib + (dev) pytest + ruff. All later tasks depend on this. The w3lib install should sit behind a `checkpoint:human-verify` (slopcheck unavailable; A5).

## Validation Architecture

> `workflow.nyquist_validation: true` in config — section required.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.3 (pin `>=8,<10`) |
| Config file | none yet — add `[tool.pytest.ini_options]` to `pyproject.toml` in Wave 0 |
| Quick run command | `uv run pytest -x -q` (or `python -m pytest -x -q`) |
| Full suite command | `uv run pytest` |

### Phase Requirements → Test Map
| Req ID / Criterion | Behavior | Test Type | Automated Command | File Exists? |
|--------------------|----------|-----------|-------------------|--------------|
| Criterion #1 / HIST-01 | Write run → read back identical (model equality) | unit | `pytest tests/test_store.py::test_round_trip_identity -x` | ❌ Wave 0 |
| Criterion #1 | Exact bytes preserved in `record_json` TEXT column | unit | `pytest tests/test_store.py::test_record_json_bytes_preserved -x` | ❌ Wave 0 |
| Criterion #2 | Polarity-driven direction (lower vs higher is better) on fixtures | unit | `pytest tests/test_delta.py::test_direction_by_polarity -x` | ❌ Wave 0 |
| Criterion #2 / D-10 | `deltaPct` is None when previous==0 (no inf/NaN) | unit | `pytest tests/test_delta.py::test_deltapct_zero_guard -x` | ❌ Wave 0 |
| Criterion #2 / D-11 | new / removed / not_comparable emitted (removed never dropped) | unit | `pytest tests/test_delta.py::test_edge_status_enum -x` | ❌ Wave 0 |
| Criterion #2 / D-12 | `unchanged` == literal equality (no noise band) | unit | `pytest tests/test_delta.py::test_unchanged_is_literal -x` | ❌ Wave 0 |
| Criterion #3 / D-06,D-08 | Old-schema blob loads under newer model (missing fields → None) | unit | `pytest tests/test_store.py::test_old_schema_loads -x` | ❌ Wave 0 |
| Criterion #3 | `schema_version` defaults correctly + persists | unit | `pytest tests/test_models.py::test_schema_version_default -x` | ❌ Wave 0 |
| Criterion #4 / D-02..D-05 | tracking params dropped, query sorted, trailing slash stripped, fragment dropped, %-case normalized | unit | `pytest tests/test_canonical.py -x` | ❌ Wave 0 |
| Criterion #4 | same logical page → same key across run variants (self-join works) | unit | `pytest tests/test_canonical.py::test_variants_collapse -x` | ❌ Wave 0 |
| Criterion #4 / D-03,D-04 | distinct pages NOT over-merged (`?page=2`≠`?page=3`, www≠apex) | unit | `pytest tests/test_canonical.py::test_no_over_merge -x` | ❌ Wave 0 |
| D-07 | promote a metric via VIRTUAL generated column (STORED ALTER rejected) | unit | `pytest tests/test_store.py::test_promote_column_virtual -x` | ❌ Wave 0 |
| D-15 | bare `inp` field rejected; only labeled TBT proxy allowed | unit | `pytest tests/test_models.py::test_inp_proxy_naming -x` | ❌ Wave 0 |

### Fixture Data Shape Needed
- `tests/fixtures/run_v1.json` — a full `RunRecord` with ≥2 pages, populated metrics + `samples[]` + an `analysis` block.
- `tests/fixtures/run_v1_old_schema.json` — same run with later-phase fields **absent** (proves criterion #3).
- A **two-run pair for one site** in `conftest.py` covering: an improved metric, a regressed metric, an unchanged metric, a `previous=0` metric, a **new** page, a **removed** page, and a metric present on only one side (`not_comparable`) — one fixture exercises all of D-09..D-12.
- URL variant set for `test_canonical.py`: `[https://Example.com/Path/?utm_source=x&b=2&a=1#frag, https://example.com/Path?a=1&b=2]` should collapse to one key; `?page=2` vs `?page=3` and `www.` vs apex must stay distinct.

### Sampling Rate
- **Per task commit:** `uv run pytest -x -q` (whole suite is tiny/fast — run it all).
- **Per wave merge:** `uv run pytest`
- **Phase gate:** full suite green before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] Project scaffolding: `pyproject.toml` (uv), `src/perfcrawl/` package, `[tool.pytest.ini_options]`, `[tool.ruff]` — nothing exists yet (greenfield).
- [ ] Install pytest + pydantic + w3lib (w3lib behind checkpoint) — none installed.
- [ ] `tests/conftest.py` — shared fixtures (the two-run pair, sample RunRecord).
- [ ] `tests/fixtures/*.json` — `run_v1.json`, `run_v1_old_schema.json`, delta-pair JSON.
- [ ] All five `tests/test_*.py` files listed above.

## Security Domain

> `security_enforcement` not present in config → treat as enabled. Most ASVS categories are **N/A** for a pure offline library with no network, auth, sessions, or user input at runtime in this phase. The relevant ones:

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|------------------|
| V2 Authentication | no | No auth in Phase 1 (Phase 4). `auth_used` is a nullable flag only. |
| V3 Session Management | no | No sessions. |
| V4 Access Control | no | Local library, no actors. |
| V5 Input Validation | **yes (light)** | Pydantic validates all model input; the canonicalizer parses untrusted URL strings (defensive — must not crash on malformed input). |
| V6 Cryptography | no | No crypto/secrets in Phase 1. |
| V7/V8 Errors & Data Protection | **yes (light)** | The persisted blob may later contain page URLs; no secrets are stored in Phase 1. SQLite file is local; document that it is gitignored when it later holds real data. |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection in the store layer | Tampering | Use **parameterized queries only** (`?` placeholders) — never f-string SQL. Already shown in Pattern 3. |
| Malformed/hostile URL crashing canonicalizer | Denial of Service | `canonical_key()` must handle non-URL strings gracefully (return a deterministic fallback or raise a typed error) — add a malformed-input fixture to `test_canonical.py`. |
| JSON deserialization of an untrusted blob | Tampering | `model_validate_json()` validates against the schema (no arbitrary object construction); `extra="ignore"` drops unknown keys. Safe. |
| Path/SQLite file handling | Tampering | Open the DB by explicit path; no dynamic table names. |

## Sources

### Primary (HIGH confidence)
- Local toolchain probes — Python 3.14.0, sqlite 3.50.4 (JSON1 + generated columns + STRICT confirmed), pip 25.3; uv/ruff/pytest absent. `[VERIFIED: local]`
- PyPI `pip index versions` — pydantic 2.13.4, w3lib 2.4.1, uv 0.11.16, ruff 0.15.14, pytest 9.0.3, url-normalize 3.0.0, courlan 1.3.2. `[VERIFIED: PyPI]`
- https://sqlite.org/json1.html — JSON storage TEXT vs JSONB, generated columns via `json_extract`, byte preservation. `[CITED]`
- https://sqlite.org/gencol.html + https://sqlite.org/lang_altertable.html — STORED-via-ALTER restriction; VIRTUAL columns addable + indexable. `[CITED]`
- https://docs.pydantic.dev/latest/concepts/models, /concepts/fields, /concepts/validators, /migration — v2 serialize/validate, `extra` config, `model_validator`, optionals. `[CITED]`
- https://w3lib.readthedocs.io/en/latest/w3lib.html — `canonicalize_url` and `url_query_cleaner` behavior + params. `[CITED]`
- CLAUDE.md (project root) — locked stack: Pydantic 2.x, SQLite over DuckDB/JSON, schema sketch + self-join pattern, uv/Ruff/pytest, INP-as-lab-proxy. `[CITED]`
- 01-CONTEXT.md (D-01..D-17) — locked decisions. `[CITED]`

### Secondary (MEDIUM confidence)
- WebSearch (verified against the official docs above) on Pydantic forward-compat (`extra="ignore"`), SQLite hybrid JSON+generated-column patterns, w3lib canonicalization scope, and the ALTER-TABLE STORED restriction.

### Tertiary (LOW confidence)
- None relied upon. w3lib's exact coverage of every D-02 sub-rule is marked `[ASSUMED]` (A2) pending execution-time test assertions.

## Metadata

**Confidence breakdown:**
- Standard stack: **HIGH** — locked by CLAUDE.md/CONTEXT and re-verified against PyPI + local toolchain.
- Architecture (TEXT-blob + generated columns; VIRTUAL-on-ALTER; polarity registry; canonicalizer split): **HIGH** — confirmed against official SQLite + Pydantic + w3lib docs.
- Pitfalls: **HIGH** — each tied to a documented behavior (JSONB re-serialize, STORED ALTER error, deltaPct guard).
- w3lib full D-02 coverage: **MEDIUM** — verify the few sub-rules in `test_canonical.py` (A2).
- Round-trip semantics intent (model vs byte): **MEDIUM** — flagged for confirmation (A3).

**Research date:** 2026-05-25
**Valid until:** 2026-06-24 (~30 days; stable stdlib + stable Pydantic 2.x / SQLite features)
