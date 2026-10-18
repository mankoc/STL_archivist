"""Tests for CLIP embedding storage in the sqlite-vec ``image_vec`` table (native)."""
import sqlite3

import numpy as np
import pytest

from app.database import native_db
from app.database.db import init_db
from app.services import browser_data
from app.services.image_similarity import find_similar_models, load_model_vectors
from app.utils.image_embedding import EMBED_DIM, EMBED_MODEL_ID
from tests import factory


def _vec(*head) -> np.ndarray:
    """Unit vector of EMBED_DIM whose leading components are *head*."""
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    v[:len(head)] = head
    return v / np.linalg.norm(v)


def _model(name, n_images=1, missing=False) -> tuple[int, list[int]]:
    mid = factory.add_model(f"/lib/{name}.7z", name, title=name, is_missing=int(missing))
    return mid, [factory.add_image(mid, f"{name}_{i}.jpg", thumbnail_path=f"{name}{i}")
                 for i in range(n_images)]


def _store(items) -> int:
    return native_db.library().vectors_store(
        EMBED_DIM, [(i, np.asarray(v, np.float32).tobytes()) for i, v in items])


def _read_all():
    ids, mat = native_db.library().vectors_read_all(EMBED_DIM)
    ids = np.frombuffer(ids, dtype=np.int64)
    mat = np.frombuffer(mat, dtype=np.float32).reshape(-1, EMBED_DIM)
    order = np.argsort(ids)
    return ids[order], mat[order]


def _vec_ids():
    return _read_all()[0].tolist()


def test_store_replaces_and_skips_wrong_dimension():
    _mid, (img,) = _model("a")
    assert _store([(img, _vec(1))]) == 1
    assert _store([(img, _vec(0, 1))]) == 1               # replace, not duplicate
    assert _store([(img, np.ones(3, dtype=np.float32))]) == 0
    assert native_db.library().vectors_count() == 1
    ids, mat = _read_all()
    assert ids.tolist() == [img] and np.allclose(mat[0], _vec(0, 1))


def test_deleting_image_or_model_removes_vector():
    _a, (gone, keep) = _model("a", n_images=2)
    b, b_imgs = _model("b")
    _store([(i, _vec(1)) for i in (gone, keep, *b_imgs)])
    assert browser_data.delete_image(gone)
    browser_data.delete_models([b])
    assert _vec_ids() == [keep]


def test_prune_orphans():
    _mid, (img,) = _model("a")
    _store([(img, _vec(1)), (9999, _vec(1))])
    assert native_db.library().vectors_prune_orphans() == 1
    assert _vec_ids() == [img]


def test_find_similar_models_best_per_model():
    a, a_imgs = _model("a", n_images=2)
    b, (b_img,) = _model("b")
    _c, (c_img,) = _model("c")
    _store([
        (a_imgs[0], _vec(1, 0.5)),
        (a_imgs[1], _vec(1, 0.1)),   # A's best match
        (b_img, _vec(1, 0.6)),
        (c_img, _vec(0, 0, 1)),      # orthogonal, below threshold
    ])
    res, capped = find_similar_models(None, _vec(1), threshold=0.8)
    assert not capped
    assert [r[1] for r in res] == [a, b]
    assert res[0][0] == pytest.approx(float(_vec(1) @ _vec(1, 0.1)), abs=1e-5)
    assert tuple(res[0][2:4]) == ("a", "a.7z")


def test_find_similar_models_caps_to_closest_images():
    models = [_model(f"m{i}") for i in range(5)]
    _store([(imgs[0], _vec(1, 0.01 * i)) for i, (_m, imgs) in enumerate(models)])
    res, capped = find_similar_models(None, _vec(1), threshold=0.9, limit=3)
    assert capped
    assert [r[1] for r in res] == [m for m, _ in models[:3]]
    res, capped = find_similar_models(None, _vec(1), threshold=0.9, limit=10)
    assert not capped and len(res) == 5


