"""Image embedding storage in a sqlite-vec ``vec0`` virtual table.

CLIP embeddings live in ``image_vec`` (one row per ``Image.id``), not on the
``images`` table.  vec0 stores the vectors in contiguous chunks and runs
nearest-neighbour (KNN) search in C, so Find by Image no longer streams every
vector into Python.

Two consequences of vec0 being a virtual table from a loadable extension:

* **The extension must be loaded on every connection** before ``image_vec`` can
  be read or written.  :func:`on_connect` does this from the engine's
  ``connect`` event.  If loading fails (a Python built without extension
  support), the rest of the app keeps working and :func:`available` returns
  False so the embedding features disable themselves.
* **Virtual tables have no foreign keys**, so deleting an image does not
  cascade.  A ``TEMP`` trigger, created per connection in :func:`on_connect`,
  deletes the matching vector.  It is deliberately not a persistent trigger:
  that would make every image delete fail with "no such module: vec0" in any
  connection without the extension (DB Browser, a restored backup).  Rows
  orphaned by such outside deletes are swept by :func:`prune_orphans`.

Every vector in the table comes from one model.  ``vector_store_info`` records
which (``EMBED_MODEL_ID``, ``EMBED_DIM``); when the code's model changes the
table is recreated empty.  A per-row model column would cost ~65% on KNN
search and ~4x on full scans for no benefit.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Iterable

from sqlalchemy import Column, Integer, LargeBinary, MetaData, Table, select, text

from app.utils.image_embedding import EMBED_DIM, EMBED_MODEL_ID, to_blob

logger = logging.getLogger(__name__)

TABLE = "image_vec"
_INFO = "vector_store_info"

# sqlite-vec's hard ceiling on ``k`` in a KNN query.
MAX_KNN = 4096

# Core Table for plain (non-KNN) queries.  Kept out of ``Base.metadata`` so
# ``create_all`` never tries to create it as an ordinary table.
image_vec = Table(
    TABLE, MetaData(),
    Column("image_id", Integer, primary_key=True),
    Column("embedding", LargeBinary),
)

_CREATE_TABLE = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING vec0("
    f"image_id INTEGER PRIMARY KEY, "
    f"embedding float[{EMBED_DIM}] distance_metric=cosine)"
)

_CREATE_TRIGGER = (
    f"CREATE TEMP TRIGGER IF NOT EXISTS {TABLE}_image_deleted "
    f"AFTER DELETE ON main.images BEGIN "
    f"DELETE FROM {TABLE} WHERE image_id = OLD.id; END"
)

_available = False
_warned = False


def available() -> bool:
    """True if sqlite-vec loaded on the current engine's connections."""
    return _available


def on_connect(dbapi_conn) -> None:
    """Load sqlite-vec into a new DBAPI connection and add the delete trigger."""
    global _available, _warned
    try:
        import sqlite_vec
        dbapi_conn.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_conn)
        finally:
            dbapi_conn.enable_load_extension(False)
    except Exception as exc:
        if not _warned:
            logger.warning("sqlite-vec unavailable, AI image embeddings disabled: %s", exc)
            _warned = True
        _available = False
        return
    _available = True
    _create_trigger(dbapi_conn)


def _create_trigger(dbapi_conn) -> None:
    """Add the TEMP delete trigger; skipped until both tables exist (fresh DB)."""
    cur = dbapi_conn.cursor()
    try:
        names = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('images', ?)", (TABLE,))}
        if names == {"images", TABLE}:
            cur.execute(_CREATE_TRIGGER)
    finally:
        cur.close()


def legacy_embeddings_present(db_path: str) -> bool:
    """True if *db_path* still has the pre-sqlite-vec ``images.embedding`` column.

    Lets startup warn that the one-time move is about to run.  Opens the file
    read-only and never creates it.
    """
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return any(r[1] == "embedding" for r in con.execute("PRAGMA table_info(images)"))
        finally:
            con.close()
    except sqlite3.Error:
        return False


