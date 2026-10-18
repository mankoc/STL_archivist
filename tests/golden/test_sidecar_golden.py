"""Golden tests: native METADATA.7z export and import match folder_metadata in Python.

Exports are compared member by member (``metadata.json`` parsed, without
``generated_at``).  Imports run from the same sidecar into two copies of an
empty database, through Python and natively, and the databases and stored
thumbnails are compared.  Malformed sidecars must import identically or be
refused natively without writing.
"""
import io
import json
import random
import shutil
import sqlite3
import zipfile
from pathlib import Path

import py7zr
import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "sidecar_import"):
    pytest.skip("stl_native predates native sidecars; rebuild it", allow_module_level=True)

from app import native
from app.crawler import folder_metadata as FM
from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import (
    Category, Collection, CrawledFolder, Image, Release, Scale, Sculptor, STLFile, STLModel, Subcollection, Tag,
)
from app.utils import thumbnailer

TIMESTAMPS = ("created_at", "updated_at")


@pytest.fixture(autouse=True)
def _native_on(monkeypatch):
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()
    yield
    thumbnailer.close_thumbnail_store()


def _archive(folder: Path, name: str) -> Path:
    p = folder / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "")
    p.write_bytes(buf.getvalue())
    return p


def _read_7z(path: Path):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp, py7zr.SevenZipFile(path, "r") as z:
        z.extractall(tmp)
        files = {f.relative_to(tmp).as_posix(): f.read_bytes() for f in Path(tmp).rglob("*") if f.is_file()}
    data = json.loads(files.pop("metadata.json"))
    data.pop("generated_at")
    return data, files


def _dump(db: Path):
    con = sqlite3.connect(db)
    out = {}
    for t in ("models", "images", "stl_files", "model_tags", "model_scales", "tags", "scales", "sculptors",
              "collections", "subcollections", "categories", "releases", "crawled_folders"):
        cur = con.execute(f"SELECT * FROM {t} ORDER BY 1")
        cols = [d[0] for d in cur.description]
        keep = [k for k, c in enumerate(cols) if c not in TIMESTAMPS]
        out[t] = [tuple(r[k] for k in keep) for r in cur.fetchall()]
    con.close()
    return out


def _library(tmp_path: Path, rng: random.Random):
    data = tmp_path / "lib"
    data.mkdir()
    init_db(str(data / "stlLibrary.db"))
    thumbnailer.init_thumbnail_store(data / "thumbnails.lmdb")
    assert thumbnailer.store_backend() == "native"
    folder = tmp_path / "Models Ñ"
    folder.mkdir()
    (folder / "deeper").mkdir()
    with session_scope() as s:
        s.add(CrawledFolder(path=str(folder), pattern="{creator}/{name}"))
        cols = [Collection(name=n) for n in ("Comics", "Games")]
        sculptors = [Sculptor(name=f"S{k}") for k in range(3)]
        cat, rel = Category(name="Busts"), Release(name="May")
        s.add_all(cols + sculptors + [cat, rel, Tag(name="dragon"), Scale(name="28mm")])
        s.flush()
        sub = Subcollection(name="Marvel", collection_id=cols[0].id)
        s.add(sub)
        for i in range(12):
            where = folder / "deeper" if i == 11 else folder
            p = _archive(where, f"m{i} é.zip")
            m = STLModel(title=rng.choice([f"T{i}", None, "ünï \"q\""]), creator=rng.choice(["C", None]),
                         source="patreon", archive_path=str(p), archive_hash=f"h{i}",
                         release_year=rng.choice([None, 2024]), release_month=rng.choice([None, 3]),
                         url=rng.choice([None, "http://x"]), description=rng.choice([None, "line\nbreak\t"]),
                         sequence=rng.choice([None, 2]), presupports=rng.choice([None, "Lychee"]),
                         warning=rng.choice([None, "w"]), compression_rate=rng.choice([None, 0.1234, 1e-05, 1.0]),
                         sculptor=rng.choice([None] + sculptors),
                         collection=rng.choice([None, cols[0], cols[1]]),
                         category=rng.choice([None, cat]),
                         release=rng.choice([None, rel]))
            if m.collection is cols[0] and rng.random() < 0.5:
                m.subcollection = sub
            s.add(m)
            s.flush()
            m.tags.extend(s.query(Tag).filter(Tag.name.in_(rng.sample(["dragon"], rng.randint(0, 1)))).all())
            m.tags.extend([Tag(name=f"t{i}")] if rng.random() < 0.5 else [])
            m.scales.extend(s.query(Scale).limit(rng.randint(0, 1)).all())
            for k in range(rng.randint(0, 3)):
                img = Image(model_id=m.id, filename=rng.choice([f"i{k}.jpg", None]))
                s.add(img)
                s.flush()
                if rng.random() < 0.8:
                    img.thumbnail_path = str(img.id)
                    if rng.random() < 0.9:
                        thumbnailer.write_raw_thumbnail(img.id, bytes([k, i]) * 50)
            for name in rng.sample(["b.stl", "a.stl", "c.lys"], rng.randint(0, 3)):
                s.add(STLFile(model_id=m.id, filename=name, file_size=rng.choice([None, 7]),
                              archive_hash=rng.choice([None, f"h{i}"])))
    return data, folder


