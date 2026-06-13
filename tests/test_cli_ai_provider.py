"""--ai-provider selection + resolved-key fail-fast (Phase 05.2 Plan 04).

These offline integration tests pin the user-facing slice of the provider-agnostic
adapter:

  - D-01 resolution: an explicit ``--ai-provider`` wins; omitting it auto-detects
    from the env, with Anthropic winning the tie-break when BOTH keys are present
    (back-compat).
  - D-02 fail-fast: ``--ai-provider X`` with X's key absent (or ``--ai`` with NO
    provider key at all) exits ``ExitCode.USER_ERROR`` at t=0 — BEFORE any
    measurement — and the AI post-pass is NEVER reached.

No real API call fires: resolution-path tests fake ``build_provider`` (capturing
the RESOLVED provider name and returning a no-op provider so the real
``resolve_provider`` + ``analyze_run`` seams still execute offline), and fail-fast
tests fake ``_run_ai_post_pass`` to a recorder so its non-invocation is provable.
Both ``measure`` and ``crawl`` are covered for the openai-selected + fail-fast cases.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from perfcrawl.cli import app
from perfcrawl.constants import ExitCode
from perfcrawl.models import MetricSample, PageResult, RunRecord

runner = CliRunner()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_stub_run() -> RunRecord:
    """A minimal valid single-page RunRecord (a non-error page → analyze_run calls
    the provider) — the measure_url contract."""
    return RunRecord(
        id=UUID("3f1c2b9a-0000-4000-8000-0000000000d4"),
        started_at=datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC),
        target="https://example.com/",
        chrome_version="137.0.7151.40",
        lighthouse_version="13.3.0",
        emulation="mobile",
        pages=[
            PageResult(
                url="https://example.com/",
                url_key="https://example.com/",
                perf_score=92.0,
                lcp_ms=MetricSample(median=1234.0, samples=[1234.0]),
                status_code=200,
            )
        ],
    )


def _make_stub_artifacts(run: RunRecord) -> dict[str, tuple[str, str]]:
    return {run.pages[0].url_key: ('{"lhr":{}}', "<html/>")}


class _NoopProvider:
    """A Provider whose structured-parse always degrades to None (no network)."""

    def parse_structured(self, **_kwargs):
        return None


def _capture_build_provider(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake ``cli.build_provider`` to record the RESOLVED provider name + key.

    Leaves the real ``resolve_provider`` (keygate + post-pass) and ``analyze_run``
    running offline — only the concrete SDK-client construction is replaced.
    """
    captured: dict = {}

    def fake_build_provider(name, key, **kw):
        captured["name"] = name
        captured["key"] = key
        captured["base_url"] = kw.get("base_url")
        return _NoopProvider()

    monkeypatch.setattr("perfcrawl.cli.build_provider", fake_build_provider)
    return captured


def _record_post_pass(monkeypatch: pytest.MonkeyPatch) -> list:
    """Fake ``cli._run_ai_post_pass`` to a recorder so non-invocation is provable."""
    calls: list = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return {"analyzed": 0, "degraded": 0, "insufficient": 0, "violations": {}}

    monkeypatch.setattr("perfcrawl.cli._run_ai_post_pass", fake)
    return calls


