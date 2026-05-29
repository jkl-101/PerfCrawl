# Phase 2: Single-Page Measurement Slice - Pattern Map

**Mapped:** 2026-05-28
**Files analyzed:** 19 new (8 Python modules + 3 Node files + 8 test modules + 3 fixtures) + 1 modified (`pyproject.toml`)
**Analogs found:** 16 / 19 (3 with no Python analog: the `lighthouse-worker/*` Node files; documented under "No Analog Found")

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/perfcrawl/cli.py` | controller (CLI entry) | request-response | `src/perfcrawl/store.py` (top-level module entry + docstring shape) | role-match (no prior CLI exists; mostly fresh from RESEARCH Pattern + Typer docs) |
| `src/perfcrawl/orchestrator.py` | service (subprocess + Playwright lifecycle) | event-driven (per-sample loop, retry, timeout) | `src/perfcrawl/store.py` `write_run` (atomic `with` block + per-connection re-assert) | role-match |
| `src/perfcrawl/lighthouse_worker.py` | service (Python-side subprocess wrapper) | request-response (one-shot subprocess per call) | `src/perfcrawl/canonical.py` (defensive try/except + deterministic fallback) | role-match |
| `src/perfcrawl/normalizer.py` | transform (LH JSON → `PageResult`) | transform | `src/perfcrawl/canonical.py` (single-purpose external-input → typed-output transform with defensive guards) | exact-shape (both are pure-function boundary transforms) |
| `src/perfcrawl/aggregator.py` | utility (median-of-N over `MetricSample.samples[]`) | transform (list → scalar) | `src/perfcrawl/delta.py` `safe_pct` / `_safe_abs` (finite-guard pattern) | exact-shape |
| `src/perfcrawl/slug.py` | utility (IN-02-safe URL → filesystem-safe stem) | transform | `src/perfcrawl/canonical.py` `canonical_key` (defensive try/except + deterministic fallback for arbitrary external strings) | **exact** — explicitly called out in LEARNINGS as the pattern to mirror |
| `src/perfcrawl/output.py` | service (CSV + JSON + raw artifact writers) | file-I/O | `src/perfcrawl/store.py` `write_run` (atomic-write pattern, model-driven serialization via `model_dump_json()`) | role-match |
| `src/perfcrawl/constants.py` | config (single-place tunables) | n/a | `src/perfcrawl/registry.py` (`TRACKING_PARAM_DENYLIST`, `METRIC_POLARITY`) | **exact** — explicitly the "one-editable-place registry tables" pattern |
| `pyproject.toml` (modified) | config | n/a | existing `pyproject.toml` shape | exact |
| `lighthouse-worker/package.json` | config (Node sibling project) | n/a | — | no analog (first Node surface in repo) |
| `lighthouse-worker/package-lock.json` | config (lockfile) | n/a | `uv.lock` (committed Python lockfile) | role-only — "commit lockfiles for byte-identical installs" |
| `lighthouse-worker/run.mjs` | service (Node Lighthouse worker) | request-response (argv-in, stdout-JSON-out) | — | no Python analog; mostly from RESEARCH Pattern 2 |
| `tests/test_normalizer.py` | test (unit + fixture) | request-response | `tests/test_canonical.py` (boundary-function tests + parametrize over inputs) | **exact** |
| `tests/test_slug.py` | test (unit + parametrize) | request-response | `tests/test_canonical.py` (parametrize over malformed inputs + deterministic-output asserts) | **exact** |
| `tests/test_aggregator.py` | test (unit + finite-guard) | request-response | `tests/test_models.py::test_metric_sample_rejects_non_finite_*` (parametrize over `[math.inf, -math.inf, math.nan]`) | **exact** |
| `tests/test_worker.py` | test (subprocess contract, mocked) | request-response | `tests/test_canonical.py` parametrize style + `tests/test_store.py::test_write_run_is_atomic_on_failure` (subclass-to-inject-failure pattern) | role-match |
| `tests/test_orchestrator.py` | test (integration, mocked Playwright + subprocess) | event-driven | `tests/test_store.py::test_write_run_reasserts_foreign_keys` (use `tmp_path`, create real DB on disk, multi-step assertion) | role-match |
| `tests/test_output.py` | test (integration, on-disk artifacts) | file-I/O | `tests/test_store.py::test_round_trip_identity` + `test_record_json_bytes_preserved` (write + read-back + byte-equality) | role-match |
| `tests/test_cli.py` | test (Typer CliRunner) | request-response | `tests/test_canonical.py` parametrize style (no existing Typer test in repo) | partial-match |
| `tests/test_e2e.py` | test (optional `@pytest.mark.e2e`) | end-to-end | — | new test marker; pattern: `pytest.mark.X` registered in `conftest.py` |
| `tests/fixtures/lighthouse/studyhalo-home-200.json` | test fixture | n/a | `tests/fixtures/run_v1.json` (committed JSON fixture loaded by `conftest.py`) | role-match |
| `tests/fixtures/lighthouse/studyhalo-404.json` | test fixture | n/a | `tests/fixtures/run_v1_old_schema.json` (variant fixture beside the canonical one) | role-match |
| `tests/fixtures/lighthouse/version-drift-14.json` | test fixture | n/a | `tests/fixtures/run_v1_old_schema.json` (synthetic variant for the gate test) | role-match |

---

## Pattern Assignments

### `src/perfcrawl/slug.py` (utility, transform — IN-02 boundary)

**Analog:** `src/perfcrawl/canonical.py` (THE Phase-1 pattern that LEARNINGS explicitly calls out as the template for D-07: defensive try/except + deterministic fallback for untrusted external input that will become a filesystem path).

**Module docstring shape** (`canonical.py` lines 1-31):

```python
"""Canonical URL key derivation — success criterion #4, D-01..D-05.

``canonical_key(url)`` derives the stable cross-run page-identity key used to
self-join the same logical page across runs (D-01: the raw URL is stored
separately and never mutated — this function only *derives* the key).

…

Malformed / non-URL input never raises — it returns a deterministic value
(Security Domain DoS mitigation, threat T-01-01). Specifically:

  - empty or whitespace-only input short-circuits to ``""`` (the empty-key
    sentinel) BEFORE w3lib runs, so blank/garbage-that-normalizes-to-empty inputs
    do NOT collapse onto the real root key (e.g. ``"https://x.com/"``) and merge
    distinct broken pages into one cross-run identity (WR-03);
  - the rare input that makes w3lib raise falls back to the stripped original.
"""
```

**For slug.py:** same shape — docstring opens with what the function does, then a "Never raises on…" paragraph, then an enumerated list of failure modes and their deterministic fallback. Cite **D-07 and the IN-02 landmine** in the opening lines so any future reader sees the threat model before the code.

**Defensive try/except + deterministic fallback** (`canonical.py` lines 62-108):

```python
def canonical_key(url: str) -> str:
    """Derive the canonical cross-run identity key for ``url`` (D-01..D-05).

    Never raises on malformed input — returns a deterministic string so an
    untrusted/hostile URL cannot crash the pipeline (threat T-01-01).
    …
    """
    # WR-03: handle empty/blank input explicitly. Without this, w3lib normalizes
    # "" and "   " to a "/" path, colliding every blank/empty-normalizing input
    # onto the single real root key…
    if not (url or "").strip():
        return ""
    try:
        # … the real work …
        return urlunsplit((parts.scheme, netloc, path or "/", parts.query, ""))
    except Exception:
        # Deterministic, never-raising fallback for non-URL / malformed input.
        return (url or "").strip()
