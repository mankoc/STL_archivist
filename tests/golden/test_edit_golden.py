"""Golden tests: native writes leave the database as the Python services do.

Two copies of one library get the same operations, one through the Python
services (ORM, session, commit) and one through ``stl_native.LibraryDb``; the
databases are then compared table by table. ``updated_at`` is compared as
"changed or not", since the two runs happen at different instants.
"""
import random
import shutil
import sqlite3
import sys

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "apply_cell_edits"):
    pytest.skip("stl_native predates native writes; rebuild it", allow_module_level=True)

import sqlite_vec

from app import native
from app.database import helpers
from app.database.db import init_db, session_scope
from app.database.models import (
    Category, Collection, Image, Release, Scale, Sculptor, STLModel, Subcollection, Tag,
)
from app.services import editing, moves
from app.utils import thumbnailer

SENTINEL = "2000-01-01 00:00:00.000000"


@pytest.fixture
def pair(tmp_path, monkeypatch):
    """(python_db_path, native LibraryDb on an identical copy)."""
    monkeypatch.setenv("STL_NATIVE", "0")            # the Python side must not go native
    native.reload()
    a = tmp_path / "a" / "stlLibrary.db"
    a.parent.mkdir()
    init_db(str(a))
    rng = random.Random(5)
    with session_scope() as s:
        sculptors = [Sculptor(name=n) for n in ("Alice", "Bob")]
        cols = [Collection(name=n) for n in ("Comics", "Games", "Empty")]
        s.add_all(sculptors + cols + [Category(name="Busts"), Release(name="May"),
                                      Scale(name="28mm"), Scale(name="75MM"),
                                      Tag(name="dragon"), Tag(name="elf"), Sculptor(name="Unused")])
        s.flush()
        subs = [Subcollection(name="Marvel", collection_id=cols[0].id),
                Subcollection(name="Zelda", collection_id=cols[1].id),
                Subcollection(name="Marvel", collection_id=cols[1].id)]
        s.add_all(subs)
        s.flush()
        for i in range(40):
            m = STLModel(title=f"M{i}", creator=rng.choice(["a", "b", None]), archive_path=f"F:/lib/{i % 5}/m{i}.7z",
                         archive_hash=f"h{i}", sculptor=rng.choice(sculptors + [None]),
                         release_year=rng.choice([None, 2023]), sequence=rng.choice([None, 3]))
            if rng.random() < 0.5:
                m.subcollection = rng.choice(subs)
                m.collection = next(c for c in cols if c.id == m.subcollection.collection_id)
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 2)):
                s.add(Image(model_id=m.id, filename=f"{k}.jpg", thumbnail_path=str(1000 * i + k)))
        s.flush()
    con = sqlite3.connect(a)
    con.execute("UPDATE models SET updated_at = ?", (SENTINEL,))
    con.commit()
    con.close()
    thumbnailer.close_thumbnail_store()
    init_db(str(a))
    b = tmp_path / "b" / "stlLibrary.db"
    b.parent.mkdir()
    con = sqlite3.connect(a)
    out = sqlite3.connect(b)
    con.backup(out)
    out.close()
    con.close()
    lib = stl_native.LibraryDb(str(b))
    yield a, lib, b
    lib.close()
    thumbnailer.close_thumbnail_store()


def _dump(path):
    con = sqlite3.connect(path)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    out = {}
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'image_vec_%' "
                               "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        rows = con.execute(f'SELECT * FROM "{name}"').fetchall()
        cols = [d[0] for d in con.execute(f'SELECT * FROM "{name}" LIMIT 0').description]
        if "updated_at" in cols:
            k = cols.index("updated_at")
            rows = [r[:k] + (r[k] != SENTINEL,) + r[k + 1:] for r in rows]
        out[name] = sorted(rows, key=repr)
    con.close()
    return out


def _assert_same(a, b):
    da, db_ = _dump(a), _dump(b)
    assert da.keys() == db_.keys()
    for table in da:
        assert da[table] == db_[table], table


FIELDS = sorted(editing.CELL_FIELDS)
TEXTS = {
    "title": ["New", "  spaced  ", "", None, "Ñ"], "creator": ["x", "", "  "], "source": ["patreon", ""],
    "date": ["2024-03", "2024_7", "2024", "bad", "", " 2021-12 "],
    "tags": ["Dragon, ELF", "new tag, , other", "", "elf,elf,Elf"],
    "scale": ["28MM, 75mm", "32mm", "", "1/35, 1/35"],
    "sculptor": ["Alice", "Carol", "", "  Bob "], "collection": ["Comics", "Games", "New col", ""],
    "subcollection": ["Marvel", "Zelda", "Fresh", ""], "category": ["Busts", "Minis", ""],
    "release": ["May", "June", ""], "sequence": ["7", "+2", "1_0", "x", "", "-1"],
    "presupports": ["Lychee", ""], "to_print": ["1", "0", ""],
}


def test_cell_edits_match(pair):
    a, lib, b = pair
    rng = random.Random(11)
    with session_scope() as s:
        ids = [m.id for m in s.query(STLModel.id)]
    for batch_no in range(60):
        edits = []
        for _ in range(rng.randint(1, 5)):
            field = rng.choice(FIELDS)
            edits.append((rng.choice(ids + [10**9]), field, rng.choice(TEXTS[field])))
        with session_scope() as s:
            for mid, field, text in edits:
                m = s.get(STLModel, mid)
                if m is not None:
                    editing.apply_cell_edit(s, m, field, text)
        lib.apply_cell_edits(edits)
    _assert_same(a, b)


def test_non_ascii_digits_are_left_to_python(pair):
    _a, lib, _b = pair
    with pytest.raises(ValueError):
        lib.apply_cell_edits([(1, "date", "٢٠٢٤")])
    with pytest.raises(ValueError):
        lib.apply_cell_edits([(1, "sequence", "٣")])
    with pytest.raises(KeyError):
        lib.apply_cell_edits([(1, "nope", "x")])


def test_moves_delete_and_prune_match(pair):
    a, lib, b = pair
    with session_scope() as s:
        refs_py = moves.delete_model(s, s.get(STLModel, 3))
        old, new = s.get(STLModel, 5), s.get(STLModel, 6)
        new.compression_rate = None
        refs_py += moves.merge_into_missing(s, old, new)
        moves.relocate(s.get(STLModel, 7), "D:\\moved\\x.7z", "hx", 12, 34, 0.5)
        moves.relocate(s.get(STLModel, 8), "//nas/share/y.zip", "hy", None, None)
    with session_scope() as s:
        pruned_py = helpers.prune_unused_lookups(s)
    con = sqlite3.connect(b)
    con.execute("UPDATE models SET compression_rate = NULL WHERE id = 6")
    con.commit()
    con.close()
    refs_rs = lib.delete_model(3) + lib.merge_into_missing(5, 6)
    lib.relocate(7, "D:\\moved\\x.7z", "hx", 12, 34, 0.5)
    lib.relocate(8, "//nas/share/y.zip", "hy", None, None)
    pruned_rs = lib.prune_unused_lookups()
    assert refs_rs == refs_py
    assert pruned_rs == pruned_py
    _assert_same(a, b)