def test_orphaned_vectors_are_skipped_but_count_towards_the_cap():
    models = [_model(f"m{i}") for i in range(3)]
    _store([(9001, _vec(1)), (9002, _vec(1))])            # nearest two: orphans
    _store([(imgs[0], _vec(1, 0.01)) for _m, imgs in models])
    res, capped = find_similar_models(None, _vec(1), threshold=0.9, limit=3)
    assert capped and len(res) == 1
    assert len(find_similar_models(None, _vec(1), threshold=0.9)[0]) == 3


def test_load_model_vectors_skips_missing_models_and_orphans():
    a, (a_img,) = _model("a")
    _g, (gone,) = _model("gone", missing=True)
    _store([(a_img, _vec(1)), (gone, _vec(1)), (9999, _vec(1))])
    mids, mat = load_model_vectors()
    assert mids.tolist() == [a]
    assert mat.shape == (1, EMBED_DIM) and np.allclose(mat[0], _vec(1))


def test_read_all_across_chunks_with_holes():
    """Vectors across several 1024-slot chunks, with deleted slots (bitmap holes)."""
    rng = np.random.default_rng(0)
    mid, _ = _model("big", n_images=0)
    with factory.connect() as con:
        imgs = [con.execute("INSERT INTO images (model_id, filename) VALUES (?, 'x.jpg')",
                            (mid,)).lastrowid for _ in range(2500)]
    vecs = {i: rng.standard_normal(EMBED_DIM).astype(np.float32) for i in imgs}
    _store(vecs.items())
    with factory.connect() as con:
        con.executemany("DELETE FROM images WHERE id = ?", [(i,) for i in imgs[::7]])
    native_db.library().vectors_prune_orphans()
    ids, mat = _read_all()
    kept = [i for k, i in enumerate(imgs) if k % 7]
    assert ids.tolist() == kept
    assert np.array_equal(mat, np.stack([vecs[i] for i in kept]))


def test_model_change_recreates_table(library_db, monkeypatch):
    _mid, (img,) = _model("a")
    _store([(img, _vec(1))])
    import app.utils.image_embedding as emb
    monkeypatch.setattr(emb, "EMBED_MODEL_ID", "another-model")
    init_db(str(library_db))
    assert native_db.library().vectors_count() == 0
    assert factory.scalar("SELECT model FROM vector_store_info") == "another-model"


def test_legacy_embeddings_are_migrated(tmp_path):
    sqlite_vec = pytest.importorskip("sqlite_vec")       # only to drop the vec0 table
    db = tmp_path / "legacy" / "lib.db"
    init_db(str(db))
    con = sqlite3.connect(db)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.executescript("""
        DROP TABLE image_vec;
        ALTER TABLE images ADD COLUMN embedding BLOB;
        ALTER TABLE images ADD COLUMN embedding_model VARCHAR(64);
        INSERT INTO models(id, title, archive_path, archive_hash, is_missing, to_print)
            VALUES (1, 'a', '/a.7z', 'h', 0, 0);
    """)
    con.executemany(
        "INSERT INTO images(id, model_id, filename, embedding, embedding_model) VALUES (?, 1, 'x', ?, ?)",
        [(1, _vec(1).tobytes(), EMBED_MODEL_ID),      # migrated
         (2, _vec(0, 1).tobytes(), "old-model"),      # stale model: dropped
         (3, b"\x00" * 12, EMBED_MODEL_ID),           # wrong size: dropped
         (4, None, None)])                            # never embedded
    con.commit()
    con.close()

    from app.database.vectors import legacy_embeddings_present
    assert legacy_embeddings_present(str(db))
    init_db(str(db))
    assert not legacy_embeddings_present(str(db))
    cols = {r[1] for r in factory.rows("PRAGMA table_info(images)")}
    assert "embedding" not in cols and "embedding_model" not in cols
    assert _vec_ids() == [1]
    assert find_similar_models(None, _vec(1), 0.99)[0][0][1] == 1
