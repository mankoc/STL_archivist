"""Golden tests: the native browser queries return exactly what Python returns.

A random library (case variants, non-ASCII, NULL vs empty values, backslash and
UNC paths, folders with and without ``archive_folder``, ``%`` / ``_`` in names)
is written to a file database, then many random ``FilterSpec`` s are run through
``table_ids``, ``page_rows``, ``filter_tree_data``, ``usage_counts`` and
``model_summary`` natively and in Python.
"""
import random
import sys
from datetime import datetime, timedelta

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb"):
    pytest.skip("stl_native predates LibraryDb; rebuild it", allow_module_level=True)
if sys.platform != "win32":
    pytest.skip("native database reads are Windows-only until SQLAlchemy is gone",
                allow_module_level=True)

from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import (
    Category, Collection, Image, Release, Scale, Sculptor, STLModel, Subcollection, Tag,
)
from app.services import filter_tree as FT
from app.services import library_query as LQ
from app.services import lookups, model_summary as MS
from app.services.library_query import FACETS, GROUPS, FilterSpec
from app.utils import thumbnailer

CREATORS = ["Alice", "alice", "ALICE", "Bob", "Ñandú", "ñandú", "Zoë", "", None, "100%_Real", "Chris"]
SOURCES = ["patreon", "Patreon", "kickstarter", "mmf", "", None]
WARNINGS = [None, "", "Nested archives", "Archive without images\nNested archives",
            "Models with presupported stl", "Unreadable archive", "Archive without printable files"]
PRESUP = [None, "No", "Lychee", "Chitubox", "Presupported", ""]
FOLDERS = ["D:/lib/Alice", "D:/lib/Bob/sub", "D:/lib/100%_x", "E:/other", "//nas/share/minis",
           "D:/lib/Ñandú/deep/er"]


@pytest.fixture(scope="module")
def _library_file(tmp_path_factory):
    rng = random.Random(1234)
    path = tmp_path_factory.mktemp("lib") / "stlLibrary.db"
    init_db(str(path))
    with session_scope() as s:
        sculptors = [Sculptor(name=n) for n in ("Alice S", "Bob S", "Émile")]
        cols = [Collection(name=n) for n in ("Comics", "Games", "África")]
        cats = [Category(name=n) for n in ("Busts", "Minis")]
        rels = [Release(name=n) for n in ("May", "June", "Annual 2024")]
        scales = [Scale(name=n) for n in ("28mm", "32mm", "75mm", "1/35")]
        tags = [Tag(name=n) for n in ("dragon", "elf", "Orc", "ñu", "x_y", "50%")]
        s.add_all(sculptors + cols + cats + rels + scales + tags)
        s.flush()
        subs = [Subcollection(name=n, collection_id=c.id) for c, n in
                ((cols[0], "Marvel"), (cols[0], "DC"), (cols[1], "Zelda"), (cols[2], "Marvel"))]
        s.add_all(subs)
        s.flush()
        base = datetime(2024, 1, 1, 12, 0, 0)
        for i in range(400):
            folder = rng.choice(FOLDERS)
            ext = rng.choice([".7z", ".zip", ".rar", ".ZIP", ".7Z", ""])
            name = f"m{i}{ext}"
            ap = folder + "/" + name
            if rng.random() < 0.3:
                ap = ap.replace("/", "\\")
            m = STLModel(
                title=rng.choice([f"Model {i}", f"model {i % 7}", None, "Ñ title", ""]),
                creator=rng.choice(CREATORS), source=rng.choice(SOURCES),
                archive_path=ap, archive_hash=f"h{i}",
                archive_folder=folder if rng.random() < 0.6 else None,
                release_year=rng.choice([None, 2021, 2022, 2023]),
                release_month=rng.choice([None, 1, 5, 12]),
                presupports=rng.choice(PRESUP), warning=rng.choice(WARNINGS),
                archive_size=rng.choice([None, 10, 5000, 10**9]),
                compression_rate=rng.choice([None, 0.5, 0.9]),
                sequence=rng.choice([None, 1, 2, 10]), to_print=rng.random() < 0.2,
                sculptor=rng.choice(sculptors + [None]), category=rng.choice(cats + [None]),
                release=rng.choice(rels + [None]),
                created_at=base + timedelta(seconds=rng.randint(0, 10**7), microseconds=rng.choice([0, 123456])),
            )
            if rng.random() < 0.7:
                sub = rng.choice(subs)
                m.subcollection = sub
                m.collection = next(c for c in cols if c.id == sub.collection_id)
            elif rng.random() < 0.5:
                m.collection = rng.choice(cols)
            m.tags.extend(rng.sample(tags, rng.randint(0, 3)))
            m.scales.extend(rng.sample(scales, rng.randint(0, 2)))
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 2)):
                s.add(Image(model_id=m.id, filename=f"{k}.jpg"))
        values = {
            "creator": [c for c in CREATORS if c] + ["nobody"],
            "source": [x for x in SOURCES if x] + ["PATREON"],
            "tag": [t.name for t in tags] + ["missing"],
            "path": FOLDERS + ["D:/lib", "D:/lib/", "D:\\lib\\Bob", "//nas/share"],
            "date": [(2021, None), (2022, 5), (2023, 12), (2023, None), (1999, None)],
            "sculptor": [x.id for x in sculptors], "collection": [x.id for x in cols],
            "subcollection": [x.id for x in subs], "category": [x.id for x in cats],
            "release": [x.id for x in rels], "scale": [x.id for x in scales],
            "presupports": [p for p in PRESUP if p],
            "warning": ["Nested archives", "Archive without images", "Unreadable", "printable"],
            "filetype": [".7z", ".zip", ".rar", ".none"],
        }
    thumbnailer.close_thumbnail_store()
    return path, values


