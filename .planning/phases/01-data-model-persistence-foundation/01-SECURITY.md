---
phase: 1
slug: data-model-persistence-foundation
status: verified
threats_open: 0
asvs_level: 1
created: 2026-05-26
---

# Phase 1 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Audited 2026-05-26 by `gsd-security-auditor` (FORCE stance) — every declared
> mitigation treated as ABSENT until a concrete code location proved it present.
> Implementation files are read-only here; gaps are recorded, never patched in place.
>
> **Disposition:** SECURED — 9/9 threats CLOSED (7 mitigate, 2 accept), 0 open,
> 0 unregistered flags. Test suite at audit time: `uv run pytest -q` → 67 passed.
>
> Phase 1 is an offline, library-only layer: no network, no auth, no secrets, no
> CLI. The only external trust boundaries are (a) the supply-chain install of
> `w3lib`, (b) arbitrary URL strings fed to `canonical_key()`, (c) a JSON blob
> fed to `model_validate_json()`, and (d) a caller-supplied SQLite file path.
> All four are addressed below.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| package registry → local install | `uv add w3lib` pulls third-party code into the project at install time. | Python wheel / sdist (one-time, install-time only). |
| untrusted URL string → `canonical_key()` | URLs originating from future crawl/fixtures are parsed by `canonical.py`. | Arbitrary `str` (possibly malformed, oversized, NUL-bearing). |
| Pydantic model → SQLite store | `RunRecord` serialized to JSON and written into a SQLite TEXT column. | Validated typed model → JSON TEXT. |
| untrusted JSON blob → `model_validate_json()` | A stored/fixture JSON blob is deserialized back into the typed model. | JSON TEXT (possibly old-schema or extra-keyed). |
| SQLite file path → filesystem | `store.py` opens a DB via a caller-supplied `sqlite3.Connection`. | Filesystem path (caller-owned, never in SQL). |
| two `RunRecord`s → `compute_deltas` | Pure in-memory regression computation over already-validated models. | Typed models only — no I/O, no network. |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-01-SC | Tampering (supply chain) | `uv add w3lib` install | mitigate | `pyproject.toml:9` pins `w3lib>=2.3,<3` (range-bounded); `uv.lock:239-244` locks w3lib **2.4.1** from `pypi.org/simple` with sdist + wheel SHA-256 hashes (`8dd69e…be864` / `409301…138d`). Human-verify supply-chain gate recorded approved before install (`01-01-SUMMARY.md` decisions / "What Was Built" Task 1). Stdlib fallback documented in plan if declined. | closed |
| T-01-01 | Denial of Service | `canonical_key()` parsing untrusted/malformed URLs | mitigate | `canonical.py:88-89` empty/blank short-circuit to `""`; `canonical.py:90-108` wraps the whole transform in `try/except Exception` returning a deterministic `(url or "").strip()` — never raises. Tests: `test_canonical.py:101-107` (parametrized malformed inputs incl. `"://broken"`, `"http://"`), `:110-113` blank sentinel. Runtime probe: NUL bytes, `a*100000`, `ht!tp://[bad` all return a `str` with no exception. | closed |
| T-01-02 | Tampering (version drift) | Pydantic / w3lib version drift | accept | Acceptance basis verified in code: ranges pinned (`pyproject.toml:8` `pydantic>=2.10,<3`, `:9` `w3lib>=2.3,<3`) AND exact versions locked with hashes (`uv.lock` pydantic 2.13.4 `:78-81`, w3lib 2.4.1 `:239-244`). See Accepted Risks Log. | closed |
| T-01-T | Tampering (SQL injection) | `store.py` `write_run` / `read_run` SQL | mitigate | Every data statement uses `?` placeholders: `store.py:140-150` (runs INSERT), `:151-154` (page_results `executemany`), `:162-164` (read SELECT). DDL is a static `executescript` of a module constant (`store.py:49-67,77`) with no interpolated values; PRAGMAs are static literals (`:76,:134`). No dynamic table names. Acceptance-criterion grep `execute\(f"\|execute\(.*%\|\.format\(` over `store.py` → **no matches**. Tests: `test_store.py` round-trip + adversarial-key suite (`test_write_run_rejects_duplicate_url_key` uses a fragment-laden URL as data, stored safely as a bind param). | closed |
| T-01-D2 | Tampering (untrusted/old blob) | `model_validate_json()` on stored blob | mitigate | `read_run` only ever calls `RunRecord.model_validate_json(...)` (`store.py:167`) — schema-validated construction, no arbitrary object instantiation. `model_config = ConfigDict(extra="ignore")` on every model (`models.py:68,80,96,118,178`; `delta.py:57`) drops unknown keys; all later-phase fields are `Optional[...] = None`, so old-schema blobs load with `None`. Tests: `test_store.py:54-68` (`test_old_schema_loads`), `test_models.py:84-108` (old blob → None defaults; newer blob with `future_field` → dropped). | closed |
| T-01-P | Tampering (SQLite file path) | DB path handling | mitigate | DB is opened via an explicit caller-supplied `sqlite3.Connection` (`store.py` `init_db`/`write_run`/`read_run` all take `conn`); the path never enters any SQL string (no path concatenation — confirmed by the dynamic-SQL grep above). `.gitignore:20-22` ignores `*.db` / `*.sqlite` / `*.sqlite3` so a future populated store is not committed. Documented at `store.py:33-37`. | closed |
| T-01-I | Information Disclosure | `record_json` blob contents | accept | Acceptance basis verified: no secret/credential field is persisted in the Phase 1 schema. Grep of `models.py` for `password\|secret\|token\|api_key\|credential\|cookie\|bearer` → **no matches**. `auth_used` (`models.py:184`) is a `bool` flag, not a credential. See Accepted Risks Log; re-evaluate at Phase 4 (auth). | closed |
| T-01-D | Denial of Service (numerics) | `compute_deltas` on malformed numerics (previous==0, None, inf, NaN) | mitigate | `safe_pct` returns `None` when `previous in (None, 0)` or `current is None`, and re-checks `isfinite(pct)` (`delta.py:134-137`) — never inf/NaN/ZeroDivisionError. `_safe_abs` mirrors the finite guard (`delta.py:150-153`). `classify` handles `None` on either side (`delta.py:104-113`). `RunDelta` has `allow_inf_nan=False` model backstop (`delta.py:57`). Tests: `test_delta.py:100-110` (previous==0 → pct None, abs still computed), `:127-145` (non-finite inputs → None), `:157-179` (overflow → None). Runtime probe confirms no exception on 0/None/inf. | closed |
| T-01-N | Tampering (direction mislabel) | direction logic (regression mislabeled as improvement) | mitigate | `direction` is derived from the single `METRIC_POLARITY` registry — `delta.py:38` imports it; `delta.py:110,117-121` reads polarity from the table and never hardcodes which metric is lower/higher-is-better. The per-metric polarity fact lives only in `registry.py:48-62`. The lone enum comparison at `delta.py:117` (`is Polarity.LOWER_IS_BETTER`) is the generic registry read, not a per-call-site hardcode. `class DirectionStatus` is NOT redefined in `delta.py` (imported from `models.py`). Tests: `test_delta.py:45-94` (`test_direction_by_polarity` asserts both polarities + a mirror case proving direction tracks polarity, not the sign of the delta). | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

