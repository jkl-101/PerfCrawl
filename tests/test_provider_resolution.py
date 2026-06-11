"""Offline unit tests for resolve_provider (Phase 05.2 — D-01/D-02, no marker).

The full D-01/D-02 resolution matrix, with zero network and no key construction:
  - D-01: explicit flag wins; else Anthropic-wins back-compat tie-break; else
    OpenAI; the order is the contract.
  - D-02: env-only key — an explicit flag whose key_env is absent, or no key at
    all, raises the REUSED ``UserError`` (from perfcrawl.orchestrator) with an
    env-only-never-a-flag message.

``resolve_provider`` reads an env *mapping* (``os.environ`` in prod), so these
pass a plain dict — no monkeypatching of the real environment needed.
"""

from __future__ import annotations

import pytest

from perfcrawl.constants import (
    ANTHROPIC_API_KEY_ENV,
    OPENAI_API_KEY_ENV,
)
from perfcrawl.orchestrator import UserError
from perfcrawl.provider import resolve_provider


@pytest.mark.parametrize(
    ("flag", "env", "expected"),
    [
        # explicit flag wins (key present)
        ("openai", {OPENAI_API_KEY_ENV: "o"}, "openai"),
        ("anthropic", {ANTHROPIC_API_KEY_ENV: "a"}, "anthropic"),
        # explicit flag wins even when BOTH keys are present
        ("openai", {ANTHROPIC_API_KEY_ENV: "a", OPENAI_API_KEY_ENV: "o"}, "openai"),
        # D-01 auto-detect: Anthropic-wins tie-break when both keys present, no flag
        (None, {ANTHROPIC_API_KEY_ENV: "a", OPENAI_API_KEY_ENV: "o"}, "anthropic"),
        # auto-detect: only OpenAI key → openai
        (None, {OPENAI_API_KEY_ENV: "o"}, "openai"),
        # auto-detect: only Anthropic key → anthropic
        (None, {ANTHROPIC_API_KEY_ENV: "a"}, "anthropic"),
    ],
)
def test_resolve_provider_selects(flag, env, expected):
    assert resolve_provider(flag, env) == expected


def test_resolve_provider_explicit_flag_missing_key_raises():
    with pytest.raises(UserError) as exc:
        resolve_provider("openai", {})
    msg = str(exc.value)
    assert OPENAI_API_KEY_ENV in msg
    assert "never a flag" in msg


def test_resolve_provider_explicit_anthropic_missing_key_raises():
    with pytest.raises(UserError) as exc:
        resolve_provider("anthropic", {OPENAI_API_KEY_ENV: "o"})
    msg = str(exc.value)
    assert ANTHROPIC_API_KEY_ENV in msg
    assert "never a flag" in msg


def test_resolve_provider_no_key_at_all_raises():
    with pytest.raises(UserError) as exc:
        resolve_provider(None, {})
    msg = str(exc.value)
    assert ANTHROPIC_API_KEY_ENV in msg
    assert OPENAI_API_KEY_ENV in msg
    assert "never a flag" in msg