```

**For `page_slug(url_key: str, *, max_len: int = 80) -> str`:** same scaffolding — `if not (url_key or "").strip(): return "_"` short-circuit (a no-empty-slug sentinel that is distinct from any real slug; `"_"` because `""` would be a path-injection risk in some shells), `try: … except Exception: return "_"` deterministic fallback. The "real work" between is the RESEARCH § Pattern 4 body (drop scheme, replace `/` with `_`, collapse `..` runs to `__`, `re.sub(r"[^A-Za-z0-9._-]+", "_", …)`, strip leading/trailing `._-`, truncate to `max_len`).

**Inline-comment idiom** for the IN-02 guard: cite the LEARNINGS surprise verbatim — `"# IN-02: w3lib decodes %2e%2e to literal '../' in url_key; this is the documented sanitization boundary"`. The Phase 1 LEARNINGS section "Patterns" → "Defensive try/except + deterministic fallback for untrusted input" is the exact reference this docstring should cite.

---

### `src/perfcrawl/normalizer.py` (transform, LH JSON → `PageResult`)

**Analog:** `src/perfcrawl/canonical.py` (shape: pure-function boundary transform with hard-error-on-violation + defensive guards on per-field reads).

**Version gate (hard-error) pattern** — modeled on the `MetricSample` `allow_inf_nan=False` invariant from `models.py` (fail-loud at the model boundary, never silently null) and the LEARNINGS "Pydantic v2 accepts inf/nan by default; needs `allow_inf_nan=False`" lesson. The version gate is the same shape: hard-raise on schema drift, never silently produce a corrupted record. From `models.py` lines 64-68:

```python
# allow_inf_nan=False rejects inf/nan at validation time (WR-01): Pydantic's
# JSON mode serializes those to ``null``, so an inf/nan metric would silently
# become None on write and break the byte-identical round-trip (criterion #1,
# Pitfall 1). Failing loud here surfaces an upstream measurement bug instead.
model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
```

**For normalizer.py version gate** (D-10, from RESEARCH Pattern 3):

```python
from perfcrawl.constants import EXPECTED_LIGHTHOUSE_MAJOR_MINOR  # "13.x"

def _check_version(lhr: dict) -> None:
    """Hard-error on Lighthouse major drift (D-10).

    Prevents silent audit-shape corruption on a lockfile bump (the realistic
    failure mode where someone upgrades to 14.0 without updating the parser).
    Mirrors the model-layer fail-loud invariant from PageResult.allow_inf_nan
    (WR-01): silent corruption is the worst-case outcome, so raise here.
    """
    actual = lhr.get("lighthouseVersion", "")
    expected_major = EXPECTED_LIGHTHOUSE_MAJOR_MINOR.split(".")[0]
    if not actual.startswith(expected_major + "."):
        raise ValueError(
            f"Lighthouse version mismatch: expected major {expected_major}.x, "
            f"got {actual!r}. Normalizer is locked to LH "
            f"{EXPECTED_LIGHTHOUSE_MAJOR_MINOR} audit shape; refusing to "
            f"silently produce a corrupted PageResult."
        )
```

**Per-field defensive reads** — mirror the `_scalar()` helper in `delta.py` lines 68-82:

```python
def _scalar(page: PageResult | None, metric: str) -> float | None:
    if page is None:
        return None
    value = getattr(page, metric, None)
    if value is None:
        return None
    if isinstance(value, MetricSample):
        return value.median
    return value
```

**For normalizer field readers:** small named helpers that return `None` instead of raising on missing keys (`_numeric(audit_id) -> float | None`, `_cat_score(key) -> float | None`). Pattern: `audits.get(audit_id, {}).get("numericValue")` — chained `.get()` so a missing `audits` dict or missing audit ID never raises. From RESEARCH § "Normalizer skeleton":

```python
def _numeric(audit_id: str) -> float | None:
    v = audits.get(audit_id, {}).get("numericValue")
    return float(v) if v is not None else None

def _cat_score(key: str) -> float | None:
    score = cats.get(key, {}).get("score")
    return float(score * 100) if score is not None else None
```

**LH 13 waterfall key names** (RESEARCH Pitfall 2 — load-bearing for METRIC-03):

```python
# LH 12+ split startTime/endTime into THREE fields. The old keys silently
# return None and produce null timing_ms on every WaterfallEntry — see Pitfall 2.
start = item.get("networkRequestTime")
end = item.get("networkEndTime")
timing = (end - start) if (start is not None and end is not None) else None
```

**INP-proxy mapping** (D-11/D-15, the labeled-proxy invariant) — mirror the `models.py` `_no_bare_inp` validator pattern (lines 152-166) at the call site by **never constructing a variable named `inp`** inside the normalizer; always assign directly to `inp_proxy_tbt_ms`. From `models.py`:

```python
_FORBIDDEN_INP_FIELDS = frozenset({"inp", "inp_ms", "interaction_to_next_paint"})

@model_validator(mode="after")
def _no_bare_inp(self) -> "PageResult":
    """Reject any bare-INP field at the model layer (D-15)."""
```

**For normalizer.py:** add a top-of-file comment block that documents the invariant ("Never name a local variable `inp` or `inp_ms` — the only INP-flavored slot is `inp_proxy_tbt_ms`. The model layer's `_no_bare_inp` validator is the floor; the normalizer should never get close to the floor."). The TBT read goes directly into `inp_proxy_tbt_ms`:

```python
# D-11/D-15: TBT IS the labeled lab proxy. NEVER name a local 'inp'.
inp_proxy_tbt_ms=MetricSample(
    median=_numeric("total-blocking-time"),
    samples=[_numeric("total-blocking-time")]
            if _numeric("total-blocking-time") is not None else [],
),
```

---

### `src/perfcrawl/aggregator.py` (utility, list → scalar median)

**Analog:** `src/perfcrawl/delta.py` `safe_pct` + `_safe_abs` (lines 124-153) — the **finite-guard pattern** from LEARNINGS that the planner-discretion items in CONTEXT explicitly point at.

**Finite-guard pattern from `delta.py` lines 140-153:**

```python
def _safe_abs(current: float | None, previous: float | None) -> float | None:
    """Absolute delta when both sides are present and finite, else ``None``.

    Mirrors the ``safe_pct`` finite guard (WR-01): subtracting two individually
    finite floats can still overflow to ``inf`` (e.g. ``1.5e308 - -1.5e308``).
    An ``inf``/``nan`` ``delta_abs`` serializes to ``null`` in Pydantic JSON
    mode, silently nulling a real delta, so a non-finite diff yields ``None``
    here (and ``RunDelta``'s ``allow_inf_nan=False`` is the model-layer backstop)
    to honor the documented "never inf/NaN" contract (D-10).
    """
    if current is None or previous is None:
        return None
    diff = current - previous
    return diff if isfinite(diff) else None
```

**For `aggregator.py::aggregate_samples()`:** same shape — drop `None`s up front, apply `math.isfinite()` defense-in-depth even though `MetricSample`'s `allow_inf_nan=False` would catch it on the way out, guard `statistics.median([])` with `if not clean: return MetricSample(median=None, samples=[])` (Pitfall 3 + D-16). From RESEARCH § "Median aggregation":

```python
import math
import statistics
from perfcrawl.models import MetricSample


