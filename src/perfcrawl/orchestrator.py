"""Per-sample Playwright + Lighthouse orchestration (Phase 2 D-01..D-04, D-14..D-16).

Python owns Chrome's lifecycle so Phase 4 can layer ``storage_state`` on this
same seam without architectural change. Per-sample isolation comes from cycling
``BrowserContext`` (NOT killing Chrome) — the cold-cache invariant survives
any Lighthouse upgrade per D-03.

The orchestrator launches Chromium via ``subprocess.Popen`` (Pitfall 5 — using
the persistent-context launcher would force the ``.browser is None`` corner;
``Popen`` + ``connect_over_cdp`` gives a real Browser object with ``.new_context()``
available), then waits for Chrome to write ``DevToolsActivePort`` into the
per-run user_data_dir (Pitfall 1 — never pre-pick a port via socket bind:
TOCTOU race vs the kernel re-issuing the port).

Security (RESEARCH § Security Domain):

- ``subprocess`` argv lists are ALWAYS Python lists, never shell-strings (T-02-03-SH).
- Chrome lifecycle is wrapped in try/finally so a crash never leaks a zombie
  Chromium (T-02-03-Z); the per-run ``tempfile.mkdtemp`` is ``shutil.rmtree``'d
  in the same finally.
- Per-run tempdir + ``--remote-debugging-port=0`` (kernel-picked) means two
  concurrent ``perfcrawl measure`` runs cannot collide on user-data-dir or
  port by construction (T-02-03-CONCURRENT / Assumption A6).
- All-samples-fail raises ``MeasurementError`` (D-14) which the CLI maps to
  ``ExitCode.MEASUREMENT_ERROR`` (D-15) — silent zero-data runs are forbidden
  (T-02-03-PARTIAL).
"""

import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

from perfcrawl.aggregator import aggregate_page_samples
from perfcrawl.constants import (
    DEVTOOLS_PORT_FILE_TIMEOUT_S,
    DEVTOOLS_PORT_POLL_INTERVAL_S,
    PER_SAMPLE_TIMEOUT_S,
)
from perfcrawl.lighthouse_worker import MeasurementError, preflight, run_one_sample
from perfcrawl.models import PageResult, RunRecord
from perfcrawl.normalizer import normalize_lh

# Re-export MeasurementError so 02-04 CLI can `from perfcrawl.orchestrator import …`
# in a single statement. The exception is defined in lighthouse_worker.py to avoid
# a circular import (worker.preflight needs to raise it; orchestrator imports both).
__all__ = ["measure_url", "MeasurementError", "UserError"]


class UserError(Exception):
    """Bad input — CLI maps to ExitCode.USER_ERROR (D-15).

    Raised for empty/whitespace URL, ``samples < 1``, ``emulation`` not in
    ``{"mobile", "desktop"}``. The CLI's two-arm catch (UserError ⇒ 1,
    MeasurementError ⇒ 2) covers every failure path exhaustively.
    """


