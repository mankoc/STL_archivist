"""
Lookup-table administration for the editor dialogs — Qt-free.

The tag, category, collection, subcollection and scale editors, the author
(creator name) editor and the creator profile dialog used to run their own ORM
queries.  They now call these functions, which run natively
(``stl_native.LibraryDb``, ``rust/stl-core/src/storage/admin.rs``) when
:func:`app.database.native_db.library` allows it and in Python otherwise;
``tests/golden/test_admin_golden.py`` compares the two.

*kind* is one of :data:`KINDS`.  Each write runs in its own transaction.
"""
from __future__ import annotations

from sqlalchemy import func

from app.database import native_db
from app.database.db import session_scope
from app.database.models import (
    Category, Collection, Creator, Scale, STLModel, Subcollection, Tag, model_tags,
)

KINDS = ("tag", "category", "collection", "subcollection", "scale")
_CLS = {"tag": Tag, "category": Category, "collection": Collection,
        "subcollection": Subcollection, "scale": Scale}


def _cls(kind: str):
    try:
        return _CLS[kind]
    except KeyError:
        raise ValueError(f"unknown lookup kind: {kind!r}") from None


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_lookups(kind: str) -> list[tuple]:
    """Rows for an editor table, by name: ``(id, name, models)``.

    Subcollections: ``(id, name, models, collection_id, collection_name)``,
    ordered by collection name, then name.
    """
    _cls(kind)
    lib = native_db.library()
    if lib is not None:
        return lib.admin_list(kind)
    from app.services.lookups import usage_counts
    with session_scope() as s:
        counts = usage_counts(s, kind)
        if kind == "subcollection":
            q = (s.query(Subcollection.id, Subcollection.name, Collection.id, Collection.name)
                 .join(Collection, Subcollection.collection_id == Collection.id)
                 .order_by(Collection.name, Subcollection.name))
            return [(sid, name, counts.get(sid, 0), cid, cname) for sid, name, cid, cname in q]
        cls = _CLS[kind]
        return [(i, name, counts.get(i, 0)) for i, name in s.query(cls.id, cls.name).order_by(cls.name)]


def tags_for_merge_scan() -> list[tuple[int, str, int]]:
    """``(id, name, models)`` for every tag, by id (the merge-similar scan's input)."""
    lib = native_db.library()
    if lib is not None:
        return lib.admin_tags_for_scan()
    with session_scope() as s:
        return [tuple(r) for r in (
            s.query(Tag.id, Tag.name, func.count(model_tags.c.model_id))
            .outerjoin(model_tags, model_tags.c.tag_id == Tag.id)
            .group_by(Tag.id, Tag.name)
            .order_by(Tag.id)
        )]


def collection_names() -> list[str]:
    """All collection names, sorted."""
    return [name for _i, name, _n in list_lookups("collection")]


# ---------------------------------------------------------------------------
# Add / rename / delete
# ---------------------------------------------------------------------------

def add(kind: str, name: str, collection_name: str | None = None) -> bool:
    """Create a lookup row; ``False`` when the name exists already.

    A subcollection needs *collection_name* (its parent); ``False`` when that
    collection does not exist either.
    """
    _cls(kind)
    lib = native_db.library()
    if lib is not None:
        return lib.admin_add(kind, name, collection_name)
    with session_scope() as s:
        if kind == "subcollection":
            col = s.query(Collection).filter_by(name=collection_name).order_by(Collection.id).first()
            if col is None:
                return False
            if s.query(Subcollection).filter_by(name=name, collection_id=col.id).first():
                return False
            s.add(Subcollection(name=name, collection_id=col.id))
            return True
        cls = _CLS[kind]
        if s.query(cls).filter_by(name=name).first():
            return False
        s.add(cls(name=name))
        return True


def name_of(kind: str, lookup_id: int) -> "tuple[str, int | None] | None":
    """``(name, collection_id)`` of a row (``collection_id`` only for subcollections)."""
    _cls(kind)
    lib = native_db.library()
    if lib is not None:
        return lib.admin_name_of(kind, int(lookup_id))
    with session_scope() as s:
        row = s.get(_CLS[kind], lookup_id)
        if row is None:
            return None
        return row.name, (row.collection_id if kind == "subcollection" else None)


def rename(kind: str, lookup_id: int, new_name: str) -> bool:
    """Rename a row; ``False`` when *new_name* is taken (within the same
    collection, for a subcollection)."""
    _cls(kind)
    lib = native_db.library()
    if lib is not None:
        return lib.admin_rename(kind, int(lookup_id), new_name)
    with session_scope() as s:
        cls = _CLS[kind]
        row = s.get(cls, lookup_id)
        if row is None:
            return False
        taken = (s.query(cls).filter_by(name=new_name, collection_id=row.collection_id).first()
                 if kind == "subcollection" else s.query(cls).filter_by(name=new_name).first())
        if taken:
            return False
        row.name = new_name
        return True


def delete_info(kind: str, lookup_id: int) -> "tuple[str, int, int] | None":
    """``(name, models using it, subcollections)`` for the delete confirmation."""
    _cls(kind)
    lib = native_db.library()
    if lib is not None:
        return lib.admin_delete_info(kind, int(lookup_id))
    from app.services.lookups import usage_count
    with session_scope() as s:
        row = s.get(_CLS[kind], lookup_id)
        if row is None:
            return None
        subs = len(row.subcollections) if kind == "collection" else 0
        return row.name, usage_count(s, kind, lookup_id), subs


def delete(kind: str, lookup_id: int) -> None:
    """Delete a row.  Models referring to it lose the value; a collection takes
    its subcollections with it; a tag or scale is unlinked from its models."""
    _cls(kind)
    lib = native_db.library()
    if lib is not None:
        lib.admin_delete(kind, int(lookup_id))
        return
    with session_scope() as s:
        row = s.get(_CLS[kind], lookup_id)
        if row is not None:
            s.delete(row)


