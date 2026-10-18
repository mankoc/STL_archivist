"""
Archive jobs outside the crawl — Qt-free database work.

The writes of the browser's Generate Thumbnails and Rescan workers and of
Update Crawled Folders' reconciliation passes.  The workers extract archives
and build thumbnails; the writes run natively (``stl_native.LibraryDb``, ``rust/stl-core/src/storage/jobs.rs``).
"""
from __future__ import annotations

from typing import Optional

from app.database import native_db


def _lib():
    return native_db.library()


def rescan_state(model_id: int) -> Optional[tuple]:
    """``(archive_path, archive_size, archive_mtime_ns, archive_hash)``, or ``None``."""
    lib = _lib()
    return lib.jobs_rescan_state(int(model_id))


def replace_thumbnails(model_id: int, thumbs: list[tuple[str, Optional[bytes]]],
                       warning: Optional[str]) -> Optional[int]:
    """Generate Thumbnails for one model: delete its images and their
    thumbnails, store *thumbs* (``(member name, JPEG or None when conversion
    failed)``) and set *warning*.  Returns how many were stored (``None``
    without the model)."""
    from app.utils.thumbnailer import native_store
    lib = _lib()
    return lib.jobs_replace_thumbnails(int(model_id), [(n, j) for n, j in thumbs],
                                       warning or None, native_store())


def apply_rescan(model_id: int, values: dict) -> bool:
    """The browser's Rescan for one model; ``False`` without the model.

    *values*: ``archive_hash``, ``archive_size``, ``archive_mtime_ns``,
    ``compression_rate`` (``None``: keep), ``title`` / ``creator`` / ``source``
    / ``url`` / ``description`` (``None`` or empty: keep), ``release``
    (``(year, month)`` or ``None``: keep), ``presupports``, ``warning``,
    ``scales`` (empty: keep), ``stl_files`` (``None``: not stored).
    """
    lib = _lib()
    return lib.jobs_apply_rescan(int(model_id), values)


def cross_folder_reconcile(new_paths: set[str]) -> tuple[list[tuple[str, str]], list[str]]:
    """Merge missing records into new archives with the same hash (unique on
    both sides).  Returns ``(old path, new path)`` per move and the thumbnail
    refs to delete after commit."""
    lib = _lib()
    moved, refs = lib.jobs_cross_folder(sorted(new_paths))
    return [tuple(m) for m in moved], refs


def delete_missing_under(roots: list[str]) -> tuple[list[str], list[str]]:
    """Delete the missing records under any of *roots*; returns their paths and
    the thumbnail refs to delete after commit."""
    lib = _lib()
    return lib.jobs_delete_missing_under([str(r) for r in roots])
