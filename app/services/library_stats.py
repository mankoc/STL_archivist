"""
Library statistics for the Statistics and Column Distribution dialogs — Qt-free.

Each function runs natively (``stl_native.LibraryDb``,
``rust/stl-core/src/storage/stats.rs``) when :func:`app.database.native_db.library`
allows it, and in Python (the dialogs' former queries, moved) otherwise;
``tests/golden/test_browser_golden.py`` compares the two.  Rankings break count
ties by value (direct columns) or by lookup id, so both give the same order.
"""
from __future__ import annotations

from sqlalchemy import func

from app.database import native_db
from app.database.db import session_scope
from app.database.models import (
    Category, Collection, Image, Release, Scale, Sculptor, STLFile, STLModel, Subcollection, Tag,
    model_scales, model_tags,
)

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

#: Column keys :func:`column_distribution` accepts.
COLUMN_KEYS = ("category", "collection", "creator", "presupports", "release", "release_month",
               "release_year", "scale", "sculptor", "source", "subcollection", "tag")

# Direct columns: (column, label for NULL, skip NULL).
_DIRECT = {
    "source": ("source", "(unknown)", False),
    "release_year": ("release_year", "(none)", True),
    "release_month": ("release_month", "(none)", True),
    "presupports": ("presupports", "(not set)", False),
    "creator": ("creator", "(unknown)", False),
}


def library_statistics() -> dict:
    """The database part of the Statistics dialog: counts, sums and rankings.

    Rankings are lists of ``(value, count)`` tuples.
    """
    lib = native_db.library()
    if lib is not None:
        return lib.stats_library()
    stats: dict = {}
    with session_scope() as s:
        count = lambda col: s.query(func.count(col)).scalar() or 0  # noqa: E731
        stats["total_models"] = count(STLModel.id)
        stats["total_images"] = count(Image.id)
        stats["total_sculptors"] = count(Sculptor.id)
        stats["total_collections"] = count(Collection.id)
        stats["total_categories"] = count(Category.id)
        stats["total_releases"] = count(Release.id)
        stats["total_scales"] = count(Scale.id)
        stats["total_tags"] = count(Tag.id)
        stats["total_archive_size"] = s.query(func.sum(STLModel.archive_size)).scalar() or 0
        stats["total_file_size"] = s.query(func.sum(STLFile.file_size)).scalar() or 0

        stats["presupported"] = s.query(func.count(STLModel.id)).filter(
            STLModel.presupports.isnot(None),
            STLModel.presupports.notin_(["", "No"]),
        ).scalar() or 0
        stats["with_warnings"] = s.query(func.count(STLModel.id)).filter(
            STLModel.warning.isnot(None),
        ).scalar() or 0
        stats["no_images"] = s.query(func.count(STLModel.id)).outerjoin(
            Image, STLModel.id == Image.model_id
        ).filter(Image.id.is_(None)).scalar() or 0
        stats["with_url"] = s.query(func.count(STLModel.id)).filter(
            STLModel.url.isnot(None),
            STLModel.url != "",
        ).scalar() or 0

        n = func.count(STLModel.id)
        stats["by_source"] = _tuples(
            s.query(STLModel.source, n).group_by(STLModel.source).order_by(n.desc(), STLModel.source))
        stats["by_year"] = _tuples(
            s.query(STLModel.release_year, n).filter(STLModel.release_year.isnot(None))
            .group_by(STLModel.release_year).order_by(n.desc(), STLModel.release_year).limit(10))
        stats["top_sculptors"] = _tuples(
            s.query(Sculptor.name, n).join(STLModel, STLModel.sculptor_id == Sculptor.id)
            .group_by(Sculptor.id).order_by(n.desc(), Sculptor.id).limit(15))
        nt = func.count(model_tags.c.model_id)
        stats["top_tags"] = _tuples(
            s.query(Tag.name, nt).join(model_tags, model_tags.c.tag_id == Tag.id)
            .group_by(Tag.id).order_by(nt.desc(), Tag.id).limit(15))
        stats["top_collections"] = _tuples(
            s.query(Collection.name, n).join(STLModel, STLModel.collection_id == Collection.id)
            .group_by(Collection.id).order_by(n.desc(), Collection.id).limit(10))
    return stats


def _tuples(q) -> list[tuple]:
    return [tuple(r) for r in q.all()]


def column_distribution(key: str, top_n: int | None) -> tuple[list[tuple[str, int]], int]:
    """Return ``(data, total_models)`` for the Column Distribution dialog.

    *data* is a list of ``(label, count)`` pairs sorted by count descending,
    capped at *top_n* entries; an unknown *key* gives no data.
    """
    lib = native_db.library()
    if lib is not None:
        raw, total = lib.stats_column(key, top_n)
    else:
        raw, total = _column_py(key, top_n)
    if key in _DIRECT:
        null_label = _DIRECT[key][1]
        data = [(str(v) if v is not None else null_label, cnt) for v, cnt in raw]
        if key == "release_month":
            data = [(MONTH_NAMES.get(int(v), v) if v.isdigit() else v, cnt) for v, cnt in data]
        return data, total
    return [tuple(r) for r in raw], total


def _column_py(key: str, top_n: int | None) -> tuple[list[tuple], int]:
    """Raw ``(value, count)`` rows: direct column values are not yet labelled."""
    with session_scope() as session:
        total: int = session.query(func.count(STLModel.id)).scalar() or 0

        def cap(q):
            return _tuples(q.limit(top_n) if top_n is not None else q)

        n = func.count(STLModel.id)
        if key in _DIRECT:
            col = getattr(STLModel, _DIRECT[key][0])
            q = session.query(col, n)
            if _DIRECT[key][2]:
                q = q.filter(col.isnot(None))
            return cap(q.group_by(col).order_by(n.desc(), col)), total
        joined = {
            "sculptor": (Sculptor, STLModel.sculptor_id),
            "collection": (Collection, STLModel.collection_id),
            "subcollection": (Subcollection, STLModel.subcollection_id),
            "category": (Category, STLModel.category_id),
            "release": (Release, STLModel.release_id),
        }
        if key in joined:
            cls, fk = joined[key]
            return cap(session.query(cls.name, n).join(STLModel, fk == cls.id)
                       .group_by(cls.id).order_by(n.desc(), cls.id)), total
        links = {"scale": (Scale, model_scales, model_scales.c.scale_id),
                 "tag": (Tag, model_tags, model_tags.c.tag_id)}
        if key in links:
            cls, table, fk = links[key]
            nl = func.count(table.c.model_id)
            return cap(session.query(cls.name, nl).join(table, fk == cls.id)
                       .group_by(cls.id).order_by(nl.desc(), cls.id)), total
        return [], total
