---
phase: 01-data-model-persistence-foundation
reviewed: 2026-05-25T12:54:06Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - pyproject.toml
  - src/perfcrawl/__init__.py
  - src/perfcrawl/registry.py
  - src/perfcrawl/canonical.py
  - src/perfcrawl/models.py
  - src/perfcrawl/store.py
  - src/perfcrawl/delta.py
  - tests/conftest.py
  - tests/test_canonical.py
  - tests/test_models.py
  - tests/test_store.py
  - tests/test_delta.py
findings:
  critical: 1
  warning: 7
  info: 4
  total: 12
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-05-25T12:54:06Z
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Phase 1 establishes the PerfCrawl data contract: URL canonicalization (`canonical.py`),
Pydantic record models (`models.py`), a hybrid SQLite store with generated columns
(`store.py`), and a cross-run delta engine (`delta.py`). The code is well-documented,
the test suite (39 tests) passes cleanly, and the headline security concern — **SQL
injection in the store — is genuinely well-handled**: every statement uses `?`
placeholders, there are no dynamic table names, no f-string/`%`/`.format` SQL, and the
only f-string touches a `KeyError` message. The `delta_pct` zero-baseline guard works
correctly (including for `-0.0`), and the registry/model/polarity tables are mutually
consistent.

The defects below are concentrated in **robustness and contract-fidelity**, not
injection. The one Critical issue is a write-atomicity gap: `write_run` performs
multi-statement inserts under a deferred transaction with no explicit transaction
boundary and no rollback on failure, so a mid-write failure can leave a partial run
that a subsequent `commit()` would persist. The remaining warnings cover documented
invariants that aren't actually enforced (tz-aware timestamps, the malformed-URL
fallback contract, per-connection FK enforcement), a JSON round-trip hole for
`inf`/`nan` floats, a caller-object mutation side effect, and a `url_key` blank-check
that under-triggers. None of these is exploitable as a security hole, but several
undermine the "one contract that never needs retrofitting" and "round-trip identity"
guarantees that are this phase's entire reason to exist.

All findings were verified by executing the code against the project venv
(w3lib 2.4.1, pydantic 2.13.4, SQLite 3.50.4) — they are observed behavior, not
speculation.

## Critical Issues

### CR-01: `write_run` is not atomic — a mid-write failure can persist a partial run

**File:** `src/perfcrawl/store.py:75-103`
**Issue:** `write_run` inserts the `runs` row, then loops inserting each `page_results`
row, then calls `conn.commit()` once at the end. The connection uses Python's default
deferred isolation (`isolation_level == ''`), so all inserts accumulate in a single
implicit transaction. There is **no explicit transaction wrapper and no rollback in an
exception handler**. Verified behavior: if any page insert raises after the `runs` row
and one page row are inserted, `write_run` propagates the exception while leaving an
**open, uncommitted transaction** containing a partial run (1 run row + 1 page). On a
long-lived/shared connection (which is the intended pattern — `init_db` and `write_run`
take a caller-supplied `conn`), any later `conn.commit()` — from a retry, a different
write, or cleanup — will flush that partial run to disk. A "run" with a truncated page
set then silently corrupts every downstream regression delta, because `compute_deltas`
trusts `RunRecord.pages` to be the complete set for that run. This directly violates the
criterion #1 persistence guarantee (a run is written whole or not at all).
**Fix:** Wrap the whole write in an explicit transaction and roll back on failure so a
partial run can never be committed:
```python
def write_run(conn: sqlite3.Connection, run: RunRecord) -> None:
    for page in run.pages:
        if not page.url_key:
            page.url_key = canonical_key(page.url)
    try:
        with conn:  # commits on success, ROLLS BACK on any exception
            conn.execute(
                "INSERT INTO runs (id, started_at, target, schema_version, record_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(run.id), run.started_at.isoformat(), run.target,
                 run.schema_version, run.model_dump_json()),
            )
            conn.executemany(
                "INSERT INTO page_results (run_id, record_json) VALUES (?, ?)",
                [(str(run.id), p.model_dump_json()) for p in run.pages],
            )
    except Exception:
        # `with conn` already rolled back; re-raise so the caller sees the failure.
        raise
```
(`with conn:` guarantees rollback on exception; remove the manual `conn.commit()`.)

## Warnings

### WR-01: `inf`/`nan` floats silently become `null` — round-trip identity broken

