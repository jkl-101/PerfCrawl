"""Phase-05.2 provider-agnostic AI adapter — the single dispatch seam (D-04).

This module is the only genuinely new code in the phase. It formalizes a
copy-paste that already exists twice in the tree (``analysis.analyze_page`` and
``tests/eval/judge.judge_pair`` both call ``client.messages.parse(output_format=
...)`` with the same degrade-to-None disposition) behind one ``Provider``
protocol with a single ``parse_structured(...) -> BaseModel | None`` method.

D-04 (researcher's resolved recommendation): an in-house thin adapter over each
SDK's native ``.parse()`` — NOT LiteLLM. The native ``.parse()`` re-validates the
structured response client-side against the Pydantic schema, which is exactly the
guarantee a generic proxy layer cannot make.

Two impls:
  - ``AnthropicProvider`` — the VERBATIM-lifted body of ``analysis.py:291-304``
    (cache_control ephemeral system block + zero-sampling determinism + the passed
    ``max_tokens``).
  - ``OpenAIProvider`` — the three call-shape deltas (RESEARCH Pitfalls 1-3): no
    sampling-temp key (the reasoning model rejects it; ``reasoning_effort`` is set
    to the single-source ``OPENAI_REASONING_EFFORT`` = ``"low"`` — the cross-model
    floor, since ``"minimal"`` 400s on the gpt-5.5 judge), ``max_completion_tokens``
    (NOT ``max_tokens``), a plain-string system message (NO ``cache_control`` —
    OpenAI auto prefix-caches).

Both degrade to ``None`` on SDK error / refusal / truncation — they NEVER crash
the run (D-09). No app-level retry loop (D-11 — the SDK owns ``max_retries``).

``resolve_provider`` implements D-01 (explicit flag wins → else Anthropic-wins
back-compat tie-break → else OpenAI → else fail) and D-02 (env-only key; raises
when the resolved provider's key is absent). ``build_provider`` constructs the
concrete SDK client with the single-source retry budget + timeout on both.

Single-source discipline (constants.py:182-187 / 241-246): every model id, token
cap, env-var name, retry count, and timeout is imported BY NAME from
``perfcrawl.constants`` — never inlined (the Phase-1 grep meta-test enforces it).
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from typing import Protocol

import anthropic
import openai
from pydantic import BaseModel

from perfcrawl.constants import (
    AI_MAX_RETRIES,
    AI_REQUEST_TIMEOUT_S,
    ANTHROPIC_API_KEY_ENV,
    OPENAI_AI_MAX_TOKENS,
    OPENAI_API_KEY_ENV,
    OPENAI_REASONING_EFFORT,
    PROVIDERS,
)
from perfcrawl.orchestrator import UserError  # reuse — never define a new error type


#: Deterministic client-error statuses worth surfacing: a 400 (bad request / bad
#: params), 401 (wrong/absent key), 403 (no access), 404 (unknown model), 409, 422.
#: These fail IDENTICALLY for every page — a config mistake, not an outage. 429
#: (rate-limit) and 5xx are transient (the SDK already retried), so they stay silent
#: degrade like a connection error. `400 <= status < 429` captures exactly the
#: deterministic 4xx set and excludes 429.
def _is_deterministic_client_error(status: int | None) -> bool:
    return status is not None and 400 <= status < 429


def _warn_request_rejected(provider_label: str, model: str, exc: Exception) -> None:
    """Surface a deterministic 4xx request rejection once (WR-01, widened).

    A deterministic 4xx — 400 (bad model id / an ``--ai-model`` that rejects
    ``reasoning_effort`` / a schema the model can't honor), 401 (wrong or absent
    key, e.g. an OpenRouter ``sk-or-v1-…`` key sent to api.openai.com), 403 (no
    access), 404 (unknown model) — fails IDENTICALLY for every page, so the
    degrade-to-None contract would otherwise report "all pages degraded" with no
    hint that the cause was a bad invocation rather than a transient outage. Emit
    one ``RuntimeWarning`` (Python's default filter shows it once per call site, so
    an N-page run warns once). We include only the model + exception type + status +
    offending param — NEVER ``str(exc)``: provider.py has no scrubber, and the body
    can echo a (masked) key or request content. The exception TYPE (e.g.
    ``AuthenticationError``) + status + param NAME is safe and is exactly what a
    misconfigured run needs to self-diagnose.
    """
    status = getattr(exc, "status_code", "?")
    param = getattr(exc, "param", None) or getattr(exc, "code", None)
    detail = f" param={param}" if param else ""
    warnings.warn(
        f"{provider_label} request rejected (model={model!r}, "
        f"{type(exc).__name__} {status}{detail}); this page degraded to no-analysis. "
        f"Check the provider API key and --ai-model / params.",
        RuntimeWarning,
        stacklevel=2,
    )


class Provider(Protocol):
    """The single seam both call sites narrow to (D-04).

    One method: a stateless structured-output parse that returns a validated
    Pydantic model instance, or ``None`` when the call refuses / truncates /
    errors (the degrade-to-None contract every consumer relies on).
    """

    def parse_structured(
        self,
        *,
        system_text: str,
        user_text: str,
        output_model: type[BaseModel],
        model: str,
        max_tokens: int,
    ) -> BaseModel | None: ...


class AnthropicProvider:
    """Wraps the EXISTING proven ``messages.parse`` call verbatim (analysis.py:291-304).

    The cache_control ephemeral system block + zero-sampling determinism +
    ``max_tokens`` shape is unchanged from the GREEN Phase-5/05.1 code — that
    body lifted behind the protocol, with ``system_text`` / ``output_model`` /
    ``max_tokens`` parameterized in place of the hard-coded RUBRIC / AnalysisResult
    / AI_MAX_TOKENS.
    """

    def __init__(self, client: anthropic.Anthropic) -> None:
        self._client = client

    def parse_structured(
        self,
        *,
        system_text: str,
        user_text: str,
        output_model: type[BaseModel],
        model: str,
        max_tokens: int,
    ) -> BaseModel | None:
        try:
            resp = self._client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                temperature=0,
                system=[
                    {
                        "type": "text",
                        "text": system_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_text}],
                output_format=output_model,
            )
            return resp.parsed_output
        except anthropic.APIStatusError as e:
            # WR-01/IN-02 (widened): a deterministic 4xx (bad request, wrong key,
            # no access, unknown model) is surfaced once; transient 429/5xx fall
            # through to silent degrade. Always returns None: never crashes (D-09).
            if _is_deterministic_client_error(getattr(e, "status_code", None)):
                _warn_request_rejected("Anthropic", model, e)
            return None
        except Exception:  # transient API/connection error / refusal / SDK shape — degrade
            return None


class OpenAIProvider:
    """Encapsulates the three OpenAI call-shape deltas (RESEARCH Pitfalls 1-3).

    Construction stores the completion-token cap (Pitfall 2): the generator lane
    defaults to ``OPENAI_AI_MAX_TOKENS``; the judge lane constructs with
    ``OPENAI_JUDGE_MAX_TOKENS``. The ``max_tokens`` arg passed to
    ``parse_structured`` is intentionally IGNORED by this impl — the
    Anthropic-sized 600/800 caps would starve the reasoning model's hidden tokens
    (which are spent BEFORE the visible answer) and truncate the structured parse
    to a degraded ``None`` (Pitfall 2). The construction-time cap governs instead.
    """

    def __init__(
        self,
        client: openai.OpenAI,
        *,
        max_completion_tokens: int = OPENAI_AI_MAX_TOKENS,
        send_reasoning_effort: bool = True,
    ) -> None:
        self._client = client
        self._max_completion_tokens = max_completion_tokens
        # D-04: whether this endpoint accepts the gpt-5-family reasoning_effort kwarg.
        # Default True preserves the native 05.2 gpt-5 path for any existing caller;
        # build_provider sets it False for OpenRouter / any custom base_url endpoint.
        self._send_reasoning_effort = send_reasoning_effort

    def parse_structured(
        self,
        *,
        system_text: str,
        user_text: str,
        output_model: type[BaseModel],
        model: str,
        max_tokens: int,  # noqa: ARG002 — Pitfall 2: NOT used (see class docstring)
    ) -> BaseModel | None:
        try:
            # D-04: build the call kwargs conditionally on the capability flag. The
            # native gpt-5 path keeps the 05.2 shape byte-for-byte (reasoning_effort,
            # NO temperature); the portable path drops reasoning_effort (the cheap
            # OpenRouter models 400 on it) and sends temperature=0 instead.
            parse_kwargs: dict = {
                "model": model,
                # Pitfall 3: max_completion_tokens, NOT max_tokens.
                "max_completion_tokens": self._max_completion_tokens,
                "messages": [
                    # Plain string, NO cache_control — OpenAI auto prefix-caches.
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ],
                "response_format": output_model,
            }
            if self._send_reasoning_effort:
                # Pitfall 1: NO temperature — the gpt-5 family 400s on it.
                # reasoning_effort is the single-source cross-model floor (constants.py):
                # "low" — "minimal" is gpt-5-mini-only and 400s on the gpt-5.5 judge.
                parse_kwargs["reasoning_effort"] = OPENAI_REASONING_EFFORT
            else:
                # Portable shape: temperature=0 is broadly accepted; reasoning_effort
                # is omitted so a non-reasoning endpoint never 400s every page.
                parse_kwargs["temperature"] = 0
            completion = self._client.chat.completions.parse(**parse_kwargs)
            msg = completion.choices[0].message
            # Refusal / truncation → None, the SAME disposition the Anthropic path
            # gives a ``parsed_output is None`` (RESEARCH §degrade-to-None).
            if getattr(msg, "refusal", None):
                return None
            return msg.parsed
        except openai.APIStatusError as e:
            # WR-01 (widened): a deterministic 4xx — bad request (reasoning_effort /
            # unknown model id), 401 wrong key (e.g. an OpenRouter key sent to
            # api.openai.com), 403, 404 — is surfaced once; transient 429/5xx fall
            # through to silent degrade. Still returns None (D-09, never crashes).
            if _is_deterministic_client_error(getattr(e, "status_code", None)):
                _warn_request_rejected("OpenAI", model, e)
            return None
        except Exception:  # transient API/connection error / refusal / SDK shape — degrade
            return None


def resolve_provider(flag: str | None, env: Mapping[str, str]) -> str:
    """Map an optional ``--ai-provider`` flag + the environment to a provider name.

    D-01 resolution order (RESEARCH Pattern 3):
      1. an explicit ``flag`` wins — but raises ``UserError`` if that provider's
         key_env is absent (D-02 env-only key, fail-fast);
      2. else ``ANTHROPIC_API_KEY`` present → "anthropic" (back-compat tie-break);
      3. else ``OPENAI_API_KEY`` present → "openai";
      4. else raise ``UserError`` — ``--ai`` needs one of the two keys (env-only).

    The key is NEVER a flag (argv is visible in ``ps`` / shell history) — every
    error message says so explicitly.
    """
    if flag:  # explicit wins
        # CR-01: --ai-provider is a free-form str (no click.Choice), so guard the
        # registry lookup BEFORE indexing it — a typo (`OpenAI`, `claude`, `gpt4`)
        # must raise the reused UserError (→ ExitCode.USER_ERROR at both call sites),
        # never an uncaught KeyError / raw traceback (D-01/D-02 fail-fast contract).
        if flag not in PROVIDERS:
            raise UserError(
                f"--ai-provider {flag!r} is not recognized; "
                f"expected one of {sorted(PROVIDERS)}"
            )
        key_env = PROVIDERS[flag]["key_env"]
        if not env.get(key_env):
            raise UserError(
                f"--ai-provider {flag} requires {key_env} (env or .env), never a flag"
            )
        return flag
    if env.get(ANTHROPIC_API_KEY_ENV):  # D-01 Anthropic-wins tie-break (back-compat)
        return "anthropic"
    if env.get(OPENAI_API_KEY_ENV):
        return "openai"
    raise UserError(
        "--ai requires ANTHROPIC_API_KEY or OPENAI_API_KEY (env or .env), never a flag"
    )


def build_provider(
    name: str,
    key: str,
    *,
    base_url: str | None = None,
    openai_max_completion_tokens: int = OPENAI_AI_MAX_TOKENS,
) -> Provider:
    """Construct the concrete ``Provider`` for ``name`` with the single-source budget.

    Both SDK clients get ``max_retries=AI_MAX_RETRIES`` + ``timeout=
    AI_REQUEST_TIMEOUT_S`` (T-05.2-07 — a hung call degrades a page promptly; the
    SDK owns the retry loop, D-11). The OpenAI client is given the completion-token
    cap (Pitfall 2): the generator lane defaults to ``OPENAI_AI_MAX_TOKENS``; the
    judge lane passes ``OPENAI_JUDGE_MAX_TOKENS`` via this arg.

    D-04/D-01: ``base_url`` threads a custom OpenAI-compatible endpoint into the
    SDK client. The effective base_url is the explicit ``base_url`` arg when given,
    else the registry default (OpenRouter bakes its own). ``send_reasoning_effort``
    is the registry capability flag AND requires no base_url override — so ANY
    custom endpoint (including ``--ai-provider openai --ai-base-url <gw>``) takes
    the portable call shape, closing the edge a pure registry flag would miss.
    """
    if name == "anthropic":
        return AnthropicProvider(
            anthropic.Anthropic(
                api_key=key,
                max_retries=AI_MAX_RETRIES,
                timeout=AI_REQUEST_TIMEOUT_S,
            )
        )
    if name in ("openai", "openrouter"):
        cfg = PROVIDERS[name]
        effective_base_url = base_url or cfg.get("base_url")
        send_re = cfg.get("send_reasoning_effort", False) and (
            effective_base_url is None
        )
        return OpenAIProvider(
            openai.OpenAI(
                api_key=key,
                base_url=effective_base_url,
                max_retries=AI_MAX_RETRIES,
                timeout=AI_REQUEST_TIMEOUT_S,
            ),
            max_completion_tokens=openai_max_completion_tokens,
            send_reasoning_effort=send_re,
        )
    raise UserError(f"unknown provider {name!r}; expected one of {sorted(PROVIDERS)}")