def aggregate_samples(per_sample_values: list[float | None]) -> MetricSample:
    """D-14/D-16: median over successful samples; empty → honest empty.

    No padding, no fabricated median, no minimum-sample floor. If two of N
    samples produced an LCP, MetricSample.samples is length 2 (the empty
    distribution honestly reflects what was measured). Mirrors the finite-
    guard pattern from delta.py::_safe_abs (LEARNINGS).
    """
    # Drop None (failed sample for this metric) AND non-finite (defense in
    # depth — LH JSON shouldn't produce inf/nan, but MetricSample.allow_inf_nan=False
    # would otherwise reject the model on the way out anyway).
    clean = [v for v in per_sample_values if v is not None and math.isfinite(v)]
    if not clean:
        # D-16: honestly empty, never fabricated.
        # Pitfall 3: statistics.median([]) raises StatisticsError.
        return MetricSample(median=None, samples=[])
    return MetricSample(median=statistics.median(clean), samples=clean)
```

---

### `src/perfcrawl/constants.py` (config, single-place tunables)

**Analog:** `src/perfcrawl/registry.py` — THE Phase-1 "one editable place" pattern from LEARNINGS, explicitly called out in CONTEXT § "Established Patterns" as the shape Phase 2's constants module must mirror.

**Full shape of `registry.py`:**

```python
"""The two "one editable place" registry tables for PerfCrawl.

Later phases extend these constants *here only* — call sites never inline
the denylist or hardcode metric direction.

- ``TRACKING_PARAM_DENYLIST`` (D-04): query-param keys dropped during URL
  canonicalization. Consumed by ``perfcrawl.canonical.canonical_key``.
- ``Polarity`` / ``METRIC_POLARITY`` (D-09): each metric's "which direction is
  better" declaration. The RunDelta engine (Plan 03) derives ``direction`` from
  this table and never hardcodes lower/higher-is-better at call sites.
"""

from enum import StrEnum

# --- D-04: tracking-param denylist (the ONE editable place) -----------------
TRACKING_PARAM_DENYLIST: list[str] = [
    "utm_source",
    "utm_medium",
    …
]


# --- D-09: metric polarity (the ONE editable place) -------------------------
class Polarity(StrEnum):
    """Whether a smaller or larger value is the improvement for a metric."""
    LOWER_IS_BETTER = "lower"
    HIGHER_IS_BETTER = "higher"


METRIC_POLARITY: dict[str, Polarity] = {
    "lcp_ms": Polarity.LOWER_IS_BETTER,
    …
}
```

**For `constants.py`:** same opening docstring with the "Later phases extend these constants *here only*" sentence; one section per group, each prefixed by a `# --- D-XX: <name> (the ONE editable place) ---` banner that cites the source decision. Concrete list (from CONTEXT § "Established Patterns" and the planner's-discretion items in CONTEXT/RESEARCH):

```python
"""Single-place tunables for Phase 2 measurement (D-02..D-16).

Later phases extend these constants *here only* — call sites never inline
a timeout, sample count, version string, exit code, or column label.

- PER_SAMPLE_TIMEOUT_S (D-14): subprocess.run(timeout=…) for the Node worker.
- DEFAULT_SAMPLES_N (D-08/D-16, Claude's-discretion): default for `--samples`.
- EXPECTED_LIGHTHOUSE_MAJOR_MINOR (D-10): normalizer version gate.
- INP_PROXY_DISPLAY_LABEL (D-11): the human-summary column header for the TBT
  proxy. The CSV column name is the field name (`inp_proxy_tbt_ms`); the Rich
  table header reads the label declared here.
- EXIT_* (D-15): the three exit codes — 0 success / 1 user error / 2 measurement
  error. Phase 6 will add EXIT_BUDGET_EXCEEDED (deferred).
- DEVTOOLS_PORT_FILE_TIMEOUT_S / DEVTOOLS_PORT_POLL_INTERVAL_S (RESEARCH Pitfall 1):
  how long to wait for Chrome's DevToolsActivePort file before declaring launch
  failure.
"""

from enum import IntEnum

PER_SAMPLE_TIMEOUT_S: int = 60        # D-14 — adjustable; the only "per-sample timeout" reference in the codebase
DEFAULT_SAMPLES_N: int = 3            # D-08/D-16 — odd-N is friendlier for median
EXPECTED_LIGHTHOUSE_MAJOR_MINOR: str = "13.x"  # D-10 — bump when the worker's package-lock.json bumps

INP_PROXY_DISPLAY_LABEL: str = "INP (lab proxy, TBT-based)"  # D-11 — Rich table header only

DEVTOOLS_PORT_FILE_TIMEOUT_S: float = 5.0
DEVTOOLS_PORT_POLL_INTERVAL_S: float = 0.1


class ExitCode(IntEnum):
    """D-15: 0 success / 1 user error / 2 measurement error.

    Phase 6 budget verdicts (BUDG-01) will carve out 10+; the gap is intentional.
    """
    SUCCESS = 0
    USER_ERROR = 1
    MEASUREMENT_ERROR = 2
```

**Critical invariant from `registry.py` precedent:** call sites IMPORT from this module; **never inline** a literal `60` for the timeout, `3` for `--samples`, `"13.x"` for the version pin, `2` for the measurement-error exit code, or the INP label string. Phase 1's plan acceptance criteria grep-asserted this for the denylist; the planner should consider a similar grep guard for `constants.py` (e.g., `grep -rn "timeout=60" src/perfcrawl/ | grep -v constants.py | grep -v "test_"` must be empty).

---

### `src/perfcrawl/orchestrator.py` (service, event-driven sample loop)

**Analog:** `src/perfcrawl/store.py::write_run` (lines 81-154) — the atomic `with` block + per-connection re-assert pattern + try/finally cleanup. Plus the LEARNINGS "Atomic `with conn:` write_run" pattern.

**Imports + module docstring shape** (`store.py` lines 1-44):

```python
"""Hybrid SQLite run store — write a run, read it back identically (criterion #1).

The store is the persistence half of the Phase 1 data contract. It implements
the D-07 "hybrid" design: …

Security (threat T-01-T, T-01-P): every statement uses ``?`` placeholders — never
f-string / ``%`` / ``.format`` SQL — and the DB is opened by an explicit
caller-supplied path with no dynamic table names…
"""
```

**For orchestrator.py docstring:** open with "Per-sample Playwright + subprocess orchestration — Phase 2 D-01..D-04, D-14." Then a "Security" paragraph that calls out:
- "subprocess argv is always a `list[str]`, never `shell=True`" (RESEARCH § Security Domain "Shell injection via URL containing metacharacters")
- "Chrome lifecycle is wrapped in try/finally so a crash never leaks a zombie Chromium" (RESEARCH § Security Domain "Zombie Chrome processes")
- "Per-run tempdir via `tempfile.TemporaryDirectory()` so two concurrent `perfcrawl measure` runs cannot collide on user-data-dir" (RESEARCH § Assumption A6)

**Atomic+cleanup block pattern from `store.py` lines 134-154** (the LEARNINGS "Atomic `with conn:` write_run" pattern):

```python
# PRAGMA foreign_keys is PER-CONNECTION, not stored in the DB (WR-05). A
# caller who init_db()s once and later opens a fresh connection for writes
# gets foreign_keys=OFF by default…
conn.execute("PRAGMA foreign_keys = ON")

# `with conn:` is a transaction context manager — it COMMITs on a clean exit
# and ROLLS BACK if the block raises, so a half-written run is never left in
# an open transaction for a subsequent commit() to persist (CR-01).
with conn:
    conn.execute(
        "INSERT INTO runs (id, started_at, target, schema_version, record_json) "
        …
    )
```

