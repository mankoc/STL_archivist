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

from typing import Any, TypeVar

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

# Type alias for the optional per-batch lookup cache passed by hot-loop callers.
# Keys are (model_cls.__name__, frozenset(filters.items())); values are ORM rows.
LookupCache = dict[tuple[str, frozenset], Any]


def get_or_create(
    session: Session,
    model_cls: type[T],
    *,
    cache: LookupCache | None = None,
    **filters,
) -> T:
    """Return an existing row matching *filters*, or create and flush a new one.

    The new row is added to *session* and ``session.flush()`` is called so
    that the auto-incremented primary key is populated before this function
    returns.  Caller is still responsible for ``session.commit()``.

    When *cache* is provided, the result is memoised per-call-site so that
    repeated requests for the same (model_cls, filters) tuple within a batch
    skip the SELECT entirely. The cache should be scoped to a single batch
    or commit boundary — it stores live ORM instances which are only valid
    within the originating session.
    """
    cache_key: tuple[str, frozenset] | None = None
    if cache is not None:
        cache_key = (model_cls.__name__, frozenset(filters.items()))
        hit = cache.get(cache_key)
        if hit is not None:
            return hit  # type: ignore[return-value]

    instance = session.query(model_cls).filter_by(**filters).first()
    if instance is None:
        instance = model_cls(**filters)
        session.add(instance)
        session.flush()

    if cache_key is not None:
        cache[cache_key] = instance
    return instance


def get_or_create_tag(
    session: Session, name: str, *, cache: LookupCache | None = None,
) -> Tag:
    """Look up or create a ``Tag`` row.  Names are stripped and lowercased."""
    return get_or_create(session, Tag, cache=cache, name=name.strip().lower())


def get_or_create_sculptor(
    session: Session, name: str, *, cache: LookupCache | None = None,
) -> Sculptor:
    """Look up or create a ``Sculptor`` row by exact-case name."""
    return get_or_create(session, Sculptor, cache=cache, name=name)


def get_or_create_collection(
    session: Session, name: str, *, cache: LookupCache | None = None,
) -> Collection:
    """Look up or create a ``Collection`` row by exact-case name."""
    return get_or_create(session, Collection, cache=cache, name=name)


def get_or_create_category(
    session: Session, name: str, *, cache: LookupCache | None = None,
) -> Category:
    """Look up or create a ``Category`` row by exact-case name."""
    return get_or_create(session, Category, cache=cache, name=name)


def get_or_create_release(
    session: Session, name: str, *, cache: LookupCache | None = None,
) -> Release:
    """Look up or create a ``Release`` row by exact-case name."""
    return get_or_create(session, Release, cache=cache, name=name)


def get_or_create_scale(
    session: Session, name: str, *, cache: LookupCache | None = None,
) -> Scale:
    """Look up or create a ``Scale`` row by exact-case name."""
    return get_or_create(session, Scale, cache=cache, name=name)


def prune_unused_lookups(session: Session) -> dict[str, int]:
    """Delete lookup rows no longer referenced by any model.

    Order matters: subcollections are pruned before collections so that a
    collection whose only remaining children were orphan subcollections is also
    removed in the same call.

    Returns a mapping of ``{table_label: rows_deleted}`` for non-zero counts.

    Runs natively in its own transaction when *session* has not started one
    (``app.database.native_db``); otherwise inside *session*.
    """
    from sqlalchemy import select
    from app.database import native_db
    from app.database.models import model_scales

    lib = native_db.library() if not session.in_transaction() else None
    if lib is not None:
        counts = lib.prune_unused_lookups()
        order = ("subcollections", "collections", "sculptors", "categories", "releases", "scales")
        return {label: counts[label] for label in order if label in counts}

    deleted: dict[str, int] = {}

    def _bulk_delete(label: str, cls, *conditions) -> None:
        # One DELETE … WHERE id NOT IN (subquery) per table instead of loading
        # each orphan row and deleting it individually.  None of these rows
        # has ORM-level cascades to honour (orphans are referenced by nothing,
        # and orphan collections have no subcollections left by then).
        n = (session.query(cls)
             .filter(*conditions)
             .delete(synchronize_session="fetch"))
        if n:
            deleted[label] = n

    def _used(col):
        return select(col).where(col.isnot(None))

    # 1. Orphan subcollections
    _bulk_delete("subcollections", Subcollection,
                 Subcollection.id.not_in(_used(STLModel.subcollection_id)))

    # 2. Orphan collections (no model reference AND no subcollections left)
    _bulk_delete("collections", Collection,
                 Collection.id.not_in(_used(STLModel.collection_id)),
                 Collection.id.not_in(select(Subcollection.collection_id)))

    # 3. Other simple FK lookup tables
    for label, cls, fk_attr in [
        ("sculptors",  Sculptor, STLModel.sculptor_id),
        ("categories", Category, STLModel.category_id),
        ("releases",   Release,  STLModel.release_id),
    ]:
        _bulk_delete(label, cls, cls.id.not_in(_used(fk_attr)))

    # 4. Scales are many-to-many (model_scales); prune any not referenced there.
    _bulk_delete("scales", Scale, Scale.id.not_in(select(model_scales.c.scale_id)))

    session.flush()
    return deleted


def get_or_create_subcollection(
    session: Session,
    name: str,
    collection_id: int,
    *,
    cache: LookupCache | None = None,
) -> Subcollection:
    """Look up or create a ``Subcollection`` row scoped to *collection_id*.

    Subcollection names are unique per parent collection, so the lookup must
    include both the name *and* the collection ID.
    """
    return get_or_create(
        session,
        Subcollection,
        cache=cache,
        name=name,
        collection_id=collection_id,
    )
