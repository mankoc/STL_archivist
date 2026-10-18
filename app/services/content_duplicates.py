"""
Find Duplicate Archives (content) and folder lists — Qt-free.

The STL file-list overlap scan, the thumbnail id lookups of the result popups,
the crawled-folder list and the sidecar dialogs' folder list used to query
through the ORM in their dialogs.  Each function here runs natively
(``stl_native.LibraryDb``, ``rust/stl-core/src/storage/duplicates.rs``) when
:func:`app.database.native_db.library` allows it, and in Python (the dialogs'
former code, moved) otherwise; ``tests/golden/test_content_duplicates_golden.py``
compares the two.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable

from app.database import native_db
from app.database.db import session_scope
from app.database.models import CrawledFolder, Image, STLFile, STLModel


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
    if lib is not None:
        import stl_native
        log("Comparing STL file lists…")
        try:
            pairs = lib.dup_stl_content_pairs(float(threshold), should_stop=should_stop)
        except stl_native.Stopped:
            raise Stopped from None
    else:
        pairs = _stl_pairs_py(threshold, log, should_stop)
    return [{
        "id_a": a, "title_a": ta_ or Path(pa).stem, "path_a": pa or "", "total_a": na,
        "id_b": b, "title_b": tb_ or Path(pb).stem, "path_b": pb or "", "total_b": nb,
        "overlap": n, "sim": sim,
    } for a, b, n, na, nb, sim, ta_, pa, tb_, pb in pairs]


def _stl_pairs_py(threshold, log, should_stop) -> list[tuple]:
    def check():
        if should_stop():
            raise Stopped

    with session_scope() as session:
        log("Loading STL file records…")
        rows = (session.query(STLFile.model_id, STLFile.filename, STLFile.file_size)
                .join(STLModel, STLModel.id == STLFile.model_id)
                .filter(STLFile.file_size.isnot(None))
                .filter(STLModel.is_missing.isnot(True))
                .all())
        if not rows:
            return []
        model_files: dict[int, set[tuple]] = defaultdict(set)
        for model_id, filename, file_size in rows:
            model_files[model_id].add((filename, file_size))
        check()

        log(f"Building index over {len(model_files)} archive(s)…")
        file_to_models: dict[tuple, set[int]] = defaultdict(set)
        for model_id, files in model_files.items():
            for key in files:
                file_to_models[key].add(model_id)

        log("Computing pair overlaps…")
        pair_overlap: dict[tuple[int, int], int] = defaultdict(int)
        for models in file_to_models.values():
            if len(models) < 2:
                continue
            check()
            ms = sorted(models)
            for i in range(len(ms)):
                for j in range(i + 1, len(ms)):
                    pair_overlap[(ms[i], ms[j])] += 1

        log("Filtering by threshold…")
        candidates = []
        for (id_a, id_b), overlap in pair_overlap.items():
            ta, tb = len(model_files[id_a]), len(model_files[id_b])
            sim = overlap / max(ta, tb)
            if sim >= threshold:
                candidates.append((id_a, id_b, overlap, ta, tb, sim))
        if not candidates:
            return []
        candidates.sort(key=lambda x: (-x[5], x[0], x[1]))

        log("Loading model titles…")
        ids = {c[0] for c in candidates} | {c[1] for c in candidates}
        info = {mid: (t, p) for mid, t, p in session.query(STLModel.id, STLModel.title, STLModel.archive_path)
                .filter(STLModel.id.in_(list(ids)))}
    return [c + info[c[0]] + info[c[1]] for c in candidates if c[0] in info and c[1] in info]


def thumbnail_image_ids(model_id: int, limit: int) -> list[int]:
    """Ids of a model's thumbnailed images, by id, at most *limit*."""
    lib = native_db.library()
    if lib is not None:
        return lib.dup_thumbnail_image_ids(int(model_id), int(limit))
    with session_scope() as s:
        return [i for (i,) in s.query(Image.id)
                .filter(Image.model_id == model_id, Image.thumbnail_path.isnot(None))
                .order_by(Image.id).limit(limit)]


def present_images() -> list[tuple]:
    """``(image id, model id, dhash)`` of thumbnailed images of present models, by image id."""
    lib = native_db.library()
    if lib is not None:
        return lib.dup_present_images()
    with session_scope() as s:
        return [tuple(r) for r in s.connection().exec_driver_sql(
            "SELECT i.id, i.model_id, i.dhash FROM images i JOIN models m ON m.id = i.model_id "
            "WHERE i.thumbnail_path IS NOT NULL AND m.is_missing IS NOT 1 ORDER BY i.id")]


def model_info() -> list[tuple]:
    """``(id, title, archive_path, archive_size)`` of every model."""
    lib = native_db.library()
    if lib is not None:
        return lib.dup_model_info()
    with session_scope() as s:
        return [tuple(r) for r in s.connection().exec_driver_sql(
            "SELECT id, title, archive_path, archive_size FROM models")]


def crawled_folders() -> list[tuple[str, str | None]]:
    """``(path, pattern)`` of the crawled folders, by path."""
    lib = native_db.library()
    if lib is not None:
        return lib.dup_crawled_folders()
    with session_scope() as s:
        return [tuple(r) for r in s.query(CrawledFolder.path, CrawledFolder.pattern).order_by(CrawledFolder.path)]


def remove_crawled_folder(path: str) -> None:
    """Stop tracking a crawled folder (its models stay)."""
    lib = native_db.library()
    if lib is not None:
        lib.dup_remove_crawled_folder(path)
        return
    with session_scope() as s:
        record = s.query(CrawledFolder).filter_by(path=path).first()
        if record:
            s.delete(record)


def sidecar_folders() -> list[Path]:
    """The parent folder of every archive in the database, sorted."""
    lib = native_db.library()
    if lib is not None:
        paths = lib.dup_all_archive_paths()
    else:
        with session_scope() as s:
            paths = [p for (p,) in s.query(STLModel.archive_path) if p]
    return sorted({Path(p).parent for p in paths})