**For orchestrator.py per-sample loop:** wrap Chrome launch in a `try: … finally: chrome.kill()` (and rmtree the user_data_dir) outer block; inside, wrap each sample in `try: subprocess.run(...) ... except subprocess.TimeoutExpired: <retry once>` (D-14). The retry shape mirrors `_safe_abs` / `safe_pct`'s "compute, then guard, then None on failure" style — `subprocess.run` returns a value or raises; the retry is wrapped in the same try/except shape; if BOTH fail, the sample is dropped (D-16 honest empty), never fabricated. From RESEARCH § Pattern 1:

```python
for i in range(samples):
    fresh = browser.new_context()  # D-03 cold cache
    try:
        proc = subprocess.run(
            ["node", "lighthouse-worker/run.mjs", f"--port={port}", f"--url={url}", …],
            capture_output=True, text=True, encoding="utf-8",
            timeout=PER_SAMPLE_TIMEOUT_S,  # constants.PER_SAMPLE_TIMEOUT_S
        )
        if proc.returncode == 0:
            sample_results.append(json.loads(proc.stdout))
        else:
            # D-14: one retry per sample
            …
    except subprocess.TimeoutExpired:
        # D-14: retry once on timeout too, same shape
        …
    finally:
        fresh.close()  # always cycle the context, even on failure
```

**Critical anti-pattern carryover:** the existing codebase uses `?` placeholders for SQL (never f-string interpolation, threat T-01-T). The Phase 2 orchestrator does the same for `subprocess.run` — **argv as a list, never `shell=True`**, never f-string-interpolate the URL into a shell string. Test for it: a parametrize test that passes a URL containing `;`, `&`, `$()`, backticks, etc. through `orchestrator.run(url)` and asserts no shell expansion occurred.

---

### `src/perfcrawl/lighthouse_worker.py` (service, Python-side subprocess wrapper)

**Analog:** `src/perfcrawl/canonical.py` (defensive try/except + deterministic-fallback shape, applied to subprocess output parsing rather than URL parsing).

**Three failure modes to model deterministically** (per D-14/D-15):
1. `subprocess.TimeoutExpired` → caller retries once; if retry also times out, sample is dropped.
2. `proc.returncode != 0` → same retry-once-or-drop policy.
3. `json.JSONDecodeError` on `proc.stdout` → treat as a worker-side failure, drop the sample.

**Pattern from `canonical.py` lines 84-108:**

```python
if not (url or "").strip():
    return ""
try:
    …
except Exception:
    return (url or "").strip()
```

**For lighthouse_worker.py:** expose a single function `run_one_sample(*, port: int, url: str, emulation: str, timeout_s: int) -> dict | None`. Returns `None` on any of the three failure modes (worker stderr is logged to the orchestrator's `err_console`, never silently discarded — that's the D-15 "actionable error message" requirement). Caller decides what to do with `None` (drop the sample per D-16; if all N return `None`, exit code 2 per D-14).

```python
def run_one_sample(*, port: int, url: str, emulation: str, timeout_s: int) -> dict | None:
    """Invoke the Node Lighthouse worker once; return parsed JSON or None on failure.

    Three failure modes all collapse to None (the caller's D-14 retry-or-drop is
    cleaner if the worker layer presents a single boolean signal):
      - subprocess.TimeoutExpired (Node hung)
      - proc.returncode != 0 (Lighthouse raised)
      - json.JSONDecodeError (worker stdout corrupted)

    Mirrors canonical.py's defensive try/except + deterministic-fallback shape
    (LEARNINGS): never raise on external-process flake.
    """
    try:
        proc = subprocess.run(
            ["node", "lighthouse-worker/run.mjs",
             f"--port={port}", f"--url={url}", f"--form-factor={emulation}"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
```

**Preflight check pattern** (RESEARCH Open Q5): mirror the deterministic-fallback ethos — if `lighthouse-worker/node_modules/lighthouse/package.json` does not exist, raise a `MeasurementError` with an actionable message ("run `cd lighthouse-worker && npm ci` before first invocation"), which the CLI maps to exit code 2 per D-15.

---

### `src/perfcrawl/output.py` (service, file-I/O writers)

**Analog:** `src/perfcrawl/store.py::write_run` (atomic-write pattern + serialization via `model_dump_json()`).

**Serialization pattern from `store.py` lines 139-153:**

```python
with conn:
    conn.execute(
        "INSERT INTO runs (id, started_at, target, schema_version, record_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            str(run.id),
            run.started_at.isoformat(),
            run.target,
            run.schema_version,
            run.model_dump_json(),  # ← the exact bytes; byte-identity preserved
        ),
    )
```

**For output.py:** writing the full-fidelity JSON to `output/<run_id>/result.json` is `Path(...).write_text(run_record.model_dump_json(indent=2))` — same source-of-truth as the store, so the on-disk JSON and the SQLite blob match byte-for-byte (modulo `indent=2`). The CSV row is built from the `RunRecord` via Pydantic accessors, never by hand-walking dicts.

**CSV column order — locked in this module** (mirrors the `METRIC_POLARITY` "one editable place" pattern: the CSV column list lives in exactly one place):

```python
# OUT-04: the CSV column order is a contract Phase 6's Sheets exporter reads.
# Adding a column = one edit here; the row builder iterates this list. Never
# inline a column name at a call site. (Same pattern as registry.METRIC_POLARITY.)
CSV_COLUMNS: list[str] = [
    "page", "url", "test_date", "cache_disabled",
    "total_page_load_time", "request_count", "total_bytes",
    "slowest_request_url", "slowest_request_ms", "ttfb_ms", "status_code",
    "perf_score", "a11y_score", "seo_score", "best_practices_score",
    "lcp_ms", "cls", "inp_proxy_tbt_ms",   # ← LABELED column name (D-11/D-15)
    "schema_version", "run_id", "chrome_version", "lighthouse_version", "emulation",
]
```

**The IN-02 boundary applies here** — every `output/<run_id>/lighthouse/<slug>.{json,html}` write goes through `page_slug(url_key)`, never `url_key` directly. From `canonical.py` lines 72-82 (the warning docstring): "MUST sanitize the key at that filesystem boundary; never treat `url_key` as a safe path component." Output.py is THAT boundary; the slug-sanitizer call is the load-bearing line.

**Atomic-write idiom:** write `result.csv` and `result.json` via `tempfile.NamedTemporaryFile(dir=output_dir, delete=False)` + `os.replace(tmp, final)` so a crash mid-write doesn't leave a half-CSV. (This is the file-I/O analog of `store.py`'s `with conn:` transaction.) The planner may consider this for v1 or defer to v2; the store-layer atomic-write precedent is the reference.

---

### `src/perfcrawl/cli.py` (controller, request-response)

**Analog:** no existing Typer code in the repo — primary template is RESEARCH § "Typer CLI shape" + RESEARCH § Architectural Responsibility Map. Phase 1 LEARNINGS notes Typer was deliberately removed from Phase 1; Phase 2 introduces it. The closest existing module-level analog is `store.py`'s top-of-file docstring shape.

**Module docstring shape** (mirror `store.py` lines 1-38):

```python
"""perfcrawl measure — single-URL end-to-end audit (Phase 2 D-05/D-06/D-15).

The CLI is the controller layer: parse argv → build a RunConfig → hand off to
orchestrator.run() → render the result (Rich table to stdout for humans,
or RunRecord JSON to stdout for `--json`). Progress and errors always go to
stderr so machine-readable stdout under `--json` is never contaminated (D-06).

Exit codes (D-15):
  0  page measured (including non-2xx — D-13 partial result with status_code set)
  1  user error — bad URL, bad flags, can't write output dir
  2  measurement error — all N samples failed, Chrome won't launch, etc.

(Phase 6's budget verdicts BUDG-01 will carve out 10+; the gap is intentional.)
"""
```

