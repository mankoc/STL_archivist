"""Golden tests: native model editing matches services.model_edit in Python.

Reads run through the service on one database with the native path off and
on.  Writes run through the Python service on database A and through
``LibraryDb`` directly on a copy B (the service would fall back to Python on
A for unsupported input); a native refusal must leave B untouched, and that
operation is then skipped on A.  Both databases are compared at the end.
"""
import random
import sqlite3
from pathlib import Path

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "edit_mass_save"):
    pytest.skip("stl_native predates native model editing; rebuild it", allow_module_level=True)

from app import native
from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import (
    Category, Collection, Image, Release, Scale, Sculptor, STLFile, STLModel, Subcollection, Tag,
)
from app.services import model_edit as ME
from app.utils import thumbnailer
from app.utils.path_pattern import parse_path

SENTINEL = "2000-01-01 00:00:00.000000"
WORDS = ["Dragon", "dragon", "  Elf  ", "Ñandú", "", "28mm", "Comics", "Marvel", "New", "x", "Zelda"]
PATTERNS = ["{creator}/{date} - {name}", "{source}/{creator}/{title}", "{collection}/{subcollection}/{name}",
            "{sculptor} - {release}", "{creator}/{sequence}. {name}", "*/{name}"]


@pytest.fixture
def pair(tmp_path, monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    a = tmp_path / "a" / "stlLibrary.db"
    a.parent.mkdir()
    init_db(str(a))
    rng = random.Random(8)
    with session_scope() as s:
        cols = [Collection(name=n) for n in ("Comics", "Games")]
        sculptors = [Sculptor(name=n) for n in ("Alice", "Bob")]
        rels = [Release(name="May")]
        cats = [Category(name="Busts")]
        tags = [Tag(name=n) for n in ("dragon", "elf", "orc")]
        scales = [Scale(name=n) for n in ("28mm", "75MM")]
        s.add_all(cols + sculptors + rels + cats + tags + scales)
        s.flush()
        subs = [Subcollection(name="Marvel", collection_id=cols[0].id),
                Subcollection(name="Zelda", collection_id=cols[1].id),
                Subcollection(name="Marvel", collection_id=cols[1].id)]
        s.add_all(subs)
        s.flush()
        for i in range(25):
            folder = rng.choice(["Sanix/2024-03 - Dragon", "Patreon/Bob/Elf", "Comics/Marvel", "Alice - May", "Sanix/7. Orc"])
            m = STLModel(title=rng.choice([f"T{i}", None, " padded "]), creator=rng.choice(["Sanix", None, ""]),
                         source=rng.choice(["patreon", None]), archive_path=f"F:/lib/{folder}/m{i}.7z",
                         archive_hash=f"h{i}", url=rng.choice([None, "http://x"]),
                         description=rng.choice([None, " d "]), release_year=rng.choice([None, 2023]),
                         sequence=rng.choice([None, 0, 2]), sculptor=rng.choice(sculptors + [None]))
            if rng.random() < 0.6:
                sub = rng.choice(subs)
                m.subcollection = sub
                m.collection = next(c for c in cols if c.id == sub.collection_id)
            m.tags.extend(rng.sample(tags, rng.randint(0, 2)))
            m.scales.extend(rng.sample(scales, rng.randint(0, 2)))
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 2)):
                s.add(Image(model_id=m.id, filename=f"img{k}.jpg", thumbnail_path=str(m.id * 10 + k) if k else None))
            for k in range(rng.randint(0, 2)):
                s.add(STLFile(model_id=m.id, filename=f"part{k}.stl", file_size=k))
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
              "subcollections", "sculptors", "releases"):
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if "updated_at" in cols:
            k = cols.index("updated_at")
            rows = [r[:k] + (r[k] != SENTINEL,) + r[k + 1:] for r in rows]
        out[t] = sorted(rows, key=repr)
    con.close()
    return out


