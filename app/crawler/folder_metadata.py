"""
Per-folder METADATA.7z sidecar files.

Each folder containing STL archives can carry a METADATA.7z that stores:
  - metadata.json  : DB record snapshot for every direct-child archive
  - thumbnails/    : JPEG thumbnails referenced by the JSON

This allows a library to be restored on a new machine without re-crawling
every archive: the crawler imports the sidecar first, then only re-extracts
archives whose hash has changed since the snapshot was written.

Both directions run in the native core (``rust/stl-core/src/storage/sidecar.rs``),
reading and writing thumbnails through the thumbnail store this process
holds (``thumbnailer.native_store()``).

Public API
----------
export_folder_metadata(folder, session, thumb_base_dir)
    Write/update METADATA.7z for all direct-child archives in *folder*.

import_folder_metadata(folder, session, thumb_base_dir)
    Populate the DB from METADATA.7z; return list of imported archive_paths.

folder_has_db_records(folder, session)
    Return True if any direct-child archive of *folder* is already in the DB.

The *session* arguments are unused (kept for existing callers).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

METADATA_FILENAME = "METADATA.7z"


def folder_has_db_records(folder: Path, _session=None) -> bool:
    """Return True when at least one direct-child archive of *folder* is in DB."""
    from app.crawler.archive import ARCHIVE_SUFFIXES
    from app.database import native_db

    try:
        child_paths = [
            str(p)
            for p in Path(folder).iterdir()
            if p.suffix.lower() in ARCHIVE_SUFFIXES and p.name != METADATA_FILENAME
        ]
    except OSError:
        return False
    return bool(child_paths) and native_db.library().any_archive_path(child_paths)


def export_folder_metadata(
    folder: Path,
    _session=None,
    thumb_base_dir: "Path | None" = None,
    include_thumbnails: bool = True,
) -> bool:
    """
    Create or replace METADATA.7z in *folder* with a snapshot of all
    direct-child archive records currently in the DB.

    When *include_thumbnails* is False the ``images`` lists are empty and no
    image files are written.  Returns True when the file was written, False
    when there was nothing to do (no matching records) or the write failed.
    """
    from app.database import native_db
    from app.utils.thumbnailer import native_store

    store = native_store()
    try:
        return native_db.library().sidecar_export(str(Path(folder)), bool(include_thumbnails and store),
                                                  store)
    except ValueError as exc:
        logger.warning("METADATA.7z not written in %s: %s", folder, exc)
        return False
    except OSError as exc:
        logger.error("%s", exc)
        return False


def import_folder_metadata(folder: Path, _session=None, thumb_base_dir: "Path | None" = None) -> list[str]:
    """
    Read METADATA.7z from *folder* and add the records whose archive file
    exists on disk but is not yet in the DB.  Returns the archive paths
    inserted (none for a sidecar this library cannot read).
    """
    from app.database import native_db
    from app.utils.thumbnailer import native_store

    store = native_store()
    if store is None:
        return []
    try:
        return native_db.library().sidecar_import(str(Path(folder)), store)
    except ValueError as exc:
        logger.warning("METADATA.7z in %s not imported: %s", folder, exc)
        return []
