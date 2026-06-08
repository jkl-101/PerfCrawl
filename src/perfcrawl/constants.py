"""Single-place tunables for Phase 2 measurement (D-02..D-16).

Later phases extend these constants *here only* — call sites never inline
a timeout, sample count, version string, exit code, or column label.

- ``PER_SAMPLE_TIMEOUT_S`` (D-14): subprocess.run(timeout=...) for the Node worker.
- ``DEFAULT_SAMPLES_N`` (D-08/D-16, Claude's-discretion): default for ``--samples``.
  Odd-N is friendlier for median (no even-N midpoint averaging).
- ``EXPECTED_LIGHTHOUSE_MAJOR`` (D-10): normalizer version gate. Bump when
  the worker's ``package-lock.json`` bumps the Lighthouse major. The name
  reflects the actual enforcement (major-only); minor-level pinning is
  intentionally not enforced because per-minor LH releases routinely keep
  audit shape backward-compatible.
- ``INP_PROXY_DISPLAY_LABEL`` (D-11): the human-summary column header for the TBT
  proxy. The CSV column name is the field name (``inp_proxy_tbt_ms``); the Rich
  table header reads the label declared here. Defense-in-depth: model layer +
  normalizer + display layer all enforce the labeled-proxy invariant.
- ``ExitCode`` (D-15): three exit codes — 0 success / 1 user error / 2 measurement
  error. Phase 6 will carve out 10+ for BUDG-01 budget verdicts; the gap is
  intentional.
- ``DEVTOOLS_PORT_FILE_TIMEOUT_S`` / ``DEVTOOLS_PORT_POLL_INTERVAL_S`` (Pitfall 1,
  Claude's-discretion): how long to wait for Chrome's ``DevToolsActivePort`` file
  before declaring launch failure. Pattern: poll the file inside the user-data-dir
  for up to TIMEOUT seconds at POLL_INTERVAL granularity.
- ``ALWAYS_INCLUDE_AUDITS`` (MEDIUM-4 carve-out from plan-check, supports OUT-04):
  audit IDs that are kept in ``diagnostics`` regardless of score. The default D-12
  filter is "score < 1", but a fast page can score 1.0 on ``interactive`` and
  still need its ``numericValue`` for the CSV ``total_page_load_time`` column.
  This frozenset is the carve-out.

Critical invariant (from Phase 1 LEARNINGS § "one editable place"): call sites
IMPORT from this module; never inline the literal. Phase 1 grep-asserted this
for ``TRACKING_PARAM_DENYLIST``; Phase 2 enforces the same for the timeout, the
samples default, the version pin, the exit codes, and the INP label.
"""

from enum import IntEnum

# --- D-14: per-sample subprocess timeout (the ONE editable place) -----------
PER_SAMPLE_TIMEOUT_S: int = 60  # seconds; the only "per-sample timeout" reference

# --- D-08 / D-16: default --samples count (the ONE editable place) ----------
DEFAULT_SAMPLES_N: int = 3  # odd-N is friendlier for median (Claude's discretion)

# --- D-10: normalizer version gate (the ONE editable place) -----------------
# Bumped only when ``lighthouse-worker/package-lock.json`` bumps the LH MAJOR.
# WR-02: the previous name ``EXPECTED_LIGHTHOUSE_MAJOR_MINOR`` (value "13.x")
# advertised minor-band pinning that the gate did not enforce — the gate has
# always been major-only. The name now matches the behavior; per-minor LH
# releases routinely keep audit shape backward-compatible, so major-only is
# the right band. To raise the floor, bump this to ``"14"`` etc.
EXPECTED_LIGHTHOUSE_MAJOR: str = "13"

# --- D-11 / D-15: labeled-proxy display label (the ONE editable place) ------
# Read by the Rich table header in the CLI display layer; defense-in-depth above
# the model-layer ``_no_bare_inp`` validator.
INP_PROXY_DISPLAY_LABEL: str = "INP (lab proxy, TBT-based)"

# --- Pitfall 1: DevToolsActivePort polling (Claude's discretion) ------------
# When Chrome launches with ``--remote-debugging-port=0`` it writes the resolved
# port to ``<user_data_dir>/DevToolsActivePort`` (line 1 = port). The orchestrator
# polls that file at POLL_INTERVAL granularity for up to TIMEOUT seconds.
DEVTOOLS_PORT_FILE_TIMEOUT_S: float = 5.0
DEVTOOLS_PORT_POLL_INTERVAL_S: float = 0.1

