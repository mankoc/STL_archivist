"""Tests for Database Maintenance → Compact Database (SQLite VACUUM)."""
import numpy as np
import pytest

from app.database import native_db
from app.database.db import database_path, init_db
from app.services.maintenance import (
    compact_database, database_file_bytes, database_free_bytes,
)
from tests import factory


def _fill(n: int) -> None:
    with factory.connect() as con:
        for i in range(n):
            mid = con.execute("INSERT INTO models (title, archive_path, archive_hash, is_missing, to_print) "
                              "VALUES (?, ?, ?, 0, 0)", ("x" * 2000, f"/lib/{i}.7z", str(i))).lastrowid
            con.execute("INSERT INTO images (model_id, filename) VALUES (?, ?)", (mid, f"{i}.jpg"))
    ids = [r[0] for r in factory.rows("SELECT id FROM images")]
    native_db.library().vectors_store(512, [(i, np.ones(512, np.float32).tobytes()) for i in ids])


def test_in_memory_database_is_refused():
    with pytest.raises((ValueError, RuntimeError)):
        init_db(":memory:")


def test_compact_reclaims_space_and_keeps_data(library_db):
    db = library_db
    assert database_path() == db
    _fill(2000)
    factory.execute("DELETE FROM models WHERE id % 10 != 0")   # keep 200
    # plain sqlite3 has no vec0 delete trigger (native connections do)
    assert native_db.library().vectors_prune_orphans() == 1800
    assert database_free_bytes() > 0

    before, after, wal_truncated = compact_database(db)

    assert after == database_file_bytes(db) < before
    assert wal_truncated
    wal = db.with_name(db.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    assert database_free_bytes() == 0
    assert factory.count("models") == 200
    assert native_db.library().vectors_count() == 200       # sqlite-vec table survives VACUUM
    assert factory.scalar("PRAGMA integrity_check") == "ok"
