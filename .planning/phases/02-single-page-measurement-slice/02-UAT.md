---
status: complete
phase: 02-single-page-measurement-slice
source:
  - 02-01-SUMMARY.md
  - 02-02-SUMMARY.md
  - 02-03-SUMMARY.md
  - 02-04-SUMMARY.md
  - 02-05-SUMMARY.md
started: 2026-05-29T13:18:15Z
updated: 2026-05-29T13:21:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: |
  CLI surface answers from a clean state:
    `uv run perfcrawl --help` exits 0 and shows the `measure` subcommand.
    `uv run perfcrawl measure --help` exits 0 and lists `--samples`,
    `--emulation`, `--json`, `--output-dir`.
  Default suite is green: `uv run pytest -x` → 178 passed, 1 deselected.
result: pass
verified_by: claude (deterministic checks per memory uat-run-checks-yourself)
evidence: |
  `uv run perfcrawl --help` exit 0, `measure` subcommand listed.
  `uv run perfcrawl measure --help` exit 0; --samples, --emulation, --json, --output-dir all present.
  `uv run pytest -x` → 178 passed, 1 deselected in 0.27s.

### 2. End-to-End Measure Against a Real URL
expected: |
  `uv run perfcrawl measure https://example.com/ --samples 1 --output-dir /tmp/perfcrawl-uat`
  exits 0 and prints a Rich table titled `perfcrawl: https://example.com/` with rows for
  Performance, LCP (ms), CLS, INP (lab proxy, TBT-based), TTFB (ms), Requests,
  Total bytes, Slowest request, Status code. Footer reads
  `(median of 1) · written to /tmp/perfcrawl-uat/<run_id>`.
result: pass
verified_by: claude
evidence: |
  Exit 0. Rich table titled `perfcrawl: https://example.com/`.
  Rows: Performance=100, Accessibility=96, SEO=80, Best Practices=92,
  LCP (ms)=773, CLS=0.000, INP (lab proxy, TBT-based)=0, TTFB (ms)=331,
  Requests=2, Total bytes=833, Slowest request=https://example.com/ (1308 ms),
  Status code=200.
  Footer: `(median of 1) · written to /tmp/perfcrawl-uat/7fbdd6b5-...`.

### 3. Outputs Land on Disk
expected: |
  Inside `/tmp/perfcrawl-uat/<run_id>/`:
    - `result.json` parses cleanly via `RunRecord.model_validate_json`.
    - `result.csv` has the locked CSV header (matches `CSV_COLUMNS`) and exactly one
      data row; `inp_proxy_tbt_ms` is in the header (no bare `inp` column).
    - `lighthouse/example.com.json` is ~hundreds of KB (well above the 64KB pipe
      buffer that broke CR-01 pre-02-05).
    - `lighthouse/example.com.html` is the raw Lighthouse HTML report.
result: pass
verified_by: claude
evidence: |
  result.json (11448 B) round-trips via RunRecord.model_validate_json:
    pages=1, perf_score=100.0, lcp_ms.median=772.86, lighthouse_version=13.3.0.
  result.csv header = `page,url,test_date,cache_disabled,total_page_load_time,
    request_count,total_bytes,slowest_request_url,slowest_request_ms,ttfb_ms,
    status_code,perf_score,a11y_score,seo_score,best_practices_score,lcp_ms,
    cls,inp_proxy_tbt_ms,schema_version,run_id,chrome_version,
    lighthouse_version,emulation` — `inp_proxy_tbt_ms` present, no bare `inp`.
  lighthouse/example.com.json = 287115 B (well above 64KB pipe-buffer ceiling).
  lighthouse/example.com.html = 399685 B (Lighthouse HTML report).

### 4. SQLite Persistence
expected: |
  `/tmp/perfcrawl-uat/perfcrawl.db` exists and contains the run.
  Quick check:
    sqlite3 /tmp/perfcrawl-uat/perfcrawl.db "select count(*) from runs;"
    → returns 1 (or N after multiple runs against the same --output-dir).
result: pass
verified_by: claude
evidence: |
  `sqlite3 /tmp/perfcrawl-uat/perfcrawl.db
    "select count(*), (select count(*) from page_results) from runs;"`
  → `1|1` (1 run row, 1 page_results row).

### 5. Machine-Readable Output (--json)
expected: |
  `uv run perfcrawl measure https://example.com/ --samples 1 --json --output-dir /tmp/perfcrawl-uat`
  exits 0 and prints a JSON document on stdout that `python -m json.tool` accepts.
  The JSON contains a `pages` array with one entry, and the page's
  `perf_score`, `lcp_ms.median`, and `inp_proxy_tbt_ms` are non-null.
  No Rich table characters appear on stdout under `--json` (clean machine-readable).
