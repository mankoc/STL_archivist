"""Tests for Database Maintenance → Compact Database (SQLite VACUUM)."""
import numpy as np
from sqlalchemy import text

from app.database import vectors
from app.database.db import database_path, get_engine, init_db, session_scope
from app.database.models import Image, STLModel
from app.services.maintenance import (
    compact_database, database_file_bytes, database_free_bytes,
)


def _fill(n: int) -> None:
    with session_scope() as s:
        for i in range(n):
            s.add(STLModel(title="x" * 2000, archive_path=f"/lib/{i}.7z", archive_hash=str(i),
                           images=[Image(filename=f"{i}.jpg")]))
        s.flush()
        ids = [i for (i,) in s.execute(text("SELECT id FROM images"))]
        vectors.store(s, [(i, np.ones(512, np.float32)) for i in ids])


def test_database_path_none_for_memory():
    init_db(":memory:")
    assert database_path() is None


def test_compact_reclaims_space_and_keeps_data(tmp_path):
    db = tmp_path / "lib.db"
    init_db(str(db))
    assert database_path() == db
    _fill(2000)
    with session_scope() as s:
        s.execute(text("DELETE FROM models WHERE id % 10 != 0"))   # keep 200
    with session_scope() as s:
        assert database_free_bytes(s) > 0

    before, after, wal_truncated = compact_database(get_engine(), db)

    assert after == database_file_bytes(db) < before
    assert wal_truncated
    assert not db.with_name("lib.db-wal").exists() or db.with_name("lib.db-wal").stat().st_size == 0
    with session_scope() as s:
        assert database_free_bytes(s) == 0
        assert s.query(STLModel).count() == 200
        assert vectors.count(s) == 200          # sqlite-vec table survives VACUUM
        assert s.execute(text("PRAGMA temp_store")).scalar_one() == 2   # MEMORY restored
        assert s.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
