---
phase: 1
phase_name: "data-model-persistence-foundation"
project: "PerfCrawl"
generated: "2026-05-26"
counts:
  decisions: 14
  lessons: 9
  patterns: 11
  surprises: 9
missing_artifacts: []
---

# Phase 1 Learnings: data-model-persistence-foundation

## Decisions

### w3lib approved as runtime canonicalization engine over stdlib fallback
The Plan 01 Task 1 human-verify supply-chain gate (threat T-01-SC) was
approved by the human before install. w3lib 2.4.1 (official Scrapy org,
on PyPI since 2010) was added as `w3lib>=2.3,<3`; the documented stdlib
`urllib.parse` fallback was declined.

**Rationale:** w3lib is the canonicalization engine inside Scrapy and is
hash-locked in `uv.lock`; rewriting the same rules by hand on stdlib was
~50 lines of duplicated logic for no security benefit once the
legitimacy gate had passed.
**Source:** 01-01-PLAN.md, 01-01-SUMMARY.md, 01-SECURITY.md (T-01-SC)

---

### Phase 1 is library-only — no Typer/CLI scaffolding
Removed the `uv init`-generated `[project.scripts] perfcrawl = "perfcrawl:main"`
entry point and the `main()` stub. `__init__.py` is a library docstring
plus `__version__`.

**Rationale:** Plan boundary — every later component targets the model;
CLI lands in Phase 2 with measurement. Keeping a stub now would invite
premature coupling.
**Source:** 01-01-SUMMARY.md (commit 5aa4222)

---

### `requires-python = ">=3.12"` pinned manually
`uv init` auto-set `requires-python = ">=3.14"` from the local
interpreter; corrected to `>=3.12` (CLAUDE.md target) and ruff
`target-version = "py312"`.

**Rationale:** Greenfield should not auto-pin the developer's exact
local interpreter as the project floor — that silently rejects every
other contributor's environment.
**Source:** 01-01-SUMMARY.md (commit 5aa4222)

---

### Hybrid store: full-fidelity JSON-TEXT blob + GENERATED columns
`record_json` is raw TEXT (the exact `model_dump_json()` bytes — never
JSONB), and `url_key` + `perf_score` are
`GENERATED ALWAYS AS (json_extract(record_json, '$...')) STORED`
columns indexed for the cross-run self-join.

