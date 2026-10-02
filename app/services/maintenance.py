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
    from app.database.db import session_scope
    from app.database.models import STLModel

    with session_scope() as session:
        records = session.query(
            STLModel.id, STLModel.title, STLModel.archive_path,
            STLModel.archive_hash, STLModel.archive_size, STLModel.is_missing,
        ).all()

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
    with session_scope() as session:
        rows = session.query(
            STLModel.id, STLModel.title, STLModel.archive_path,
            STLModel.archive_hash, STLModel.archive_size,
        ).all()

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
    from app.utils.thumbnailer import delete_thumbnail

    keep = max(0, int(keep))
    with session_scope() as session:
        model_ids = [mid for (mid,) in (
            session.query(DBImage.model_id)
            .group_by(DBImage.model_id)
            .having(func.count(DBImage.id) > keep)
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
            for img_id in excess:
                delete_thumbnail(img_id)
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
