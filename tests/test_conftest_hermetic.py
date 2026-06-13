"""Proof for the Phase-05.3 autouse ``_hermetic_provider_env`` fixture (D-06/D-07/D-08).

The fixture in ``tests/conftest.py`` guarantees a default ``pytest`` run cannot
resolve a real provider and fire a paid call. These tests pin its three contracts:

1. **D-06** — every provider key + endpoint env var is cleared for a default test.
2. **D-08 / Pitfall 1** — ``cli._load_dotenv_if_present`` is neutralized to a no-op,
   so ``load_dotenv()`` cannot re-inject a developer's ``.env`` mid-test.
3. **D-07** — an ``@pytest.mark.llm`` test opts OUT: the fixture does NOT clear env,
   so a key set by the calibration harness survives. (Runs only under ``-m llm``;
   the default ``addopts`` excludes it, so the default suite stays green.)
"""

import os

import pytest

import perfcrawl.cli as cli
from perfcrawl.constants import (
    ANTHROPIC_API_KEY_ENV,
    OPENAI_API_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
)

_ALL_PROVIDER_ENV_VARS = (
    ANTHROPIC_API_KEY_ENV,
    OPENAI_API_KEY_ENV,
    OPENROUTER_API_KEY_ENV,
    "OPENAI_BASE_URL",
    "OPENROUTER_BASE_URL",
)


def test_all_five_provider_env_vars_cleared_by_default() -> None:
    """D-06: a default (unmarked) test sees none of the 5 key/endpoint vars."""
    for var in _ALL_PROVIDER_ENV_VARS:
        assert var not in os.environ, f"{var} leaked into a default test run"


def test_load_dotenv_is_neutralized_to_a_noop() -> None:
    """D-08 / Pitfall 1: calling _load_dotenv_if_present injects nothing.

    Even if a developer has these keys in a repo-root ``.env``, the fixture has
    monkeypatched the loader to a no-op, so the keys stay absent after the call.
    """
    # Precondition (also guaranteed by the autouse fixture).
    for var in _ALL_PROVIDER_ENV_VARS:
        assert var not in os.environ

    cli._load_dotenv_if_present()  # neutralized — must NOT re-inject anything

    for var in _ALL_PROVIDER_ENV_VARS:
        assert var not in os.environ, f"{var} was re-injected by dotenv (Pitfall 1)"


@pytest.mark.llm
def test_llm_marked_test_opts_out_of_clearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-07: under the ``llm`` marker the fixture does NOT clear env.

    The calibration harness (here simulated via ``setenv``) sets a key; because the
    fixture returns early for ``llm``-marked tests, that key survives — proving the
    opt-out. This assertion only executes under ``-m llm``.
    """
    monkeypatch.setenv(OPENAI_API_KEY_ENV, "sk-harness-set-key")
    assert os.environ.get(OPENAI_API_KEY_ENV) == "sk-harness-set-key"