def test_export_matches(tmp_path):
    rng = random.Random(2)
    data, folder = _library(tmp_path, rng)
    lib = native_db.library()
    assert lib is not None
    for include in (True, False):
        with session_scope() as s:
            assert FM.export_folder_metadata(folder, s, data, include_thumbnails=include)
        py = _read_7z(folder / FM.METADATA_FILENAME)
        assert lib.sidecar_export(str(folder), include, thumbnailer.native_store())
        rs = _read_7z(folder / FM.METADATA_FILENAME)
        assert rs == py
        assert len(py[0]["models"]) == 11
    assert not lib.sidecar_export(str(tmp_path / "empty"), False, None)


def _fresh(tmp_path: Path, name: str):
    d = tmp_path / name
    d.mkdir()
    init_db(str(d / "stlLibrary.db"))
    return d / "stlLibrary.db"


def _import_both(tmp_path: Path, folder: Path, tag: str):
    """Import *folder*'s sidecar into fresh copies: Python on A, natively on B."""
    a = _fresh(tmp_path, f"a_{tag}")
    b = a.parent.parent / f"b_{tag}.db"
    shutil.copy(a, b)
    lib_b = stl_native.LibraryDb(str(b))
    before = _dump(b)
    try:
        rs = lib_b.sidecar_import(str(folder), thumbnailer.native_store())
    except ValueError:
        rs = "refused"
    finally:
        lib_b.close()
    try:
        with session_scope() as s:
            py = FM.import_folder_metadata(folder, s, a.parent)
    except Exception:
        py = "raised"
    if rs == "refused" or py == "raised":
        assert rs == "refused", "Python raises: the native import must refuse"
        assert _dump(b) == before, "a refused import must not write"
        return "refused"
    assert rs == py
    assert _dump(b) == _dump(a)
    con = sqlite3.connect(b)
    for (iid,) in con.execute("SELECT id FROM images WHERE thumbnail_path IS NOT NULL"):
        assert thumbnailer.get_thumbnail_bytes(iid)
    con.close()
    return "native"


def test_import_matches_both_writers(tmp_path):
    rng = random.Random(4)
    data, folder = _library(tmp_path, rng)
    lib = native_db.library()
    with session_scope() as s:
        FM.export_folder_metadata(folder, s, data, include_thumbnails=True)
    assert _import_both(tmp_path, folder, "py") == "native"
    assert lib.sidecar_export(str(folder), True, thumbnailer.native_store())
    assert _import_both(tmp_path, folder, "rs") == "native"


MODEL = {"archive_name": "m0 é.zip", "archive_hash": "h", "title": "T", "tags": ["Dragon", " x "],
         "scales": ["28mm"], "images": [{"filename": "a.jpg", "thumbnail": "thumbnails/0_0.jpg"}],
         "stl_files": [{"filename": "a.stl", "file_size": 3, "archive_hash": None}]}
VARIANTS = [
    {}, {"author": "A", "creator": ""}, {"collection": "", "category": "Cat"}, {"category": "OldCol"},
    {"collection": "C", "subcollection": "S"}, {"subcategory": "S"}, {"scales": None, "scale": "75mm"},
    {"scales": "28mm"}, {"scales": ["a", "a"]}, {"tags": None}, {"tags": ["x", "X "]}, {"tags": [1]},
    {"title": 5}, {"release_year": "2024"}, {"release_year": 2024.0}, {"compression_rate": 1},
    {"archive_hash": None}, {"images": None}, {"images": [{"thumbnail": ["x"]}]}, {"images": [{"thumbnail": 5}]},
    {"images": [{"thumbnail": "thumbnails/0_0.jpg", "filename": None}]}, {"stl_files": [{"filename": ""}]},
    {"stl_files": [{"filename": "a"}, {"filename": "a"}]}, {"archive_name": "missing.zip"},
    {"archive_name": "sub/x.zip"}, {"archive_name": ""}, {"pattern": "p"},
]


def test_malformed_sidecars(tmp_path):
    rng = random.Random(6)
    data, folder = _library(tmp_path, rng)
    outcomes = {"native": 0, "refused": 0}
    docs = [{"version": 1, "models": [dict(MODEL, **v)]} for v in VARIANTS]
    docs += [{"version": 2, "models": [MODEL]}, {"version": True, "models": [MODEL]}, {"version": 1, "models": None},
             {"version": 1, "pattern": "{x}", "models": [MODEL, MODEL]}, [1]]
    for k, doc in enumerate(docs):
        with py7zr.SevenZipFile(folder / FM.METADATA_FILENAME, "w") as z:
            z.writestr(json.dumps(doc), "metadata.json")
            z.writestr(b"\xff\xd8thumb", "thumbnails/0_0.jpg")
        outcomes[_import_both(tmp_path, folder, f"v{k}")] += 1
    assert outcomes["native"] > 15 and outcomes["refused"] > 5
