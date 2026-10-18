"""
Field-level edits of a single ``STLModel``, shared by the model editor, the
browser's inline cell editing and the AI tagger.

Every lookup is resolved through :mod:`app.database.helpers` so get-or-create
semantics (tag normalisation, subcollections scoped to their collection,
flush-before-assign) are defined in exactly one place.  Nothing here commits;
callers own the session / transaction.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.database.helpers import (
    get_or_create_category,
    get_or_create_collection,
    get_or_create_release,
    get_or_create_scale,
    get_or_create_sculptor,
    get_or_create_subcollection,
    get_or_create_tag,
)
from app.database.models import STLModel, Subcollection
from app.utils.path_pattern import parse_release_date


def clean(value: str | None) -> str | None:
    """Strip *value*; return ``None`` for empty / whitespace-only input."""
    return (value or "").strip() or None


def split_list(text: str | None) -> list[str]:
    """Split a comma-separated field into stripped, non-empty items."""
    return [p.strip() for p in (text or "").split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Many-to-many fields
# ---------------------------------------------------------------------------

def assign_tags(session: Session, model: STLModel, names: Iterable[str], *, replace: bool = True) -> None:
    """Set (or, with ``replace=False``, extend) the model's tags.

    Names are normalised (stripped + lowercased) and de-duplicated.
    """
    if replace:
        model.tags.clear()
    seen = {t.name.lower() for t in model.tags}
    for raw in names:
        name = raw.strip().lower()
        if not name or name in seen:
            continue
        model.tags.append(get_or_create_tag(session, name))
        seen.add(name)


def assign_scales(session: Session, model: STLModel, names: Iterable[str], *, replace: bool = True) -> None:
    """Set (or extend) the model's scales, de-duplicating case-insensitively.

    Scale rows keep the exact casing they were first created with.
    """
    if replace:
        model.scales.clear()
    seen = {s.name.lower() for s in model.scales}
    for raw in names:
        name = raw.strip()
        if not name or name.lower() in seen:
            continue
        model.scales.append(get_or_create_scale(session, name))
        seen.add(name.lower())


# ---------------------------------------------------------------------------
# Many-to-one lookups (an empty name clears the field)
# ---------------------------------------------------------------------------

def set_sculptor(session: Session, model: STLModel, name: str | None) -> None:
    name = clean(name)
    model.sculptor = get_or_create_sculptor(session, name) if name else None


def set_category(session: Session, model: STLModel, name: str | None) -> None:
    name = clean(name)
    model.category = get_or_create_category(session, name) if name else None


def set_release(session: Session, model: STLModel, name: str | None) -> None:
    name = clean(name)
    model.release = get_or_create_release(session, name) if name else None


def set_collection(session: Session, model: STLModel, name: str | None) -> None:
    """Set the collection.  A subcollection belonging to another collection is cleared."""
    name = clean(name)
    if not name:
        model.collection = None
        model.subcollection = None
        return
    col = get_or_create_collection(session, name)
    model.collection = col
    if model.subcollection is not None and model.subcollection.collection_id != col.id:
        model.subcollection = None


def set_subcollection(session: Session, model: STLModel, name: str | None) -> None:
    """Set the subcollection, scoped to the model's collection.

    With a collection, the subcollection is looked up (or created) under it.
    Without one, an existing subcollection of that name is used and the
    model's collection is set to its parent, so the pair stays consistent;
    nothing is created because there is no parent to create it under.
    """
    name = clean(name)
    if not name:
        model.subcollection = None
        return
    if model.collection_id is not None or model.collection is not None:
        col_id = model.collection.id if model.collection is not None else model.collection_id
        model.subcollection = get_or_create_subcollection(session, name, col_id)
        return
    sub = session.query(Subcollection).filter_by(name=name).order_by(Subcollection.id).first()
    if sub is not None:
        model.collection = sub.collection
        model.subcollection = sub


# ---------------------------------------------------------------------------
# Browser inline (cell) edits
# ---------------------------------------------------------------------------

CELL_FIELDS = frozenset({
    "title", "creator", "source", "date", "tags", "sculptor", "collection",
    "subcollection", "category", "sequence", "release", "scale",
    "presupports", "to_print",
})


def apply_cell_edit(session: Session, model: STLModel, field: str, text: str | None) -> None:
    """Apply one browser-table cell edit to *model*.

    *text* is the raw cell text; empty text clears the field.  Unknown fields
    raise ``KeyError``; an unparsable sequence is ignored (as the table did).
    """
    if field not in CELL_FIELDS:
        raise KeyError(field)
    value = clean(text)
    if field == "title":
        model.title = value
    elif field == "creator":
        model.creator = value
    elif field == "source":
        model.source = value
    elif field == "date":
        model.release_year, model.release_month = parse_release_date(value or "")
    elif field == "tags":
        assign_tags(session, model, split_list(value))
    elif field == "scale":
        assign_scales(session, model, split_list(value))
    elif field == "sculptor":
        set_sculptor(session, model, value)
    elif field == "collection":
        set_collection(session, model, value)
    elif field == "subcollection":
        set_subcollection(session, model, value)
    elif field == "category":
        set_category(session, model, value)
    elif field == "release":
        set_release(session, model, value)
    elif field == "sequence":
        try:
            model.sequence = int(value) if value else None
        except ValueError:
            pass
    elif field == "presupports":
        model.presupports = value
    elif field == "to_print":
        model.to_print = value == "1"


def apply_cell_edits(edits: Iterable[tuple[int, str, str | None]]) -> list[str]:
    """Apply browser-table cell edits ``(model id, field, text)`` in one
    transaction; returns the sorted archive folders touched (for sidecars).

    Runs natively when :func:`app.database.native_db.library` allows it and the
    native port reproduces the input (``ValueError`` otherwise: nothing was
    written, and Python runs it).
    """
    from pathlib import Path

    from app.database import native_db
    from app.database.db import session_scope

    edits = list(edits)
    lib = native_db.library()
    if lib is not None:
        try:
            paths = lib.apply_cell_edits([(int(mid), field, text) for mid, field, text in edits])
            return sorted({str(Path(p).parent) for p in paths})
        except ValueError:
            pass
    folders: set[str] = set()
    with session_scope() as session:
        for model_id, field, text in edits:
            stl = session.get(STLModel, model_id)
            if stl is None:
                continue
            apply_cell_edit(session, stl, field, text)
            if stl.archive_path:
                folders.add(str(Path(stl.archive_path).parent))
    return sorted(folders)
