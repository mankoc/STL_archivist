"""Golden tests: services.archive_jobs natively matches the ORM implementation.

Each job runs through Python on database A and natively on a copy B; the
results and both databases are compared after every step.
"""
import random
import sqlite3

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "jobs_apply_rescan"):
    pytest.skip("stl_native predates native archive jobs; rebuild it", allow_module_level=True)

from app import native
from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import Image, Scale, STLFile, STLModel
from app.services import archive_jobs as AJ
from app.utils import thumbnailer

TIMESTAMPS = {"created_at", "updated_at"}


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()
    a = tmp_path / "a" / "stlLibrary.db"
    a.parent.mkdir()
    init_db(str(a))
    rng = random.Random(1)
    with session_scope() as s:
        s.add(Scale(name="28mm"))
        for i in range(12):
            root = "F:\\STL\\A" if i % 2 else "F:\\STL\\B"
            m = STLModel(title=f"T{i}", archive_path=f"{root}\\m{i}.zip", archive_hash=f"h{i}" if i >= 8 else rng.choice([f"h{i}", "dup"]),
                         is_missing=i >= 8, warning=rng.choice([None, "w"]), archive_size=rng.choice([None, 9]))
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 3)):
                img = Image(model_id=m.id, filename=f"i{k}.jpg")
                s.add(img)
                s.flush()
                img.thumbnail_path = str(img.id)
                thumbnailer.write_raw_thumbnail(img.id, b"old")
            s.add(STLFile(model_id=m.id, filename="x.stl"))
        for i in range(3):     # new archives the missing ones may have moved to
            s.add(STLModel(title=f"N{i}", archive_path=f"G:\\New\\n{i}.zip", archive_hash=["h8", "h9", "dup"][i]))
    b = tmp_path / "b.db"
    src, dst = sqlite3.connect(a), sqlite3.connect(b)
    src.backup(dst)
    src.close()
    dst.close()
    lib_b = stl_native.LibraryDb(str(b))
    yield a, b, lib_b
    lib_b.close()
    thumbnailer.close_thumbnail_store()


def _dump(db):
    con = sqlite3.connect(db)
    out = {}
    for t in ("models", "images", "stl_files", "model_scales", "scales"):
        cur = con.execute(f"SELECT * FROM {t} ORDER BY 1, 2")
        cols = [d[0] for d in cur.description]
        keep = [k for k, c in enumerate(cols) if c not in TIMESTAMPS]
        out[t] = [tuple(r[k] for k in keep) for r in cur.fetchall()]
    con.close()
    return out


def test_archive_jobs_match(pair, monkeypatch):
    a, b, lib_b = pair

    def both(fn, *args):
        monkeypatch.setattr(AJ, "_lib", lambda: lib_b)
        rs = fn(*args)
        monkeypatch.setattr(AJ, "_lib", lambda: None)
        py = fn(*args)
        assert rs == py, (fn.__name__, args)
        assert _dump(b) == _dump(a), fn.__name__
        return py

    assert both(AJ.rescan_state, 1)[0] == "F:\\STL\\B\\m0.zip"
    assert both(AJ.rescan_state, 999) is None
    assert both(AJ.replace_thumbnails, 1, [("a.png", b"j1"), ("b.png", None), ("c.png", b"j3")], "warn") == 2
    both(AJ.replace_thumbnails, 2, [("z.png", None)], None)
    assert both(AJ.replace_thumbnails, 999, [("a.png", b"x")], None) is None
    values = {"archive_hash": "new", "archive_size": 5, "archive_mtime_ns": 7, "compression_rate": 0.5,
              "title": "", "creator": "C", "source": None, "release": (2024, 3), "url": "u",
              "description": None, "presupports": "No", "warning": "", "scales": ["28mm", "75mm"],
              "stl_files": ["a.stl", "b.stl"]}
    assert both(AJ.apply_rescan, 3, values)
    both(AJ.apply_rescan, 4, dict(values, compression_rate=None, release=None, scales=[], stl_files=None))
    assert not both(AJ.apply_rescan, 999, values)
    moved = both(AJ.cross_folder_reconcile, {"G:\\New\\n0.zip", "G:\\New\\n1.zip", "G:\\New\\n2.zip"})
    assert moved[0]
    deleted, _refs = both(AJ.delete_missing_under, ["f:\\stl\\a"])
    assert deleted
