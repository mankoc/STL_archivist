"""
Read-only model views for the viewer dialogs — Qt-free.

Archive contents (file list / compare), the archive browser, the image grid,
Find Duplicate Archives (metadata) and Find by Image used to query through
the ORM themselves.  Each function here runs natively (``stl_native.LibraryDb``,
``rust/stl-core/src/storage/views.rs``) when
:func:`app.database.native_db.library` allows it, and in Python (the dialogs'
former queries, moved) otherwise; ``tests/golden/test_views_golden.py``
compares the two.  Lists the ORM loaded in unspecified order are in id order
(images, tags, scales, models) or file-name order (STL files).
"""
from __future__ import annotations

from typing import NamedTuple, Optional

from sqlalchemy import or_

from app.database import native_db
from app.database.db import session_scope
from app.database.models import Image, STLFile, STLModel

#: Lookup fields of :func:`duplicate_rows` (``scales`` is a list).
DUPLICATE_FIELDS = ("title", "creator", "source", "sculptor", "collection", "subcollection",
                    "category", "release", "scales")


class StlFileRow(NamedTuple):
    filename: str
    file_size: Optional[int]
    archive_hash: Optional[str]


def stl_files(model_id: int) -> list[StlFileRow]:
    """A model's STL file entries, by file name."""
    lib = native_db.library()
    if lib is not None:
        return [StlFileRow(*r) for r in lib.views_stl_files(int(model_id))]
    with session_scope() as s:
        return [StlFileRow(*r) for r in s.query(STLFile.filename, STLFile.file_size, STLFile.archive_hash)
                .filter(STLFile.model_id == model_id).order_by(STLFile.filename)]


def model_label(model_id: int) -> Optional[str]:
    """``title or archive_path`` of a model, or ``None`` when it does not exist."""
    lib = native_db.library()
    if lib is not None:
        row = lib.views_title_path(int(model_id))
    else:
        with session_scope() as s:
            row = s.query(STLModel.title, STLModel.archive_path).filter(STLModel.id == model_id).first()
    return (row[0] or row[1]) if row else None


def archive_folders() -> list[str]:
    """Distinct non-empty ``archive_folder`` values, sorted."""
    lib = native_db.library()
    if lib is not None:
        return lib.views_archive_folders()
    with session_scope() as s:
        rows = (s.query(STLModel.archive_folder).distinct()
                .filter(STLModel.archive_folder.isnot(None)).order_by(STLModel.archive_folder))
        return [f for (f,) in rows if f]


def _thumbs(s, ids: list[int]) -> dict[int, list[Optional[str]]]:
    out: dict[int, list[Optional[str]]] = {i: [] for i in ids}
    if ids:
        for mid, path in (s.query(Image.model_id, Image.thumbnail_path)
                          .filter(Image.model_id.in_(ids)).order_by(Image.id)):
            out[mid].append(path)
    return out


def folder_models(folder: str, include_subfolders: bool) -> list[dict]:
    """Archive browser cells: ``id, title, creator, archive_path, thumbs`` for the
    models in *folder* (and its subfolders), by archive path."""
    lib = native_db.library()
    if lib is not None:
        rows = lib.views_folder_models(folder, include_subfolders)
    else:
        with session_scope() as s:
            if include_subfolders:
                prefix = folder.replace("\\", "/")
                if not prefix.endswith("/"):
                    prefix += "/"
                cond = or_(STLModel.archive_folder == folder, STLModel.archive_folder.like(prefix + "%"))
            else:
                cond = STLModel.archive_folder == folder
            base = [tuple(r) for r in s.query(STLModel.id, STLModel.title, STLModel.creator,
                                              STLModel.archive_path)
                    .filter(cond).order_by(STLModel.archive_path, STLModel.id)]
            thumbs = _thumbs(s, [r[0] for r in base])
            rows = [r + (thumbs[r[0]],) for r in base]
    return [dict(zip(("id", "title", "creator", "archive_path", "thumbs"), r)) for r in rows]


_GRID_FIELDS = ("title", "creator", "source", "release_year", "release_month", "archive_path",
                "presupports", "warning", "description", "url")


def grid_models(model_ids: list[int]) -> dict[int, dict]:
    """Image grid cells: ``{id: fields}`` with ``tags``, ``scales`` (id order) and
    ``thumbs``; missing ids are left out."""
    lib = native_db.library()
    if lib is not None:
        return lib.views_grid_models([int(i) for i in model_ids])
    out: dict[int, dict] = {}
    with session_scope() as s:
        models = s.query(STLModel).filter(STLModel.id.in_(list(model_ids))).order_by(STLModel.id).all()
        thumbs = _thumbs(s, [m.id for m in models])
        for m in models:
            d = {f: getattr(m, f) for f in _GRID_FIELDS}
            d["tags"] = [t.name for t in sorted(m.tags, key=lambda t: t.id)]
            d["scales"] = [x.name for x in sorted(m.scales, key=lambda x: x.id)]
            d["thumbs"] = thumbs[m.id]
            out[m.id] = d
    return out


def duplicate_rows() -> list[dict]:
    """Every model's metadata for Find Duplicate Archives, by id: ``id`` plus
    :data:`DUPLICATE_FIELDS` (lookups by name, ``scales`` a list of names)."""
    lib = native_db.library()
    if lib is not None:
        return lib.views_duplicate_rows()
    from sqlalchemy.orm import joinedload, selectinload
    out = []
    with session_scope() as s:
        for m in (s.query(STLModel).options(
                joinedload(STLModel.sculptor), joinedload(STLModel.collection),
                joinedload(STLModel.subcollection), joinedload(STLModel.category),
                joinedload(STLModel.release), selectinload(STLModel.scales)).order_by(STLModel.id)):
            out.append({
                "id": m.id, "title": m.title, "creator": m.creator, "source": m.source,
                "sculptor": m.sculptor.name if m.sculptor else None,
                "collection": m.collection.name if m.collection else None,
                "subcollection": m.subcollection.name if m.subcollection else None,
                "category": m.category.name if m.category else None,
                "release": m.release.name if m.release else None,
                "scales": [x.name for x in sorted(m.scales, key=lambda x: x.id)],
            })
    return out


def image_hash_rows() -> list[tuple]:
    """``(model_id, dhash, thumbnail_path, title, archive_path)`` for every image
    with a stored dHash, by image id (Find by Image, hash method)."""
    lib = native_db.library()
    if lib is not None:
        return lib.views_image_hash_rows()
    with session_scope() as s:
        return [tuple(r) for r in s.query(Image.model_id, Image.dhash, Image.thumbnail_path,
                                          STLModel.title, STLModel.archive_path)
                .join(STLModel, Image.model_id == STLModel.id)
                .filter(Image.dhash.isnot(None)).order_by(Image.id)]
