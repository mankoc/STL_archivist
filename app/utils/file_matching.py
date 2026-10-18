"""
Pure-logic file-matching utility for comparing two STL archive file lists.

Decoupled from the UI so it can be unit-tested without a ``QApplication``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

#: An STL file entry with ``filename``, ``file_size`` and ``archive_hash``
#: (``services.model_views.StlFileRow``).
FileRow = Any


MatchKey = Literal["filename", "size", "hash"]


def _key_fn(f: FileRow, key: MatchKey):
    """Return the comparison key for *f* given matching *key*."""
    if key == "filename":
        return Path(f.filename).name.lower()
    elif key == "size":
        return f.file_size
    else:
        return f.archive_hash


def match_files(
    files_a: list[FileRow],
    files_b: list[FileRow],
    key: MatchKey = "filename",
) -> list[tuple[FileRow | None, FileRow | None]]:
    """Pair files from *files_a* and *files_b* by *key* using greedy matching.

    Each file in A is matched against the first file in B with the same key
    value.  Files whose key is ``None`` are never matched.  Unmatched files
    appear as ``(file, None)`` or ``(None, file)`` at the end of the result.

    Returns a list of ``(file_a | None, file_b | None)`` tuples where matched
    pairs have both sides non-None, and leftover entries have exactly one side
    as ``None``.
    """
    unmatched_a = list(files_a)
    unmatched_b = list(files_b)
    rows: list[tuple[FileRow | None, FileRow | None]] = []

    for fa in list(unmatched_a):
        ka = _key_fn(fa, key)
        if ka is None:
            continue
        best = next((fb for fb in unmatched_b if _key_fn(fb, key) == ka), None)
        if best is not None:
            rows.append((fa, best))
            unmatched_a.remove(fa)
            unmatched_b.remove(best)

    for fa in unmatched_a:
        rows.append((fa, None))
    for fb in unmatched_b:
        rows.append((None, fb))

    return rows