@pytest.fixture
def library(_library_file, monkeypatch):
    """Reopen the module's file database (conftest resets to :memory: per test)."""
    from app import native
    monkeypatch.delenv("STL_NATIVE", raising=False)   # compare even in an STL_NATIVE=0 run
    native.reload()
    path, values = _library_file
    init_db(str(path))
    yield values
    native_db.reset()
    thumbnailer.close_thumbnail_store()


def _random_spec(rng, values, with_ids=None):
    include, exclude = {}, {}
    for facet in rng.sample(FACETS, rng.randint(0, 4)):
        target = include if rng.random() < 0.65 else exclude
        target[facet] = rng.sample(values[facet], rng.randint(1, min(3, len(values[facet]))))
    has = set(rng.sample(sorted(GROUPS), rng.randint(0, 2))) if rng.random() < 0.3 else set()
    no = set(rng.sample(sorted(GROUPS - has), rng.randint(0, 1))) if rng.random() < 0.3 else set()
    ids = None
    if with_ids and rng.random() < 0.15:
        ids = rng.sample(with_ids, rng.randint(0, 40))
    return FilterSpec(include=include, exclude=exclude, has_value=has, no_value=no, ids=ids)


SHOW_ALL = {k: True for k in ("author", "tags", "source", "date", "sculptor", "collection",
                              "subcollection", "category", "release", "scale", "presupports",
                              "warnings", "path")}


def _native(fn, *a):
    assert native_db.library() is not None
    return fn(*a)


def test_table_ids_match(library):
    rng = random.Random(7)
    all_ids = LQ._table_ids_py(FilterSpec(), "", None, lambda: False)
    for k in range(300):
        spec = _random_spec(rng, library, all_ids)
        search = rng.choice(["", "", "model", "ñ", "ALICE", "dragon", "%", "_", "1"])
        sort = rng.choice([None, *[(c, d) for c in LQ.SORT_COLUMNS for d in (False, True)]])
        py = LQ._table_ids_py(spec, search, sort, lambda: False)
        rs = _native(LQ.table_ids, spec, search, sort)
        if sort is None:
            py, rs = sorted(py), sorted(rs)
        assert rs == py, (k, spec, search, sort)


def test_page_rows_match(library):
    all_ids = LQ._table_ids_py(FilterSpec(), "", ("title", False), lambda: False)
    rng = random.Random(8)
    pairs = {i: rng.randint(0, 9) for i in rng.sample(all_ids, 50)}
    for start in range(0, len(all_ids), 97):
        ids = all_ids[start:start + 97] + [10**9]
        assert _native(LQ.page_rows, ids, pairs) == LQ._page_rows_py(ids, pairs)


def test_filter_tree_matches(library):
    rng = random.Random(9)
    all_ids = LQ._table_ids_py(FilterSpec(), "", None, lambda: False)
    specs = [None, FilterSpec()] + [_random_spec(rng, library, all_ids) for _ in range(120)]
    for k, spec in enumerate(specs):
        show = dict(SHOW_ALL) if k % 5 else {key: rng.random() < 0.5 for key in SHOW_ALL}
        counts = k % 7 != 3
        py = FT._filter_tree_data_py(spec, show, counts)
        rs = _native(FT.filter_tree_data, spec, show, counts)
        assert rs.keys() == py.keys()
        for key in py:
            assert rs[key] == py[key], (k, spec, key)


def test_lookups_and_summary_match(library, monkeypatch):
    with session_scope() as s:
        ids = [m.id for m in s.query(STLModel.id).limit(60)] + [10**9]
        for kind in ("tag", "scale", "category", "collection", "subcollection", "sculptor", "release"):
            native = lookups.usage_counts(s, kind)
            monkeypatch.setattr(native_db, "library", lambda: None)
            assert native == lookups.usage_counts(s, kind), kind
            monkeypatch.undo()
        for mid in ids:
            native = MS.model_summary(s, mid)
            monkeypatch.setattr(native_db, "library", lambda: None)
            assert native == MS.model_summary(s, mid), mid
            monkeypatch.undo()
