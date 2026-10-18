"""One-row summary of a model for detail panels (Find by Image)."""
from __future__ import annotations

from typing import Optional


def model_summary(_session, model_id: int) -> Optional[dict]:
    """The model's display fields (names, tags, image count) as a dict, or
    ``None`` if it no longer exists.  One indexed lookup: cheap enough for the
    GUI thread.  The first argument is unused (it was a session)."""
    from app.database import native_db
    return native_db.library().model_summary(int(model_id))
