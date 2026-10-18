"""
Round-trip tests for app.crawler.folder_metadata: export → import via METADATA.7z.
"""
import io
import struct
import zipfile
import zlib
from pathlib import Path

from app.crawler.folder_metadata import (
    METADATA_FILENAME,
    export_folder_metadata,
    folder_has_db_records,
    import_folder_metadata,
)
from app.database.db import init_db
from app.utils.thumbnailer import get_thumbnail_bytes, save_thumbnail
from tests import factory


def _fresh_library(root: Path) -> None:
    """Switch to an empty database (and thumbnail store) under *root*."""
    init_db(str(root / "stlLibrary.db"))


def _minimal_png() -> bytes:
    """1×1 RGB PNG — small, valid, accepted by Pillow."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _touch_archive(folder: Path, name: str) -> Path:
    """Create a tiny valid zip archive so archive_path.exists() succeeds."""
    p = folder / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("placeholder.txt", "")
    p.write_bytes(buf.getvalue())
    return p


# ---------------------------------------------------------------------------
# folder_has_db_records
# ---------------------------------------------------------------------------

def test_folder_has_db_records_false_when_empty(tmp_library):
    folder = tmp_library / "models"
    folder.mkdir()
    _touch_archive(folder, "model.7z")
    assert folder_has_db_records(folder) is False


def test_folder_has_db_records_true_after_insert(tmp_library):
    folder = tmp_library / "models"
    folder.mkdir()
    archive = _touch_archive(folder, "model.7z")

    factory.add_model(str(archive), "h")
    assert folder_has_db_records(folder) is True


# ---------------------------------------------------------------------------
# export_folder_metadata
# ---------------------------------------------------------------------------

def test_export_no_records_returns_false(tmp_library):
    folder = tmp_library / "empty"
    folder.mkdir()
    _touch_archive(folder, "x.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    assert export_folder_metadata(folder, None, thumb_base) is False
    assert not (folder / METADATA_FILENAME).exists()


def test_export_without_thumbnails_produces_archive(tmp_library):
    folder = tmp_library / "models"
    folder.mkdir()
    archive = _touch_archive(folder, "model.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    factory.add_model(str(archive), "h", title="Model")
    assert export_folder_metadata(folder, None, thumb_base, include_thumbnails=False) is True
    assert (folder / METADATA_FILENAME).exists()


# ---------------------------------------------------------------------------
# Full round-trip: export → import
# ---------------------------------------------------------------------------

def test_round_trip_preserves_fields_and_thumbnail(tmp_library):
    folder = tmp_library / "library"
    folder.mkdir()
    archive = _touch_archive(folder, "cyborg_hulk.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    # Seed one model + thumbnail, export, switch to a fresh DB, then import.
    mid = factory.add_model(str(archive), "abc123", title="Cyborg Hulk", creator="Artist",
                            source="Patreon", release_year=2024, release_month=3,
                            sculptor="Artist", collection="Sci-Fi", subcollection="Cyborg",
                            tags=("robot",))
    image_id = factory.add_image(mid, "preview.png")
    thumb = save_thumbnail(_minimal_png(), image_id)
    assert thumb is not None
    factory.execute("UPDATE images SET thumbnail_path = ? WHERE id = ?", thumb, image_id)

    assert export_folder_metadata(folder, None, thumb_base, include_thumbnails=True) is True
    assert (folder / METADATA_FILENAME).exists()

    # Fresh DB and thumbnail store; rebuild from METADATA.7z only.
    _fresh_library(tmp_library / "restore")
    imported = import_folder_metadata(folder)

    assert imported == [str(archive)]

    m = dict(factory.rows("SELECT * FROM models WHERE archive_path = ?", str(archive))[0])
    assert m["title"] == "Cyborg Hulk"
    assert m["creator"] == "Artist"
    assert m["source"] == "Patreon"
    assert m["release_year"] == 2024
    assert m["release_month"] == 3
    assert m["archive_hash"] == "abc123"
    assert factory.lookup_name(m["id"], "sculptor") == "Artist"
    assert factory.lookup_name(m["id"], "collection") == "Sci-Fi"
    assert factory.lookup_name(m["id"], "subcollection") == "Cyborg"
    assert factory.names(m["id"], "tags") == ["robot"]
    images = factory.rows("SELECT id, thumbnail_path FROM images WHERE model_id = ?", m["id"])
    assert len(images) == 1
    assert images[0]["thumbnail_path"] is not None
    assert get_thumbnail_bytes(images[0]["id"]) is not None


def test_import_skips_archives_missing_from_disk(tmp_library):
    folder = tmp_library / "library"
    folder.mkdir()
    archive = _touch_archive(folder, "real.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    factory.add_model(str(archive), "h", title="Real")
    # Second record whose archive doesn't exist on disk
    factory.add_model(str(folder / "ghost.7z"), "h2", title="Ghost")
    export_folder_metadata(folder, None, thumb_base, include_thumbnails=False)

    # Fresh DB — re-import. Only "real.7z" exists on disk.
    _fresh_library(tmp_library / "restore")
    imported = import_folder_metadata(folder)
    assert imported == [str(archive)]


def test_import_skips_archives_already_in_db(tmp_library):
    folder = tmp_library / "library"
    folder.mkdir()
    archive = _touch_archive(folder, "model.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    factory.add_model(str(archive), "h", title="Model")
    export_folder_metadata(folder, None, thumb_base, include_thumbnails=False)

    # Same DB still has the record — import should be a no-op.
    imported = import_folder_metadata(folder)
    assert imported == []