def merge(kind: str, keep_id: int, delete_id: int) -> None:
    """Move the models of *delete_id* to *keep_id* and delete *delete_id*.

    For collections, the moved models lose their subcollection (it belonged to
    the deleted collection).  Categories, collections and subcollections only.
    """
    if kind not in ("category", "collection", "subcollection"):
        raise ValueError(f"merge is not offered for {kind!r}")
    lib = native_db.library()
    if lib is not None:
        lib.admin_merge(kind, int(keep_id), int(delete_id))
        return
    attr = {"category": "category", "collection": "collection", "subcollection": "subcollection"}[kind]
    with session_scope() as s:
        cls = _CLS[kind]
        keep, gone = s.get(cls, keep_id), s.get(cls, delete_id)
        for model in list(gone.models):
            setattr(model, attr, keep)
            if kind == "collection":
                model.subcollection = None
        s.delete(gone)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def unused_tag_count() -> int:
    lib = native_db.library()
    if lib is not None:
        return lib.admin_unused_tag_count()
    with session_scope() as s:
        return s.query(Tag).filter(~Tag.models.any()).count()


def delete_unused_tags() -> int:
    """Delete every tag no model uses; returns how many."""
    lib = native_db.library()
    if lib is not None:
        return lib.admin_delete_unused_tags()
    with session_scope() as s:
        return s.query(Tag).filter(~Tag.models.any()).delete(synchronize_session=False)


def merge_tags(tag_ids: list[int], target: str) -> None:
    """Merge *tag_ids* into the tag named *target* (matched case-insensitively,
    created if missing): their models are re-tagged and the old tags deleted."""
    lib = native_db.library()
    if lib is not None:
        lib.admin_merge_tags([int(t) for t in tag_ids], target)
        return
    with session_scope() as s:
        keep = (s.query(Tag).filter(func.lower(Tag.name) == target.lower())
                .order_by(Tag.id).first())
        if keep is None:
            keep = Tag(name=target)
            s.add(keep)
            s.flush()
        for tid in tag_ids:
            if tid == keep.id:
                continue
            gone = s.get(Tag, tid)
            if gone is None:
                continue
            for model in list(gone.models):
                if keep not in model.tags:
                    model.tags.append(keep)
            s.delete(gone)


# ---------------------------------------------------------------------------
# Authors (STLModel.creator) and creator profiles
# ---------------------------------------------------------------------------

def author_counts() -> list[tuple[str, int]]:
    """``(creator name, models)`` for every creator, by name."""
    lib = native_db.library()
    if lib is not None:
        return lib.admin_author_counts()
    with session_scope() as s:
        return [tuple(r) for r in (
            s.query(STLModel.creator, func.count(STLModel.id))
            .filter(STLModel.creator.isnot(None))
            .group_by(STLModel.creator)
            .order_by(STLModel.creator)
        )]


def author_model_count(name: str) -> int:
    lib = native_db.library()
    if lib is not None:
        return lib.admin_author_model_count(name)
    with session_scope() as s:
        return s.query(STLModel).filter_by(creator=name).count()


def rename_author(old_name: str, new_name: str) -> None:
    """Give every model of *old_name* the creator *new_name* (merging when it
    exists) and rename or merge the creator profile."""
    lib = native_db.library()
    if lib is not None:
        lib.admin_rename_author(old_name, new_name)
        return
    with session_scope() as s:
        s.query(STLModel).filter_by(creator=old_name).update({"creator": new_name})
        old_profile = s.query(Creator).filter_by(name=old_name).first()
        if old_profile:
            if s.query(Creator).filter_by(name=new_name).first() is None:
                old_profile.name = new_name
            else:
                s.delete(old_profile)


def delete_author(name: str) -> None:
    """Clear the creator of every model of *name* and delete its profile."""
    lib = native_db.library()
    if lib is not None:
        lib.admin_delete_author(name)
        return
    with session_scope() as s:
        s.query(STLModel).filter_by(creator=name).update({"creator": None})
        profile = s.query(Creator).filter_by(name=name).first()
        if profile:
            s.delete(profile)


def merge_authors(keep_name: str, delete_name: str) -> None:
    """Move the models of *delete_name* to *keep_name*; drop its profile."""
    lib = native_db.library()
    if lib is not None:
        lib.admin_merge_authors(keep_name, delete_name)
        return
    with session_scope() as s:
        s.query(STLModel).filter_by(creator=delete_name).update({"creator": keep_name})
        old_profile = s.query(Creator).filter_by(name=delete_name).first()
        if old_profile:
            s.delete(old_profile)


PROFILE_FIELDS = ("full_name", "patreon_url", "artstation_url", "gumroad_url", "myminifactory_url")


def creator_profile(name: str) -> "dict | None":
    """The creator's profile fields, or ``None`` when there is no profile."""
    lib = native_db.library()
    if lib is not None:
        return lib.admin_creator_profile(name)
    with session_scope() as s:
        c = s.query(Creator).filter_by(name=name).first()
        return {f: getattr(c, f) for f in PROFILE_FIELDS} if c else None


def save_creator_profile(name: str, fields: dict) -> None:
    """Create or update the profile; blank values are stored as NULL."""
    values = {f: ((fields.get(f) or "").strip() or None) for f in PROFILE_FIELDS}
    lib = native_db.library()
    if lib is not None:
        lib.admin_save_creator_profile(name, values)
        return
    with session_scope() as s:
        c = s.query(Creator).filter_by(name=name).first()
        if c is None:
            c = Creator(name=name)
            s.add(c)
        for f, v in values.items():
            setattr(c, f, v)
