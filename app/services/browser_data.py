"""
Browser panel, lookup lists and sort-dialog data — Qt-free (native:
``rust/stl-core/src/storage/browser.rs``).
"""
from __future__ import annotations

from typing import Callable, Optional

from app.database import native_db

_CREATOR_FIELDS = ("full_name", "patreon_url", "artstation_url", "gumroad_url", "myminifactory_url")


def lookup_names(kind: str) -> list[str]:
    """Sorted names of a lookup kind; ``source`` gives the distinct non-empty model sources."""
    lib = native_db.library()
    return lib.browser_lookup_names(kind)


def tag_names() -> list[str]:
    """All tag names, sorted (the tag editor delegate's completions)."""
    lib = native_db.library()
    return lib.browser_tag_names()


def model_panel(model_id: int, creator: Optional[str]) -> dict:
    """The info panel's extra fields: url, description and the creator profile (or None)."""
    lib = native_db.library()
    m, c = lib.browser_model_panel(int(model_id), creator or None)
    return {
        "url": (m[0] or "") if m else "",
        "description": (m[1] or "") if m else "",
        "creator": {f: v or "" for f, v in zip(_CREATOR_FIELDS, c)} if c else None,
    }


def model_images(model_id: int) -> list[tuple[int, Optional[str]]]:
    """``(image id, thumbnail_path)`` of a model's images, in id order."""
    lib = native_db.library()
    return lib.browser_model_images(int(model_id))


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
    ids = lib.browser_add_images(int(model_id), names)
    done, failed_ids, failed = [], [], []
    for image_id, (name, jpeg) in zip(ids, encoded):
        path = write(image_id, jpeg)
        if path is None:
            failed_ids.append(image_id)
            failed.append(name)
        else:
            done.append((image_id, path))
    lib.browser_set_image_thumbnails(done)
    if failed_ids:
        lib.delete_images(failed_ids)
    return failed


def delete_image(image_id: int) -> bool:
    """Delete an image record; ``False`` if it did not exist.  The caller
    deletes the thumbnail afterwards."""
    lib = native_db.library()
    return lib.delete_images([int(image_id)]) > 0


def add_tag(name: str) -> bool:
    """Create the tag *name* unless it exists; returns whether it was created."""
    lib = native_db.library()
    return lib.browser_add_tag(name)


def delete_models(model_ids: list[int]) -> list[str]:
    """Delete models (``moves.delete_model``) in one transaction; returns the
    thumbnail references for the caller to delete after commit."""
    lib = native_db.library()
    return lib.browser_delete_models([int(i) for i in model_ids])


def sort_sources(model_ids: list[int]) -> dict[int, dict]:
    """``{id: fields}`` for the sort dialog: ``archive_path`` and the fields
    :func:`app.utils.path_pattern.render_path_pattern` uses (lookups by name).
    Missing ids are left out."""
    lib = native_db.library()
    return lib.browser_sort_sources([int(i) for i in model_ids])


def set_archive_paths(items: list[tuple[int, str]]) -> None:
    """Point models at their moved archives, in one transaction (``archive_path``
    and ``archive_folder``, as ``moves.archive_folder_for`` gives it)."""
    lib = native_db.library()
    lib.browser_set_archive_paths([(int(i), str(p)) for i, p in items])
    return