def _patch_measure_url(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch the measure() seam (cli.measure_url); record calls."""
    calls: list = []

    def fake(**kwargs):
        calls.append(kwargs)
        run = _make_stub_run()
        return run, _make_stub_artifacts(run)

    monkeypatch.setattr("perfcrawl.cli.measure_url", fake)
    return calls


def _patch_crawl_measure(monkeypatch: pytest.MonkeyPatch) -> list:
    """Patch the crawl measurement seam (crawl.measure_pass.measure_url)."""
    from perfcrawl.canonical import canonical_key

    calls: list = []

    def fake(*, url, samples=1, emulation="mobile", auth_state=None):
        calls.append(url)
        key = canonical_key(url)
        page = PageResult(
            url=url,
            url_key=key,
            perf_score=90.0,
            lcp_ms=MetricSample(median=1200.0, samples=[1200.0]),
            status_code=200,
        )
        run = RunRecord(
            id=UUID("3f1c2b9a-0000-4000-8000-0000000000d5"),
            started_at=datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC),
            target=url,
            chrome_version="137.0.7151.40",
            lighthouse_version="13.3.0",
            emulation="mobile",
            pages=[page],
        )
        return run, {key: ('{"lhr":{}}', "<html/>")}

    monkeypatch.setattr("perfcrawl.crawl.measure_pass.measure_url", fake)
    return calls


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# measure() — provider selection
# --------------------------------------------------------------------------- #


def test_measure_ai_provider_openai_resolves_openai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--ai --ai-provider openai`` with only OPENAI_API_KEY resolves openai."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-TESTKEY")
    _patch_measure_url(monkeypatch)
    captured = _capture_build_provider(monkeypatch)

    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--ai",
            "--ai-provider",
            "openai",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert captured.get("name") == "openai"
    assert captured.get("key") == "sk-openai-TESTKEY"


def test_measure_ai_both_keys_resolves_anthropic_tiebreak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D-01: ``--ai`` with BOTH keys present auto-detects anthropic (back-compat tie-break)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-TESTKEY")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-TESTKEY")
    _patch_measure_url(monkeypatch)
    captured = _capture_build_provider(monkeypatch)

    result = runner.invoke(
        app, ["measure", "https://example.com", "--ai", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert captured.get("name") == "anthropic"


def test_measure_ai_provider_openai_missing_key_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D-02: ``--ai-provider openai`` with NO OPENAI_API_KEY exits USER_ERROR at t=0.

    The post-pass must NOT be reached and measurement must NOT run (fail-fast before
    any Chrome cost). The error message names the env-only-never-a-flag rule.
    """
    _clear_keys(monkeypatch)
    measure_calls = _patch_measure_url(monkeypatch)
    post_pass = _record_post_pass(monkeypatch)

    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--ai",
            "--ai-provider",
            "openai",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == int(ExitCode.USER_ERROR), result.stdout + result.stderr
    assert post_pass == [], "AI post-pass must never run on the D-02 fail-fast"
    assert measure_calls == [], "measurement must never run on the D-02 fail-fast"
    # Rich soft-wraps the stderr line, so collapse whitespace before substring checks.
    msg = " ".join(result.stderr.split())
    assert "OPENAI_API_KEY" in msg
    assert "never a flag" in msg


def test_measure_ai_no_keys_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D-02: ``--ai`` with NO keys at all (no flag) exits USER_ERROR at t=0."""
    _clear_keys(monkeypatch)
    measure_calls = _patch_measure_url(monkeypatch)
    post_pass = _record_post_pass(monkeypatch)

    result = runner.invoke(
        app, ["measure", "https://example.com", "--ai", "--output-dir", str(tmp_path)]
    )
    assert result.exit_code == int(ExitCode.USER_ERROR), result.stdout + result.stderr
    assert post_pass == []
    assert measure_calls == []
    msg = " ".join(result.stderr.split())
    assert "ANTHROPIC_API_KEY" in msg
    assert "OPENAI_API_KEY" in msg
    assert "never a flag" in msg


# --------------------------------------------------------------------------- #
# crawl() — provider selection (mirrors measure for the openai + fail-fast cases)
# --------------------------------------------------------------------------- #


def test_crawl_ai_provider_openai_resolves_openai(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local_server: str
) -> None:
    """``crawl --ai --ai-provider openai`` with only OPENAI_API_KEY resolves openai."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-TESTKEY")
    _patch_crawl_measure(monkeypatch)
    captured = _capture_build_provider(monkeypatch)

    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--ai",
            "--ai-provider",
            "openai",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert captured.get("name") == "openai"
    assert captured.get("key") == "sk-openai-TESTKEY"


def test_crawl_ai_provider_openai_missing_key_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local_server: str
) -> None:
    """D-02: ``crawl --ai-provider openai`` with NO OPENAI_API_KEY exits USER_ERROR.

    Fail-fast at t=0 — BEFORE discovery/measurement — so the post-pass and the
    measurement seam are both never reached.
    """
    _clear_keys(monkeypatch)
    measure_calls = _patch_crawl_measure(monkeypatch)
    post_pass = _record_post_pass(monkeypatch)

    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--ai",
            "--ai-provider",
            "openai",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == int(ExitCode.USER_ERROR), result.stdout + result.stderr
    assert post_pass == [], "AI post-pass must never run on the D-02 fail-fast"
    assert measure_calls == [], "measurement must never run on the D-02 fail-fast"
    assert "OPENAI_API_KEY" in " ".join(result.stderr.split())


# --------------------------------------------------------------------------- #
# --ai-base-url (D-02) wiring + openrouter selection + D-05 fail-fast
# --------------------------------------------------------------------------- #


