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
    """D-15: 0 success / 1 user error / 2 measurement error.

    Phase 6 budget verdicts (BUDG-01) will carve out 10+; the gap is intentional.
    Callers can ``case $? in 0) parse JSON ;; 1) fix invocation ;; 2) investigate
    environment ;; esac``.
    """

    SUCCESS = 0  # page measured (including non-2xx — D-13 partial)
    USER_ERROR = 1  # bad URL, bad flags, can't write output dir, Typer usage error
    MEASUREMENT_ERROR = 2  # all N samples failed, Chrome won't launch, etc.
