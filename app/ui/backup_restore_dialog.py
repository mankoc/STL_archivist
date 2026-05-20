"""
Backup and restore the stlLibrary database and thumbnails.

Backup  — packs stlLibrary.db, thumbnails.lmdb/, and settings.json
           into a single .7z archive chosen by the user.
Restore — extracts a previously created backup .7z into the current
           data directory, replacing existing files after confirmation.
"""
import logging
from datetime import datetime
from pathlib import Path

import py7zr

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
)

logger = logging.getLogger(__name__)

_LMDB_DIR      = "thumbnails.lmdb"
_DB_FILENAME   = "stlLibrary.db"
_SETTINGS_FILE = "settings.json"

_BACKUP_ITEMS = [_DB_FILENAME, _LMDB_DIR, _SETTINGS_FILE]


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class _BackupWorker(QThread):
    log      = pyqtSignal(str)
    progress = pyqtSignal(int, int)   # current, total
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, data_dir: Path, dest: Path, parent=None) -> None:
        super().__init__(parent)
        self._data_dir = data_dir
        self._dest     = dest

    def run(self) -> None:
        try:
            items = [self._data_dir / name for name in _BACKUP_ITEMS
                     if (self._data_dir / name).exists()]

            all_files: list[Path] = []
            for item in items:
                if item.is_dir():
                    all_files.extend(f for f in item.rglob("*") if f.is_file())
                else:
                    all_files.append(item)

            total = len(all_files)
            self.log.emit(f"Backing up {total} file(s) → {self._dest.name}")

            with py7zr.SevenZipFile(str(self._dest), mode="w") as zf:
                for idx, fpath in enumerate(all_files, 1):
                    arcname = str(fpath.relative_to(self._data_dir))
                    zf.write(fpath, arcname)
                    self.progress.emit(idx, total)

            size_mb = self._dest.stat().st_size / (1024 * 1024)
            self.log.emit(f"Backup complete — {size_mb:.1f} MB written.")
            self.finished.emit(True, "Backup completed successfully.")
        except Exception as exc:
            logger.exception("Backup failed")
            self.finished.emit(False, f"Backup failed: {exc}")


class _RestoreWorker(QThread):
    log      = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, src: Path, data_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self._src      = src
        self._data_dir = data_dir

    def run(self) -> None:
        try:
            with py7zr.SevenZipFile(str(self._src), mode="r") as zf:
                names = zf.getnames()
                total = len(names)
                self.log.emit(f"Restoring {total} file(s) from {self._src.name}…")
                # py7zr doesn't support per-file extraction with easy progress;
                # extract all at once and emit incremental ticks while it runs.
                # We emit progress after extraction completes for each named entry
                # by using a callback-free approach: extract then report.
                zf.extractall(path=str(self._data_dir))
                self.progress.emit(total, total)

            self.log.emit("Restore complete — please restart the application.")
            self.finished.emit(True, "Restore completed. Please restart stlLibrary.")
        except Exception as exc:
            logger.exception("Restore failed")
            self.finished.emit(False, f"Restore failed: {exc}")


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class BackupRestoreDialog(QDialog):
    """Modal dialog for backing up or restoring the library."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Backup / Restore Library")
        self.setMinimumWidth(520)
        self._worker: QThread | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        from app.ui.config_dialog import get_data_dir
        self._data_dir = Path(get_data_dir())

        info = QLabel(
            f"<b>Data directory:</b> {self._data_dir}<br>"
            "Backs up: database, thumbnails, and settings."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        self._btn_backup  = QPushButton("Backup…")
        self._btn_restore = QPushButton("Restore…")
        self._btn_backup.clicked.connect(self._start_backup)
        self._btn_restore.clicked.connect(self._start_restore)
        btn_row.addWidget(self._btn_backup)
        btn_row.addWidget(self._btn_restore)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(160)
        layout.addWidget(self._log)

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)
        self._close_btn = bbox.button(QDialogButtonBox.StandardButton.Close)

    # ------------------------------------------------------------------
    def _log_line(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)

    def _on_finished(self, success: bool, msg: str) -> None:
        self._progress.setVisible(False)
        self._btn_backup.setEnabled(True)
        self._btn_restore.setEnabled(True)
        self._close_btn.setEnabled(True)
        self._worker = None
        if success:
            QMessageBox.information(self, "Done", msg)
        else:
            QMessageBox.critical(self, "Error", msg)

    def _lock_ui(self) -> None:
        self._btn_backup.setEnabled(False)
        self._btn_restore.setEnabled(False)
        self._close_btn.setEnabled(False)
        self._progress.setValue(0)
        self._progress.setVisible(True)

    # ------------------------------------------------------------------
    def _start_backup(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"stlLibrary_backup_{timestamp}.7z"
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save Backup As",
            str(Path.home() / default_name),
            "7-Zip Archives (*.7z)",
        )
        if not dest:
            return

        self._lock_ui()
        self._log.clear()
        worker = _BackupWorker(self._data_dir, Path(dest), parent=self)
        worker.log.connect(self._log_line)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _start_restore(self) -> None:
        src, _ = QFileDialog.getOpenFileName(
            self,
            "Select Backup File",
            str(Path.home()),
            "7-Zip Archives (*.7z)",
        )
        if not src:
            return

        reply = QMessageBox.warning(
            self,
            "Confirm Restore",
            "This will <b>overwrite</b> your current database, thumbnails, and settings "
            "with the contents of the selected backup.<br><br>"
            "The application must be restarted afterwards.<br><br>"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._lock_ui()
        self._log.clear()
        worker = _RestoreWorker(Path(src), self._data_dir, parent=self)
        worker.log.connect(self._log_line)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)
