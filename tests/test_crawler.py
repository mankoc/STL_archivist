"""
Tests for crawler utilities: archive hashing and extraction, path-pattern
behaviour, and end-to-end crawls (unchanged archives are skipped, changed
ones get fresh thumbnails).
"""
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image as PILImage

from app.config import CrawlerConfig
from tests import factory


def _jpeg(color) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


def _zip(path: Path, color, marker: str = "m") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("model.stl", f"solid {marker}\nendsolid\n")
        zf.writestr("render.jpg", _jpeg(color))
    return path


def _crawl(folder: Path):
    from app.crawler.crawler import CrawlerWorker
    cfg = CrawlerConfig()
    cfg.metadata_sidecar_enabled = False
    worker = CrawlerWorker(str(folder), config=cfg)
    worker.run()        # synchronously, no thread
    return worker


# ---------------------------------------------------------------------------
# test_hash_file
# ---------------------------------------------------------------------------

def test_archive_hash_stable():
    """archive_hash must return the same value for identical archive content."""
    from app.crawler.archive import archive_hash

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"alpha")
        zf.writestr("b.txt", b"beta")
    buf.seek(0)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(buf.read())
    tmp_path = Path(f.name)

    try:
        h1 = archive_hash(tmp_path)
        h2 = archive_hash(tmp_path)
        assert h1 == h2
        assert isinstance(h1, str)
        assert len(h1) > 0
    finally:
        tmp_path.unlink()


# ---------------------------------------------------------------------------
# test_extract_zip_contents
# ---------------------------------------------------------------------------

def test_extract_zip_contents_round_trip():
    """Round-trip: zip in memory → disk → extract_archive_contents → verify."""
    from app.crawler.archive import extract_archive_contents

    img_bytes = b"\xff\xd8\xff" + b"\x00" * 100  # JPEG-like header + padding
    json_bytes = b'{"name": "test"}'
    stl_bytes = b"solid test\nendsolid test\n"   # must be ignored

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("preview.jpg", img_bytes)
        zf.writestr("info.json", json_bytes)
        zf.writestr("model.stl", stl_bytes)
    buf.seek(0)

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(buf.read())
        tmp_path = Path(f.name)

    try:
        contents = extract_archive_contents(tmp_path)
        assert "preview.jpg" in contents
        assert "info.json" in contents
        assert "model.stl" not in contents        # .stl is not a wanted type
        assert contents["preview.jpg"] == img_bytes
        assert contents["info.json"] == json_bytes
    finally:
        tmp_path.unlink()


# ---------------------------------------------------------------------------
# test_pattern_to_regex_no_dir_crossing
# ---------------------------------------------------------------------------

def test_pattern_to_regex_no_dir_crossing():
    """`*` must not match a `/` — it should consume exactly one path segment.

    The current implementation maps `*` to `.*` (greedy, crosses directories).
    The correct behaviour is `[^/]+` (stops at the first `/`).

    With pattern `*/{title}` and path `a/b/c`:
    - Correct ([^/]+): first match is a/b  → title="b"
    - Buggy   (.*):    greedy match is a/b/c but .* consumes a/b, title="c"
    """
    from app.utils.path_pattern import pattern_to_regex

    rx = pattern_to_regex("*/{title}")
    assert rx is not None

    m = rx.search("a/b/c")
    assert m is not None, "pattern should match somewhere in 'a/b/c'"
    assert "/" not in m.group("title"), (
        "`{title}` captured a path containing `/`; `*` likely consumed too many segments"
    )
    assert m.group("title") == "b", (
        f"Expected title='b' (one-segment wildcard), got {m.group('title')!r}. "
        "This indicates `*` is using `.*` instead of `[^/]+`."
    )


# ---------------------------------------------------------------------------
# End-to-end crawls
# ---------------------------------------------------------------------------

def test_crawl_stores_model_and_thumbnail(tmp_path):
    from app.utils.thumbnailer import get_thumbnail_bytes
    _zip(tmp_path / "lib" / "Knight.zip", (10, 120, 200))
    _crawl(tmp_path / "lib")
    (m,) = factory.rows("SELECT * FROM models")
    assert m["archive_path"] == str(tmp_path / "lib" / "Knight.zip")
    assert m["archive_folder"] == (tmp_path / "lib").as_posix()
    (img,) = factory.rows("SELECT id FROM images WHERE model_id = ?", m["id"])
    assert get_thumbnail_bytes(img["id"])


def test_crawler_skips_unchanged_archive(tmp_path):
    import app.crawler.crawler as crawler_mod
    _zip(tmp_path / "lib" / "Knight.zip", (10, 120, 200))
    _crawl(tmp_path / "lib")
    factory.execute("UPDATE models SET title = 'Edited'")
    with patch.object(crawler_mod, "extract_archive_contents",
                      wraps=crawler_mod.extract_archive_contents) as extract:
        _crawl(tmp_path / "lib")
    assert extract.call_count == 0
    assert factory.scalar("SELECT title FROM models") == "Edited"


def test_thumbnail_refresh_on_hash_change(tmp_path):
    from app.utils.thumbnailer import get_thumbnail_bytes
    path = _zip(tmp_path / "lib" / "Orc.zip", (200, 10, 10), "a")
    _crawl(tmp_path / "lib")
    old_hash = factory.scalar("SELECT archive_hash FROM models")
    (old_img,) = [r[0] for r in factory.rows("SELECT id FROM images")]
    old_thumb = get_thumbnail_bytes(old_img)

    _zip(path, (10, 200, 10), "changed")
    _crawl(tmp_path / "lib")

    assert factory.count("models") == 1
    assert factory.scalar("SELECT archive_hash FROM models") != old_hash
    (new_img,) = [r[0] for r in factory.rows("SELECT id FROM images")]
    assert get_thumbnail_bytes(new_img) not in (None, old_thumb)
