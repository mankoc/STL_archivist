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
