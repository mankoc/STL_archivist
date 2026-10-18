"""Golden tests: native viewer reads (services.model_views) match Python."""
import random

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "views_grid_models"):
    pytest.skip("stl_native predates native viewer reads; rebuild it", allow_module_level=True)

from app import native
from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import (
    Category, Collection, Image, Release, Scale, Sculptor, STLFile, STLModel, Subcollection, Tag,
)
from app.services import image_similarity as IS
from app.services import model_summary as MS
from app.services import model_views as MV
from app.utils import thumbnailer


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    path = tmp_path / "stlLibrary.db"
    init_db(str(path))
    rng = random.Random(3)
    folders = ["F:/lib/A", "F:/lib/A/sub", "F:/lib/A_b", "F:/lib/b%", "F:/lib", "f:/LIB/a", None, ""]
    with session_scope() as s:
        cols = [Collection(name="Comics"), Collection(name="Games")]
        tags = [Tag(name=n) for n in ("z", "a", "m")]
        scales = [Scale(name=n) for n in ("75mm", "28mm")]
        sculptors = [Sculptor(name="Alice"), Sculptor(name="Bob")]
        s.add_all(cols + tags + scales + sculptors + [Category(name="Busts"), Release(name="May")])
        s.flush()
        sub = Subcollection(name="Marvel", collection_id=cols[0].id)
        s.add(sub)
        s.flush()
        for i in range(50):
            folder = rng.choice(folders)
            m = STLModel(title=rng.choice([f"T{i}", None, ""]), creator=rng.choice(["C", None]),
                         source=rng.choice(["patreon", None]), archive_path=f"{folder or 'X:'}/m{i}.7z",
                         archive_folder=folder, archive_hash=f"h{i}", release_year=rng.choice([None, 2024]),
                         release_month=rng.choice([None, 4]), presupports=rng.choice([None, "Lychee"]),
                         warning=rng.choice([None, "w"]), description=rng.choice([None, "d"]),
                         url=rng.choice([None, "u"]), sculptor=rng.choice(sculptors + [None]),
                         collection=rng.choice(cols + [None]))
            if rng.random() < 0.3:
                m.subcollection = sub
            m.tags.extend(rng.sample(tags, rng.randint(0, 3)))
            m.scales.extend(rng.sample(scales, rng.randint(0, 2)))
            s.add(m)
            s.flush()
            for k in range(rng.randint(0, 3)):
                s.add(Image(model_id=m.id, filename=f"i{k}.jpg", thumbnail_path=rng.choice([None, f"{m.id}{k}"]),
                            dhash=rng.choice([None, "2:" + "0" * 31 + str(k)])))
            for name in rng.sample(["b.stl", "a.stl", "c.lys"], rng.randint(0, 3)):
                s.add(STLFile(model_id=m.id, filename=name, file_size=rng.choice([None, 3]),
                              archive_hash=rng.choice([None, "x"])))
    lib = stl_native.LibraryDb(str(path))
    yield lib
    lib.close()
    thumbnailer.close_thumbnail_store()


def test_viewer_reads_match(lib, monkeypatch):
    def read(fn, *args):
        monkeypatch.setattr(native_db, "library", lambda: None)
        py = fn(*args)
        monkeypatch.setattr(native_db, "library", lambda: lib)
        rs = fn(*args)
        assert rs == py, (fn.__name__, args)
        return py

    assert read(MV.archive_folders)
    for folder in ["F:/lib/A", "F:/lib", "F:\\lib\\A", "F:/lib/b%", "f:/LIB/a", "", "nope"]:
        for sub in (False, True):
            read(MV.folder_models, folder, sub)
    ids = list(range(0, 53))
    assert read(MV.grid_models, ids)
    read(MV.grid_models, [])
    for mid in ids:
        read(MV.stl_files, mid)
        read(MV.model_label, mid)
        read(lambda m: MS.model_summary(None, m), mid)
    assert read(MV.duplicate_rows)
    assert read(MV.image_hash_rows)
