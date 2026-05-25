# Phase 1: Data Model & Persistence Foundation - Context

**Gathered:** 2026-05-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Define the canonical, typed result model — `PageResult` / `RunRecord` / `RunDelta` — and the SQLite run store that persists runs and computes regression deltas. This is the **data contract** every later component (measurement, AI, exporters, regression) targets. Nothing is user-visible at runtime in this phase; the explicit goal is **one contract that never needs retrofitting**.

**In scope:** the Pydantic model layer, the SQLite store (write run / read run back identically), `RunDelta` computation against fixture data, `schemaVersion` on the model, and the canonical URL key used for cross-run page identity (ROADMAP Phase 1 success criteria 1–4, requirement HIST-01).

**Out of scope (belongs to later phases):** actually measuring pages (Phase 2), running median-of-N sampling (Phase 2 — Phase 1 only makes the distribution *storable*), crawling/discovery (Phase 3), auth (Phase 4), AI generation (Phase 5), exporters + noise-band regression gating (Phase 6), and v2 backend metrics.

</domain>

<decisions>
## Implementation Decisions

### Page Identity (canonical URL key) — success criterion #4
- **D-01:** Persist **both** the URL as-measured **and** a separate **canonical key** used for cross-run matching / `RunDelta` joins. The original is never mutated; the key is derived.
- **D-02:** Canonicalization follows RFC 3986 syntax normalization plus perf-tool conventions: **lowercase** scheme + host; strip default ports (`:80`/`:443`); resolve `.`/`..` dot-segments; uppercase percent-encoding hex; **preserve path case** (paths are case-sensitive on most servers).
- **D-03:** **Strip the trailing slash** except for root `/`. Do **not** strip `www` or `index.html` by default — those can be genuinely distinct hosts/resources; avoiding the merge prevents false page-identity collisions.
- **D-04:** Query string: **drop known tracking params** (`utm_*`, `gclid`, `fbclid`, …), **sort remaining params alphabetically**, and **keep functional params** (`?page=2`, `?id=5` are genuinely distinct pages). This also pre-bounds the Phase 3 "query-string explosion" problem; keep the tracking-param denylist in a single editable place.
- **D-05:** **Always drop the `#fragment`** — it never identifies a distinct server resource.

### Schema Evolution (schemaVersion) — success criterion #3
- **D-06:** **Additive-only ("expand") evolution.** Never remove or rename fields; only add new optional ones. Each `RunRecord`/`PageResult` carries an integer `schemaVersion`.
- **D-07:** **Hybrid SQLite store**: a full-fidelity **JSON blob** of each record **plus promoted scalar columns** for the metrics that get queried/joined (regression, lookups). Adding a model field requires **no** `ALTER TABLE`; promoting a field to a queryable column is a cheap additive `ADD COLUMN`. This is the mechanism that delivers "never needs retrofitting."
- **D-08:** On read, missing fields default to `null` via Pydantic optionals, so a run stored under an older schema loads cleanly under the newer model. A genuine (rare) non-additive migration gets a tiny migration registry — **deferred until actually needed**, not built now.

### RunDelta Semantics — success criterion #2
- **D-09:** Each metric declares its **polarity** in a central metric registry: lower-is-better (LCP, CLS, TBT, TTFB, total bytes, request count, slowest-request time) vs higher-is-better (Lighthouse Performance/Accessibility/SEO/Best-Practices scores). `direction` is **derived from polarity**, never hardcoded at call sites.
- **D-10:** `RunDelta` fields: `current, previous, deltaAbs, deltaPct, direction`. `deltaPct` is **guarded against `previous == 0`** (emit `null`, never infinity/NaN).
- **D-11:** Edge cases via a **status/direction enum** `{improvement, regression, unchanged, new, removed, not_comparable}`: new page → `previous=null, direction=new`; disappeared page → `current=null, direction=removed` (**emitted, never silently dropped**); a metric present on only one side (schema drift) → `not_comparable`.
- **D-12:** Phase 1 computes **raw** direction only — `unchanged` means literally equal. The **noise-band / variance gating stays in Phase 6**; Phase 1 must not pre-empt it.

### Model Field Scope (forward-compat vs YAGNI) — phase goal
- **D-13:** Model the **full known v1 superset now**, with later-phase fields **nullable**: Lighthouse category scores; CWV; network facts (TTFB, request count, total bytes, response sizes, status code, slowest-request URL + ms) mapping to the existing Google Sheet columns; the network waterfall list (per request: URL, type, size, timing, status); a Lighthouse opportunities/diagnostics blob (METRIC-05 raw material); and an optional `analysis` sub-model for Phase 5 AI fields (Observation / Potential Cause / Suggested Optimization).
- **D-14:** **First-class median-of-N storage** now: per metric, store the aggregated `median` **and** the raw `samples[]`, so Phase 2 fills the distribution rather than retrofitting the model.
- **D-15:** The lab-INP-proxy field is **explicitly named as a TBT-based lab proxy** (e.g. `inp_proxy_tbt`) — **never a bare `inp`** that could be mistaken for field INP. Enforce this at the model layer.
- **D-16:** **Do NOT speculatively model v2 backend metrics** (BACK-01..03). Additive evolution (D-06/D-07) makes adding them free once the security spike lands; modeling them now is premature. This is the correct YAGNI line.

