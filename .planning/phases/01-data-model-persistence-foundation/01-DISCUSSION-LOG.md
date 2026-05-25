# Phase 1: Data Model & Persistence Foundation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-25
**Phase:** 1-Data Model & Persistence Foundation
**Areas discussed:** Page identity rules, Schema evolution, RunDelta semantics, Model field scope

---

## Area selection

| Option | Description | Selected |
|--------|-------------|----------|
| Page identity rules | What makes two URLs "the same page" across runs (criterion #4) | ✓ |
| Schema evolution | How older runs behave in comparisons when fields are added (criterion #3) | ✓ |
| RunDelta semantics | Per-metric direction + edge cases for missing comparisons (criterion #2) | ✓ |
| Model field scope | How forward-looking PageResult is now (phase goal: never retrofit) | ✓ |

**User's choice:** All four areas selected, then deferred the decisions themselves to best practices.
**Notes:** User said "Im not sure, lets follow best practices in these areas." Builder presented one consolidated best-practice recommendation per area; user chose "Lock all in (recommended)."

---

## Page identity rules

| Option | Description | Selected |
|--------|-------------|----------|
| Canonical key (RFC 3986 + perf conventions) | Store URL-as-measured + derived canonical key; lowercase scheme/host, strip default ports, resolve dot-segments, strip trailing slash (except root), drop tracking params + sort the rest + keep functional params, drop fragment; preserve path case; don't strip www/index.html | ✓ |
| Drop all query strings | Simpler key, but merges paginated/filtered pages incorrectly | |
| Keep raw URL as key | No normalization — duplicate keys for trivially different URLs | |

**User's choice:** Best practice (canonical key).
**Notes:** Pre-bounds the Phase 3 query-string-explosion problem. Tracking-param denylist kept in one editable place.

---

## Schema evolution

| Option | Description | Selected |
|--------|-------------|----------|
| Additive-only + hybrid JSON-blob/columns store | Never remove/rename; schemaVersion int; full-fidelity JSON blob + promoted scalar columns; missing fields default null on read; migration registry deferred | ✓ |
| Destructive migrations per change | ALTER/rewrite on every schema change — brittle, breaks "never retrofit" | |
| Exclude old runs from new-metric deltas | Loses comparability the user wants to keep | |

**User's choice:** Best practice (additive + hybrid store).
**Notes:** This mechanism is what literally delivers the phase goal "one contract that never needs retrofitting."

---

## RunDelta semantics

| Option | Description | Selected |
|--------|-------------|----------|
| Polarity registry + status enum | Metric polarity declared centrally; fields current/previous/deltaAbs/deltaPct/direction; deltaPct guarded vs previous==0; enum {improvement, regression, unchanged, new, removed, not_comparable}; raw direction only (noise band stays Phase 6) | ✓ |
| Hardcode direction per call site | Error-prone, duplicated polarity logic | |
| Apply noise-band gating here | Would pre-empt Phase 6 (HIST-02) | |

**User's choice:** Best practice (polarity registry + status enum).
**Notes:** Disappeared pages are emitted with direction=removed, never silently dropped.

---

## Model field scope

| Option | Description | Selected |
|--------|-------------|----------|
| Full known v1 superset, later fields nullable; no v2 backend fields | Model Lighthouse scores/CWV/network facts/waterfall/diagnostics + optional analysis sub-model + first-class median+samples; INP named as TBT lab proxy; defer v2 backend fields to additive evolution | ✓ |
| Minimal (only HIST-01 essentials) now | Would force retrofits in Phases 2/5 — violates phase goal | |
| Model everything incl. v2 backend | Speculative generality; YAGNI violation | |

**User's choice:** Best practice (known v1 superset, nullable later fields, no speculative v2).
**Notes:** Additive evolution (D-06/D-07) makes adding v2 backend metrics free later, so reserving them now is unnecessary.

---

## Claude's Discretion

User deferred all four areas to best practices. Builder made the calls; remaining latitude (module layout, exact field names/validators, SQLite DDL, JSON-vs-column split, polarity registration) left to the planner/executor provided the observable contract D-01..D-17 holds.

## Deferred Ideas

- v2 backend metrics (BACK-01..03) — not modeled now; clean additive add-on after the security spike.
- Noise-band / variance-aware regression gating — Phase 6 (HIST-02).
- Non-additive schema migration registry — built only when a real non-additive change forces it.
