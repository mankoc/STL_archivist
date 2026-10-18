"""Golden tests: the crawler's native database phases match the ORM phases.

The same crawl scenario runs twice over an identical, deterministically
rebuilt source tree at one path: once with the Python (SQLAlchemy) phases,
once with ``stl_native.LibraryDb``'s.  The databases, the stored thumbnails
and the emitted signals are compared after every step.  The scenario covers
new archives, an info.json with tags and a release, folder images, a path
pattern, forced values, a changed archive, an early move (by hash), a move
reconciled after the crawl, a deleted archive and an unreadable one.
"""
import io
import json
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest
from PIL import Image as PILImage

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "LibraryDb") or not hasattr(stl_native.LibraryDb, "crawl_upsert_batch"):
    pytest.skip("stl_native predates the native crawl phases; rebuild it", allow_module_level=True)

from app import native
from app.config import CrawlerConfig
from app.crawler.crawler import CrawlerWorker
from app.database.db import init_db
from app.utils import thumbnailer

MTIME = 1_700_000_000
TIMESTAMPS = {"created_at", "updated_at", "last_crawled_at"}


CrawlerWorker._native_library_real = staticmethod(CrawlerWorker._native_library)


@pytest.fixture(autouse=True)
def _native_on(monkeypatch):
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()
    yield
    thumbnailer.close_thumbnail_store()


def _png(color) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (40, 30), color).save(buf, "PNG")
    return buf.getvalue()


_PDF_CACHE: list[bytes] = []


def _pdf() -> bytes:
    """One PDF per test run (PyMuPDF writes a timestamp into each new one)."""
    if _PDF_CACHE:
        return _PDF_CACHE[0]
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=200, height=150)
    page.insert_text((20, 60), "Assembly guide")
    _PDF_CACHE.append(doc.tobytes())
    return _PDF_CACHE[0]


def _cmyk_jpeg() -> bytes:
    buf = io.BytesIO()
    PILImage.new("CMYK", (30, 20), (0, 128, 255, 0)).save(buf, "JPEG")
    return buf.getvalue()


