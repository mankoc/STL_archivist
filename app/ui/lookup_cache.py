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
    from app.services.browser_data import lookup_names
    return lookup_names(kind)
