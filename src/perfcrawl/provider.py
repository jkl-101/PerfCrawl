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
    to ``"minimal"`` instead), ``max_completion_tokens`` (NOT ``max_tokens``), a
    plain-string system message (NO ``cache_control`` — OpenAI auto prefix-caches).

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
    PROVIDERS,
)
from perfcrawl.orchestrator import UserError  # reuse — never define a new error type


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
        except anthropic.APIError:
            return None
        except Exception:  # defense-in-depth — never crash the run/harness
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
    ) -> None:
        self._client = client
        self._max_completion_tokens = max_completion_tokens

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
            completion = self._client.chat.completions.parse(
                model=model,
                # Pitfall 1: NO temperature — the gpt-5 family 400s on it.
                reasoning_effort="minimal",
                # Pitfall 3: max_completion_tokens, NOT max_tokens.
                max_completion_tokens=self._max_completion_tokens,
                messages=[
                    # Plain string, NO cache_control — OpenAI auto prefix-caches.
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_text},
                ],
                response_format=output_model,
            )
            msg = completion.choices[0].message
            # Refusal / truncation → None, the SAME disposition the Anthropic path
            # gives a ``parsed_output is None`` (RESEARCH §degrade-to-None).
            if getattr(msg, "refusal", None):
                return None
            return msg.parsed
        except openai.OpenAIError:
            return None
        except Exception:  # defense-in-depth — never crash the run/harness
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
    openai_max_completion_tokens: int = OPENAI_AI_MAX_TOKENS,
) -> Provider:
    """Construct the concrete ``Provider`` for ``name`` with the single-source budget.

    Both SDK clients get ``max_retries=AI_MAX_RETRIES`` + ``timeout=
    AI_REQUEST_TIMEOUT_S`` (T-05.2-07 — a hung call degrades a page promptly; the
    SDK owns the retry loop, D-11). The OpenAI client is given the completion-token
    cap (Pitfall 2): the generator lane defaults to ``OPENAI_AI_MAX_TOKENS``; the
    judge lane passes ``OPENAI_JUDGE_MAX_TOKENS`` via this arg.
    """
    if name == "anthropic":
        return AnthropicProvider(
            anthropic.Anthropic(
                api_key=key,
                max_retries=AI_MAX_RETRIES,
                timeout=AI_REQUEST_TIMEOUT_S,
            )
        )
    if name == "openai":
        return OpenAIProvider(
            openai.OpenAI(
                api_key=key,
                max_retries=AI_MAX_RETRIES,
                timeout=AI_REQUEST_TIMEOUT_S,
            ),
            max_completion_tokens=openai_max_completion_tokens,
        )
    raise UserError(f"unknown provider {name!r}; expected one of {sorted(PROVIDERS)}")
