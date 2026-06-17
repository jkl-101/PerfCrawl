"""Hybrid SQLite run store — write a run, read it back identically (criterion #1).

The store is the persistence half of the Phase 1 data contract. It implements
the D-07 "hybrid" design: each record is kept as a full-fidelity **JSON TEXT
blob** (the exact ``model_dump_json()`` bytes) so a run reads back byte-for-byte,
while the metrics that get queried/self-joined are exposed as **generated
columns** computed *from* that blob via ``json_extract`` — so a promoted column
can never drift from the source record.

Note: ``page_results`` is the intended cross-run self-join / promotion query
surface for LATER phases (D-07). In Phase 1 it is write-only — ``read_run``
reconstructs a ``RunRecord`` solely from ``runs.record_json`` — so the table is
populated on write but not yet queried by name through the store API. It is
correct forward design, not dead code (IN-03).

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
    the caller left it blank OR whitespace-only (D-01: the raw ``url`` is never
    mutated — only the derived key is set). The exact ``model_dump_json()`` bytes
    are written so the record reads back identically.

    The caller's ``RunRecord`` is NOT mutated (WR-06): key derivation happens on a
    deep copy, so a caller that reuses the object after ``write_run`` (re-serialize
    it, diff it against a snapshot, write it to a second store) still sees its
    original blank ``url_key`` values.

    ``url_key`` is the per-run canonical page identity: one page per key per run.
    Two pages sharing a key would silently collapse in any reader that buckets a
    run's pages by ``url_key`` (e.g. ``compute_deltas``'s ``{p.url_key: p}`` keeps
    only the last) — masking a regression or fabricating an improvement. A
    duplicate signals an upstream crawler bug, so this is rejected at WRITE time
    with a ``ValueError`` (the invariant then holds for every reader) rather than
    silently grouped/merged (WR-02).

    The whole write is wrapped in an explicit transaction (``with conn:``): the
    ``runs`` row and every ``page_results`` row commit together on success, or the
    transaction ROLLS BACK on any exception so a mid-write failure can never leave
    a partial run that a later ``commit()`` would flush to disk (criterion #1 — a
    run is written whole or not at all, CR-01).
    """
    # Derive keys on a deep copy so the caller's object is never mutated (WR-06).
    run = run.model_copy(deep=True)
    for page in run.pages:
        # Regenerate when the key is missing OR whitespace-only (WR-07): a
        # truthy-but-blank key like "   " would otherwise be stored verbatim into
        # the self-join column and never match the canonical key of the same
        # logical page in another run — silently breaking the cross-run delta.
        if not (page.url_key or "").strip():
            page.url_key = canonical_key(page.url)

    # Enforce the one-page-per-key-per-run invariant (WR-02). Checked AFTER key
    # derivation so it covers both caller-supplied and just-derived keys. A
    # duplicate canonical key means two of this run's pages share a cross-run
    # identity — a reader that buckets by url_key would drop all but one. Reject
    # it loudly (upstream crawler bug) instead of silently grouping/merging.
    seen: set[str] = set()
    for page in run.pages:
        if page.url_key in seen:
            raise ValueError(f"duplicate url_key in run: {page.url_key!r}")
        seen.add(page.url_key)

    # PRAGMA foreign_keys is PER-CONNECTION, not stored in the DB (WR-05). A
    # caller who init_db()s once and later opens a fresh connection for writes
    # gets foreign_keys=OFF by default, letting orphan page_results rows slip in
    # past the REFERENCES constraint. Re-assert it here (cheap, idempotent) so FK
    # enforcement holds on every write connection, not only the init_db one.
    conn.execute("PRAGMA foreign_keys = ON")

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


def read_previous_run(
    conn: sqlite3.Connection, target: str, before_started_at: str
) -> RunRecord | None:
    """Return the immediately-prior same-target run, or ``None`` (D-04 baseline).

    The HIST-02 regression layer needs the run to diff the *current* run against:
    the most recent earlier run for the SAME ``target``. This is the point lookup
    analog of ``read_run`` — same ``model_validate_json`` round-trip — but it
    returns ``None`` (NOT a ``KeyError``) when there is no prior run, because the
    very first audit of a site legitimately has no baseline (the caller then skips
    regression flagging; a first run never errors, never flags — D-04/D-14).

    ``before_started_at`` is the CURRENT run's ``started_at.isoformat()`` supplied
    by the caller (Plan 06). The ``started_at < ?`` filter is what keeps the
    current run from being selected as its own baseline (Pitfall 4): the lookup
    only ever sees runs strictly OLDER than the one being analyzed, and
    ``ORDER BY started_at DESC LIMIT 1`` picks the immediately-prior one.

    Security (threat T-06-06 / T-01-T): ``target`` and ``before_started_at`` are
    bound as ``?`` placeholders — never f-string / ``%`` / ``.format`` SQL — and
    there are no dynamic table/column names.
    """
    row = conn.execute(
        "SELECT record_json FROM runs WHERE target = ? AND started_at < ? "
        "ORDER BY started_at DESC LIMIT 1",
        (target, before_started_at),
    ).fetchone()
    if row is None:
        return None
    return RunRecord.model_validate_json(row[0])
