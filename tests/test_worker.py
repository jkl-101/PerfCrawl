"""Tests for the Python-side Lighthouse worker subprocess wrapper (Phase 2 D-02/D-14).

These pin the three-failure-modes-to-None contract (timeout / non-zero exit /
JSON decode), argv passthrough (mobile + desktop form-factor), shell-metacharacter
safety (T-02-03-SH), the timeout argument passthrough, and the preflight check
(Open Q5: actionable message when ``lighthouse-worker/node_modules`` is absent).

Mocks ``subprocess.run`` per 02-PATTERNS § "tests/test_worker.py" — the worker
itself is intentionally a thin wrapper, so the tests live entirely in Python land.
"""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_worker_returns_dict_on_success(monkeypatch):
    """D-02 happy path: a zero-exit subprocess with parseable stdout → dict."""
    from perfcrawl.lighthouse_worker import run_one_sample

    def _fake(argv, **kw):
        return SimpleNamespace(
            returncode=0,
            stdout='{"lhr":{"lighthouseVersion":"13.3.0"},"reportJson":"{}","reportHtml":"<html/>"}',
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", _fake)
    result = run_one_sample(port=9222, url="https://x.com", emulation="mobile", timeout_s=60)
    assert isinstance(result, dict)
    assert result["lhr"]["lighthouseVersion"] == "13.3.0"


def test_worker_returns_none_on_timeout(monkeypatch):
    """D-14 timeout branch: subprocess.TimeoutExpired → run_one_sample returns None."""
    from perfcrawl.lighthouse_worker import run_one_sample

    def _raise_timeout(*args, **kw):
        raise subprocess.TimeoutExpired(cmd="node", timeout=60)

    monkeypatch.setattr("subprocess.run", _raise_timeout)
    assert run_one_sample(port=9222, url="https://x.com", emulation="mobile", timeout_s=60) is None


def test_worker_returns_none_on_nonzero_exit(monkeypatch):
    """D-14 worker-failure branch: proc.returncode != 0 → None."""
    from perfcrawl.lighthouse_worker import run_one_sample

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stdout="", stderr="worker error: LH crashed"),
    )
    assert run_one_sample(port=9222, url="https://x.com", emulation="mobile", timeout_s=60) is None


def test_worker_returns_none_on_json_decode_error(monkeypatch):
    """A zero-exit subprocess with garbage stdout → None (not raise)."""
    from perfcrawl.lighthouse_worker import run_one_sample

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout="not json", stderr=""),
    )
    assert run_one_sample(port=9222, url="https://x.com", emulation="mobile", timeout_s=60) is None


def test_worker_returns_none_on_non_utf8_stdout(monkeypatch):
    """WR-06: non-UTF-8 stdout bytes → None (not uncaught UnicodeDecodeError).

    Previously ``subprocess.run(text=True, encoding="utf-8")`` raised
    ``UnicodeDecodeError`` if the worker emitted non-UTF-8 bytes (a future LH
    version leaking a binary trace, a UTF-16 BOM, etc.). The exception was
    NOT caught by the ``except subprocess.TimeoutExpired`` block, so it
    bubbled up and violated the D-15 three-exit-code contract.

    Fix: capture bytes and decode with ``errors="replace"``. The garbled
    decoded stdout fails ``json.loads`` and the existing
    ``json.JSONDecodeError`` handler converts to ``None`` cleanly.
    """
    from perfcrawl.lighthouse_worker import run_one_sample

    # Bytes that cannot be decoded as UTF-8 (lone 0x80 continuation byte).
    non_utf8 = b"\x80\x81\x82\x83 not json"

    def _fake(argv, **kw):
        # The fix uses ``capture_output=True`` WITHOUT ``text=True``, so the
        # returned proc has bytes for stdout/stderr.
        return SimpleNamespace(returncode=0, stdout=non_utf8, stderr=b"")

    monkeypatch.setattr("subprocess.run", _fake)
    # Must NOT raise UnicodeDecodeError; returns None (JSON-decode-failure path).
    assert run_one_sample(
        port=9222, url="https://x.com", emulation="mobile", timeout_s=60
    ) is None


def test_worker_decodes_stderr_defensively_on_nonzero_exit(monkeypatch, capsys):
    """WR-06: worker-error stderr bytes are decoded with errors='replace' for logging."""
    from perfcrawl.lighthouse_worker import run_one_sample

    # Non-UTF-8 bytes in stderr — must not raise on the sys.stderr.write call.
    def _fake(argv, **kw):
        return SimpleNamespace(
            returncode=1, stdout=b"", stderr=b"worker error: \x80 partial msg"
        )

    monkeypatch.setattr("subprocess.run", _fake)
    assert run_one_sample(
        port=9222, url="https://x.com", emulation="mobile", timeout_s=60
    ) is None
    err = capsys.readouterr().err
    # The replacement char (U+FFFD) appears where 0x80 was.
    assert "worker error" in err