def ensure_schema(engine) -> None:
    """Create ``image_vec`` and move legacy ``images.embedding`` vectors into it.

    Older databases kept each vector as a BLOB on ``images`` (``embedding`` +
    ``embedding_model``).  On the first start with sqlite-vec available, current
    vectors are copied into ``image_vec`` and both columns are dropped, in one
    transaction, then the file is vacuumed to give the space back.  Without
    sqlite-vec nothing is touched, so no data is lost.
    """
    if not _available:
        return
    with engine.begin() as conn:
        conn.exec_driver_sql(f"CREATE TABLE IF NOT EXISTS {_INFO} (model TEXT NOT NULL, dim INTEGER NOT NULL)")
        stored = conn.exec_driver_sql(f"SELECT model, dim FROM {_INFO}").first()
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (TABLE,)).first()
        if exists and tuple(stored or ()) != (EMBED_MODEL_ID, EMBED_DIM):
            logger.warning("Embedding model changed from %s to %s; stored vectors discarded",
                           tuple(stored or ()), (EMBED_MODEL_ID, EMBED_DIM))
            conn.exec_driver_sql(f"DROP TABLE {TABLE}")
        conn.exec_driver_sql(_CREATE_TABLE)
        conn.exec_driver_sql(f"DELETE FROM {_INFO}")
        conn.exec_driver_sql(f"INSERT INTO {_INFO} VALUES (?, ?)", (EMBED_MODEL_ID, EMBED_DIM))

        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(images)")}
        migrated = "embedding" in cols
        if migrated:
            _migrate_legacy(conn, has_model_col="embedding_model" in cols)
        # The connection that created the table was opened before it existed.
        _create_trigger(conn.connection.dbapi_connection)

    if migrated:
        t0 = time.perf_counter()
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("VACUUM")
        logger.info("Vacuumed database after embedding move in %.1f s", time.perf_counter() - t0)


def _migrate_legacy(conn, has_model_col: bool) -> None:
    t0 = time.perf_counter()
    model_filter = "AND embedding_model = :m" if has_model_col else ""
    moved = conn.execute(text(
        f"INSERT INTO {TABLE}(image_id, embedding) "
        f"SELECT id, embedding FROM images "
        f"WHERE embedding IS NOT NULL AND length(embedding) = :n {model_filter}"
    ), {"m": EMBED_MODEL_ID, "n": EMBED_DIM * 4}).rowcount
    t1 = time.perf_counter()
    conn.exec_driver_sql("ALTER TABLE images DROP COLUMN embedding")
    if has_model_col:
        conn.exec_driver_sql("ALTER TABLE images DROP COLUMN embedding_model")
    logger.info("Moved %d image embedding(s) into %s: copy %.1f s, drop columns %.1f s",
                moved, TABLE, t1 - t0, time.perf_counter() - t1)


