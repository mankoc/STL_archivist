"""
The browser's filter model and the table queries it drives — Qt-free.

One :class:`FilterSpec` describes what the filter pane selects; the native
core turns it into the table's ids (:func:`table_ids`), its rows
(:func:`page_rows`) and the filter-pane counts (``services.filter_tree``),
from one predicate builder (``rust/stl-core/src/storage/library.rs``).
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Filter-pane sections, in display order.  Each may carry include/exclude values.
FACETS: tuple[str, ...] = (
    "creator", "source", "tag", "path", "date", "sculptor", "collection",
    "subcollection", "category", "release", "scale", "presupports", "warning",
    "filetype",
)

#: Keys accepted by the has-value / no-value group filters.
GROUPS: frozenset[str] = frozenset({
    "creator", "tag", "source", "date", "sculptor", "collection", "subcollection",
    "category", "release", "scale", "presupports", "warning", "path",
})

@dataclass
class FilterSpec:
    """What the user has selected in the filter pane.

    ``include[facet]`` values are OR-ed within the facet; facets are AND-ed.
    ``exclude[facet]`` removes rows matching any of its values.

    Value types per facet: ``creator``/``source``/``tag``/``presupports``/
    ``warning`` — strings; ``filetype`` — extensions such as ``".7z"``;
    ``path`` — folder paths; ``date`` — ``(year, month_or_None)`` tuples;
    ``sculptor``/``collection``/``subcollection``/``category``/``release``/
    ``scale`` — lookup-row ids.
    """
    include:   dict[str, list] = field(default_factory=dict)
    exclude:   dict[str, list] = field(default_factory=dict)
    has_value: set[str] = field(default_factory=set)
    no_value:  set[str] = field(default_factory=set)
    #: Restrict to these model ids (e.g. "show these models" from a dialog).
    ids: list[int] | None = None

    def __post_init__(self) -> None:
        for name in (*self.include, *self.exclude):
            if name not in FACETS:
                raise ValueError(f"Unknown facet: {name!r}")
        for name in (*self.has_value, *self.no_value):
            if name not in GROUPS:
                raise ValueError(f"Unknown group: {name!r}")
        # Normalise: drop empty lists so "active" checks stay simple.
        self.include = {k: list(v) for k, v in self.include.items() if v}
        self.exclude = {k: list(v) for k, v in self.exclude.items() if v}

    def facet_active(self, facet: str) -> bool:
        return facet in self.include or facet in self.exclude

    def is_empty(self) -> bool:
        """True when no facet, group or id restriction is set."""
        return not (self.include or self.exclude or self.has_value
                    or self.no_value or self.ids is not None)

    def pruned_paths(self) -> tuple[list[str], list[str]]:
        """Return the (include, exclude) path lists minus redundant descendants."""
        return (prune_ancestor_paths(self.include.get("path", [])),
                prune_ancestor_paths(self.exclude.get("path", [])))


def prune_ancestor_paths(paths: list[str]) -> list[str]:
    """Return a minimal covering subset: drop paths whose ancestor is already present.

    Prevents SQLite "Expression tree is too large" when a high-level folder is
    checked in the tree and hundreds of descendant paths are also collected.
    """
    normalized = sorted(p.replace("\\", "/").rstrip("/") for p in paths)
    result: list[str] = []
    for p in normalized:
        if not any(p.startswith(kept + "/") for kept in result):
            result.append(p)
    return result


#: Columns the table can sort by (``models_view`` names).
SORT_COLUMNS: frozenset[str] = frozenset({
    "title", "creator", "source", "sculptor_name", "collection_name",
    "subcollection_name", "sequence", "release_name", "archive_path", "presupports",
    "created_at", "archive_size", "compression_rate", "category_name",
})


def table_ids(
    spec: FilterSpec,
    search: str = "",
    sort: "tuple[str, bool] | None" = ("title", False),
    should_stop=lambda: False,
) -> "list[int] | None":
    """Ids of the models the browser table shows, in display order.

    *sort* is ``(column, descending)`` with *column* in :data:`SORT_COLUMNS`
    (NULLs last, ties by id so the order is reproducible), or ``None`` for no
    ordering (the caller orders, as the duplicate-pairs view does).  *search*
    matches title, creator, source, path and names.  Returns ``None`` when
    *should_stop* became true meanwhile.
    """
    from app.database import native_db
    if sort is not None and sort[0] not in SORT_COLUMNS:
        raise ValueError(f"not a sortable column: {sort[0]!r}")
    ids = native_db.library().table_ids(native_db.spec_dict(spec), search, tuple(sort) if sort else None)
    return None if should_stop() else ids


def page_rows(ids: list[int], pair_for_id: "dict[int, int] | None" = None) -> list[tuple]:
    """Display rows for *ids* (in that order; ids no longer present are skipped).

    Each row: ``(id, title, creator, source, release_year, release_month,
    archive_path, presupports, warning, archive_size, compression_rate,
    sequence, created_at, sculptor_name, collection_name, subcollection_name,
    category_name, release_name, scales, tags, pair_index, to_print)``; *scales*
    and *tags* are ``", "``-joined names in id order, *pair_index* is
    ``pair_for_id[id]`` or -1.
    """
    from datetime import datetime

    from app.database import native_db
    pair_for_id = pair_for_id or {}
    out = []
    for (head, tail) in native_db.library().page_rows(list(ids)):
        created = datetime.fromisoformat(tail[2]) if tail[2] else None
        out.append(head + (tail[0], tail[1], created) + tail[3:10]
                   + (pair_for_id.get(head[0], -1), bool(tail[10]) if tail[10] is not None else None))
    return out