**Typer command structure** (from RESEARCH § "Typer CLI shape"):

```python
import sys
import typer
from rich.console import Console
from perfcrawl.constants import DEFAULT_SAMPLES_N, ExitCode

app = typer.Typer(no_args_is_help=True, add_completion=False)
err_console = Console(stderr=True)  # D-06: progress + errors → stderr


@app.command()
def measure(
    url: str = typer.Argument(..., help="URL to audit"),
    samples: int = typer.Option(DEFAULT_SAMPLES_N, "--samples", "-n", min=1),
    emulation: str = typer.Option("mobile", "--emulation", help="mobile|desktop"),
    output_json: bool = typer.Option(False, "--json", help="Machine output to stdout"),
    output_dir: str = typer.Option("output", "--output-dir"),
) -> None:
    """Measure one URL end-to-end (D-05)."""
    try:
        run_record = orchestrator.run(url=url, samples=samples, emulation=emulation,
                                       output_dir=output_dir)
    except UserError as e:
        err_console.print(f"[red]error:[/red] {e}", style="bold")
        raise typer.Exit(code=ExitCode.USER_ERROR)  # D-15
    except MeasurementError as e:
        err_console.print(f"[red]measurement failed:[/red] {e}", style="bold")
        raise typer.Exit(code=ExitCode.MEASUREMENT_ERROR)  # D-15

    if output_json:
        sys.stdout.write(run_record.model_dump_json(indent=2))
    else:
        _render_human_table(run_record, samples)
    # implicit exit 0 — D-13 success-or-tagged path
```

**Custom exception pattern** (`UserError`, `MeasurementError`) — defined in `cli.py` or `orchestrator.py`; the boundary between "exit code 1" and "exit code 2" lives in the exception class hierarchy so the CLI layer's catch arms are exhaustive and reviewable.

---

### `src/perfcrawl/cli.py` — entry point wiring in `pyproject.toml`

**Analog:** Phase 1 LEARNINGS § Decisions "Phase 1 is library-only — no Typer/CLI scaffolding" — the `[project.scripts] perfcrawl = "perfcrawl:main"` block was **deliberately removed** in Phase 1 (commit `5aa4222`). Phase 2 **restores it**, but pointing at the real CLI:

```toml
[project.scripts]
perfcrawl = "perfcrawl.cli:app"
```

Plus Phase 2 dep additions:

```toml
dependencies = [
    "pydantic>=2.10,<3",
    "w3lib>=2.3,<3",
    "playwright>=1.60,<2",
    "typer>=0.15",
    "rich>=13",
]
```

The dep block stays alphabetized to match the existing shape.

---

### `tests/test_normalizer.py` (test, unit + fixture-driven)

**Analog:** `tests/test_canonical.py` — explicit, near-perfect match for the boundary-function + parametrize style.

**Imports + module docstring** (`test_canonical.py` lines 1-12):

```python
"""Canonical URL key tests — success criterion #4, D-01..D-05.

These assertions pin the observable transform of ``canonical_key(url)``:
tracking params dropped, query sorted, fragment dropped, trailing slash
stripped (except root), scheme+host lowercased, path case PRESERVED, default
ports stripped, percent-hex uppercased — and crucially, distinct pages are NOT
over-merged and malformed input never raises.
"""

import pytest

from perfcrawl.canonical import canonical_key
```

**For test_normalizer.py:** open with the same shape — list each invariant in the docstring (METRIC-01..05, D-10 version gate, D-11 INP-proxy mapping, D-13 partial-result-on-non-2xx). One named test per invariant; parametrize over fixture variants.

**Fixture loading pattern** (from `conftest.py` lines 24-37):

```python
FIXTURES_DIR = Path(__file__).parent / "fixtures"

@pytest.fixture
def run_v1_json() -> str:
    """Raw JSON text of the full RunRecord fixture (>=2 pages, metrics + analysis)."""
    return (FIXTURES_DIR / "run_v1.json").read_text()
```

**For Phase 2 fixtures in conftest.py** (append to existing):

```python
LH_FIXTURES_DIR = FIXTURES_DIR / "lighthouse"

@pytest.fixture
def lh_home_200() -> dict:
    """A real LH 13.3.0 JSON capture of a 200-response homepage."""
    return json.loads((LH_FIXTURES_DIR / "studyhalo-home-200.json").read_text())

@pytest.fixture
def lh_404() -> dict:
    """A real LH 13.3.0 JSON capture of a 404 (D-13 partial-result)."""
    return json.loads((LH_FIXTURES_DIR / "studyhalo-404.json").read_text())

@pytest.fixture
def lh_version_14_drift() -> dict:
    """Synthetic LH 14.0.0 JSON for the D-10 version-gate test."""
    return json.loads((LH_FIXTURES_DIR / "version-drift-14.json").read_text())
```

**Parametrize style from `test_canonical.py` lines 101-107:**

```python
@pytest.mark.parametrize("bad", ["not a url", "", "://broken", "http://", "   "])
def test_malformed_input_does_not_raise(bad):
    """Malformed / non-URL input returns deterministically without raising (DoS mitigation)."""
    result = canonical_key(bad)
    assert isinstance(result, str)
    assert canonical_key(bad) == result  # deterministic
```

**Test naming pattern** — descriptive `test_<thing>_<expected_behavior>` (e.g. `test_waterfall_timing_uses_lh13_keys`, `test_version_gate_rejects_major_drift`, `test_partial_result_on_non_2xx_keeps_status_code`, `test_inp_proxy_tbt_ms_never_named_inp`, `test_diagnostics_only_failing_audits`). Each name maps 1:1 to a row in the RESEARCH § "Phase Requirements → Test Map" table.

---

### `tests/test_slug.py` (test, parametrize + property-style)

**Analog:** `tests/test_canonical.py` — exact match. Both functions are: "arbitrary external string in, deterministic safe identifier out, never raise."

**Negative-space parametrize** (the IN-02 vectors from LEARNINGS) — copy the shape of `test_malformed_input_does_not_raise`:

```python
@pytest.mark.parametrize("traversal_attempt", [
    "https://x.com/a/%2e%2e/b",    # the canonical LEARNINGS IN-02 example
    "https://x.com/a/../b",         # already-decoded
    "https://x.com/../../etc/passwd",
    "https://x.com/.../...//",
    "..",
    "../../../",
])
def test_no_path_traversal_in_slug(traversal_attempt):
    """page_slug() must never produce a stem that contains '..', '/', or '\\'."""
    from perfcrawl.canonical import canonical_key
    from perfcrawl.slug import page_slug
    slug = page_slug(canonical_key(traversal_attempt))
    assert ".." not in slug
    assert "/" not in slug
    assert "\\" not in slug
    # also no leading dot (filesystem-hidden) and no path-separator-equivalent
    assert not slug.startswith(".")
```

**Deterministic-output assertion** (from `test_canonical.py` lines 110-113):

```python
@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  \t"])
def test_empty_or_blank_input_returns_empty_sentinel(blank):
    """Empty/whitespace input returns the "" sentinel (WR-03)."""
    assert canonical_key(blank) == ""
```

**For test_slug.py:** mirror with `assert page_slug(blank) == "_"` (or whatever sentinel the implementer chooses; planner-discretion per CONTEXT). The point is: documented, deterministic, distinct from any real slug.