### Unregistered Flags

None. The three Plan SUMMARY files contain no `## Threat Flags` section, and the
implementation introduced no new attack surface beyond the registered components:
all public entry points (`canonical_key`, `init_db`/`write_run`/`read_run`,
`compute_deltas`/`classify`/`safe_pct`, and the model classes) map to threats
already in the register. The executor's additional hardening (duplicate-`url_key`
rejection WR-02, per-connection FK re-assertion WR-05, atomic-write rollback
CR-01, tz-aware `started_at` WR-04, inf/NaN rejection WR-01) strengthens — and
does not widen — the registered surface; noted as defense-in-depth, not as
unregistered threats.

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-01-02 | T-01-02 | Pydantic / w3lib version drift: both deps are range-bounded in `pyproject.toml` (`pydantic>=2.10,<3`, `w3lib>=2.3,<3`) and exact-version + hash-locked in `uv.lock` (pydantic 2.13.4, w3lib 2.4.1). Local-only library, no runtime network. Residual drift risk is bounded by the lockfile and accepted. **Re-evaluate** on any major-version dep bump, or when network-facing phases land. | gsd-security-auditor | 2026-05-26 |
| AR-01-I | T-01-I | `record_json` blob contents: Phase 1 schema persists no secrets/credentials (grep-verified: no password/secret/token/api_key/credential/cookie/bearer fields in `models.py`; `auth_used` is a bool). The blob may later hold page URLs only. Accepted for Phase 1. **Re-evaluate at Phase 4** (authentication) — must re-confirm no session tokens/cookies are serialized into `record_json` once auth exists. | gsd-security-auditor | 2026-05-26 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-05-26 | 9 | 9 | 0 | gsd-security-auditor (opus) via /gsd-secure-phase 1 |

---

## Notes / Forward-looking security debt (not Phase 1 blockers)

- **`url_key` is NOT a safe filesystem path.** `canonical.py:72-82` documents (IN-02)
  that w3lib decodes percent-encoded dots, so `canonical_key("…/a/%2e%2e/b")` yields
  literal `../` segments. Benign now (the key is only ever an opaque SQL bind
  parameter), but any FUTURE consumer that derives a filename/path from `url_key`
  (e.g. per-page Lighthouse artifacts on disk) MUST sanitize at that boundary. Track
  when artifact-on-disk writing is implemented (Phase 2+).
- **FK enforcement is per-connection.** `write_run` re-asserts `PRAGMA foreign_keys = ON`
  (`store.py:134`) so write connections that did not run `init_db` still enforce the
  `page_results → runs` reference. Any future code path that inserts into
  `page_results` on a fresh connection should follow the same pattern.

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log (AR-01-02, AR-01-I)
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter
