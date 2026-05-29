"""Python-side single-sample wrapper around the Node Lighthouse worker (Phase 2 D-02/D-14).

Translates three failure modes — ``subprocess.TimeoutExpired``,
``proc.returncode != 0``, ``json.JSONDecodeError`` — into a single
``Optional[dict]`` return so the orchestrator's per-sample retry/drop loop
(D-14/D-16) has a clean boolean signal. Mirrors ``canonical.py``'s defensive
try/except + deterministic-fallback shape from Phase 1 LEARNINGS: never raise
on external-process flake; the caller decides what "drop on failure" means.

Security (RESEARCH § Security Domain):
- ``subprocess.run`` argv is ALWAYS a ``list[str]`` and the shell-invocation kwarg
  is NEVER passed (threat T-02-03-SH). The URL appears as one argv element and
  cannot be interpolated into a shell command.
- The Node worker has its own 55s watchdog (Assumption A5, from 02-01); this
  layer's ``timeout=`` is the belt-and-suspenders backstop. The worker fires first.
- The PyPI ``lighthouse`` decoy is never imported — that is the abandoned 2016
  service-discovery package, not Google Lighthouse (RESEARCH Pitfall 8 +
  CLAUDE.md "What NOT to Use"). The ONLY lighthouse surface in the repo is the
  Node ``lighthouse-worker/`` sibling project (D-04).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

# The Node worker lives in the repo-root sibling dir per D-04. Compute the path
# relative to THIS file so cwd-changes (tests via tmp_path, alternate working
# dirs, etc.) don't break the lookup.
WORKER_SCRIPT: Path = Path(__file__).resolve().parents[2] / "lighthouse-worker" / "run.mjs"


class MeasurementError(Exception):
    """Couldn't measure — CLI maps to ExitCode.MEASUREMENT_ERROR (D-15).

    Raised here only by ``preflight()`` when the Node worker is not installed
    (Open Q5 — actionable "npm ci" message). The orchestrator (02-03 Task 2)
    also raises this exception for measurement-side failures (all samples
    failed, Chrome won't launch, DevToolsActivePort never appeared).
    """


def run_one_sample(
    *, port: int, url: str, emulation: str, timeout_s: int
) -> dict | None:
    """Invoke the Node Lighthouse worker once; return parsed JSON or ``None`` on failure.

    Three failure modes all collapse to ``None`` (the orchestrator's D-14
    retry-or-drop loop is cleaner if this layer presents a single boolean signal):

      - ``subprocess.TimeoutExpired`` — Node hung past ``timeout_s``.
      - ``proc.returncode != 0`` — Lighthouse raised; ``proc.stderr`` is logged
        to ``sys.stderr`` so the CLI's stderr passthrough surfaces it (D-15).
      - ``json.JSONDecodeError`` — worker stdout was not parseable JSON.

    The returned dict (on success) is the worker's full envelope:
    ``{"lhr": {...}, "reportJson": "...", "reportHtml": "..."}`` per 02-01 Task 2
    (Pitfall 6 — the worker emits both stringified-Lighthouse-JSON and the
    HTML report so OUT-03 can land them on disk via the orchestrator's
    side-channel).

    Security: argv is ALWAYS a ``list[str]``; the shell-invocation kwarg is
    NEVER passed. Threat T-02-03-SH: a URL like ``"https://x.com/;rm -rf /"``
    passes through as a single argv element and cannot trigger shell expansion.
    """
    argv: list[str] = [
        "node",
        str(WORKER_SCRIPT),
        f"--port={port}",
        f"--url={url}",
        f"--form-factor={emulation}",
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        # D-14 timeout branch: caller will retry once or drop the sample.
        return None
    if proc.returncode != 0:
        # D-15: surface worker stderr so the CLI's stderr passthrough renders
        # an actionable message. The CLI (02-04) maps the all-samples-fail
        # case to MEASUREMENT_ERROR; this line is the breadcrumb the user sees.
        sys.stderr.write(f"worker error (exit {proc.returncode}): {proc.stderr}\n")
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        # Worker exited 0 but emitted garbage — treat as drop, same as a crash.
        return None


def preflight(worker_dir: Path | None = None) -> None:
    """Verify the Node runtime + Lighthouse worker are installed (Open Q5 / WR-01).

    Called once per ``measure_url(...)`` invocation by the orchestrator
    (02-03 Task 2) BEFORE Chrome is launched, so a missing install fails fast
    with an actionable message rather than after a 5s DevToolsActivePort timeout.

    Two checks (both raise ``MeasurementError`` — the CLI maps to
    ``ExitCode.MEASUREMENT_ERROR`` per D-15):

    1. WR-01: ``shutil.which("node")`` confirms the Node binary itself is
       resolvable on PATH. Without this check a missing ``node`` produces a
       ``FileNotFoundError`` from ``subprocess.run(["node", ...])`` inside
       ``run_one_sample`` that the ``except subprocess.TimeoutExpired`` block
       does NOT catch, leaving an uncaught traceback that violates the D-15
       three-exit-code contract.
    2. ``{worker_dir}/node_modules/lighthouse/package.json`` confirms the
       worker's npm install ran.

    Raises:
        MeasurementError: with an actionable message naming the missing
            dependency and the documented install command (CLAUDE.md §
            Installation).
    """
    # WR-01: check the node binary BEFORE the node_modules marker so a missing
    # runtime is named explicitly rather than being shadowed by the worker
    # install message. CLAUDE.md § Installation requires Node >=22.19.
    if shutil.which("node") is None:
        raise MeasurementError(
            "node binary not found on PATH — install Node >=22.19 "
            "(see CLAUDE.md § 'Installation')."
        )
    if worker_dir is None:
        worker_dir = WORKER_SCRIPT.parent
    marker = worker_dir / "node_modules" / "lighthouse" / "package.json"
    if not marker.exists():
        raise MeasurementError(
            "lighthouse-worker not installed — "
            "run `cd lighthouse-worker && npm ci` before invoking measure "
            "(see CLAUDE.md § 'Installation')."
        )