def _capturing_subprocess(captured: dict):
    """Helper: replace subprocess.run with a recorder that returns a stub success."""

    def _fake(argv, **kw):
        captured["argv"] = argv
        captured["kwargs"] = kw
        return SimpleNamespace(
            returncode=0,
            stdout='{"lhr":{"lighthouseVersion":"13.3.0"},"reportJson":"","reportHtml":""}',
            stderr="",
        )

    return _fake


def test_worker_argv_passthrough_mobile(monkeypatch):
    """RUN-01: --form-factor=mobile present in subprocess argv."""
    from perfcrawl.lighthouse_worker import run_one_sample

    captured: dict = {}
    monkeypatch.setattr("subprocess.run", _capturing_subprocess(captured))
    run_one_sample(port=9222, url="https://x.com/", emulation="mobile", timeout_s=60)
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "node"
    assert "--port=9222" in argv
    assert "--url=https://x.com/" in argv
    assert "--form-factor=mobile" in argv
    # The Node script is the second positional argv element by the documented shape.
    assert any("run.mjs" in a for a in argv)


def test_worker_argv_passthrough_desktop(monkeypatch):
    """RUN-01: --form-factor=desktop forwarded correctly."""
    from perfcrawl.lighthouse_worker import run_one_sample

    captured: dict = {}
    monkeypatch.setattr("subprocess.run", _capturing_subprocess(captured))
    run_one_sample(port=9222, url="https://x.com/", emulation="desktop", timeout_s=60)
    assert "--form-factor=desktop" in captured["argv"]


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/;rm -rf /",
        "https://x.com/&whoami",
        "https://x.com/$(echo pwned)",
        "https://x.com/`whoami`",
        "https://x.com/|cat",
        "https://x.com/>out",
    ],
)
def test_worker_argv_is_list_no_shell_expansion(monkeypatch, url):
    """T-02-03-SH: shell metacharacters in URL cannot trigger shell expansion."""
    from perfcrawl.lighthouse_worker import run_one_sample

    captured: dict = {}
    monkeypatch.setattr("subprocess.run", _capturing_subprocess(captured))
    run_one_sample(port=9222, url=url, emulation="mobile", timeout_s=60)
    assert isinstance(captured["argv"], list)
    # shell=True must NOT be passed; if "shell" key is present it must be False.
    assert "shell" not in captured["kwargs"] or captured["kwargs"]["shell"] is False
    # The URL appears as a single argv element (never f-string-interpolated into
    # one shell command string), so the only argv element that mentions it must
    # contain it verbatim, not as a substring of a longer shell-string.
    assert any(url in arg for arg in captured["argv"])


def test_worker_uses_timeout_from_arg(monkeypatch):
    """D-14: subprocess.run timeout= matches the timeout_s argument."""
    from perfcrawl.lighthouse_worker import run_one_sample

    captured: dict = {}
    monkeypatch.setattr("subprocess.run", _capturing_subprocess(captured))
    run_one_sample(port=9222, url="https://x.com/", emulation="mobile", timeout_s=42)
    assert captured["kwargs"].get("timeout") == 42


def test_worker_preflight_raises_when_node_modules_missing(tmp_path):
    """Open Q5: missing lighthouse-worker/node_modules → MeasurementError with 'npm ci' guidance."""
    from perfcrawl.lighthouse_worker import MeasurementError, preflight

    # tmp_path is empty; node_modules/lighthouse/package.json does not exist.
    fake_worker_dir = tmp_path / "lighthouse-worker"
    fake_worker_dir.mkdir()
    with pytest.raises(MeasurementError) as exc_info:
        preflight(worker_dir=fake_worker_dir)
    assert "npm ci" in str(exc_info.value)


def test_worker_preflight_succeeds_when_node_modules_present(tmp_path):
    """Preflight is a no-op when node_modules/lighthouse/package.json exists."""
    from perfcrawl.lighthouse_worker import preflight

    fake_worker_dir = tmp_path / "lighthouse-worker"
    pkg_dir = fake_worker_dir / "node_modules" / "lighthouse"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text('{"name":"lighthouse","version":"13.3.0"}')
    # Should not raise.
    preflight(worker_dir=fake_worker_dir)


