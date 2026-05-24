"""
Crawler controls: folder list, path-pattern editor, start/stop, progress, results table.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QSizePolicy,
    QComboBox, QInputDialog, QMessageBox, QToolButton, QToolTip,
    QCheckBox, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtGui import QIcon, QCursor, QBrush, QColor
from pathlib import Path as _Path

_ICONS_DIR = _Path(__file__).parent.parent.parent / "resources" / "icons"

from app.ui.config_dialog import get_max_thumbnails
from app.ui.settings_loader import build_crawler_config
from app.utils.path_pattern import parse_path, PATTERN_HELP, ATTR_LABEL
from app.database.db import session_scope
from app.database.models import CrawledFolder

_SAVED_PATTERNS_KEY      = "crawler/saved_patterns"
_LAST_PATTERN_KEY        = "crawler/last_pattern"
_GENERATE_THUMBNAILS_KEY = "crawler/generate_thumbnails"
_SEP = ":::"

_COLOUR: dict[str, QColor] = {
    "New":     QColor("#2e7d32"),
    "Updated": QColor("#1565c0"),
    "Deleted": QColor("#c62828"),
    "Moved":   QColor("#e65100"),
}
_COL_FILENAME = 0
_COL_PATH     = 1
_COL_CHANGE   = 2
_NCOLS        = 3


def _tbl_item(text: str, colour: QColor | None = None, tooltip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if colour is not None:
        it.setForeground(QBrush(colour))
    if tooltip:
        it.setToolTip(tooltip)
    return it


class CrawlerTab(QWidget):
    """Crawler control panel: folder list, path-pattern editor, progress, results table.

    Signals:
        close_requested: Emitted when the user clicks Close or Run in Background.
        progress_updated: Emitted with (current, total) when progress changes.
        crawl_finished: Emitted when the crawl completes.
    """

    close_requested  = pyqtSignal()
    progress_updated = pyqtSignal(int, int)
    crawl_finished   = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker = None
        self._all_folders: list[tuple[str, str | None]] = []
        self._cnt_new = self._cnt_updated = self._cnt_unchanged = 0
        self._cnt_moved = self._cnt_deleted = 0
        self._build_ui()
        self._load_folders()
        self._load_settings()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # --- Folder list ---
        root.addWidget(QLabel("Folders to crawl:"))

        self._folder_list = QListWidget()
        self._folder_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._folder_list.setMinimumHeight(100)
        self._folder_list.setMaximumHeight(150)
        root.addWidget(self._folder_list)

        sel_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        sel_row.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self._deselect_all)
        sel_row.addWidget(deselect_all_btn)
        sel_row.addStretch()
        self._add_btn = QPushButton("Add Folder…")
        self._add_btn.clicked.connect(self._add_folder)
        sel_row.addWidget(self._add_btn)
        self._remove_btn = QPushButton("Remove Selected")
        self._remove_btn.clicked.connect(self._remove_folder)
        sel_row.addWidget(self._remove_btn)
        root.addLayout(sel_row)

        self._folder_status_label = QLabel()
        root.addWidget(self._folder_status_label)

        # --- Path pattern group ---
        pattern_group = QGroupBox("Parse metadata from path (optional)")
        pattern_layout = QVBoxLayout(pattern_group)

        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel("Saved:"))
        self._saved_combo = QComboBox()
        self._saved_combo.setPlaceholderText("— load a saved pattern —")
        self._saved_combo.activated.connect(self._load_saved_pattern)
        saved_row.addWidget(self._saved_combo, stretch=1)
        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self._save_pattern)
        saved_row.addWidget(save_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_pattern)
        saved_row.addWidget(delete_btn)
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip(PATTERN_HELP)
        help_btn.setStyleSheet(
            "QToolButton { font-weight: bold; border: 1px solid gray;"
            " border-radius: 9px; min-width: 18px; min-height: 18px; }"
        )
        help_btn.enterEvent = lambda e: QToolTip.showText(QCursor.pos(), PATTERN_HELP, help_btn)
        saved_row.addWidget(help_btn)
        pattern_layout.addLayout(saved_row)

        pattern_row = QHBoxLayout()
        self._pattern_edit = QLineEdit()
        self._pattern_edit.setPlaceholderText("/{source}/{author}/{month}/{name}")
        self._pattern_edit.textChanged.connect(self._update_preview)
        pattern_row.addWidget(self._pattern_edit)
        pattern_layout.addLayout(pattern_row)

        file_pick_row = QHBoxLayout()
        file_pick_row.addWidget(QLabel("Test file:"))
        self._example_file_edit = QLineEdit()
        self._example_file_edit.setReadOnly(True)
        self._example_file_edit.setPlaceholderText("click 'Random' to test against a file…")
        self._example_file_edit.textChanged.connect(self._update_preview)
        file_pick_row.addWidget(self._example_file_edit, stretch=1)
        random_file_btn = QPushButton("Random")
        random_file_btn.clicked.connect(self._pick_random_file)
        file_pick_row.addWidget(random_file_btn)
        pattern_layout.addLayout(file_pick_row)

        self._preview_label = QLabel("")
        self._preview_label.setTextFormat(Qt.TextFormat.RichText)
        self._preview_label.setWordWrap(True)
        self._preview_label.setStyleSheet("color: #555; font-style: italic;")
        pattern_layout.addWidget(self._preview_label)

        self._use_pattern_check = QCheckBox("Apply path pattern to metadata extraction")
        self._use_pattern_check.setChecked(True)
        self._use_pattern_check.setEnabled(False)
        self._pattern_edit.textChanged.connect(
            lambda t: self._use_pattern_check.setEnabled(bool(t.strip()))
        )
        pattern_layout.addWidget(self._use_pattern_check)

        root.addWidget(pattern_group)

        # --- Crawl options ---
        self._generate_thumbs_check = QCheckBox("Generate thumbnails")
        self._generate_thumbs_check.setChecked(True)
        root.addWidget(self._generate_thumbs_check)

        self._force_rescan_check = QCheckBox("Force rescan (re-extract all archives regardless of hash)")
        root.addWidget(self._force_rescan_check)

        # --- Metadata overrides ---
        overrides_group = QGroupBox("Override metadata (leave blank to auto-detect)")
        grid = QGridLayout(overrides_group)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        def _override_field(placeholder: str) -> QLineEdit:
            w = QLineEdit()
            w.setPlaceholderText(placeholder)
            return w

        def _add_row(row: int, col: int, label: str, widget: QLineEdit) -> None:
            grid.addWidget(QLabel(label), row, col * 2)
            grid.addWidget(widget, row, col * 2 + 1)

        self._force_creator_edit    = _override_field("e.g. CA3D")
        self._force_source_edit     = _override_field("e.g. Patreon")
        self._force_sculptor_edit   = _override_field("e.g. Jane Doe")
        self._force_scale_edit      = _override_field("e.g. 32mm")
        self._force_category_edit   = _override_field("e.g. Fantasy")
        self._force_collection_edit = _override_field("e.g. Undead Legion")
        self._force_release_edit    = _override_field("e.g. 2024-01 Dragon Set")
        self._force_date_edit       = _override_field("YYYY-MM or YYYY")

        _add_row(0, 0, "Creator:",    self._force_creator_edit)
        _add_row(0, 1, "Source:",     self._force_source_edit)
        _add_row(1, 0, "Sculptor:",   self._force_sculptor_edit)
        _add_row(1, 1, "Scale:",      self._force_scale_edit)
        _add_row(2, 0, "Category:",   self._force_category_edit)
        _add_row(2, 1, "Collection:", self._force_collection_edit)
        _add_row(3, 0, "Release:",    self._force_release_edit)
        _add_row(3, 1, "Date:",       self._force_date_edit)

        root.addWidget(overrides_group)

        # --- Status / progress ---
        self._status_label = QLabel()
        root.addWidget(self._status_label)

        self._current_folder_label = QLabel()
        self._current_folder_label.setWordWrap(True)
        self._current_folder_label.setStyleSheet("color: #555; font-style: italic;")
        root.addWidget(self._current_folder_label)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self._progress)

        # --- Results table ---
        root.addWidget(QLabel("Changes:"))
        self._table = QTableWidget(0, _NCOLS)
        self._table.setHorizontalHeaderLabels(["Filename", "File Path", "Change"])
        self._table.horizontalHeader().setSectionResizeMode(_COL_FILENAME, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_PATH,     QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(_COL_CHANGE,   QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._table)

        self._counts_label = QLabel("New: 0  |  Updated: 0  |  Unchanged: 0  |  Moved: 0  |  Deleted: 0")
        self._counts_label.setStyleSheet(
            "QLabel { "
            "background-color: palette(window); "
            "border-top: 1px solid palette(mid); "
            "padding: 4px 6px; "
            "font-family: monospace; "
            "}"
        )
        root.addWidget(self._counts_label)

        # --- Control buttons ---
        from app.ui.config_dialog import get_color_scheme as _get_cs
        _v = "light" if "light" in _get_cs().lower() else "dark"

        def _icon(name: str) -> QIcon:
            p = _ICONS_DIR / f"{name}_{_v}.svg"
            return QIcon(str(p)) if p.exists() else QIcon()

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton(_icon("crawler_start"), "Start Crawl")
        self._start_btn.clicked.connect(self._start_crawl)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton(_icon("crawler_stop"), "Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_crawl)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()

        self._bg_btn = QPushButton(_icon("crawler_bg"), "Run in Background")
        self._bg_btn.setEnabled(False)
        self._bg_btn.clicked.connect(self.close_requested)
        btn_row.addWidget(self._bg_btn)

        self._close_btn = QPushButton(_icon("crawler_close"), "Close")
        self._close_btn.clicked.connect(self.close_requested)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

        self._refresh_saved_combo()

    # ------------------------------------------------------------------
    # Folder list management
    # ------------------------------------------------------------------

    def _load_folders(self) -> None:
        try:
            with session_scope() as session:
                rows = (
                    session.query(CrawledFolder)
                    .order_by(CrawledFolder.path)
                    .all()
                )
                self._all_folders = [(r.path, r.pattern) for r in rows]
        except Exception:
            self._all_folders = []

        self._folder_list.clear()
        for path, _pattern in self._all_folders:
            self._append_folder_item(path)
        self._refresh_folder_status()

    def _append_folder_item(self, path: str) -> None:
        item = QListWidgetItem(path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self._folder_list.addItem(item)

    def _refresh_folder_status(self) -> None:
        n = len(self._all_folders)
        if n == 0:
            self._folder_status_label.setText(
                "No folders yet. Use 'Add Folder…' to add a folder to crawl."
            )
            self._start_btn.setEnabled(False)
        else:
            self._folder_status_label.setText(f"{n} folder{'s' if n != 1 else ''} configured.")
            self._start_btn.setEnabled(True)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add Folder to Crawl")
        if not path:
            return
        if any(p == path for p, _ in self._all_folders):
            QMessageBox.information(self, "Already Listed", f"{path}\nis already in the list.")
            return
        try:
            with session_scope() as session:
                existing = session.query(CrawledFolder).filter_by(path=path).first()
                if not existing:
                    session.add(CrawledFolder(path=path, pattern=None))
        except Exception:
            pass
        self._all_folders.append((path, None))
        self._append_folder_item(path)
        self._refresh_folder_status()

    def _remove_folder(self) -> None:
        row = self._folder_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "No Selection", "Select a folder in the list first.")
            return
        path, _ = self._all_folders[row]
        reply = QMessageBox.question(
            self,
            "Remove Folder",
            f"Remove this folder from the tracked list?\n\n{path}\n\n"
            "Existing models in the database will not be deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            with session_scope() as session:
                record = session.query(CrawledFolder).filter_by(path=path).first()
                if record:
                    session.delete(record)
        except Exception:
            pass
        del self._all_folders[row]
        self._folder_list.takeItem(row)
        self._refresh_folder_status()

    def _select_all(self) -> None:
        for i in range(self._folder_list.count()):
            self._folder_list.item(i).setCheckState(Qt.CheckState.Checked)

    def _deselect_all(self) -> None:
        for i in range(self._folder_list.count()):
            self._folder_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _selected_folders(self) -> list[tuple[str, str | None]]:
        global_pattern = self._pattern_edit.text().strip()
        use_global = bool(global_pattern) and self._use_pattern_check.isChecked()
        checked = []
        for i in range(self._folder_list.count()):
            item = self._folder_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                path, stored_pattern = self._all_folders[i]
                pattern = global_pattern if use_global else stored_pattern
                checked.append((path, pattern))
        return checked

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        s = QSettings()
        self._pattern_edit.setText(s.value(_LAST_PATTERN_KEY, ""))
        self._generate_thumbs_check.setChecked(
            s.value(_GENERATE_THUMBNAILS_KEY, True, type=bool)
        )

    def _save_settings(self) -> None:
        s = QSettings()
        s.setValue(_LAST_PATTERN_KEY, self._pattern_edit.text().strip())
        s.setValue(_GENERATE_THUMBNAILS_KEY, self._generate_thumbs_check.isChecked())

    # ------------------------------------------------------------------
    # Saved-pattern helpers
    # ------------------------------------------------------------------

    def _saved_patterns(self) -> list[tuple[str, str]]:
        raw = QSettings().value(_SAVED_PATTERNS_KEY, [])
        if isinstance(raw, str):
            raw = [raw]
        result = []
        for entry in raw:
            if _SEP in entry:
                name, _, pattern = entry.partition(_SEP)
                result.append((name.strip(), pattern.strip()))
        return result

    def _write_saved_patterns(self, patterns: list[tuple[str, str]]) -> None:
        entries = [f"{name}{_SEP}{pat}" for name, pat in patterns]
        QSettings().setValue(_SAVED_PATTERNS_KEY, entries)

    def _refresh_saved_combo(self) -> None:
        self._saved_combo.clear()
        for name, _ in self._saved_patterns():
            self._saved_combo.addItem(name)

    def _load_saved_pattern(self, index: int) -> None:
        patterns = self._saved_patterns()
        if 0 <= index < len(patterns):
            self._pattern_edit.setText(patterns[index][1])

    def _save_pattern(self) -> None:
        pattern = self._pattern_edit.text().strip()
        if not pattern:
            QMessageBox.information(self, "Save Pattern", "Enter a pattern first.")
            return
        name, ok = QInputDialog.getText(self, "Save Pattern", "Pattern name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        patterns = self._saved_patterns()
        patterns = [(n, p) for n, p in patterns if n != name]
        patterns.append((name, pattern))
        self._write_saved_patterns(patterns)
        self._refresh_saved_combo()

    def _delete_pattern(self) -> None:
        idx = self._saved_combo.currentIndex()
        if idx < 0:
            return
        patterns = self._saved_patterns()
        if 0 <= idx < len(patterns):
            patterns.pop(idx)
            self._write_saved_patterns(patterns)
            self._refresh_saved_combo()

    # ------------------------------------------------------------------
    # Live preview
    # ------------------------------------------------------------------

    def _pick_random_file(self) -> None:
        import random
        folder = None
        for i in range(self._folder_list.count()):
            item = self._folder_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                folder = self._all_folders[i][0]
                break
        if not folder:
            return
        from app.crawler.archive import is_archive
        files = [str(p) for p in Path(folder).rglob("*") if is_archive(p)]
        if files:
            self._example_file_edit.setText(random.choice(files))

    def _update_preview(self) -> None:
        pattern = self._pattern_edit.text().strip()
        example = self._example_file_edit.text().strip()
        if not pattern or not example:
            self._preview_label.clear()
            return

        source = None
        for i in range(self._folder_list.count()):
            item = self._folder_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                source = self._all_folders[i][0]
                break

        if source and example.startswith(source):
            rel = example[len(source):]
            if not rel.startswith("/"):
                rel = "/" + rel
            match_path = rel
        else:
            match_path = example

        extracted = parse_path(pattern, match_path)
        if not extracted:
            self._preview_label.setText("<i>no match</i>")
            return

        parts = [
            f"<b>{ATTR_LABEL.get(attr, attr)}:</b>&nbsp;{val}"
            for attr, val in extracted.items()
        ]
        self._preview_label.setText("&nbsp;&nbsp;→&nbsp;&nbsp;".join(parts))

    # ------------------------------------------------------------------
    # Results table helpers
    # ------------------------------------------------------------------

    def _add_table_row(self, filename: str, path: str, change: str, tooltip: str = "") -> None:
        colour = _COLOUR.get(change)
        self._table.setSortingEnabled(False)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, _COL_FILENAME, _tbl_item(filename, colour, tooltip))
        self._table.setItem(row, _COL_PATH,     _tbl_item(path,     colour, tooltip))
        self._table.setItem(row, _COL_CHANGE,   _tbl_item(change,   colour, tooltip))
        self._table.setSortingEnabled(True)

    def _refresh_counts_label(self) -> None:
        self._counts_label.setText(
            f"New: {self._cnt_new}  |  Updated: {self._cnt_updated}"
            f"  |  Unchanged: {self._cnt_unchanged}"
            f"  |  Moved: {self._cnt_moved}  |  Deleted: {self._cnt_deleted}"
        )

    # ------------------------------------------------------------------
    # Crawl control
    # ------------------------------------------------------------------

    def _start_crawl(self) -> None:
        from app.ui.update_crawled_folders_dialog import _UpdateAllWorker

        folders = self._selected_folders()
        if not folders:
            self._status_label.setText("No folders selected — tick at least one folder.")
            return

        self._save_settings()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setSortingEnabled(True)
        self._progress.setValue(0)
        self._cnt_new = self._cnt_updated = self._cnt_unchanged = 0
        self._cnt_moved = self._cnt_deleted = 0
        self._refresh_counts_label()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._bg_btn.setEnabled(True)
        self._folder_list.setEnabled(False)
        self._add_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)

        n = len(folders)
        self._status_label.setText(f"Crawling {n} folder{'s' if n != 1 else ''}…")

        max_thumbs = get_max_thumbnails() if self._generate_thumbs_check.isChecked() else 0
        config = build_crawler_config(
            max_thumbnails=max_thumbs,
            force_rescan=self._force_rescan_check.isChecked(),
        )
        config.log_unchanged    = False
        config.force_creator    = self._force_creator_edit.text().strip()
        config.force_source     = self._force_source_edit.text().strip()
        config.force_sculptor   = self._force_sculptor_edit.text().strip()
        config.force_scale      = self._force_scale_edit.text().strip()
        config.force_category   = self._force_category_edit.text().strip()
        config.force_collection = self._force_collection_edit.text().strip()
        config.force_release    = self._force_release_edit.text().strip()
        config.force_date       = self._force_date_edit.text().strip()

        self._worker = _UpdateAllWorker(folders, config=config, parent=self)
        self._worker.folder_started.connect(self._on_folder_started)
        self._worker.progress.connect(self._on_progress)
        self._worker.counts_changed.connect(self._on_counts_changed)
        self._worker.archive_result.connect(self._on_archive_result)
        self._worker.file_moved.connect(self._on_file_moved)
        self._worker.file_deleted.connect(self._on_file_deleted)
        self._worker.all_finished.connect(self._on_finished)
        self._worker.start()

    def _stop_crawl(self) -> None:
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    def _on_folder_started(self, path: str, idx: int, total: int) -> None:
        self._status_label.setText(f"Folder {idx} / {total}")
        self._current_folder_label.setText(path)
        self._progress.setMaximum(0)
        self._progress.setValue(0)

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")
        self.progress_updated.emit(current, total)

    def _on_counts_changed(self, new: int, updated: int, unchanged: int) -> None:
        self._cnt_new, self._cnt_updated, self._cnt_unchanged = new, updated, unchanged
        self._refresh_counts_label()

    def _on_archive_result(self, folder: str, filename: str, status: str) -> None:
        if status not in ("new", "updated"):
            return
        change = "New" if status == "new" else "Updated"
        self._add_table_row(filename, str(Path(folder) / filename), change)

    def _on_file_moved(self, old_path: str, new_path: str) -> None:
        filename = Path(old_path).name
        tooltip = f"From: {old_path}\nTo:   {new_path}"
        self._add_table_row(filename, new_path, "Moved", tooltip)
        self._cnt_moved += 1
        self._refresh_counts_label()

    def _on_file_deleted(self, path: str) -> None:
        self._add_table_row(Path(path).name, path, "Deleted")
        self._cnt_deleted += 1
        self._refresh_counts_label()

    def _on_finished(self) -> None:
        n = self._folder_list.count()
        self._status_label.setText(
            f"Complete — {n} folder{'s' if n != 1 else ''} processed."
        )
        self._current_folder_label.clear()
        self._progress.setMaximum(1)
        self._progress.setValue(1)
        self._progress.setFormat("done")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._bg_btn.setEnabled(False)
        self._folder_list.setEnabled(True)
        self._add_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)
        self._worker = None
        self.crawl_finished.emit()