def _zip(path: Path, members: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(zipfile.ZipInfo(name, (2024, 1, 1, 0, 0, 0)), data)
    path.write_bytes(buf.getvalue())
    os.utime(path, (MTIME, MTIME))


def _build(src: Path) -> None:
    """The initial source tree (identical bytes and mtimes every time)."""
    if src.exists():
        shutil.rmtree(src)
    pat = src / "Patreon" / "Sanix" / "2024-03"
    _zip(pat / "Big_Dragon-Bust.zip", {
        "dragon/body_presupported.stl": b"solid", "dragon/a.png": _png("red"),
        "dragon/b.png": _png("blue"), "dragon/bad.png": b"not an image",
        "info.json": json.dumps({"name": "Dragon", "tags": ["Fantasy", " big "], "release": "March"}),
        "link.url": b"[InternetShortcut]\nURL=http://example.com/d\n",
    })
    _zip(pat / "Elf 28mm.zip", {"elf.stl": b"solid", "elf.jpg": _png("green")})
    _zip(src / "Other" / "orc.zip", {"orc.stl": b"solid"})
    _zip(src / "Other" / "manual.zip", {"guide.pdf": _pdf(), "cmyk.jpg": _cmyk_jpeg(), "m.stl": b"solid m"})
    _zip(src / "Other" / "same_content_1.zip", {"x.stl": b"solid x"})
    (src / "Other" / "cover.png").write_bytes(_png("yellow"))
    (src / "Patreon" / "Sanix" / "banner.jpg").write_bytes(_png("purple"))
    (src / "Other" / "broken.zip").write_bytes(b"PK\x03\x04 broken")
    os.utime(src / "Other" / "broken.zip", (MTIME, MTIME))


def _step2(src: Path) -> None:
    """Change one archive, move two (one by hash early, one renamed), delete one, add one."""
    pat = src / "Patreon" / "Sanix" / "2024-03"
    _zip(pat / "Elf 28mm.zip", {"elf.stl": b"solid v2", "elf.jpg": _png("white"), "e2.png": _png("black")})
    os.utime(pat / "Elf 28mm.zip", (MTIME + 5, MTIME + 5))
    moved = src / "Moved"
    moved.mkdir()
    shutil.move(str(src / "Other" / "orc.zip"), str(moved / "orc.zip"))
    os.utime(moved / "orc.zip", (MTIME, MTIME))
    (src / "Other" / "same_content_1.zip").unlink()
    _zip(src / "New" / "2025 Hero.zip", {"hero.stl": b"solid h", "h.png": _png("gray"),
                                         "info.json": b"{\"name\": \"Hero\", \"tags\": [\"A\"]}"})


def _dump(db: Path):
    con = sqlite3.connect(db)
    out = {}
    for t in ("models", "images", "stl_files", "model_tags", "model_scales", "tags", "scales", "sculptors",
              "collections", "subcollections", "categories", "releases", "crawled_folders"):
        cur = con.execute(f"SELECT * FROM {t} ORDER BY 1, 2")
        cols = [d[0] for d in cur.description]
        keep = [k for k, c in enumerate(cols) if c not in TIMESTAMPS]
        out[t] = [tuple(r[k] for k in keep) for r in cur.fetchall()]
    ids = [i for (i,) in con.execute("SELECT id FROM images WHERE thumbnail_path IS NOT NULL ORDER BY id")]
    con.close()
    out["thumbs"] = [(i, thumbnailer.get_thumbnail_bytes(i)) for i in ids]
    return out


def _crawl(src: Path, pattern: str, **config):
    cfg = CrawlerConfig(metadata_sidecar_enabled=False, store_stl_file_list=True, max_workers=2,
                        max_thumbnails=3, **config)
    w = CrawlerWorker(str(src), pattern=pattern, config=cfg)
    events = []
    w.file_moved.connect(lambda a, b: events.append(("moved", a, b)))
    w.file_deleted.connect(lambda a: events.append(("deleted", a)))
    w.archive_result.connect(lambda a, b: events.append(("result", a, b)))
    w.counts_changed.connect(lambda *c: events.append(("counts",) + c))
    w.run()
    return events


def _scenario(tmp_path: Path, name: str, use_native: bool, monkeypatch, keep_patches: bool = False):
    src = tmp_path / "src"
    _build(src)
    data = tmp_path / name
    data.mkdir()
    init_db(str(data / "stlLibrary.db"))
    if not use_native:
        monkeypatch.setattr(CrawlerWorker, "_native_library", staticmethod(lambda: None))
    else:
        if not keep_patches:
            monkeypatch.undo()      # the real _native_library again
        monkeypatch.setattr(CrawlerWorker, "_native_library", staticmethod(CrawlerWorker._native_library_real))
        monkeypatch.delenv("STL_NATIVE", raising=False)
        native.reload()
        assert CrawlerWorker._native_library() is not None
    db = data / "stlLibrary.db"
    steps = []
    steps.append((_crawl(src, "/{source}/{creator}/{date}/{name}"), _dump(db)))
    _step2(src)
    steps.append((_crawl(src, "/{source}/{creator}/{date}/{name}", force_sculptor="Forced S",
                         force_scale="75mm", warn_low_compression=True, low_compression_threshold=0.1), _dump(db)))
    steps.append((_crawl(src, "", force_rescan=True, force_date="2023-07"), _dump(db)))
    thumbnailer.close_thumbnail_store()
    return steps


def _modes(monkeypatch, mode):
    """``"py"``: the ORM phases; ``"db"``: Python orchestration over the native
    database phases; ``"rs"``: the whole crawl in the native core."""
    runs = []
    real = CrawlerWorker._run_native
    monkeypatch.setattr(CrawlerWorker, "_run_native", lambda self: runs.append(1) or real(self))
    if mode == "db":
        monkeypatch.setattr(CrawlerWorker, "_run_native", lambda self: pytest.fail("native crawl used"))
        orig = CrawlerWorker._reset_state

        def reset(self):
            orig(self)
            if self._lib is not None:
                self._lib = _NoCrawl(self._lib)
        monkeypatch.setattr(CrawlerWorker, "_reset_state", reset)
    return runs


class _NoCrawl:
    """A ``LibraryDb`` without ``crawl``: the worker orchestrates in Python."""

    def __init__(self, lib):
        self._lib = lib

    def __getattr__(self, name):
        if name == "crawl":
            raise AttributeError(name)
        return getattr(self._lib, name)


@pytest.mark.parametrize("mode", ["rs", "db"])
def test_crawl_native_matches_orm(tmp_path, monkeypatch, mode):
    py = _scenario(tmp_path, "py", False, monkeypatch)
    runs = _modes(monkeypatch, mode)
    rs = _scenario(tmp_path, "rs", True, monkeypatch, keep_patches=True)
    assert bool(runs) == (mode == "rs")
    for k, ((py_events, py_db), (rs_events, rs_db)) in enumerate(zip(py, rs)):
        assert rs_events == py_events, f"step {k}: signals"
        for table in py_db:
            if rs_db[table] != py_db[table]:
                diff = [(a, b) for a, b in zip(py_db[table], rs_db[table]) if a != b][:3]
                pytest.fail(f"step {k}: {table}: {len(py_db[table])} vs {len(rs_db[table])} rows; py/rs {diff}")
    # The scenario exercised what it is meant to.
    first_db = py[0][1]
    assert len(first_db["models"]) == 6 and first_db["thumbs"]
    assert sum(1 for r in first_db["images"] if "guide.pdf" in str(r) or "cmyk.jpg" in str(r)) == 2
    assert any(e[0] == "moved" for e in py[1][0]) and any(e[0] == "deleted" for e in py[1][0])
