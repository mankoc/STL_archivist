"""
The browser's filter model and the SQL it produces — Qt-free.

Previously the include/exclude predicates were written twice in
``browser_tab.py``: once for the table (against ``models_view``) and once for
the faceted filter-pane counts (against ``STLModel``).  The two copies had
already drifted (the *Path* has-value group only applied to the table).  Now
both are generated from one :class:`FilterSpec` by :func:`conditions`.

Facet names double as the keys of the per-section scope dict the filter-tree
worker consumes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql import ColumnElement, Select

from app.database.models import STLModel, Tag, model_scales, model_tags

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

# Facets whose values are lookup-row ids compared against an FK column.
_ID_FACETS = {
    "sculptor":      "sculptor_id",
    "collection":    "collection_id",
    "subcollection": "subcollection_id",
    "category":      "category_id",
    "release":       "release_id",
}


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


def _path_matches(archive_path_col, paths: list[str]) -> ColumnElement:
    norm = func.replace(archive_path_col, "\\", "/")
    return or_(*(norm.like(p.rstrip("/") + "/%") for p in paths))


def path_id_select(path_inc: list[str], path_exc: list[str]) -> Select:
    """SELECT of model ids matching the (already pruned) path filters.

    The filter-tree worker materialises this into a TEMP table once, so the
    un-indexable ``LIKE`` scan is not repeated for every facet scope.
    """
    stmt = select(STLModel.id)
    if path_inc:
        stmt = stmt.where(_path_matches(STLModel.archive_path, path_inc))
    if path_exc:
        stmt = stmt.where(~_path_matches(STLModel.archive_path, path_exc))
    return stmt


def conditions(
    spec: FilterSpec,
    cols,
    *,
    skip: str | None = None,
    path_ids=None,
) -> list[ColumnElement]:
    """Build the WHERE conditions for *spec*.

    *cols* is any namespace exposing the model columns by name — the
    ``models_view`` ``.c`` collection or the ``STLModel`` class.  *skip* omits
    one facet (for faceted counts: each section is scoped by all the others).
    *path_ids*, when given, is a selectable of ids that replaces the path
    ``LIKE`` predicates (see :func:`path_id_select`).
    """
    inc, exc = spec.include, spec.exclude
    out: list[ColumnElement] = []
    model_id = cols.id

    def want(facet: str) -> bool:
        return facet != skip and spec.facet_active(facet)

    if spec.ids is not None:
        out.append(model_id.in_(spec.ids))

    for facet in ("creator", "source"):
        if want(facet):
            col = func.lower(getattr(cols, facet))
            if facet in inc:
                out.append(col.in_([v.lower() for v in inc[facet]]))
            if facet in exc:
                out.append(~col.in_([v.lower() for v in exc[facet]]))

    if want("tag"):
        tagged = select(model_tags.c.model_id).join(Tag, model_tags.c.tag_id == Tag.id)
        if "tag" in inc:
            out.append(model_id.in_(tagged.where(Tag.name.in_(inc["tag"]))))
        if "tag" in exc:
            out.append(model_id.not_in(tagged.where(Tag.name.in_(exc["tag"]))))

    if want("path"):
        if path_ids is not None:
            out.append(model_id.in_(path_ids))
        else:
            p_inc, p_exc = spec.pruned_paths()
            if p_inc:
                out.append(_path_matches(cols.archive_path, p_inc))
            if p_exc:
                out.append(~_path_matches(cols.archive_path, p_exc))

    if want("date"):
        def _dates(pairs):
            return or_(*(
                (cols.release_year == y) if mo is None
                else and_(cols.release_year == y, cols.release_month == mo)
                for y, mo in pairs
            ))
        if "date" in inc:
            out.append(_dates(inc["date"]))
        if "date" in exc:
            out.append(~_dates(exc["date"]))

    for facet, column in _ID_FACETS.items():
        if want(facet):
            col = getattr(cols, column)
            if facet in inc:
                out.append(col.in_(inc[facet]))
            if facet in exc:
                out.append(~col.in_(exc[facet]))

    if want("scale"):
        scaled = select(model_scales.c.model_id)
        if "scale" in inc:
            out.append(model_id.in_(scaled.where(model_scales.c.scale_id.in_(inc["scale"]))))
        if "scale" in exc:
            out.append(model_id.not_in(scaled.where(model_scales.c.scale_id.in_(exc["scale"]))))

    if want("presupports"):
        if "presupports" in inc:
            out.append(cols.presupports.in_(inc["presupports"]))
        if "presupports" in exc:
            out.append(~cols.presupports.in_(exc["presupports"]))

    if want("warning"):
        if "warning" in inc:
            out.append(or_(*(cols.warning.contains(w) for w in inc["warning"])))
        if "warning" in exc:
            out.append(and_(*(~cols.warning.contains(w) for w in exc["warning"])))

    if want("filetype"):
        ap = func.lower(cols.archive_path)
        if "filetype" in inc:
            out.append(or_(*(ap.like("%" + ext) for ext in inc["filetype"])))
        if "filetype" in exc:
            out.append(and_(*(~ap.like("%" + ext) for ext in exc["filetype"])))

    # Has-value / no-value groups are never skipped: they are not a section.
    for key in spec.has_value:
        out.append(_group_has_value(key, cols))
    for key in spec.no_value:
        out.append(~_group_has_value(key, cols))

    return out


def _group_has_value(key: str, cols) -> ColumnElement:
    def _non_empty(col):
        return and_(col.isnot(None), col != "")
    if key == "tag":
        return cols.id.in_(select(model_tags.c.model_id).distinct())
    if key == "scale":
        return cols.id.in_(select(model_scales.c.model_id).distinct())
    if key == "date":
        return cols.release_year.isnot(None)
    if key in _ID_FACETS:
        return getattr(cols, _ID_FACETS[key]).isnot(None)
    if key == "path":
        return _non_empty(cols.archive_path)
    return _non_empty(getattr(cols, key))   # creator, source, presupports, warning


def table_where(spec: FilterSpec, mv) -> ColumnElement | None:
    """Combined WHERE clause for the browser table over ``models_view`` *mv*."""
    conds = conditions(spec, mv.c)
    return and_(*conds) if conds else None


def facet_scopes(spec: FilterSpec, *, path_ids=None) -> dict[str, Select | None] | None:
    """Per-section scope id-SELECTs for the faceted filter pane.

    Each section's scope is the set of model ids matching every *other* active
    filter, so a section's counts show what selecting one of its values would
    add.  Use them as ``col.in_(scope)``.  Returns ``None`` when nothing is
    filtered (counts come from the cache); a section maps to ``None`` when no
    other filter applies to it.
    """
    if spec.is_empty():
        return None
    scopes: dict[str, Select | None] = {}
    for facet in FACETS:
        conds = conditions(spec, STLModel, skip=facet, path_ids=path_ids)
        scopes[facet] = select(STLModel.id).where(*conds) if conds else None
    return scopes


#: Columns the browser table can sort by (``models_view`` names).  Scale and tags
#: are many-to-many and not sortable.
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
    adds :func:`search_condition`.  Returns ``None`` when *should_stop* became
    true while the ids streamed in.
    """
    from sqlalchemy import asc, desc, nullslast

    from app.database.db import get_models_view, get_session

    mv = get_models_view()
    stmt = select(mv.c.id).select_from(mv)
    where = table_where(spec, mv)
    if where is not None:
        stmt = stmt.where(where)
    if search:
        stmt = stmt.where(search_condition(mv, search))
    if sort is not None:
        column, descending = sort
        if column not in SORT_COLUMNS:
            raise ValueError(f"not a sortable column: {column!r}")
        fn = desc if descending else asc
        stmt = stmt.order_by(nullslast(fn(mv.c[column])), mv.c.id)
    session = get_session()
    try:
        ids: list[int] = []
        for row in session.execute(stmt.execution_options(yield_per=2000)):
            if should_stop():
                return None
            ids.append(row[0])
        return ids
    finally:
        session.close()


