"""Golden tests: the native schema migration leaves a database exactly as Python's does.

Each case builds a database (fresh, or in an older layout), migrates one copy
with the Python steps in ``db.init_db`` and one with ``stl_native.migrate_database``,
then compares everything: ``sqlite_master`` (tables, indexes, views and their
SQL text), every row of every table (``image_vec`` and ``sqlite_stat1``
included) and the column declarations.
"""
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "migrate_database"):
    pytest.skip("stl_native predates migrate_database; rebuild it", allow_module_level=True)

import sqlite_vec

from app import native
from app.database import db
from app.utils import thumbnailer

ROOT = Path(__file__).resolve().parents[2]


def test_generated_schema_is_up_to_date():
    """rust/stl-core/src/storage/schema_sql.rs must match app/database/models.py."""
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_schema.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


LEGACY_FULL = """
CREATE TABLE categories (id INTEGER PRIMARY KEY, name VARCHAR(128) NOT NULL UNIQUE);
CREATE TABLE subcategories (id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL, name VARCHAR(128) NOT NULL);
CREATE TABLE sculptors (id INTEGER PRIMARY KEY, name VARCHAR(256) NOT NULL UNIQUE);
CREATE TABLE scales (id INTEGER PRIMARY KEY, name VARCHAR(64) NOT NULL UNIQUE);
CREATE TABLE models (
    id INTEGER PRIMARY KEY, title VARCHAR(512), author VARCHAR(256), source VARCHAR(128),
    archive_path TEXT NOT NULL UNIQUE, archive_hash VARCHAR(64) NOT NULL, warning TEXT,
    category_id INTEGER, subcategory_id INTEGER, sculptor_id INTEGER, scale_id INTEGER);
CREATE TABLE images (id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL, filename VARCHAR(512),
    thumbnail_path VARCHAR(512), thumbnail BLOB, dhash INTEGER, embedding BLOB, embedding_model TEXT);
INSERT INTO categories VALUES (1, 'Comics'), (2, 'Games');
INSERT INTO subcategories VALUES (1, 1, 'Marvel'), (2, 2, 'Zelda');
INSERT INTO sculptors VALUES (1, 'Sanix');
INSERT INTO scales VALUES (1, '32 mm'), (2, '1/35');
INSERT INTO models VALUES
    (1, 'Hulk', 'Sanix', 'patreon', 'F:/a/hulk.7z', 'h1', 'Archive inside archive', 1, 1, 1, 1),
    (2, 'Link', 'Bob', NULL, 'F:/a/link.zip', 'h2', NULL, 2, 2, NULL, 2),
    (3, 'Tank', NULL, NULL, 'F:/b/tank.rar', 'h3', 'Archive without images', NULL, NULL, NULL, NULL);
INSERT INTO images VALUES
    (1, 1, 'a.jpg', '1', NULL, 'abcdef0123', X'00000000', 'old-model'),
    (2, 1, 'b.jpg', '2', NULL, '0000000000000000', NULL, NULL),
    (3, 2, 'c.png', '3', NULL, 42, NULL, NULL);
"""

LEGACY_DHASH_ONLY = """
CREATE TABLE images (id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL, filename VARCHAR(512),
    thumbnail_path VARCHAR(512), thumbnail BLOB, dhash INTEGER);
INSERT INTO images VALUES (1, 1, 'a', '1', NULL, '2:00ff'), (2, 1, 'b', '2', NULL, '123456');
"""


def _legacy_with_vectors(dim_ok: bool) -> str:
    vec = "X'" + "00" * (512 * 4) + "'"
    model = "clip-vit-b32-onnx" if dim_ok else "some-other-model"
    return LEGACY_FULL + f"""
UPDATE images SET embedding = {vec}, embedding_model = '{model}' WHERE id = 2;
"""


CASES = {
    "fresh": None,
    "legacy full": LEGACY_FULL,
    "legacy dhash only": LEGACY_DHASH_ONLY,
    "legacy vectors moved": _legacy_with_vectors(True),
    "legacy vectors wrong model": _legacy_with_vectors(False),
}


def _build(path: Path, script):
    if script is not None:
        con = sqlite3.connect(path)
        con.executescript(script)
        con.commit()
        con.close()


def _migrate(path: Path, use_native: bool, monkeypatch):
    if use_native:
        monkeypatch.delenv("STL_NATIVE", raising=False)
    else:
        monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    assert (native.module() is not None) == use_native
    try:
        db.init_db(str(path))
    finally:
        thumbnailer.close_thumbnail_store()
        if db._engine is not None:
            db._engine.dispose()


def _dump(path: Path) -> dict:
    con = sqlite3.connect(path)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    try:
        master = sorted(con.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'image_vec_%'"))
        out = {"master": master}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                   "AND name NOT LIKE 'image_vec_%' ORDER BY name"):
            cols = con.execute(f'PRAGMA table_info("{name}")').fetchall()
            out[f"cols:{name}"] = cols
            out[f"rows:{name}"] = sorted(con.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
        return out
    finally:
        con.close()


@pytest.mark.parametrize("case", sorted(CASES))
def test_native_migration_matches_python(tmp_path, monkeypatch, case):
    dumps = {}
    for use_native in (False, True):
        d = tmp_path / ("native" if use_native else "python")
        d.mkdir()
        path = d / "stlLibrary.db"
        _build(path, CASES[case])
        _migrate(path, use_native, monkeypatch)
        _migrate(path, use_native, monkeypatch)    # a second start changes nothing more
        dumps[use_native] = _dump(path)
    py, rs = dumps[False], dumps[True]
    assert py.keys() == rs.keys()
    for key in py:
        assert py[key] == rs[key], key


def test_native_migration_is_used_for_file_databases(tmp_path, monkeypatch):
    calls = []
    real = stl_native.migrate_database
    monkeypatch.setattr(stl_native, "migrate_database", lambda p, *a: calls.append(p) or real(p, *a))
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()
    try:
        db.init_db(str(tmp_path / "lib.db"))
    finally:
        thumbnailer.close_thumbnail_store()
        db._engine.dispose()
    assert calls == [str(tmp_path / "lib.db")]
