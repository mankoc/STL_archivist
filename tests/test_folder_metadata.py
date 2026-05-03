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
from app.database.db import init_db, session_scope
from app.database.models import Collection, Image, STLModel, Sculptor, Subcollection, Tag
from app.utils.thumbnailer import get_thumbnail_bytes, init_thumbnail_store, save_thumbnail


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
    with session_scope() as session:
        assert folder_has_db_records(folder, session) is False


def test_folder_has_db_records_true_after_insert(tmp_library):
    folder = tmp_library / "models"
    folder.mkdir()
    archive = _touch_archive(folder, "model.7z")

    with session_scope() as session:
        session.add(STLModel(archive_path=str(archive), archive_hash="h"))

    with session_scope() as session:
        assert folder_has_db_records(folder, session) is True


# ---------------------------------------------------------------------------
# export_folder_metadata
# ---------------------------------------------------------------------------

def test_export_no_records_returns_false(tmp_library):
    folder = tmp_library / "empty"
    folder.mkdir()
    _touch_archive(folder, "x.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    with session_scope() as session:
        assert export_folder_metadata(folder, session, thumb_base) is False
    assert not (folder / METADATA_FILENAME).exists()


def test_export_without_thumbnails_produces_archive(tmp_library):
    folder = tmp_library / "models"
    folder.mkdir()
    archive = _touch_archive(folder, "model.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    with session_scope() as session:
        session.add(STLModel(
            archive_path=str(archive), archive_hash="h", title="Model"
        ))

    with session_scope() as session:
        assert export_folder_metadata(
            folder, session, thumb_base, include_thumbnails=False,
        ) is True
    assert (folder / METADATA_FILENAME).exists()


# ---------------------------------------------------------------------------
# Full round-trip: export → import
# ---------------------------------------------------------------------------

def test_round_trip_preserves_fields_and_thumbnail(tmp_library):
    folder = tmp_library / "library"
    folder.mkdir()
    archive = _touch_archive(folder, "cyborg_hulk.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    # Seed one model + thumbnail, export, wipe DB, then import.
    with session_scope() as session:
        sculptor = Sculptor(name="Artist")
        collection = Collection(name="Sci-Fi")
        subcol = Subcollection(name="Cyborg", collection=collection)
        tag = Tag(name="robot")
        session.add_all([sculptor, collection, subcol, tag])
        session.flush()

        model = STLModel(
            archive_path=str(archive),
            archive_hash="abc123",
            title="Cyborg Hulk",
            creator="Artist",
            source="Patreon",
            release_year=2024,
            release_month=3,
            sculptor=sculptor,
            collection=collection,
            subcollection=subcol,
        )
        model.tags.append(tag)
        session.add(model)
        session.flush()

        img = Image(model=model, filename="preview.png")
        session.add(img)
        session.flush()
        img.thumbnail_path = save_thumbnail(_minimal_png(), img.id)
        assert img.thumbnail_path is not None

    with session_scope() as session:
        assert export_folder_metadata(
            folder, session, thumb_base, include_thumbnails=True,
        ) is True
    assert (folder / METADATA_FILENAME).exists()

    # Wipe DB tables and LMDB store; rebuild from METADATA.7z only.
    init_db(":memory:")
    init_thumbnail_store(tmp_library / "thumbnails_restore.lmdb")

    with session_scope() as session:
        imported = import_folder_metadata(folder, session, thumb_base)

    assert imported == [str(archive)]

    with session_scope() as session:
        m = session.query(STLModel).filter_by(archive_path=str(archive)).one()
        assert m.title == "Cyborg Hulk"
        assert m.creator == "Artist"
        assert m.source == "Patreon"
        assert m.release_year == 2024
        assert m.release_month == 3
        assert m.archive_hash == "abc123"
        assert m.sculptor.name == "Artist"
        assert m.collection.name == "Sci-Fi"
        assert m.subcollection.name == "Cyborg"
        assert {t.name for t in m.tags} == {"robot"}
        assert len(m.images) == 1
        restored_img = m.images[0]
        assert restored_img.thumbnail_path is not None
        assert get_thumbnail_bytes(restored_img.id) is not None


def test_import_skips_archives_missing_from_disk(tmp_library):
    folder = tmp_library / "library"
    folder.mkdir()
    archive = _touch_archive(folder, "real.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    with session_scope() as session:
        session.add(STLModel(
            archive_path=str(archive), archive_hash="h", title="Real",
        ))
        # Second record whose archive doesn't exist on disk
        ghost_path = str(folder / "ghost.7z")
        session.add(STLModel(
            archive_path=ghost_path, archive_hash="h2", title="Ghost",
        ))

    with session_scope() as session:
        export_folder_metadata(folder, session, thumb_base, include_thumbnails=False)

    # Fresh DB — re-import. Only "real.7z" exists on disk.
    init_db(":memory:")
    with session_scope() as session:
        imported = import_folder_metadata(folder, session, thumb_base)
    assert imported == [str(archive)]


def test_import_skips_archives_already_in_db(tmp_library):
    folder = tmp_library / "library"
    folder.mkdir()
    archive = _touch_archive(folder, "model.7z")
    thumb_base = tmp_library / "thumbnails.lmdb"

    with session_scope() as session:
        session.add(STLModel(
            archive_path=str(archive), archive_hash="h", title="Model",
        ))
        export_folder_metadata(folder, session, thumb_base, include_thumbnails=False)

    # Same DB still has the record — import should be a no-op.
    with session_scope() as session:
        imported = import_folder_metadata(folder, session, thumb_base)
    assert imported == []