**Idempotency test** (from `test_canonical.py` lines 130-134):

```python
def test_idempotent():
    """Canonicalizing an already-canonical key is a no-op (stable self-join key)."""
    once = canonical_key("https://Example.com/Path/?utm_source=x&b=2&a=1#frag")
    twice = canonical_key(once)
    assert once == twice
```

**For test_slug.py:** `page_slug(page_slug(x)) == page_slug(x)` — load-bearing because the slug might be passed through twice in the same code path (e.g., once when building the artifact filename, again when logging it).

---

### `tests/test_aggregator.py` (test, finite-guard + edge cases)

**Analog:** `tests/test_models.py` lines 127-138 — the parametrize-over-non-finite-floats pattern.

**The Phase 1 pattern:**

```python
@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_metric_sample_rejects_non_finite_median(bad):
    """inf/nan medians are rejected, not silently nulled on round-trip (WR-01)."""
    with pytest.raises(ValidationError):
        MetricSample(median=bad)
```

**For test_aggregator.py:** parametrize over the *aggregator's* non-finite inputs — but the aggregator silently drops them (defense in depth) rather than raising. The shape is the same:

```python
@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_aggregator_drops_non_finite_samples(bad):
    """Non-finite samples are dropped silently (defense in depth; MetricSample
    would otherwise reject the whole MetricSample on the way out)."""
    from perfcrawl.aggregator import aggregate_samples
    result = aggregate_samples([1.0, bad, 2.0])
    assert result.samples == [1.0, 2.0]  # bad dropped
    assert result.median == 1.5
```

**Edge cases mapped to RESEARCH § "Phase Requirements → Test Map":**

| Test name | Invariant | Source |
|-----------|-----------|--------|
| `test_median_of_n` | RUN-04 happy path | RESEARCH Test Map |
| `test_median_of_one` | RESEARCH Pitfall 7 — `--samples 1` end-to-end | RESEARCH Test Map |
| `test_empty_samples_median_none` | D-16 + Pitfall 3 (`statistics.median([])`) | LEARNINGS Finite-guard |
| `test_aggregator_drops_non_finite_samples` | Defense in depth | Phase 1 LEARNINGS |
| `test_aggregator_drops_none` | RUN-04 partial sample | RESEARCH Test Map |

---

### `tests/test_worker.py` (test, subprocess contract — mocked)

**Analog:** `tests/test_store.py::test_write_run_is_atomic_on_failure` (lines 298-338) — the **subclass-to-inject-failure pattern**. The store test couldn't monkeypatch `sqlite3.Connection.executemany` (C-attribute), so it subclassed `sqlite3.Connection` to inject the failure. The worker test will use the same shape for `subprocess.run`.

**Phase 1 pattern (`test_store.py` lines 298-308):**

```python
class _FailOnPageInsertConnection(sqlite3.Connection):
    """A Connection whose page_results bulk insert always raises.

    sqlite3.Connection methods are read-only C attributes (cannot be
    monkeypatched), so we subclass to inject a mid-write failure: the ``runs``
    insert succeeds, then ``executemany`` (the page_results bulk insert) raises,
    exercising the rollback path of write_run's explicit transaction.
    """

    def executemany(self, *args, **kwargs):
        raise sqlite3.OperationalError("simulated mid-write failure")
```

**For test_worker.py:** use `unittest.mock.patch` on `subprocess.run` (a Python-level function, easier to mock than `sqlite3.Connection`), but follow the same "inject a specific failure mode per test" discipline. One test per failure shape:

```python
def test_worker_returns_none_on_timeout(monkeypatch):
    """D-14: subprocess.TimeoutExpired → run_one_sample returns None."""
    import subprocess
    from perfcrawl.lighthouse_worker import run_one_sample

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="node", timeout=60)
    monkeypatch.setattr(subprocess, "run", _raise_timeout)

    assert run_one_sample(port=9222, url="https://x.com", emulation="mobile", timeout_s=60) is None


def test_worker_returns_none_on_nonzero_exit(monkeypatch):
    """D-14: proc.returncode != 0 → run_one_sample returns None."""
    from types import SimpleNamespace
    monkeypatch.setattr("subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="LH crashed"))
    assert run_one_sample(...) is None


def test_worker_argv_passthrough(monkeypatch):
    """RUN-01: --form-factor passes through to the worker subprocess argv."""
    captured = {}
    monkeypatch.setattr("subprocess.run",
        lambda argv, **kw: (captured.update(argv=argv), SimpleNamespace(returncode=0, stdout='{"lhr":{},"reportJson":"","reportHtml":""}'))[1])
    run_one_sample(port=9222, url="https://x.com", emulation="desktop", timeout_s=60)
    assert "--form-factor=desktop" in captured["argv"]
```

---

### `tests/test_orchestrator.py` (test, integration — mocked Playwright + subprocess)

**Analog:** `tests/test_store.py::test_write_run_reasserts_foreign_keys` (lines 267-296) — the **realistic multi-connection / multi-resource setup** pattern, with `tmp_path` for on-disk state.

**Pattern (`test_store.py` lines 267-296):**

```python
def test_write_run_reasserts_foreign_keys(sample_run: RunRecord, tmp_path):
    """write_run re-asserts PRAGMA foreign_keys on its connection (WR-05)."""
    db = tmp_path / "perfcrawl.db"
    init_conn = sqlite3.connect(db)
    init_db(init_conn)
    init_conn.close()

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        write_run(conn, sample_run)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        …
    finally:
        conn.close()
```

**For test_orchestrator.py:** use `tmp_path` for the output directory, `monkeypatch` for Playwright `sync_playwright` to inject a stub Chrome lifecycle, and a `monkeypatch.setattr("subprocess.run", …)` to inject worker responses. Each test exercises one D-XX:

- `test_fresh_context_per_sample` — RUN-03 — assert `browser.new_context()` was called N times.
- `test_timeout_retry_then_drop` — D-14 — first call raises `TimeoutExpired`, second also raises, sample is dropped.
- `test_all_samples_fail_returns_exit_code_2` — D-14 + D-15 — orchestrator returns / raises in a way that maps to `ExitCode.MEASUREMENT_ERROR`.
- `test_partial_result_on_non_2xx` — D-13 — a fixture worker response with `statusCode=404` produces a `PageResult` with `status_code=404` and `perf_score=None`.

**Cleanup discipline from `test_store.py` line 295:** every test that allocates a resource closes it in `finally`. For orchestrator tests, that means the stub Chrome process is `.kill()`-ed in the test's teardown — and the implementation under test must do the same (asserting this is part of the "no zombie Chrome" mitigation from RESEARCH § Security Domain).

---

### `tests/test_output.py` (test, on-disk artifacts)

**Analog:** `tests/test_store.py::test_round_trip_identity` + `test_record_json_bytes_preserved` (lines 35-52) — the **write-then-read-back + byte-equality** discipline.

**Pattern:**

```python
def test_round_trip_identity(conn, sample_run: RunRecord):
    """read_run(write_run(r)) == r by model equality (criterion #1 / HIST-01)."""
    write_run(conn, sample_run)
    loaded = read_run(conn, str(sample_run.id))
    assert loaded.model_dump() == sample_run.model_dump()
    assert loaded == sample_run


def test_record_json_bytes_preserved(conn, sample_run: RunRecord):
    """The exact model_dump_json() bytes are stored in record_json TEXT (not JSONB)."""
    write_run(conn, sample_run)
    row = conn.execute(
        "SELECT record_json FROM runs WHERE id = ?", (str(sample_run.id),)
    ).fetchone()
    assert row is not None
    assert row[0] == sample_run.model_dump_json()
```

