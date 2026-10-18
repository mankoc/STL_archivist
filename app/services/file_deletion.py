"""
Archive file deletion service.

Two modes are supported:

* **trash**  — move the archive into a configured trash folder and write a
  JSON recovery sidecar alongside it (original path, title, creator, hash,
  delete timestamp).  Filename collisions are resolved by appending an
  integer suffix.
* **purge** — delete the archive file permanently with ``Path.unlink``.

Both modes also remove the corresponding ``STLModel`` row (cascade-deletes
images, tags, and stl_files).  Thumbnails stored
in LMDB are purged via ``delete_thumbnail`` so the LMDB store does not grow
unbounded.

The function is intentionally Qt-free; the UI layer collects user input
and shows results.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.database import native_db
from app.services import browser_data
from app.utils.thumbnailer import delete_thumbnails

logger = logging.getLogger(__name__)


@dataclass
class DeletionReport:
    """Summary of what :func:`delete_archives` did.

    Attributes:
        deleted: Number of model records successfully removed (file move /
            unlink AND DB delete both succeeded).
        failed: One ``(archive_path, error_message)`` tuple per model whose
            deletion raised.  The DB record for failed entries is rolled back
            and left in place.
    """

    deleted: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.deleted + len(self.failed)


def delete_archives(
    model_ids: list[int],
    use_trash: bool,
    trash_path: Path | str | None,
) -> DeletionReport:
    """Delete archive files for the given model IDs and return a report.

    Each archive file is moved to the trash or unlinked first; the records
    whose file step succeeded are then deleted in one transaction (natively
    when :func:`app.database.native_db.library` allows it), and their
    thumbnails after that.

    Args:
        model_ids: Primary keys of the ``STLModel`` rows to delete.
        use_trash: When ``True``, move files into *trash_path* instead of
            permanently deleting them.  Ignored when *trash_path* is
            falsy — falls back to permanent deletion in that case.
        trash_path: Destination folder for moved files.  May be ``None``
            or an empty string when *use_trash* is ``False``.

    Returns:
        A :class:`DeletionReport` with per-archive success / failure counts.
    """
    report = DeletionReport()
    if not model_ids:
        return report

    trash = Path(trash_path) if (use_trash and trash_path) else None
    if trash is not None:
        trash.mkdir(parents=True, exist_ok=True)

    done: list[int] = []
    for model_id, archive_path, title, creator, archive_hash in _rows(model_ids):
        try:
            file_path = Path(archive_path)
            if file_path.exists():
                if trash is not None:
                    _move_to_trash(file_path, trash, title, creator, archive_hash)
                else:
                    file_path.unlink()
            done.append(model_id)
            report.deleted += 1
        except Exception as exc:
            logger.error("Failed to delete %s: %s", archive_path, exc, exc_info=True)
            report.failed.append((archive_path, str(exc)))

    # One LMDB transaction for all models: each one ends with a disk flush.
    delete_thumbnails(browser_data.delete_models(done))
    return report


def _rows(model_ids: list[int]) -> list[tuple]:
    """``(id, archive_path, title, creator, archive_hash)`` in id order."""
    lib = native_db.library()
    return lib.browser_deletion_rows([int(i) for i in model_ids])


def _move_to_trash(file_path: Path, trash_dir: Path, title, creator, archive_hash) -> None:
    """Move *file_path* into *trash_dir* and write a recovery JSON sidecar.

    Filename collisions in the trash directory are resolved by appending
    ``_1``, ``_2``, … to the stem until a free name is found.
    """
    dest = trash_dir / file_path.name
    counter = 1
    while dest.exists():
        dest = trash_dir / f"{file_path.stem}_{counter}{file_path.suffix}"
        counter += 1

    shutil.move(str(file_path), str(dest))

    info = {
        "original_path": str(file_path),
        "title":         title,
        "creator":       creator,
        "archive_hash":  archive_hash,
        "deleted_at":    datetime.now(timezone.utc).isoformat(),
    }
    sidecar = dest.with_suffix(dest.suffix + ".json")
    try:
        sidecar.write_text(json.dumps(info, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write recovery sidecar %s: %s", sidecar, exc)