### RunRecord metadata
- **D-17:** `RunRecord` carries: run id (UUID), `started_at` timestamp, `target` (site/seed), `schemaVersion`, an `auth_used` flag (Phase 4), and a stamped-environment slot (Chrome version, Lighthouse version, throttling config, mobile/desktop emulation) that Phase 2 fills. Defined now, nullable until Phase 2 populates.

### Claude's Discretion
User deferred all four discussed areas to best practices ("not sure, lets follow best practices"). The decisions above are the builder's best-practice calls and are locked. Remaining latitude for the planner/executor: exact module layout, Pydantic field names and validator implementation, the precise SQLite DDL, the JSON-vs-column split per field, and how `metric polarity` is registered — provided the observable contract (D-01..D-17) holds.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope & requirements
- `.planning/ROADMAP.md` § "Phase 1: Data Model & Persistence Foundation" — the 4 success criteria this phase is verified against (round-trip persistence, RunDelta on fixtures, schemaVersion comparability, canonical URL key).
- `.planning/REQUIREMENTS.md` — **HIST-01** (persist every run) is the mapped requirement; **METRIC-01..05** and **RUN-04** (median-of-N) define the field shape the model must accommodate even though they ship in Phase 2.

### Stack & architecture decisions (research)
- `CLAUDE.md` (project root) § "Technology Stack" → "Run Persistence / History Store" — locks **SQLite over DuckDB/JSON** with rationale, the `runs` + `page_results` **schema sketch**, and the "regression = self-join on url against prior run_id" pattern. Also § "Recommended Stack" for **Pydantic 2.x** as the model layer.
- `CLAUDE.md` (project root) § "Measurement Engine" / "What NOT to Use" — the **INP-as-lab-proxy** rule (drives D-15) and the canonical-model-as-keystone principle (exporters/AI/history consume only the model, never raw engine output).

### Compatibility target (informs field set)
- `.planning/PROJECT.md` § "Context" — the **existing Google Sheet columns** the model's network-facts fields must eventually map to (Page, URL, Total Page Load Time, Number of Requests, Total Data Transferred, Slowest Request URL/Time, TTFB, Response Size, Status Code, …) and the reference sheet URL. The exporter is Phase 6, but the model fields are shaped here.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- **Greenfield** — no application source exists yet (only `CLAUDE.md` and `.planning/`). This phase establishes the first source modules and the project's Python scaffolding (uv / Ruff / pytest per the research stack).

### Established Patterns
- None in-repo yet. The research stack in `CLAUDE.md` is the de-facto pattern source: Pydantic 2.x models, stdlib `sqlite3`, `pytest` with canned fixtures (mock the future Node worker by feeding canned Lighthouse JSON).

### Integration Points
- This phase **is** the integration point for everything downstream — Phase 2's measurement normalizes into `PageResult`; Phase 5 fills the `analysis` sub-model; Phase 6's exporters and regression flagging read `RunRecord`/`RunDelta`. Keep the public model + store API the stable seam.

</code_context>

<specifics>
## Specific Ideas

- RunDelta direction must be **symmetric** about disappeared pages — they are emitted with `direction=removed`, never silently omitted (D-11).
- The tracking-param denylist (D-04) and the metric polarity table (D-09) should each live in **one editable place** so later phases extend them without touching call sites.
- Test the round-trip (write → read-back-identical) and RunDelta math against **fixture data** specifically (success criteria #1 and #2 call out fixtures) — no live measurement needed to verify this phase.

</specifics>

<deferred>
## Deferred Ideas

- **v2 backend metrics (BACK-01..03)** — deliberately not modeled now (D-16); additive evolution makes them a clean structural add-on once the security-gated access-mechanism spike lands. Tracked in REQUIREMENTS.md v2 + STATE.md deferred items.
- **Noise-band / variance-aware regression gating** — `RunDelta` here computes raw direction only; the threshold that suppresses false flags is **Phase 6** (HIST-02).
- **Non-additive schema migrations** — a migration registry is sketched (D-08) but not built until a real non-additive change forces it.

None of these are scope creep introduced in discussion — they are pre-existing roadmap boundaries restated to keep the planner inside Phase 1.

</deferred>

---

*Phase: 1-Data Model & Persistence Foundation*
*Context gathered: 2026-05-25*
