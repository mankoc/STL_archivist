"""
Lookup-table queries shared by the editor dialogs.

``usage_counts`` replaces per-row ``len(lookup.models)`` — which lazy-loads
every referencing ``STLModel`` row (an N+1 query that materialises most of
the library on large collections) — with a single ``GROUP BY``.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import STLModel, model_scales, model_tags

# kind -> column that references the lookup row's id
_REF_COLUMNS = {
    "tag":           model_tags.c.tag_id,
    "scale":         model_scales.c.scale_id,
    "category":      STLModel.category_id,
    "collection":    STLModel.collection_id,
    "subcollection": STLModel.subcollection_id,
    "sculptor":      STLModel.sculptor_id,
    "release":       STLModel.release_id,
}


def usage_counts(session: Session, kind: str) -> dict[int, int]:
    """Return ``{lookup_id: number_of_models}`` for every referenced row of *kind*.

    Rows that no model references are absent — use ``.get(id, 0)``.
    Runs natively when it can (``app.database.native_db``).
    """
    from app.database import native_db
    if kind not in _REF_COLUMNS:
        raise KeyError(kind)
    lib = native_db.library() if native_db.session_clean(session) else None
    if lib is not None:
        return lib.usage_counts(kind)
    col = _REF_COLUMNS[kind]
    stmt = select(col, func.count()).where(col.isnot(None)).group_by(col)
    return {ref_id: n for ref_id, n in session.execute(stmt)}


def usage_count(session: Session, kind: str, lookup_id: int) -> int:
    """Return the number of models referencing one lookup row."""
    col = _REF_COLUMNS[kind]
    return session.execute(select(func.count()).where(col == lookup_id)).scalar_one()
