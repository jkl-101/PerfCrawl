---
phase: 2
slug: single-page-measurement-slice
status: verified
threats_open: 0
asvs_level: 1
created: 2026-05-29
---

# Phase 2 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.
> Audited 2026-05-29 by `gsd-security-auditor` (FORCE stance) — every declared
> mitigation treated as ABSENT until a concrete code location proved it present.
> Implementation files are read-only here; gaps are recorded, never patched in place.
>
> **Disposition:** SECURED — 26/26 threats CLOSED (25 mitigate, 1 accept), 0 open,
> 0 unregistered flags. Threat register authored at plan time (`register_authored_at_plan_time: true`)
> across five plans (02-01..02-05); each SUMMARY's `## Threat Flags` / `## Threat Model`
> section maps to threats already in the register.
>
> Phase 2 introduces the measurement subsystem: URL → CLI → orchestrator (Playwright
> + CDP + Chromium subprocess) → Node Lighthouse worker → normalizer → aggregator
> → outputs (CSV / JSON / raw LH artifacts + SQLite via Phase 1 store). New external
> trust boundaries: (a) user-controlled URL into a Chromium subprocess argv and the
> filesystem (page_slug), (b) Node worker stdout as untrusted JSON, (c) Chrome process
> lifecycle vs the host process table and `/tmp`, (d) two new PyPI deps (`playwright`,
> `typer`, `rich`) reaffirming the Pitfall 8 slopsquat stance from Phase 1. The
> Phase 1 SQLite floor (`store.py`) is reused unchanged; its `with conn:` atomic
> transaction + parametrized SQL + per-connection `PRAGMA foreign_keys = ON`
> remain the mitigations for T-02-04-DB.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| user-controlled URL → `page_slug` | URL (decoded `%2e%2e` per Phase 1 IN-02) becomes a filesystem path component for raw LH artifacts. | Arbitrary `str` (possibly literal `..`, shell metachars, NUL). |
| user-controlled URL → subprocess argv | URL flows into `subprocess.run(argv_list)` for the Node worker; shell metachars must not trigger shell expansion. | `str` placed as one argv element. |
| Node worker stdout → Python normalizer | Worker emits `{lhr, reportJson, reportHtml}` JSON; an attacker-controlled or version-drifted worker could supply hostile audit shape. | JSON string; D-10 LH-major version gate is the integrity check. |
| Chrome process → host machine (`/tmp` + process table) | Long-running Chromium subprocess; on crash must be killed AND reaped (CR-02 zombie-defunct) AND its `user_data_dir` `shutil.rmtree`'d (CR-03 disk-leak). | Process lifecycle owned by orchestrator. |
| per-sample data → aggregated PageResult | N per-sample PageResults from worker subprocesses feed `aggregate_page_samples`; hostile inf/nan/None must be dropped without raising. | List of validated `PageResult` models. |
| output dir → host filesystem | `--output-dir` is a user-supplied path; writing outside that dir is a path-confusion bug. | `Path` (caller-owned), atomic-write via `os.replace`. |
| pyproject.toml deps → PyPI | `playwright`, `typer`, `rich` (added in Phase 2) reaffirm the Pitfall 8 stance: the abandoned 2016 PyPI `lighthouse` decoy must NEVER appear. | Lockfile + pinned semver bounds. |
| committed fixtures → repo | `tests/fixtures/lighthouse/` is checked in; must contain only IANA-reserved / non-sensitive data. | JSON capture files (read-only). |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-02-01 | Tampering | `slug.py::page_slug` (URL → filesystem stem) | mitigate | `slug.py:32` `_SAFE = re.compile(r"[^A-Za-z0-9._-]+")` charset restriction; `slug.py:36` `_DOTRUN = re.compile(r"\.{2,}")` collapses `..` runs to `__`; `slug.py:63` `_DOTRUN.sub("__", stem)` applies it; `slug.py:67` `stem.strip("._-")` strips leading/trailing dots; `slug.py:75-76` deterministic `"_"` fallback never raises. Tests: `tests/test_slug.py:37` `test_no_path_traversal_in_slug` parametrized over 6 traversal vectors. | closed |
| T-02-02 | Tampering | `normalizer.py` LH-JSON parser | mitigate | `normalizer.py:26-59` `_check_version()` raises `ValueError` on any LH major drift (D-10); `:75` invoked FIRST in `normalize_lh` before any audit shape is read. Model-layer floor: `models.py:68,96,118` `ConfigDict(extra="ignore", allow_inf_nan=False)` on `MetricSample`/`WaterfallEntry`/`PageResult`. Test: `tests/test_normalizer.py:190` `test_version_gate_rejects_major_drift` against `tests/fixtures/lighthouse/version-drift-14.json`. | closed |
| T-02-SC | Tampering (slopsquat) | `pyproject.toml` (Pitfall 8) | mitigate | `pyproject.toml:7-13` deps list contains no `"lighthouse"` line (only `playwright`, `pydantic`, `rich`, `typer`, `w3lib`). The two `lighthouse` substrings (lines 16, 21) are comments referencing `lighthouse-worker/` dir, not deps. The ONLY legitimate `lighthouse` reference is `lighthouse-worker/package.json:12` `"lighthouse": "13.3.0"` (npm — Google Lighthouse) with `lighthouse-worker/package-lock.json` committed (91KB, locked install). | closed |
| T-02-N | Spoofing | INP labeling drift (3-layer defense) | mitigate | Layer 1 (model): `models.py:38` `_FORBIDDEN_INP_FIELDS = frozenset({"inp", "inp_ms", "interaction_to_next_paint"})` + `:152-166` `@model_validator(mode="after") def _no_bare_inp`. Layer 2 (normalizer): `normalizer.py:156` writes TBT directly into `inp_proxy_tbt_ms=...` kwarg; grep over `normalizer.py` for `\binp\b` outside `inp_proxy_tbt_ms` returns no matches. Layer 3 (constant): `constants.py:57` `INP_PROXY_DISPLAY_LABEL: str = "INP (lab proxy, TBT-based)"`. | closed |
| T-02-D | Disclosure | `tests/fixtures/lighthouse/` (committed LH captures) | accept | Grep verification: `tests/fixtures/lighthouse/studyhalo-home-200.json` `"requestedUrl": "https://example.com/"` + `"finalDisplayedUrl": "https://example.com/"`; `studyhalo-404.json` `"requestedUrl": "https://example.com/__nope-404__"`; `version-drift-14.json` `"requestedUrl": "https://example.com/"`. ZERO `studyhalo.com` substrings in any fixture; 17 / 1 / 1 `example.com` substrings respectively. `example.com` is IANA-reserved (RFC 2606 / RFC 6761). Filenames are misleading (a relic of capture naming); content is the non-sensitive test domain. See Accepted Risks Log AR-02-D. | closed |
| T-02-02-A | Tampering | `aggregate_samples` (inf/nan input) | mitigate | `aggregator.py:59` `clean = [v for v in per_sample_values if v is not None and math.isfinite(v)]` — finite guard BEFORE `statistics.median()`. Defense-in-depth above `MetricSample.allow_inf_nan=False` (model layer). Test: `tests/test_aggregator.py:128` `test_aggregator_drops_non_finite_samples` parametrized over `[math.inf, -math.inf, math.nan]`. | closed |
| T-02-02-B | Denial of Service | `aggregate_samples([])` (Pitfall 3) | mitigate | `aggregator.py:60-61` `if not clean: return MetricSample(median=None, samples=[])` — explicit empty-list guard returns the honest-empty D-16 shape, never invokes `statistics.median([])` which would raise `StatisticsError`. Test: `tests/test_aggregator.py:105` `test_empty_samples_median_none`. | closed |
| T-02-02-C | Tampering | `aggregate_page_samples` (URL mismatch) | mitigate | `aggregator.py:94-98` `keys = {s.url_key for s in samples}; if len(keys) > 1: raise ValueError(...)` — explicit guard prevents silently merging two pages' metrics. Test: `tests/test_aggregator.py:251` `test_url_mismatch_raises`. | closed |
| T-02-02-D | Tampering | INP labeling in aggregator | mitigate | `aggregator.py:38-43` `_METRIC_SAMPLE_FIELDS = ("lcp_ms", "cls", "inp_proxy_tbt_ms", "ttfb_ms")` — the labeled name is the only INP-flavored token in the module. `aggregator.py:119` uses `samples[0].model_copy(update=updates)` preserving the Phase 1 `_no_bare_inp` floor by construction. Grep over `aggregator.py` for `\binp\b` outside `inp_proxy_tbt_ms` / `INP_PROXY` returns only a docstring at `:77`, never a variable. | closed |
| T-02-03-SH | Tampering | `lighthouse_worker.py::run_one_sample` (URL → subprocess) | mitigate | `lighthouse_worker.py:67-73` `argv: list[str] = [...]`; `:82-86` `subprocess.run(argv, capture_output=True, timeout=timeout_s)` — no `shell=True` kwarg. Grep guard: `grep -nE "shell\s*=\s*True" src/perfcrawl/*.py` → no matches. Tests: `tests/test_worker.py:173` `test_worker_argv_is_list_no_shell_expansion` (6 parametrized shell-metachar URL vectors); `tests/test_orchestrator.py:703` `test_orchestrator_source_has_no_shell_invocation` (source-level scan). | closed |
| T-02-03-Z | Denial of Service | Chrome process lifecycle | mitigate | `orchestrator.py:202-322` `try/finally` wraps the entire Playwright/CDP session; `:313-321` `chrome.kill()` + `chrome.wait(timeout=5)` (CR-02 reap) + `:322` `shutil.rmtree(user_data_dir, ignore_errors=True)` (CR-03 disk cleanup) on both success and failure. Tests: `tests/test_orchestrator.py:360` `test_chrome_killed_on_success`, `:373` `test_chrome_killed_on_failure`, plus `.waited is True` assertions at `:370`, `:384`, `:407`, `:593`. | closed |
| T-02-03-HANG | Denial of Service | Lighthouse subprocess hangs | mitigate | Belt-and-suspenders: Python `lighthouse_worker.py:85` `subprocess.run(..., timeout=timeout_s)` with `timeout_s=PER_SAMPLE_TIMEOUT_S=60` (`constants.py:40`); Node side `lighthouse-worker/run.mjs:26-37` `const WATCHDOG_MS = 55_000; const watchdog = setTimeout(() => { ... process.exit(1); }, WATCHDOG_MS);` — worker's 55s self-terminate fires before Python's 60s backstop. | closed |
| T-02-03-RACE | Tampering | DevToolsActivePort TOCTOU | mitigate | `orchestrator.py:82` `tempfile.mkdtemp(prefix="perfcrawl-chrome-")`; `:101-108` argv passes `--remote-debugging-port=0`; `:119-144` polls `<user_data_dir>/DevToolsActivePort` (Chrome's documented contract) with monotonic-deadline loop. Grep guard: `grep -nE "socket\.bind" src/perfcrawl/*.py` matches only a docstring at `orchestrator.py:70` describing the anti-pattern — NEVER an actual `socket.bind` call. Test: `tests/test_orchestrator.py:415` `test_devtools_port_polling`. | closed |
| T-02-03-CONCURRENT | Tampering | Multi-invocation collision | mitigate | `orchestrator.py:82` `tempfile.mkdtemp(prefix="perfcrawl-chrome-")` — process-unique dir per invocation; `:104` `--remote-debugging-port=0` — kernel-picked free port. Module docstring `:1-27` documents the by-construction invariant. Two concurrent `perfcrawl measure` runs cannot collide on user-data-dir or port. | closed |
| T-02-03-SLOPSQUAT | Tampering | `pyproject.toml` (Pitfall 8 reaffirmation) | mitigate | `pyproject.toml:8` `"playwright>=1.60,<2"` (Microsoft-maintained, RESEARCH § Package Legitimacy Audit verified) added in Phase 2. No `"lighthouse"` dep line. Identical pattern to T-02-SC; reaffirmed at Plan 02-03. | closed |
| T-02-03-PARTIAL | Tampering | All-samples-fail → silent zero-data run | mitigate | `orchestrator.py:264-266` `if not per_sample_results: raise MeasurementError(f"all {samples} samples failed")` — never produces a silently-empty PageResult. `cli.py:167-169` catches `MeasurementError` and maps to `ExitCode.MEASUREMENT_ERROR`. Test: `tests/test_orchestrator.py:341` `test_all_samples_fail_raises_measurement_error`. | closed |
| T-02-04-PATH | Tampering | `output.py::write_outputs` (URL → filesystem path) | mitigate | `output.py:256` `base_slug = page_slug(page.url_key)` — Phase 2-01 IN-02 boundary applied to every per-page artifact. `:261-266` write paths constructed from `base_slug` only (never raw `url_key`). No `f"{url_key}.json"` pattern in source. Test: `tests/test_output.py:147` `test_slug_in_artifact_path_never_traverses` with `url_key="https://x.com/a/../b"`. | closed |
| T-02-04-DISCLOSURE | Disclosure | Committed `output/` with measured URLs + HTML reports | mitigate | `.gitignore:26` `output/` entry. Grep guard: `grep -c "^output/$" .gitignore` → `1`. Documented at `.gitignore:24-25` ("perfcrawl runtime artifacts — per-developer; never committed"). | closed |
| T-02-04-LABEL | Spoofing | Rich table INP labeling drift (4-layer DiD) | mitigate | Layer 1 (model): `models.py:152-166` `_no_bare_inp` validator. Layer 2 (normalizer): `normalizer.py:156` direct `inp_proxy_tbt_ms=` write. Layer 3 (CSV header): `output.py:64` `"inp_proxy_tbt_ms"` literal in `CSV_COLUMNS`. Layer 4 (Rich label): `cli.py:39` `from perfcrawl.constants import ... INP_PROXY_DISPLAY_LABEL`; `cli.py:113` `table.add_row(INP_PROXY_DISPLAY_LABEL, _format_metric_sample(page.inp_proxy_tbt_ms))`. Tests: `tests/test_output.py:71` `test_csv_inp_proxy_column_is_labeled`; `tests/test_cli.py:218` `test_inp_label_visible_in_rich_table`; `tests/test_cli.py:289` `test_cli_source_has_no_bare_inp` (source-level grep meta-test). | closed |
| T-02-04-SLOPSQUAT | Tampering | `pyproject.toml` (3rd reaffirmation) | mitigate | `pyproject.toml:10` `"rich>=13"` (Textualize) + `:11` `"typer>=0.15"` (tiangolo) added in Phase 2-04, both verified mature per RESEARCH § Package Legitimacy Audit. No `"lighthouse"` dep line (3rd grep confirmation). | closed |
| T-02-04-WRITE | Denial of Service | Output dir unwriteable | mitigate | `output.py:155` `target.parent.mkdir(parents=True, exist_ok=True)` and `:167` `os.replace` propagate `OSError` upward. `cli.py:172-180` catches `except OSError` → `raise typer.Exit(code=int(ExitCode.USER_ERROR))` per D-15. Tests: `tests/test_output.py:192` `test_output_dir_unwriteable_raises_oserror`; `tests/test_cli.py:157` `test_exit_one_when_output_dir_unwriteable`. | closed |
| T-02-04-DB | Tampering | SQLite write path | mitigate | `cli.py:184-189` reuses Phase 1 `store.init_db` + `store.write_run` unchanged. Phase 1 floor verified intact: `store.py:76,134` `PRAGMA foreign_keys = ON` per-connection; `:139` `with conn:` atomic transaction. Dynamic-SQL grep `grep -nE "execute\(f\"|execute\(.+%|\.format\(" src/perfcrawl/store.py` → no matches. Inherited from Phase 1 audit (T-01-T closed). | closed |
| T-02-05-LEAK-PROC | Information Disclosure / DoS | Zombie `<defunct>` Chromium | mitigate | CR-02 fix verified at TWO sites: `orchestrator.py:151-156` in `_launch_chrome_with_cdp_port` timeout path (`proc.kill()` + `proc.wait(timeout=5)`); `orchestrator.py:313-321` in `measure_url` finally (`chrome.kill()` + `chrome.wait(timeout=5)`). Tests: `tests/test_orchestrator.py:370,384,407` `chrome_proc.waited is True`; `:593` `fake_proc.waited is True`. | closed |
| T-02-05-LEAK-DISK | DoS (disk exhaustion) | `user_data_dir` leaks on launcher timeout | mitigate | CR-03 fix: `orchestrator.py:116` (resolve-then-launch exception cleanup, WR-11 structural sibling), `:157` (DevToolsActivePort timeout cleanup BEFORE raise), `:322` (measure_url finally) — all three `shutil.rmtree(user_data_dir, ignore_errors=True)` BEFORE any `raise MeasurementError`. Tested via `not user_data_dir.exists()` assertions in `tests/test_orchestrator.py`. | closed |
| T-02-05-TRUNC | Tampering (silent data loss) | Worker→Python JSON pipe truncation at ~64KB | mitigate | CR-01 fix: `lighthouse-worker/run.mjs:124-135` callback-form `process.stdout.write(payload, (err) => { clearTimeout(watchdog); if (err) { ... } process.exit(0); });` — drains kernel pipe BEFORE exit. Grep guard: `grep -c "process.stdout.write(payload" lighthouse-worker/run.mjs` → `1`. Test: `tests/test_worker.py:265` `test_worker_drains_large_stdout_payload` (1.5MB real-subprocess shim). | closed |
| T-02-05-TEST-SHIM | Tampering (test integrity) | Task 2 shim could silently truncate | mitigate | `tests/test_worker.py:304-308` shim mirrors EXACT Task 1 pattern: `process.stdout.write(payload, (err) => { if (err) { ... } process.exit(0); });`. Three layered assertions: `:320` `len(proc.stdout) > 1_000_000`; `:325` `json.loads(proc.stdout)` succeeds; `:327-328` `len(parsed["reportJson"]) == 1_500_000` AND `len(parsed["reportHtml"]) == 1_500_000`. Any truncation makes one fail. | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

### Unregistered Flags

None. All five Plan SUMMARY files contain `## Threat Flags` (or `## Threat Model — Mitigation Verification`) sections that map each summary entry to a threat already in the register:

- **02-01-SUMMARY** — "Threat Flags: None" + per-threat mapping to T-02-01 / T-02-02 / T-02-SC / T-02-N / T-02-D.
- **02-02-SUMMARY** — `## Threat Model — Mitigation Verification` table covering T-02-02-A / T-02-02-B / T-02-02-C / T-02-02-D.
- **02-03-SUMMARY** — "Threat Flags: None" + per-threat mapping to T-02-03-SH / T-02-03-Z / T-02-03-HANG / T-02-03-RACE / T-02-03-CONCURRENT / T-02-03-SLOPSQUAT / T-02-03-PARTIAL.
- **02-04-SUMMARY** — "Threat Flags: None" + per-threat mapping to T-02-04-PATH / T-02-04-DISCLOSURE / T-02-04-LABEL / T-02-04-SLOPSQUAT / T-02-04-WRITE / T-02-04-DB.
- **02-05-SUMMARY** — `## Threat Model — Mitigations Applied` table covering T-02-05-LEAK-PROC / T-02-05-LEAK-DISK / T-02-05-TRUNC / T-02-05-TEST-SHIM.

Executor-introduced hardening (IN-01 worker-side form-factor validation; IN-03 chrome_version triple parsing; IN-05 atomic-write durability docstring; IN-06/07/08 stderr callback drains and import ordering; WR-04/05/06/07/09/10/11/12 various review fixes) strengthens — and does not widen — the registered surface; noted as defense-in-depth, not as unregistered threats.

---

## ASVS Coverage (Level 1)

Aggregated across the five plans:

| Family | Coverage | Locations |
|--------|----------|-----------|
| V5 Input Validation | URL→slug sanitization; LH-version gate; None/inf/nan filtering; empty-list guard; URL-mismatch raise; UserError input gates on URL/samples/emulation; Typer argv parser. | `slug.py:39-78`, `normalizer.py:26-59`, `aggregator.py:59-98`, `orchestrator.py:188-196`, `cli.py:132-156`. |
| V7 Error Handling | UserError / MeasurementError two-arm contract; deterministic empty rather than crash on Pitfall 3; three-way exit-code mapping (0/1/2) with no stack-trace leakage to stdout. | `orchestrator.py:56-62,264-266`, `aggregator.py:60-61`, `cli.py:160-170,172-180`. |
| V8 Data Protection | `output/` in `.gitignore` prevents URL/HTML leakage; committed fixtures restricted to IANA-reserved example.com. | `.gitignore:26`, fixture grep verification above. |
| V12 Files and Resources | IN-02 slug boundary at every per-page artifact; atomic-write via `os.replace`; per-run `tempfile.mkdtemp` cleaned via `shutil.rmtree` in finally; CR-02 process reap; CR-03 disk cleanup before raise. | `output.py:136-167,256`, `orchestrator.py:116,157,313-322`. |
| V14 Configuration | `lighthouse-worker/package-lock.json` committed (91KB) for byte-identical npm installs; `pyproject.toml` semver bounds on every dep; `uv.lock` carried forward from Phase 1. | `lighthouse-worker/package-lock.json`, `pyproject.toml:7-13`. |

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-02-D | T-02-D | `tests/fixtures/lighthouse/{studyhalo-home-200,studyhalo-404,version-drift-14}.json` are committed real-LH captures whose CONTENT is entirely from `https://example.com/` (IANA-reserved per RFC 2606 / RFC 6761; not sensitive). Filenames retain a `studyhalo-` prefix from the team's naming convention but contain ZERO `studyhalo.com` substrings (grep-verified at audit time: 0/0/0 matches). No credentials, PII, internal hostnames, session cookies, or owned-domain artifacts. **Re-evaluate** the moment any new fixture is captured from a non-public domain or any owned site (e.g., `studyhalo.com`, internal staging, authenticated dashboards): such a fixture MUST go through a separate human-review checkpoint before commit (per Phase 4 auth-handling boundary, not this phase). Consider renaming the existing fixtures to `example-com-*.json` in a future cosmetic pass to remove the misleading naming. | gsd-security-auditor | 2026-05-29 |

*Accepted risks do not resurface in future audit runs.*

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-05-29 | 26 | 26 | 0 | gsd-security-auditor (opus) via /gsd-secure-phase 2 |

---

## Notes / Forward-looking security debt (not Phase 2 blockers)

- **Misleading fixture filenames.** `tests/fixtures/lighthouse/studyhalo-*.json` are
  IANA-reserved `example.com` captures; the name is a relic. Consider renaming to
  `example-com-*.json` to remove the implication that owned-site data is committed.
  Pure cosmetic; the content audit above is the authoritative check.
- **Phase 4 auth-handling boundary.** When `storage_state` cookies / session tokens
  enter the orchestrator, `RunRecord.auth_used` will start being meaningful and the
  fixture-capture policy AR-02-D references becomes load-bearing. Track at Phase 4.
- **Worker wheel-install path.** `pyproject.toml:14-23` documents the wheel-install
  failure mode (`lighthouse-worker/` sibling not bundled into wheel); Phase 3 plan
  notes the `PERFCRAWL_WORKER_DIR` env var as the lift. Surfaced as actionable
  `MeasurementError` from `lighthouse_worker.preflight()` today (`:159-165`).
- **Concurrent-invocation invariant by construction.** `tempfile.mkdtemp` + port-0
  is the basis for T-02-03-CONCURRENT closure; if a future plan stamps a fixed
  port or shared user-data-dir for any reason, that threat re-opens. Verify on any
  orchestrator-launcher refactor.

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log (AR-02-D)
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter
- [x] No `unregistered_flag` warnings
- [x] Phase 1 SQLite store invariants verified still intact (T-02-04-DB inheritance)