**For test_output.py:**

```python
def test_json_round_trip(tmp_path, sample_run: RunRecord):
    """OUT-04: result.json reads back as an identical RunRecord (model equality)."""
    output_dir = tmp_path / str(sample_run.id)
    write_outputs(sample_run, output_dir=tmp_path)
    loaded = RunRecord.model_validate_json((output_dir / "result.json").read_text())
    assert loaded.model_dump() == sample_run.model_dump()


def test_csv_column_order(tmp_path, sample_run: RunRecord):
    """OUT-04: result.csv columns appear in the locked CSV_COLUMNS order."""
    write_outputs(sample_run, output_dir=tmp_path)
    csv_text = (tmp_path / str(sample_run.id) / "result.csv").read_text()
    header = csv_text.splitlines()[0].split(",")
    from perfcrawl.output import CSV_COLUMNS
    assert header == CSV_COLUMNS


def test_raw_artifacts_on_disk(tmp_path, sample_run, lh_home_200):
    """OUT-03: raw LH JSON + HTML written to lighthouse/<slug>.{json,html}."""
    write_outputs(sample_run, output_dir=tmp_path, raw_lh=lh_home_200, raw_html="<html>…")
    slug_dir = tmp_path / str(sample_run.id) / "lighthouse"
    files = sorted(p.name for p in slug_dir.iterdir())
    # the slug is derived from the page's url_key — NEVER contains "..", "/"
    for f in files:
        assert ".." not in f
        assert "/" not in f
```

**The `sample_run` fixture is REUSED from Phase 1 conftest.py** (lines 51-92) — already has the full v2 shape (two pages, all CWV slots filled, MetricSample distributions). Don't redefine it.

---

### `tests/test_cli.py` (test, Typer CliRunner)

**Analog:** no existing Typer test in the repo; closest is `tests/test_canonical.py` for the parametrize-over-inputs style.

**Pattern from RESEARCH § "Typer CLI shape" + Typer docs:**

```python
from typer.testing import CliRunner
from perfcrawl.cli import app

runner = CliRunner(mix_stderr=False)  # D-06: separate stdout from stderr

def test_exit_zero_on_success(monkeypatch, tmp_path):
    """D-15: a successful measurement exits 0."""
    monkeypatch.setattr("perfcrawl.orchestrator.run", lambda **kw: _make_sample_run_record())
    result = runner.invoke(app, ["measure", "https://example.com", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0


def test_exit_one_on_bad_url():
    """D-15: malformed flags / usage error exits 1 (Typer's own UsageError path)."""
    result = runner.invoke(app, ["measure"])  # missing required URL arg
    assert result.exit_code in (1, 2)  # Typer maps usage errors to 2 by default; our wrapper coerces to 1


def test_exit_two_on_all_samples_failed(monkeypatch):
    """D-15: all N samples failing exits 2."""
    monkeypatch.setattr("perfcrawl.orchestrator.run",
                        lambda **kw: (_ for _ in ()).throw(MeasurementError("all samples failed")))
    result = runner.invoke(app, ["measure", "https://example.com"])
    assert result.exit_code == 2


def test_json_flag_emits_valid_json_to_stdout(monkeypatch, tmp_path):
    """D-06: --json puts a parseable RunRecord JSON on stdout; stderr unused for it."""
    monkeypatch.setattr("perfcrawl.orchestrator.run", lambda **kw: _make_sample_run_record())
    result = runner.invoke(app, ["measure", "https://example.com", "--json",
                                  "--output-dir", str(tmp_path)])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "id" in parsed and "pages" in parsed
```

---

### Test fixtures: `tests/fixtures/lighthouse/*.json`

**Analog:** `tests/fixtures/run_v1.json` and `tests/fixtures/run_v1_old_schema.json` (committed JSON fixtures loaded by `conftest.py`).

**Convention from Phase 1:**
- One canonical fixture (`run_v1.json`) representing the happy path.
- A variant (`run_v1_old_schema.json`) representing an edge case.
- Both loaded via `(FIXTURES_DIR / "…").read_text()` in fixtures defined at the top of `conftest.py`.

**For Phase 2:** three Lighthouse fixtures, each captured-or-synthesized once, then never regenerated without bumping `EXPECTED_LIGHTHOUSE_MAJOR_MINOR` (the version-gate guarantee). RESEARCH § "Wave 0 Gaps" lists exactly which:

| File | Purpose | How to generate |
|------|---------|------------------|
| `studyhalo-home-200.json` | Real LH 13.3.0 capture of a 200 response | `cd lighthouse-worker && node run.mjs --port=<live-port> --url=https://studyhalo.com/ --form-factor=mobile > tests/fixtures/lighthouse/studyhalo-home-200.json` (one-time, committed) |
| `studyhalo-404.json` | Real LH 13.3.0 capture of a 404 (D-13) | Same, against a known-404 URL like `https://studyhalo.com/__nope__` |
| `version-drift-14.json` | Synthetic LH 14.0.0 for the D-10 gate | Copy `studyhalo-home-200.json` and edit only `lhr.lighthouseVersion` to `"14.0.0"`; minimal, hand-crafted |

The first two are several KB each (real LH JSON); the third is small. All three are committed to the repo so the test suite runs offline (criterion: "unit tests of the normalizer run without Node" from RESEARCH § "Primary recommendation").

---

## Shared Patterns

### Defensive try/except + deterministic fallback for untrusted input

**Source:** `src/perfcrawl/canonical.py` lines 84-108 (and the LEARNINGS § Patterns entry of the same name).

**Apply to:** `slug.py::page_slug()`, `lighthouse_worker.py::run_one_sample()` (all three failure modes → `None`), `orchestrator.py` per-sample loop (timeout/non-zero/JSON-decode all → drop the sample, never raise).

```python
if not (url or "").strip():
    return ""  # deterministic empty sentinel
try:
    # the real work
    return _do_the_thing(url)
except Exception:
    return (url or "").strip()  # deterministic, never-raising fallback
```

**Invariant:** any function that parses arbitrary external strings (URLs, subprocess stdout, fixture JSON) is DoS-safe: never raises, always returns a deterministic value. The CALLER decides what "drop on failure" means in context.

---

### One editable place: constants/registry tables consumed by call sites

**Source:** `src/perfcrawl/registry.py` (`TRACKING_PARAM_DENYLIST`, `METRIC_POLARITY`).

**Apply to:** all of `constants.py` — every tunable Phase 2 introduces (`PER_SAMPLE_TIMEOUT_S`, `DEFAULT_SAMPLES_N`, `EXPECTED_LIGHTHOUSE_MAJOR_MINOR`, `INP_PROXY_DISPLAY_LABEL`, `ExitCode.*`, `DEVTOOLS_PORT_FILE_TIMEOUT_S`) plus `output.py::CSV_COLUMNS`.

```python
# --- D-XX: <name> (the ONE editable place) ---------------------------------
CONSTANT_NAME: T = value  # used by <consumer module>
```

**Invariant:** the call site IMPORTS the constant; never inlines the literal. Phase 1 grep-asserted this for `TRACKING_PARAM_DENYLIST`; Phase 2 should grep-assert the same for `PER_SAMPLE_TIMEOUT_S` and `EXPECTED_LIGHTHOUSE_MAJOR_MINOR` at minimum (those two are the highest-risk for accidental drift between the constant and an inlined value).

---

### Finite-guard pattern: `math.isfinite(result)` after arithmetic

