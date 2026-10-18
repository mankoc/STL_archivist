"""
Lookup-table usage counts shared by the editor dialogs (native).
"""
from __future__ import annotations

KINDS = ("tag", "scale", "category", "collection", "subcollection", "sculptor", "release")


def usage_counts(kind: str) -> dict[int, int]:
    """Return ``{lookup_id: number_of_models}`` for every referenced row of *kind*.

    Rows that no model references are absent — use ``.get(id, 0)``.
    """
    from app.database import native_db
    if kind not in KINDS:
        raise KeyError(kind)
    return native_db.library().usage_counts(kind)


def usage_count(kind: str, lookup_id: int) -> int:
    """The number of models referencing one lookup row."""
    return usage_counts(kind).get(lookup_id, 0)
