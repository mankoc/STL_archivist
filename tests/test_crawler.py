"""
Tests for crawler utilities: file hashing, archive extraction, path-pattern
behaviour, apply_extracted_to_model, and CrawlerWorker._upsert_archive logic.
"""
import io
import tempfile
import types
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import CrawlerConfig
from app.database.db import init_db, session_scope
from app.database.models import Image, STLModel


@pytest.fixture(autouse=True)
def in_memory_db():
    """Fresh in-memory SQLite for each test."""
    init_db(":memory:")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_worker(source_dir: str = "/tmp", pattern: str = ""):
    """Return a duck-typed stand-in for CrawlerWorker.

    CrawlerWorker inherits from QThread; creating a real instance requires Qt
    internals.  Instead we build a SimpleNamespace with the attributes that
    _upsert_archive reads from `self`, then call the method as an unbound
    function so no Qt initialisation is needed.
    """
    from app.crawler.crawler import CrawlerWorker

    ns = types.SimpleNamespace(
        source_dir=Path(source_dir),
        _pattern=pattern,
        _config=CrawlerConfig(),
        _stop_requested=False,
        log=MagicMock(),
        # Bind the real method to our namespace so it runs with our attributes
        _upsert_archive=lambda *a, **kw: CrawlerWorker._upsert_archive(ns, *a, **kw),
    )
    return ns


# ---------------------------------------------------------------------------
# test_hash_file
# ---------------------------------------------------------------------------

def test_hash_file_stable():
    """_hash_file must return the same value for identical content."""
    from app.crawler.crawler import _hash_file

    content = b"Hello, STL database!\n" * 1000
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(content)
    tmp_path = Path(f.name)

    try:
        h1 = _hash_file(tmp_path)
        h2 = _hash_file(tmp_path)
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
# test_apply_extracted_overwrite_false
# ---------------------------------------------------------------------------

def test_apply_extracted_overwrite_false():
    """Fields already set on a model are preserved when overwrite=False."""
    from app.utils.path_pattern import apply_extracted_to_model

    with session_scope() as session:
        model = STLModel(
            archive_path="/tmp/test.7z",
            archive_hash="aaa",
            title="Original Title",
            author="Original Author",
        )
        session.add(model)
        session.flush()

        extracted = {
            "title":  "New Title",
            "author": "New Author",
            "source": "Patreon",   # was empty → should be filled
        }
        apply_extracted_to_model(session, model, extracted, overwrite=False)

        assert model.title  == "Original Title"   # must NOT be overwritten
        assert model.author == "Original Author"   # must NOT be overwritten
        assert model.source == "Patreon"           # was None → filled in


# ---------------------------------------------------------------------------
# test_crawler_skips_unchanged_hash
# ---------------------------------------------------------------------------

def test_crawler_skips_unchanged_hash():
    """When archive hash matches the DB record, extract_metadata is never called.

    In the new design the caller pre-computes the hash and passes contents=None
    to signal that the archive is unchanged.
    """
    KNOWN_HASH = "deadbeefdeadbeef"

    with session_scope() as session:
        session.add(STLModel(
            archive_path="/tmp/unchanged.zip",
            archive_hash=KNOWN_HASH,
            title="Existing",
        ))

    worker = _make_worker()

    with patch("app.crawler.crawler.extract_metadata") as mock_meta:
        with session_scope() as session:
            existing = session.query(STLModel).filter_by(
                archive_path="/tmp/unchanged.zip"
            ).first()
            worker._upsert_archive(
                Path("/tmp/unchanged.zip"), session, 5, 512,
                current_hash=KNOWN_HASH,
                existing=existing,
                contents=None,   # None → hash unchanged, skip extraction
            )

        mock_meta.assert_not_called()


# ---------------------------------------------------------------------------
# test_thumbnail_refresh_on_hash_change
# ---------------------------------------------------------------------------

def test_thumbnail_refresh_on_hash_change():
    """Old thumbnails are deleted and new ones generated when the hash changes."""
    from app.crawler.metadata import ModelMetadata

    OLD_HASH = "oldhash"
    NEW_HASH = "newhash"

    with session_scope() as session:
        model = STLModel(
            archive_path="/tmp/changed.zip",
            archive_hash=OLD_HASH,
            title="OldModel",
        )
        img = Image(
            model=model,
            filename="thumb.jpg",
            thumbnail_path="thumbs/1.jpg",
        )
        session.add(model)
        session.add(img)

    worker = _make_worker()

    # Minimal PNG-like bytes so save_thumbnail gets real image data
    import struct, zlib
    def _minimal_png() -> bytes:
        sig = b"\x89PNG\r\n\x1a\n"
        def chunk(name, data):
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        idat_data = zlib.compress(b"\x00\xff\xff\xff")
        idat = chunk(b"IDAT", idat_data)
        iend = chunk(b"IEND", b"")
        return sig + ihdr + idat + iend

    img_bytes = _minimal_png()

    with (
        patch("app.crawler.crawler.extract_metadata",
              return_value=ModelMetadata(title="NewModel")),
        patch("app.crawler.crawler.delete_thumbnail") as mock_delete,
        patch("app.crawler.crawler.save_thumbnail", return_value="thumbs/2.jpg"),
    ):
        with session_scope() as session:
            existing = session.query(STLModel).filter_by(
                archive_path="/tmp/changed.zip"
            ).first()
            worker._upsert_archive(
                Path("/tmp/changed.zip"), session, 5, 512,
                current_hash=NEW_HASH,
                existing=existing,
                contents={"thumb.jpg": img_bytes},  # pre-extracted by caller
            )

        # Old thumbnail must have been deleted
        mock_delete.assert_called_once_with("thumbs/1.jpg")

    # DB must reflect the updated hash and new title
    with session_scope() as session:
        m = session.query(STLModel).filter_by(archive_path="/tmp/changed.zip").first()
        assert m is not None
        assert m.archive_hash == NEW_HASH
        assert m.title == "NewModel"