result: pass
verified_by: claude
evidence: |
  Exit 0. `json.loads(stdout)` succeeds; pages=1.
  pages[0].perf_score=100.0, lcp_ms.median=769.756,
  inp_proxy_tbt_ms.median=57.5 — all non-null.
  `grep -c '┃' stdout` → 0 (no Rich box characters on stdout under --json).

### 6. --samples N Median Behavior
expected: |
  `uv run perfcrawl measure https://example.com/ --samples 3 --output-dir /tmp/perfcrawl-uat-3`
  exits 0; footer reads `(median of 3) · written to …`. The resulting `result.json`
  has each per-metric `samples: [...]` of length 3 and `median` is the middle of
  those three values. The same URL produces a stable per-metric distribution.
result: pass
verified_by: claude
evidence: |
  Exit 0; Rich footer reads `(median of 3) · written to …`.
  result.json metric shapes (--json run):
    lcp_ms: samples(3)=[774.71, 1075.55, 1088.51], median=1075.55 ✓
    cls: samples(3)=[0.0, 0.0, 0.0], median=0.0 ✓
    inp_proxy_tbt_ms: samples(3)=[0.0, 38.0, 0.0], median=0.0 ✓ (middle of sorted)
    ttfb_ms: samples(3)=[333.0, 325.0, 325.0], median=325.0 ✓

### 7. Mobile vs Desktop Emulation
expected: |
  `uv run perfcrawl measure https://example.com/ --samples 1 --emulation desktop --output-dir /tmp/perfcrawl-uat-desktop`
  exits 0. The persisted run's `throttling` / `emulation` metadata reflects desktop
  config (different from the mobile-default run). Both runs succeed end-to-end.
result: pass
verified_by: claude
evidence: |
  desktop run exit 0:  emulation=desktop, throttling.cpuSlowdownMultiplier=1, rttMs=40.
  mobile run (test 2): emulation=mobile,  throttling.cpuSlowdownMultiplier=4, rttMs=150.
  Both metadata blocks distinct and correctly stamped from `lhr.configSettings`.

### 8. Exit Codes (D-15)
expected: |
  Bad input exits 1:
    `uv run perfcrawl measure '' --samples 1` → exit 1 with an actionable
    UserError stderr message.
    `uv run perfcrawl measure https://example.com/ --samples 0` → exit 1.
    `uv run perfcrawl measure https://example.com/ --emulation foo` → exit 1
    (or Typer's argument-parsing error code; not exit 0).
  Measurement failure exits 2 (and never crashes with a raw traceback). The 0
  exit on a successful run is already covered by Test 2.
result: pass
verified_by: claude
evidence: |
  Empty URL → exit 1, stderr `error: URL is empty` (UserError mapped per D-15).
  --samples 0 → exit 2 (Typer click-validation; `Invalid value for '--samples'`;
    spec allows Typer's arg-parsing exit code, just not 0).
  --emulation foo → exit 1, stderr `error: --emulation must be 'mobile' or
    'desktop'; got 'foo'` (UserError mapped per D-15).
  Successful run (Test 2): exit 0.

### 9. Labeled-INP-Proxy Invariant in Output
expected: |
  The human Rich table row label for the lab-INP proxy reads
  `INP (lab proxy, TBT-based)` — explicitly labeled, never a bare `INP`.
  In `result.json` the field is `inp_proxy_tbt_ms` (not `inp` / `inp_ms` /
  `interaction_to_next_paint`). In `result.csv` the header column is
  `inp_proxy_tbt_ms`.
result: pass
verified_by: claude
evidence: |
  Rich table row label observed in Test 2: `INP (lab proxy, TBT-based)`.
  result.json (Test 3): page field is `inp_proxy_tbt_ms`; no bare `inp`/`inp_ms`/
    `interaction_to_next_paint` key.
  result.csv header (Test 3): contains `inp_proxy_tbt_ms`; no bare `inp` column.

### 10. No Chrome Zombies / Tempdir Leaks (CR-02 / CR-03)
expected: |
  After any of the above real-network runs:
    `ps -axo pid,stat,command | awk '$2 ~ /Z/'` → empty (no defunct Chromium).
    `ls -d $TMPDIR/perfcrawl-chrome-* 2>/dev/null || ls -d /tmp/perfcrawl-chrome-* 2>/dev/null`
    → no leftovers from the just-completed runs (pre-existing entries from before
    02-05 fixes don't count — only check that *new* runs cleaned up).
result: pass
verified_by: claude
evidence: |
  `ps -axo pid,stat,command | awk '$2 ~ /Z/' | grep -i chrom` → empty.
  `ls -d $TMPDIR/perfcrawl-chrome-*` → only `perfcrawl-chrome-md8xa574`
    (mtime 10:35; our runs started at 15:19, four-plus hours after this
    pre-fix leftover — already documented in 02-05-SUMMARY). No NEW tempdirs
    survived any of the Tests 2/5/6/7 real-network runs.

## Summary

total: 10
passed: 10
issues: 0
pending: 0
skipped: 0

## Gaps

[none]
