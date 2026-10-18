"""Tests for app.services.image_duplicates (Find Duplicate Archives → Images)."""
import io
from collections import defaultdict
from itertools import combinations

import numpy as np
import pytest
from PIL import Image as PILImage

import app.services.image_duplicates as D
from app.database import native_db
from app.utils.image_embedding import EMBED_DIM
from app.utils.image_hash import format_dhash, split_dhashes
from app.utils.thumbnailer import write_raw_thumbnail
from tests import factory

rng = np.random.default_rng(7)


@pytest.fixture(autouse=True)
def tiny_tiles(monkeypatch):
    # Tiles smaller than the data so tile and triangle boundaries are exercised.
    monkeypatch.setattr(D, "_DHASH_TILE", (3, 5))
    monkeypatch.setattr(D, "_EMBED_TILE", (4, 6))


@pytest.fixture(autouse=True, params=["python", "native"])
def implementation(request, monkeypatch):
    """Run every test against the NumPy scans and the Rust ones."""
    if request.param == "python":
        monkeypatch.setattr(D, "_native_pairs", lambda *a, **k: None)
    return request.param


def _flip(h: int, nbits: int) -> int:
    for bit in rng.choice(128, nbits, replace=False):
        h ^= 1 << int(bit)
    return h


# ── scans ────────────────────────────────────────────────────────────────────

def test_near_dhash_pairs_match_brute_force():
    base = [int(rng.integers(0, 2**63)) << 65 | int(rng.integers(0, 2**63)) for _ in range(12)]
    hashes = base + [_flip(h, k) for h, k in zip(base, (1, 3, 6, 9, 12, 20))]
    hi, lo = split_dhashes([format_dhash(h) for h in hashes])
    a, b, d = D.near_dhash_pairs(hi, lo, 9, workers=3)
    got = {(int(x), int(y)): int(z) for x, y, z in zip(a, b, d)}
    want = {(i, j): bin(hashes[i] ^ hashes[j]).count("1")
            for i, j in combinations(range(len(hashes)), 2)
            if bin(hashes[i] ^ hashes[j]).count("1") <= 9}
    assert got == want and len(want) >= 4


def test_near_embedding_pairs_match_full_matrix():
    v = rng.standard_normal((23, 8)).astype(np.float32)
    v[5] = v[2] + 0.05
    v[17] = v[2] + 0.04
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    a, b, s = D.near_embedding_pairs(v, 0.9, workers=2)
    sims = v @ v.T
    want = {(i, j) for i, j in combinations(range(23), 2) if sims[i, j] >= 0.9}
    assert {(int(x), int(y)) for x, y in zip(a, b)} == want and len(want) >= 3
    assert np.allclose(s, sims[a, b], atol=1e-5)


def test_stop_interrupts_scan():
    hi, lo = split_dhashes([format_dhash(i) for i in range(40)])
    with pytest.raises(D.Stopped):
        D.near_dhash_pairs(hi, lo, 4, should_stop=lambda: True)


# ── aggregation vs a slow reference ──────────────────────────────────────────

def _reference(n_items, img_item, img_model, edges, perfect, higher, max_models):
    models_of = defaultdict(set)
    for it, m in zip(img_item, img_model):
        models_of[it].add(m)
    nbrs = defaultdict(set)
    for u, v, _s in edges:
        nbrs[u].add(v); nbrs[v].add(u)
    shared = {u for u in range(n_items)
              if len(models_of[u].union(*(models_of[v] for v in nbrs[u]))) > max_models}
    imgs = list(zip(img_item, img_model))
    score = {}
    for u, v, s in edges:
        score[(u, v)] = score[(v, u)] = s
    out = {}
    for (iu, mu), (iv, mv) in combinations(imgs, 2):
        if mu == mv or iu in shared or iv in shared:
            continue
        s = perfect if iu == iv else score.get((iu, iv))
        if s is None:
            continue
        key = (min(mu, mv), max(mu, mv))
        c, best = out.get(key, (0, None))
        best = s if best is None else (max(best, s) if higher else min(best, s))
        out[key] = (c + 1, best)
    return out, len(shared)


@pytest.mark.parametrize("seed", range(40))
def test_model_pairs_matches_reference(seed):
    r = np.random.default_rng(seed)
    n_items, n_imgs = int(r.integers(2, 20)), int(r.integers(2, 70))
    max_models = int(r.choice([1, 2, 3, 5, 100]))
    img_item = r.integers(0, n_items, n_imgs)
    img_model = r.integers(100, 100 + int(r.integers(2, 14)), n_imgs)
    edges = [(int(u), int(v), int(r.integers(0, 8)))
             for u, v in combinations(range(n_items), 2) if r.random() < 0.2]
    if not edges:
        edges = [(0, 1, 3)]
    a = np.array([e[0] for e in edges]); b = np.array([e[1] for e in edges])
    s = np.array([e[2] for e in edges], np.int16)
    ma, mb, pairs, best, skipped = D.model_pairs(
        n_items, img_item, img_model, a, b, s, 0, False, max_models, chunk=7)
    got = {(int(x), int(y)): (int(p), int(q)) for x, y, p, q in zip(ma, mb, pairs, best)}
    want, want_skipped = _reference(n_items, img_item.tolist(), img_model.tolist(), edges, 0,
                                    False, max_models)
    assert got == want
    assert skipped == want_skipped


