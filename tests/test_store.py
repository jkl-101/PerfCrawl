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


def test_promote_column_virtual(conn, sample_run: RunRecord):
    """Promote a metric via a VIRTUAL generated column; STORED-via-ALTER raises (D-07).

    The "promote later" path always runs against a table that already holds rows
    (you promote a metric AFTER runs exist), so a row is written first — that is
    precisely when SQLite enforces the restriction: it cannot backfill an existing
    row for a STORED column. (On an empty table the STORED ALTER would no-op, which
    is not the real-world case D-07 cares about.)
    """
    write_run(conn, sample_run)
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
    # sample_run has two pages: one lcp median 2410, the other 3120 (IN-03 — the
    # earlier "third (none)" note was wrong; this fixture has no third page).
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


def test_url_key_whitespace_only_is_regenerated(conn):
    """A whitespace-only url_key is regenerated, not stored verbatim (WR-07).

    'if not page.url_key' would treat '   ' as truthy and persist the blanks
    into the self-join column, so the same logical page would never match across
    runs. The store must strip-check and regenerate the canonical key.
    """
    from datetime import UTC, datetime

    from perfcrawl.models import PageResult

    run = RunRecord(
        started_at=datetime(2026, 5, 25, tzinfo=UTC),
        target="https://x.com",
        pages=[PageResult(url="https://Example.com/Path/?b=2&a=1", url_key="   ")],
    )
    write_run(conn, run)
    loaded = read_run(conn, str(run.id))
    # whitespace was replaced by the derived canonical key, not stored verbatim
    assert loaded.pages[0].url_key == "https://example.com/Path?a=1&b=2"
    # and the generated self-join column matches (no whitespace leaked through)
    col_key = conn.execute(
        "SELECT url_key FROM page_results WHERE run_id = ?", (str(run.id),)
    ).fetchone()[0]
    assert col_key == "https://example.com/Path?a=1&b=2"


def test_write_run_does_not_mutate_caller(conn):
    """write_run never mutates the caller's RunRecord (WR-06).

    Key derivation happens on a deep copy, so a caller's blank/whitespace
    url_key values survive write_run unchanged for reuse afterward.
    """
    from datetime import UTC, datetime

    from perfcrawl.models import PageResult

    run = RunRecord(
        started_at=datetime(2026, 5, 25, tzinfo=UTC),
        target="https://x.com",
        pages=[
            PageResult(url="https://x.com/a", url_key=""),
            PageResult(url="https://x.com/b", url_key="   "),
        ],
    )
    write_run(conn, run)
    # The caller's object is unchanged: its blank keys were NOT back-filled.
    assert run.pages[0].url_key == ""
    assert run.pages[1].url_key == "   "
    # But the persisted record DID derive the canonical keys.
    loaded = read_run(conn, str(run.id))
    assert loaded.pages[0].url_key == "https://x.com/a"
    assert loaded.pages[1].url_key == "https://x.com/b"


