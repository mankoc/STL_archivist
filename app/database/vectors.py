"""
CLIP image embeddings in the sqlite-vec ``vec0`` table ``image_vec``.

The native core bundles sqlite-vec and owns the table (schema, writes, KNN
searches and whole-table reads: ``rust/stl-core/src/storage/vectors.rs``).
"""
from __future__ import annotations

#: The vec0 table holding one embedding per ``Image.id``.
TABLE = "image_vec"

#: sqlite-vec caps a KNN query at this many neighbours.
MAX_KNN = 4096


def available() -> bool:
    """Whether embeddings can be stored: always, the native core bundles sqlite-vec."""
    return True


def legacy_embeddings_present(db_path: str) -> bool:
    """Whether the file still keeps embeddings on ``images`` (moved by the next
    :func:`app.database.db.init_db`); for the splash screen."""
    import sqlite3
    from pathlib import Path

    if not Path(db_path).is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            return any(r[1] == "embedding" for r in con.execute("PRAGMA table_info(images)"))
        finally:
            con.close()
    except sqlite3.Error:
        return False
