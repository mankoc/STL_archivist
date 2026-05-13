"""
Get-or-create helpers for FK-linked entities.

Several modules (``crawler.py``, ``folder_metadata.py``, ``path_pattern.py``)
all need the same "look up an entity by name, create it if missing, flush
the session" pattern.  Centralising it here:

- removes ~150 lines of copy-pasted code,
- guarantees consistent ``session.flush()`` semantics so the new row's PK
  is available before the caller assigns it to a parent record,
- gives type-checked wrappers for the specific entities the crawler uses.

Tags are normalised (stripped + lowercased) before lookup; everything else
preserves the original casing of *name*.
"""
from __future__ import annotations

from typing import TypeVar

from sqlalchemy.orm import Session

from app.database.models import (
    Category,
    Collection,
    Release,
    Scale,
    Sculptor,
    STLModel,
    Subcollection,
    Tag,
)

T = TypeVar("T")


def get_or_create(session: Session, model_cls: type[T], **filters) -> T:
    """Return an existing row matching *filters*, or create and flush a new one.

    The new row is added to *session* and ``session.flush()`` is called so
    that the auto-incremented primary key is populated before this function
    returns.  Caller is still responsible for ``session.commit()``.
    """
    instance = session.query(model_cls).filter_by(**filters).first()
    if instance is not None:
        return instance
    instance = model_cls(**filters)
    session.add(instance)
    session.flush()
    return instance


def get_or_create_tag(session: Session, name: str) -> Tag:
    """Look up or create a ``Tag`` row.  Names are stripped and lowercased."""
    return get_or_create(session, Tag, name=name.strip().lower())


def get_or_create_sculptor(session: Session, name: str) -> Sculptor:
    """Look up or create a ``Sculptor`` row by exact-case name."""
    return get_or_create(session, Sculptor, name=name)


def get_or_create_collection(session: Session, name: str) -> Collection:
    """Look up or create a ``Collection`` row by exact-case name."""
    return get_or_create(session, Collection, name=name)


def get_or_create_category(session: Session, name: str) -> Category:
    """Look up or create a ``Category`` row by exact-case name."""
    return get_or_create(session, Category, name=name)


def get_or_create_release(session: Session, name: str) -> Release:
    """Look up or create a ``Release`` row by exact-case name."""
    return get_or_create(session, Release, name=name)


def get_or_create_scale(session: Session, name: str) -> Scale:
    """Look up or create a ``Scale`` row by exact-case name."""
    return get_or_create(session, Scale, name=name)


def prune_unused_lookups(session: Session) -> dict[str, int]:
    """Delete lookup rows no longer referenced by any model.

    Order matters: subcollections are pruned before collections so that a
    collection whose only remaining children were orphan subcollections is also
    removed in the same call.

    Returns a mapping of ``{table_label: rows_deleted}`` for non-zero counts.
    """
    from sqlalchemy import select

    deleted: dict[str, int] = {}

    # 1. Orphan subcollections
    used_sub = select(STLModel.subcollection_id).where(
        STLModel.subcollection_id.isnot(None)
    ).scalar_subquery()
    orphan_subs = session.query(Subcollection).filter(
        Subcollection.id.not_in(used_sub)
    ).all()
    for row in orphan_subs:
        session.delete(row)
    if orphan_subs:
        deleted["subcollections"] = len(orphan_subs)
        session.flush()

    # 2. Orphan collections (no model reference AND no subcollections left)
    used_col = select(STLModel.collection_id).where(
        STLModel.collection_id.isnot(None)
    ).scalar_subquery()
    has_sub = select(Subcollection.collection_id).scalar_subquery()
    orphan_cols = session.query(Collection).filter(
        Collection.id.not_in(used_col),
        Collection.id.not_in(has_sub),
    ).all()
    for row in orphan_cols:
        session.delete(row)
    if orphan_cols:
        deleted["collections"] = len(orphan_cols)
        session.flush()

    # 3. Other simple FK lookup tables
    for label, cls, fk_attr in [
        ("sculptors",  Sculptor, STLModel.sculptor_id),
        ("categories", Category, STLModel.category_id),
        ("releases",   Release,  STLModel.release_id),
        ("scales",     Scale,    STLModel.scale_id),
    ]:
        used = select(fk_attr).where(fk_attr.isnot(None)).scalar_subquery()
        orphans = session.query(cls).filter(cls.id.not_in(used)).all()
        for row in orphans:
            session.delete(row)
        if orphans:
            deleted[label] = len(orphans)
            session.flush()

    return deleted


def get_or_create_subcollection(
    session: Session,
    name: str,
    collection_id: int,
) -> Subcollection:
    """Look up or create a ``Subcollection`` row scoped to *collection_id*.

    Subcollection names are unique per parent collection, so the lookup must
    include both the name *and* the collection ID.
    """
    return get_or_create(
        session,
        Subcollection,
        name=name,
        collection_id=collection_id,
    )
