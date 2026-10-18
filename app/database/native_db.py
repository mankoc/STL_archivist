"""Native read access to the library database (``stl_native.LibraryDb``).

The browser and editor read services (``library_query``, ``filter_tree``,
``lookups``, ``model_summary``) run natively when :func:`library` returns an
object, and in Python otherwise.  It returns ``None``:

* without the native core (or ``STL_NATIVE=0``);
* for an in-memory database (a native connection would see a different one);
* **off Windows.**  rusqlite bundles its own SQLite, and on POSIX two SQLite
  copies holding one database file in one process can corrupt it (closing a
  file in one drops the other's locks).  Until SQLAlchemy is removed (Phase 4h)
  the native connections are only used where file locks are per handle.
"""
from __future__ import annotations

import sys
import threading

_lock = threading.Lock()
_lib = None
_lib_path = None


def library():
    """The ``LibraryDb`` for the open database, or ``None`` to use Python."""
    global _lib, _lib_path
    if sys.platform != "win32":
        return None
    from app import native
    mod = native.module()
    if mod is None or not hasattr(mod, "LibraryDb"):
        return None
    from app.database.db import database_path
    try:
        path = database_path()
    except RuntimeError:
        return None
    if path is None:
        return None
    with _lock:
        if _lib is None or _lib_path != path or type(_lib) is not mod.LibraryDb:
            if _lib is not None:
                _lib.close()
            _lib = mod.LibraryDb(str(path))
            _lib_path = path
        return _lib


def reset() -> None:
    """Drop the pooled native connections (``init_db`` calls this)."""
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


def session_clean(session) -> bool:
    """True when *session* holds no changes a native read would miss: none
    pending, and none flushed but not yet committed (a native connection
    cannot see another connection's open transaction)."""
    if session is None:
        return True
    return not (session.new or session.dirty or session.deleted or session.info.get("uncommitted_flush"))


def _track_flushes() -> None:
    """Mark sessions that flushed changes, until they commit or roll back."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    @event.listens_for(Session, "after_flush")
    def _flushed(session, _ctx):
        session.info["uncommitted_flush"] = True

    @event.listens_for(Session, "after_commit")
    @event.listens_for(Session, "after_rollback")
    def _ended(session):
        session.info.pop("uncommitted_flush", None)


_track_flushes()
