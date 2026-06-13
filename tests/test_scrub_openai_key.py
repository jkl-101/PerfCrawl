"""CR-01 — an OPENAI_API_KEY is redacted at EVERY output sink (Phase 05.2 Plan 04).

The Phase-5/05.1 lesson (`[[perfcrawl-scrub-every-sink-result-csv]]`) is that a
single missed sink leaks: the AUTH-04 scrubber was seeded for stderr/result.json
but the *result.csv* row was written from the raw RunRecord, leaking a
URL-embedded credential into a persisted artifact. So this test does NOT assert
redaction at one representative sink — it asserts it across the FOUR distinct
sink shapes a provider key can land in, with the result.csv ROW (the exact sink
missed before) called out explicitly.

The provider-agnostic adapter (05.2) makes the OpenAI key a first-class secret:
``make_scrubber(anthropic_key, openai_key)`` is now seeded from BOTH present
provider keys at every ``make_scrubber(...)`` call site in ``cli.measure`` and
``cli.crawl``. This unit test pins the closure behavior that wiring depends on —
the OpenAI key value must be replaced with ``REDACTION_PLACEHOLDER`` no matter
which sink-shaped string carries it, and a ``None`` second key (a non-AI or
anthropic-only run) must be a filtered no-op, never corrupting text.
"""

from perfcrawl.auth import make_scrubber
from perfcrawl.constants import REDACTION_PLACEHOLDER

# A realistic-shaped OpenAI key value (the secret) + an Anthropic key seeded
# alongside it (so the test exercises the BOTH-keys seeding the CLI now does).
_OPENAI_KEY = "sk-proj-AbC123dEf456GhI789jKl012MnO345pQr678StU901vWx"
_ANTHROPIC_KEY = "sk-ant-api03-ZZZredactme999"


def _sinks(key: str) -> dict[str, str]:
    """Build one string per representative output sink, each embedding ``key``.

    The four shapes mirror the real egress points the CLI scrubber guards:
      - result.json  — a JSON object string with the key in a field value
      - result.csv   — a single comma-separated CSV ROW (the previously-missed sink)
      - --json stdout — the model JSON streamed to a (possibly piped) stdout
      - stderr        — a degrade/grounding log line printed to the error console
    """
    return {
        "result.json": (
            '{"target": "https://example.com/", '
            '"analysis": {"observation": "ok via ' + key + '"}}'
        ),
        # The exact sink the AUTH-04 fix missed before: a raw CSV data row whose
        # one field carries the key value (a URL-embedded credential leak class).
        "result.csv": "https://user:" + key + "@example.com/,90,1200,200",
        "--json stdout": '{"id": "run-1", "note": "key=' + key + '"}',
        "stderr": "[yellow]AI degraded for page 3 (auth " + key + ")[/yellow]",
    }


def test_openai_key_redacted_at_every_sink() -> None:
    """The OpenAI key is replaced with the placeholder across all four sink shapes."""
    scrub = make_scrubber(_ANTHROPIC_KEY, _OPENAI_KEY)
    sinks = _sinks(_OPENAI_KEY)

    # Guard against a degenerate test: every sink must actually carry the raw key
    # BEFORE scrubbing (otherwise a "not in" assertion would pass vacuously).
    for name, text in sinks.items():
        assert _OPENAI_KEY in text, f"fixture for {name!r} must embed the raw key"

    for name, text in sinks.items():
        scrubbed = scrub(text)
        assert _OPENAI_KEY not in scrubbed, f"OpenAI key leaked at the {name!r} sink"
        assert REDACTION_PLACEHOLDER in scrubbed, f"no redaction at the {name!r} sink"


def test_result_csv_row_sink_redacted() -> None:
    """CR-01 regression pin: the result.csv ROW sink (the one missed before) redacts.

    Asserted on its own — `[[perfcrawl-scrub-every-sink-result-csv]]` was a real
    leak through exactly this sink shape, so it gets a dedicated guard beyond the
    every-sink sweep above.
    """
    scrub = make_scrubber(_ANTHROPIC_KEY, _OPENAI_KEY)
    csv_row = "https://user:" + _OPENAI_KEY + "@example.com/,90,1200,200"
    scrubbed = scrub(csv_row)
    assert _OPENAI_KEY not in scrubbed
    assert REDACTION_PLACEHOLDER in scrubbed
    # The non-secret columns must survive intact (the scrubber only masks secrets).
    assert scrubbed.endswith(",90,1200,200")


def test_anthropic_key_also_redacted_when_both_seeded() -> None:
    """Both seeded keys are masked — seeding the OpenAI key never displaces the other."""
    scrub = make_scrubber(_ANTHROPIC_KEY, _OPENAI_KEY)
    text = "a=" + _ANTHROPIC_KEY + " o=" + _OPENAI_KEY
    scrubbed = scrub(text)
    assert _ANTHROPIC_KEY not in scrubbed
    assert _OPENAI_KEY not in scrubbed
    assert scrubbed == f"a={REDACTION_PLACEHOLDER} o={REDACTION_PLACEHOLDER}"


def test_none_second_key_is_filtered_noop() -> None:
    """A None OpenAI key (non-AI / anthropic-only run) is a no-op, not a corruption.

    ``"".replace("", X)`` sprays X between every character; the factory must filter
    falsy secrets (auth.py:80) so seeding ``(anthropic_key, None)`` leaves non-secret
    text byte-identical and only masks the real Anthropic key.
    """
    text = "no openai key here, just an anthropic one: " + _ANTHROPIC_KEY
    scrub = make_scrubber(_ANTHROPIC_KEY, None)
    scrubbed = scrub(text)
    # The placeholder is NOT sprayed between characters of the clean prefix.
    assert scrubbed.startswith("no openai key here, just an anthropic one: ")
    assert _ANTHROPIC_KEY not in scrubbed
    assert scrubbed.count(REDACTION_PLACEHOLDER) == 1

    # And a fully-None AI seeding (a non-AI run) is pure identity.
    clean = "no secrets at all in this line"
    assert make_scrubber(None, None)(clean) == clean