**File:** `src/perfcrawl/models.py:66` (`MetricSample.median`), and every `float | None`
metric field; surfaces through `src/perfcrawl/store.py:95,101`
**Issue:** Pydantic accepts `float('inf')`/`float('nan')` for any `float` field in memory,
but `model_dump_json()` serializes them to `null` (Pydantic 2 JSON mode). Verified:
`MetricSample(median=float('inf')).model_dump_json()` → `{"median":null,...}`, and the
JSON re-parse yields `median=None`. The store's central guarantee (criterion #1, Pitfall 1)
is byte-identical round-trip — but a `PageResult` carrying an `inf`/`nan` metric does NOT
round-trip; it is silently mutated to `None` on write and reads back wrong. An upstream
measurement bug (e.g., a divide producing `inf` in Phase 2) would be silently swallowed
by the persistence layer instead of surfacing.
**Fix:** Reject non-finite floats at the model layer so corruption fails loud instead of
silently nulling. Add a field validator (or `allow_inf_nan=False` model config in pydantic):
```python
from math import isfinite
from pydantic import field_validator

class MetricSample(BaseModel):
    model_config = ConfigDict(extra="ignore")
    median: float | None = None
    samples: list[float] = Field(default_factory=list)

    @field_validator("median", "samples")
    @classmethod
    def _finite(cls, v):
        vals = v if isinstance(v, list) else [v]
        if any(x is not None and not isfinite(x) for x in vals):
            raise ValueError("non-finite float (inf/nan) is not JSON-round-trippable")
        return v
```

### WR-02: `safe_pct` docstring claims an inf/NaN guard it does not implement

**File:** `src/perfcrawl/delta.py:114-123`
**Issue:** The docstring states it returns the percentage "(no inf/NaN/ZeroDivisionError)".
The implementation only guards `previous in (None, 0)` and `current is None`. Verified:
`safe_pct(float('inf'), 5.0)` → `inf`; `safe_pct(5.0, float('inf'))` → `nan`;
`safe_pct(float('nan'), 5.0)` → `nan`. So an `inf`/`nan` input flows straight through to an
`inf`/`nan` `delta_pct`, contradicting the stated contract and (with WR-01 fixed at the
model layer) becoming the second line of defense. The `previous == 0` guard itself is
correct, including for `-0.0`.
**Fix:** Either tighten the function or correct the docstring. To match the documented
contract:
```python
from math import isfinite
def safe_pct(current, previous):
    if previous in (None, 0) or current is None:
        return None
    pct = (current - previous) / previous * 100.0
    return pct if isfinite(pct) else None
```

### WR-03: malformed-URL fallback contract is not what the docstring claims (identity collisions)

**File:** `src/perfcrawl/canonical.py:64-66`
**Issue:** The docstring (and module header) promise that malformed/non-URL input
"returns a deterministic value" via the `except` fallback `(url or "").strip()`. In
practice `url_query_cleaner`/`canonicalize_url` tolerate almost anything, so the `except`
branch is rarely reached and the documented fallback is mostly dead code. Verified:
`canonical_key("")` → `"/"`, `canonical_key("   ")` → `"/"`, `canonical_key("not a url")`
→ `"not%20a%20url"`, `canonical_key("http://")` → `"http:///"`. The empty/whitespace →
`"/"` collapse is the concerning one: **every empty-ish/garbage URL that normalizes to an
empty path collides onto the single key `"/"`**, merging genuinely distinct broken pages
into one cross-run identity (the exact over-merge the module says it avoids). The "never
raises / deterministic" property does hold; the *specific* contract does not.
**Fix:** Make the empty/degenerate case explicit and non-colliding, and align the docstring
with reality. For example, short-circuit empty/whitespace input before w3lib, and return a
sentinel that won't collide with a real root key:
```python
def canonical_key(url: str) -> str:
    stripped = (url or "").strip()
    if not stripped:
        return ""  # documented deterministic value for empty/blank input
    try:
        ...
    except Exception:
        return stripped
```
Then update the docstring to describe what the non-empty malformed inputs actually return.

### WR-04: `started_at` accepts naive datetimes despite the "tz-aware" contract

**File:** `src/perfcrawl/models.py:171` (`RunRecord.started_at: datetime`)
**Issue:** The field comment says "D-17: tz-aware ISO-8601 timestamp", but nothing enforces
tz-awareness. Verified: `RunRecord(started_at=datetime(2026,5,25,12,0,0), target="x")` is
accepted with `tzinfo=None`, round-trips, and stores ISO `"2026-05-25T12:00:00"` (no
offset). The whole point of this phase is to be the foundation for cross-run regression
("get the previous run for URL X"); naive timestamps make run ordering ambiguous across
DST/timezone boundaries and can silently mis-select the "previous" run. A latent
correctness bug for the feature this phase exists to enable.
**Fix:** Enforce tz-awareness at the model layer:
```python
from pydantic import field_validator

@field_validator("started_at")
@classmethod
def _tz_aware(cls, v: datetime) -> datetime:
    if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
        raise ValueError("started_at must be timezone-aware (D-17)")
    return v
```

### WR-05: foreign-key enforcement is per-connection and not re-asserted by `write_run`

**File:** `src/perfcrawl/store.py:64-72` (set in `init_db`) and `75-103` (`write_run` does
not set it)
**Issue:** `init_db` runs `PRAGMA foreign_keys = ON`, but that pragma is **per-connection**,
not stored in the database. Verified: a fresh `sqlite3.connect()` reports
`foreign_keys = 0`. The API deliberately separates `init_db` (one-time schema creation)
from `write_run`/`read_run` (per-use), and `write_run` never re-asserts the pragma. A
caller who initializes a DB once and then opens a new connection for writes — entirely
normal for an on-demand CLI re-run against an existing `.db` — gets **no FK enforcement**,
allowing orphan `page_results` rows (which then corrupt `read_run`/delta joins) with no
error. The `REFERENCES runs(id)` constraint in the DDL is silently inert on such
connections.
**Fix:** Assert the pragma wherever a write happens (cheap, idempotent), not only in
`init_db`:
```python
def write_run(conn: sqlite3.Connection, run: RunRecord) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    ...
```

