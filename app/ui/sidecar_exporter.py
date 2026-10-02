"""
Debounced, background ``METADATA.7z`` export for edited folders.

Saving an edit used to rebuild the folder's sidecar (optionally embedding
every thumbnail) synchronously on the GUI thread — once per edited cell.
``schedule()`` instead collects folders for a short quiet period and exports
each one once, on a single background thread so exports never overlap.

Call ``schedule()`` only after the edit's transaction has committed: the
export opens its own session and snapshots what is in the database.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from PyQt6.QtCore import QRunnable, QThreadPool, QTimer

logger = logging.getLogger(__name__)

_DEBOUNCE_MS = 1500

_pending: set[Path] = set()
_timer: QTimer | None = None
_pool: QThreadPool | None = None


class _ExportRunnable(QRunnable):
    def __init__(self, folders: list[Path]) -> None:
        super().__init__()
        self._folders = folders
        self.setAutoDelete(True)

    def run(self) -> None:
        from app.core.settings import get_metadata_thumbnails_enabled
        from app.crawler.folder_metadata import export_folder_metadata
        from app.database.db import session_scope
        from app.utils.thumbnailer import get_thumbnail_base_dir

        thumb_base = get_thumbnail_base_dir()
        if thumb_base is None:
            return
        include_thumbs = get_metadata_thumbnails_enabled()
        for folder in self._folders:
            try:
                with session_scope() as session:
                    export_folder_metadata(folder, session, thumb_base,
                                           include_thumbnails=include_thumbs)
            except Exception:
                # Best-effort, as before: a failed sidecar never blocks an edit.
                logger.warning("Sidecar export failed for %s", folder, exc_info=True)


def _flush() -> None:
    if not _pending:
        return
    folders = sorted(_pending)
    _pending.clear()
    _pool.start(_ExportRunnable(folders))


def schedule(folders: Iterable[Path | str]) -> None:
    """Queue *folders* for a sidecar refresh (GUI thread only).

    A no-op when sidecars are disabled in Configuration.
    """
    global _timer, _pool
    from app.core.settings import get_metadata_sidecar_enabled
    if not get_metadata_sidecar_enabled():
        return
    new = {Path(f) for f in folders if f}
    if not new:
        return
    if _timer is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(1)
        _timer = QTimer()
        _timer.setSingleShot(True)
        _timer.setInterval(_DEBOUNCE_MS)
        _timer.timeout.connect(_flush)
    _pending.update(new)
    _timer.start()


def flush_now() -> None:
    """Start any pending export immediately (e.g. on application shutdown)."""
    if _timer is not None:
        _timer.stop()
        _flush()


def wait_for_done(msecs: int = 30000) -> bool:
    """Block until queued exports finish; returns False on timeout."""
    return _pool.waitForDone(msecs) if _pool is not None else True
