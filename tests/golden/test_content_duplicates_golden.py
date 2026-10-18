"""Golden tests: native content-duplicate reads and scans match Python."""
import random

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "dup_stl_content_pairs"):
    pytest.skip("stl_native predates native content duplicates; rebuild it", allow_module_level=True)

from app import native
from app.database import native_db
from app.database.db import init_db, session_scope
from app.database.models import CrawledFolder, Image, STLFile, STLModel
from app.services import content_duplicates as CD
from app.services import image_duplicates as D
from app.utils import thumbnailer


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    path = tmp_path / "stlLibrary.db"
    init_db(str(path))
    rng = random.Random(11)
    with session_scope() as s:
        s.add_all([CrawledFolder(path="F:/b", pattern="{name}"), CrawledFolder(path="F:/a")])
        for i in range(80):
            m = STLModel(title=rng.choice([f"T{i}", None, ""]), archive_path=f"F:/lib/{i % 5}/m{i}.7z",
                         archive_hash=f"h{i}", archive_size=rng.choice([None, 10]),
                         is_missing=rng.random() < 0.1)
            s.add(m)
            s.flush()
            for name in rng.sample([f"p{k}.stl" for k in range(8)], rng.randint(0, 6)):
                s.add(STLFile(model_id=m.id, filename=name, file_size=rng.choice([None, 1, 2])))
            for k in range(rng.randint(0, 3)):
                s.add(Image(model_id=m.id, filename=f"i{k}.jpg",
                            thumbnail_path=rng.choice([None, f"{m.id}{k}"]),
                            dhash=rng.choice([None, "2:" + "%032x" % rng.getrandbits(8)])))
    lib = stl_native.LibraryDb(str(path))
    yield lib
    lib.close()
    thumbnailer.close_thumbnail_store()


def test_content_duplicates_match(lib, monkeypatch):
    def read(fn, *args):
        monkeypatch.setattr(native_db, "library", lambda: None)
        py = fn(*args)
        monkeypatch.setattr(native_db, "library", lambda: lib)
        rs = fn(*args)
        assert rs == py, (getattr(fn, "__name__", fn), args)
        return py

    for thr in (0.0, 0.3, 0.5, 0.8, 1.0):
        res = read(CD.find_stl_duplicates, thr)
        if thr <= 0.5:
            assert res
    for mid in (1, 5, 40, 999):
        for limit in (1, 24):
            read(CD.thumbnail_image_ids, mid, limit)
    assert read(CD.present_images)
    read(CD.model_info)
    assert read(CD.crawled_folders) == [("F:/a", None), ("F:/b", "{name}")]
    assert read(CD.sidecar_folders)
    # The dHash scan through the native reads (session None) and through a session.
    native_rep = D.find_dhash_duplicates(None, max_dist=4, max_models=50)
    monkeypatch.setattr(native_db, "library", lambda: None)
    with session_scope() as s:
        py_rep = D.find_dhash_duplicates(s, max_dist=4, max_models=50)
    assert native_rep.results == py_rep.results and native_rep.results


def test_stop_and_remove(lib, monkeypatch):
    monkeypatch.setattr(native_db, "library", lambda: lib)
    with pytest.raises(CD.Stopped):
        CD.find_stl_duplicates(0.1, should_stop=lambda: True)
    CD.remove_crawled_folder("F:/a")
    assert CD.crawled_folders() == [("F:/b", "{name}")]
    monkeypatch.setattr(native_db, "library", lambda: None)
    with pytest.raises(CD.Stopped):
        CD.find_stl_duplicates(0.1, should_stop=lambda: True)
