"""
Lookup-table administration for the editor dialogs — Qt-free.

The tag, category, collection, subcollection and scale editors, the author
(creator name) editor and the creator profile dialog call these functions,
which run natively (``stl_native.LibraryDb``, ``rust/stl-core/src/storage/admin.rs``).

*kind* is one of :data:`KINDS`.  Each write runs in its own transaction.
"""
from __future__ import annotations


from app.database import native_db
KINDS = ("tag", "category", "collection", "subcollection", "scale")


def _cls(kind: str) -> str:
    """Check *kind* (the native calls take the kind name)."""
    if kind not in KINDS:
        raise ValueError(f"unknown lookup kind: {kind!r}")
    return kind


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
    return lib.admin_list(kind)


def tags_for_merge_scan() -> list[tuple[int, str, int]]:
    """``(id, name, models)`` for every tag, by id (the merge-similar scan's input)."""
    lib = native_db.library()
    return lib.admin_tags_for_scan()


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
    return lib.admin_add(kind, name, collection_name)


def name_of(kind: str, lookup_id: int) -> "tuple[str, int | None] | None":
    """``(name, collection_id)`` of a row (``collection_id`` only for subcollections)."""
    _cls(kind)
    lib = native_db.library()
    return lib.admin_name_of(kind, int(lookup_id))


def rename(kind: str, lookup_id: int, new_name: str) -> bool:
    """Rename a row; ``False`` when *new_name* is taken (within the same
    collection, for a subcollection)."""
    _cls(kind)
    lib = native_db.library()
    return lib.admin_rename(kind, int(lookup_id), new_name)


def delete_info(kind: str, lookup_id: int) -> "tuple[str, int, int] | None":
    """``(name, models using it, subcollections)`` for the delete confirmation."""
    _cls(kind)
    lib = native_db.library()
    return lib.admin_delete_info(kind, int(lookup_id))


def delete(kind: str, lookup_id: int) -> None:
    """Delete a row.  Models referring to it lose the value; a collection takes
    its subcollections with it; a tag or scale is unlinked from its models."""
    _cls(kind)
    lib = native_db.library()
    lib.admin_delete(kind, int(lookup_id))
    return


def merge(kind: str, keep_id: int, delete_id: int) -> None:
    """Move the models of *delete_id* to *keep_id* and delete *delete_id*.

    For collections, the moved models lose their subcollection (it belonged to
    the deleted collection).  Categories, collections and subcollections only.
    """
    if kind not in ("category", "collection", "subcollection"):
        raise ValueError(f"merge is not offered for {kind!r}")
    lib = native_db.library()
    lib.admin_merge(kind, int(keep_id), int(delete_id))
    return


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

def unused_tag_count() -> int:
    lib = native_db.library()
    return lib.admin_unused_tag_count()


def delete_unused_tags() -> int:
    """Delete every tag no model uses; returns how many."""
    lib = native_db.library()
    return lib.admin_delete_unused_tags()


def merge_tags(tag_ids: list[int], target: str) -> None:
    """Merge *tag_ids* into the tag named *target* (matched case-insensitively,
    created if missing): their models are re-tagged and the old tags deleted."""
    lib = native_db.library()
    lib.admin_merge_tags([int(t) for t in tag_ids], target)
    return


# ---------------------------------------------------------------------------
# Authors (STLModel.creator) and creator profiles
# ---------------------------------------------------------------------------

def author_counts() -> list[tuple[str, int]]:
    """``(creator name, models)`` for every creator, by name."""
    lib = native_db.library()
    return lib.admin_author_counts()


def author_model_count(name: str) -> int:
    lib = native_db.library()
    return lib.admin_author_model_count(name)


def rename_author(old_name: str, new_name: str) -> None:
    """Give every model of *old_name* the creator *new_name* (merging when it
    exists) and rename or merge the creator profile."""
    lib = native_db.library()
    lib.admin_rename_author(old_name, new_name)
    return


def delete_author(name: str) -> None:
    """Clear the creator of every model of *name* and delete its profile."""
    lib = native_db.library()
    lib.admin_delete_author(name)
    return


def merge_authors(keep_name: str, delete_name: str) -> None:
    """Move the models of *delete_name* to *keep_name*; drop its profile."""
    lib = native_db.library()
    lib.admin_merge_authors(keep_name, delete_name)
    return


PROFILE_FIELDS = ("full_name", "patreon_url", "artstation_url", "gumroad_url", "myminifactory_url")


def creator_profile(name: str) -> "dict | None":
    """The creator's profile fields, or ``None`` when there is no profile."""
    lib = native_db.library()
    return lib.admin_creator_profile(name)


def save_creator_profile(name: str, fields: dict) -> None:
    """Create or update the profile; blank values are stored as NULL."""
    values = {f: ((fields.get(f) or "").strip() or None) for f in PROFILE_FIELDS}
    lib = native_db.library()
    lib.admin_save_creator_profile(name, values)
    return