def store(session, items: Iterable[tuple[int, object]]) -> int:
    """Insert or replace the vectors for ``(image_id, vector)`` pairs.

    vec0 has no ``INSERT OR REPLACE``, so each id is deleted first.  Vectors of
    the wrong dimension are skipped.  Runs in *session*'s transaction.
    """
    rows = []
    for image_id, vec in items:
        blob = to_blob(vec)
        if len(blob) != EMBED_DIM * 4:
            logger.warning("Skipping embedding for image %s: %d dimensions, expected %d",
                           image_id, len(blob) // 4, EMBED_DIM)
            continue
        rows.append({"id": image_id, "e": blob})
    if not rows:
        return 0
    session.execute(text(f"DELETE FROM {TABLE} WHERE image_id = :id"),
                    [{"id": r["id"]} for r in rows])
    session.execute(text(f"INSERT INTO {TABLE}(image_id, embedding) VALUES (:id, :e)"), rows)
    return len(rows)


# sqlite-vec versions whose shadow-table layout read_all() has been checked
# against.  tests/test_vectors.py fails if a new version changes it.
_CHUNK_LAYOUT_VERSIONS = frozenset({"v0.1.6", "v0.1.7", "v0.1.8", "v0.1.9"})


def read_all(session):
    """Every stored vector as ``(image_ids, matrix)``: int64 ``(N,)``, float32 ``(N, EMBED_DIM)``.

    For whole-table work (low-threshold search, all-pairs duplicates).  Reading
    vec0 row by row costs ~7 µs a row (2.4 s for 334k), so this reads
    sqlite-vec's chunk shadow tables directly (~0.5 s).  That layout is
    internal, so the fast path runs only for checked versions and only if the
    rows it decodes match ``count(*)``; otherwise it uses the public scan.
    """
    try:
        out = _read_chunks(session)
        if out is not None:
            return out
    except Exception:
        logger.warning("Fast read of %s failed; using the slower scan", TABLE, exc_info=True)
    return _read_rows(session)


def _read_chunks(session):
    import numpy as np

    version = session.execute(text("SELECT vec_version()")).scalar_one()
    if version not in _CHUNK_LAYOUT_VERSIONS:
        logger.info("sqlite-vec %s: chunk layout unchecked, using the slower scan", version)
        return None
    expected = count(session)
    ids = np.empty(expected, dtype=np.int64)
    mat = np.empty((expected, EMBED_DIM), dtype=np.float32)
    filled = 0
    # Each chunk holds `size` slots: int64 rowids (the image ids), a validity
    # bitmap (bit i set = slot i in use, LSB first), and size×dim float32s.
    chunks = session.execute(text(
        f"SELECT c.size, c.rowids, c.validity, v.vectors "
        f"FROM {TABLE}_chunks c JOIN {TABLE}_vector_chunks00 v ON v.rowid = c.chunk_id"
    ).execution_options(yield_per=8))
    for size, rowids, validity, vecs in chunks:
        if (len(rowids) != size * 8 or len(validity) != (size + 7) // 8
                or len(vecs) != size * EMBED_DIM * 4):
            logger.warning("Unexpected %s chunk layout; using the slower scan", TABLE)
            return None
        valid = np.unpackbits(np.frombuffer(validity, dtype=np.uint8),
                              bitorder="little")[:size].astype(bool)
        n = int(valid.sum())
        if filled + n > expected:
            return None
        rid = np.frombuffer(rowids, dtype=np.int64)
        block = np.frombuffer(vecs, dtype=np.float32).reshape(size, EMBED_DIM)
        if n == size:      # full chunk (the usual case): copy once, no mask temp
            ids[filled:filled + n] = rid
            mat[filled:filled + n] = block
        else:
            ids[filled:filled + n] = rid[valid]
            mat[filled:filled + n] = block[valid]
        filled += n
    if filled != expected:
        logger.warning("%s chunks held %d vectors, expected %d; using the slower scan",
                       TABLE, filled, expected)
        return None
    return ids, mat


def _read_rows(session):
    import numpy as np

    expected = count(session)
    ids = np.empty(expected, dtype=np.int64)
    mat = np.empty((expected, EMBED_DIM), dtype=np.float32)
    n = 0
    stmt = select(image_vec.c.image_id, image_vec.c.embedding).execution_options(yield_per=5000)
    for image_id, blob in session.execute(stmt):
        if n == expected:
            break
        ids[n] = image_id
        mat[n] = np.frombuffer(blob, dtype=np.float32)
        n += 1
    return ids[:n], mat[:n]


def current_ids():
    """SELECT of the image ids that have an embedding."""
    return select(image_vec.c.image_id)


def prune_orphans(session) -> int:
    """Delete vectors whose image no longer exists (deleted outside the app)."""
    return session.execute(text(
        f"DELETE FROM {TABLE} WHERE image_id NOT IN (SELECT id FROM images)"
    )).rowcount


def count(session) -> int:
    """Number of stored vectors."""
    return session.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
