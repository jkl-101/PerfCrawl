"""Deterministic grounding eval (Wave-0 RED harness) — names per 05-VALIDATION.

The non-deterministic AI feature is validated *deterministically*: a fake Anthropic
client drives the engine (no network, no key) and the grounding invariants are pure
functions run both here and at runtime as pre-write guardrails. Test names are the
binding contract from 05-VALIDATION § "Per-Task Verification Map":

  test_null_short_circuit · test_degrade · test_key_scrubbed_every_sink ·
  test_no_bare_inp · test_no_fabricated_number · test_no_unsupported_entity
  (+ test_good_page_schema_valid for the schema-validity dimension)

Pattern: inject ``FakeAnthropic`` into ``analyze_run`` (monkeypatch-the-seam, like
``tests/test_cli_crawl.py``) for the engine dims, and call the pure grounding
functions over fixtures for the rest. All import the Task-2 ``perfcrawl.analysis``
stub, so they collect cleanly and fail RED (``NotImplementedError`` / assertion)
until Plans 02/03 implement the bodies — the intended Wave-0 outcome.
"""

import json
from datetime import UTC, datetime

from perfcrawl import analysis
from perfcrawl.auth import make_scrubber
from perfcrawl.cli import _format_calibration_note
from perfcrawl.models import AnalysisResult, PageResult, RunRecord
from perfcrawl.output import write_outputs
from perfcrawl.provider import AnthropicProvider

TEST_KEY = "sk-ant-TESTKEY"


def _run_with(pages: list[PageResult]) -> RunRecord:
    """A minimal tz-aware RunRecord wrapping ``pages`` (analyze_run mutates it)."""
    return RunRecord(
        started_at=datetime(2026, 6, 8, 12, 0, 0, tzinfo=UTC),
        target="https://www.studyhalo.com",
        pages=pages,
    )


# --------------------------------------------------------------------------- #
# D-06: null short-circuit — error row → analysis None AND NO API call
# --------------------------------------------------------------------------- #


def test_null_short_circuit(digest_page, fake_anthropic_good) -> None:
    """A fully-null error row leaves ``analysis is None`` AND never calls the client."""
    error_row = digest_page("fully-null-error-row")
    run = _run_with([error_row])

    analysis.analyze_run(run, provider=AnthropicProvider(fake_anthropic_good), scrub=lambda t: t)

    assert run.pages[0].analysis is None, "error row must short-circuit to analysis=None"
    assert fake_anthropic_good.call_count == 0, (
        "D-06: no API call may be made for a fully-null error row"
    )


# --------------------------------------------------------------------------- #
# D-09: graceful degrade — APIError / None → analysis None, run completes
# --------------------------------------------------------------------------- #


def test_degrade(
    digest_page, fake_anthropic_error, fake_anthropic_none, fake_anthropic_good
) -> None:
    """A failing AI client degrades every data page to None without losing the run.

    The run must complete (never raise), every data page degrades to
    ``analysis is None`` on both the ``APIError`` and the ``parsed_output is None``
    paths, the error row counts as insufficient (no call), and a *working* client
    instead yields a result on those same pages — proving a single AI miss is
    enrichment lost, never a crawl failure (D-09).
    """
    data_pages = ["healthy-all-green", "slow-lcp"]

    # APIError variant → both data pages degrade; run completes; counts correct.
    run = _run_with([digest_page(n) for n in data_pages] + [digest_page("fully-null-error-row")])
    summary = analysis.analyze_run(
        run, provider=AnthropicProvider(fake_anthropic_error), scrub=lambda t: t
    )
    assert all(p.analysis is None for p in run.pages), "APIError must degrade every page to None"
    assert summary["degraded"] == 2, summary
    assert summary["insufficient"] == 1, summary
    assert summary["analyzed"] == 0, summary

    # Refusal/None variant → identical degrade behavior.
    run_none = _run_with([digest_page(n) for n in data_pages])
    summary_none = analysis.analyze_run(
        run_none, provider=AnthropicProvider(fake_anthropic_none), scrub=lambda t: t
    )
    assert all(p.analysis is None for p in run_none.pages)
    assert summary_none["degraded"] == 2, summary_none

    # Working client → the SAME pages get a real analysis (others-get-a-result).
    run_ok = _run_with([digest_page(n) for n in data_pages])
    summary_ok = analysis.analyze_run(
        run_ok, provider=AnthropicProvider(fake_anthropic_good), scrub=lambda t: t
    )
    assert all(p.analysis is not None for p in run_ok.pages), "a working client must analyze pages"
    assert summary_ok["analyzed"] == 2, summary_ok


