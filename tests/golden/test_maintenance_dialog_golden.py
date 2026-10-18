"""Golden tests: the Database Maintenance dialog's native reads and writes match Python.

Writes run through the Python service on database A and natively on a copy B;
both databases are compared at the end.
"""
import random
import sqlite3

import numpy as np
import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "maint_merge_moved"):
    pytest.skip("stl_native predates the native maintenance dialog; rebuild it", allow_module_level=True)

from app import native
from app.database import native_db, vectors
from app.database.db import init_db, session_scope
from app.database.models import Collection, Image, Sculptor, STLFile, STLModel, Tag
from app.services import maintenance as M
from app.utils import thumbnailer
from app.utils.image_embedding import EMBED_DIM

SENTINEL = "2000-01-01 00:00:00.000000"


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    a = tmp_path / "a" / "stlLibrary.db"
    a.parent.mkdir()
    init_db(str(a))
    if not vectors.available():
        pytest.skip("sqlite-vec not loadable")
    rng = random.Random(4)
    with session_scope() as s:
        cols = [Collection(name=n) for n in ("Comics", "Unused")]
        scs = [Sculptor(name=n) for n in ("Alice", "Bob")]
        s.add_all(cols + scs + [Tag(name="t")])
        s.flush()
        for i in range(40):
            m = STLModel(title=f"T{i}", archive_path=f"F:/lib/{i % 3}/m{i}.7z", archive_hash=f"h{i % 30}",
                         archive_size=rng.choice([None, 5]), warning=rng.choice([None, "a", "b"]),
                         is_missing=i < 10, collection=rng.choice([cols[0], None]),
                         sculptor=rng.choice(scs + [None]))
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 3)):
                s.add(Image(model_id=m.id, filename=f"i{k}.jpg", thumbnail_path=rng.choice([None, f"{m.id}{k}"]),
                            thumbnail=rng.choice([None, b"legacy"])))
            if rng.random() < 0.5:
                s.add(STLFile(model_id=m.id, filename="a.stl", file_size=1))
        s.flush()
        img_ids = [i for (i,) in s.query(Image.id)]
        vecs = np.random.default_rng(1).normal(size=(len(img_ids), EMBED_DIM)).astype(np.float32)
        vectors.store(s, list(zip(img_ids[::2], vecs[::2])))
        vectors.store(s, [(10**6, vecs[0])])          # an orphan
    con = sqlite3.connect(a)
    con.execute("UPDATE models SET updated_at = ?", (SENTINEL,))
    con.commit()
    b = tmp_path / "b" / "stlLibrary.db"
    b.parent.mkdir()
    out = sqlite3.connect(b)
    con.backup(out)
    out.close()
    con.close()
    lib_b = stl_native.LibraryDb(str(b))
    yield a, b, lib_b
    lib_b.close()
    thumbnailer.close_thumbnail_store()


def _dump(path):
    con = sqlite3.connect(path)
    out = {}
    for t in ("models", "images", "stl_files", "collections", "sculptors", "tags"):
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if "updated_at" in cols:
            k = cols.index("updated_at")
            rows = [r[:k] + (r[k] != SENTINEL,) + r[k + 1:] for r in rows]
        out[t] = sorted(rows, key=repr)
    con.close()
    return out


def test_maintenance_dialog_matches(pair, monkeypatch):
    a, b, lib_b = pair
    use = lambda lib: monkeypatch.setattr(native_db, "library", lambda: lib)  # noqa: E731

    def both(fn, *args):
        use(lib_b)
        rs = fn(*args)
        use(None)
        py = fn(*args)
        assert rs == py, (fn.__name__, args)
        return py

    assert both(M.warning_records)
    assert both(M.location_rows)
    both(M.database_free_bytes)
    orphans, targets = both(M.embedding_targets, False)
    assert orphans == 1 and targets
    assert both(M.embedding_targets, True)[1]
    vecs = np.random.default_rng(2).normal(size=(len(targets), EMBED_DIM)).astype(np.float32)
    items = [(iid, v) for (iid, _blob), v in zip(targets, vecs)] + [(targets[0][0], np.zeros(3, np.float32))]
    assert both(M.store_embeddings, items) == len(targets)
    assert both(M.embedding_targets, False) == (0, [])
    both(M.set_warnings, [(1, "a"), (2, None), (3, "new"), (999, "x")])
    rows = both(M.location_rows)
    path_of = {mid: p for mid, p, _h, _s in rows}
    both(M.merge_moved, [(1, path_of[20]), (2, "nope"), (999, path_of[21]), (3, path_of[22])])
    both(M.remove_records, [4, 5, 30, 999])
    assert both(M.warning_records)
    lib_b.close()
    assert _dump(a) == _dump(b)
    va = sqlite3.connect(a).execute("SELECT count(*) FROM image_vec_rowids").fetchone()
    vb = sqlite3.connect(b).execute("SELECT count(*) FROM image_vec_rowids").fetchone()
    assert va == vb
