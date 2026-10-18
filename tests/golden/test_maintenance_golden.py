"""Golden tests: native maintenance and vector operations match the Python ones.

Covers ``verify_archives``, ``find_duplicate_entries``, ``prune_images``,
``vectors.read_all``, ``find_similar_models`` and ``load_model_vectors``.
Read-only operations are compared on one database with the native path
switched off and on; the ones that write run on two identical copies.
"""
import random
import shutil
import sqlite3
import sys

import numpy as np
import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "verify_archives"):
    pytest.skip("stl_native predates native maintenance; rebuild it", allow_module_level=True)
if sys.platform != "win32":
    pytest.skip("native database access is Windows-only until SQLAlchemy is gone", allow_module_level=True)

from app import native
from app.database import native_db, vectors
from app.database.db import init_db, session_scope
from app.database.models import Image, STLModel
from app.services import image_similarity as IS
from app.services import maintenance as M
from app.utils import thumbnailer
from app.utils.image_embedding import EMBED_DIM


def _build(root, rng):
    """A library whose archives exist, are missing, changed size or came back."""
    files = root / "files"
    files.mkdir()
    db = root / "a" / "stlLibrary.db"
    db.parent.mkdir()
    init_db(str(db))
    with session_scope() as s:
        for i in range(60):
            kind = rng.choice(["ok", "missing", "modified", "back", "flagged", "nopath"])
            name = rng.choice([f"m{i}.7z", "same.zip", f"d{i % 4}.rar"])
            path = files / f"{i}" / name
            path.parent.mkdir()
            size = 100 + i
            if kind in ("ok", "modified", "back"):
                path.write_bytes(b"x" * (size if kind != "modified" else size + 1))
            ahash = rng.choice([f"h{i}", "dup-hash", "fallback:123", "", f"h{i % 9}"])
            m = STLModel(title=rng.choice([f"T{i}", None]),
                         archive_path=str(path) if kind != "nopath" else f"Z:/gone/{i}/{name}",
                         archive_hash=ahash, archive_size=rng.choice([size, size, None]),
                         is_missing=kind in ("back", "flagged"))
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 5)):
                s.add(Image(model_id=m.id, filename=f"{k}.jpg", thumbnail_path=str(m.id * 10 + k)))
        s.flush()
        img_ids = [i for (i,) in s.query(Image.id)]
        vecs = np.random.default_rng(rng.randint(0, 999)).normal(size=(len(img_ids), EMBED_DIM)).astype(np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        vectors.store(s, list(zip(img_ids, vecs)))
    thumbnailer.close_thumbnail_store()
    return db, vecs


@pytest.fixture
def lib_db(tmp_path, monkeypatch):
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()
    db, vecs = _build(tmp_path, random.Random(4))
    con = sqlite3.connect(db)
    con.execute("UPDATE models SET updated_at = ?", (SENTINEL,))
    con.commit()
    con.close()
    copy = tmp_path / "b" / "stlLibrary.db"
    copy.parent.mkdir()
    src, dst = sqlite3.connect(db), sqlite3.connect(copy)
    src.backup(dst)
    src.close()
    dst.close()
    yield db, copy, vecs
    native_db.reset()
    thumbnailer.close_thumbnail_store()


def _python(monkeypatch):
    monkeypatch.setattr(native_db, "library", lambda: None)


SENTINEL = "2000-01-01 00:00:00.000000"


def _dump(path):
    """models and images rows; ``updated_at`` as "changed or not" (runs differ in time)."""
    con = sqlite3.connect(path)
    out = {}
    for t in ("models", "images"):
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if "updated_at" in cols:
            k = cols.index("updated_at")
            rows = [r[:k] + (r[k] != SENTINEL,) + r[k + 1:] for r in rows]
        out[t] = sorted(rows, key=repr)
    con.close()
    return out


def test_verify_matches(lib_db, monkeypatch):
    db, copy, _ = lib_db
    init_db(str(copy))
    assert native_db.library() is not None
    logs_rs = []
    rs = M.verify_archives(log=logs_rs.append)
    init_db(str(db))
    with monkeypatch.context() as mp:
        _python(mp)
        logs_py = []
        py = M.verify_archives(log=logs_py.append)
    assert rs == py
    assert logs_rs == logs_py
    thumbnailer.close_thumbnail_store()
    native_db.reset()
    assert _dump(db) == _dump(copy)                  # same missing flags cleared


def test_verify_stops(lib_db):
    db, _copy, _ = lib_db
    init_db(str(db))
    assert M.verify_archives(should_stop=lambda: True) == []


def test_duplicates_match(lib_db, monkeypatch):
    db, _copy, _ = lib_db
    init_db(str(db))
    rs = M.find_duplicate_entries()
    with monkeypatch.context() as mp:
        _python(mp)
        py = M.find_duplicate_entries()
    assert rs == py and len(rs) > 4


@pytest.mark.parametrize("keep", [0, 2])
def test_prune_matches(lib_db, monkeypatch, keep):
    db, copy, _ = lib_db
    init_db(str(copy))
    rs = M.prune_images(keep, batch_size=7)
    init_db(str(db))
    with monkeypatch.context() as mp:
        _python(mp)
        py = M.prune_images(keep, batch_size=7)
    assert rs == py and rs[1] > 0
    thumbnailer.close_thumbnail_store()
    native_db.reset()
    assert _dump(db) == _dump(copy)
    con_a, con_b = sqlite3.connect(db), sqlite3.connect(copy)
    for con in (con_a, con_b):
        con.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(con)
    ids_a = sorted(r[0] for r in con_a.execute("SELECT image_id FROM image_vec"))
    ids_b = sorted(r[0] for r in con_b.execute("SELECT image_id FROM image_vec"))
    assert ids_a == ids_b                             # vectors left with their images


def test_vectors_match(lib_db, monkeypatch):
    db, _copy, vecs = lib_db
    init_db(str(db))
    with session_scope() as s:
        ids_rs, mat_rs = vectors.read_all(s)
        mids_rs, mmat_rs = IS.load_model_vectors(s)
        sims_rs = [IS.find_similar_models(s, vecs[k], thr, lim)
                   for k in (0, 5, 17) for thr in (0.99, 0.0, -1.0) for lim in (1, 10, 1024)]
    with monkeypatch.context() as mp:
        _python(mp)
        with session_scope() as s:
            ids_py, mat_py = vectors.read_all(s)
            mids_py, mmat_py = IS.load_model_vectors(s)
            sims_py = [IS.find_similar_models(s, vecs[k], thr, lim)
                       for k in (0, 5, 17) for thr in (0.99, 0.0, -1.0) for lim in (1, 10, 1024)]
    assert np.array_equal(ids_rs, ids_py) and np.array_equal(mat_rs, mat_py)
    assert np.array_equal(mids_rs, mids_py) and np.array_equal(mmat_rs, mmat_py)
    mat_rs[0, 0] = 1.0                                 # writable, as the Python arrays are
    assert [(list(map(tuple, r)), c) for r, c in sims_rs] == [(list(map(tuple, r)), c) for r, c in sims_py]
