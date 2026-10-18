"""
Library statistics for the Statistics and Column Distribution dialogs — Qt-free.

Each function runs natively (``stl_native.LibraryDb``,
``rust/stl-core/src/storage/stats.rs``).  Rankings break count ties by value
(direct columns) or by lookup id.
"""
from __future__ import annotations


from app.database import native_db

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
    return lib.stats_library()


def _tuples(q) -> list[tuple]:
    return [tuple(r) for r in q.all()]


def column_distribution(key: str, top_n: int | None) -> tuple[list[tuple[str, int]], int]:
    """Return ``(data, total_models)`` for the Column Distribution dialog.

    *data* is a list of ``(label, count)`` pairs sorted by count descending,
    capped at *top_n* entries; an unknown *key* gives no data.
    """
    lib = native_db.library()
    raw, total = lib.stats_column(key, top_n)
    if key in _DIRECT:
        null_label = _DIRECT[key][1]
        data = [(str(v) if v is not None else null_label, cnt) for v, cnt in raw]
        if key == "release_month":
            data = [(MONTH_NAMES.get(int(v), v) if v.isdigit() else v, cnt) for v, cnt in data]
        return data, total
    return [tuple(r) for r in raw], total


