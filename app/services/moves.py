"""Moved archives: the helpers the crawler and maintenance share.

The writes (relocating, merging a moved record into its missing one,
deleting) are native: ``rust/stl-core/src/storage/edit.rs``.
"""
from __future__ import annotations

from pathlib import PurePosixPath


def archive_folder_for(archive_path: str) -> str:
    """The ``archive_folder`` value for *archive_path*: its parent, with forward slashes.

    Must match what the crawler stores for new archives; folder views and the
    filter tree compare this column as a string.
    """
    return PurePosixPath(archive_path.replace("\\", "/")).parent.as_posix()


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