def test_model_editing_matches(pair, monkeypatch):
    a, b, lib_b = pair
    rng = random.Random(21)

    def read(fn, *args):
        monkeypatch.setattr(native_db, "library", lambda: None)
        py = fn(*args)
        monkeypatch.setattr(native_db, "library", lambda: lib_b)
        rs = fn(*args)
        assert rs == py, (fn.__name__, args)
        monkeypatch.setattr(native_db, "library", lambda: None)
        return py

    def write(py_call, native_call):
        before = _dump(b)
        try:
            rs = native_call()
        except ValueError:
            assert _dump(b) == before, "a refused native write must not change anything"
            return "refused"
        monkeypatch.setattr(native_db, "library", lambda: None)
        py = py_call()
        return rs, py

    refused = 0
    for step in range(120):
        ids = [i for (i,) in sqlite3.connect(a).execute("SELECT id FROM models")] + [10**6]
        mid = rng.choice(ids)
        read(ME.editor_choices)
        read(ME.subcollection_names, rng.choice(["Comics", " Games ", "", "nope"]))
        read(ME.model_for_editor, mid)
        read(ME.ai_model_inputs, mid)
        sel = rng.sample(ids, rng.randint(1, 6))
        read(ME.mass_choices, sel)
        read(ME.archive_paths, sel)
        op = rng.random()
        if op < 0.3:
            form = {k: rng.choice(WORDS) for k in ("title", "creator", "source", "presupports", "url",
                                                   "description", "release", "sculptor", "collection",
                                                   "subcollection", "category")}
            form["date"] = rng.choice(["2024-03", " 2021 ", "bad", "", "2024_7"])
            form["sequence"] = rng.choice([0, 3, -1])
            form["scales"] = rng.sample(["28mm", "28MM", "1/35", " 75mm "], rng.randint(0, 3))
            form["tags"] = rng.sample(["Dragon", "dragon", "new one", "  orc "], rng.randint(0, 3))
            def native_save():
                path = lib_b.edit_save_model_editor(mid, ME._editor_form(form))
                return str(Path(path).parent) if path else None
            r = write(lambda: ME.save_model_editor(mid, form), native_save)
        elif op < 0.55:
            fields = {k: rng.choice(WORDS) for k in ("title", "creator", "description", "source", "sculptor",
                                                     "collection", "subcollection")}
            fields["scale"] = rng.choice(["", "28mm, 1/35", "75mm"])
            fields["year"] = rng.choice([None, 2020])
            tags = rng.sample(["Dragon", "fresh", " elf "], rng.randint(0, 3))
            ow = rng.random() < 0.5
            f = {k: str(fields.get(k) or "") for k in ("title", "creator", "description", "source",
                                                       "sculptor", "collection", "subcollection", "scale")}
            r = write(lambda: ME.ai_commit(mid, fields, tags, ow),
                      lambda: lib_b.edit_ai_commit(mid, f, fields["year"], tags, ow))
        else:
            sel_ids = rng.sample(ids, rng.randint(1, 8))
            pattern = rng.choice(PATTERNS + [""])
            paths = ME.archive_paths(sel_ids)
            path_results = {}
            if pattern:
                for i in sel_ids:
                    if i in paths:
                        ex = parse_path(pattern, paths[i])
                        if ex:
                            path_results[i] = ex
            clearing = set(rng.sample(["creator", "source", "release_date", "url", "title", "release",
                                       "collection", "subcollection", "category", "sculptor", "scale",
                                       "presupports", "tags"], rng.randint(0, 3)))
            manual = {}
            for field in ("source", "creator", "release_date", "url", "title"):
                if field in clearing:
                    manual[field] = None
                elif rng.random() < 0.3:
                    manual[field] = rng.choice(["{creator} — {title}", "{filename}", "{folder}", "2024-01",
                                                "{date}", "fixed", "{unknown}"])
            find = None
            if rng.random() < 0.4:
                find = {"find": rng.choice(["a", "Dr", "ñ", "x.y", "T"]), "replace": rng.choice(["", "B", r"\1", "Z"]),
                        "case": rng.random() < 0.5, "regex": rng.random() < 0.2,
                        "fields": rng.sample(["title", "creator", "source", "collection", "subcollection"], 2)}
            plan = {"model_ids": sel_ids, "path_results": path_results, "overwrite": rng.random() < 0.5,
                    "manual": manual, "clearing": sorted(clearing),
                    "scale": rng.choice(["", "28mm, 28MM", "1/35"]),
                    "presupports": rng.choice([None, "Lychee", ""]),
                    "release": rng.choice(["", "{creator} drop", "June"]),
                    "collection": rng.choice(["", "Comics", "Brand New"]),
                    "subcollection": rng.choice(["", "Marvel", "Fresh"]),
                    "category": rng.choice(["", "Busts", "Minis"]),
                    "sculptor": rng.choice(["", "Alice", "Carol"]),
                    "tags": rng.choice(["", "{creator}, extra", "a, A", "one"]),
                    "find": find, "trim": rng.random() < 0.5}
            r = write(lambda: ME._mass_save_py(plan, _compile(find)),
                      lambda: lib_b.edit_mass_save(ME._native_plan(plan)))
        if r == "refused":
            refused += 1
        else:
            assert r[0] == r[1], (step, r)
    thumbnailer.close_thumbnail_store()
    lib_b.close()
    assert _dump(a) == _dump(b)
    assert 0 < refused < 60          # the refusal path was exercised, but not everything refused


def _compile(find):
    import re
    if not find:
        return None
    raw = find["find"] if find["regex"] else re.escape(find["find"])
    return re.compile(raw, 0 if find["case"] else re.IGNORECASE)
