"""
Persistent cache for the filter-tree payload.

A single row in the ``filter_cache`` table (id=1) stores the pre-computed
unscoped filter-tree data as a JSON blob.  This lets the filter pane populate
instantly on startup and after in-app navigation without re-running the
expensive GROUP BY / COUNT queries against the full models table.

Lifecycle
---------
* Written by ``_FilterTreeWorker`` after every successful unscoped
  (no active filters) tree build.
* Invalidated by ``BrowserTab.refresh()``, which is called after every
  crawl, edit, or bulk operation that changes model data.
* Rebuilt lazily: the next unscoped worker run finds no cache, executes the
  full queries, then persists the result.

JSON encoding
-------------
The ``counts`` dict uses tuple keys such as ``("sculptor_id", 42)`` and
``("date_month", (2023, 1))``, which are not directly JSON-serialisable.
They are stored as ``[kind, val]`` arrays and decoded back on load.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def _encode_counts(counts: dict) -> list:
    """Serialise the counts dict to a JSON-safe list of ``[kind, val, n]``."""
    result = []
    for (kind, val), count in counts.items():
        # date_month keys carry a (year, month) tuple as val
        result.append([kind, list(val) if isinstance(val, tuple) else val, count])
    return result


def _decode_counts(data: list) -> dict:
    """Reconstruct the counts dict from its serialised form."""
    counts: dict = {}
    for item in data:
        kind, val, count = item
        if kind == "date_month":
            val = tuple(val)
        counts[(kind, val)] = count
    return counts


def _encode_payload(payload: dict) -> str:
    cacheable = {
        "counts":      _encode_counts(payload.get("counts") or {}),
        "path_counts": payload.get("path_counts") or {},
        "creators":    payload.get("creators") or [],
        "tags":        payload.get("tags") or [],
        "sources":     payload.get("sources") or [],
        # dates is a list of (year, month) tuples
        "dates":       [[y, m] for y, m in (payload.get("dates") or [])],
        # sculptors/collections/categories/releases/scales are (name, id) tuples
        "sculptors":   payload.get("sculptors") or [],
        "collections": payload.get("collections") or [],
        "sub_by_col":  [
            [col_name, col_id, list(subs)]
            for col_name, col_id, subs in (payload.get("sub_by_col") or [])
        ],
        "categories":  payload.get("categories") or [],
        "releases":    payload.get("releases") or [],
        "scales":      payload.get("scales") or [],
        "presupports": payload.get("presupports") or [],
        "sorted_dirs": payload.get("sorted_dirs") or [],
    }
    return json.dumps(cacheable, ensure_ascii=False)


def _decode_payload(raw: str) -> dict:
    data = json.loads(raw)
    sorted_dirs = data.get("sorted_dirs") or []
    return {
        "counts":        _decode_counts(data.get("counts") or []),
        "path_counts":   data.get("path_counts") or {},
        "creators":      data.get("creators") or [],
        "tags":          data.get("tags") or [],
        "sources":       data.get("sources") or [],
        "dates":         [tuple(d) for d in (data.get("dates") or [])],
        "sculptors":     [tuple(s) for s in (data.get("sculptors") or [])],
        "collections":   [tuple(c) for c in (data.get("collections") or [])],
        "sub_by_col":    [
            (item[0], item[1], [tuple(s) for s in item[2]])
            for item in (data.get("sub_by_col") or [])
        ],
        "categories":    [tuple(c) for c in (data.get("categories") or [])],
        "releases":      [tuple(r) for r in (data.get("releases") or [])],
        "scales":        [tuple(s) for s in (data.get("scales") or [])],
        "presupports":   data.get("presupports") or [],
        "sorted_dirs":   sorted_dirs,
        # Provide new_dirs_frozen so the path-cache logic in the worker stays intact
        "new_dirs_frozen": frozenset(sorted_dirs),
        # Scoped fields are empty — will be filled by live queries when filters are active
        "scoped_counts":   {},
        "sc_path_counts":  None,
        "scoped_dirs":     None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_filter_cache(payload: dict) -> None:
    """Persist the unscoped filter-tree payload to the database."""
    from app.database.db import session_scope
    from app.database.models import FilterCache

    try:
        encoded = _encode_payload(payload)
        with session_scope() as session:
            row = session.get(FilterCache, 1)
            if row is None:
                row = FilterCache(id=1)
                session.add(row)
            row.payload    = encoded
            row.updated_at = datetime.now(timezone.utc)
        logger.debug("Filter cache saved.")
    except Exception as exc:
        logger.warning("Failed to save filter cache: %s", exc)


def load_filter_cache() -> Optional[dict]:
    """Return the cached unscoped payload, or *None* if no cache exists."""
    from app.database.db import session_scope
    from app.database.models import FilterCache

    try:
        with session_scope() as session:
            row = session.get(FilterCache, 1)
            if row is None or not row.payload:
                return None
            raw = row.payload
        result = _decode_payload(raw)
        logger.debug("Filter cache loaded.")
        return result
    except Exception as exc:
        logger.warning("Failed to load filter cache: %s", exc)
        return None


def invalidate_filter_cache() -> None:
    """Delete the cached row so the next unscoped tree build refreshes it."""
    from app.database.db import session_scope
    from app.database.models import FilterCache

    try:
        with session_scope() as session:
            row = session.get(FilterCache, 1)
            if row is not None:
                session.delete(row)
        logger.debug("Filter cache invalidated.")
    except Exception as exc:
        logger.warning("Failed to invalidate filter cache: %s", exc)
