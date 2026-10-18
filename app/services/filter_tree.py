"""
The browser filter pane's data: per-section values and counts — Qt-free.

``filter_tree_data`` runs every query the filter tree needs for one rebuild
(natively: ``rust/stl-core/src/storage/library.rs``); ``_FilterTreeWorker``
in ``app.ui.browser_filter_tree`` wraps it.  Scoped counts count each section
over the models matching every *other* active filter.
"""
from __future__ import annotations

from app.services.library_query import FilterSpec


def filter_tree_data(spec: "FilterSpec | None", show: dict, show_counts: bool) -> dict:
    """Values and counts for every visible filter section.

    *spec* ``None`` (or empty) gives the unscoped tree.  Returns the payload keys
    the browser consumes (``counts``, ``path_counts``, ``scoped_counts``,
    ``sc_path_counts``, the value lists, ``sorted_dirs`` / ``scoped_dirs`` /
    ``new_dirs_frozen``) plus ``scoped``: whether section scopes applied.
    """
    from app.database import native_db
    spec_d = native_db.spec_dict(spec) if spec is not None and not spec.is_empty() else None
    data = native_db.library().filter_tree(spec_d, [k for k, v in show.items() if v], show_counts)
    new_dirs = data.pop("new_dirs")
    data["new_dirs_frozen"] = frozenset(new_dirs) if new_dirs is not None else None
    if not show_counts:
        data["path_counts"] = None
    return data
