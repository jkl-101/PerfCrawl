"""Deterministic digest + frozen-rubric eval (Wave-0 RED harness).

Pattern: pure-function-over-fixture (mirrors ``tests/test_normalizer.py``) — feed a
curated ``tests/fixtures/digests/*.json`` ``PageResult`` through ``build_digest``
and assert the output is byte-stable and timestamp/UUID-free (so the cached prompt
prefix can never drift — AI-SPEC Pitfall 1), plus that ``RUBRIC`` is a frozen
module constant long enough to clear the prompt-cache token minimum (Pitfall 2).

These import the Task-2 ``perfcrawl.analysis`` stub, so they collect cleanly and
fail RED (``NotImplementedError`` / the empty-``RUBRIC`` assertion) until Plan 02
lands the real ``build_digest`` + rubric. That RED state is the intended Wave-0
outcome — these tests ARE the executable spec Plan 02 turns GREEN.
"""

import re

from perfcrawl import analysis

# ~4 chars/token is the standard rough heuristic; clearing this char floor
# approximates clearing the 1,024-token prompt-cache minimum for Sonnet-4.6 /
# Opus-4.8 (AI-SPEC §4b / RESEARCH Pitfall 2). The empty placeholder RUBRIC in
# the Task-2 stub fails this on purpose.
CACHE_MIN_TOKENS = 1024
CHARS_PER_TOKEN = 4
RUBRIC_CHAR_FLOOR = CACHE_MIN_TOKENS * CHARS_PER_TOKEN

# A datetime- or UUID-shaped token must never appear in a digest (would drift the
# cache prefix and leak nondeterminism). ISO timestamp or canonical UUID shapes.
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_HEX = "[0-9a-fA-F]"
_UUID_RE = re.compile(rf"{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}")


def test_digest_stable(digest_page) -> None:
    """``build_digest`` is byte-identical across calls and waterfall reorderings.

    Determinism is the cache-prefix contract (AI-SPEC Pitfall 1): the same page
    must render the same bytes every time, a reordered waterfall must render
    identically (the builder sorts internally), and no ``datetime``/UUID token
    may leak into the text.
    """
    page = digest_page("slow-lcp")

    first = analysis.build_digest(page)
    second = analysis.build_digest(page)
    assert first == second, "build_digest must be byte-identical across calls"

    # Reorder the waterfall on a copy; the digest must be unchanged (internal sort).
    shuffled = page.model_copy(deep=True)
    shuffled.waterfall = list(reversed(shuffled.waterfall))
    assert analysis.build_digest(shuffled) == first, (
        "a reordered waterfall must render identically (deterministic sort)"
    )

    # No nondeterministic tokens leak into the cached/variable text.
    assert not _DATETIME_RE.search(first), "digest must contain no datetime token"
    assert not _UUID_RE.search(first), "digest must contain no UUID token"


def test_rubric_frozen() -> None:
    """``RUBRIC`` is a frozen module-level ``str`` long enough to cache (Pitfall 2)."""
    rubric = analysis.RUBRIC
    assert isinstance(rubric, str), "RUBRIC must be a module-level str constant"
    approx_tokens = len(rubric) / CHARS_PER_TOKEN
    assert len(rubric) >= RUBRIC_CHAR_FLOOR, (
        f"RUBRIC is ~{approx_tokens:.0f} tokens (<{CACHE_MIN_TOKENS}); a too-short "
        "rubric silently never caches (AI-SPEC Pitfall 2) — bulk it past the floor."
    )
