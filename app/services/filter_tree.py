"""
The browser filter pane's data: per-section values and counts — Qt-free.

``filter_tree_data`` runs every query the filter tree needs for one rebuild.
Moved out of ``app.ui.browser_filter_tree._FilterTreeWorker`` (which now only
wraps it) so it can be tested without Qt and served natively.

Scoped counts follow :func:`app.services.library_query.facet_scopes`: each
section is counted over the models matching every *other* active filter.
Path matches are materialised once into a connection-local TEMP table
(:data:`FILTER_PATH_IDS`), so the un-indexable ``archive_path LIKE`` scan runs a
single time instead of once per section.
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, Table, select

from app.services.library_query import FilterSpec, facet_scopes

FILTER_PATH_IDS_NAME = "_filter_path_ids"
FILTER_PATH_IDS = Table(
    FILTER_PATH_IDS_NAME, MetaData(),
    Column("id", Integer, primary_key=True),
)


def filter_tree_data(spec: "FilterSpec | None", show: dict, show_counts: bool) -> dict:
    """Values and counts for every visible filter section.

    *spec* ``None`` (or empty) gives the unscoped tree.  Returns the payload keys
    the browser consumes (``counts``, ``path_counts``, ``scoped_counts``,
    ``sc_path_counts``, the value lists, ``sorted_dirs`` / ``scoped_dirs`` /
    ``new_dirs_frozen``) plus ``scoped``: whether section scopes applied.
    """
    return _filter_tree_data_py(spec, show, show_counts)


def _filter_tree_data_py(spec: "FilterSpec | None", show: dict, show_counts: bool) -> dict:
    from pathlib import Path as _P

    from sqlalchemy import func as _func

    from app.database.db import get_session
    from app.database.models import (
        STLModel, Tag, Sculptor, Collection, Subcollection,
        Category, Release, Scale, model_tags as _mt, model_scales as _ms,
    )

    path_inc: list[str] = []
    path_exc: list[str] = []
    scope = None
    if spec is not None and not spec.is_empty():
        path_ids = None
        if spec.facet_active("path"):
            path_inc, path_exc = spec.pruned_paths()
            path_ids = select(FILTER_PATH_IDS.c.id)
        scope = facet_scopes(spec, path_ids=path_ids)
    _FILTER_PATH_IDS = FILTER_PATH_IDS
    _FILTER_PATH_IDS_NAME = FILTER_PATH_IDS_NAME

    def _s(section):
        return scope.get(section) if scope else None

    session = get_session()
    try:
        # ── Path filter: materialise matching IDs once into a connection-local
        # TEMP table that the scoped subqueries reference (see _FILTER_PATH_IDS).
        # The archive_path LIKE scan runs a single time; every subsequent lookup
        # is an indexed primary-key probe. ──────────────────────────────────────
        if path_inc or path_exc:
            from sqlalchemy import insert as _insert
            conn = session.connection()
            conn.exec_driver_sql(
                f"CREATE TEMP TABLE IF NOT EXISTS {_FILTER_PATH_IDS_NAME} "
                f"(id INTEGER PRIMARY KEY)"
            )
            conn.exec_driver_sql(f"DELETE FROM {_FILTER_PATH_IDS_NAME}")
            from app.services.library_query import path_id_select
            _psel = path_id_select(path_inc, path_exc)
            session.execute(_insert(_FILTER_PATH_IDS).from_select(["id"], _psel))

        # ── Counts ─────────────────────────────────────────────────────────
        _counts: dict = {}
        _path_counts: "dict[str, int] | None" = None
        _scoped_counts: dict = {}
        _sc_path_counts: "dict[str, int] | None" = None

        if show_counts:
            _grp: dict = {}

            def _count_col(col, kind: str, *, none_filter: bool = True) -> None:
                q = session.query(col, _func.count(STLModel.id))
                if none_filter:
                    q = q.filter(col.isnot(None))
                total = 0
                for v, c in q.group_by(col).all():
                    _counts[(kind, v)] = c
                    total += c
                _grp[kind] = total

            _count_col(STLModel.creator,          "creator")
            _count_col(STLModel.source,           "source")
            _count_col(STLModel.sculptor_id,      "sculptor_id")
            _count_col(STLModel.collection_id,    "collection_id")
            _count_col(STLModel.subcollection_id, "subcollection_id")
            _count_col(STLModel.category_id,      "category_id")
            _count_col(STLModel.release_id,       "release_id")
            _count_col(STLModel.presupports,      "presupports")

            _year_cnt: dict = {}
            _date_total = 0
            for yr, mo, c in (
                session.query(STLModel.release_year, STLModel.release_month, _func.count(STLModel.id))
                .filter(STLModel.release_year.isnot(None))
                .group_by(STLModel.release_year, STLModel.release_month)
                .all()
            ):
                _counts[("date_month", (yr, mo))] = c
                _year_cnt[yr] = _year_cnt.get(yr, 0) + c
                _date_total += c
            for yr, c in _year_cnt.items():
                _counts[("date_year", yr)] = c
            _grp["date"] = _date_total

            _all_warnings = [
                w for (w,) in session.query(STLModel.warning)
                .filter(STLModel.warning.isnot(None)).all()
            ]
            for _wt in ("Models with presupported stl", "Archive without images",
                        "Nested archives",
                        "Archive without printable files", "Unreadable archive"):
                _counts[("warning", _wt)] = sum(1 for w in _all_warnings if _wt in w)
            _grp["warning"] = sum(1 for w in _all_warnings if w)

            # File type counts (derived from archive_path extension).
            _ft_raw = [
                _P(ap).suffix.lower()
                for (ap,) in session.query(STLModel.archive_path)
                .filter(STLModel.archive_path.isnot(None)).all()
                if ap
            ]
            for _ft in set(_ft_raw):
                _counts[("filetype", _ft)] = _ft_raw.count(_ft)
            _grp["filetype"] = len(_ft_raw)

            # Use pre-computed archive_folder when available (SQL GROUP BY),
            # falling back to Python path parsing for older rows where it is NULL.
            _direct: dict = {}
            for (folder, cnt) in (
                session.query(STLModel.archive_folder, _func.count(STLModel.id))
                .filter(STLModel.archive_folder.isnot(None))
                .group_by(STLModel.archive_folder)
                .all()
            ):
                _direct[folder] = _direct.get(folder, 0) + cnt
            for (ap,) in session.query(STLModel.archive_path).filter(
                STLModel.archive_folder.is_(None),
                STLModel.archive_path.isnot(None),
            ).all():
                folder = _P(ap).parent.as_posix()
                _direct[folder] = _direct.get(folder, 0) + 1

            for k, v in _grp.items():
                _counts[("group", k)] = v

            for tn, c in (
                session.query(Tag.name, _func.count(_mt.c.model_id))
                .join(_mt, Tag.id == _mt.c.tag_id)
                .group_by(Tag.name)
                .all()
            ):
                _counts[("tag", tn)] = c
            _counts[("group", "tag")] = (
                session.query(_func.count(_mt.c.model_id.distinct())).scalar() or 0
            )

            # Scale is many-to-many (model_scales); count like tags but keep
            # the id-based leaf keys the tree uses (("scale_id", sid)).
            for sid, c in (
                session.query(_ms.c.scale_id, _func.count(_ms.c.model_id))
                .group_by(_ms.c.scale_id)
                .all()
            ):
                _counts[("scale_id", sid)] = c
            _counts[("group", "scale")] = (
                session.query(_func.count(_ms.c.model_id.distinct())).scalar() or 0
            )

            _path_counts = {}
            for folder, _c in _direct.items():
                _path_counts[folder] = _path_counts.get(folder, 0) + _c
                _f = _P(folder)
                while True:
                    _p = _f.parent
                    if _p == _f:
                        break
                    _ps = _p.as_posix()
                    _path_counts[_ps] = _path_counts.get(_ps, 0) + _c
                    _f = _p
            _counts[("group", "path")] = sum(_direct.values())

            # Scoped counts
            if scope is not None:
                from app.database.models import model_tags as _mt2

                def _sc_count_col(col, kind: str, sc_ids, *, none_filter=True) -> None:
                    rows = (
                        session.query(col, _func.count(STLModel.id))
                        .filter(STLModel.id.in_(sc_ids))
                    )
                    if none_filter:
                        rows = rows.filter(col.isnot(None))
                    rows = rows.group_by(col).all()
                    for v, c in rows:
                        _scoped_counts[(kind, v)] = c
                    _scoped_counts[("group", kind.rstrip("_id"))] = sum(c for _, c in rows)

                for section, col, kind in (
                    ("creator",       STLModel.creator,          "creator"),
                    ("source",        STLModel.source,           "source"),
                    ("sculptor",      STLModel.sculptor_id,      "sculptor_id"),
                    ("collection",    STLModel.collection_id,    "collection_id"),
                    ("subcollection", STLModel.subcollection_id, "subcollection_id"),
                    ("category",      STLModel.category_id,      "category_id"),
                    ("release",       STLModel.release_id,       "release_id"),
                    ("presupports",   STLModel.presupports,      "presupports"),
                ):
                    sc_ids = _s(section)
                    if sc_ids is not None:
                        _sc_count_col(col, kind, sc_ids)

                _scale_sc = _s("scale")
                if _scale_sc is not None:
                    for sid, c in (
                        session.query(_ms.c.scale_id, _func.count(_ms.c.model_id))
                        .filter(_ms.c.model_id.in_(_scale_sc))
                        .group_by(_ms.c.scale_id)
                        .all()
                    ):
                        _scoped_counts[("scale_id", sid)] = c
                    _scoped_counts[("group", "scale")] = (
                        session.query(_func.count(_ms.c.model_id.distinct()))
                        .filter(_ms.c.model_id.in_(_scale_sc))
                        .scalar() or 0
                    )

                _tag_sc = _s("tag")
                if _tag_sc is not None:
                    for tn, c in (
                        session.query(Tag.name, _func.count(_mt2.c.model_id))
                        .join(_mt2, Tag.id == _mt2.c.tag_id)
                        .filter(_mt2.c.model_id.in_(_tag_sc))
                        .group_by(Tag.name)
                        .all()
                    ):
                        _scoped_counts[("tag", tn)] = c
                    _scoped_counts[("group", "tag")] = (
                        session.query(_func.count(_mt2.c.model_id.distinct()))
                        .filter(_mt2.c.model_id.in_(_tag_sc))
                        .scalar() or 0
                    )

                _date_sc = _s("date")
                if _date_sc is not None:
                    _sc_year: dict = {}
                    _drows = (
                        session.query(STLModel.release_year, STLModel.release_month, _func.count(STLModel.id))
                        .filter(STLModel.id.in_(_date_sc))
                        .filter(STLModel.release_year.isnot(None))
                        .group_by(STLModel.release_year, STLModel.release_month)
                        .all()
                    )
                    for yr, mo, c in _drows:
                        _scoped_counts[("date_month", (yr, mo))] = c
                        _sc_year[yr] = _sc_year.get(yr, 0) + c
                    for yr, c in _sc_year.items():
                        _scoped_counts[("date_year", yr)] = c
                    _scoped_counts[("group", "date")] = sum(c for _, _, c in _drows)

                _warn_sc = _s("warning")
                if _warn_sc is not None:
                    _sc_warns = [
                        w for (w,) in session.query(STLModel.warning)
                        .filter(STLModel.id.in_(_warn_sc))
                        .filter(STLModel.warning.isnot(None))
                        .all()
                    ]
                    for _wt in ("Models with presupported stl", "Archive without images",
                                "Nested archives",
                                "Archive without printable files"):
                        _scoped_counts[("warning", _wt)] = sum(1 for w in _sc_warns if _wt in w)
                    _scoped_counts[("group", "warning")] = sum(1 for w in _sc_warns if w)

                _ft_sc = _s("filetype")
                if _ft_sc is not None:
                    _sc_ft = [
                        _P(ap).suffix.lower()
                        for (ap,) in session.query(STLModel.archive_path)
                        .filter(STLModel.id.in_(_ft_sc))
                        .filter(STLModel.archive_path.isnot(None)).all()
                        if ap
                    ]
                    for _ft in set(_sc_ft):
                        _scoped_counts[("filetype", _ft)] = _sc_ft.count(_ft)
                    _scoped_counts[("group", "filetype")] = len(_sc_ft)

                _path_sc = _s("path")
                if _path_sc is not None:
                    _sc_direct: dict = {}
                    for (folder, cnt) in (
                        session.query(STLModel.archive_folder, _func.count(STLModel.id))
                        .filter(STLModel.id.in_(_path_sc))
                        .filter(STLModel.archive_folder.isnot(None))
                        .group_by(STLModel.archive_folder)
                        .all()
                    ):
                        _sc_direct[folder] = _sc_direct.get(folder, 0) + cnt
                    for (ap,) in (
                        session.query(STLModel.archive_path)
                        .filter(STLModel.id.in_(_path_sc))
                        .filter(STLModel.archive_folder.is_(None))
                        .filter(STLModel.archive_path.isnot(None))
                        .all()
                    ):
                        folder = _P(ap).parent.as_posix()
                        _sc_direct[folder] = _sc_direct.get(folder, 0) + 1
                    _sc_path_counts = {}
                    for folder, _c in _sc_direct.items():
                        _sc_path_counts[folder] = _sc_path_counts.get(folder, 0) + _c
                        _f = _P(folder)
                        while True:
                            _p = _f.parent
                            if _p == _f:
                                break
                            _ps = _p.as_posix()
                            _sc_path_counts[_ps] = _sc_path_counts.get(_ps, 0) + _c
                            _f = _p
                    _scoped_counts[("group", "path")] = sum(_sc_direct.values())

        # ── Distinct value lists for each visible section ───────────────────
        def _scoped_ids(section: str):
            return _s(section)

        creators: list[str] = []
        if show.get("author"):
            _cs = _scoped_ids("creator")
            q = session.query(STLModel.creator).distinct().order_by(STLModel.creator)
            if _cs is not None:
                q = q.filter(STLModel.id.in_(_cs))
            creators = [c for (c,) in q.all() if c]

        tags: list[str] = []
        if show.get("tags"):
            _ts = _scoped_ids("tag")
            q = session.query(Tag).order_by(Tag.name)
            if _ts is not None:
                q = (
                    q.join(_mt, Tag.id == _mt.c.tag_id)
                    .filter(_mt.c.model_id.in_(_ts))
                    .distinct()
                )
            tags = [t.name for t in q.all()]

        sources: list[str] = []
        if show.get("source"):
            _ss = _scoped_ids("source")
            q = session.query(STLModel.source).distinct().order_by(STLModel.source)
            if _ss is not None:
                q = q.filter(STLModel.id.in_(_ss))
            sources = [s for (s,) in q.all() if s]

        dates: list = []
        if show.get("date"):
            _ds = _scoped_ids("date")
            q = (
                session.query(STLModel.release_year, STLModel.release_month)
                .filter(STLModel.release_year.isnot(None))
                .distinct()
                .order_by(STLModel.release_year.desc(), STLModel.release_month)
            )
            if _ds is not None:
                q = q.filter(STLModel.id.in_(_ds))
            dates = list(q.all())

        sculptors: list = []
        if show.get("sculptor"):
            _scs = _scoped_ids("sculptor")
            q = session.query(Sculptor).order_by(Sculptor.name)
            if _scs is not None:
                _sc_ids = {
                    sid for (sid,) in
                    session.query(STLModel.sculptor_id)
                    .filter(STLModel.id.in_(_scs))
                    .filter(STLModel.sculptor_id.isnot(None))
                    .distinct()
                }
                q = q.filter(Sculptor.id.in_(_sc_ids))
            sculptors = [(sc.name, sc.id) for sc in q.all()]

        collections: list = []
        if show.get("collection"):
            _cos = _scoped_ids("collection")
            q = session.query(Collection).order_by(Collection.name)
            if _cos is not None:
                _col_ids = {
                    cid for (cid,) in
                    session.query(STLModel.collection_id)
                    .filter(STLModel.id.in_(_cos))
                    .filter(STLModel.collection_id.isnot(None))
                    .distinct()
                }
                q = q.filter(Collection.id.in_(_col_ids))
            collections = [(col.name, col.id) for col in q.all()]

        sub_by_col: list = []  # list[(col_name, col_id, [(sub_name, sub_id)])]
        if show.get("subcollection"):
            _subs = _scoped_ids("subcollection")
            _scoped_sub_ids = None
            if _subs is not None:
                _scoped_sub_ids = {
                    sid for (sid,) in
                    session.query(STLModel.subcollection_id)
                    .filter(STLModel.id.in_(_subs))
                    .filter(STLModel.subcollection_id.isnot(None))
                    .distinct()
                }
            _col_map: dict = {}
            for col_name, col_id, sub_name, sub_id in (
                session.query(Collection.name, Collection.id, Subcollection.name, Subcollection.id)
                .join(Subcollection, Subcollection.collection_id == Collection.id)
                .order_by(Collection.name, Subcollection.name)
                .all()
            ):
                if _scoped_sub_ids is not None and sub_id not in _scoped_sub_ids:
                    continue
                if col_id not in _col_map:
                    _col_map[col_id] = (col_name, [])
                _col_map[col_id][1].append((sub_name, sub_id))
            sub_by_col = [(name, col_id, subs) for col_id, (name, subs) in _col_map.items()]

        categories: list = []
        if show.get("category"):
            _cas = _scoped_ids("category")
            q = session.query(Category).order_by(Category.name)
            if _cas is not None:
                _cat_ids = {
                    cid for (cid,) in
                    session.query(STLModel.category_id)
                    .filter(STLModel.id.in_(_cas))
                    .filter(STLModel.category_id.isnot(None))
                    .distinct()
                }
                q = q.filter(Category.id.in_(_cat_ids))
            categories = [(cat.name, cat.id) for cat in q.all()]

        releases: list = []
        if show.get("release"):
            _rs = _scoped_ids("release")
            q = session.query(Release).order_by(Release.name)
            if _rs is not None:
                _rel_ids = {
                    rid for (rid,) in
                    session.query(STLModel.release_id)
                    .filter(STLModel.id.in_(_rs))
                    .filter(STLModel.release_id.isnot(None))
                    .distinct()
                }
                q = q.filter(Release.id.in_(_rel_ids))
            releases = [(r.name, r.id) for r in q.all()]

        scales: list = []
        if show.get("scale"):
            _sls = _scoped_ids("scale")
            q = session.query(Scale).order_by(Scale.name)
            if _sls is not None:
                _scale_ids = {
                    sid for (sid,) in
                    session.query(_ms.c.scale_id)
                    .filter(_ms.c.model_id.in_(_sls))
                    .distinct()
                }
                q = q.filter(Scale.id.in_(_scale_ids))
            scales = [(sc.name, sc.id) for sc in q.all()]

        presupports: list[str] = []
        if show.get("presupports"):
            _ps2 = _scoped_ids("presupports")
            q = (
                session.query(STLModel.presupports)
                .filter(STLModel.presupports.isnot(None))
                .distinct()
                .order_by(STLModel.presupports)
            )
            if _ps2 is not None:
                q = q.filter(STLModel.id.in_(_ps2))
            presupports = [v for (v,) in q.all() if v]

        # ── File types ─────────────────────────────────────────────────────
        filetypes: list[str] = []
        _ft_sc2 = _scoped_ids("filetype")
        _ft_q = session.query(STLModel.archive_path).filter(STLModel.archive_path.isnot(None))
        if _ft_sc2 is not None:
            _ft_q = _ft_q.filter(STLModel.id.in_(_ft_sc2))
        filetypes = sorted({
            _P(ap).suffix.lower()
            for (ap,) in _ft_q.all()
            if ap and _P(ap).suffix
        })

        # ── Path dirs ──────────────────────────────────────────────────────
        sorted_dirs: list[str] = []
        scoped_dirs: "list[str] | None" = None
        new_dirs_frozen: "frozenset | None" = None
        if show.get("path"):
            _path_sc2 = _scoped_ids("path")
            if _path_sc2 is not None:
                _sd: set[str] = set()
                for (folder,) in (
                    session.query(STLModel.archive_folder)
                    .filter(STLModel.id.in_(_path_sc2))
                    .filter(STLModel.archive_folder.isnot(None))
                    .distinct()
                    .all()
                ):
                    _sd.add(folder)
                # fallback for rows without archive_folder
                for (ap,) in (
                    session.query(STLModel.archive_path)
                    .filter(STLModel.id.in_(_path_sc2))
                    .filter(STLModel.archive_folder.is_(None))
                    .filter(STLModel.archive_path.isnot(None))
                    .distinct()
                    .all()
                ):
                    _sd.add(_P(ap).parent.as_posix())
                scoped_dirs = sorted(_sd)
            else:
                _all_dirs: set[str] = set()
                for (folder,) in (
                    session.query(STLModel.archive_folder)
                    .filter(STLModel.archive_folder.isnot(None))
                    .distinct()
                    .all()
                ):
                    _all_dirs.add(folder)
                # fallback for rows without archive_folder
                for (ap,) in (
                    session.query(STLModel.archive_path)
                    .filter(STLModel.archive_folder.is_(None))
                    .distinct()
                    .all()
                ):
                    if ap:
                        _all_dirs.add(_P(ap).parent.as_posix())
                new_dirs_frozen = frozenset(_all_dirs)
                sorted_dirs = sorted(_all_dirs)

    finally:
        session.close()


    return {
        "counts": _counts,
        "path_counts": _path_counts,
        "scoped_counts": _scoped_counts,
        "sc_path_counts": _sc_path_counts,
        "creators": creators,
        "tags": tags,
        "sources": sources,
        "dates": [tuple(d) for d in dates],
        "sculptors": sculptors,
        "collections": collections,
        "sub_by_col": sub_by_col,
        "categories": categories,
        "releases": releases,
        "scales": scales,
        "presupports": presupports,
        "filetypes": filetypes,
        "sorted_dirs": sorted_dirs,
        "scoped_dirs": scoped_dirs,
        "new_dirs_frozen": new_dirs_frozen,
        "scoped": scope is not None,
    }
