"""
Dialog that re-crawls stored CrawledFolder records to pick up new and
modified archives, using the pattern saved with each folder.

The user can select which folders to re-crawl via a checkbox list, add new
folders, or remove existing ones from the tracked list.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.crawler.crawler import CrawlerWorker
from app.database.db import session_scope
from app.database.models import CrawledFolder, STLModel

# ── Change-type colours ───────────────────────────────────────────────────────
_COLOUR: dict[str, QColor] = {
    "New":     QColor("#2e7d32"),   # dark green
    "Updated": QColor("#1565c0"),   # dark blue
    "Deleted": QColor("#c62828"),   # dark red
    "Moved":   QColor("#e65100"),   # dark orange
}

_COL_FILENAME = 0
_COL_CHANGE   = 1
_COL_FOLDER   = 2
_NCOLS        = 3


def _tbl_item(text: str, colour: QColor | None = None,
              tooltip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if colour is not None:
        it.setForeground(QBrush(colour))
    if tooltip:
        it.setToolTip(tooltip)
    return it


class _UpdateAllWorker(QThread):
    """Runs one CrawlerWorker per folder sequentially, waiting for each to finish.

    After all per-folder crawls complete, a cross-folder reconciliation pass
    matches ``is_missing=True`` records against new archives discovered in other
    folders during this run, treating hash matches as file moves.  Any remaining
    in-scope ``is_missing`` records are then deleted.
    """

    log            = pyqtSignal(str)
    phase          = pyqtSignal(str)              # high-level step description
    folder_started = pyqtSignal(str, int, int)   # path, index, total
    progress       = pyqtSignal(int, int)         # current archive, total archives
    counts_changed = pyqtSignal(int, int, int)    # new, updated, unchanged (accumulated)
    archive_result = pyqtSignal(str, str, str)    # folder_path, full archive path, "new"|"updated"
    file_moved     = pyqtSignal(str, str)         # old_path, new_path
    file_deleted   = pyqtSignal(str)              # path
    all_finished   = pyqtSignal()

    def __init__(self, folders: list[tuple[str, str | None]], config, parent=None):
        super().__init__(parent)
        self._folders = folders
        self._config  = config
        self._current_worker: CrawlerWorker | None = None
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True
        if self._current_worker is not None:
            self._current_worker.stop()

    def run(self) -> None:
        # Enable deferred deletion so that in-scope missing records are only
        # marked is_missing=True during each per-folder crawl.  This lets the
        # cross-folder reconciliation pass (below) detect moves between folders
        # before committing to deletion.
        self._config.defer_missing_deletion = True

        total_new = total_updated = total_unchanged = 0
        all_new_paths: set[str] = set()
        crawled_folder_paths: list[str] = []
        total = len(self._folders)
        for idx, (path, pattern) in enumerate(self._folders, start=1):
            if self._stop_requested:
                break
            self.folder_started.emit(path, idx, total)
            worker = CrawlerWorker(path, pattern=pattern or "", config=self._config)
            self._current_worker = worker
            crawled_folder_paths.append(path)

            # Capture offsets so lambdas close over the right values.
            _off_n, _off_u, _off_unc = total_new, total_updated, total_unchanged
            _path = path

            # DirectConnection bypasses _UpdateAllWorker's blocked event loop
            # so signals from the nested worker reach the main thread directly.
            worker.log.connect(self.log, Qt.ConnectionType.DirectConnection)
            worker.phase.connect(self.phase, Qt.ConnectionType.DirectConnection)
            worker.progress.connect(self.progress, Qt.ConnectionType.DirectConnection)
            worker.counts_changed.connect(
                lambda n, u, unc, on=_off_n, ou=_off_u, ounc=_off_unc:
                    self.counts_changed.emit(on + n, ou + u, ounc + unc),
                Qt.ConnectionType.DirectConnection,
            )
            worker.archive_result.connect(
                lambda fname, status, fp=_path:
                    self.archive_result.emit(fp, fname, status),
                Qt.ConnectionType.DirectConnection,
            )
            worker.file_moved.connect(self.file_moved, Qt.ConnectionType.DirectConnection)
            worker.file_deleted.connect(self.file_deleted, Qt.ConnectionType.DirectConnection)

            worker.start()
            worker.wait()

            # Accumulate new archive paths from this worker for cross-folder matching.
            all_new_paths.update(worker._new_archive_paths)

            total_new       += worker._stats_new
            total_updated   += worker._stats_updated
            total_unchanged += worker._stats_unchanged
            self._current_worker = None

        if not self._stop_requested and len(crawled_folder_paths) > 1:
            self.phase.emit("Cross-folder move detection…")
            self._cross_folder_reconcile(all_new_paths)
            self.phase.emit("Cleaning up deleted files…")
            self._cleanup_deferred_deletions(crawled_folder_paths)

        self.all_finished.emit()

    def _cross_folder_reconcile(self, all_new_paths: set[str]) -> None:
        """Match is_missing records against new archives from other crawled folders.

        Uses the same strategy as CrawlerWorker._reconcile_moved_files: redirect
        the old (missing) record's path to the new location so all user-edited
        metadata is preserved, then delete the freshly-created (shallow) record.
        Hash collisions on either side are excluded to avoid mis-attribution.
        """
        from app.utils.thumbnailer import delete_thumbnail

        if not all_new_paths:
            return

        with session_scope() as session:
            missing_rows = session.query(
                STLModel.id,
                STLModel.archive_path,
                STLModel.archive_hash,
            ).filter(STLModel.is_missing == True).all()  # noqa: E712

            if not missing_rows:
                return

            # Build hash → missing-id index, tracking collisions.
            hash_to_missing_id: dict[str, int]  = {}
            missing_hash_collision: set[str]    = set()
            for mid, _mpath, mhash in missing_rows:
                if not mhash:
                    continue
                if mhash in hash_to_missing_id:
                    missing_hash_collision.add(mhash)
                else:
                    hash_to_missing_id[mhash] = mid

            candidate_hashes = [
                h for h in hash_to_missing_id if h not in missing_hash_collision
            ]
            if not candidate_hashes:
                return

            new_models = {
                m.archive_path: m
                for m in session.query(STLModel)
                    .filter(STLModel.archive_path.in_(list(all_new_paths)))
                    .all()
            }

            # Build hash → new-path index, tracking collisions.
            hash_to_new_path: dict[str, str] = {}
            new_hash_collision: set[str]     = set()
            for npath, nm in new_models.items():
                h = nm.archive_hash
                if not h:
                    continue
                if h in hash_to_new_path:
                    new_hash_collision.add(h)
                else:
                    hash_to_new_path[h] = npath

            used_new: set[str] = set()
            matched = 0

            for h, missing_id in hash_to_missing_id.items():
                if h in missing_hash_collision or h in new_hash_collision:
                    continue
                new_path = hash_to_new_path.get(h)
                if new_path is None or new_path in used_new:
                    continue

                old_model = session.get(STLModel, missing_id)
                new_model = new_models.get(new_path)
                if old_model is None or new_model is None:
                    continue

                old_path = old_model.archive_path

                # Capture filesystem metadata from the new record before deletion.
                new_archive_path  = new_model.archive_path
                new_archive_hash  = new_model.archive_hash
                new_archive_size  = new_model.archive_size
                new_archive_mtime = new_model.archive_mtime_ns
                new_compression   = new_model.compression_rate

                # Delete new model's thumbnails; old record keeps its existing ones.
                for img in list(new_model.images):
                    if img.thumbnail_path:
                        delete_thumbnail(img.thumbnail_path)
                    session.delete(img)
                session.flush()
                for stl in list(new_model.stl_files):
                    session.delete(stl)
                session.flush()
                session.delete(new_model)
                session.flush()

                # Redirect old record to new location.
                old_model.archive_path     = new_archive_path
                old_model.archive_hash     = new_archive_hash
                old_model.archive_size     = new_archive_size
                old_model.archive_mtime_ns = new_archive_mtime
                old_model.archive_folder   = str(Path(new_archive_path).parent)
                old_model.is_missing       = False
                if new_compression is not None:
                    old_model.compression_rate = new_compression
                session.flush()

                matched += 1
                used_new.add(new_path)

                filename = Path(old_path).name
                self.log.emit(f"Cross-folder move: {filename}")
                self.file_moved.emit(old_path, new_archive_path)

            if matched:
                self.log.emit(f"Detected {matched} cross-folder move(s).")

    def _cleanup_deferred_deletions(self, crawled_folder_paths: list[str]) -> None:
        """Delete in-scope is_missing records that were not matched as moves.

        These are archives that were genuinely deleted (not moved) from one of
        the crawled folders during this update run.
        """
        from app.utils.thumbnailer import delete_thumbnail

        folder_roots = [Path(p) for p in crawled_folder_paths]

        with session_scope() as session:
            missing_models = (
                session.query(STLModel)
                .filter(STLModel.is_missing == True)  # noqa: E712
                .all()
            )

            deleted = 0
            for model in missing_models:
                if not model.archive_path:
                    continue
                try:
                    in_scope = any(
                        Path(model.archive_path).is_relative_to(root)
                        for root in folder_roots
                    )
                except ValueError:
                    in_scope = False
                if not in_scope:
                    continue

                old_path = model.archive_path
                for img in list(model.images):
                    if img.thumbnail_path:
                        delete_thumbnail(img.thumbnail_path)
                    session.delete(img)
                session.flush()
                for stl in list(model.stl_files):
                    session.delete(stl)
                session.flush()
                session.delete(model)
                deleted += 1
                self.file_deleted.emit(old_path)

            if deleted:
                self.log.emit(
                    f"Deleted {deleted} record(s) removed from crawled folders."
                )


class UpdateCrawledFoldersDialog(QDialog):
    """Shows a folder checklist and crawl progress for re-crawling stored CrawledFolder entries."""

    # Forwarding signals so the main window can track progress before exec() returns.
    crawl_started  = pyqtSignal(int, int)  # folder_idx, folder_total
    crawl_progress = pyqtSignal(int, int)  # current_archive, total_archives
    crawl_finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Update Crawled Folders"))
        self.resize(760, 680)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._worker: _UpdateAllWorker | None = None
        self._background_worker: _UpdateAllWorker | None = None  # set when user runs in bg
        self._all_folders: list[tuple[str, str | None]] = []
        self._background_mode = False
        # Running counters for the counts label
        self._cnt_new = self._cnt_updated = self._cnt_unchanged = 0
        self._cnt_moved = self._cnt_deleted = 0
        self._build_ui()
        self._load_folders()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Folder selection list ---
        layout.addWidget(QLabel(self.tr("Select folders to re-crawl:")))

        self._folder_list = QListWidget()
        self._folder_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._folder_list.setMinimumHeight(140)
        layout.addWidget(self._folder_list)

        sel_row = QHBoxLayout()

        select_all_btn = QPushButton(self.tr("Select All"))
        select_all_btn.clicked.connect(self._select_all)
        sel_row.addWidget(select_all_btn)

        deselect_all_btn = QPushButton(self.tr("Deselect All"))
        deselect_all_btn.clicked.connect(self._deselect_all)
        sel_row.addWidget(deselect_all_btn)

        sel_row.addStretch()

        self._add_btn = QPushButton(self.tr("Add Folder…"))
        self._add_btn.clicked.connect(self._add_folder)
        sel_row.addWidget(self._add_btn)

        self._remove_btn = QPushButton(self.tr("Remove Selected"))
        self._remove_btn.clicked.connect(self._remove_folder)
        sel_row.addWidget(self._remove_btn)

        layout.addLayout(sel_row)

        # --- Run status ---
        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        self._folder_label = QLabel()
        self._folder_label.setWordWrap(True)
        self._folder_label.setStyleSheet("color: #555; font-style: italic;")
        layout.addWidget(self._folder_label)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        layout.addWidget(self._progress)

        # --- Results table ---
        layout.addWidget(QLabel(self.tr("Changes:")))
        self._table = QTableWidget(0, _NCOLS)
        self._table.setHorizontalHeaderLabels([self.tr("Filename"), self.tr("Change"), self.tr("Folder")])
        self._table.horizontalHeader().setSectionResizeMode(_COL_FILENAME, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_CHANGE,   QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_FOLDER,   QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._table)

        self._counts_label = QLabel(self.tr("New: 0  |  Updated: 0  |  Unchanged: 0  |  Moved: 0  |  Deleted: 0"))
        self._counts_label.setStyleSheet(
            "QLabel { "
            "background-color: palette(window); "
            "border-top: 1px solid palette(mid); "
            "padding: 4px 6px; "
            "font-family: monospace; "
            "}"
        )
        layout.addWidget(self._counts_label)

        style = QApplication.style()
        SP = style.StandardPixmap
        btn_row = QHBoxLayout()

        self._start_btn = QPushButton(style.standardIcon(SP.SP_MediaPlay), self.tr("Start"))
        self._start_btn.clicked.connect(self._start)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton(style.standardIcon(SP.SP_MediaStop), self.tr("Stop"))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()

        self._bg_btn = QPushButton(style.standardIcon(SP.SP_TitleBarMinButton), self.tr("Run in Background"))
        self._bg_btn.setEnabled(False)
        self._bg_btn.setToolTip(
            self.tr("Close this dialog and continue the update in the background.\n"
            "Progress will be shown in the status bar.")
        )
        self._bg_btn.clicked.connect(self._run_in_background)
        btn_row.addWidget(self._bg_btn)

        self._close_btn = QPushButton(style.standardIcon(SP.SP_DialogCloseButton), self.tr("Close"))
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Folder list management
    # ------------------------------------------------------------------

    def _load_folders(self) -> None:
        with session_scope() as session:
            rows = (
                session.query(CrawledFolder)
                .order_by(CrawledFolder.path)
                .all()
            )
            self._all_folders = [(r.path, r.pattern) for r in rows]

        self._folder_list.clear()
        for path, _pattern in self._all_folders:
            self._append_list_item(path)

        self._refresh_status_label()

    def _append_list_item(self, path: str) -> None:
        item = QListWidgetItem(path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self._folder_list.addItem(item)

    def _refresh_status_label(self) -> None:
        n = len(self._all_folders)
        if n == 0:
            self._status_label.setText(
                self.tr("No crawled folders stored yet. "
                "Run the Crawler on a folder first to register it, or use Add Folder…")
            )
            self._start_btn.setEnabled(False)
        else:
            self._status_label.setText(f"{n} folder{'s' if n != 1 else ''} available.")
            self._start_btn.setEnabled(True)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr("Add Folder to Re-crawl List"))
        if not path:
            return
        if any(p == path for p, _ in self._all_folders):
            QMessageBox.information(self, self.tr("Already Listed"), f"{path}\nis already in the list.")
            return
        self._all_folders.append((path, None))
        self._append_list_item(path)
        self._refresh_status_label()

    def _remove_folder(self) -> None:
        row = self._folder_list.currentRow()
        if row < 0:
            QMessageBox.information(self, self.tr("No Selection"), self.tr("Select a folder in the list first."))
            return
        path, _ = self._all_folders[row]
        reply = QMessageBox.question(
            self,
            self.tr("Remove Folder"),
            f"Remove this folder from the tracked list?\n\n{path}\n\n"
            "Existing models in the database will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        with session_scope() as session:
            record = session.query(CrawledFolder).filter_by(path=path).first()
            if record:
                session.delete(record)
        del self._all_folders[row]
        self._folder_list.takeItem(row)
        self._refresh_status_label()

    def _select_all(self) -> None:
        for i in range(self._folder_list.count()):
            self._folder_list.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self._folder_list.count()):
            self._folder_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _selected_folders(self) -> list[tuple[str, str | None]]:
        checked: list[tuple[str, str | None]] = []
        for i in range(self._folder_list.count()):
            item = self._folder_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked.append(self._all_folders[i])
        return checked

    # ------------------------------------------------------------------
    # Crawl control
    # ------------------------------------------------------------------

    def _start(self) -> None:
        from app.ui.config_dialog import get_max_thumbnails
        from app.ui.settings_loader import build_crawler_config

        folders = self._selected_folders()
        if not folders:
            self._status_label.setText(self.tr("No folders selected — tick at least one folder to re-crawl."))
            return

        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setSortingEnabled(True)
        self._progress.setValue(0)
        self._cnt_new = self._cnt_updated = self._cnt_unchanged = 0
        self._cnt_moved = self._cnt_deleted = 0
        self._counts_label.setText(self.tr("New: 0  |  Updated: 0  |  Unchanged: 0  |  Moved: 0  |  Deleted: 0"))
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._bg_btn.setEnabled(True)
        self._close_btn.setEnabled(False)
        self._folder_list.setEnabled(False)
        self._add_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)

        n = len(folders)
        self._status_label.setText(f"Re-crawling {n} folder{'s' if n != 1 else ''}…")

        config = build_crawler_config(max_thumbnails=get_max_thumbnails())
        config.log_unchanged = False
        self._worker = _UpdateAllWorker(folders, config=config, parent=self)
        self._worker.folder_started.connect(self._on_folder_started)
        self._worker.progress.connect(self._on_progress)
        self._worker.counts_changed.connect(self._on_counts_changed)
        self._worker.archive_result.connect(self._on_archive_result)
        self._worker.file_moved.connect(self._on_file_moved)
        self._worker.file_deleted.connect(self._on_file_deleted)
        self._worker.all_finished.connect(self._on_finished)
        self._worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    def _run_in_background(self) -> None:
        """Detach the running worker from this dialog and close it.

        The worker is reparented to None so it outlives the dialog.  The main
        window reads ``_background_worker`` after exec() returns and wires the
        remaining signals to the status bar.

        NOTE: accept() only calls hide(), not closeEvent(), so worker detachment
        must happen here rather than in closeEvent.
        """
        if self._worker is None:
            if self._background_mode:
                self.hide()
            return
        for sig, slot in (
            (self._worker.folder_started, self._on_folder_started),
            (self._worker.progress,       self._on_progress),
            (self._worker.counts_changed, self._on_counts_changed),
            (self._worker.archive_result, self._on_archive_result),
            (self._worker.file_moved,     self._on_file_moved),
            (self._worker.file_deleted,   self._on_file_deleted),
            (self._worker.all_finished,   self._on_finished),
        ):
            try:
                sig.disconnect(slot)
            except RuntimeError:
                pass
        self._worker.setParent(None)
        self._background_worker = self._worker
        self._worker = None
        self._background_mode = True
        self.accept()

    # ------------------------------------------------------------------
    # Table helpers
    # ------------------------------------------------------------------

    def _add_table_row(self, filename: str, change: str, folder: str,
                       tooltip: str = "") -> None:
        colour = _COLOUR.get(change)
        # Disable sorting while populating all three cells; otherwise Qt re-sorts
        # after the first setItem call, moving the new row to a different index,
        # and the remaining setItem calls land on the wrong row (empty cells).
        self._table.setSortingEnabled(False)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, _COL_FILENAME, _tbl_item(filename, colour, tooltip))
        self._table.setItem(row, _COL_CHANGE,   _tbl_item(change,   colour, tooltip))
        self._table.setItem(row, _COL_FOLDER,   _tbl_item(folder,   colour, tooltip))
        self._table.setSortingEnabled(True)

    def _refresh_counts_label(self) -> None:
        self._counts_label.setText(
            f"New: {self._cnt_new}  |  Updated: {self._cnt_updated}"
            f"  |  Unchanged: {self._cnt_unchanged}"
            f"  |  Moved: {self._cnt_moved}  |  Deleted: {self._cnt_deleted}"
        )

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    def _on_folder_started(self, path: str, idx: int, total: int) -> None:
        self._status_label.setText(f"Folder {idx} / {total}")
        self._folder_label.setText(path)
        self._progress.setMaximum(0)
        self._progress.setValue(0)
        self.crawl_started.emit(idx, total)

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")
        self.crawl_progress.emit(current, total)

    def _on_counts_changed(self, new: int, updated: int, unchanged: int) -> None:
        self._cnt_new, self._cnt_updated, self._cnt_unchanged = new, updated, unchanged
        self._refresh_counts_label()

    def _on_archive_result(self, folder: str, archive_path: str, status: str) -> None:
        if status not in ("new", "updated"):
            return
        change = "New" if status == "new" else "Updated"
        p = Path(archive_path)
        self._add_table_row(p.name, change, str(p.parent))

    def _on_file_moved(self, old_path: str, new_path: str) -> None:
        filename = Path(old_path).name
        tooltip = f"From: {old_path}\nTo:   {new_path}"
        self._add_table_row(filename, "Moved", str(Path(new_path).parent), tooltip)
        self._cnt_moved += 1
        self._refresh_counts_label()

    def _on_file_deleted(self, path: str) -> None:
        p = Path(path)
        self._add_table_row(p.name, "Deleted", str(p.parent))
        self._cnt_deleted += 1
        self._refresh_counts_label()

    def _on_finished(self) -> None:
        n = self._folder_list.count()
        self._status_label.setText(
            f"Update complete — {n} folder{'s' if n != 1 else ''} processed."
        )
        self._folder_label.clear()
        self._progress.setMaximum(1)
        self._progress.setValue(1)
        self._progress.setFormat("done")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._bg_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._folder_list.setEnabled(True)
        self._add_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)
        self._worker = None
        self._background_mode = False  # allow normal close once the run is done
        self.crawl_finished.emit()

    def changeEvent(self, event) -> None:
        from PyQt6.QtCore import QEvent
        if (event.type() == QEvent.Type.WindowStateChange
                and bool(self.windowState() & Qt.WindowState.WindowMinimized)
                and self._bg_btn.isEnabled()):
            self.setWindowState(Qt.WindowState.WindowNoState)
            self._run_in_background()
            return
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait()
        elif self._background_mode:
            # Worker is owned by the main window — just hide so it can be re-shown.
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)
