"""
Read-only model views for the viewer dialogs — Qt-free.

Archive contents (file list / compare), the archive browser, the image grid,
Find Duplicate Archives (metadata) and Find by Image.  Each function runs
natively (``stl_native.LibraryDb``, ``rust/stl-core/src/storage/views.rs``).
Lists are in id order (images, tags, scales, models) or file-name order (STL
files).
"""
from __future__ import annotations

from typing import NamedTuple, Optional


from app.database import native_db

#: Lookup fields of :func:`duplicate_rows` (``scales`` is a list).
DUPLICATE_FIELDS = ("title", "creator", "source", "sculptor", "collection", "subcollection",
                    "category", "release", "scales")


class StlFileRow(NamedTuple):
    filename: str
    file_size: Optional[int]
    archive_hash: Optional[str]


def stl_files(model_id: int) -> list[StlFileRow]:
    """A model's STL file entries, by file name."""
    lib = native_db.library()
    return [StlFileRow(*r) for r in lib.views_stl_files(int(model_id))]


def model_label(model_id: int) -> Optional[str]:
    """``title or archive_path`` of a model, or ``None`` when it does not exist."""
    lib = native_db.library()
    row = lib.views_title_path(int(model_id))
    return (row[0] or row[1]) if row else None


def archive_folders() -> list[str]:
    """Distinct non-empty ``archive_folder`` values, sorted."""
    lib = native_db.library()
    return lib.views_archive_folders()


def folder_models(folder: str, include_subfolders: bool) -> list[dict]:
    """Archive browser cells: ``id, title, creator, archive_path, thumbs`` for the
    models in *folder* (and its subfolders), by archive path."""
    lib = native_db.library()
    rows = lib.views_folder_models(folder, include_subfolders)
    return [dict(zip(("id", "title", "creator", "archive_path", "thumbs"), r)) for r in rows]


_GRID_FIELDS = ("title", "creator", "source", "release_year", "release_month", "archive_path",
                "presupports", "warning", "description", "url")


def grid_models(model_ids: list[int]) -> dict[int, dict]:
    """Image grid cells: ``{id: fields}`` with ``tags``, ``scales`` (id order) and
    ``thumbs``; missing ids are left out."""
    lib = native_db.library()
    return lib.views_grid_models([int(i) for i in model_ids])


def duplicate_rows() -> list[dict]:
    """Every model's metadata for Find Duplicate Archives, by id: ``id`` plus
    :data:`DUPLICATE_FIELDS` (lookups by name, ``scales`` a list of names)."""
    lib = native_db.library()
    return lib.views_duplicate_rows()


def image_hash_rows() -> list[tuple]:
    """``(model_id, dhash, thumbnail_path, title, archive_path)`` for every image
    with a stored dHash, by image id (Find by Image, hash method)."""
    lib = native_db.library()
    return lib.views_image_hash_rows()
