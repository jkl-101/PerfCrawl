"""Tests for the Python-side Lighthouse worker subprocess wrapper (Phase 2 D-02/D-14).

These pin the three-failure-modes-to-None contract (timeout / non-zero exit /
JSON decode), argv passthrough (mobile + desktop form-factor), shell-metacharacter
safety (T-02-03-SH), the timeout argument passthrough, and the preflight check
(Open Q5: actionable message when ``lighthouse-worker/node_modules`` is absent).

Mocks ``subprocess.run`` per 02-PATTERNS § "tests/test_worker.py" — the worker
itself is intentionally a thin wrapper, so the tests live entirely in Python land.
"""

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


def test_worker_script_path_resolves_to_repo_root_sibling():
    """WORKER_SCRIPT points at <repo>/lighthouse-worker/run.mjs regardless of cwd."""
    from perfcrawl.lighthouse_worker import WORKER_SCRIPT

    assert isinstance(WORKER_SCRIPT, Path)
    assert WORKER_SCRIPT.name == "run.mjs"
    assert WORKER_SCRIPT.parent.name == "lighthouse-worker"