**Rationale:** TEXT preserves byte-identity for the round-trip
contract (criterion #1); GENERATED columns are computed from the blob,
so a projected column can never drift from the source-of-truth JSON.
**Source:** 01-02-PLAN.md, 01-02-SUMMARY.md, 01-RESEARCH.md (Pattern 3)

---

### Round-trip identity (A3) implemented as BOTH model equality AND byte preservation
Two asserts: `read.model_dump() == original.model_dump()`
(`test_round_trip_identity`) AND `record_json` TEXT bytes ==
`model_dump_json()` (`test_record_json_bytes_preserved`).

**Rationale:** Criterion #1 in ROADMAP.md was ambiguous between
"semantic identity" and "byte identity"; proving both removes the
ambiguity for downstream phases and protects against a future JSONB
substitution that would silently break byte-identity.
**Source:** 01-02-SUMMARY.md (decisions frontmatter)

---

### Polarity registry is the only source of direction; never hardcoded
`delta.py` imports `METRIC_POLARITY` from `registry.py`; `classify()`
reads polarity per-metric; no `if metric == "lcp_ms": …` style logic
exists at any call site (grep-asserted in plan acceptance criteria).

**Rationale:** D-09 one-editable-place — adding a new metric in
Phase 2/6 only requires editing the registry, never the delta engine.
A regression mislabelled as an improvement (T-01-N) is otherwise the
easy mistake.
**Source:** 01-03-PLAN.md, 01-03-SUMMARY.md, 01-SECURITY.md (T-01-N)

---

### `DirectionStatus` defined once in `models.py`, imported by `delta.py`
`delta.py` imports `DirectionStatus` from `perfcrawl.models`; the
class is NOT redefined in `delta.py` (grep-asserted).

**Rationale:** One enum, one source of truth — prevents two diverging
copies of the six edge states (new / removed / not_comparable /
unchanged / improvement / regression).
**Source:** 01-03-PLAN.md acceptance criteria, 01-03-SUMMARY.md

---

### `WaterfallEntry` modeled as a typed submodel, not `list[dict]`
The METRIC-03 per-request waterfall is a typed `WaterfallEntry(url,
resource_type, size_bytes, timing_ms, status_code)` with
`extra="ignore"` rather than `list[dict]` (the plan explicitly allowed
either).

**Rationale:** Typed gives Phase 2 a clear shape to fill;
`extra="ignore"` keeps it forward-compatible without sacrificing
schema-discipline.
**Source:** 01-02-SUMMARY.md (decisions frontmatter)

---

### `compute_deltas` returns a flat `list[RunDelta]` over the union of pages × metrics
Single flat list keyed by `(url_key, metric)`; previous-run page order
first (so removed pages keep position), then current-only pages.

**Rationale:** Open Q2 resolved during planning — a nested
`dict[page][metric]` shape would force every Phase 6 grouping pass to
re-flatten. Flat is the seam.
**Source:** 01-03-PLAN.md (Open Q2), 01-03-SUMMARY.md

---

### `classify()` takes explicit page-presence keyword flags
`classify(metric, current, previous, *, page_in_current,
page_in_previous)` — both flags distinguish whole-page new/removed
from a one-sided metric (not_comparable), since both otherwise surface
as a `None` scalar.

**Rationale:** Without the flags, "page only in previous" and "metric
only in current" both look identical to the classifier; the flag pair
is the cheapest disambiguation.
**Source:** 01-03-SUMMARY.md (decisions frontmatter)

---

### `delta_pct` = None when `previous` is None or 0 (D-10); no inf/NaN
`safe_pct` returns `None` when `previous in (None, 0)` or `current is
None`; an additional `isfinite(pct)` check (added by WR-02) catches
inf from very large floats. `delta_abs` is still computed.

**Rationale:** A regression report with `inf%` or `NaN%` is unusable;
absence (None) is honest and Phase 6 can decide how to render it.
**Source:** 01-03-PLAN.md, 01-REVIEW.md (WR-02), 01-SECURITY.md (T-01-D)

---

### `unchanged` is literal equality — no noise band in Phase 1
`current == previous` → `UNCHANGED`. A delta of `100.0 → 100.1` on a
lower-is-better metric classifies as `REGRESSION`, NOT `UNCHANGED`
(asserted by `test_unchanged_is_literal`).

**Rationale:** D-12 — Phase 6 will add the variance/noise-band gate
on top of this raw direction. Pre-empting it in Phase 1 would couple
the two; the explicit test asserts the gate has NOT been pre-empted.
**Source:** 01-03-PLAN.md, 01-03-SUMMARY.md (D-12)

---

### v2 backend metrics (BACK-01..03) deliberately NOT modeled
The PageResult schema carries no fields for owned-site SQL/cache/timing
internals. Additive schema evolution will add them in v2.

**Rationale:** D-16 — modeling now would be premature; D-13 nullable
superset + `schema_version` make later addition free without breaking
old runs.
**Source:** 01-02-PLAN.md, 01-02-SUMMARY.md, STATE.md (Deferred Items)

---

### `branching_strategy` flipped `"none" → "phase"` after Phase 1 ship
Phases 2-6 will be built on `gsd/phase-{N}-{slug}` branches and
shipped via real `gh pr create`. Phase 1 itself closed administratively
because work was already on `main` by the time `/gsd-ship 1` ran.

**Rationale:** With `branching_strategy: "none"` the phase 1 work
landed directly on `main` and was pushed; `main == origin/main` left
no diff to PR. The fix (per the user's choice) is to use feature
branches going forward, not to rewrite shared history.
**Source:** Ship commit 295be58, STATE.md ship note

---

## Lessons

### w3lib does NOT strip default ports `:80` / `:443`
Direct probe during Plan 01 Task 3 showed `canonicalize_url(
"http://x.com:80/p")` leaves the `:80` intact. Required a wrapper
`_strip_default_port` to satisfy D-02.

**Context:** RESEARCH.md flagged this as Assumption A2 ("w3lib *probably*
covers all D-02 sub-rules"). The probe turned the assumption into a
known sub-rule the wrapper handles. The same probe also confirmed
w3lib DOES uppercase percent-hex and sort the query string, so those
sub-rules needed no extra code.
**Source:** 01-01-SUMMARY.md (Deviations § 1)

---

### SQLite STORED-via-ALTER only raises on a NON-EMPTY table
On SQLite 3.50.4, `ALTER TABLE … ADD COLUMN … GENERATED ALWAYS AS (…)
STORED` no-ops successfully on an empty table; the "cannot add a STORED
column" error fires only when SQLite has to backfill existing rows.

**Context:** D-07 / Pitfall 2 in research described the restriction
unconditionally. The first test failed (`DID NOT RAISE`) because the
`conn` fixture was empty. Fix: `write_run(conn, sample_run)` first,
then attempt the STORED ALTER — that's the realistic "promote later"
case and is when SQLite actually enforces the restriction.
**Source:** 01-02-SUMMARY.md (Deviations § 1), 01-02-PLAN.md (D-07)

---

### `_safe_abs` was the unguarded twin of `safe_pct`
Two finite floats can subtract to `inf`; each input passes the WR-01
`allow_inf_nan=False` validation individually, so the model layer
catches nothing. The resulting `RunDelta.delta_abs = inf` gets
serialized to `null` by Pydantic JSON mode — exactly the silent
corruption the WR-01/WR-02 fixes were supposed to prevent.

**Context:** Surfaced only in code-review re-review (the WR-01 pass
guarded `delta_pct` but not `_safe_abs`). Fix mirrored the `isfinite`
guard into `_safe_abs` and added `allow_inf_nan=False` to `RunDelta`
itself as a model-layer backstop.
**Source:** 01-REVIEW.md (WR-01)

---

### Duplicate `url_key` within one run silently dropped pages in `compute_deltas`
`cur_by_key = {p.url_key: p for p in current_run.pages}` collapses
same-key pages — every earlier page is silently discarded with no
warning. Reachable because (a) `canonical_key` deliberately collapses
spellings and (b) `write_run` had no uniqueness constraint on
`url_key` (only a non-unique index).

**Context:** Surfaced in code-review re-review. Two distinct measured
URLs (`https://x.com/?a=1` and `https://x.com/?a=1#frag`) legitimately
canonicalize to the same key. The fix rejects duplicates at write
time (`raise ValueError` in `write_run`) so every reader can trust
url_key uniqueness within a run.
**Source:** 01-REVIEW.md (WR-02), commit 3b0fd3d

---

### `canonical_key("…/a/%2e%2e/b")` returns literal `../` in the key
w3lib decodes percent-encoded dots; the `..` segments are NOT
resolved. Benign in Phase 1 (the key is only an opaque SQL bind
parameter), but any FUTURE consumer that derives a filename/path from
`url_key` (e.g. per-page Lighthouse artifacts on disk) MUST sanitize
at that boundary.

**Context:** Code-review IN-02; documented in `canonical.py` so a
future Phase 2/3 reader doesn't inherit a path-traversal vector by
treating the canonical key as a safe path component.
**Source:** 01-REVIEW.md (IN-02), 01-SECURITY.md (Notes)

---

### Pydantic v2 accepts inf/nan by default; needs `allow_inf_nan=False`
The default `BaseModel` would happily round-trip `float('inf')` and
`float('nan')` through a metric field. WR-01 added
`allow_inf_nan=False` on `MetricSample`, `WaterfallEntry`, and
`PageResult`; later WR-01 (re-review) added it to `RunDelta`.

**Context:** Comparison/regression math against inf/nan produces
nonsense direction labels and broken percentages. Rejecting at the
model layer is the cheapest place to enforce finiteness.
**Source:** 01-REVIEW.md (WR-01 fix history)

---

### FK enforcement in SQLite is per-connection, not per-database
`write_run` must re-assert `PRAGMA foreign_keys = ON` because a write
connection that did not also run `init_db` would otherwise allow an
orphan page row.

**Context:** Surfaced in WR-05; any future code path that inserts into
`page_results` on a fresh connection must follow the same per-connection
PRAGMA pattern.
**Source:** 01-REVIEW.md (WR-05), 01-SECURITY.md (Notes)

---

### Naive `datetime` strings are accepted by default; needed an explicit validator
`RunRecord.started_at` initially accepted naive datetimes; WR-04
added a validator that rejects naive `datetime` instances AND naive
ISO strings (date-only also rejected; offset-bearing strings like
`Z`, `+05:30` accepted).

**Context:** Cross-run comparisons rely on a total order on
`started_at`; naive timestamps from two different machines silently
violate that order.
**Source:** 01-REVIEW.md (WR-04), commit 6aa50a8

---

### `branching_strategy: "none"` bites at ship time, not at execute time
With "none", all phase work goes directly to `main` and (if the user
pushes during execution) lands on `origin/main` before `/gsd-ship`
ever runs — leaving `main == origin/main` with no diff to PR.

**Context:** Discovered only when `/gsd-ship 1` ran preflight checks.
The phase had to close administratively; PRs cannot be created
retroactively without rewriting shared history. The forward-fix is to
branch BEFORE the first phase commit (which is now enforced by
flipping `branching_strategy → "phase"`).
**Source:** Ship commit 295be58, STATE.md ship note

---

## Patterns

### One editable place: registry tables consumed by call sites
`TRACKING_PARAM_DENYLIST` (D-04) and `METRIC_POLARITY` (D-09) live
only in `registry.py`; `canonical.py` and `delta.py` import them and
never inline the literals. Acceptance criteria grep-assert the
denylist is not inlined.

**When to use:** Any policy table that a future phase will extend
(new tracking params, new metrics, new polarity). Inlining a single
literal at a single call site is the trap that this pattern blocks.
**Source:** 01-01-PLAN.md, 01-03-PLAN.md acceptance criteria

---

### Hybrid TEXT-blob + GENERATED-column store
Persist the canonical model as a raw JSON TEXT blob and promote
query-relevant fields via `GENERATED ALWAYS AS
(json_extract(record_json, '$.field'))`. STORED at CREATE; VIRTUAL
when promoting after rows already exist (D-07 / Pitfall 2).

**When to use:** Any record store where round-trip identity must hold
AND a subset of fields must be queryable/indexable. Cannot drift
because the column is computed from the blob, not a denormalized
copy.
**Source:** 01-RESEARCH.md (Pattern 3), 01-02-PLAN.md, 01-02-SUMMARY.md

---

### Forward-compat models: `extra="ignore"` + `Optional[…] = None`
Every model sets `model_config = ConfigDict(extra="ignore")` and every
later-phase field is `Optional` with a `None` default. Two-way
compatibility: newer blobs load under older code (extra keys dropped);
older blobs load under newer code (missing keys default to None).

**When to use:** Any data contract that will evolve additively across
phases. Bump `SCHEMA_VERSION` only on additive change; never remove or
rename a field (D-06).
**Source:** 01-02-PLAN.md, 01-02-SUMMARY.md, 01-RESEARCH.md (Pattern 1)

---

### Labeled-proxy invariant enforced at the model layer
A `@model_validator(mode="after")` rejects any field name in
`{"inp", "inp_ms", "interaction_to_next_paint"}`. Only the
explicitly-labeled `inp_proxy_tbt_ms` is allowed.

**When to use:** Whenever a measurement is structurally a proxy for
something it is NOT (here: TBT-as-INP-proxy in lab). The validator
makes the rule survive a future refactor that would otherwise
re-introduce the bare-INP name.
**Source:** 01-02-PLAN.md, 01-02-SUMMARY.md (D-15)

---

### TDD RED → GREEN commit pair per task
First commit writes the failing tests and confirms `ModuleNotFoundError`
or assertion failure; second commit adds the implementation. Six such
pairs across the phase (one per Task 3 of Plan 01, two per Plan 02,
one for Plan 03).

**When to use:** Any task where the contract is well-defined and the
test surface is small enough to write up-front. Keeps the
implementation honest and produces a permanent record that the test
existed BEFORE the code passed it.
**Source:** All three SUMMARY.md files (TDD Gate Compliance sections)

---

### Atomic `with conn:` write_run
`write_run` wraps the run-row insert AND the page-row `executemany`
in a single `with conn:` block, so a crash mid-write rolls back the
whole batch (CR-01 fix).

**When to use:** Any multi-statement write whose partial completion
would leave the store in a corrupt state (orphan rows, missing
children). The implicit `BEGIN`/`COMMIT` from Python's `sqlite3`
default isolation_level is functionally similar but invisible —
explicit is better.
**Source:** 01-REVIEW.md (CR-01), 01-VERIFICATION.md (Advisory Note)

---

### Defensive try/except + deterministic fallback for untrusted input
`canonical_key()` is wrapped in `try/except Exception` returning
`(url or "").strip()`. Empty/blank short-circuits to `""` BEFORE the
w3lib call so it does not collide with the canonicalized root URL
`"…/"` (WR-03).

**When to use:** Any function that parses arbitrary external strings
(URLs from crawls, fixtures, user input). DoS-safe: the function never
raises; the caller decides what to do with the deterministic fallback.
**Source:** 01-01-PLAN.md (T-01-01), 01-SECURITY.md (T-01-01)

---

### Page-presence flags in classify()
`classify(metric, current, previous, *, page_in_current,
page_in_previous)` — distinguish whole-page new/removed from one-sided
metric not_comparable, since both otherwise present as a `None` scalar.

**When to use:** Any classification over a (container, item) pair
where missing-container and missing-item must take different code
paths but look the same at the inner-value level.
**Source:** 01-03-SUMMARY.md (decisions frontmatter)

---

### Union-of-pages iteration so removed pages are emitted
`compute_deltas` iterates the UNION of `url_key`s from both runs
(previous-run order first, then current-only pages). Removed pages
appear in the output with `direction=REMOVED`, `current=None`.

**When to use:** Any cross-run diff where dropped items must be
visible. The trap is a left-join mindset where the "current" set
becomes the iteration baseline and disappearance becomes silence.
**Source:** 01-03-PLAN.md (D-11/Pitfall 4), 01-03-SUMMARY.md

---

### Finite-guard pattern: `isfinite(result)` after arithmetic
`safe_pct` and `_safe_abs` both apply `isfinite(…)` to their result
and return `None` if not finite. Sidesteps the "two finite inputs
overflow to inf" silent-corruption path.

**When to use:** Any arithmetic on float fields that crosses a model
boundary. The model-layer `allow_inf_nan=False` is necessary but not
sufficient — it catches inf/nan ON INPUT but not inf/nan PRODUCED by
operations.
**Source:** 01-REVIEW.md (WR-01 history), 01-SECURITY.md (T-01-D)

---

### Grep-clean documentation for negative-space invariants
`delta.py` doc text avoids the literal tokens `noise`, `threshold`,
`tolerance`, `noise_band` so the D-12 acceptance check
(`grep -Eiq "noise|threshold|tolerance|noise_band"` FAILS) stays
clean while still documenting that the variance gate is Phase 6 work.

**When to use:** Whenever a grep-asserted invariant ("X is NOT
present") forbids a vocabulary the implementer would naturally use in
comments. Substitute synonyms; document the constraint at the file
top so a later reader knows the vocabulary discipline is intentional.
**Source:** 01-03-SUMMARY.md (decisions frontmatter)

---

## Surprises

### uv was not pre-installed; pip install was PEP 668 blocked
At Plan 01 Task 2 start, `uv` was absent from PATH; the obvious `pip
install uv` was rejected by the Homebrew externally-managed-environment
guard (PEP 668). Used the official Astral installer
(`astral.sh/uv/install.sh`) to put uv 0.11.16 in `~/.local/bin`.

**Impact:** ~2 minutes of investigation and an environment note in
the SUMMARY. Future projects on this machine inherit a working `uv`,
so this surprise should not recur.
**Source:** 01-01-SUMMARY.md (Environment Notes)

---

### `STORED ALTER` test failed because the page_results table was empty
The D-07/Pitfall 2 invariant ("STORED ALTER raises") was version-
correct but condition-incomplete: SQLite 3.50.4 only enforces it once
the table holds at least one row. First test run was a green
`DID NOT RAISE` fail.

**Impact:** Required updating the test to `write_run(conn,
sample_run)` BEFORE the STORED ALTER attempt — which is the realistic
"promote a metric after runs exist" case anyway. The D-07 invariant
holds; the test now asserts it under realistic conditions.
**Source:** 01-02-SUMMARY.md (Deviations § 1)

---

### Two finite floats overflowed to `inf`, silently nulled on JSON write
A subtraction of `1.5e308 − (−1.5e308)` produces `inf`; Pydantic JSON
serialization turned `inf` into `null` with no error. Each input
passed the WR-01 model-layer guard individually because each is
finite — the unguarded operation was the arithmetic INSIDE the delta
engine.

**Impact:** A real regression on a maximum-magnitude metric would be
silently nulled in the output. Surfaced only on code-review re-review;
fix added the `_safe_abs` finite guard AND `allow_inf_nan=False` on
`RunDelta`.
**Source:** 01-REVIEW.md (WR-01)

---

### Duplicate `url_key` rows silently dropped in `compute_deltas`
`{p.url_key: p for p in pages}` keeps only the LAST same-key entry;
two pages whose distinct URLs canonicalize to the same key get
collapsed to one with no warning. The store had no uniqueness
constraint (only a non-unique index), so both rows persisted; only
the in-memory dict comprehension dropped them.

**Impact:** A regression could be masked, an "improvement" fabricated,
or a removed page misclassified — silently corrupting criterion #2.
Surfaced in code-review re-review; fix rejects duplicates at
`write_run` time so the invariant holds for every reader.
**Source:** 01-REVIEW.md (WR-02)

---

### `canonical_key` of `…/?a=1#frag` and `…/?a=1` collide by design
Stripping the fragment is part of D-05, so two distinct measured URLs
legitimately produce the same canonical key. This is the realistic
duplicate-url_key source — not a bug, but the reason WR-02 had to be
solved.

**Impact:** Reinforces that "url_key" is a logical-identity key, not a
URL alias; collisions are expected when only the fragment differs.
**Source:** 01-REVIEW.md (WR-02 reproducer)

---

### `%2e%2e` decodes to literal `../` in the canonical key
w3lib decodes percent-encoded dots without resolving the `..`
segments; `canonical_key("https://x.com/a/%2e%2e/b")` returns
`"https://x.com/a/../b"`. Benign now because url_key is opaque, but
documented as a future path-traversal trap (IN-02).

**Impact:** Phase 2+ artifact writers MUST sanitize before using
url_key as a path component. Documented in canonical.py and in
`01-SECURITY.md` forward-looking debt.
**Source:** 01-REVIEW.md (IN-02), 01-SECURITY.md (Notes)

---

### Code review added 54 tests on top of the original Nyquist 13
The validation contract specified 13 criterion-mapped tests; code
review iterations (WR-01..WR-07, IN-01..IN-04, CR-01) brought the
total to 67 — adversarial coverage for non-finite numerics, write_run
atomicity / FK reassertion / no-mutation / duplicate-key guards,
generated-column drift, and root-vs-blank-input collisions.

**Impact:** Final phase suite is ~5x the planned size and runs in
0.06 seconds. The review-driven growth was where the genuinely
surprising bugs (WR-01/WR-02 reviewer findings) were caught.
**Source:** 01-VALIDATION.md (Validation Audit 2026-05-26)

---

### The full 67-test suite runs in 0.06 seconds
Pure in-memory SQLite + pure in-memory Pydantic = effectively no I/O.
Sampling latency stayed under 100 ms even at the end of the phase.

**Impact:** Enables the documented "run pytest after every task
commit" sampling pattern without execution-time penalty. As the
project grows network/Lighthouse calls, this sub-second budget will
be the keystone "always run on commit" suite separate from any slow
integration tier.
**Source:** 01-VALIDATION.md, 01-UAT.md (Test 2)

---

### `/gsd-ship 1` had no diff to PR because work was already on main
The `branching_strategy: "none"` setting routed every Phase 1 commit
to `main`, and the user pushed during execution, so `main ==
origin/main` by the time ship ran. Phase 1 closed administratively;
the workflow can't retroactively PR what's already merged without
rewriting shared history.

**Impact:** Same-session config flip to `"phase"` so Phases 2-6 use
real feature branches; memory written so the next session branches
BEFORE the first phase commit. The cost of finding this at ship
instead of at execute time was one administrative ship + a config
fix, not a destructive history rewrite — which was the right
trade-off here.
**Source:** Ship commit 295be58, STATE.md ship note,
`perfcrawl-feature-branch-flow` memory