# ── dhash column affinity ────────────────────────────────────────────────────

def test_integer_dhash_column_is_rebuilt_as_text(tmp_path):
    import sqlite3
    from app.database.db import init_db
    db = tmp_path / "lib.db"
    init_db(str(db))
    con = sqlite3.connect(db)
    con.executescript("""
        DROP INDEX IF EXISTS ix_images_dhash;
        ALTER TABLE images RENAME COLUMN dhash TO x;
        ALTER TABLE images ADD COLUMN dhash INTEGER;   -- the legacy declaration
        ALTER TABLE images DROP COLUMN x;
        INSERT INTO models(id, title, archive_path, archive_hash, is_missing, to_print)
            VALUES (1, 'a', '/a.7z', 'h', 0, 0);
    """)
    hex_ok = "0f" * 16
    digits = "0123456789" * 3 + "01"                   # all decimal digits: gets converted
    con.executemany("INSERT INTO images(id, model_id, filename, dhash) VALUES (?, 1, 'x', ?)",
                    [(1, hex_ok), (2, digits)])
    con.commit()
    assert con.execute("SELECT typeof(dhash) FROM images WHERE id = 2").fetchone()[0] != "text"
    con.close()

    init_db(str(db))
    decl = [r[2] for r in factory.rows("PRAGMA table_info(images)") if r[1] == "dhash"][0]
    assert "CHAR" in decl.upper()
    assert {r[0]: r[1] for r in factory.rows("SELECT id, dhash FROM images")} == {1: hex_ok, 2: None}
    factory.execute("UPDATE images SET dhash = ? WHERE id = 2", digits)
    assert factory.scalar("SELECT dhash FROM images WHERE id = 2") == digits
    assert factory.scalar("SELECT 1 FROM sqlite_master WHERE name = 'ix_images_dhash'") == 1


# ── end to end ───────────────────────────────────────────────────────────────

def _jpeg(seed: int) -> bytes:
    px = np.random.default_rng(seed).integers(0, 255, (24, 32, 3), dtype=np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(px).resize((96, 72)).save(buf, "JPEG")
    return buf.getvalue()


def _models(n):
    return [factory.add_model(f"/lib/m{k}.7z", str(k), title=f"M{k}", archive_size=1000 + k)
            for k in range(n)]


def _img(model_id, dhash=None, thumb=None):
    image_id = factory.add_image(model_id, "x.jpg", dhash=dhash)
    factory.execute("UPDATE images SET thumbnail_path = ? WHERE id = ?", str(image_id), image_id)
    if thumb is not None:
        write_raw_thumbnail(image_id, thumb)
    return image_id


def test_find_dhash_duplicates_end_to_end(thumb_store):
    h = int(rng.integers(0, 2**63)) << 64 | 12345
    banner = int(rng.integers(0, 2**63)) << 64 | 999
    ids = _models(16)
    _img(ids[0], format_dhash(h))
    _img(ids[1], format_dhash(_flip(h, 3)))            # near: distance 3
    _img(ids[2], format_dhash(h))                       # identical
    _img(ids[3], format_dhash(_flip(h, 40)))           # far
    for m in ids[4:16]:                                 # banner in 12 models
        _img(m, format_dhash(banner))
    _img(ids[3], None, thumb=_jpeg(1))                  # hash computed from thumbnail
    _img(ids[15], None, thumb=_jpeg(1))
    rep = D.find_dhash_duplicates(None, max_dist=5, max_models=10)
    got = {(r["id_a"], r["id_b"]): (r["pairs"], r["best"]) for r in rep.results}
    want = {(ids[0], ids[1]): (1, 3), (ids[0], ids[2]): (1, 0), (ids[1], ids[2]): (1, 3),
            (ids[3], ids[15]): (1, 0)}
    assert rep.shared_skipped == 1 and rep.images == 18 and rep.items == 5
    assert got == want
    row = next(r for r in rep.results if r["id_a"] == ids[0] and r["id_b"] == ids[2])
    assert (row["size_a"], row["imgs_a"], row["title_b"]) == (1000, 1, "M2")


def test_find_embedding_duplicates_end_to_end():
    base = rng.standard_normal(EMBED_DIM).astype(np.float32)
    other = rng.standard_normal(EMBED_DIM).astype(np.float32)
    ids = _models(4)
    imgs = [_img(m) for m in ids]
    native_db.library().vectors_store(EMBED_DIM, [
        (imgs[0], base.tobytes()), (imgs[1], (base + 0.01).astype(np.float32).tobytes()),
        (imgs[2], base.tobytes()), (imgs[3], other.tobytes())])
    rep = D.find_embedding_duplicates(None, threshold=0.95)
    got = {(r["id_a"], r["id_b"]): r["best"] for r in rep.results}
    assert set(got) == {(ids[0], ids[1]), (ids[0], ids[2]), (ids[1], ids[2])}
    assert got[(ids[0], ids[2])] == pytest.approx(1.0)
    assert rep.items == 3