**Source:** `src/perfcrawl/delta.py` lines 124-153 (`safe_pct`, `_safe_abs`).

**Apply to:** `aggregator.py::aggregate_samples()`, and any derived metric in `normalizer.py` (e.g., the per-request `timing_ms = networkEndTime - networkRequestTime` subtraction).

```python
diff = current - previous
return diff if math.isfinite(diff) else None
```

**Invariant:** `MetricSample.allow_inf_nan=False` and `PageResult.allow_inf_nan=False` are the model-layer backstop, but they catch inf/nan ON INPUT, not inf/nan PRODUCED by arithmetic. Guard at the arithmetic site too (defense in depth — Phase 1's WR-01 re-review surprise).

---

### Atomic `with conn:` (or its file-I/O analog) for multi-step writes

**Source:** `src/perfcrawl/store.py::write_run` (lines 134-154) and the LEARNINGS § Patterns "Atomic `with conn:` write_run" entry.

**Apply to:** `orchestrator.py` Chrome lifecycle (try/finally around the whole `measure` run — kill Chrome + cleanup tempdir even on crash); `output.py` for the per-file writes (`tempfile.NamedTemporaryFile` + `os.replace`).

```python
with conn:
    conn.execute(...)
    conn.executemany(...)
# Either both commit or both roll back — never half.
```

**Invariant:** any multi-step write whose partial completion would leave the system in a corrupt state (orphan rows, half-written CSV, zombie Chrome) is wrapped in a transaction-or-finally block.

---

### Labeled-proxy invariant enforced in defense-in-depth layers

**Source:** `src/perfcrawl/models.py` `_no_bare_inp` validator (lines 152-166) — the **model-layer floor**.

**Apply to:** `normalizer.py` (never construct a local named `inp`/`inp_ms`/`interaction_to_next_paint`; assign TBT directly to `inp_proxy_tbt_ms`); `output.py::CSV_COLUMNS` (the column name is the field name `inp_proxy_tbt_ms`, not `inp`); `cli.py` Rich table header (uses `INP_PROXY_DISPLAY_LABEL` from `constants.py`, which reads `"INP (lab proxy, TBT-based)"` — fully labeled).

**Invariant:** every layer (model / normalizer / persistence / display) refuses to surface a bare `inp` name. The model-layer validator is the floor; the planner should consider a grep-asserted negative check: `grep -rn "\\binp\\b" src/perfcrawl/ tests/ | grep -v "inp_proxy_tbt_ms\\|INP (lab proxy" | grep -v "_FORBIDDEN_INP_FIELDS\\|_no_bare_inp"` must be empty.

---

### TDD RED → GREEN commit pair per task

**Source:** `01-{01,02,03}-SUMMARY.md` TDD Gate Compliance sections, and the LEARNINGS § Patterns entry of the same name.

**Apply to:** every Phase 2 task. For each new module:
1. First commit: write `test_<module>.py` with all the parametrize / fixture-driven test cases, run `uv run pytest` and confirm failure (`ModuleNotFoundError` for the new module, or `AssertionError` for a partially-implemented one).
2. Second commit: add the module implementation, confirm `uv run pytest` is green.

**Invariant:** the test exists in commit history BEFORE the code that passes it. This is the most-cited Phase 1 pattern and the planner should structure each plan's task list as task pairs.

---

### Forward-compat models: `extra="ignore"` + `Optional[…] = None`

**Source:** every model in `src/perfcrawl/models.py` (lines 68, 80, 96, 118, 178).

**Apply to:** any new `BaseModel` Phase 2 introduces. RESEARCH explicitly notes Phase 2 does NOT add fields to the existing models (the v1 superset already covers Phase 2), but if Phase 2 needs a new internal helper model (e.g., `RunConfig` for the orchestrator inputs), it follows the same shape:

```python
class RunConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    url: str
    samples: int = DEFAULT_SAMPLES_N
    emulation: str = "mobile"
    output_dir: Path = Path("output")
```

---

### Security: `?` placeholders for SQL → `list[str]` argv for subprocess

**Source:** `src/perfcrawl/store.py` module docstring lines 33-38, every query in `store.py` (all `?`-parameterized).

**Apply to:** every `subprocess.run` call in `orchestrator.py` and `lighthouse_worker.py` — argv is a `list[str]`, never `shell=True`, never f-string-interpolate the URL into a shell string. This is the **same threat shape** (untrusted input → execution context) — SQL injection's subprocess cousin.

```python
# Phase 1 SQL:
conn.execute("SELECT ... WHERE id = ?", (run_id,))   # ✅ parameterized
# Phase 2 subprocess:
subprocess.run(["node", "run.mjs", f"--url={url}"])  # ✅ argv list, no shell
# NEVER:
# subprocess.run(f"node run.mjs --url={url}", shell=True)  # ❌
```

**Test for it:** parametrize over URLs containing `;`, `&`, `$()`, backticks, newlines — assert the orchestrator handles them without shell expansion.

---

## No Analog Found

Files with no close match in the codebase (the planner should rely on RESEARCH patterns for these):

| File | Role | Data Flow | Reason | RESEARCH section |
|------|------|-----------|--------|------------------|
| `lighthouse-worker/package.json` | Node config | n/a | First Node surface in the repo | RESEARCH § Installation |
| `lighthouse-worker/package-lock.json` | Node lockfile | n/a | First Node surface; convention is `npm ci` for byte-identical installs | RESEARCH § Standard Stack / Installation |
| `lighthouse-worker/run.mjs` | Node service (Lighthouse worker) | request-response | First Node code in the repo; the Python-side `lighthouse_worker.py` consumes it via subprocess | RESEARCH § Pattern 2 (the complete worker code is in the RESEARCH file — copy it directly) |

**Mitigation:** the Node worker is intentionally tiny (~40 lines per Pattern 2) and stateless — the boundary contract (argv-in, JSON-over-stdout, non-zero-on-failure) is fully specified in RESEARCH § Pattern 2, RESEARCH § Architectural Responsibility Map, and CONTEXT D-02. Treat it as "implement RESEARCH Pattern 2 verbatim, commit `package-lock.json`, document `npm ci` in README."

---

## Metadata

**Analog search scope:**
- `src/perfcrawl/` (all 5 Phase 1 modules: `models.py`, `store.py`, `canonical.py`, `delta.py`, `registry.py`)
- `tests/` (all 4 Phase 1 test files: `test_models.py`, `test_store.py`, `test_canonical.py`, `test_delta.py`, plus `conftest.py`)
- `pyproject.toml` (project config + dev deps)
- `.planning/phases/01-data-model-persistence-foundation/01-CONTEXT.md` and `01-LEARNINGS.md` (Phase 1 decisions + patterns)

**Files scanned:** 11 source/test files + 2 Phase 1 artifacts + Phase 2 CONTEXT + Phase 2 RESEARCH.

**Pattern extraction date:** 2026-05-28

**Coverage summary:**
- Files with **exact** analog: 5 (`slug.py`, `constants.py`, `aggregator.py`, `test_normalizer.py`, `test_slug.py`, `test_aggregator.py` — all map 1:1 to Phase 1 modules/tests)
- Files with **role-match** analog: 11 (`cli.py`, `orchestrator.py`, `lighthouse_worker.py`, `normalizer.py`, `output.py`, `test_worker.py`, `test_orchestrator.py`, `test_output.py`, `test_cli.py`, the 3 fixtures)
- Files with **no analog**: 3 (the `lighthouse-worker/*` Node files — RESEARCH Pattern 2 is the template).

---

## PATTERN MAPPING COMPLETE
