"""
Find Duplicate Archives (content) and folder lists — Qt-free.

The STL file-list overlap scan, the thumbnail id lookups of the result popups,
the crawled-folder list and the sidecar dialogs' folder list, run natively
(``stl_native.LibraryDb``, ``rust/stl-core/src/storage/duplicates.rs``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.database import native_db


class Stopped(Exception):
    """Raised by :func:`find_stl_duplicates` when *should_stop* returns True."""


def _never() -> bool:
    return False


def _noop(*_a) -> None:
    pass


def find_stl_duplicates(
    threshold: float,
    log: Callable[[str], None] = _noop,
    should_stop: Callable[[], bool] = _never,
) -> list[dict]:
    """Archive pairs whose STL file lists overlap: shared ``(file name, size)``
    entries over the larger list, at least *threshold*.  Most similar first,
    ties by ids.  Raises :class:`Stopped` when *should_stop* asks."""
    lib = native_db.library()
    import stl_native
    log("Comparing STL file lists…")
    try:
        pairs = lib.dup_stl_content_pairs(float(threshold), should_stop=should_stop)
    except stl_native.Stopped:
        raise Stopped from None
    return [{
        "id_a": a, "title_a": ta_ or Path(pa).stem, "path_a": pa or "", "total_a": na,
        "id_b": b, "title_b": tb_ or Path(pb).stem, "path_b": pb or "", "total_b": nb,
        "overlap": n, "sim": sim,
    } for a, b, n, na, nb, sim, ta_, pa, tb_, pb in pairs]


def thumbnail_image_ids(model_id: int, limit: int) -> list[int]:
    """Ids of a model's thumbnailed images, by id, at most *limit*."""
    lib = native_db.library()
    return lib.dup_thumbnail_image_ids(int(model_id), int(limit))


def present_images() -> list[tuple]:
    """``(image id, model id, dhash)`` of thumbnailed images of present models, by image id."""
    lib = native_db.library()
    return lib.dup_present_images()


def model_info() -> list[tuple]:
    """``(id, title, archive_path, archive_size)`` of every model."""
    lib = native_db.library()
    return lib.dup_model_info()


def crawled_folders() -> list[tuple[str, str | None]]:
    """``(path, pattern)`` of the crawled folders, by path."""
    lib = native_db.library()
    return lib.dup_crawled_folders()


def add_crawled_folder(path: str) -> bool:
    """Track a folder for crawling unless it is tracked; returns whether it was added."""
    lib = native_db.library()
    return lib.dup_add_crawled_folder(path)


def remove_crawled_folder(path: str) -> None:
    """Stop tracking a crawled folder (its models stay)."""
    lib = native_db.library()
    lib.dup_remove_crawled_folder(path)
    return


def sidecar_folders() -> list[Path]:
    """The parent folder of every archive in the database, sorted."""
    lib = native_db.library()
    paths = lib.dup_all_archive_paths()
    return sorted({Path(p).parent for p in paths})
