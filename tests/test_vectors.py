"""Tests for CLIP embedding storage in the sqlite-vec ``image_vec`` table."""
import sqlite3

import numpy as np
import pytest
from sqlalchemy import text

from app.database import vectors
from app.database.db import init_db, session_scope
from app.database.models import Image, STLModel
from app.services.image_similarity import find_similar_models, load_model_vectors
from app.utils.image_embedding import EMBED_DIM, EMBED_MODEL_ID


def _vec(*head) -> np.ndarray:
    """Unit vector of EMBED_DIM whose leading components are *head*."""
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    v[:len(head)] = head
    return v / np.linalg.norm(v)


def _model(session, name, n_images=1, missing=False):
    m = STLModel(title=name, archive_path=f"/lib/{name}.7z", archive_hash=name, is_missing=missing)
    m.images = [Image(filename=f"{name}_{i}.jpg", thumbnail_path=f"{name}{i}") for i in range(n_images)]
    session.add(m)
    session.flush()
    return m


def _vec_ids(session):
    return sorted(r[0] for r in session.execute(text("SELECT image_id FROM image_vec")))


def test_extension_loads():
    assert vectors.available()


def test_store_replaces_and_skips_wrong_dimension():
    with session_scope() as s:
        img = _model(s, "a").images[0]
        assert vectors.store(s, [(img.id, _vec(1))]) == 1
        assert vectors.store(s, [(img.id, _vec(0, 1))]) == 1  # replace, not duplicate
        assert vectors.store(s, [(img.id, np.ones(3, dtype=np.float32))]) == 0
        assert vectors.count(s) == 1
        (blob,) = s.execute(text("SELECT embedding FROM image_vec")).one()
        assert np.allclose(np.frombuffer(blob, dtype=np.float32), _vec(0, 1))


def test_deleting_image_or_model_removes_vector():
    with session_scope() as s:
        a = _model(s, "a", n_images=2)
        b = _model(s, "b")
        vectors.store(s, [(i.id, _vec(1)) for i in a.images + b.images])
        keep = a.images[1].id
        s.delete(a.images[0])                 # ORM delete of one image
        s.flush()
        s.execute(text("DELETE FROM models WHERE id = :id"), {"id": b.id})  # FK cascade
        assert _vec_ids(s) == [keep]


def test_prune_orphans():
    with session_scope() as s:
        img = _model(s, "a").images[0]
        vectors.store(s, [(img.id, _vec(1)), (9999, _vec(1))])
        assert vectors.prune_orphans(s) == 1
        assert _vec_ids(s) == [img.id]


def test_current_ids_drives_missing_query():
    with session_scope() as s:
        a, b = _model(s, "a").images[0], _model(s, "b").images[0]
        vectors.store(s, [(a.id, _vec(1))])
        missing = s.query(Image.id).filter(Image.id.notin_(vectors.current_ids())).all()
        assert [r[0] for r in missing] == [b.id]


def test_find_similar_models_best_per_model():
    with session_scope() as s:
        a = _model(s, "a", n_images=2)
        b = _model(s, "b")
        c = _model(s, "c")
        vectors.store(s, [
            (a.images[0].id, _vec(1, 0.5)),
            (a.images[1].id, _vec(1, 0.1)),   # A's best match
            (b.images[0].id, _vec(1, 0.6)),
            (c.images[0].id, _vec(0, 0, 1)),  # orthogonal, below threshold
        ])
        res, capped = find_similar_models(s, _vec(1), threshold=0.8)
    assert not capped
    assert [r[1] for r in res] == [a.id, b.id]
    assert res[0][0] == pytest.approx(float(_vec(1) @ _vec(1, 0.1)), abs=1e-5)
    assert res[0][2:4] == ("a", "a.7z")


def test_find_similar_models_caps_to_closest_images():
    with session_scope() as s:
        imgs = [_model(s, f"m{i}").images[0] for i in range(5)]
        vectors.store(s, [(img.id, _vec(1, 0.01 * i)) for i, img in enumerate(imgs)])
        res, capped = find_similar_models(s, _vec(1), threshold=0.9, limit=3)
        assert capped
        assert [r[1] for r in res] == [img.model_id for img in imgs[:3]]
        res, capped = find_similar_models(s, _vec(1), threshold=0.9, limit=10)
        assert not capped and len(res) == 5


def test_load_model_vectors_skips_missing_models_and_orphans():
    with session_scope() as s:
        a = _model(s, "a").images[0]
        gone = _model(s, "gone", missing=True).images[0]
        vectors.store(s, [(a.id, _vec(1)), (gone.id, _vec(1)), (9999, _vec(1))])
        mids, mat = load_model_vectors(s)
    assert mids.tolist() == [a.model_id]
    assert mat.shape == (1, EMBED_DIM) and np.allclose(mat[0], _vec(1))


def _sorted(ids, mat):
    order = np.argsort(ids)
    return ids[order], mat[order]


