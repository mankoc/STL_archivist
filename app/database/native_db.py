"""The library database's native connection pool (``stl_native.LibraryDb``).

Every database read and write goes through :func:`library`, which returns the
pool for the database :func:`app.database.db.init_db` opened.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_lib = None
_lib_path = None


def library():
    """The ``LibraryDb`` for the open database.  Raises ``RuntimeError`` before
    ``init_db`` (or without the native core)."""
    global _lib, _lib_path
    from app import native
    from app.database.db import database_path
    mod = native.module()
    path = database_path()
    with _lock:
        if _lib is None or _lib_path != path:
            if _lib is not None:
                _lib.close()
            _lib = mod.LibraryDb(str(path))
            _lib_path = path
        return _lib


def reset() -> None:
    """Drop the pooled connections (``init_db`` calls this)."""
    global _lib, _lib_path
    with _lock:
        if _lib is not None:
            _lib.close()
        _lib = None
        _lib_path = None


def spec_dict(spec) -> dict:
    """A ``FilterSpec`` as the plain dict ``LibraryDb`` takes."""
    return {
        "include": {k: list(v) for k, v in spec.include.items()},
        "exclude": {k: list(v) for k, v in spec.exclude.items()},
        "has_value": sorted(spec.has_value),
        "no_value": sorted(spec.no_value),
        "ids": list(spec.ids) if spec.ids is not None else None,
    }