### WR-06: `write_run` mutates the caller's `RunRecord` in place

**File:** `src/perfcrawl/store.py:83-85`
**Issue:** `write_run` sets `page.url_key = canonical_key(page.url)` directly on the
caller-owned `RunRecord` when `url_key` is blank. Verified: a `PageResult.url_key` left
`""` by the caller is mutated to the derived key after `write_run` returns. A function
named `write_run` reads as persistence-only; silently mutating the input object surprises
callers who reuse the object afterward (re-serialize it, compare it against a pre-write
snapshot, or write it to a second store expecting the original blanks). This is a
hidden-side-effect quality defect.
**Fix:** Derive the key without mutating the caller's object — compute the value used for
serialization on a copy, e.g.:
```python
def write_run(conn, run):
    run = run.model_copy(deep=True)  # don't mutate the caller's object
    for page in run.pages:
        if not page.url_key:
            page.url_key = canonical_key(page.url)
    ...
```
(Or document the mutation explicitly in the signature/docstring if it is intended.)

### WR-07: `url_key` blank-check is truthiness-based — whitespace-only keys slip through

**File:** `src/perfcrawl/store.py:84`
**Issue:** `if not page.url_key:` only regenerates the key when it is falsy (`""`, `None`).
A whitespace-only `url_key` such as `"   "` is truthy and is therefore stored verbatim
into the generated `page_results.url_key` column. Verified: a page written with
`url_key="   "` round-trips with `url_key == "   "` and that whitespace lands in the
self-join column, so it will never match the canonical key of the same logical page in
another run — silently breaking the cross-run delta for that page. Combined with WR-03
(empty/garbage URLs canonicalizing to `"/"`), the key-derivation path has two ways to
produce a non-canonical self-join key.
**Fix:** Normalize before the blank-check, and regenerate when the trimmed key is empty:
```python
if not (page.url_key or "").strip():
    page.url_key = canonical_key(page.url)
```

## Info

### IN-01: `RunDelta` omits the `extra="ignore"` config every model.py model sets

**File:** `src/perfcrawl/delta.py:39-55`
**Issue:** Every model in `models.py` sets `model_config = ConfigDict(extra="ignore")` for
forward/backward schema tolerance, but `RunDelta` does not. It is an internal in-memory
result type (not persisted in Phase 1), so the impact is nil today, but it is an
inconsistency with the stated project convention and will matter if deltas are ever
serialized/round-tripped.
**Fix:** Add `model_config = ConfigDict(extra="ignore")` to `RunDelta` for consistency, or
note explicitly that delta records are intentionally strict.

### IN-02: `page_results` table is write-only in Phase 1 (never read back)

**File:** `src/perfcrawl/store.py:98-102`, `106-116`
**Issue:** `read_run` reconstructs the `RunRecord` entirely from `runs.record_json`, and
`compute_deltas` operates on in-memory `RunRecord.pages` — nothing in Phase 1 reads the
`page_results` table or its generated columns. The denormalized projection is correct and
forward-looking (it is the future self-join/query surface), but reviewers should know it is
currently exercised only by tests, so a bug in the projected blob would not surface through
the normal `read_run` path. Not a defect; flagged for situational awareness.
**Fix:** None required. Optionally add a test that asserts the `page_results` blob equals
the corresponding page inside `runs.record_json` to lock the two representations together.

### IN-03: comment in `test_promoted_virtual_column_is_queryable` overstates NULL ordering

**File:** `tests/test_store.py:107`
**Issue:** The comment says "the third (none) sorts first as NULL", but `sample_run` has
exactly two pages and only one lacks an `lcp_ms` median; there is no third page. The test
assertions themselves are correct (they only check membership of 2410/3120). The comment is
just inaccurate.
**Fix:** Correct the comment to "the page without lcp_ms yields NULL and sorts first".

### IN-04: `canonical_key` decodes `%2e%2e` to literal `..` in the stored key

**File:** `src/perfcrawl/canonical.py:51-63`
**Issue:** Verified: `canonical_key("https://x.com/a/%2e%2e/b")` →
`"https://x.com/a/../b"` (w3lib percent-decodes the dots; it does NOT resolve the `..`
segments). This is benign for PerfCrawl specifically — the key is only ever bound as a `?`
SQL parameter and compared as an opaque string, so there is no path-traversal or injection
risk here. Flagged only so a future consumer that ever treats `url_key` as a filesystem
path or re-fetches it does not inherit a traversal surprise.
**Fix:** None required for Phase 1. If `url_key` is ever used as a path or re-requested,
reject or normalize `..` segments at that consumer.

---

_Reviewed: 2026-05-25T12:54:06Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