# --- D-12 + MEDIUM-4 (plan-check) carve-out: audits kept regardless of score ---
# The default ``diagnostics`` filter drops any audit with ``score >= 1`` (passing
# audits and meta audits) per D-12 — keeps the persisted JSON bounded. But the
# CSV ``total_page_load_time`` column (OUT-04) sources from ``audits["interactive"]
# .numericValue``, which is empty for fast pages that pass with ``score == 1.0``.
# This frozenset is the per-audit carve-out: include unconditionally regardless
# of ``score``. Add IDs here as new CSV columns reach for ``audits[…].numericValue``.
ALWAYS_INCLUDE_AUDITS: frozenset[str] = frozenset({"interactive"})


# --- D-15: exit codes (the ONE editable place) ------------------------------
class ExitCode(IntEnum):
    """D-15: 0 success / 1 user error / 2 measurement error / 3 auth error.

    Phase 4 adds ``AUTH_ERROR = 3`` (the "auth band"). Phase 6 budget verdicts
    (BUDG-01) will carve out 10+; the gap above 3 is intentional. Callers can
    ``case $? in 0) parse JSON ;; 1) fix invocation ;; 2) investigate
    environment ;; 3) re-auth ;; esac``.
    """

    SUCCESS = 0  # page measured (including non-2xx — D-13 partial)
    USER_ERROR = 1  # bad URL, bad flags, can't write output dir, Typer usage error
    MEASUREMENT_ERROR = 2  # all N samples failed, Chrome won't launch, etc.
    # Phase 4 (D-15 / RESEARCH A2): the "auth band". A distinct code (not a reuse
    # of 2) so `case $? in 3) re-auth ;; esac` scripting can tell a session/login
    # problem (login can't be confirmed, stale --auth-state, mid-crawl session
    # loss) apart from Chrome/LH breakage. The gap after 2 (constants.py:18-20
    # docstring) was reserved precisely for this; Phase 6 BUDG-01 still carves 10+.
    AUTH_ERROR = 3


# --- Phase 3 crawl defaults (D-08 / D-09 / D-10 / D-12) ---------------------
# The ONE editable place for every Phase-3 crawl tunable. `crawl/config.py`'s
# CrawlConfig reads each of these as a dataclass-field default; the discovery
# BFS, the measurement worker pool, the politeness gate, and the sitemap parser
# all import from HERE — never inline a page cap, depth, delay, or UA string.
# Phase 1 grep-asserts this discipline for TRACKING_PARAM_DENYLIST; Phase 2 for
# the timeout/samples/version/exit-codes/INP-label; Phase 3 extends it to these.

# D-09: bare `perfcrawl crawl <url>` measures at most this many in-scope pages.
DEFAULT_MAX_PAGES: int = 100  # D-09 conservative default; flag-overridable

# D-09: BFS depth bound (sitemap seeds = depth 0); finite tree height.
DEFAULT_MAX_DEPTH: int = 3  # D-09

# D-09: per-host concurrency == Chrome-pool size (one Chrome per worker, A6).
DEFAULT_CONCURRENCY: int = 2  # D-09 per-host concurrency = Chrome pool size

# D-09: minimum inter-request delay (seconds) between polite GETs to one host.
DEFAULT_MIN_DELAY_S: float = 0.5  # D-09 min inter-request delay

# D-08: per-base-path distinct query-variant cap — bounds facet/calendar traps.
DEFAULT_QUERY_VARIANT_CAP: int = 10  # D-08 per-base-path distinct query-variant cap

# D-10: crawl defaults to a single sample (measure keeps DEFAULT_SAMPLES_N = 3).
DEFAULT_CRAWL_SAMPLES_N: int = 1  # D-10

# D-12: exponential-backoff base (seconds) for 429/503 retries.
BACKOFF_BASE_S: float = 1.0  # D-12 exponential backoff base

# D-12: retries before a URL is tagged an error and abandoned (never hammer).
BACKOFF_MAX_RETRIES: int = 3  # D-12

# Pitfall 7: recursion bound on nested <sitemapindex> expansion (trap defense).
SITEMAP_MAX_RECURSION_DEPTH: int = 5  # Pitfall 7 sitemap-trap bound

# The User-Agent the discovery pass and robots-matching identify as.
CRAWLER_USER_AGENT: str = "PerfCrawl/0.1 (+https://github.com/jkl-101/PerfCrawl)"


# --- Phase 4 auth constants (D-01 / D-05 / D-07) ----------------------------
# The ONE editable place for every Phase-4 auth literal. `auth.py`, the CLI
# credential intake, the redaction sinks, and `CrawlConfig`'s deny field all
# import from HERE — never inline a deny token, an env-var name, the redaction
# placeholder, or the login-wait timeout. Phase 1 grep-asserts this discipline
# for TRACKING_PARAM_DENYLIST; Phase 4 extends it to these.

