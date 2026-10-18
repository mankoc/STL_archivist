"""
The library database: its file, schema and maintenance actions.

The native core owns the schema (``rust/stl-core/src/storage``, migrated by
:func:`init_db`) and all database access; Python code reaches it through
:func:`app.database.native_db.library` (``stl_native.LibraryDb``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_db_path: Optional[Path] = None


def init_db(db_path: str = "stlLibrary.db") -> None:
    """Open (creating or migrating) the library database at *db_path*, and the
    thumbnail store beside it."""
    global _db_path
    if not db_path or db_path == ":memory:":
        raise ValueError("The library database must be a file")
    from app import native
    from app.database import native_db
    from app.utils.image_embedding import EMBED_DIM, EMBED_MODEL_ID
    from app.utils.thumbnailer import init_thumbnail_store

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    native_db.reset()   # pooled connections belong to the previous database
    report = native.module().migrate_database(str(path), EMBED_MODEL_ID, EMBED_DIM, True)
    changes = {k: v for k, v in report.items() if v}
    if changes:
        logger.info("Schema migration: %s", changes)
    _db_path = path
    init_thumbnail_store(path.parent / "thumbnails.lmdb")


def database_path() -> Path:
    """Path of the open database file.  Raises ``RuntimeError`` before :func:`init_db`."""
    if _db_path is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _db_path


def clear_db() -> None:
    """Delete all rows from every table (keeps the schema)."""
    from app.database import native_db
    native_db.library().db_clear_all()


def backfill_archive_folder() -> int:
    """Fill ``archive_folder`` where it is missing and normalise backslashed
    ones; returns how many rows were filled."""
    from app.database import native_db
    return native_db.library().db_backfill_archive_folder()
