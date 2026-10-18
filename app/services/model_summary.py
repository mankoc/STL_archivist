"""One-row summary of a model for detail panels (Find by Image)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text

# One indexed lookup: models_view row plus tag names and image count from
# correlated subqueries, so a GUI-thread caller stays within the work budget.
_SQL = """
SELECT v.id, v.title, v.creator, v.source, v.sculptor_name, v.collection_name,
       v.subcollection_name, v.category_name, v.release_name, v.release_year,
       v.release_month, v.archive_path, v.archive_size, v.is_missing,
       (SELECT group_concat(t.name, ', ') FROM model_tags mt
          JOIN tags t ON t.id = mt.tag_id WHERE mt.model_id = v.id) AS tags,
       (SELECT count(*) FROM images i WHERE i.model_id = v.id) AS image_count
FROM models_view v
WHERE v.id = :id
"""


def model_summary(session, model_id: int) -> Optional[dict]:
    """Return the model's display fields as a dict, or None if it no longer exists."""
    from app.database import native_db
    lib = native_db.library() if native_db.session_clean(session) else None
    if lib is not None:
        return lib.model_summary(int(model_id))
    row = session.execute(text(_SQL), {"id": model_id}).mappings().first()
    return dict(row) if row else None
