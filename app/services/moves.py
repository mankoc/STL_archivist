"""Moved archives: point a catalogued model at its archive's new location.

Used by the crawler, Update Crawled Folders and Database Maintenance → Merge
Moved, which used to carry three copies of this logic.

Thumbnail references returned here must be deleted from the LMDB store with
``delete_thumbnails()`` *after* the session commits, in one call: a rolled-back
transaction then leaves no image row without its thumbnail, and one batched
LMDB write replaces one disk flush per image.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional


def archive_folder_for(archive_path: str) -> str:
    """The ``archive_folder`` value for *archive_path*: its parent, with forward slashes.

    Must match what the crawler stores for new archives; folder views and the
    filter tree compare this column as a string.
    """
    return PurePosixPath(archive_path.replace("\\", "/")).parent.as_posix()


def relocate(
    model, new_path: str, archive_hash: str,
    size: Optional[int], mtime_ns: Optional[int], compression: Optional[float] = None,
) -> None:
    """Point *model* at its archive's new location and clear its missing flag."""
    model.archive_path = new_path
    model.archive_hash = archive_hash
    model.archive_size = size
    model.archive_mtime_ns = mtime_ns
    model.archive_folder = archive_folder_for(new_path)
    model.is_missing = False
    if compression is not None:
        model.compression_rate = compression


def merge_into_missing(session, old_model, new_model) -> list[str]:
    """Keep *old_model* (with its edited metadata) at *new_model*'s location.

    *new_model* is the shallow record the crawler created for the archive at
    its new path; it is deleted along with its images and STL file list.
    Returns its thumbnail references for deletion after commit.
    """
    refs = [img.thumbnail_path for img in new_model.images if img.thumbnail_path]
    location = (new_model.archive_path, new_model.archive_hash, new_model.archive_size,
                new_model.archive_mtime_ns, new_model.compression_rate)
    session.delete(new_model)     # images / stl_files cascade (delete-orphan)
    session.flush()               # frees the unique archive_path before reuse
    relocate(old_model, *location)
    session.flush()
    return refs


def delete_model(session, model) -> list[str]:
    """Delete *model* with its images and STL file list; return thumbnail refs."""
    refs = [img.thumbnail_path for img in model.images if img.thumbnail_path]
    session.delete(model)
    return refs


def index_by_hash(missing: list[dict]) -> dict[str, dict]:
    """Map content hash → missing record, for hashes that identify one record.

    Skips empty and fallback fingerprints (unreadable archives) and hashes
    shared by several missing records: those are content duplicates, and
    picking one would be a guess.
    """
    index: dict[str, dict] = {}
    shared: set[str] = set()
    for rec in missing:
        h = rec.get("hash") or ""
        if not h or h.startswith("fallback:"):
            continue
        if h in index:
            shared.add(h)
        else:
            index[h] = rec
    for h in shared:
        del index[h]
    return index