def _launch_chrome_with_cdp_port() -> tuple[subprocess.Popen, int, Path]:
    """Launch Chromium with ``--remote-debugging-port=0`` and read the resolved port.

    Per Pitfall 1: Chrome writes the kernel-picked port to
    ``<user_data_dir>/DevToolsActivePort`` (line 1 = port). Polling that file
    is the documented contract — ``socket.bind(("", 0))`` is a TOCTOU race
    because the kernel can re-issue the port before Chrome binds it.

    Per Pitfall 5: launches via ``subprocess.Popen`` + ``connect_over_cdp``
    rather than the persistent-context launcher so the orchestrator can call
    ``browser.new_context()`` for RUN-03 cold-cache cycling (a persistent-context
    Browser has no ``.new_context()`` method).

    Returns ``(chrome_proc, port, user_data_dir)``. The caller MUST wrap the use
    of the returned values in try/finally that kills the proc and rmtree's the
    user_data_dir (T-02-03-Z — no zombie Chrome, no leaked tempdirs).
    """
    user_data_dir = Path(tempfile.mkdtemp(prefix="perfcrawl-chrome-"))

    # WR-11: structural sibling of CR-03. The original launcher leaked
    # ``user_data_dir`` on three failure paths: (1) DevToolsActivePort timeout
    # (CR-03 — fixed), (2) ``sync_playwright()`` raising (Playwright not
    # installed, chromium binary not downloaded), and (3) ``subprocess.Popen``
    # raising (chrome binary missing / not executable). Wrap the
    # resolve-then-launch section in try/except so a raise from either
    # uncovered path rmtree's the dir BEFORE re-raising. The caller's
    # ``chrome, port, user_data_dir = ...`` assignment never completes on a
    # raise, so the caller cannot clean up — the launcher is self-contained
    # on its failure path (CR-03 invariant extended).
    try:
        # Resolve the Playwright-bundled Chromium executable. A brief
        # sync_playwright() context gives us p.chromium.executable_path; we
        # then close it and launch via Popen (Pitfall 5).
        with sync_playwright() as p:
            chrome_path = p.chromium.executable_path

        argv = [
            chrome_path,
            f"--user-data-dir={user_data_dir}",
            "--remote-debugging-port=0",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # Cleanup-on-raise: same disk-leak class as CR-03, same rmtree pattern.
        shutil.rmtree(user_data_dir, ignore_errors=True)
        raise

    port_file = user_data_dir / "DevToolsActivePort"
    # WR-05: monotonic-deadline loop. Two improvements over the prior
    # ``for _ in range(int(timeout/interval)):`` shape:
    #   1. Check existence BEFORE sleeping so a file already present at t=0
    #      returns without paying one ``DEVTOOLS_PORT_POLL_INTERVAL_S`` wait
    #      — Chrome can write the file in milliseconds on a fast machine.
    #   2. Drop the ``int(...)`` truncation that turned a 5.0s budget at a
    #      0.15s interval (33.33 attempts) into 33 — fewer than the timeout
    #      implies. The deadline is monotonic-clock based and honors the
    #      full budget exactly.
    deadline = time.monotonic() + DEVTOOLS_PORT_FILE_TIMEOUT_S
    while True:
        if port_file.exists():
            text = port_file.read_text().strip()
            if text:
                # Line 1 is the port; the rest is the /devtools/browser/<uuid> path.
                first_line = text.splitlines()[0]
                try:
                    port = int(first_line)
                    return proc, port, user_data_dir
                except ValueError:
                    # Partial / mid-write file content — keep polling.
                    pass
        if time.monotonic() >= deadline:
            break
        time.sleep(DEVTOOLS_PORT_POLL_INTERVAL_S)

    # Timeout: never wrote the file. CR-02 + CR-03: kill Chrome, wait to reap
    # so it never becomes a <defunct> zombie, then remove the user_data_dir
    # tempdir BEFORE raising. The caller's `chrome, port, user_data_dir = ...`
    # assignment never completes when we raise, so the caller's finally cannot
    # clean up — this launcher must be self-contained on its failure path.
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Already SIGKILL'd; the kernel will reap eventually.
        pass
    shutil.rmtree(user_data_dir, ignore_errors=True)
    raise MeasurementError(
        f"Chrome did not write DevToolsActivePort within {DEVTOOLS_PORT_FILE_TIMEOUT_S}s"
    )


def measure_url(
    *,
    url: str,
    samples: int = 1,
    emulation: str = "mobile",
) -> tuple[RunRecord, dict[str, tuple[str, str]]]:
    """Measure one URL end-to-end (D-05): Chrome → CDP → per-sample LH → aggregate.

    Returns ``(run_record, raw_artifacts)`` where ``run_record`` wraps the
    aggregated single-page result and ``raw_artifacts`` is
    ``{url_key: (reportJson, reportHtml)}`` populated from the FIRST successful
    sample's worker envelope. The CLI (02-04) destructures and forwards
    ``raw_artifacts`` to ``output.write_outputs`` for OUT-03 (raw LH artifacts on disk).

    Failure paths:

      - Bad input (empty URL / samples < 1 / unknown emulation) → ``UserError``
        before any subprocess is launched. CLI maps to ``ExitCode.USER_ERROR`` (D-15).
      - Worker not installed → ``MeasurementError`` from ``preflight()``.
      - Chrome won't launch / DevToolsActivePort never appears →
        ``MeasurementError`` from ``_launch_chrome_with_cdp_port()``.
      - All N samples fail (each: initial + retry both returned None) →
        ``MeasurementError(f"all {samples} samples failed")``. CLI maps to
        ``ExitCode.MEASUREMENT_ERROR`` (D-15).
    """
    # --- Input validation (D-15 UserError arm) ---
    if not (url or "").strip():
        raise UserError("URL is empty")
    if samples < 1:
        raise UserError(f"--samples must be >= 1; got {samples}")
    if emulation not in {"mobile", "desktop"}:
        raise UserError(
            f"--emulation must be 'mobile' or 'desktop'; got {emulation!r}"
        )

    # --- Worker preflight (fails fast before Chrome is launched) ---
    preflight()

    # --- Chrome lifecycle (T-02-03-Z: kill + rmtree in finally, always) ---
    chrome, port, user_data_dir = _launch_chrome_with_cdp_port()
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{port}")

            per_sample_results: list[PageResult] = []
            lhr_for_metadata: dict | None = None
            first_raw_report: tuple[str, str] | None = None

            for _sample_idx in range(samples):
                # D-03 cold cache: a fresh BrowserContext per sample. The Chrome
                # process stays alive across samples; isolation comes from
                # cycling the context, not from killing the browser.
                context = browser.new_context()
                try:
                    # D-14: initial attempt, then one retry on failure.
                    lh = run_one_sample(
                        port=port,
                        url=url,
                        emulation=emulation,
                        timeout_s=PER_SAMPLE_TIMEOUT_S,
                    )
                    if lh is None:
                        lh = run_one_sample(
                            port=port,
                            url=url,
                            emulation=emulation,
                            timeout_s=PER_SAMPLE_TIMEOUT_S,
                        )
                    if lh is not None:
                        lhr = lh.get("lhr", {})
                        page_result = normalize_lh(lhr, url_as_measured=url)
                        per_sample_results.append(page_result)
                        if lhr_for_metadata is None:
                            lhr_for_metadata = lhr
                        if first_raw_report is None:
                            # OUT-03 side-channel: stash the FIRST successful
                            # sample's reportJson + reportHtml strings for the
                            # CLI's output.write_outputs to land on disk.
                            # WR-04: use the empty string only as a "key absent"
                            # signal; output.py treats falsy strings as "no
                            # artifact" so a malformed envelope produces a
                            # missing file rather than a zero-byte one.
                            first_raw_report = (
                                lh.get("reportJson") or "",
                                lh.get("reportHtml") or "",
                            )
                finally:
                    context.close()

            if not per_sample_results:
                # D-14 + T-02-03-PARTIAL: never produce a silently-empty PageResult.
                raise MeasurementError(f"all {samples} samples failed")

            # D-16: aggregate the surviving samples into one PageResult.
            aggregated = aggregate_page_samples(per_sample_results)

            # D-04 + RUN-02: stamp run-level metadata from the first successful
            # sample's lhr. Defensive .get() chains so a missing key yields None
            # rather than raising (the slot is nullable on RunRecord).
            assert lhr_for_metadata is not None  # guaranteed by per_sample_results check
            chrome_version = (
                lhr_for_metadata.get("environment", {}).get("hostUserAgent")
            )
            lighthouse_version = lhr_for_metadata.get("lighthouseVersion")
            throttling = lhr_for_metadata.get("configSettings", {}).get("throttling")

            run_record = RunRecord(
                id=uuid4(),
                started_at=datetime.now(UTC),
                target=url,
                chrome_version=chrome_version,
                lighthouse_version=lighthouse_version,
                throttling=throttling,
                emulation=emulation,
                pages=[aggregated],
            )

            raw_artifacts: dict[str, tuple[str, str]] = (
                {aggregated.url_key: first_raw_report}
                if first_raw_report is not None
                else {}
            )
            return run_record, raw_artifacts
    finally:
        # T-02-03-Z: kill Chrome + reap the exit status + rmtree the
        # user_data_dir on BOTH success and failure paths. CR-02: without
        # the wait() the killed Chromium stays a <defunct> zombie in the
        # process table until the Python interpreter exits.
        # ignore_errors=True on rmtree so a partial-cleanup failure doesn't
        # mask the original exception.
        try:
            chrome.kill()
            try:
                chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Already SIGKILL'd; the kernel will reap eventually.
                pass
        except Exception:
            pass
        shutil.rmtree(user_data_dir, ignore_errors=True)