def test_worker_preflight_raises_when_node_binary_missing(tmp_path, monkeypatch):
    """WR-01: missing ``node`` on PATH → MeasurementError with actionable guidance.

    Previously ``preflight()`` only verified the lighthouse-worker
    ``node_modules`` install — a missing ``node`` binary itself surfaced as
    an uncaught ``FileNotFoundError`` from ``subprocess.run(["node", ...])``
    later in ``run_one_sample``, which the orchestrator's
    ``except subprocess.TimeoutExpired`` did not catch. The traceback bubbled
    up and violated the D-15 three-exit-code contract.

    Preflight should detect this fast and raise ``MeasurementError`` with a
    "install Node >=22.19" hint so the CLI maps to ``ExitCode.MEASUREMENT_ERROR``
    cleanly (CLAUDE.md § Installation).
    """
    from perfcrawl.lighthouse_worker import MeasurementError, preflight

    # Make a valid worker dir so the node_modules check passes (we want to
    # isolate the node-binary missing path).
    fake_worker_dir = tmp_path / "lighthouse-worker"
    pkg_dir = fake_worker_dir / "node_modules" / "lighthouse"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "package.json").write_text('{"name":"lighthouse","version":"13.3.0"}')

    # Pretend ``node`` is not on PATH.
    monkeypatch.setattr("shutil.which", lambda binary: None if binary == "node" else "/x")
    with pytest.raises(MeasurementError) as exc_info:
        preflight(worker_dir=fake_worker_dir)
    msg = str(exc_info.value)
    assert "node" in msg.lower()
    # Hint mentions the install requirement (CLAUDE.md § Installation cites Node >=22.19).
    assert "22" in msg or "install" in msg.lower()


def test_worker_script_path_resolves_to_repo_root_sibling():
    """WORKER_SCRIPT points at <repo>/lighthouse-worker/run.mjs regardless of cwd."""
    from perfcrawl.lighthouse_worker import WORKER_SCRIPT

    assert isinstance(WORKER_SCRIPT, Path)
    assert WORKER_SCRIPT.name == "run.mjs"
    assert WORKER_SCRIPT.parent.name == "lighthouse-worker"


def test_worker_drains_large_stdout_payload(tmp_path: Path):
    """CR-01 regression: a >1MB stdout payload survives the Node->Python pipe.

    Verifier finding (02-VERIFICATION.md gap #1):

      "Add a regression test that pumps a >1 MB synthetic payload through the
       worker subprocess (real subprocess, not mocked) and asserts Python
       parses the full JSON."

    This test does NOT invoke the real lighthouse-worker/run.mjs — that would
    require Node + chrome-launcher + a target URL (the e2e-suite's territory).
    Instead it writes a tiny shim script using the SAME drain-before-exit
    pattern Task 1 introduces (process.stdout.write(payload, (err) => ...))
    and spawns it via subprocess.run. If this test goes red, the worker's
    stdout handoff pattern is broken at the language/runtime level, not just
    at the Lighthouse-specific level.

    The shim emits a 1.5MB JSON envelope (well above the ~64KB Linux pipe
    buffer and the ~16KB macOS pipe buffer) and asserts Python receives
    every byte intact.

    Skipped (not failed) when ``node`` is not on PATH so CI without Node
    passes cleanly.

    Negative control intentionally omitted — Task 1's grep-guard for
    ``process.exit(0)`` position is the static analog.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node binary not on PATH; CR-01 regression requires Node runtime")

    big_chunk = "x" * 1_500_000  # 1.5 MB — comfortably above all pipe buffers
    shim = tmp_path / "shim.mjs"
    shim.write_text(
        "const payload = JSON.stringify({"
        "lhr:{lighthouseVersion:'13.3.0'},"
        f"reportJson:'{big_chunk}',"
        f"reportHtml:'{big_chunk}'"
        "});\n"
        # Mirror Task 1's drain-before-exit pattern verbatim.
        "process.stdout.write(payload, (err) => {\n"
        "  if (err) { process.stderr.write(String(err)); process.exit(1); }\n"
        "  process.exit(0);\n"
        "});\n"
    )

    proc = subprocess.run(
        [node, str(shim)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert proc.returncode == 0, f"shim exit {proc.returncode}; stderr={proc.stderr}"
    # Sanity: stdout is bigger than any pipe buffer we'd ever encounter.
    assert len(proc.stdout) > 1_000_000, (
        f"stdout truncated at {len(proc.stdout)} bytes — drain failed"
    )
    # The whole envelope round-trips through json.loads (the same call
    # lighthouse_worker.py:91 makes against the real worker).
    parsed = json.loads(proc.stdout)
    assert parsed["lhr"]["lighthouseVersion"] == "13.3.0"
    assert len(parsed["reportJson"]) == 1_500_000
    assert len(parsed["reportHtml"]) == 1_500_000
    assert all(c == "x" for c in parsed["reportJson"][:1000])
