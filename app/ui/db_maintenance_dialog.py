"""
Database Maintenance — verify that all catalogued archives still exist on disk,
relocate moved archives by content hash, or remove stale records.
"""
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

_STATUS_OK      = "OK"
_STATUS_MISSING = "Missing"
_STATUS_FOUND   = "Relocated"

_COL_ID     = 0
_COL_TITLE  = 1
_COL_PATH   = 2
_COL_STATUS = 3

def _color_missing() -> QColor:
    from app.ui.config_dialog import get_missing_file_color
    return QColor(get_missing_file_color())

def _color_found() -> QColor:
    from app.ui.config_dialog import get_found_file_color
    return QColor(get_found_file_color())

_COLOR_FG = QColor(255, 255, 255)


class VerifyWorker(QThread):
    """Check whether each archive_path in the database still exists on disk.

    Signals:
        progress(current, total): Archive index and total count.
        log(message): Human-readable status line for the UI log panel.
        finished(results): List of result dicts once verification completes.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    finished = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.database.db import session_scope
        from app.database.models import STLModel

        self._stop_requested = False
        results: list[dict] = []

        with session_scope() as session:
            records = session.query(
                STLModel.id,
                STLModel.title,
                STLModel.archive_path,
                STLModel.archive_hash,
            ).all()

        total = len(records)
        if total == 0:
            self.log.emit("No records found in the database.")
            self.finished.emit([])
            return

        self.log.emit(f"Verifying {total} archive(s)…")
        missing = 0
        for idx, (model_id, title, path, ahash) in enumerate(records, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break
            self.progress.emit(idx, total)
            exists = bool(path and Path(path).is_file())
            status = _STATUS_OK if exists else _STATUS_MISSING
            if not exists:
                missing += 1
            results.append({
                "id":     model_id,
                "title":  title or "",
                "path":   path or "",
                "hash":   ahash or "",
                "status": status,
            })

        found_count = total - missing
        self.log.emit(
            f"Done — {found_count} OK, {missing} missing out of {total} archive(s)."
        )
        self.finished.emit(results)


class SearchWorker(QThread):
    """Scan a folder tree for archives that match missing database records by content hash.

    For archives stored with a fallback fingerprint ("fallback:{size}:{mtime}") the
    file size is used as a fast pre-filter before attempting hash comparison.  All
    other matches are confirmed via the full CRC-based content hash (reads archive
    catalog only — no decompression required).

    Signals:
        progress(current, total): Candidate file index and total candidate count.
        log(message): Human-readable status line for the UI log panel.
        found(model_id, new_path): Emitted each time a missing archive is located.
        finished(): Emitted when the scan completes or is stopped.
    """

    progress = pyqtSignal(int, int)
    log      = pyqtSignal(str)
    found    = pyqtSignal(int, str)
    finished = pyqtSignal()

    def __init__(self, search_root: Path, targets: dict, parent=None) -> None:
        """
        targets: mapping of {archive_hash: model_id} for the missing records to locate.
        """
        super().__init__(parent)
        self._stop_requested = False
        self._search_root = search_root
        self._targets = targets

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        from app.crawler.archive import ARCHIVE_SUFFIXES, METADATA_FILENAME, archive_hash
        from app.database.db import session_scope
        from app.database.models import STLModel
        from app.ui.config_dialog import get_unrar_path

        self._stop_requested = False
        unrar = get_unrar_path()

        # Extract expected file sizes from fallback fingerprints ("fallback:{size}:{mtime}")
        # so we can skip obviously wrong candidates before computing their hash.
        fallback_sizes: dict[str, int] = {}
        for h in self._targets:
            if h.startswith("fallback:"):
                parts = h.split(":")
                if len(parts) >= 2:
                    try:
                        fallback_sizes[h] = int(parts[1])
                    except ValueError:
                        pass

        candidates = [
            p for p in self._search_root.rglob("*")
            if p.suffix.lower() in ARCHIVE_SUFFIXES and p.name != METADATA_FILENAME
        ]
        total = len(candidates)
        if total == 0:
            self.log.emit("No archive files found in the selected folder.")
            self.finished.emit()
            return

        self.log.emit(
            f"Scanning {total} archive(s) in {self._search_root} "
            f"for {len(self._targets)} missing record(s)…"
        )

        matched = 0
        remaining = dict(self._targets)

        for idx, candidate in enumerate(candidates, 1):
            if self._stop_requested:
                self.log.emit("Stopped by user.")
                break
            if not remaining:
                self.log.emit("All missing archives have been found.")
                break

            self.progress.emit(idx, total)

            # Size pre-filter: if all remaining targets have fallback fingerprints and
            # this candidate's size doesn't match any of them, skip the expensive hash.
            if fallback_sizes and all(h in fallback_sizes for h in remaining):
                try:
                    cand_size = candidate.stat().st_size
                    if not any(cand_size == sz for h, sz in fallback_sizes.items() if h in remaining):
                        continue
                except OSError:
                    pass

            try:
                h = archive_hash(candidate, unrar_path=unrar)
            except Exception as exc:
                self.log.emit(f"  Could not hash {candidate.name}: {exc}")
                continue

            if h not in remaining:
                continue

            model_id = remaining.pop(h)
            new_path = str(candidate)
            try:
                with session_scope() as session:
                    model = session.query(STLModel).filter(STLModel.id == model_id).first()
                    if model:
                        model.archive_path = new_path
                self.found.emit(model_id, new_path)
                self.log.emit(f"  [{idx}/{total}] Found: {candidate.name} → updated record #{model_id}")
                matched += 1
            except Exception as exc:
                self.log.emit(f"  ERROR updating DB for {candidate.name}: {exc}")
                logger.error(
                    "Failed to update archive_path for model %d: %s", model_id, exc, exc_info=True
                )

        self.log.emit(
            f"Search complete — {matched} of {len(self._targets)} missing archive(s) located."
        )
        self.finished.emit()


class DbMaintenanceDialog(QDialog):
    """Database maintenance dialog.

    Three operations are available after running Verify:
      - Relocate: search a user-selected folder tree for missing archives by content hash.
      - Remove Selected: delete chosen records from the database.
      - Remove All Missing: delete every record whose archive file no longer exists.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Database Maintenance")
        self.resize(900, 620)
        self.setModal(True)
        self._verify_worker: VerifyWorker | None = None
        self._search_worker: SearchWorker | None = None
        self._results: list[dict] = []
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        desc = QLabel(
            "Verify that every archive catalogued in the database still exists on disk. "
            "Missing archives can be relocated by searching a folder recursively, "
            "or removed from the database."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        style = QApplication.style()
        SP = style.StandardPixmap
        btn_row = QHBoxLayout()

        self._verify_btn = QPushButton(style.standardIcon(SP.SP_BrowserReload), "Verify")
        self._verify_btn.setToolTip("Check all archive paths for existence on disk")
        self._verify_btn.clicked.connect(self._start_verify)
        btn_row.addWidget(self._verify_btn)

        self._stop_btn = QPushButton(style.standardIcon(SP.SP_MediaStop), "Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()

        self._search_btn = QPushButton(style.standardIcon(SP.SP_DirOpenIcon), "Search in Folder…")
        self._search_btn.setToolTip(
            "Pick a folder to search recursively for selected (or all) missing archives"
        )
        self._search_btn.setEnabled(False)
        self._search_btn.clicked.connect(self._start_search)
        btn_row.addWidget(self._search_btn)

        self._remove_sel_btn = QPushButton(style.standardIcon(SP.SP_TrashIcon), "Remove Selected")
        self._remove_sel_btn.setToolTip("Remove the selected records from the database")
        self._remove_sel_btn.setEnabled(False)
        self._remove_sel_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(self._remove_sel_btn)

        self._remove_all_btn = QPushButton(
            style.standardIcon(SP.SP_TrashIcon), "Remove All Missing"
        )
        self._remove_all_btn.setToolTip(
            "Remove every record whose archive no longer exists on disk"
        )
        self._remove_all_btn.setEnabled(False)
        self._remove_all_btn.clicked.connect(self._remove_all_missing)
        btn_row.addWidget(self._remove_all_btn)

        btn_row.addStretch()

        self._close_btn = QPushButton(
            style.standardIcon(SP.SP_DialogCloseButton), "Close"
        )
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        filter_row = QHBoxLayout()
        self._only_failed_cb = QCheckBox("Show only missing")
        self._only_failed_cb.toggled.connect(self._apply_filter)
        filter_row.addWidget(self._only_failed_cb)
        filter_row.addStretch()
        root.addLayout(filter_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["ID", "Title", "Archive Path", "Status"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_ID,     QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_TITLE,  QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_PATH,   QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setColumnHidden(_COL_ID, True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

        root.addWidget(QLabel("Log:"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        root.addWidget(self._log)

    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._verify_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)
        self._search_btn.setEnabled(False)
        self._remove_sel_btn.setEnabled(False)
        self._remove_all_btn.setEnabled(False)
        self._close_btn.setEnabled(not busy)

    def _set_idle(self) -> None:
        has_missing = any(r["status"] == _STATUS_MISSING for r in self._results)
        has_any     = bool(self._results)
        self._verify_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._search_btn.setEnabled(has_missing)
        self._remove_sel_btn.setEnabled(has_any)
        self._remove_all_btn.setEnabled(has_missing)
        self._close_btn.setEnabled(True)

    def _stop(self) -> None:
        if self._verify_worker and self._verify_worker.isRunning():
            self._verify_worker.stop()
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.stop()
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _start_verify(self) -> None:
        self._log.clear()
        self._progress.setValue(0)
        self._table.setRowCount(0)
        self._results.clear()
        self._set_busy(True)

        self._verify_worker = VerifyWorker()
        self._verify_worker.log.connect(self._on_log)
        self._verify_worker.progress.connect(self._on_progress)
        self._verify_worker.finished.connect(self._on_verify_finished)
        self._verify_worker.start()

    def _on_verify_finished(self, results: list) -> None:
        self._results = results
        self._populate_table(results)
        self._verify_worker = None
        self._set_idle()

    def _populate_table(self, results: list) -> None:
        self._table.setRowCount(0)
        for row in results:
            r = self._table.rowCount()
            self._table.insertRow(r)

            items = [
                QTableWidgetItem(str(row["id"])),
                QTableWidgetItem(row["title"]),
                QTableWidgetItem(row["path"]),
                QTableWidgetItem(row["status"]),
            ]
            for item in items:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            status = row["status"]
            if status == _STATUS_MISSING:
                self._color_row(items, _color_missing())
            elif status == _STATUS_FOUND:
                self._color_row(items, _color_found())

            for col, item in enumerate(items):
                self._table.setItem(r, col, item)

        self._apply_filter()

    def _apply_filter(self) -> None:
        only_missing = self._only_failed_cb.isChecked()
        for row in range(self._table.rowCount()):
            status_item = self._table.item(row, _COL_STATUS)
            if status_item is None:
                continue
            hide = only_missing and status_item.text() != _STATUS_MISSING
            self._table.setRowHidden(row, hide)

    @staticmethod
    def _color_row(items: list[QTableWidgetItem], bg: QColor) -> None:
        bg_brush = QBrush(bg)
        fg_brush = QBrush(_COLOR_FG)
        for item in items:
            item.setBackground(bg_brush)
            item.setForeground(fg_brush)

    # ------------------------------------------------------------------
    def _start_search(self) -> None:
        selected_rows = {idx.row() for idx in self._table.selectedIndexes()}

        if selected_rows:
            targets = {
                self._results[r]["hash"]: self._results[r]["id"]
                for r in selected_rows
                if r < len(self._results) and self._results[r]["status"] == _STATUS_MISSING
            }
            if not targets:
                QMessageBox.information(
                    self, "Search",
                    "None of the selected rows are missing — nothing to search for."
                )
                return
        else:
            targets = {
                r["hash"]: r["id"]
                for r in self._results
                if r["status"] == _STATUS_MISSING
            }

        if not targets:
            QMessageBox.information(self, "Search", "No missing archives to search for.")
            return

        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Search", "",
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return

        self._set_busy(True)
        self._log.appendPlainText(
            f"Searching for {len(targets)} missing archive(s) in: {folder}"
        )

        self._search_worker = SearchWorker(Path(folder), targets)
        self._search_worker.log.connect(self._on_log)
        self._search_worker.progress.connect(self._on_progress)
        self._search_worker.found.connect(self._on_archive_found)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_archive_found(self, model_id: int, new_path: str) -> None:
        for i, row in enumerate(self._results):
            if row["id"] == model_id:
                row["path"]   = new_path
                row["status"] = _STATUS_FOUND
                items = [self._table.item(i, col) for col in range(4)]
                self._table.item(i, _COL_PATH).setText(new_path)
                self._table.item(i, _COL_STATUS).setText(_STATUS_FOUND)
                self._color_row([it for it in items if it], _color_found())
                if self._only_failed_cb.isChecked():
                    self._table.setRowHidden(i, True)
                break

    def _on_search_finished(self) -> None:
        self._search_worker = None
        self._set_idle()

    # ------------------------------------------------------------------
    def _remove_selected(self) -> None:
        selected_rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()}, reverse=True
        )
        if not selected_rows:
            QMessageBox.information(self, "Remove", "No rows selected.")
            return

        count = len(selected_rows)
        reply = QMessageBox.warning(
            self,
            "Remove Records",
            f"Permanently remove <b>{count}</b> record(s) from the database?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ids_to_remove = [
            self._results[r]["id"] for r in selected_rows if r < len(self._results)
        ]
        self._remove_records(ids_to_remove)

        for r in selected_rows:
            if r < len(self._results):
                del self._results[r]
                self._table.removeRow(r)

        self._log.appendPlainText(f"Removed {len(ids_to_remove)} record(s) from the database.")
        self._refresh_action_buttons()

    def _remove_all_missing(self) -> None:
        missing = [r for r in self._results if r["status"] == _STATUS_MISSING]
        if not missing:
            QMessageBox.information(self, "Remove", "No missing archives to remove.")
            return

        count = len(missing)
        reply = QMessageBox.warning(
            self,
            "Remove All Missing",
            f"Permanently remove <b>{count}</b> missing archive record(s) from the database?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._remove_records([r["id"] for r in missing])
        self._results = [r for r in self._results if r["status"] != _STATUS_MISSING]
        self._populate_table(self._results)
        self._log.appendPlainText(f"Removed {count} missing record(s) from the database.")
        self._refresh_action_buttons()

    def _remove_records(self, ids: list[int]) -> None:
        from app.database.db import session_scope
        from app.database.models import STLModel

        try:
            with session_scope() as session:
                for model_id in ids:
                    model = session.query(STLModel).filter(STLModel.id == model_id).first()
                    if model:
                        session.delete(model)
        except Exception as exc:
            self._log.appendPlainText(f"ERROR removing records: {exc}")
            logger.error("Failed to remove DB records: %s", exc, exc_info=True)

    def _refresh_action_buttons(self) -> None:
        has_missing = any(r["status"] == _STATUS_MISSING for r in self._results)
        self._search_btn.setEnabled(has_missing)
        self._remove_all_btn.setEnabled(has_missing)
        self._remove_sel_btn.setEnabled(bool(self._results))

    # ------------------------------------------------------------------
    def _on_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        for worker in (self._verify_worker, self._search_worker):
            if worker and worker.isRunning():
                worker.stop()
                worker.wait(3000)
        super().closeEvent(event)
