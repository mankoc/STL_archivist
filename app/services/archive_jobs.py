"""
Archive jobs outside the crawl — Qt-free database work.

The browser's Generate Thumbnails and Rescan workers and Update Crawled
Folders' reconciliation passes used to write through the ORM themselves.  The
workers still extract archives and build thumbnails; the writes are here, run
natively (``stl_native.LibraryDb``, ``rust/stl-core/src/storage/jobs.rs``) when
:func:`app.database.native_db.library` allows it and in Python otherwise.
``tests/golden/test_archive_jobs_golden.py`` compares the two.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.database import native_db
from app.database.db import session_scope
from app.database.helpers import get_or_create_scale
from app.database.models import Image, STLFile, STLModel
from app.services import moves


def _lib():
    """The native library, when the thumbnail store is native too (or closed)."""
    from app.utils.thumbnailer import store_backend
    lib = native_db.library()
    if lib is None or not hasattr(lib, "jobs_replace_thumbnails") or store_backend() == "lmdb":
        return None
    return lib


def rescan_state(model_id: int) -> Optional[tuple]:
    """``(archive_path, archive_size, archive_mtime_ns, archive_hash)``, or ``None``."""
    lib = _lib()
    if lib is not None:
        return lib.jobs_rescan_state(int(model_id))
    with session_scope() as s:
        m = s.get(STLModel, model_id)
        return (m.archive_path, m.archive_size, m.archive_mtime_ns, m.archive_hash) if m else None


def replace_thumbnails(model_id: int, thumbs: list[tuple[str, Optional[bytes]]],
                       warning: Optional[str]) -> Optional[int]:
    """Generate Thumbnails for one model: delete its images and their
    thumbnails, store *thumbs* (``(member name, JPEG or None when conversion
    failed)``) and set *warning*.  Returns how many were stored (``None``
    without the model)."""
    from app.utils.thumbnailer import delete_thumbnails, native_store, write_raw_thumbnail
    lib = _lib()
    if lib is not None:
        return lib.jobs_replace_thumbnails(int(model_id), [(n, j) for n, j in thumbs],
                                           warning or None, native_store())
    with session_scope() as session:
        stl = session.get(STLModel, model_id)
        if stl is None:
            return None
        delete_thumbnails([i.thumbnail_path for i in stl.images if i.thumbnail_path])
        for old_img in list(stl.images):
            session.delete(old_img)
        session.flush()
        count = 0
        for filename, jpeg in thumbs:
            img = Image(model=stl, filename=filename)
            session.add(img)
            session.flush()
            rel_path = write_raw_thumbnail(img.id, jpeg) if jpeg else None
            if rel_path:
                img.thumbnail_path = rel_path
                count += 1
            else:
                session.delete(img)
        stl.warning = warning or None
        return count


def apply_rescan(model_id: int, values: dict) -> bool:
    """The browser's Rescan for one model; ``False`` without the model.

    *values*: ``archive_hash``, ``archive_size``, ``archive_mtime_ns``,
    ``compression_rate`` (``None``: keep), ``title`` / ``creator`` / ``source``
    / ``url`` / ``description`` (``None`` or empty: keep), ``release``
    (``(year, month)`` or ``None``: keep), ``presupports``, ``warning``,
    ``scales`` (empty: keep), ``stl_files`` (``None``: not stored).
    """
    lib = _lib()
    if lib is not None:
        return lib.jobs_apply_rescan(int(model_id), values)
    with session_scope() as session:
        m = session.get(STLModel, model_id)
        if m is None:
            return False
        m.archive_hash = values["archive_hash"]
        m.archive_size = values.get("archive_size")
        m.archive_mtime_ns = values.get("archive_mtime_ns")
        if values.get("compression_rate") is not None:
            m.compression_rate = values["compression_rate"]
        m.title = values.get("title") or m.title
        m.creator = values.get("creator") or m.creator
        m.source = values.get("source") or m.source
        if values.get("release") is not None:
            m.release_year, m.release_month = values["release"]
        m.url = values.get("url") or m.url
        m.description = values.get("description") or m.description
        m.presupports = values.get("presupports")
        m.warning = values.get("warning") or None
        if values.get("scales"):
            m.scales = [get_or_create_scale(session, s) for s in values["scales"]]
        if values.get("stl_files") is not None:
            for old in list(m.stl_files):
                session.delete(old)
            session.flush()
            for fname in values["stl_files"]:
                session.add(STLFile(model=m, filename=fname, file_size=None,
                                    archive_hash=values["archive_hash"]))
        return True


def cross_folder_reconcile(new_paths: set[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Merge missing records into new archives with the same hash (unique on
    both sides).  Returns ``(old path, new path)`` per move and the thumbnail
    refs to delete after commit."""
    lib = _lib()
    if lib is not None:
        moved, refs = lib.jobs_cross_folder(sorted(new_paths))
        return [tuple(m) for m in moved], refs
    moved: list[tuple[str, str]] = []
    refs: list[str] = []
    with session_scope() as session:
        missing_rows = (session.query(STLModel.id, STLModel.archive_path, STLModel.archive_hash)
                        .filter(STLModel.is_missing == True).order_by(STLModel.id).all())  # noqa: E712
        if not missing_rows:
            return moved, refs
        hash_to_missing_id: dict[str, int] = {}
        missing_collision: set[str] = set()
        for mid, _p, h in missing_rows:
            if not h:
                continue
            if h in hash_to_missing_id:
                missing_collision.add(h)
            else:
                hash_to_missing_id[h] = mid
        if not [h for h in hash_to_missing_id if h not in missing_collision]:
            return moved, refs
        new_models = {m.archive_path: m for m in session.query(STLModel)
                      .filter(STLModel.archive_path.in_(list(new_paths))).order_by(STLModel.id)}
        hash_to_new: dict[str, str] = {}
        new_collision: set[str] = set()
        for path, nm in new_models.items():
            if not nm.archive_hash:
                continue
            if nm.archive_hash in hash_to_new:
                new_collision.add(nm.archive_hash)
            else:
                hash_to_new[nm.archive_hash] = path
        used: set[str] = set()
        for h, missing_id in hash_to_missing_id.items():
            if h in missing_collision or h in new_collision:
                continue
            new_path = hash_to_new.get(h)
            if new_path is None or new_path in used:
                continue
            old, new = session.get(STLModel, missing_id), new_models.get(new_path)
            if old is None or new is None:
                continue
            old_path = old.archive_path
            refs += moves.merge_into_missing(session, old, new)
            used.add(new_path)
            moved.append((old_path, new_path))
    return moved, refs


def delete_missing_under(roots: list[str]) -> tuple[list[str], list[str]]:
    """Delete the missing records under any of *roots*; returns their paths and
    the thumbnail refs to delete after commit."""
    lib = _lib()
    if lib is not None:
        return lib.jobs_delete_missing_under([str(r) for r in roots])
    folder_roots = [Path(r) for r in roots]
    deleted: list[str] = []
    refs: list[str] = []
    with session_scope() as session:
        for model in (session.query(STLModel).filter(STLModel.is_missing == True)  # noqa: E712
                      .order_by(STLModel.id)):
            if not model.archive_path:
                continue
            try:
                in_scope = any(Path(model.archive_path).is_relative_to(r) for r in folder_roots)
            except ValueError:
                in_scope = False
            if in_scope:
                deleted.append(model.archive_path)
                refs += moves.delete_model(session, model)
    return deleted, refs
