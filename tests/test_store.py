"""Hybrid SQLite store tests — criteria #1, #3 and D-06/D-07/D-08.

Pins the store's observable contract:

  - ``read_run(write_run(r))`` equals ``r`` by MODEL equality (criterion #1 /
    HIST-01); round-trip identity is asserted at the model level (Pitfall 1, A3).
  - the exact ``model_dump_json()`` bytes are preserved in the ``record_json``
    TEXT column (TEXT, not JSONB).
  - an older-schema blob loads under the newer model with missing fields ``None``
    (criterion #3 / D-06/D-08).
  - a metric can be promoted later via ``ADD COLUMN ... GENERATED ALWAYS AS (...)
    VIRTUAL``; the identical statement with ``STORED`` raises
    ``sqlite3.OperationalError`` (D-07 / Pitfall 2).
  - the generated ``url_key`` column is queryable and equals the page's canonical
    key (the cross-run self-join key).
"""

import sqlite3

import pytest

from perfcrawl.models import RunRecord
from perfcrawl.store import init_db, read_run, write_run


@pytest.fixture
def conn():
    """A fresh in-memory SQLite connection with the schema initialised."""
    c = sqlite3.connect(":memory:")
    init_db(c)
    yield c
    c.close()


def test_round_trip_identity(conn, sample_run: RunRecord):
    """read_run(write_run(r)) == r by model equality (criterion #1 / HIST-01)."""
    write_run(conn, sample_run)
    loaded = read_run(conn, str(sample_run.id))
    assert loaded.model_dump() == sample_run.model_dump()
    assert loaded == sample_run


def test_record_json_bytes_preserved(conn, sample_run: RunRecord):
    """The exact model_dump_json() bytes are stored in record_json TEXT (not JSONB)."""
    write_run(conn, sample_run)
    row = conn.execute(
        "SELECT record_json FROM runs WHERE id = ?", (str(sample_run.id),)
    ).fetchone()
    assert row is not None
    # byte/string identity: the column holds exactly what was serialized
    assert row[0] == sample_run.model_dump_json()


def test_old_schema_loads(conn, run_v1_old_schema_json: str):
    """An older-schema blob writes + reads under the newer model, missing -> None.

    (criterion #3 / D-06/D-08)
    """
    old_run = RunRecord.model_validate_json(run_v1_old_schema_json)
    write_run(conn, old_run)
    loaded = read_run(conn, str(old_run.id))
    assert loaded.schema_version == 1
    assert len(loaded.pages) == 2
    # later-phase fields absent in the old blob default to None
    assert loaded.pages[0].lcp_ms is None
    assert loaded.pages[0].perf_score is None
    assert loaded.auth_used is None
    assert loaded == old_run


def test_promote_column_virtual(conn):
    """Promote a metric via a VIRTUAL generated column; STORED-via-ALTER raises (D-07)."""
    # VIRTUAL: succeeds (computed on read, indexable)
    conn.execute(
        "ALTER TABLE page_results ADD COLUMN lcp_median REAL "
        "GENERATED ALWAYS AS (json_extract(record_json, '$.lcp_ms.median')) VIRTUAL"
    )
    conn.execute("CREATE INDEX idx_pr_lcp ON page_results(lcp_median)")
    # STORED via ALTER: SQLite forbids it (cannot backfill rows) -> OperationalError
    with pytest.raises(sqlite3.OperationalError):
        conn.execute(
            "ALTER TABLE page_results ADD COLUMN lcp_median_stored REAL "
            "GENERATED ALWAYS AS (json_extract(record_json, '$.lcp_ms.median')) STORED"
        )


def test_promoted_virtual_column_is_queryable(conn, sample_run: RunRecord):
    """A promoted VIRTUAL column computes the right value from the blob and is queryable."""
    write_run(conn, sample_run)
    conn.execute(
        "ALTER TABLE page_results ADD COLUMN lcp_median REAL "
        "GENERATED ALWAYS AS (json_extract(record_json, '$.lcp_ms.median')) VIRTUAL"
    )
    rows = conn.execute(
        "SELECT lcp_median FROM page_results WHERE run_id = ? ORDER BY lcp_median",
        (str(sample_run.id),),
    ).fetchall()
    medians = [r[0] for r in rows]
    # one page has lcp median 2410, the other 3120; the third (none) sorts first as NULL
    assert 2410.0 in medians
    assert 3120.0 in medians


def test_url_key_generated(conn, sample_run: RunRecord):
    """page_results.url_key (generated from the blob) equals the canonical key."""
    write_run(conn, sample_run)
    rows = conn.execute(
        "SELECT url_key FROM page_results WHERE run_id = ? ORDER BY url_key",
        (str(sample_run.id),),
    ).fetchall()
    keys = [r[0] for r in rows]
    assert keys == [
        "https://studyhalo.com/",
        "https://studyhalo.com/courses?page=2",
    ]


def test_url_key_set_on_write_when_missing(conn):
    """write_run derives url_key via canonical_key() when the caller left it blank (D-01)."""
    from datetime import UTC, datetime

    from perfcrawl.models import PageResult

    # url_key intentionally empty -> the store must populate it from canonical_key(url)
    run = RunRecord(
        started_at=datetime(2026, 5, 25, tzinfo=UTC),
        target="https://x.com",
        pages=[
            PageResult(url="https://Example.com/Path/?utm_source=x&b=2&a=1#frag", url_key="")
        ],
    )
    write_run(conn, run)
    loaded = read_run(conn, str(run.id))
    # raw url is never mutated (D-01); url_key is the derived canonical key
    assert loaded.pages[0].url == "https://Example.com/Path/?utm_source=x&b=2&a=1#frag"
    assert loaded.pages[0].url_key == "https://example.com/Path?a=1&b=2"


def test_generated_column_cannot_drift(conn, sample_run: RunRecord):
    """The generated url_key column is computed FROM the blob, so it cannot disagree."""
    write_run(conn, sample_run)
    rows = conn.execute(
        "SELECT json_extract(record_json, '$.url_key'), url_key FROM page_results "
        "WHERE run_id = ?",
        (str(sample_run.id),),
    ).fetchall()
    for blob_key, col_key in rows:
        assert blob_key == col_key