# --------------------------------------------------------------------------- #
# Schema validity — a good page yields a schema-valid AnalysisResult
# --------------------------------------------------------------------------- #


def test_good_page_schema_valid(digest_page, fake_anthropic_good) -> None:
    """A good page through ``analyze_run`` yields a schema-valid ``AnalysisResult``."""
    run = _run_with([digest_page("healthy-all-green")])
    analysis.analyze_run(run, provider=AnthropicProvider(fake_anthropic_good), scrub=lambda t: t)
    result = run.pages[0].analysis
    assert isinstance(result, AnalysisResult), "analysis must be a schema-valid AnalysisResult"


# --------------------------------------------------------------------------- #
# AUTH-04: the API key is scrubbed at EVERY sink (incl. result.csv — CR-01)
# --------------------------------------------------------------------------- #


def test_key_scrubbed_every_sink(tmp_path) -> None:
    """Seeded with the key, no sink (result.json, result.csv, --json stdout, analysis) leaks it.

    The key is embedded in BOTH an ``analysis`` field (serialized to result.json
    and the ``--json`` stdout shape) AND a URL field (``slowest_request_url``,
    which flows to result.csv) — so the result.csv sink is tested explicitly per
    the CR-01 "scrub every sink incl. result.csv" MEMORY lesson, not just stderr.
    """
    scrub = make_scrubber(TEST_KEY)
    page = PageResult(
        url="https://www.studyhalo.com/",
        url_key="https://www.studyhalo.com/",
        perf_score=90.0,
        status_code=200,
        # key embedded in a CSV-bound URL field (CR-01 every-sink incl. result.csv)
        slowest_request_url=f"https://www.studyhalo.com/static/app.js?k={TEST_KEY}",
        slowest_request_ms=120.0,
        # key embedded in the AI analysis fields
        analysis=AnalysisResult(
            observation=f"see key {TEST_KEY} in observation",
            potential_cause=f"cause mentions {TEST_KEY}",
            suggested_optimization=f"opt mentions {TEST_KEY}",
        ),
    )
    run = _run_with([page])

    run_dir = write_outputs(run, output_dir=tmp_path, scrub=scrub)

    result_json = (run_dir / "result.json").read_text()
    result_csv = (run_dir / "result.csv").read_text()
    json_stdout = scrub(run.model_dump_json())  # the `--json` stdout shape

    assert TEST_KEY not in result_json, "key leaked into result.json"
    assert TEST_KEY not in result_csv, "key leaked into result.csv (CR-01 sink)"
    assert TEST_KEY not in json_stdout, "key leaked into --json stdout"
    # The analysis fields specifically must be redacted in the persisted record.
    persisted = json.loads(result_json)
    analysis_blob = json.dumps(persisted["pages"][0]["analysis"])
    assert TEST_KEY not in analysis_blob, "key leaked into a persisted analysis field"


# --------------------------------------------------------------------------- #
# AUTH-04 / T-05.1-10: the judge-lane calibration-report sink is ALSO scrubbed.
# Distinct from test_key_scrubbed_every_sink (which exercises the analyze/write
# path) — this proves the SECOND key-bearing lane (the paid judge's calibration
# report surfaced by _render_ai_health) reuses the same make_scrubber.
# --------------------------------------------------------------------------- #


def test_key_scrubbed_judge_lane() -> None:
    """The judge-lane calibration report passes through make_scrubber — key cannot survive.

    The judge lane spends real tokens with the key, so its calibration report is a
    distinct AUTH-04 sink from the analyze path. A calibration payload bearing the
    fake key (standing in for any key-bearing token reaching the calibration-report
    sink) must come back redacted when routed through ``_format_calibration_note``
    with a key-seeded scrubber — proving the judge lane reuses the SAME scrubber, not
    a second hand-rolled redactor.
    """
    scrub = make_scrubber(TEST_KEY)
    # The fake key embedded in calibration content reaching the report sink.
    calibration = {
        f"causal_plausibility {TEST_KEY}": {"spearman": 0.81, "kappa": 0.74, "trusted": True},
        "threshold_correctness": {"spearman": 0.62, "kappa": 0.55, "trusted": False},
    }

    note = _format_calibration_note(calibration, scrub=scrub)

    assert note is not None, "a non-empty calibration payload must render a note"
    assert TEST_KEY not in note, "key leaked into the judge-lane calibration report (AUTH-04 sink)"
    # And the scrubber is genuinely engaged: an unscrubbed render WOULD carry the key.
    assert TEST_KEY in _format_calibration_note(calibration, scrub=None)


