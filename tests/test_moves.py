"""Tests for moved-archive handling: the shared service and the crawler's early match."""
import io
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image as PILImage

import app.utils.thumbnailer as T
from app.config import CrawlerConfig
from app.crawler.crawler import CrawlerWorker
from app.database import native_db
from app.database.db import backfill_archive_folder
from app.services import moves
from tests import factory


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


def _zip(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("model.stl", f"solid {marker}\nendsolid\n")
        zf.writestr("render.jpg", _jpeg((10, 120, 200)))
    return path


def _crawl(folder: Path) -> tuple[CrawlerWorker, list]:
    cfg = CrawlerConfig()
    cfg.metadata_sidecar_enabled = False
    worker = CrawlerWorker(str(folder), config=cfg)
    moved: list = []
    worker.file_moved.connect(lambda old, new: moved.append((old, new)))
    worker.run()        # synchronously, no thread
    return worker, moved


# ── service ──────────────────────────────────────────────────────────────────

def test_archive_folder_for_uses_forward_slashes():
    assert moves.archive_folder_for(r"F:\STL\Studio\a.7z") == "F:/STL/Studio"
    assert moves.archive_folder_for("/lib/Studio/a.7z") == "/lib/Studio"


def test_index_by_hash_skips_empty_fallback_and_shared():
    recs = [{"id": 1, "hash": "a"}, {"id": 2, "hash": "b"}, {"id": 3, "hash": "b"},
            {"id": 4, "hash": ""}, {"id": 5, "hash": "fallback:x"}]
    assert moves.index_by_hash(recs) == {"a": recs[0]}


def test_merge_into_missing_keeps_old_metadata(thumb_store):
    old = factory.add_model(r"F:\old\a.7z", "h", title="Edited title", is_missing=1, tags=("mine",))
    factory.add_image(old, "old.jpg")
    new = factory.add_model(r"F:\new\a.7z", "h", title="Shallow", archive_size=10, archive_mtime_ns=5)
    new_img = factory.add_image(new, "n.jpg", thumbnail_path="x")
    new_ref = str(new_img)
    factory.execute("UPDATE images SET thumbnail_path = ? WHERE id = ?", new_ref, new_img)
    refs = native_db.library().merge_into_missing(old, new)
    assert refs == [new_ref]
    (m,) = [dict(r) for r in factory.rows("SELECT * FROM models")]
    assert m["id"] == old and m["title"] == "Edited title"
    assert m["archive_path"] == r"F:\new\a.7z" and m["archive_folder"] == "F:/new"
    assert (m["archive_size"], m["archive_mtime_ns"], m["is_missing"]) == (10, 5, 0)
    assert factory.names(old, "tags") == ["mine"]
    assert [r[0] for r in factory.rows("SELECT filename FROM images WHERE model_id = ?", old)] == ["old.jpg"]


def test_delete_thumbnails_removes_all_in_one_call(thumb_store):
    for i in (1, 2, 3):
        T.write_raw_thumbnail(i, _jpeg((i * 50, 0, 0)))
    T.delete_thumbnails(["1", 2, None, "not-an-id"])
    assert T.get_thumbnail_bytes(1) is None and T.get_thumbnail_bytes(2) is None
    assert T.get_thumbnail_bytes(3) is not None


def test_backfill_normalises_backslash_folders():
    factory.add_model(r"F:\x\a.7z", "h", archive_folder=r"F:\x")
    backfill_archive_folder()
    assert factory.scalar("SELECT archive_folder FROM models") == "F:/x"


# ── crawler ──────────────────────────────────────────────────────────────────

def test_crawler_matches_moved_archive_before_extracting(tmp_path, thumb_store):
    src = _zip(tmp_path / "old" / "Knight.zip", "knight")
    _crawl(tmp_path / "old")
    model_id = factory.scalar("SELECT id FROM models")
    factory.execute("UPDATE models SET title = 'My edited title'")
    image_ids = [r[0] for r in factory.rows("SELECT id FROM images ORDER BY id")]
    assert image_ids, "first crawl should have stored a thumbnail"

    dest = tmp_path / "new" / "sub" / "Knight.zip"
    dest.parent.mkdir(parents=True)
    shutil.move(src, dest)

    import app.crawler.crawler as crawler_mod
    with patch.object(crawler_mod, "extract_archive_contents",
                      wraps=crawler_mod.extract_archive_contents) as extract:
        worker, moved = _crawl(tmp_path / "new")
    extracted = {Path(c.args[0]).name for c in extract.call_args_list}

    assert "Knight.zip" not in extracted          # no re-extraction of the moved file
    assert moved == [(str(src), str(dest))]
    assert worker._stats_new == 0
    (m,) = [dict(r) for r in factory.rows("SELECT * FROM models")]   # no shallow duplicate
    assert m["id"] == model_id and m["title"] == "My edited title"
    assert m["archive_path"] == str(dest)
    assert m["archive_folder"] == dest.parent.as_posix()
    assert not m["is_missing"]
    assert [r[0] for r in factory.rows("SELECT id FROM images ORDER BY id")] == image_ids
    assert all(T.get_thumbnail_bytes(i) for i in image_ids)   # thumbnails kept


def test_crawler_still_reconciles_name_size_matches_after_crawl(tmp_path, thumb_store):
    # Different content hash but same name and size: matched after the crawl.
    _zip(tmp_path / "old" / "Orc.zip", "orc-a")
    _crawl(tmp_path / "old")
    model_id = factory.scalar("SELECT id FROM models")
    factory.execute("UPDATE models SET archive_hash = 'stale-hash', archive_size = ?",   # forces the fallback path
                    (tmp_path / "old" / "Orc.zip").stat().st_size)
    (tmp_path / "old" / "Orc.zip").rename(tmp_path / "Orc.zip.bak")
    dest = tmp_path / "new" / "Orc.zip"
    dest.parent.mkdir()
    (tmp_path / "Orc.zip.bak").rename(dest)

    _worker, moved = _crawl(tmp_path / "new")
    assert len(moved) == 1
    (m,) = [dict(r) for r in factory.rows("SELECT * FROM models")]
    assert m["id"] == model_id and m["archive_path"] == str(dest)
    assert m["archive_folder"] == dest.parent.as_posix()
