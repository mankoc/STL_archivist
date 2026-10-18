"""
Browser panel, lookup lists and sort-dialog data — Qt-free.

The browser tab, its delegates, ``lookup_cache`` and the sort dialog used to
query through the ORM themselves.  Each function here runs natively
(``stl_native.LibraryDb``, ``rust/stl-core/src/storage/browser.rs``) when
:func:`app.database.native_db.library` allows it, and in Python (the widgets'
former code, moved) otherwise; ``tests/golden/test_browser_golden.py`` compares
the two.
"""
from __future__ import annotations

from typing import Callable, Optional

from app.database import native_db
from app.database.db import session_scope
from app.database.models import (
    Category, Collection, Creator, Image, Release, Scale, Sculptor, STLModel, Subcollection, Tag,
)
from app.services import moves

_LOOKUPS = {
    "sculptor": Sculptor, "collection": Collection, "subcollection": Subcollection,
    "category": Category, "release": Release, "scale": Scale,
}
_CREATOR_FIELDS = ("full_name", "patreon_url", "artstation_url", "gumroad_url", "myminifactory_url")


def lookup_names(kind: str) -> list[str]:
    """Sorted names of a lookup kind; ``source`` gives the distinct non-empty model sources."""
    lib = native_db.library()
    if lib is not None:
        return lib.browser_lookup_names(kind)
    with session_scope() as s:
        if kind == "source":
            return sorted(filter(None, (v for (v,) in s.query(STLModel.source).distinct())))
        cls = _LOOKUPS.get(kind)
        if cls is None:
            return []
        return sorted(n for (n,) in s.query(cls.name) if n is not None)


def tag_names() -> list[str]:
    """All tag names, sorted (the tag editor delegate's completions)."""
    lib = native_db.library()
    if lib is not None:
        return lib.browser_tag_names()
    with session_scope() as s:
        return [n for (n,) in s.query(Tag.name).order_by(Tag.name)]


def model_panel(model_id: int, creator: Optional[str]) -> dict:
    """The info panel's extra fields: url, description and the creator profile (or None)."""
    lib = native_db.library()
    if lib is not None:
        m, c = lib.browser_model_panel(int(model_id), creator or None)
    else:
        with session_scope() as s:
            m = s.query(STLModel.url, STLModel.description).filter_by(id=model_id).first()
            m = tuple(m) if m else None
            row = s.get(Creator, creator) if creator else None
            c = tuple(getattr(row, f) for f in _CREATOR_FIELDS) if row else None
    return {
        "url": (m[0] or "") if m else "",
        "description": (m[1] or "") if m else "",
        "creator": {f: v or "" for f, v in zip(_CREATOR_FIELDS, c)} if c else None,
    }


def model_images(model_id: int) -> list[tuple[int, Optional[str]]]:
    """``(image id, thumbnail_path)`` of a model's images, in id order."""
    lib = native_db.library()
    if lib is not None:
        return lib.browser_model_images(int(model_id))
    with session_scope() as s:
        return [tuple(r) for r in s.query(Image.id, Image.thumbnail_path)
                .filter(Image.model_id == model_id).order_by(Image.id)]


def add_images(
    model_id: int,
    encoded: list[tuple[str, bytes]],
    write: Callable[[int, bytes], Optional[str]],
) -> list[str]:
    """Store dropped images: insert the records, write each thumbnail with
    *write* (``thumbnailer.write_raw_thumbnail``), then record the thumbnail
    paths and drop the records whose write failed.  Returns the failed file names.
    """
    if not encoded:
        return []
    lib = native_db.library()
    names = [name for name, _ in encoded]
    if lib is not None:
        ids = lib.browser_add_images(int(model_id), names)
    else:
        with session_scope() as s:
            recs = [Image(model_id=model_id, filename=n) for n in names]
            s.add_all(recs)
            s.flush()
            ids = [r.id for r in recs]
    done, failed_ids, failed = [], [], []
    for image_id, (name, jpeg) in zip(ids, encoded):
        path = write(image_id, jpeg)
        if path is None:
            failed_ids.append(image_id)
            failed.append(name)
        else:
            done.append((image_id, path))
    if lib is not None:
        lib.browser_set_image_thumbnails(done)
        if failed_ids:
            lib.delete_images(failed_ids)
    else:
        with session_scope() as s:
            for image_id, path in done:
                s.get(Image, image_id).thumbnail_path = path
            for image_id in failed_ids:
                s.delete(s.get(Image, image_id))
    return failed


def delete_image(image_id: int) -> bool:
    """Delete an image record; ``False`` if it did not exist.  The caller
    deletes the thumbnail afterwards."""
    lib = native_db.library()
    if lib is not None:
        return lib.delete_images([int(image_id)]) > 0
    with session_scope() as s:
        image = s.get(Image, image_id)
        if image is None:
            return False
        s.delete(image)
        return True


def add_tag(name: str) -> bool:
    """Create the tag *name* unless it exists; returns whether it was created."""
    lib = native_db.library()
    if lib is not None:
        return lib.browser_add_tag(name)
    with session_scope() as s:
        if s.query(Tag.id).filter_by(name=name).first():
            return False
        s.add(Tag(name=name))
        return True


def delete_models(model_ids: list[int]) -> list[str]:
    """Delete models (``moves.delete_model``) in one transaction; returns the
    thumbnail references for the caller to delete after commit."""
    lib = native_db.library()
    if lib is not None:
        return lib.browser_delete_models([int(i) for i in model_ids])
    refs: list[str] = []
    with session_scope() as s:
        for model_id in model_ids:
            model = s.get(STLModel, model_id)
            if model is not None:
                refs += moves.delete_model(s, model)
    return refs


def sort_sources(model_ids: list[int]) -> dict[int, dict]:
    """``{id: fields}`` for the sort dialog: ``archive_path`` and the fields
    :func:`app.utils.path_pattern.render_path_pattern` uses (lookups by name).
    Missing ids are left out."""
    lib = native_db.library()
    if lib is not None:
        return lib.browser_sort_sources([int(i) for i in model_ids])
    out = {}
    with session_scope() as s:
        for m in s.query(STLModel).filter(STLModel.id.in_(model_ids)).order_by(STLModel.id):
            out[m.id] = {
                "archive_path": m.archive_path, "title": m.title, "source": m.source,
                "creator": m.creator, "release_year": m.release_year,
                "release_month": m.release_month, "url": m.url, "sequence": m.sequence,
                "sculptor": m.sculptor.name if m.sculptor else None,
                "collection": m.collection.name if m.collection else None,
                "subcollection": m.subcollection.name if m.subcollection else None,
                "release": m.release.name if m.release else None,
            }
    return out


def set_archive_paths(items: list[tuple[int, str]]) -> None:
    """Point models at their moved archives, in one transaction (``archive_path``
    and ``archive_folder``, as ``moves.archive_folder_for`` gives it)."""
    lib = native_db.library()
    if lib is not None:
        lib.browser_set_archive_paths([(int(i), str(p)) for i, p in items])
        return
    with session_scope() as s:
        for model_id, path in items:
            model = s.get(STLModel, model_id)
            if model is not None:
                model.archive_path = str(path)
                model.archive_folder = moves.archive_folder_for(str(path))
