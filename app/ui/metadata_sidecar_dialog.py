"""
Dialog for generating or updating METADATA.7z sidecar files across all folders
that have records in the database.
"""
import logging
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QPlainTextEdit,
    QSizePolicy, QApplication, QMessageBox,
)

logger = logging.getLogger(__name__)


class MetadataSidecarWorker(QThread):
    """Background thread that exports METADATA.7z for every folder with DB records.

    When *folders* is provided only those specific folders are processed;
    otherwise all distinct parent directories of archive records are used.

    Signals:
        progress(current, total): Folder index and total folder count.
        log(message): Human-readable status line for the UI log panel.
        finished(): Emitted when all folders are processed or the worker is stopped.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, folders=None, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._folders = folders  # None → process all DB folders

    def stop(self) -> None:
        """Request the worker to stop after the current folder finishes."""
        self._stop_requested = True

    def run(self) -> None:
        """Main thread entry point — iterates folders and writes METADATA.7z files."""
        from app.database.db import session_scope
        from app.database.models import STLModel
        from app.crawler.folder_metadata import export_folder_metadata
        from app.utils.thumbnailer import get_thumbnail_base_dir
        from app.ui.config_dialog import (
            get_metadata_sidecar_enabled,
            get_metadata_thumbnails_enabled,
        )

        self._stop_requested = False

        if not get_metadata_sidecar_enabled():
            self.log.emit(
                "Metadata sidecar is disabled in Configuration. "
                "Enable it first, then retry."
            )
            self.finished.emit()
            return

        thumb_base = get_thumbnail_base_dir()
        if thumb_base is None:
            self.log.emit("Thumbnail directory is not configured — cannot run.")
            self.finished.emit()
            return

        include_thumbs = get_metadata_thumbnails_enabled()

        # Collect target folders
        if self._folders is not None:
            folders = sorted(self._folders)
        else:
            with session_scope() as session:
                rows = session.query(STLModel.archive_path).all()
            folders = sorted({Path(row.archive_path).parent for row in rows if row.archive_path})

        total = len(folders)

        if total == 0:
            self.log.emit("No folders with database records found.")
            self.finished.emit()
            return

        self.log.emit(
            f"Processing {total} folder(s). Writing METADATA.7z "
            f"(thumbnails={'yes' if include_thumbs else 'no'})…"
        )
        logger.info(
            "MetadataSidecarWorker: processing %d folder(s), thumbnails=%s",
            total, include_thumbs,
        )

        written = 0
        for idx, folder in enumerate(folders, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break

            self.progress.emit(idx, total)
            try:
                with session_scope() as session:
                    ok = export_folder_metadata(
                        folder, session, thumb_base,
                        include_thumbnails=include_thumbs,
                    )
                if ok:
                    self.log.emit(f"[{idx}/{total}] Updated: {folder}")
                    written += 1
                    logger.debug("  wrote METADATA.7z in %s", folder)
                else:
                    self.log.emit(f"[{idx}/{total}] Skipped (no records): {folder}")
            except Exception as exc:
                self.log.emit(f"[{idx}/{total}] ERROR in {folder}: {exc}")
                logger.error("Failed to write METADATA.7z in %s: %s", folder, exc, exc_info=True)

        self.log.emit(f"Done — updated {written} of {total} folder(s).")
        logger.info("MetadataSidecarWorker finished: %d/%d written", written, total)
        self.finished.emit()


class RemoveSidecarsWorker(QThread):
    """Background thread that deletes METADATA.7z from every folder with DB records.

    Signals:
        progress(current, total): Folder index and total folder count.
        log(message): Human-readable status line for the UI log panel.
        finished(): Emitted when all folders are processed or the worker is stopped.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, folders=None, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False
        self._folders = folders  # None → process all DB folders

    def stop(self) -> None:
        """Request the worker to stop after the current folder finishes."""
        self._stop_requested = True

    def run(self) -> None:
        """Main thread entry point — iterates folders and removes METADATA.7z files."""
        from app.database.db import session_scope
        from app.database.models import STLModel

        self._stop_requested = False

        if self._folders is not None:
            folders = sorted(self._folders)
        else:
            with session_scope() as session:
                rows = session.query(STLModel.archive_path).all()
            folders = sorted({Path(row.archive_path).parent for row in rows if row.archive_path})

        total = len(folders)

        if total == 0:
            self.log.emit("No folders with database records found.")
            self.finished.emit()
            return

        self.log.emit(f"Scanning {total} folder(s) for METADATA.7z files…")
        logger.info("RemoveSidecarsWorker: scanning %d folder(s)", total)

        removed = 0
        for idx, folder in enumerate(folders, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break

            self.progress.emit(idx, total)
            sidecar = folder / "METADATA.7z"
            if sidecar.exists():
                try:
                    sidecar.unlink()
                    self.log.emit(f"[{idx}/{total}] Removed: {sidecar}")
                    removed += 1
                    logger.debug("  removed %s", sidecar)
                except Exception as exc:
                    self.log.emit(f"[{idx}/{total}] ERROR removing {sidecar}: {exc}")
                    logger.error("Failed to remove %s: %s", sidecar, exc, exc_info=True)
            else:
                self.log.emit(f"[{idx}/{total}] Not found: {folder}")

        self.log.emit(f"Done — removed {removed} of {total} folder(s).")
        logger.info("RemoveSidecarsWorker finished: %d/%d removed", removed, total)
        self.finished.emit()


class MetadataSidecarDialog(QDialog):
    """Modal dialog that runs MetadataSidecarWorker and shows its progress.

    If *folders* is provided the dialog targets only those folders and starts
    automatically when shown.
    """

    def __init__(self, folders=None, parent=None) -> None:
        super().__init__(parent)
        self._folders = folders
        title = (
            f"Create / Update Sidecar for {len(folders)} folder(s)"
            if folders is not None
            else "Generate / Update Metadata Sidecars"
        )
        self.setWindowTitle(title)
        self.resize(640, 420)
        self.setModal(True)
        self._worker: MetadataSidecarWorker | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        if self._folders is not None:
            folder_list = "<br>".join(f"&nbsp;• {f}" for f in sorted(self._folders))
            desc = (
                f"Writes or refreshes <b>METADATA.7z</b> for the "
                f"{len(self._folders)} folder(s) containing the selected models:<br>"
                f"{folder_list}"
            )
        else:
            desc = (
                "Writes or refreshes a <b>METADATA.7z</b> sidecar file in every "
                "folder that contains archived models in the database.<br>"
                "This allows the library to be restored on a new machine without "
                "re-crawling every archive."
            )
        info = QLabel(desc)
        info.setWordWrap(True)
        root.addWidget(info)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        root.addWidget(QLabel("Log:"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._log)

        style = QApplication.style()
        btn_row = QHBoxLayout()

        self._start_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_MediaPlay), "Start"
        )
        self._start_btn.clicked.connect(self._start)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_MediaStop), "Stop"
        )
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self._stop_btn)

        self._remove_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_TrashIcon), "Remove all sidecars"
        )
        self._remove_btn.clicked.connect(self._remove)
        btn_row.addWidget(self._remove_btn)

        btn_row.addStretch()

        self._close_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_DialogCloseButton), "Close"
        )
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:
        """Auto-start the worker when the dialog opens with a pre-set folder list."""
        super().showEvent(event)
        if self._folders is not None and self._start_btn.isEnabled():
            self._start()

    def _start(self) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._remove_btn.setEnabled(False)
        self._close_btn.setEnabled(False)

        self._worker = MetadataSidecarWorker(folders=self._folders)
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop(self) -> None:
        """Request the worker to stop after the current folder."""
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    def _remove(self) -> None:
        """Confirm and then delete every METADATA.7z file found in DB folders."""
        answer = QMessageBox.question(
            self,
            "Remove all sidecars",
            "This will permanently delete every <b>METADATA.7z</b> sidecar file "
            "found in the folders that have database records.<br><br>"
            "Are you sure you want to continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._log.clear()
        self._progress.setValue(0)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._remove_btn.setEnabled(False)
        self._close_btn.setEnabled(False)

        self._worker = RemoveSidecarsWorker(folders=self._folders)
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    # ------------------------------------------------------------------
    def _on_log(self, msg: str) -> None:
        """Append a worker log message to the log panel."""
        self._log.appendPlainText(msg)

    def _on_progress(self, current: int, total: int) -> None:
        """Update the progress bar with the current folder index and total."""
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")

    def _on_finished(self) -> None:
        """Re-enable buttons and clear the worker reference when done."""
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._remove_btn.setEnabled(True)
        self._close_btn.setEnabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        """Stop the worker if the dialog is closed while running."""
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
        super().closeEvent(event)
