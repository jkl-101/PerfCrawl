"""Hybrid SQLite run store — write a run, read it back identically (criterion #1).

The store is the persistence half of the Phase 1 data contract. It implements
the D-07 "hybrid" design: each record is kept as a full-fidelity **JSON TEXT
blob** (the exact ``model_dump_json()`` bytes) so a run reads back byte-for-byte,
while the metrics that get queried/self-joined are exposed as **generated
columns** computed *from* that blob via ``json_extract`` — so a promoted column
can never drift from the source record.

Why TEXT, not JSONB: JSONB re-serializes to SQLite's canonical binary form and
would not preserve the input bytes, breaking the round-trip-identity guarantee
(criterion #1, Pitfall 1). ``record_json`` is therefore RAW TEXT.

Schema evolution (D-06/D-07/D-08):
  - adding a new *model* field needs NO ``ALTER TABLE`` — it just lands inside the
    blob and old blobs read back with the new field defaulting to ``None``.
  - promoting a field to a queryable column later is a cheap additive
    ``ALTER TABLE ... ADD COLUMN ... GENERATED ALWAYS AS (json_extract(...))
    VIRTUAL``. NOTE: SQLite cannot add a **STORED** generated column via
    ``ALTER TABLE`` once the table holds rows — it would have to backfill each
    existing row and raises ``OperationalError: cannot add a STORED column``
    (verified on SQLite 3.50.4). Always use VIRTUAL when promoting later (it is
    still indexable); declare STORED columns only at ``CREATE TABLE`` time. The
    real "promote a metric" path always runs against a populated table, so treat
    STORED-via-ALTER as unavailable.

Security (threat T-01-T, T-01-P): every statement uses ``?`` placeholders — never
f-string / ``%`` / ``.format`` SQL — and the DB is opened by an explicit
caller-supplied path with no dynamic table names. Once this ``.db`` holds real
run data (Phase 2+) it is local and gitignored (see ``.gitignore`` — ``*.db`` /
``*.sqlite``); Phase 1 stores no secrets (auth is Phase 4).
"""

import sqlite3

from perfcrawl.canonical import canonical_key
from perfcrawl.models import RunRecord

# STRICT tables (type-checked rows). record_json is RAW TEXT so the round-trip is
# byte-identical; the promoted columns are GENERATED from the blob (cannot drift).
# Only url_key + perf_score are promoted at create time (the self-join key + the
# headline score); other metrics are promoted later as VIRTUAL columns on demand.
_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    started_at     TEXT NOT NULL,
    target         TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    record_json    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS page_results (
    run_id      TEXT NOT NULL REFERENCES runs(id),
    record_json TEXT NOT NULL,
    url_key     TEXT GENERATED ALWAYS AS (json_extract(record_json, '$.url_key')) STORED,
    perf_score  REAL GENERATED ALWAYS AS (json_extract(record_json, '$.perf_score')) STORED
) STRICT;

CREATE INDEX IF NOT EXISTS idx_pr_urlkey ON page_results(url_key);
CREATE INDEX IF NOT EXISTS idx_pr_run    ON page_results(run_id);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create the ``runs`` + ``page_results`` tables and indexes (idempotent).

    Enables foreign-key enforcement on the connection. Safe to call repeatedly
    (``IF NOT EXISTS``).
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)
    conn.commit()


def write_run(conn: sqlite3.Connection, run: RunRecord) -> None:
    """Persist ``run`` and each of its pages as JSON-TEXT blobs (criterion #1).

    For every page, ``url_key`` is derived from ``canonical_key(page.url)`` when
    the caller left it blank (D-01: the raw ``url`` is never mutated — only the
    derived key is set). The exact ``model_dump_json()`` bytes are written so the
    record reads back identically.

    The whole write is wrapped in an explicit transaction (``with conn:``): the
    ``runs`` row and every ``page_results`` row commit together on success, or the
    transaction ROLLS BACK on any exception so a mid-write failure can never leave
    a partial run that a later ``commit()`` would flush to disk (criterion #1 — a
    run is written whole or not at all, CR-01).
    """
    for page in run.pages:
        if not page.url_key:
            page.url_key = canonical_key(page.url)

    # `with conn:` is a transaction context manager — it COMMITs on a clean exit
    # and ROLLS BACK if the block raises, so a half-written run is never left in
    # an open transaction for a subsequent commit() to persist (CR-01).
    with conn:
        conn.execute(
            "INSERT INTO runs (id, started_at, target, schema_version, record_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(run.id),
                run.started_at.isoformat(),
                run.target,
                run.schema_version,
                run.model_dump_json(),
            ),
        )
        conn.executemany(
            "INSERT INTO page_results (run_id, record_json) VALUES (?, ?)",
            [(str(run.id), page.model_dump_json()) for page in run.pages],
        )


def read_run(conn: sqlite3.Connection, run_id: str) -> RunRecord:
    """Read a run back into a ``RunRecord`` (model_validate_json round-trip).

    Raises ``KeyError`` if no run with ``run_id`` exists.
    """
    row = conn.execute(
        "SELECT record_json FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no run with id {run_id!r}")
    return RunRecord.model_validate_json(row[0])
