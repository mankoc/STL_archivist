"""
Background worker that fetches thumbnail JPEG bytes from LMDB off the GUI thread.

The worker lives on a dedicated QThread.  The GUI thread emits load_requested
(via a QueuedConnection) and receives bytes_ready signals to construct QPixmaps.
QPixmap creation must stay on the GUI thread, so only raw bytes cross the thread
boundary.

Cancellation: each load() call increments an internal counter.  If a newer call
arrives while a batch is in flight the in-progress loop detects the mismatch and
exits early, so stale bytes are never emitted.
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class ThumbnailLoaderWorker(QObject):
    bytes_ready = pyqtSignal(int, int, bytes)   # model_id, image_id, jpeg_bytes

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._seq: int = 0  # incremented on each load call to detect superseded requests

    @pyqtSlot(int, list)
    def load(self, model_id: int, image_ids: list) -> None:
        """Fetch JPEG bytes for *image_ids* and emit bytes_ready per image.

        Runs on the worker thread via a QueuedConnection from the GUI thread.
        If a newer load() call arrives mid-batch the loop exits without emitting
        stale results.
        """
        with self._lock:
            self._seq += 1
            my_seq = self._seq

        from app.utils.thumbnailer import get_thumbnail_bytes_many
        bytes_map = get_thumbnail_bytes_many(image_ids)

        for image_id in image_ids:
            with self._lock:
                if self._seq != my_seq:
                    return
            data = bytes_map.get(image_id, b"")
            self.bytes_ready.emit(model_id, image_id, data)
