"""Shared constants and pure helpers for the model-browser modules.

Extracted from ``browser_tab.py`` so the table model, filter tree, and the
``BrowserTab`` widget can share them without circular imports. No Qt-widget or
``app.ui`` dependencies — only the ``Qt`` enum namespace and SQLAlchemy.
"""
from PyQt6.QtCore import Qt
from sqlalchemy import (
    Table as _Table, MetaData as _MetaData, Column as _Column, Integer as _Integer,
)

# Image file extensions accepted by the thumbnail-pane drag-and-drop.
_IMAGE_DROP_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif',
                             '.avif'})

# Column indices
COL_THUMB         = 0
COL_TITLE         = 1
COL_CREATOR       = 2
COL_SOURCE        = 3
COL_DATE          = 4
COL_TAGS          = 5
COL_SCULPTOR      = 6
COL_COLLECTION    = 7
COL_SUBCOLLECTION = 8
COL_SEQUENCE      = 9
COL_RELEASE       = 10
COL_PATH          = 11
COL_SCALE         = 12
COL_PRESUPPORTED  = 13
COL_ADDED         = 14
COL_WARNING       = 15
COL_FILESIZE      = 16
COL_CATEGORY      = 17
COL_COMPRESSION   = 18
COL_TO_PRINT      = 19
_HEADERS = ["", "Title", "Creator", "Source", "Date", "Tags",
            "Sculptor", "Collection", "Subcollection", "Sequence", "Release", "Path",
             "Scale", "Presupports", "Added", "Warnings", "File Size", "Category",
             "Compression", "To Print"]


def _fmt_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


# Moved to the Qt-free filter model; kept under the old name for callers.
from app.services.library_query import prune_ancestor_paths as _prune_ancestor_paths  # noqa: E402,F401


MODEL_ID_ROLE = Qt.ItemDataRole.UserRole + 1

# Connection-local TEMP table holding the IDs that match the active path filter.
# The filter-tree worker populates it once per rebuild, then every per-section
# scoped-count subquery references it — so the un-indexable archive_path LIKE scan
# runs a single time and is never expanded into a giant ``IN (?, …)`` list.
_FILTER_PATH_IDS_NAME = "_filter_path_ids"
_FILTER_PATH_IDS = _Table(
    _FILTER_PATH_IDS_NAME, _MetaData(),
    _Column("id", _Integer, primary_key=True),
)


# ---------------------------------------------------------------------------
# SQL-backed paged model helpers
# ---------------------------------------------------------------------------

def _pack_row(row, tags_for: dict, scales_for: dict, pair_for_id: dict) -> tuple:
    return (
        row.id, row.title, row.creator, row.source,
        row.release_year, row.release_month, row.archive_path,
        row.presupports, row.warning, row.archive_size,
        row.compression_rate, row.sequence, row.created_at,
        row.sculptor_name, row.collection_name, row.subcollection_name,
        row.category_name, row.release_name, scales_for.get(row.id, ""),
        tags_for.get(row.id, ""), pair_for_id.get(row.id, -1), row.to_print,
    )