def test_write_run_rejects_duplicate_url_key(conn):
    """Two pages with the same url_key in one run raise ValueError (WR-02).

    url_key is the per-run canonical page identity (one page per key per run). A
    reader that buckets a run's pages by url_key (e.g. compute_deltas) keeps only
    the last duplicate, silently dropping the rest. write_run enforces the
    uniqueness invariant at write time so the contract holds for every reader.
    """
    from datetime import UTC, datetime

    from perfcrawl.models import PageResult

    run = RunRecord(
        started_at=datetime(2026, 5, 25, tzinfo=UTC),
        target="https://x.com",
        pages=[
            PageResult(url="https://x.com/?a=1", url_key="https://x.com/?a=1"),
            # Same canonical key as above (a fragment never identifies a distinct
            # resource), so both pages collide on one cross-run identity.
            PageResult(url="https://x.com/?a=1#frag", url_key="https://x.com/?a=1"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate url_key"):
        write_run(conn, run)
    # Rejected before any insert: nothing was persisted for this run.
    persisted = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE id = ?", (str(run.id),)
    ).fetchone()[0]
    assert persisted == 0


def test_write_run_rejects_duplicate_after_key_derivation(conn):
    """Duplicate detection runs AFTER key derivation: two blank keys on the same
    canonical URL collide once derived, and must still be rejected (WR-02)."""
    from datetime import UTC, datetime

    from perfcrawl.models import PageResult

    run = RunRecord(
        started_at=datetime(2026, 5, 25, tzinfo=UTC),
        target="https://x.com",
        pages=[
            # Both have blank url_key; both derive to the same canonical key, so
            # the post-derivation duplicate check (not just a raw-key check) fires.
            PageResult(url="https://Example.com/Path/?utm_source=x&a=1", url_key=""),
            PageResult(url="https://example.com/Path?a=1#frag", url_key="   "),
        ],
    )
    with pytest.raises(ValueError, match="duplicate url_key"):
        write_run(conn, run)


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


def test_write_run_reasserts_foreign_keys(sample_run: RunRecord, tmp_path):
    """write_run re-asserts PRAGMA foreign_keys on its connection (WR-05).

    foreign_keys is per-connection state, not stored in the DB. A caller who
    initialises the DB once and then opens a NEW connection to write gets
    foreign_keys=OFF by default. write_run must turn it back ON so orphan
    page_results rows are rejected, not silently accepted.
    """
    db = tmp_path / "perfcrawl.db"
    # Initialise the schema on one connection, then throw it away.
    init_conn = sqlite3.connect(db)
    init_db(init_conn)
    init_conn.close()

    # A brand-new connection: foreign_keys defaults to OFF here.
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        write_run(conn, sample_run)
        # write_run must have turned FK enforcement back ON on this connection.
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # And the constraint is actually live: an orphan page row is rejected.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO page_results (run_id, record_json) VALUES (?, ?)",
                ("no-such-run-id", '{"url":"x","url_key":"x"}'),
            )
    finally:
        conn.close()


class _FailOnPageInsertConnection(sqlite3.Connection):
    """A Connection whose page_results bulk insert always raises.

    sqlite3.Connection methods are read-only C attributes (cannot be
    monkeypatched), so we subclass to inject a mid-write failure: the ``runs``
    insert succeeds, then ``executemany`` (the page_results bulk insert) raises,
    exercising the rollback path of write_run's explicit transaction.
    """

    def executemany(self, *args, **kwargs):
        raise sqlite3.OperationalError("simulated mid-write failure")


def test_write_run_is_atomic_on_failure(sample_run: RunRecord):
    """A mid-write failure rolls back — no partial run is ever persisted (CR-01).

    write_run wraps its inserts in an explicit transaction. If the page_results
    insert raises after the runs row was inserted, the whole transaction must
    roll back so a subsequent commit() on the shared connection cannot flush a
    partial run to disk.
    """
    conn = sqlite3.connect(":memory:", factory=_FailOnPageInsertConnection)
    try:
        init_db(conn)
        with pytest.raises(sqlite3.OperationalError):
            write_run(conn, sample_run)

        # Force a commit: if the failed write had left an open transaction with a
        # partial run, this commit would flush it to the table.
        conn.commit()

        runs = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE id = ?", (str(sample_run.id),)
        ).fetchone()[0]
        pages = conn.execute(
            "SELECT COUNT(*) FROM page_results WHERE run_id = ?", (str(sample_run.id),)
        ).fetchone()[0]
        assert runs == 0, "the runs row must have rolled back (no partial run)"
        assert pages == 0, "no page_results rows may survive a rolled-back write"
    finally:
        conn.close()


# --- Phase 6 HIST-02 / D-04: prior-run baseline lookup (RED) -----------------
# These pin the (not-yet-existing) ``store.read_previous_run`` contract that the
# regression layer needs: "get the immediately-prior run for this target". They
# are AUTHORED BEFORE the implementation (Wave-0) and MUST fail until Plan 04 adds
# the function. ``read_previous_run`` is imported LAZILY inside each test so only
# THESE two tests go red — the rest of this module keeps collecting cleanly (no
# collateral breakage in a default ``pytest`` run).


def _run_for(target: str, started_at, run_id):
    """A minimal one-page RunRecord for a target at a given tz-aware time."""
    from uuid import UUID

    from perfcrawl.models import PageResult

    return RunRecord(
        id=UUID(run_id),
        started_at=started_at,
        target=target,
        pages=[PageResult(url=f"{target}/", url_key=f"{target}/")],
    )


def test_read_previous_run_none(conn):
    """No prior run for a target -> returns None, NOT a raise (the first-run case).

    The very first audit of a site has no baseline; that is legitimate, not an
    error. ``read_previous_run`` must return ``None`` so the caller can simply
    skip regression flagging (D-04 / D-14: a first run never errors, never flags).
    """
    from datetime import UTC, datetime

    from perfcrawl.store import read_previous_run  # RED: Plan 04 adds this.

    # A single run exists, but the lookup is for a DIFFERENT target.
    write_run(
        conn,
        _run_for(
            "https://studyhalo.com",
            datetime(2026, 5, 1, tzinfo=UTC),
            "11111111-0000-4000-8000-000000000001",
        ),
    )
    result = read_previous_run(
        conn, "https://other.example", datetime(2026, 6, 1, tzinfo=UTC).isoformat()
    )
    assert result is None


def test_read_previous_run_picks_prior(conn):
    """Returns the immediately-prior run for the target — never the current one (D-04).

    Two runs for the same target with increasing ``started_at`` are written; the
    lookup with the LATER run's timestamp must return the EARLIER run (the
    ``started_at < before`` filter excludes the current run from being its own
    baseline — Pitfall 4).
    """
    from datetime import UTC, datetime

    from perfcrawl.store import read_previous_run  # RED: Plan 04 adds this.

    target = "https://studyhalo.com"
    earlier = _run_for(
        target, datetime(2026, 5, 1, tzinfo=UTC), "22222222-0000-4000-8000-000000000002"
    )
    later = _run_for(
        target, datetime(2026, 5, 25, tzinfo=UTC), "33333333-0000-4000-8000-000000000003"
    )
    write_run(conn, earlier)
    write_run(conn, later)

    result = read_previous_run(conn, target, later.started_at.isoformat())
    assert result is not None
    assert result.id == earlier.id
    assert result.id != later.id
