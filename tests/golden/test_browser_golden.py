"""Golden tests: native browser data and statistics match the Python services.

Reads run through the services on one database with the native path off and
on.  Writes run through the Python service on database A and natively on a
copy B; both databases are compared at the end.
"""
import random
import sqlite3

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "browser_sort_sources"):
    pytest.skip("stl_native predates native browser data; rebuild it", allow_module_level=True)

from app import native
from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import (
    Category, Collection, Creator, Image, Release, Scale, Sculptor, STLFile, STLModel, Subcollection, Tag,
)
from app.services import browser_data as BD
from app.services import file_deletion
from app.services import library_stats as LS
from app.utils import thumbnailer

SENTINEL = "2000-01-01 00:00:00.000000"


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    a = tmp_path / "a" / "stlLibrary.db"
    a.parent.mkdir()
    init_db(str(a))
    rng = random.Random(5)
    with session_scope() as s:
        cols = [Collection(name=n) for n in ("Comics", "Games", "Zoo")]
        sculptors = [Sculptor(name=n) for n in ("Alice", "Bob", "Carol")]
        rels = [Release(name=n) for n in ("May", "June")]
        cats = [Category(name=n) for n in ("Busts", "Minis")]
        tags = [Tag(name=n) for n in ("dragon", "elf", "orc", "ñandú")]
        scales = [Scale(name=n) for n in ("28mm", "75mm", "1/35")]
        s.add_all(cols + sculptors + rels + cats + tags + scales)
        s.add_all([Creator(name="Sanix", full_name="Sanix Studio", patreon_url="p"),
                   Creator(name="Empty")])
        s.flush()
        subs = [Subcollection(name="Marvel", collection_id=cols[0].id),
                Subcollection(name="Zelda", collection_id=cols[1].id)]
        s.add_all(subs)
        s.flush()
        for i in range(60):
            m = STLModel(
                title=rng.choice([f"T{i}", None, ""]), creator=rng.choice(["Sanix", "Empty", None, "", "Bob"]),
                source=rng.choice(["patreon", "kickstarter", None, ""]),
                archive_path=f"F:/lib/{i % 4}/m{i}.7z",
                archive_hash=f"h{i}", url=rng.choice([None, "", "http://x"]),
                description=rng.choice([None, "d"]), release_year=rng.choice([None, 2022, 2023, 2024]),
                release_month=rng.choice([None, 1, 5, 12]), sequence=rng.choice([None, 0, 3]),
                presupports=rng.choice([None, "", "No", "Lychee", "Presupported"]),
                warning=rng.choice([None, "w"]), archive_size=rng.choice([None, 10, 2000]),
                sculptor=rng.choice(sculptors + [None]), collection=rng.choice(cols + [None]),
                category=rng.choice(cats + [None]), release=rng.choice(rels + [None]),
            )
            if m.collection is cols[0] and rng.random() < 0.7:
                m.subcollection = subs[0]
            m.tags.extend(rng.sample(tags, rng.randint(0, 3)))
            m.scales.extend(rng.sample(scales, rng.randint(0, 2)))
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 3)):
                s.add(Image(model_id=m.id, filename=f"img{k}.jpg", thumbnail_path=str(m.id * 10 + k) if k else None))
            for k in range(rng.randint(0, 2)):
                s.add(STLFile(model_id=m.id, filename=f"part{k}.stl", file_size=rng.choice([None, 5, 7])))
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
    for t in ("models", "tags", "images", "model_tags", "model_scales", "stl_files"):
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if "updated_at" in cols:
            k = cols.index("updated_at")
            rows = [r[:k] + (r[k] != SENTINEL,) + r[k + 1:] for r in rows]
        out[t] = sorted(rows, key=repr)
    con.close()
    return out


def test_browser_data_matches(pair, monkeypatch, tmp_path):
    a, b, lib_b = pair
    rng = random.Random(9)
    use = lambda lib: monkeypatch.setattr(native_db, "library", lambda: lib)  # noqa: E731

    def read(fn, *args):
        use(None)
        py = fn(*args)
        use(lib_b)
        rs = fn(*args)
        use(None)
        assert rs == py, (fn.__name__, args)
        return py

    def write(fn, *args):
        use(lib_b)
        rs = fn(*args)
        use(None)
        py = fn(*args)
        assert rs == py, (fn.__name__, args)

    stats = read(LS.library_statistics)
    assert stats["total_models"] == 60 and stats["top_tags"]
    for key in LS.COLUMN_KEYS + ("nope",):
        for top in (None, 3, 0):
            read(LS.column_distribution, key, top)
    for kind in ("source", "sculptor", "collection", "subcollection", "category", "release", "scale", "x"):
        read(BD.lookup_names, kind)
    read(BD.tag_names)

    # The thumbnail writer used for dropped images: fail one image in three.
    def fake_write(image_id, jpeg):
        return None if jpeg == b"bad" else str(image_id)

    for step in range(80):
        ids = [i for (i,) in sqlite3.connect(a).execute("SELECT id FROM models")] + [10**6]
        mid = rng.choice(ids)
        read(BD.model_panel, mid, rng.choice(["Sanix", "Empty", "", None, "Nobody"]))
        read(BD.model_images, mid)
        read(BD.sort_sources, rng.sample(ids, rng.randint(0, 6)))
        op = rng.random()
        if op < 0.2:
            write(BD.add_images, mid, [(f"drop{k}.png", rng.choice([b"ok", b"bad"])) for k in range(rng.randint(1, 3))],
                  fake_write)
        elif op < 0.35:
            imgs = [i for (i,) in sqlite3.connect(a).execute("SELECT id FROM images")] + [10**6]
            write(BD.delete_image, rng.choice(imgs))
        elif op < 0.5:
            write(BD.add_tag, rng.choice(["dragon", "new", "Dragon", "ñandú", f"t{step}"]))
        elif op < 0.6:
            write(BD.delete_models, rng.sample(ids, rng.randint(0, 2)))
        elif op < 0.8:
            sel = rng.sample(ids, rng.randint(1, 4))
            write(BD.set_archive_paths, [(i, rng.choice([f"G:\\sorted\\{i}.7z", f"F:/lib/{i}.7z",
                                                        f"G:/x/y/{i}.zip"])) for i in sel])
        else:
            sel = rng.sample(ids, rng.randint(1, 3))
            read(file_deletion._rows, sel)
        read(LS.library_statistics)
    thumbnailer.close_thumbnail_store()
    lib_b.close()
    assert _dump(a) == _dump(b)


def test_delete_archives_natively(pair, monkeypatch, tmp_path):
    """``delete_archives`` deletes files and records the same way through ``LibraryDb``."""
    a, b, lib_b = pair
    archive = tmp_path / "gone.zip"
    archive.write_text("x")
    con = sqlite3.connect(b)
    con.execute("UPDATE models SET archive_path = ? WHERE id = 1", (str(archive),))
    con.commit()
    con.close()
    monkeypatch.setattr(native_db, "library", lambda: lib_b)
    report = file_deletion.delete_archives([1, 2, 10**6], use_trash=True, trash_path=tmp_path / "trash")
    assert report.deleted == 2 and not archive.exists()
    assert (tmp_path / "trash" / "gone.zip.json").exists()
    left = {i for (i,) in sqlite3.connect(b).execute("SELECT id FROM models")}
    assert not ({1, 2} & left) and len(left) == 58
