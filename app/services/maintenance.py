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
    in one transaction. Returns ``(computed, skipped)``; images with no
    thumbnail or undecodable data count as skipped.
    """
    from app.database import native_db
    from app.utils.image_hash import DHASH_STR_LEN, compute_dhashes, format_dhash
    from app.utils.thumbnailer import get_thumbnail_bytes_many

    lib = native_db.library()
    ids = lib.dhash_targets(bool(recompute_all), DHASH_STR_LEN)
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
            thumbs.update(lib.legacy_thumbnails(missing))
        have = [i for i in chunk if thumbs.get(i)]
        hashes = compute_dhashes([thumbs[i] for i in have])
        rows = [(i, format_dhash(h)) for i, h in zip(have, hashes) if h is not None]
        if rows:
            lib.set_dhashes(rows)
        computed += len(rows)
        skipped += len(chunk) - len(rows)
        progress(min(start + DHASH_BATCH, total), total)

    log(f"Done — {computed:,} dHash(es) computed, {skipped:,} skipped "
        "(no thumbnail or decode error).")
    return computed, skipped


# ── Database compaction ──────────────────────────────────────────────────────

def database_free_bytes(_session=None) -> int:
    """Bytes held by free pages in the database file (what VACUUM can reclaim).

    Reads two header values, so it is cheap enough for the GUI thread.  The
    write-ahead log is not counted; compaction also truncates it.
    """
    from app.database import native_db
    return native_db.library().maint_free_bytes()


def database_file_bytes(db_path: Path) -> int:
    """On-disk size of the database: the main file plus its write-ahead log."""
    total = 0
    for p in (db_path, db_path.with_name(db_path.name + "-wal")):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def compact_database(db_path: Path) -> tuple[int, int, bool]:
    """Rewrite the database without free pages (``VACUUM``) and shrink its WAL.

    SQLite never shrinks its file on its own: deleted rows leave free pages
    that are reused, but the file stays at its high-water mark.  ``VACUUM``
    rebuilds it compactly (its temporary copy on disk, not in RAM).  In WAL
    mode the rebuilt pages go through the write-ahead log, which ``VACUUM``
    leaves as large as the database, so a ``wal_checkpoint(TRUNCATE)`` follows.

    Other connections may keep reading; a concurrent writer (a crawl) makes
    ``VACUUM`` fail with "database is locked" after the busy timeout.

    Returns ``(size_before, size_after, wal_truncated)`` in bytes.  The WAL
    is not truncated while another connection is still reading from it; it
    is then reset at a later checkpoint.
    """
    from app.database import native_db
    before = database_file_bytes(db_path)
    lib = native_db.library()
    lib.close()                 # pooled connections would keep the WAL busy
    truncated = lib.db_compact()
    return before, database_file_bytes(db_path), truncated


def _never() -> bool:
    return False


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

    lib = native_db.library()
    return _verify_native(lib, progress, log, should_stop)


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

    log("Scanning for duplicate entries…")
    from app.database import native_db
    lib = native_db.library()
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
    from app.database import native_db
    from app.utils.thumbnailer import delete_thumbnails

    keep = max(0, int(keep))
    lib = native_db.library()
    model_ids = lib.images_over(keep)

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
        pruned, excess = lib.excess_images(chunk, keep)
        models_pruned += pruned
        delete_thumbnails(excess)
        if excess:
            lib.delete_images(excess)
        images_removed += len(excess)
        done += len(chunk)
        progress(done, total)

    log(
        f"Done — removed {images_removed} image(s) from {models_pruned} model(s). "
        "Run 'Compact Thumbnail Store' to reclaim the freed disk space."
    )
    return models_pruned, images_removed


# ── Maintenance dialog reads and writes (native: storage::maintenance) ───────

def embedding_targets(recompute_all: bool) -> tuple[int, list[tuple]]:
    """Prune vectors of deleted images, then list the images to embed:
    ``(orphans removed, [(image id, legacy thumbnail blob)])`` by id."""
    from app.database import native_db
    lib = native_db.library()
    return lib.vectors_prune_orphans(), lib.maint_embedding_targets(bool(recompute_all))


def store_embeddings(items: list[tuple]) -> int:
    """Store ``(image_id, vector)`` pairs in one transaction; returns how many were stored."""
    from app.database import native_db
    from app.utils.image_embedding import EMBED_DIM, to_blob
    lib = native_db.library()
    blobs = []
    for image_id, vec in items:
        blob = to_blob(vec)
        if len(blob) != EMBED_DIM * 4:
            logger.warning("Skipping embedding for image %s: %d dimensions, expected %d",
                           image_id, len(blob) // 4, EMBED_DIM)
            continue
        blobs.append((int(image_id), blob))
    return lib.vectors_store(EMBED_DIM, blobs)


def warning_records() -> list[tuple]:
    """``(id, archive_path, warning)`` of every model, by id."""
    from app.database import native_db
    lib = native_db.library()
    return lib.maint_warning_records()


def set_warnings(items: list[tuple[int, str | None]]) -> None:
    """Set the ``warning`` of each ``(model id, warning)`` in one transaction."""
    from app.database import native_db
    lib = native_db.library()
    lib.maint_set_warnings([(int(i), w) for i, w in items])
    return


def location_rows() -> list[tuple]:
    """``(id, archive_path, archive_hash, archive_size)`` of every model, by id."""
    from app.database import native_db
    lib = native_db.library()
    return lib.maint_location_rows()


def merge_moved(items: list[tuple[int, str]]) -> tuple[int, int, list[str]]:
    """Merge each missing record into the record at its match path
    (``moves.merge_into_missing``), in one transaction.  Returns ``(merged,
    errors, thumbnail refs)``; delete the thumbnails after this returns."""
    from app.database import native_db
    lib = native_db.library()
    return tuple(lib.maint_merge_moved([(int(i), str(p)) for i, p in items]))


def remove_records(model_ids: list[int]) -> tuple[list[str], dict[str, int]]:
    """Delete models and prune unused lookups in one transaction.  Returns
    ``(thumbnail refs, {table: rows pruned})``; delete the thumbnails after."""
    from app.database import native_db
    order = ("subcollections", "collections", "sculptors", "categories", "releases", "scales")
    lib = native_db.library()
    refs, counts = lib.maint_remove_records([int(i) for i in model_ids])
    return refs, {k: counts[k] for k in order if k in counts}
