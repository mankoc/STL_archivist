"""
Database-maintenance operations — Qt-free.

Each function takes optional ``progress(current, total)``, ``log(message)``
and ``should_stop()`` callbacks, so it can run under a ``QThread`` wrapper in
the desktop app (see ``app.ui.db_maintenance_dialog``), in a test, or in a
server job.  Result rows are plain dicts.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import func

logger = logging.getLogger(__name__)

STATUS_OK        = "OK"
STATUS_MISSING   = "Missing"
STATUS_FLAGGED   = "Flagged Missing"
STATUS_FOUND     = "Relocated"
STATUS_MODIFIED  = "Modified"
STATUS_MOVED_DB  = "Moved (in DB)"
STATUS_DUPLICATE = "Duplicate"

Progress   = Callable[[int, int], None]
Log        = Callable[[str], None]
ShouldStop = Callable[[], bool]


def _noop(*_a) -> None:
    pass


# ── Perceptual hashes ────────────────────────────────────────────────────────

DHASH_BATCH = 1024   # thumbnails read, hashed (in parallel) and written per round


def recompute_dhashes(
    recompute_all: bool = False,
    progress: Progress = _noop,
    log: Log = _noop,
    should_stop: "ShouldStop" = lambda: False,
) -> tuple[int, int]:
    """Compute and store format-2 dHashes from the stored thumbnails.

    Without *recompute_all* only images lacking a current hash are done: none
    yet, or an older Pillow hash (a different length). Each round reads a batch
    of thumbnails in one LMDB transaction (falling back to the legacy blob
    column), hashes them in parallel in the native core, and writes the batch
    in one statement. Returns ``(computed, skipped)``; images with no thumbnail
    or undecodable data count as skipped. Without the native core it logs why
    and does nothing.
    """
    from sqlalchemy import update

    from app.database.db import session_scope
    from app.database.models import Image as DBImage
    from app.utils.image_hash import DHASH_STR_LEN, compute_dhashes, dhash_available, format_dhash
    from app.utils.thumbnailer import get_thumbnail_bytes_many

    if not dhash_available():
        log("Perceptual hashes are computed by the native core, which is not available.")
        return 0, 0
    with session_scope() as session:
        query = session.query(DBImage.id)
        if not recompute_all:
            query = query.filter(DBImage.dhash.is_(None)
                                 | (func.length(DBImage.dhash) != DHASH_STR_LEN))
        ids = [i for (i,) in query.order_by(DBImage.id)]
    total = len(ids)
    if total == 0:
        log("All images already have a current dHash.")
        return 0, 0
    log(f"{'Recomputing' if recompute_all else 'Computing'} dHash for {total:,} image(s)…")

    computed = skipped = 0
    for start in range(0, total, DHASH_BATCH):
        if should_stop():
            log("Stopped by user.")
            break
        chunk = ids[start:start + DHASH_BATCH]
        thumbs = get_thumbnail_bytes_many(chunk)
        missing = [i for i in chunk if not thumbs.get(i)]
        if missing:  # very old libraries kept thumbnails in the database
            with session_scope() as session:
                thumbs.update((i, blob) for i, blob in session.query(DBImage.id, DBImage.thumbnail)
                              .filter(DBImage.id.in_(missing), DBImage.thumbnail.isnot(None)))
        have = [i for i in chunk if thumbs.get(i)]
        hashes = compute_dhashes([thumbs[i] for i in have])
        rows = [{"id": i, "dhash": format_dhash(h)} for i, h in zip(have, hashes) if h is not None]
        if rows:
            with session_scope() as session:
                session.execute(update(DBImage), rows)
        computed += len(rows)
        skipped += len(chunk) - len(rows)
        progress(min(start + DHASH_BATCH, total), total)

    log(f"Done — {computed:,} dHash(es) computed, {skipped:,} skipped "
        "(no thumbnail or decode error).")
    return computed, skipped


# ── Database compaction ──────────────────────────────────────────────────────

def database_free_bytes(session=None) -> int:
    """Bytes held by free pages in the database file (what VACUUM can reclaim).

    Reads two header values, so it is cheap enough for the GUI thread.  The
    write-ahead log is not counted; compaction also truncates it.  Without a
    *session* it reads natively, or through a session of its own.
    """
    if session is None:
        from app.database import native_db
        from app.database.db import session_scope
        lib = native_db.library()
        if lib is not None:
            return lib.maint_free_bytes()
        with session_scope() as s:
            return database_free_bytes(s)
    conn = session.connection()
    free = conn.exec_driver_sql("PRAGMA freelist_count").scalar_one()
    page = conn.exec_driver_sql("PRAGMA page_size").scalar_one()
    return free * page


def database_file_bytes(db_path: Path) -> int:
    """On-disk size of the database: the main file plus its write-ahead log."""
    total = 0
    for p in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def compact_database(engine, db_path: Path) -> tuple[int, int, bool]:
    """Rewrite the database without free pages (``VACUUM``) and shrink its WAL.

    SQLite never shrinks its file on its own: deleted rows leave free pages
    that are reused, but the file stays at its high-water mark.  ``VACUUM``
    rebuilds it compactly.  In WAL mode the rebuilt pages go through the
    write-ahead log, which ``VACUUM`` leaves as large as the database, so a
    ``wal_checkpoint(TRUNCATE)`` follows to write them back and empty it.

    Other connections may keep reading; a concurrent writer (a crawl) makes
    ``VACUUM`` fail with "database is locked" after the busy timeout.

    Returns ``(size_before, size_after, wal_truncated)`` in bytes.  The WAL
    is not truncated while another connection is still reading from it; it
    is then reset at a later checkpoint.
    """
    before = database_file_bytes(db_path)
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # The app keeps temporary data in RAM (temp_store=MEMORY); VACUUM's
        # temporary copy of the whole database belongs on disk instead.
        conn.exec_driver_sql("PRAGMA temp_store=FILE")
        try:
            conn.exec_driver_sql("VACUUM")
        finally:
            conn.exec_driver_sql("PRAGMA temp_store=MEMORY")
        busy, _log_pages, _done = conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").one()
    return before, database_file_bytes(db_path), not busy


def _never() -> bool:
    return False


class _Throttle:
    """Forward at most ~*steps* progress updates (plus the last one).

    Emitting a Qt signal per row floods the GUI event queue on a large library.
    """

    def __init__(self, progress: Progress, total: int, steps: int = 500) -> None:
        self._progress = progress
        self._total = total
        self._every = max(1, total // steps)

    def __call__(self, current: int) -> None:
        if current == self._total or current % self._every == 0:
            self._progress(current, self._total)


def _result_row(model_id, title, path, ahash, size, status, match_path="") -> dict:
    return {
        "id":         model_id,
        "title":      title or "",
        "path":       path or "",
        "hash":       ahash or "",
        "size":       size,
        "status":     status,
        "match_path": match_path,
    }


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

def verify_archives(
    progress: Progress = _noop, log: Log = _noop, should_stop: ShouldStop = _never,
) -> list[dict]:
    """Check that every catalogued archive still exists on disk, unchanged in size.

    Records previously flagged missing whose file has reappeared get the flag
    cleared (in a single UPDATE at the end).
    """
    from app.database import native_db
    from app.database.db import session_scope
    from app.database.models import STLModel

    lib = native_db.library()
    if lib is not None:
        return _verify_native(lib, progress, log, should_stop)

    with session_scope() as session:
        records = session.query(
            STLModel.id, STLModel.title, STLModel.archive_path,
            STLModel.archive_hash, STLModel.archive_size, STLModel.is_missing,
        ).order_by(STLModel.id).all()

    total = len(records)
    if total == 0:
        log("No records found in the database.")
        return []

    log(f"Verifying {total} archive(s)…")
    tick = _Throttle(progress, total)
    results: list[dict] = []
    reappeared: list[int] = []
    n_missing = n_flagged = n_modified = 0
    for idx, (model_id, title, path, ahash, stored_size, is_missing) in enumerate(records, 1):
        if should_stop():
            log("Stopped by user.")
            break
        tick(idx)
        p = Path(path) if path else None
        if is_missing and (p is None or not p.is_file()):
            # Previously flagged by the crawler and still absent on disk
            status = STATUS_FLAGGED
            n_flagged += 1
        elif p is None or not p.is_file():
            status = STATUS_MISSING
            n_missing += 1
        elif stored_size is not None and p.stat().st_size != stored_size:
            status = STATUS_MODIFIED
            n_modified += 1
        else:
            status = STATUS_OK
            if is_missing:
                reappeared.append(model_id)
        results.append(_result_row(model_id, title, path, ahash, stored_size, status))

    if reappeared:
        try:
            with session_scope() as s:
                (s.query(STLModel)
                 .filter(STLModel.id.in_(reappeared))
                 .update({STLModel.is_missing: False}, synchronize_session=False))
        except Exception:
            logger.warning("Could not clear is_missing for reappeared files", exc_info=True)

    ok_count = total - n_missing - n_flagged - n_modified
    log(
        f"Done — {ok_count} OK, {n_modified} modified, "
        f"{n_missing} missing, {n_flagged} flagged missing "
        f"out of {total} archive(s)."
    )
    return results


def _verify_native(lib, progress: Progress, log: Log, should_stop: ShouldStop) -> list[dict]:
    """:func:`verify_archives` through the native core (files checked in parallel)."""
    total, rows, stopped, _cleared = lib.verify_archives(progress=progress, should_stop=should_stop)
    if total == 0:
        log("No records found in the database.")
        return []
    log(f"Verifying {total} archive(s)…")
    results = [_result_row(mid, title, path, ahash, size, status)
               for mid, title, path, ahash, size, status in rows]
    if stopped:
        log("Stopped by user.")
    count = defaultdict(int)
    for r in results:
        count[r["status"]] += 1
    n_missing, n_flagged, n_modified = count[STATUS_MISSING], count[STATUS_FLAGGED], count[STATUS_MODIFIED]
    ok_count = total - n_missing - n_flagged - n_modified
    log(
        f"Done — {ok_count} OK, {n_modified} modified, "
        f"{n_missing} missing, {n_flagged} flagged missing "
        f"out of {total} archive(s)."
    )
    return results


# ---------------------------------------------------------------------------
# Duplicate DB entries
# ---------------------------------------------------------------------------

def find_duplicate_entries(progress: Progress = _noop, log: Log = _noop) -> list[dict]:
    """Find records that catalogue the same physical file more than once.

    Matching rules, most reliable first:

    1. Identical ``archive_hash`` (fallback fingerprints excluded) — content duplicate.
    2. Identical ``(filename, archive_size)`` — for records without a real hash.

    Every record of a group is returned; ``match_path`` shows another member's
    path so the relationship is visible.
    """
    from app.database.db import session_scope
    from app.database.models import STLModel

    log("Scanning for duplicate entries…")
    from app.database import native_db
    lib = native_db.library()
    if lib is not None:
        results = []
        for mid, title, path, ahash, size, match in lib.duplicate_entries():
            rec = _result_row(mid, title, path, ahash, size, STATUS_DUPLICATE, match)
            results.append(rec)
        progress(1, 1)
        n = len(results)
        if n:
            log(f"Found {n} record(s) in {n // 2 + n % 2} duplicate group(s).")
        else:
            log("No duplicate entries found.")
        return results
    with session_scope() as session:
        rows = session.query(
            STLModel.id, STLModel.title, STLModel.archive_path,
            STLModel.archive_hash, STLModel.archive_size,
        ).order_by(STLModel.id).all()

    total = len(rows)
    progress(0, total)
    tick = _Throttle(progress, total)

    hash_groups:     dict[str, list]   = defaultdict(list)
    namesize_groups: dict[tuple, list] = defaultdict(list)
    for idx, (mid, title, path, ahash, asize) in enumerate(rows, 1):
        tick(idx)
        rec = _result_row(mid, title, path, ahash, asize, STATUS_DUPLICATE)
        if ahash and not ahash.startswith("fallback:"):
            hash_groups[ahash].append(rec)
        elif path and asize is not None:
            namesize_groups[(Path(path).name, asize)].append(rec)

    results: list[dict] = []
    reported: set[int] = set()

    def _emit(group: list[dict]) -> None:
        first_path = group[0]["path"]
        for rec in group:
            rec["match_path"] = first_path if rec["path"] != first_path else group[1]["path"]
            results.append(rec)
            reported.add(rec["id"])

    for group in hash_groups.values():
        if len(group) >= 2:
            _emit(group)
    for group in namesize_groups.values():
        # Avoid re-reporting records already caught by the hash bucket
        group = [r for r in group if r["id"] not in reported]
        if len(group) >= 2:
            _emit(group)

    n = len(results)
    if n:
        log(f"Found {n} record(s) in {n // 2 + n % 2} duplicate group(s).")
    else:
        log("No duplicate entries found.")
    return results


# ---------------------------------------------------------------------------
# Prune images per model
# ---------------------------------------------------------------------------

def prune_images(
    keep: int,
    progress: Progress = _noop, log: Log = _noop, should_stop: ShouldStop = _never,
    *, batch_size: int = 200,
) -> tuple[int, int]:
    """Keep only the first *keep* images (lowest id) of every model.

    Excess images are deleted from the database and the LMDB store.  Returns
    ``(models_pruned, images_removed)``.  Work is committed in batches of
    *batch_size* models, so stopping keeps what was already done.
    """
    from app.database.db import session_scope
    from app.database.models import Image as DBImage
    from app.utils.thumbnailer import delete_thumbnails

    from app.database import native_db

    keep = max(0, int(keep))
    lib = native_db.library()
    if lib is not None:
        model_ids = lib.images_over(keep)
    else:
        with session_scope() as session:
            model_ids = [mid for (mid,) in (
                session.query(DBImage.model_id)
                .group_by(DBImage.model_id)
                .having(func.count(DBImage.id) > keep)
                .order_by(DBImage.model_id)
                .all()
            )]

    total = len(model_ids)
    if total == 0:
        log(f"No models have more than {keep} image(s).")
        return 0, 0

    log(f"Pruning {total} model(s) to at most {keep} image(s) each…")
    models_pruned = images_removed = 0
    done = 0
    for start in range(0, total, batch_size):
        if should_stop():
            log("Stopped by user.")
            break
        chunk = model_ids[start:start + batch_size]
        if lib is not None:
            pruned, excess = lib.excess_images(chunk, keep)
            models_pruned += pruned
            delete_thumbnails(excess)
            if excess:
                lib.delete_images(excess)
            images_removed += len(excess)
            done += len(chunk)
            progress(done, total)
            continue
        with session_scope() as session:
            per_model: dict[int, list[int]] = defaultdict(list)
            for img_id, mid in (
                session.query(DBImage.id, DBImage.model_id)
                .filter(DBImage.model_id.in_(chunk))
                .order_by(DBImage.model_id, DBImage.id)
            ):
                per_model[mid].append(img_id)
            excess: list[int] = []
            for mid in chunk:
                extra = per_model.get(mid, [])[keep:]
                if extra:
                    models_pruned += 1
                    excess.extend(extra)
            delete_thumbnails(excess)
            if excess:
                (session.query(DBImage)
                 .filter(DBImage.id.in_(excess))
                 .delete(synchronize_session=False))
            images_removed += len(excess)
        done += len(chunk)
        progress(done, total)

    log(
        f"Done — removed {images_removed} image(s) from {models_pruned} model(s). "
        "Run 'Compact Thumbnail Store' to reclaim the freed disk space."
    )
    return models_pruned, images_removed


# ── Maintenance dialog reads and writes ─────────────────────────────────────
# Run natively (storage::maintenance) when native_db.library() allows it;
# tests/golden/test_maintenance_dialog_golden.py compares the two.

def embedding_targets(recompute_all: bool) -> tuple[int, list[tuple]]:
    """Prune vectors of deleted images, then list the images to embed:
    ``(orphans removed, [(image id, legacy thumbnail blob)])`` by id."""
    from app.database import native_db, vectors
    from app.database.db import session_scope
    from app.database.models import Image
    lib = native_db.library()
    if lib is not None:
        return lib.vectors_prune_orphans(), lib.maint_embedding_targets(bool(recompute_all))
    with session_scope() as s:
        orphans = vectors.prune_orphans(s)
        q = s.query(Image.id, Image.thumbnail)
        if not recompute_all:
            q = q.filter(Image.id.notin_(vectors.current_ids()))
        return orphans, [tuple(r) for r in q.order_by(Image.id)]


def store_embeddings(items: list[tuple]) -> int:
    """``vectors.store`` in a transaction of its own; returns how many were stored."""
    from app.database import native_db, vectors
    from app.database.db import session_scope
    from app.utils.image_embedding import EMBED_DIM
    lib = native_db.library()
    if lib is not None:
        blobs = []
        for image_id, vec in items:
            blob = vectors.to_blob(vec)
            if len(blob) != EMBED_DIM * 4:
                logger.warning("Skipping embedding for image %s: %d dimensions, expected %d",
                               image_id, len(blob) // 4, EMBED_DIM)
                continue
            blobs.append((int(image_id), blob))
        return lib.vectors_store(EMBED_DIM, blobs)
    with session_scope() as s:
        return vectors.store(s, items)


def warning_records() -> list[tuple]:
    """``(id, archive_path, warning)`` of every model, by id."""
    from app.database import native_db
    from app.database.db import session_scope
    from app.database.models import STLModel
    lib = native_db.library()
    if lib is not None:
        return lib.maint_warning_records()
    with session_scope() as s:
        return [tuple(r) for r in s.query(STLModel.id, STLModel.archive_path, STLModel.warning)
                .order_by(STLModel.id)]


def set_warnings(items: list[tuple[int, str | None]]) -> None:
    """Set the ``warning`` of each ``(model id, warning)`` in one transaction."""
    from app.database import native_db
    from app.database.db import session_scope
    from app.database.models import STLModel
    lib = native_db.library()
    if lib is not None:
        lib.maint_set_warnings([(int(i), w) for i, w in items])
        return
    with session_scope() as s:
        for model_id, warning in items:
            m = s.get(STLModel, model_id)
            if m is not None:
                m.warning = warning


def location_rows() -> list[tuple]:
    """``(id, archive_path, archive_hash, archive_size)`` of every model, by id."""
    from app.database import native_db
    from app.database.db import session_scope
    from app.database.models import STLModel
    lib = native_db.library()
    if lib is not None:
        return lib.maint_location_rows()
    with session_scope() as s:
        return [tuple(r) for r in s.query(STLModel.id, STLModel.archive_path, STLModel.archive_hash,
                                          STLModel.archive_size).order_by(STLModel.id)]


def merge_moved(items: list[tuple[int, str]]) -> tuple[int, int, list[str]]:
    """Merge each missing record into the record at its match path
    (``moves.merge_into_missing``), in one transaction.  Returns ``(merged,
    errors, thumbnail refs)``; delete the thumbnails after this returns."""
    from app.database import native_db
    from app.database.db import session_scope
    from app.database.models import STLModel
    from app.services import moves
    lib = native_db.library()
    if lib is not None:
        return tuple(lib.maint_merge_moved([(int(i), str(p)) for i, p in items]))
    merged = errors = 0
    refs: list[str] = []
    with session_scope() as s:
        for old_id, match_path in items:
            old = s.get(STLModel, old_id)
            new = (s.query(STLModel).filter_by(archive_path=match_path)
                   .order_by(STLModel.id).first())
            if old is None or new is None:
                errors += 1
                continue
            refs += moves.merge_into_missing(s, old, new)
            merged += 1
    return merged, errors, refs


def remove_records(model_ids: list[int]) -> tuple[list[str], dict[str, int]]:
    """Delete models and prune unused lookups in one transaction.  Returns
    ``(thumbnail refs, {table: rows pruned})``; delete the thumbnails after."""
    from app.database import native_db
    from app.database.db import session_scope
    from app.database.helpers import prune_unused_lookups
    from app.database.models import STLModel
    from app.services import moves
    order = ("subcollections", "collections", "sculptors", "categories", "releases", "scales")
    lib = native_db.library()
    if lib is not None:
        refs, counts = lib.maint_remove_records([int(i) for i in model_ids])
        return refs, {k: counts[k] for k in order if k in counts}
    refs: list[str] = []
    with session_scope() as s:
        for model_id in model_ids:
            model = s.get(STLModel, model_id)
            if model is not None:
                refs += moves.delete_model(s, model)
        s.flush()
        return refs, prune_unused_lookups(s)
