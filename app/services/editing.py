"""
Browser inline (cell) edits — Qt-free (native: ``rust/stl-core/src/storage/edit.rs``).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

#: Table fields a cell edit can change.
CELL_FIELDS = frozenset({
    "title", "creator", "source", "date", "tags", "sculptor", "collection",
    "subcollection", "category", "sequence", "release", "scale",
    "presupports", "to_print",
})


def apply_cell_edits(edits: Iterable[tuple[int, str, str | None]]) -> list[str]:
    """Apply browser-table cell edits ``(model id, field, text)`` in one
    transaction; returns the sorted archive folders touched (for sidecars).

    *text* is the raw cell text; empty text clears the field.  An unknown field
    raises ``KeyError``; an unparsable sequence is ignored.
    """
    from app.database import native_db
    paths = native_db.library().apply_cell_edits([(int(mid), field, text) for mid, field, text in edits])
    return sorted({str(Path(p).parent) for p in paths})
