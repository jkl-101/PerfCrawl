"""Slug + constants tests — D-07 (IN-02 boundary) + D-14/D-15 (one-editable-place).

These assertions pin the observable transform of ``page_slug(url_key)``:
the slug never contains ``..``, ``/``, or ``\\``; deterministic ``"_"`` sentinel
for blank input; charset is the safe filesystem subset ``[A-Za-z0-9._-]``; idempotent;
length-bounded; and bizarre input never raises.

Constants are verified by import + assert — they are the ONE editable place for
Phase 2 tunables (D-08, D-10, D-11, D-14, D-15); call sites import from
``perfcrawl.constants``, never inline.
"""

import re

import pytest

from perfcrawl.canonical import canonical_key
from perfcrawl.slug import page_slug


# --- D-07 / IN-02: the load-bearing path-traversal assertion -----------------


@pytest.mark.parametrize(
    "traversal_attempt",
    [
        # The canonical LEARNINGS IN-02 example: w3lib decodes %2e%2e to '..'
        "https://x.com/a/%2e%2e/b",
        # Already-decoded literal '..'
        "https://x.com/a/../b",
        "https://x.com/../../etc/passwd",
        "https://x.com/.../...//",
        "..",
        "../../../",
    ],
)
def test_no_path_traversal_in_slug(traversal_attempt):
    """page_slug() must never produce a stem that contains '..', '/', or '\\' (D-07 / IN-02)."""
    slug = page_slug(canonical_key(traversal_attempt))
    assert ".." not in slug
    assert "/" not in slug
    assert "\\" not in slug
    # also no leading dot (filesystem-hidden) and no path-separator-equivalent
    assert not slug.startswith(".")


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  \t"])
def test_empty_or_blank_input_returns_underscore_sentinel(blank):
    """Blank/whitespace input returns the '_' sentinel (D-07; mirrors canonical's '' sentinel)."""
    assert page_slug(blank) == "_"


def test_charset_subset_of_safe_chars():
    """page_slug always returns chars in [A-Za-z0-9._-] (D-07)."""
    samples = [
        "https://example.com/foo/bar?baz=qux",
        "https://x.com/a/%2e%2e/b",
        "https://Example.com:8443/Path/With/Unicode/éí",
        "https://x.com/" + "a" * 200,
        "://broken",
        "\x00\x01\x02",
    ]
    safe = re.compile(r"^[A-Za-z0-9._-]+$")
    for url in samples:
        slug = page_slug(url)
        assert safe.match(slug), f"slug {slug!r} from {url!r} has unsafe chars"


def test_idempotent():
    """page_slug(page_slug(x)) == page_slug(x) (D-07; same shape as canonical_key)."""
    for url in [
        "https://example.com/foo/bar",
        "https://x.com/a/%2e%2e/b",
        "https://Example.com:8443/Path",
    ]:
        once = page_slug(url)
        twice = page_slug(once)
        assert once == twice, f"not idempotent: {url!r} -> {once!r} -> {twice!r}"


def test_max_len_truncation():
    """page_slug truncates to max_len (default 80)."""
    long_url = "https://x.com/" + "a" * 200
    assert len(page_slug(long_url)) <= 80
    # Custom max_len
    assert len(page_slug(long_url, max_len=20)) <= 20


@pytest.mark.parametrize(
    "bizarre",
    ["", "://broken", "http://", " \t\n", "\x00\x01\x02", "...", "....////"],
)
def test_does_not_raise_on_bizarre_input(bizarre):
    """Bizarre input returns a non-empty str matching the safe charset (D-07)."""
    result = page_slug(bizarre)
    assert isinstance(result, str)
    assert len(result) > 0
    assert re.match(r"^[A-Za-z0-9._-]+$", result), f"unsafe chars in {result!r}"


# --- D-08 / D-10 / D-11 / D-14 / D-15: constants module is the ONE editable place ---


def test_constants_module_declares_phase2_tunables():
    """constants.py is the ONE editable place for Phase 2 tunables (D-14)."""
    from perfcrawl.constants import (
        ALWAYS_INCLUDE_AUDITS,
        DEFAULT_SAMPLES_N,
        DEVTOOLS_PORT_FILE_TIMEOUT_S,
        DEVTOOLS_PORT_POLL_INTERVAL_S,
        EXPECTED_LIGHTHOUSE_MAJOR_MINOR,
        INP_PROXY_DISPLAY_LABEL,
        PER_SAMPLE_TIMEOUT_S,
        ExitCode,
    )

    # D-14: per-sample timeout default (subprocess.run timeout=…)
    assert PER_SAMPLE_TIMEOUT_S == 60
    # D-08 + Claude's discretion: odd-N default for median friendliness
    assert DEFAULT_SAMPLES_N == 3
    # D-10: normalizer version gate (bumped when worker's package-lock.json bumps)
    assert EXPECTED_LIGHTHOUSE_MAJOR_MINOR == "13.x"
    # D-11: human-summary column header for the TBT proxy
    assert INP_PROXY_DISPLAY_LABEL == "INP (lab proxy, TBT-based)"
    # Pitfall 1 (Claude's discretion): DevToolsActivePort file polling
    assert DEVTOOLS_PORT_FILE_TIMEOUT_S > 0
    assert DEVTOOLS_PORT_POLL_INTERVAL_S > 0
    assert DEVTOOLS_PORT_FILE_TIMEOUT_S > DEVTOOLS_PORT_POLL_INTERVAL_S
    # D-15: three exit codes, intentional gap before Phase 6 BUDG-01 carve-out
    assert ExitCode.SUCCESS == 0
    assert ExitCode.USER_ERROR == 1
    assert ExitCode.MEASUREMENT_ERROR == 2
    # MEDIUM-4 fix from plan-check: ALWAYS_INCLUDE_AUDITS carve-out for OUT-04
    assert "interactive" in ALWAYS_INCLUDE_AUDITS
