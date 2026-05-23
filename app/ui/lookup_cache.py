"""
Process-wide cache for UI delegate combo-box items.

Delegates like SculptorDelegate and CollectionDelegate previously issued a
full DB query on every cell edit.  This module caches each kind lazily on
first access and exposes an invalidate() call to flush stale data after
any write that changes the underlying tables.
"""
from __future__ import annotations

import threading
from typing import Optional

_lock = threading.Lock()
_cache: dict[str, list[str]] = {}

_VALID_KINDS = frozenset({
    "category",
    "collection",
    "release",
    "scale",
    "sculptor",
    "source",
    "subcollection",
})


def get(kind: str) -> list[str]:
    """Return a sorted list of values for *kind*, loading from DB if not cached."""
    if kind not in _VALID_KINDS:
        raise ValueError(f"Unknown lookup kind: {kind!r}")
    with _lock:
        if kind in _cache:
            return _cache[kind]
        result = _load(kind)
        _cache[kind] = result
        return result


def invalidate(kind: Optional[str] = None) -> None:
    """Drop one or all cached kinds so the next get() re-queries the DB.

    Pass a *kind* string to drop only that kind; pass nothing (or None) to
    invalidate all kinds at once (e.g. after a bulk save).
    """
    with _lock:
        if kind is None:
            _cache.clear()
        else:
            _cache.pop(kind, None)


def _load(kind: str) -> list[str]:
    from app.database.db import get_session
    from app.database.models import (
        Category, Collection, Release, Scale, Sculptor, STLModel, Subcollection,
    )

    session = get_session()
    try:
        if kind == "source":
            return sorted(filter(None, (
                v for (v,) in session.query(STLModel.source).distinct().all()
            )))
        if kind == "sculptor":
            return sorted(s.name for s in session.query(Sculptor).order_by(Sculptor.name).all())
        if kind == "collection":
            return sorted(c.name for c in session.query(Collection).order_by(Collection.name).all())
        if kind == "subcollection":
            return sorted(
                s.name for s in session.query(Subcollection).order_by(Subcollection.name).all()
            )
        if kind == "category":
            return sorted(c.name for c in session.query(Category).order_by(Category.name).all())
        if kind == "release":
            return sorted(r.name for r in session.query(Release).order_by(Release.name).all())
        if kind == "scale":
            return sorted(s.name for s in session.query(Scale).order_by(Scale.name).all())
        return []
    finally:
        session.close()
