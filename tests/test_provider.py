"""Offline unit tests for the provider adapter (Phase 05.2 — Wave-0, no marker).

These run on a bare ``uv run pytest`` (no ``llm``/``e2e`` marker, no key, no
network). They pin:
  - AnthropicProvider maps a mocked ``messages.parse`` result / error / None
    parsed_output to ``AnalysisResult`` / ``None`` and sends the proven call shape
    (``temperature=0``, ``max_tokens=<passed>``, a cache_control system block);
  - OpenAIProvider maps a mocked ``chat.completions.parse`` completion / refusal /
    truncation / error to ``AnalysisResult`` / ``None`` and sends the three
    call-shape deltas (NO temperature, ``max_completion_tokens``,
    ``reasoning_effort=OPENAI_REASONING_EFFORT`` (``"low"`` — the cross-model floor),
    plain-string system, ``response_format``);
  - the JudgeVerdict strict schema: a missing dimension or out-of-band score is a
    ``ValidationError`` — a missing dimension is a schema error, NOT a silent gap.

The SDKs are stand-in doubles — constructing or calling them never touches a real
client (the providers only call ``client.messages.parse`` /
``client.chat.completions.parse``).
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import anthropic
import httpx
import openai
import pytest
from pydantic import ValidationError

from perfcrawl.constants import (
    OPENAI_AI_MAX_TOKENS,
    OPENAI_REASONING_EFFORT,
    OPENROUTER_BASE_URL,
)
from perfcrawl.models import AnalysisResult
from perfcrawl.provider import AnthropicProvider, OpenAIProvider, build_provider

# ``judge`` imports as a top-level module from ``tests/eval`` (no __init__.py →
# pytest's prepend import mode puts it on sys.path when eval tests collect). Add
# it explicitly so this file also passes when run in isolation
# (``uv run pytest tests/test_provider.py``).
_EVAL_DIR = str(Path(__file__).parent / "eval")
if _EVAL_DIR not in sys.path:
    sys.path.insert(0, _EVAL_DIR)

from judge import DimensionVerdict, JudgeVerdict  # noqa: E402

# A representative valid generator output reused across the happy-path assertions.
_GOOD_ANALYSIS = AnalysisResult(
    observation="LCP is 2410 ms (good, <2500).",
    potential_cause="The main JS bundle is the slowest request at 612 ms.",
    suggested_optimization="Code-split the app bundle to shed blocking JS.",
)


def _dummy_anthropic_api_error() -> anthropic.APIError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIError("fake transient API failure (test)", request, body=None)


def _dummy_openai_api_error() -> openai.OpenAIError:
    # openai.OpenAIError is the SDK base; APIConnectionError is a concrete subclass
    # constructible without a live response.
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/chat"))


def _dummy_openai_bad_request() -> openai.BadRequestError:
    # A deterministic 4xx — the reasoning_effort/unknown-model-id class (WR-01).
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return openai.BadRequestError(
        "Unsupported value: 'reasoning_effort' (test)", response=response, body=None
    )


def _dummy_anthropic_bad_request() -> anthropic.BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request)
    return anthropic.BadRequestError("invalid model (test)", response=response, body=None)


def _dummy_openai_auth_error() -> openai.AuthenticationError:
    # 401 — a wrong/absent key (e.g. an OpenRouter sk-or-v1 key sent to api.openai.com).
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(401, request=request)
    return openai.AuthenticationError(
        "Incorrect API key provided (test)", response=response, body=None
    )


def _dummy_openai_rate_limit() -> openai.RateLimitError:
    # 429 — transient; must NOT warn (the SDK already retried; a backoff is the answer).
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return openai.RateLimitError("slow down (test)", response=response, body=None)


# --------------------------------------------------------------------------- #
# AnthropicProvider doubles
# --------------------------------------------------------------------------- #
class _FakeAnthropicResp:
    def __init__(self, parsed_output: AnalysisResult | None) -> None:
        self.parsed_output = parsed_output


class _FakeAnthropicMessages:
    def __init__(self, parent: FakeAnthropic) -> None:
        self._parent = parent

    def parse(self, **kwargs):
        self._parent.calls.append(kwargs)
        if self._parent.error is not None:
            raise self._parent.error
        return _FakeAnthropicResp(self._parent.result)


class FakeAnthropic:
    def __init__(
        self,
        *,
        result: AnalysisResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []
        self.messages = _FakeAnthropicMessages(self)


# --------------------------------------------------------------------------- #
# OpenAIProvider doubles
# --------------------------------------------------------------------------- #
class _FakeOpenAIMessage:
    def __init__(self, parsed: AnalysisResult | None, refusal: str | None) -> None:
        self.parsed = parsed
        self.refusal = refusal


class _FakeOpenAIChoice:
    def __init__(self, message: _FakeOpenAIMessage) -> None:
        self.message = message


class _FakeOpenAICompletion:
    def __init__(self, message: _FakeOpenAIMessage) -> None:
        self.choices = [_FakeOpenAIChoice(message)]


class _FakeCompletionsNamespace:
    def __init__(self, parent: FakeOpenAI) -> None:
        self._parent = parent

    def parse(self, **kwargs):
        self._parent.calls.append(kwargs)
        if self._parent.error is not None:
            raise self._parent.error
        msg = _FakeOpenAIMessage(self._parent.parsed, self._parent.refusal)
        return _FakeOpenAICompletion(msg)


class _FakeChatNamespace:
    def __init__(self, parent: FakeOpenAI) -> None:
        self.completions = _FakeCompletionsNamespace(parent)


class FakeOpenAI:
    def __init__(
        self,
        *,
        parsed: AnalysisResult | None = None,
        refusal: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.parsed = parsed
        self.refusal = refusal
        self.error = error
        self.calls: list[dict] = []
        self.chat = _FakeChatNamespace(self)


# --------------------------------------------------------------------------- #
# AnthropicProvider behavior + call shape
# --------------------------------------------------------------------------- #
def test_anthropic_provider_returns_validated_model():
    client = FakeAnthropic(result=_GOOD_ANALYSIS)
    provider = AnthropicProvider(client)
    out = provider.parse_structured(
        system_text="RUBRIC", user_text="digest",
        output_model=AnalysisResult, model="claude-x", max_tokens=600,
    )
    assert out is _GOOD_ANALYSIS


def test_anthropic_provider_degrades_on_none_parsed_output():
    provider = AnthropicProvider(FakeAnthropic(result=None))
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="claude-x", max_tokens=600,
    )
    assert out is None


def test_anthropic_provider_degrades_on_api_error():
    provider = AnthropicProvider(FakeAnthropic(error=_dummy_anthropic_api_error()))
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="claude-x", max_tokens=600,
    )
    assert out is None


def test_anthropic_provider_degrades_on_generic_exception():
    provider = AnthropicProvider(FakeAnthropic(error=RuntimeError("boom")))
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="claude-x", max_tokens=600,
    )
    assert out is None


def test_anthropic_provider_warns_and_degrades_on_bad_request():
    # WR-01: a deterministic 4xx surfaces ONE RuntimeWarning and still degrades to None
    # (the run never crashes) — a 100%-misconfigured run is visible, not silent.
    provider = AnthropicProvider(FakeAnthropic(error=_dummy_anthropic_bad_request()))
    with pytest.warns(RuntimeWarning, match="request rejected"):
        out = provider.parse_structured(
            system_text="R", user_text="d", output_model=AnalysisResult,
            model="claude-x", max_tokens=600,
        )
    assert out is None


def test_openai_provider_warns_and_degrades_on_bad_request():
    # WR-01: the reasoning_effort / unknown-model-id 400 class — warn once, return None.
    provider = OpenAIProvider(FakeOpenAI(error=_dummy_openai_bad_request()))
    with pytest.warns(RuntimeWarning, match="request rejected"):
        out = provider.parse_structured(
            system_text="S", user_text="U", output_model=AnalysisResult,
            model="gpt-5-mini", max_tokens=600,
        )
    assert out is None


def test_openai_provider_bad_request_warning_names_the_param():
    # The surfaced warning carries the offending param (reasoning_effort) + model so a
    # misconfigured run self-diagnoses — but NOT the raw exception body (no scrubber here).
    provider = OpenAIProvider(FakeOpenAI(error=_dummy_openai_bad_request()))
    with pytest.warns(RuntimeWarning) as rec:
        provider.parse_structured(
            system_text="S", user_text="U", output_model=AnalysisResult,
            model="gpt-5-mini", max_tokens=600,
        )
    msg = str(rec[0].message)
    assert "gpt-5-mini" in msg
    assert "BadRequestError" in msg


def test_openai_provider_warns_and_degrades_on_auth_error_401():
    # WR-01 widened: a 401 (wrong key — the live-UAT OpenRouter-key case) is a
    # deterministic client error and must warn, not silently null every page.
    provider = OpenAIProvider(FakeOpenAI(error=_dummy_openai_auth_error()))
    with pytest.warns(RuntimeWarning) as rec:
        out = provider.parse_structured(
            system_text="S", user_text="U", output_model=AnalysisResult,
            model="gpt-5-mini", max_tokens=600,
        )
    assert out is None
    assert "AuthenticationError" in str(rec[0].message)


def test_openai_provider_silent_degrade_on_rate_limit_429():
    # WR-01 boundary: 429 is TRANSIENT (SDK already retried) — it must degrade
    # SILENTLY, no warning, so retryable load shedding isn't mistaken for misconfig.
    import warnings as _warnings

    provider = OpenAIProvider(FakeOpenAI(error=_dummy_openai_rate_limit()))
    with _warnings.catch_warnings():
        _warnings.simplefilter("error")  # any RuntimeWarning would raise here
        out = provider.parse_structured(
            system_text="S", user_text="U", output_model=AnalysisResult,
            model="gpt-5-mini", max_tokens=600,
        )
    assert out is None


def test_anthropic_provider_call_shape():
    client = FakeAnthropic(result=_GOOD_ANALYSIS)
    provider = AnthropicProvider(client)
    provider.parse_structured(
        system_text="THE RUBRIC", user_text="the digest",
        output_model=AnalysisResult, model="claude-sonnet-4-6", max_tokens=600,
    )
    kwargs = client.calls[0]
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 600
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["output_format"] is AnalysisResult
    # The system block carries cache_control ephemeral + the passed system_text.
    system_block = kwargs["system"][0]
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert system_block["text"] == "THE RUBRIC"
    assert kwargs["messages"][0]["content"] == "the digest"


# --------------------------------------------------------------------------- #
# OpenAIProvider behavior + call shape
# --------------------------------------------------------------------------- #
def test_openai_provider_returns_parsed_message():
    client = FakeOpenAI(parsed=_GOOD_ANALYSIS, refusal=None)
    provider = OpenAIProvider(client)
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="gpt-x", max_tokens=600,
    )
    assert out is _GOOD_ANALYSIS


def test_openai_provider_degrades_on_refusal():
    client = FakeOpenAI(parsed=_GOOD_ANALYSIS, refusal="I can't help with that.")
    provider = OpenAIProvider(client)
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="gpt-x", max_tokens=600,
    )
    assert out is None


def test_openai_provider_degrades_on_truncation_none_parsed():
    client = FakeOpenAI(parsed=None, refusal=None)
    provider = OpenAIProvider(client)
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="gpt-x", max_tokens=600,
    )
    assert out is None


def test_openai_provider_degrades_on_openai_error():
    provider = OpenAIProvider(FakeOpenAI(error=_dummy_openai_api_error()))
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="gpt-x", max_tokens=600,
    )
    assert out is None


def test_openai_provider_degrades_on_generic_exception():
    provider = OpenAIProvider(FakeOpenAI(error=RuntimeError("boom")))
    out = provider.parse_structured(
        system_text="R", user_text="d", output_model=AnalysisResult,
        model="gpt-x", max_tokens=600,
    )
    assert out is None


def test_openai_provider_call_shape_deltas():
    client = FakeOpenAI(parsed=_GOOD_ANALYSIS, refusal=None)
    provider = OpenAIProvider(client)  # defaults to OPENAI_AI_MAX_TOKENS
    provider.parse_structured(
        system_text="SYS", user_text="USR",
        output_model=AnalysisResult, model="gpt-5-mini",
        max_tokens=600,  # Anthropic-sized — must be IGNORED by the OpenAI impl
    )
    kwargs = client.calls[0]
    # Pitfall 1: NO temperature key.
    assert "temperature" not in kwargs
    # Pitfall 3: max_completion_tokens (the construction cap), NOT max_tokens.
    assert "max_tokens" not in kwargs
    assert kwargs["max_completion_tokens"] == OPENAI_AI_MAX_TOKENS
    # Pitfall 1: reasoning_effort is the single-source cross-model floor ("low").
    assert kwargs["reasoning_effort"] == OPENAI_REASONING_EFFORT
    # System content is a PLAIN STRING (no cache_control wrapper).
    system_msg = kwargs["messages"][0]
    assert system_msg["role"] == "system"
    assert isinstance(system_msg["content"], str)
    assert system_msg["content"] == "SYS"
    # response_format is the Pydantic class → strict schema.
    assert kwargs["response_format"] is AnalysisResult


def test_openai_provider_honors_construction_max_completion_tokens():
    """The judge lane constructs with a higher cap; the impl uses it, not the arg."""
    client = FakeOpenAI(parsed=_GOOD_ANALYSIS, refusal=None)
    provider = OpenAIProvider(client, max_completion_tokens=3000)
    provider.parse_structured(
        system_text="S", user_text="U", output_model=AnalysisResult,
        model="gpt-5.5", max_tokens=800,
    )
    assert client.calls[0]["max_completion_tokens"] == 3000


# --------------------------------------------------------------------------- #
# D-04: send_reasoning_effort gate — portable shape vs native gpt-5 shape.
# Get the call shape wrong and every page through OpenRouter degrades to None on
# a `reasoning_effort` 400; get it right and the native gpt-5 path is unchanged.
# --------------------------------------------------------------------------- #
def test_openai_provider_send_reasoning_effort_false_uses_temperature():
    # Portable shape: OpenRouter's cheap (non-reasoning) models 400 on
    # reasoning_effort, so it MUST be omitted and temperature=0 sent instead.
    client = FakeOpenAI(parsed=_GOOD_ANALYSIS, refusal=None)
    provider = OpenAIProvider(client, send_reasoning_effort=False)
    provider.parse_structured(
        system_text="S", user_text="U", output_model=AnalysisResult,
        model="openai/gpt-4o-mini", max_tokens=600,
    )
    kwargs = client.calls[0]
    assert kwargs["temperature"] == 0
    assert "reasoning_effort" not in kwargs


def test_openai_provider_send_reasoning_effort_true_uses_reasoning_effort():
    # Native gpt-5 shape (byte-for-byte 05.2): reasoning_effort sent, NO temperature.
    client = FakeOpenAI(parsed=_GOOD_ANALYSIS, refusal=None)
    provider = OpenAIProvider(client, send_reasoning_effort=True)
    provider.parse_structured(
        system_text="S", user_text="U", output_model=AnalysisResult,
        model="gpt-5-mini", max_tokens=600,
    )
    kwargs = client.calls[0]
    assert kwargs["reasoning_effort"] == OPENAI_REASONING_EFFORT
    assert "temperature" not in kwargs


def test_openai_provider_send_reasoning_effort_defaults_true():
    # Default preserves the native path for any existing caller that omits the flag.
    client = FakeOpenAI(parsed=_GOOD_ANALYSIS, refusal=None)
    provider = OpenAIProvider(client)
    provider.parse_structured(
        system_text="S", user_text="U", output_model=AnalysisResult,
        model="gpt-5-mini", max_tokens=600,
    )
    kwargs = client.calls[0]
    assert kwargs["reasoning_effort"] == OPENAI_REASONING_EFFORT
    assert "temperature" not in kwargs


# --------------------------------------------------------------------------- #
# build_provider: base_url threading + the send_reasoning_effort gate (D-04/D-01).
# --------------------------------------------------------------------------- #
def test_build_provider_openrouter_threads_base_url_and_portable_shape():
    provider = build_provider("openrouter", "k")
    assert isinstance(provider, OpenAIProvider)
    # The OpenRouter base_url from the registry reached the SDK client.
    assert str(provider._client.base_url).rstrip("/") == OPENROUTER_BASE_URL
    # OpenRouter takes the portable shape (its cheap models reject reasoning_effort).
    assert provider._send_reasoning_effort is False


def test_build_provider_openai_native_reasoning_effort():
    # No base_url override on the openai provider → native gpt-5 path (True).
    provider = build_provider("openai", "k")
    assert isinstance(provider, OpenAIProvider)
    assert provider._send_reasoning_effort is True


def test_build_provider_openai_custom_base_url_forces_portable():
    # The `and (effective_base_url is None)` clause: ANY custom base_url forces the
    # portable shape, even on the openai row whose registry flag is True.
    provider = build_provider("openai", "k", base_url="https://gw.example/v1")
    assert str(provider._client.base_url).rstrip("/") == "https://gw.example/v1"
    assert provider._send_reasoning_effort is False


def test_build_provider_openrouter_base_url_override_wins():
    # An explicit base_url overrides the registry default; still portable.
    provider = build_provider("openrouter", "k", base_url="https://other/v1")
    assert str(provider._client.base_url).rstrip("/") == "https://other/v1"
    assert provider._send_reasoning_effort is False


# --------------------------------------------------------------------------- #
# Single-source meta-test (CLAUDE.md hard rule + Phase-1 grep discipline).
# Mirrors tests/test_cli.py::test_cli_source_has_no_bare_inp — provider.py must
# reach the OpenRouter base_url host + default model slug ONLY via the constants
# import, never as an inline literal.
# --------------------------------------------------------------------------- #
def test_provider_source_has_no_inline_base_url_or_slug():
    import perfcrawl.provider as provider_module

    src = inspect.getsource(provider_module)
    # Strip pure-comment lines so a header comment mentioning the host/slug cannot
    # self-invalidate the gate — only the executable source body must be clean.
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert "openrouter.ai" not in code, (
        "provider.py must reach the OpenRouter host only via OPENROUTER_BASE_URL"
    )
    assert "gpt-4o-mini" not in code, (
        "provider.py must reach the OpenRouter slug only via the constant"
    )


# --------------------------------------------------------------------------- #
# JudgeVerdict strict schema (Pitfall 5 — a missing dimension is NOT silent)
# --------------------------------------------------------------------------- #
def _dim(verdict: str = "PASS", score: int = 4) -> dict:
    return {"verdict": verdict, "score": score, "rationale": "ok"}


def test_judge_schema_strict():
    schema = JudgeVerdict.model_json_schema()
    # All four sub-verdicts are REQUIRED in the strict schema OpenAI would receive.
    required = set(schema.get("required", []))
    assert {
        "causal_plausibility",
        "threshold_correctness",
        "actionability",
        "prioritization",
    } <= required

    # A complete verdict round-trips.
    full = JudgeVerdict(
        causal_plausibility=DimensionVerdict(**_dim()),
        threshold_correctness=DimensionVerdict(**_dim()),
        actionability=DimensionVerdict(**_dim()),
        prioritization=DimensionVerdict(**_dim()),
    )
    assert full.causal_plausibility.score == 4

    # A MISSING dimension is a ValidationError (a schema error, NOT a silent gap).
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate(
            {
                "causal_plausibility": _dim(),
                "threshold_correctness": _dim(),
                "actionability": _dim(),
                # prioritization omitted
            }
        )

    # A score outside 1-5 is a ValidationError.
    with pytest.raises(ValidationError):
        DimensionVerdict(verdict="PASS", score=6, rationale="too high")
    with pytest.raises(ValidationError):
        DimensionVerdict(verdict="FAIL", score=0, rationale="too low")
