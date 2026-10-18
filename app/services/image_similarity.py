"""Embedding-based image similarity over the sqlite-vec ``image_vec`` table
(native: ``rust/stl-core/src/storage/vectors.rs``)."""
from __future__ import annotations

from typing import Optional

from app.utils.image_embedding import to_blob

# Find by Image shows at most this many closest images (one result per model).
# A larger result grid is slow to build and not useful to browse; raising the
# threshold narrows the matches instead.
MAX_RESULT_IMAGES = 1024


def find_similar_models(
    _session, query_vec, threshold: float, limit: int = MAX_RESULT_IMAGES,
) -> tuple[list[tuple[float, int, str, str, Optional[str]]], bool]:
    """Search the *limit* nearest images for those with similarity >= *threshold*.

    Returns ``(results, capped)``.  *results* holds
    ``(similarity, model_id, title, archive_name, thumbnail_path)``, one entry
    per model (its best-matching image), most similar first.  Similarity is
    cosine similarity (1.0 = identical).  *capped* is True when all *limit*
    nearest images cleared the threshold, so more matches may exist.  The
    first argument is unused (it was a session).
    """
    from app.database import native_db
    return native_db.library().find_similar_models(to_blob(query_vec), float(threshold), int(limit))


def load_model_vectors(_session=None):
    """``(model_ids, matrix)`` for every vector of a present (not missing) model:
    int64 ``(N,)`` and float32 ``(N, EMBED_DIM)``, row-aligned."""
    import numpy as np

    from app.database import native_db
    from app.utils.image_embedding import EMBED_DIM
    mids, mat = native_db.library().load_model_vectors(EMBED_DIM)
    return (np.frombuffer(mids, dtype=np.int64).copy(),
            np.frombuffer(mat, dtype=np.float32).reshape(-1, EMBED_DIM))