def test_measure_ai_base_url_threads_into_build_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D-02: ``--ai-base-url`` is forwarded as ``base_url=`` into build_provider.

    The openai provider with an EXPLICIT ``--ai-model`` (so the D-05 guard is
    satisfied) threads the custom endpoint through to the concrete client builder.
    """
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-TESTKEY")
    _patch_measure_url(monkeypatch)
    captured = _capture_build_provider(monkeypatch)

    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--ai",
            "--ai-provider",
            "openai",
            "--ai-model",
            "some/custom-model",
            "--ai-base-url",
            "http://localhost:1234/v1",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert captured.get("base_url") == "http://localhost:1234/v1"


def test_measure_ai_provider_openrouter_resolves_and_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D-01/D-05: ``--ai-provider openrouter`` with only OPENROUTER_API_KEY Just Works.

    Zero other config: no --ai-model, no --ai-base-url. The named provider ships a
    valid default slug + baked base_url, so the common case requires nothing else and
    the D-05 fail-fast is NOT triggered (openrouter is exempt).
    """
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-TESTKEY")
    _patch_measure_url(monkeypatch)
    captured = _capture_build_provider(monkeypatch)

    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--ai",
            "--ai-provider",
            "openrouter",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert captured.get("name") == "openrouter"
    assert captured.get("key") == "sk-or-v1-TESTKEY"


def test_measure_ai_base_url_openai_without_model_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D-05: a bare ``--ai-base-url`` on the generic openai provider WITHOUT
    ``--ai-model`` exits USER_ERROR at t=0 — the post-pass and measurement never run.
    """
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-TESTKEY")
    measure_calls = _patch_measure_url(monkeypatch)
    post_pass = _record_post_pass(monkeypatch)

    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--ai",
            "--ai-provider",
            "openai",
            "--ai-base-url",
            "http://localhost:1234/v1",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == int(ExitCode.USER_ERROR), result.stdout + result.stderr
    assert post_pass == [], "AI post-pass must never run on the D-05 fail-fast"
    assert measure_calls == [], "measurement must never run on the D-05 fail-fast"
    msg = " ".join(result.stderr.split())
    assert "--ai-base-url" in msg
    assert "--ai-model" in msg


def test_measure_ai_base_url_openrouter_exempt_from_d05(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D-05 exemption: ``--ai-base-url`` on the openrouter provider WITHOUT --ai-model
    does NOT fail fast — openrouter ships a valid default slug.
    """
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-TESTKEY")
    _patch_measure_url(monkeypatch)
    captured = _capture_build_provider(monkeypatch)

    result = runner.invoke(
        app,
        [
            "measure",
            "https://example.com",
            "--ai",
            "--ai-provider",
            "openrouter",
            "--ai-base-url",
            "https://openrouter.ai/api/v1",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert captured.get("name") == "openrouter"
    assert captured.get("base_url") == "https://openrouter.ai/api/v1"


def test_crawl_ai_base_url_openai_without_model_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local_server: str
) -> None:
    """D-05 on crawl: bare ``--ai-base-url`` on openai w/o --ai-model exits USER_ERROR."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-TESTKEY")
    measure_calls = _patch_crawl_measure(monkeypatch)
    post_pass = _record_post_pass(monkeypatch)

    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--ai",
            "--ai-provider",
            "openai",
            "--ai-base-url",
            "http://localhost:1234/v1",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == int(ExitCode.USER_ERROR), result.stdout + result.stderr
    assert post_pass == [], "AI post-pass must never run on the D-05 fail-fast"
    assert measure_calls == [], "measurement must never run on the D-05 fail-fast"
    msg = " ".join(result.stderr.split())
    assert "--ai-base-url" in msg
    assert "--ai-model" in msg


def test_crawl_ai_base_url_threads_into_build_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local_server: str
) -> None:
    """D-02 on crawl: ``--ai-base-url`` forwards as ``base_url=`` into build_provider."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-TESTKEY")
    _patch_crawl_measure(monkeypatch)
    captured = _capture_build_provider(monkeypatch)

    result = runner.invoke(
        app,
        [
            "crawl",
            local_server + "/index.html",
            "--ai",
            "--ai-provider",
            "openrouter",
            "--ai-base-url",
            "https://openrouter.ai/api/v1",
            "--delay",
            "0",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == ExitCode.SUCCESS, result.stdout + result.stderr
    assert captured.get("name") == "openrouter"
    assert captured.get("base_url") == "https://openrouter.ai/api/v1"
