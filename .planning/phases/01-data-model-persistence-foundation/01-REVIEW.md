---
phase: 01-data-model-persistence-foundation
reviewed: 2026-05-25T00:00:00Z
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
  critical: 0
  warning: 2
  info: 4
  total: 6
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-05-25T00:00:00Z
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

Re-review after the CR-01 + WR-01..WR-07 fix pass. I verified each prior fix
against current line numbers and re-probed the implementation adversarially
(SQL-injection safety, malformed-URL DoS, division-by-zero, JSON round-trip
integrity, Pydantic model correctness). All 63 tests pass and `ruff check` is
clean. All findings below were verified by executing the current code, not by
reading alone.

**Prior fixes — all sound, no regressions introduced:**

- **CR-01** (`store.py:114`): `with conn:` wraps both inserts; rollback verified
  empirically via `_FailOnPageInsertConnection` — no partial run survives a
  forced `commit()` after a mid-write failure.
- **WR-01** (`models.py:68,96,118`): `allow_inf_nan=False` on `MetricSample`,
  `WaterfallEntry`, `PageResult`; inf/nan rejected at validation (confirmed for
  `median`, `samples[]`, and scalar floats).
- **WR-02** (`delta.py:116-129`): `safe_pct` returns `None` on a non-finite
  result via `isfinite(pct)`; confirmed for inf/nan on either side and for the
  `previous in (None, 0)` short-circuit.
- **WR-03** (`canonical.py:66-67`): empty/blank short-circuits to `""` before
  w3lib; verified it does NOT collide with the real root key `"…/"`.
- **WR-04** (`models.py:194-208`): naive `datetime` AND naive ISO string both
  rejected; offset-bearing strings (`Z`, `+05:30`) accepted; date-only rejected.
- **WR-05** (`store.py:109`): `PRAGMA foreign_keys = ON` re-asserted in
  `write_run`; a fresh write connection enforces the FK constraint (verified an
  orphan page insert raises `IntegrityError`).
- **WR-06/WR-07** (`store.py:95-102`): key derived on a deep copy; whitespace
  `url_key` regenerated; caller object not mutated. Verified.

**New concerns found this pass:** two WARNING-level correctness gaps the prior
pass did not surface. Both are silent-data-loss paths that defeat the very
contracts the WR-01/WR-02 fixes were meant to guarantee, and neither is covered
by a test. No BLOCKERs: every SQL statement uses `?` placeholders with no dynamic
table names (no injection); there is no command/path execution and no secrets;
canonicalization is DoS-safe (200K-char URLs, 5K query params, and 10K `../`
segments all complete in single-digit milliseconds and never raise).

## Structural Findings (fallow)

No structural pre-pass (`<structural_findings>`) was provided with this review.

## Narrative Findings (AI reviewer)

### Warnings

#### WR-01: `delta_abs` is not finite-guarded — two finite inputs can produce `inf`, silently nulled on JSON write

**File:** `src/perfcrawl/delta.py:132-136` (`_safe_abs`)
**Issue:** `safe_pct` honors the documented "never inf/NaN" contract (D-10) via
`isfinite(pct)` (line 129), but its sibling `_safe_abs` does NOT. Subtracting two
large *finite* floats can overflow to `inf`. Each input passes the WR-01
`allow_inf_nan=False` validation individually (each is finite), so the model
layer does not catch this — the overflow happens only at the subtraction inside
the delta engine. The resulting `RunDelta.delta_abs = inf` is then serialized to
`null` by Pydantic JSON mode, with no error.

Reproduced end-to-end through the model layer (both inputs are valid finite
`PageResult` values):
```python
prev = PageResult(url="https://x/p", url_key="https://x/p", slowest_request_ms=-1.5e308)
cur  = PageResult(url="https://x/p", url_key="https://x/p", slowest_request_ms= 1.5e308)
d = [x for x in compute_deltas(cur_run, prev_run) if x.metric == "slowest_request_ms"][0]
# d.delta_abs == inf
d.model_dump_json()
# -> {... "delta_abs": null, "delta_pct": null ...}   <-- real delta silently nulled
```
This is exactly the silent-corruption failure mode WR-01/WR-02 were created to
eliminate: the `delta_pct` path is guarded, but the `delta_abs` path is the
unguarded twin. `RunDelta` also lacks `allow_inf_nan=False` (every model in
`models.py` sets it), so there is no model-layer backstop either.

**Fix:** mirror the `safe_pct` guard in `_safe_abs`, and add the model-config
backstop to `RunDelta` (`isfinite` is already imported at `delta.py:33`):
```python
def _safe_abs(current: float | None, previous: float | None) -> float | None:
    """Absolute delta when both sides are present and finite, else None."""
    if current is None or previous is None:
        return None
    diff = current - previous
    return diff if isfinite(diff) else None
```
```python
class RunDelta(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    ...
```

#### WR-02: duplicate `url_key` within one run silently drops all-but-one page in `compute_deltas`