def _fill(s, n):
    """Store n random vectors across several 1024-slot chunks, then punch holes."""
    rng = np.random.default_rng(0)
    m = _model(s, "big", n_images=n)
    vectors.store(s, [(img.id, rng.standard_normal(EMBED_DIM).astype(np.float32)) for img in m.images])
    for img in m.images[::7]:            # deleted slots leave validity-bitmap holes
        s.delete(img)
    s.flush()


def test_read_all_chunk_path_matches_public_scan():
    with session_scope() as s:
        _fill(s, 2500)
        fast = vectors._read_chunks(s)
        slow = vectors._read_rows(s)
    # Fails when a sqlite-vec upgrade is installed whose layout is unchecked:
    # verify this test passes, then add the version to _CHUNK_LAYOUT_VERSIONS.
    assert fast is not None, "chunk fast path not taken for this sqlite-vec version"
    (fi, fm), (si, sm) = _sorted(*fast), _sorted(*slow)
    assert len(fi) == 2500 - len(range(0, 2500, 7))
    assert np.array_equal(fi, si) and np.array_equal(fm, sm)


def test_read_all_falls_back_when_layout_unchecked_or_count_differs(monkeypatch):
    with session_scope() as s:
        _fill(s, 50)
        expected = _sorted(*vectors._read_rows(s))
        monkeypatch.setattr(vectors, "_CHUNK_LAYOUT_VERSIONS", frozenset())
        got = _sorted(*vectors.read_all(s))
        assert np.array_equal(got[0], expected[0]) and np.array_equal(got[1], expected[1])
        monkeypatch.undo()
        real_count = vectors.count
        monkeypatch.setattr(vectors, "count", lambda sess: real_count(sess) + 1)
        assert vectors._read_chunks(s) is None


def _make_legacy_db(path):
    """A database in the pre-sqlite-vec layout, with vectors on ``images``."""
    import sqlite_vec
    init_db(str(path))
    con = sqlite3.connect(path)
    con.enable_load_extension(True)
    sqlite_vec.load(con)  # needed to drop the vec0 table
    con.executescript("""
        DROP TABLE image_vec;
        ALTER TABLE images ADD COLUMN embedding BLOB;
        ALTER TABLE images ADD COLUMN embedding_model VARCHAR(64);
        INSERT INTO models(id, title, archive_path, archive_hash, is_missing, to_print)
            VALUES (1, 'a', '/a.7z', 'h', 0, 0);
    """)
    rows = [
        (1, _vec(1).tobytes(), EMBED_MODEL_ID),      # migrated
        (2, _vec(0, 1).tobytes(), "old-model"),      # stale model: dropped
        (3, b"\x00" * 12, EMBED_MODEL_ID),           # wrong size: dropped
        (4, None, None),                             # never embedded
    ]
    con.executemany(
        "INSERT INTO images(id, model_id, filename, embedding, embedding_model) VALUES (?, 1, 'x', ?, ?)",
        rows)
    con.commit()
    con.close()


def test_orphaned_vectors_are_skipped_but_count_towards_the_cap():
    with session_scope() as s:
        imgs = [_model(s, f"m{i}").images[0] for i in range(3)]
        vectors.store(s, [(9001, _vec(1)), (9002, _vec(1))])  # nearest two: orphans
        vectors.store(s, [(img.id, _vec(1, 0.01)) for img in imgs])
        res, capped = find_similar_models(s, _vec(1), threshold=0.9, limit=3)
        assert capped and len(res) == 1
        assert len(find_similar_models(s, _vec(1), threshold=0.9)[0]) == 3


def test_model_change_recreates_table(tmp_path, monkeypatch):
    db = tmp_path / "lib.db"
    init_db(str(db))
    with session_scope() as s:
        vectors.store(s, [(_model(s, "a").images[0].id, _vec(1))])
    monkeypatch.setattr(vectors, "EMBED_MODEL_ID", "another-model")
    init_db(str(db))
    with session_scope() as s:
        assert vectors.count(s) == 0
        assert s.execute(text("SELECT model FROM vector_store_info")).scalar_one() == "another-model"


def test_legacy_embeddings_are_migrated(tmp_path):
    db = tmp_path / "lib.db"
    _make_legacy_db(db)
    assert vectors.legacy_embeddings_present(str(db))
    init_db(str(db))
    assert not vectors.legacy_embeddings_present(str(db))
    with session_scope() as s:
        cols = {r[1] for r in s.execute(text("PRAGMA table_info(images)"))}
        assert "embedding" not in cols and "embedding_model" not in cols
        assert _vec_ids(s) == [1]
        assert find_similar_models(s, _vec(1), 0.99)[0][0][1] == 1


def test_without_extension_app_still_works_and_keeps_legacy_data(tmp_path, monkeypatch):
    db = tmp_path / "lib.db"
    _make_legacy_db(db)
    import sqlite_vec
    monkeypatch.setattr(sqlite_vec, "load", lambda conn: (_ for _ in ()).throw(sqlite3.OperationalError("nope")))
    init_db(str(db))
    assert not vectors.available()
    with session_scope() as s:
        cols = {r[1] for r in s.execute(text("PRAGMA table_info(images)"))}
        assert "embedding" in cols                   # nothing migrated or dropped
        s.execute(text("DELETE FROM images WHERE id = 4"))  # no trigger to trip over
