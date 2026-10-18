"""Shared constants and pure helpers for the model-browser modules.

Extracted from ``browser_tab.py`` so the table model, filter tree, and the
``BrowserTab`` widget can share them without circular imports. No Qt-widget or
``app.ui`` dependencies — only the ``Qt`` enum namespace.
"""
from PyQt6.QtCore import Qt

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


