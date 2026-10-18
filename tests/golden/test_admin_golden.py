"""Golden tests: native lookup administration matches services.lookup_admin in Python.

The same random sequence of editor operations runs through the service
functions twice: once on database A with the native path off, once on a copy B
with ``native_db.library`` pointing at a ``LibraryDb`` for B.  Every returned
value and both databases (``updated_at`` as changed-or-not) are compared.
"""
import random
import sqlite3
import sys

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "admin_list"):
    pytest.skip("stl_native predates lookup administration; rebuild it", allow_module_level=True)

from app import native
from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import (
    Category, Collection, Creator, Scale, STLModel, Subcollection, Tag,
)
from app.services import lookup_admin as LA
from app.utils import thumbnailer

SENTINEL = "2000-01-01 00:00:00.000000"
NAMES = ["Comics", "Games", "comics", "Ñ", "Busts", "Minis", "x", "Marvel", "Zelda", "dragon", "Elf", "ELF"]
AUTHORS = ["Sanix", "sanix", "Bob", "Ñandú", "nobody"]


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    a = tmp_path / "a" / "stlLibrary.db"
    a.parent.mkdir()
    init_db(str(a))
    rng = random.Random(3)
    with session_scope() as s:
        cols = [Collection(name=n) for n in ("Comics", "Games", "Empty")]
        cats = [Category(name=n) for n in ("Busts", "Minis", "Terrain")]
        tags = [Tag(name=n) for n in ("dragon", "Elf", "orc", "lonely")]
        scales = [Scale(name=n) for n in ("28mm", "75mm")]
        s.add_all(cols + cats + tags + scales)
        s.flush()
        subs = [Subcollection(name=n, collection_id=c.id) for c, n in
                ((cols[0], "Marvel"), (cols[0], "DC"), (cols[1], "Zelda"), (cols[1], "Marvel"))]
        s.add_all(subs)
        s.flush()
        for i in range(30):
            m = STLModel(title=f"M{i}", archive_path=f"p{i}", archive_hash=f"h{i}",
                         creator=rng.choice(AUTHORS[:4] + [None]), category=rng.choice(cats + [None]))
            if rng.random() < 0.6:
                sub = rng.choice(subs)
                m.subcollection = sub
                m.collection = next(c for c in cols if c.id == sub.collection_id)
            m.tags.extend(rng.sample(tags, rng.randint(0, 3)))
            m.scales.extend(rng.sample(scales, rng.randint(0, 2)))
            s.add(m)
        s.add_all([Creator(name="Sanix", full_name="S"), Creator(name="Bob", patreon_url="u")])
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
    for t in ("models", "tags", "model_tags", "model_scales", "scales", "categories", "collections",
              "subcollections", "creators"):
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if "updated_at" in cols:
            k = cols.index("updated_at")
            rows = [r[:k] + (r[k] != SENTINEL,) + r[k + 1:] for r in rows]
        out[t] = sorted(rows, key=repr)
    con.close()
    return out


def test_random_admin_operations_match(pair, monkeypatch):
    a, b, lib_b = pair
    rng = random.Random(17)

    def both(fn, *args):
        monkeypatch.setattr(native_db, "library", lambda: None)
        py = fn(*args)
        monkeypatch.setattr(native_db, "library", lambda: lib_b)
        rs = fn(*args)
        assert rs == py, (fn.__name__, args)
        return py

    for step in range(150):
        kind = rng.choice(LA.KINDS)
        rows = both(LA.list_lookups, kind)
        ids = [r[0] for r in rows] + [10**6]
        op = rng.choice(["add", "rename", "delete", "merge", "info", "name", "tags", "authors"])
        if op == "add":
            both(LA.add, kind, rng.choice(NAMES), rng.choice(["Comics", "Games", "nope"]))
        elif op == "rename":
            both(LA.rename, kind, rng.choice(ids), rng.choice(NAMES))
        elif op == "delete" and rng.random() < 0.4:
            both(LA.delete, kind, rng.choice(ids))
        elif op == "merge" and kind in ("category", "collection", "subcollection") and len(rows) >= 2:
            keep, gone = rng.sample([r[0] for r in rows], 2)
            both(LA.merge, kind, keep, gone)
        elif op == "info":
            both(LA.delete_info, kind, rng.choice(ids))
        elif op == "name":
            both(LA.name_of, kind, rng.choice(ids))
        elif op == "tags":
            tag_rows = both(LA.tags_for_merge_scan)
            both(LA.unused_tag_count)
            if tag_rows and rng.random() < 0.5:
                group = rng.sample([t[0] for t in tag_rows], min(len(tag_rows), rng.randint(1, 3)))
                both(LA.merge_tags, group, rng.choice(NAMES + ["New Tag"]))
            elif rng.random() < 0.2:
                both(LA.delete_unused_tags)
        else:
            both(LA.author_counts)
            x, y = rng.sample(AUTHORS, 2)
            both(LA.author_model_count, x)
            choice = rng.random()
            if choice < 0.3:
                both(LA.rename_author, x, y)
            elif choice < 0.5:
                both(LA.merge_authors, x, y)
            elif choice < 0.6:
                both(LA.delete_author, x)
            both(LA.creator_profile, y)
            if rng.random() < 0.3:
                both(LA.save_creator_profile, y, {"full_name": rng.choice(["", " F ", None]), "gumroad_url": "g"})
    thumbnailer.close_thumbnail_store()
    lib_b.close()
    assert _dump(a) == _dump(b)
