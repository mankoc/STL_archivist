"""Embedding-based image similarity over the sqlite-vec ``image_vec`` table."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy import text

from app.database import vectors
from app.utils.image_embedding import to_blob

# Find by Image shows at most this many closest images (one result per model).
# A larger result grid is slow to build and not useful to browse; raising the
# threshold narrows the matches instead.
MAX_RESULT_IMAGES = 1024

_KNN_SQL = f"""
WITH hits AS MATERIALIZED (
    SELECT image_id, distance FROM {vectors.TABLE}
    WHERE embedding MATCH :q AND k = :k
)
SELECT hits.distance, i.model_id, i.thumbnail_path, m.title, m.archive_path
FROM hits
LEFT JOIN images i ON i.id = hits.image_id
LEFT JOIN models m ON m.id = i.model_id
"""


def find_similar_models(
    session, query_vec, threshold: float, limit: int = MAX_RESULT_IMAGES,
) -> tuple[list[tuple[float, int, str, str, Optional[str]]], bool]:
    """Search the *limit* nearest images for those with similarity >= *threshold*.

    Returns ``(results, capped)``.  *results* holds
    ``(similarity, model_id, title, archive_name, thumbnail_path)``, one entry
    per model (its best-matching image), most similar first.  Similarity is
    cosine similarity (1.0 = identical).  *capped* is True when all *limit*
    nearest images cleared the threshold, so more matches may exist.
    *session* may be ``None``: the native core or a session of its own is used.
    """
    from app.database import native_db
    lib = native_db.library() if session is None or native_db.session_clean(session) else None
    if lib is not None:
        return lib.find_similar_models(to_blob(query_vec), float(threshold), int(limit))
    if session is None:
        from app.database.db import session_scope
        with session_scope() as s:
            return find_similar_models(s, query_vec, threshold, limit)
    k = min(limit, vectors.MAX_KNN)
    maxd = 1.0 - threshold
    rows = session.execute(text(_KNN_SQL), {"q": to_blob(query_vec), "k": k}).all()
    # LEFT JOINs keep orphaned vectors in the count; they are dropped here.
    capped = len(rows) == k and max(r[0] for r in rows) <= maxd

    best: dict[int, tuple[float, str, str, Optional[str]]] = {}
    for distance, model_id, thumb_path, title, archive_path in rows:
        if distance > maxd or model_id is None:
            continue
        sim = 1.0 - float(distance)
        if model_id not in best or sim > best[model_id][0]:
            archive_name = Path(archive_path).name if archive_path else ""
            best[model_id] = (sim, title or "", archive_name, thumb_path)
    out = [(s, mid, t, arch, tp) for mid, (s, t, arch, tp) in best.items()]
    out.sort(key=lambda x: -x[0])
    return out, capped


def load_model_vectors(session):
    """``(model_ids, matrix)`` for every vector of a present (not missing) model.

    *session* may be ``None``: the native core or a session of its own is used.

    int64 ``(N,)`` and float32 ``(N, EMBED_DIM)``, row-aligned.  Vectors of
    deleted or missing models' images are left out.
    """
    import numpy as np

    from app.database import native_db
    from app.utils.image_embedding import EMBED_DIM
    lib = native_db.library() if session is None or native_db.session_clean(session) else None
    if lib is not None:
        mids, mat = lib.load_model_vectors(EMBED_DIM)
        return (np.frombuffer(mids, dtype=np.int64).copy(),
                np.frombuffer(mat, dtype=np.float32).reshape(-1, EMBED_DIM))
    if session is None:
        from app.database.db import session_scope
        with session_scope() as s:
            return load_model_vectors(s)

    ids, mat = vectors.read_all(session)
    # Plain driver rows: building ORM rows for 360k images took 1.7x as long.
    model_of = dict(session.connection().exec_driver_sql(
        "SELECT i.id, i.model_id FROM images i JOIN models m ON m.id = i.model_id "
        "WHERE m.is_missing IS NOT 1"
    ).all())
    mids = np.fromiter((model_of.get(i, -1) for i in ids.tolist()), dtype=np.int64, count=len(ids))
    keep = mids >= 0
    if keep.all():
        return mids, mat
    return mids[keep], mat[keep]