def page_rows(ids: list[int], pair_for_id: "dict[int, int] | None" = None) -> list[tuple]:
    """Display rows for *ids* (in that order; ids no longer present are skipped).

    Each row: ``(id, title, creator, source, release_year, release_month,
    archive_path, presupports, warning, archive_size, compression_rate,
    sequence, created_at, sculptor_name, collection_name, subcollection_name,
    category_name, release_name, scales, tags, pair_index, to_print)``; *scales*
    and *tags* are ``", "``-joined names in id order, *pair_index* is
    ``pair_for_id[id]`` or -1.
    """
    from app.database.db import get_models_view, get_session
    from app.database.models import Scale

    pair_for_id = pair_for_id or {}
    mv = get_models_view()
    session = get_session()
    try:
        stmt = select(
            mv.c.id, mv.c.title, mv.c.creator, mv.c.source,
            mv.c.release_year, mv.c.release_month,
            mv.c.archive_path, mv.c.presupports, mv.c.warning,
            mv.c.archive_size, mv.c.compression_rate,
            mv.c.sequence, mv.c.created_at,
            mv.c.sculptor_name, mv.c.collection_name,
            mv.c.subcollection_name, mv.c.category_name,
            mv.c.release_name, mv.c.to_print,
        ).select_from(mv).where(mv.c.id.in_(ids))
        rows_by_id = {row.id: row for row in session.execute(stmt).all()}

        def joined(join_table, fk, lookup):
            # Names in lookup-id order, as the join table's primary key orders them.
            out: dict[int, list[str]] = {}
            q = (select(join_table.c.model_id, lookup.name)
                 .join(lookup, fk == lookup.id)
                 .where(join_table.c.model_id.in_(ids))
                 .order_by(join_table.c.model_id, lookup.id))
            for mid, name in session.execute(q):
                out.setdefault(mid, []).append(name)
            return {mid: ", ".join(names) for mid, names in out.items()}

        tags_for = joined(model_tags, model_tags.c.tag_id, Tag)
        scales_for = joined(model_scales, model_scales.c.scale_id, Scale)
        return [
            (r.id, r.title, r.creator, r.source, r.release_year, r.release_month,
             r.archive_path, r.presupports, r.warning, r.archive_size,
             r.compression_rate, r.sequence, r.created_at, r.sculptor_name,
             r.collection_name, r.subcollection_name, r.category_name,
             r.release_name, scales_for.get(r.id, ""), tags_for.get(r.id, ""),
             pair_for_id.get(r.id, -1), r.to_print)
            for r in (rows_by_id.get(mid) for mid in ids) if r is not None
        ]
    finally:
        session.close()


def search_condition(mv, text: str) -> ColumnElement:
    """Free-text search across title, creator, sculptor, collection, path and tags."""
    pat = f"%{text}%"
    tagged = (
        select(model_tags.c.model_id)
        .join(Tag, model_tags.c.tag_id == Tag.id)
        .where(Tag.name.ilike(pat))
    )
    return or_(
        mv.c.title.ilike(pat),
        mv.c.creator.ilike(pat),
        mv.c.sculptor_name.ilike(pat),
        mv.c.collection_name.ilike(pat),
        mv.c.archive_path.ilike(pat),
        mv.c.id.in_(tagged),
    )