# --------------------------------------------------------------------------- #
# D-15: no bare INP — TBT is the lab proxy, never a real field INP claim
# --------------------------------------------------------------------------- #


def test_no_bare_inp() -> None:
    """A bare-INP assertion fails the check; a TBT-proxy-labeled mention passes."""
    assert analysis.check_no_bare_inp("INP is 480 ms on this page.") is False
    assert (
        analysis.check_no_bare_inp("TBT is 480 ms (lab proxy for INP; field INP not measured).")
        is True
    )


# --------------------------------------------------------------------------- #
# AI-01: fabricated-number detector — every numeric must appear in the digest
# --------------------------------------------------------------------------- #


def test_no_fabricated_number() -> None:
    """A numeric absent from the digest is flagged; a present one is not."""
    digest = "LCP (ms): 4800\nTTFB (ms): 300\nRequest count: 40"
    fabricated = analysis.find_fabricated_numbers("LCP is 9999 ms here.", digest)
    assert fabricated, "a number absent from the digest must be flagged"
    grounded = analysis.find_fabricated_numbers("LCP is 4800 ms (poor).", digest)
    assert grounded == [], "a number present in the digest must NOT be flagged"


# --------------------------------------------------------------------------- #
# Dim 5 (insufficient-data honesty) — the OUTPUT-PHRASING half (05-EVAL-REVIEW #6).
# The digest-signal half ships in test_digest.py (556be0a); this is the distinct
# output half: the acceptable note SAYS "insufficient data" for the n/a metrics
# and grounds the present ones, while a note that invents a value for an n/a
# metric is caught by the fabricated-number detector. Deterministic, offline.
# --------------------------------------------------------------------------- #


def test_partial_null_output_phrasing(digest_page, load_gold) -> None:
    """dim-5 OUTPUT half: the partial-null gold note is honest; a fabricated value is flagged.

    (a) The acceptable/gold note for the ``partial-null`` page says "insufficient
        data" (honesty phrasing) for the missing/n/a metrics AND grounds the present
        metrics — every number it cites appears in the digest (no fabrication).
    (b) A generated note that instead claims a concrete value for an n/a metric
        (e.g. an LCP / Performance number the digest renders as ``n/a``) is caught
        by ``find_fabricated_numbers`` against that same digest.
    """
    digest = analysis.build_digest(digest_page("partial-null"))
    gold = load_gold("partial-null")
    assert gold is not None, "the partial-null fixture must carry a gold label (Plan 02)"

    # (a) The acceptable note is HONEST about the missing metrics...
    observation = gold["observation"]
    assert "insufficient data" in observation.lower(), (
        "the partial-null gold note must say 'insufficient data' for the n/a metrics"
    )
    # ...and grounds the metrics it DOES cite — no number absent from the digest.
    assert analysis.find_fabricated_numbers(observation, digest) == [], (
        "the partial-null gold note must only cite numbers present in the digest"
    )

    # (b) A note that fabricates a value for an n/a metric (LCP/Performance are n/a
    # in this digest) is flagged — the dim-5 failure the output phrasing must avoid.
    fabricating_note = "Performance score is 45 and LCP is 3200 ms (poor); reduce JS."
    fabricated = analysis.find_fabricated_numbers(fabricating_note, digest)
    assert "3200" in fabricated, "an invented LCP for the n/a metric must be flagged"
    assert "45" in fabricated, "an invented performance score for the n/a metric must be flagged"


# --------------------------------------------------------------------------- #
# AI-02: out-of-evidence entity — no framework/server/CDN absent from the digest
# --------------------------------------------------------------------------- #


def test_no_unsupported_entity() -> None:
    """A stack/server entity absent from the digest is flagged; grounded text is not."""
    digest = "URL: https://www.studyhalo.com/enroll\nSlowest request: /static/main.js"
    flagged = analysis.find_unsupported_entities(
        "This is an nginx misconfiguration in your React app.", digest
    )
    assert flagged, "a framework/server entity absent from the digest must be flagged"
    grounded = analysis.find_unsupported_entities(
        "The slowest request is /static/main.js at the listed timing.", digest
    )
    assert grounded == [], "text citing only digest evidence must NOT be flagged"