# D-05: always-on destructive-link denylist (substring, case-insensitive).
# Bias toward session-ending + state-mutating path tokens; `--deny`-extensible.
# NOTE: `admin` is deliberately broad (it denies `/admin-guide/` too); CONTEXT
# explicitly named it for the locked safety set, so it stays. There is no
# `--allow` un-deny in v1 (RESEARCH Open Q3) — acceptable for a safety denylist.
DEFAULT_DENY_PATTERNS: frozenset[str] = frozenset(
    {
        "logout",
        "log-out",
        "signout",
        "sign-out",  # session-ending
        "delete",
        "destroy",
        "remove",  # destructive
        "admin",  # admin actions (CONTEXT-named; broad on purpose)
        "archive",
        "trash",  # soft-destructive
        "unsubscribe",
        "deactivate",
        "disable",  # account-state mutations
    }
)

# D-07: credential intake is env-only (NEVER a Typer Option — argv is visible in
# `ps` / shell history). These name the env vars `auth.py`/CLI read creds from.
PERFCRAWL_USERNAME_ENV: str = "PERFCRAWL_USERNAME"
PERFCRAWL_PASSWORD_ENV: str = "PERFCRAWL_PASSWORD"

# D-07: the single redaction placeholder. The scrubber (auth.make_scrubber) and
# every sink that prints/persists auth-adjacent text substitute real secret
# values with this token. Defined once so the literal never drifts across sinks.
REDACTION_PLACEHOLDER: str = "***REDACTED***"

# D-01 (Claude's discretion): how long the driven form login waits for the
# post-submit load before evaluating the success heuristic. Playwright takes
# milliseconds; this is the explicit upper bound passed to wait_for_load_state.
LOGIN_WAIT_TIMEOUT_MS: int = 15_000


# --- Phase 5 AI analysis constants (D-07/D-08/D-03/D-11) --------------------
# The ONE editable place for every Phase-5 AI literal. `analysis.py` and the CLI
# `--ai`/`--ai-model` flags import from HERE — never inline a model id, pool cap,
# retry count, token bound, timeout, the waterfall-N, or the env-var name.
# Phase 1 grep-asserts this discipline for TRACKING_PARAM_DENYLIST; Phase 5
# extends it to these.

# D-07: bulk default model — Sonnet is the cost-appropriate synthesis tier and a
# T-05-cost guard (NOT Opus by default). Override per-run via `--ai-model`.
DEFAULT_AI_MODEL: str = "claude-sonnet-4-6"

# D-07/D-08: the escalation tier reached via `--ai-model claude-opus-4-8` for
# small high-value crawls. MUST be 4-8 — `claude-opus-4-7` is superseded and is
# the value the older CLAUDE.md/AI-SPEC notes carried; never copy it forward.
AI_OPUS_MODEL: str = "claude-opus-4-8"

# D-03: analyze-pool worker cap. Network-bound, so intentionally DECOUPLED from
# DEFAULT_CONCURRENCY (the Chrome pool, one heavyweight browser per worker).
AI_POOL_SIZE: int = 4

# D-11: SDK `max_retries` (its own default is 2). Set HERE so the retry budget is
# the one editable place; the SDK does the exponential backoff (no hand-rolled loop).
AI_MAX_RETRIES: int = 2

# AI-SPEC §4: per-call output bound (three short fields) + a T-05-cost runaway guard.
AI_MAX_TOKENS: int = 600

# AI-SPEC §4: per-client request timeout, below the SDK's 10-min default so a hung
# call degrades a page promptly instead of stalling the analyze pool.
AI_REQUEST_TIMEOUT_S: float = 60.0

# D-04: cap the per-page digest waterfall at the top-N slowest entries (sorted by
# timing desc, tie-break url asc) so the cached prompt stays small + deterministic.
AI_WATERFALL_TOP_N: int = 5

# D-10: the env var the API key is read from. Credential intake is env-only — the
# key is NEVER a Typer Option (argv is visible in `ps`/shell history).
ANTHROPIC_API_KEY_ENV: str = "ANTHROPIC_API_KEY"

# D-10 / ExitCode decision (RESEARCH A2): the missing-key fail-fast REUSES the
# existing `ExitCode.USER_ERROR = 1` (its docstring already covers "bad flags") —
# NO new ExitCode member is added. A missing key on `--ai` is a user-invocation
# error, not a measurement/auth failure; `case $? in 1) fix invocation ;; esac`.