**File:** `src/perfcrawl/delta.py:150-151`
**Issue:** `cur_by_key = {p.url_key: p for p in current_run.pages}` (and the
`prev_by_key` twin) collapse a run's pages into a dict keyed by `url_key`. If a
single run contains two pages with the same canonical key, the dict keeps only
the **last** one — every earlier same-key page is silently discarded from the
delta output.

This is reachable: `write_run` imposes **no uniqueness constraint** on `url_key`
(`page_results` has only a non-unique index, `store.py:59`), and `canonical_key`
deliberately collapses spellings, so two distinct measured URLs
(`https://x.com/?a=1` and `https://x.com/?a=1#frag`) legitimately produce the
same key and both persist. Verified end-to-end: the store wrote both rows; then
`compute_deltas` reported only the second page's metrics and dropped the first's
entirely.

```python
prev pages: url_key="…/k" perf=0.5  AND  url_key="…/k" perf=0.9
# compute_deltas reports previous=0.9 — the perf=0.5 page is gone, no warning.
```
Depending on dict-insertion order this can mask a regression, fabricate an
"improvement", or misclassify a removed page — silently corrupting the cross-run
comparison that is criterion #2's whole purpose. No test exercises two same-key
pages in one run.

**Fix:** decide and enforce the uniqueness contract in one place. Cleanest is to
reject duplicate keys at write time so the invariant holds for every reader:
```python
# in write_run, after key derivation:
seen = set()
for page in run.pages:
    if page.url_key in seen:
        raise ValueError(f"duplicate url_key in run: {page.url_key!r}")
    seen.add(page.url_key)
```
If duplicates are intentionally allowed, `compute_deltas` must group by key
(`list[PageResult]` per key) with deterministic merge/emit semantics instead of
letting the dict comprehension drop rows. Either way, add a regression test with
two same-`url_key` pages in one run.

### Info

#### IN-01: `RunDelta` is the only model without an explicit `model_config`

**File:** `src/perfcrawl/delta.py:41-57`
**Issue:** Every model in `models.py` declares
`model_config = ConfigDict(extra="ignore", ...)` for documented forward-compat,
but `RunDelta` declares none. Pydantic v2 already defaults `extra` to "ignore"
(verified), so behavior is correct today — this is a consistency/intent gap. Note
the `allow_inf_nan=False` half of that config IS load-bearing here (see WR-01),
so this resolves alongside the WR-01 fix.
**Fix:** add `model_config = ConfigDict(extra="ignore", allow_inf_nan=False)` to
`RunDelta` so the codebase states its config intent uniformly.

#### IN-02: `%2e%2e` canonicalizes to literal `..` in the key

**File:** `src/perfcrawl/canonical.py:71-83`
**Issue:** Still applies. `canonical_key("https://x.com/a/%2e%2e/b")` returns
`"https://x.com/a/../b"` — the percent-encoded dots are decoded to literal `..`
(w3lib behavior; the `..` segments are not resolved). **Benign in Phase 1**:
`url_key` is only ever an opaque self-join string and a SQLite bind parameter,
never a filesystem path. Flagged so a future phase that derives a path/filename
from `url_key` (e.g. writing the per-page Lighthouse artifact to disk keyed by
URL) does not inherit a path-traversal vector.
**Fix:** none required now. When `url_key` is later used to build a path,
sanitize at that boundary; do not treat the canonical key as a safe path
component.

#### IN-03: `page_results` is written but never read by name in this phase

**File:** `src/perfcrawl/store.py:52-60,126-129`; `read_run` (`store.py:132-142`)
**Issue:** Still applies. `read_run` reconstructs a `RunRecord` solely from
`runs.record_json`; the `page_results` table (and its generated
`url_key`/`perf_score` columns) is populated on write but never queried by the
store API. It is the intended future self-join/promotion surface (D-07), so this
is correct forward design — but in Phase 1 it is write-only, exercised only by
tests, so a bug in the projected page blob would not surface through `read_run`.
**Fix:** none required (intentional seam). A one-line note in the store docstring
that `page_results` is the query surface for later phases would prevent a future
reader mistaking it for dead code. (`test_generated_column_cannot_drift` already
locks the two representations together — good.)

#### IN-04: `_strip_default_port` correct but undocumented for the bracketed-IPv6 edge

**File:** `src/perfcrawl/canonical.py:44-49`
**Issue:** `_strip_default_port` uses `netloc.endswith(f":{default}")`. I verified
IPv6 literals are handled correctly: `[fe80::443]` (no port) is kept,
`[fe80::443]:443` strips only the trailing port, and non-default ports are
preserved. No bug — but the `endswith` anchoring is the subtle thing that makes
it correct (a substring check like `":443" in netloc` would wrongly strip a port
that appears inside a bracketed address). The prior review's IN-04 (`%2e%2e`) has
been re-slotted to IN-02; this IN-04 is a new, lower-stakes note.
**Fix:** add a brief comment noting the bracketed-IPv6 case is why the check is
anchored with `endswith`, so a future "simplification" to a substring check does
not silently regress.

---

_Reviewed: 2026-05-25T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
