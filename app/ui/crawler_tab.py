"""
Crawler controls: folder list, path-pattern editor, start/stop, progress, results table.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QSizePolicy, QSplitter,
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


class _CollapsibleSection(QWidget):
    """Section with an arrow-toggle header and a hideable content area."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._toggle = QToolButton()
        self._toggle.setStyleSheet("QToolButton { border: none; font-weight: bold; }")
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.toggled.connect(self._on_toggled)
        self._toggle.toggled.connect(self.toggled)

        self._content = QWidget()
        self._content.setVisible(False)
        self.content_layout = QVBoxLayout(self._content)
        self.content_layout.setContentsMargins(18, 4, 4, 4)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._toggle)
        lay.addWidget(self._content)

    def _on_toggled(self, on: bool) -> None:
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        self._content.setVisible(on)

    def set_expanded(self, on: bool) -> None:
        self._toggle.setChecked(on)

    def add_layout(self, layout) -> None:
        self.content_layout.addLayout(layout)

    def add_widget(self, widget: QWidget) -> None:
        self.content_layout.addWidget(widget)


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

        # --- Folder list (capped at 5 visible rows, scroll past that) ---
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setChildrenCollapsible(False)
        root.addWidget(self._splitter, stretch=1)

        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)

        top_layout.addWidget(QLabel(self.tr("Folders to crawl:")))

        self._folder_list = QListWidget()
        self._folder_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._folder_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        top_layout.addWidget(self._folder_list, stretch=1)

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
        top_layout.addLayout(sel_row)

        self._folder_status_label = QLabel()
        top_layout.addWidget(self._folder_status_label)

        self._splitter.addWidget(top_widget)

        bottom_widget = QSplitter(Qt.Orientation.Vertical)
        bottom_widget.setChildrenCollapsible(False)

        upper_widget = QWidget()
        bottom_layout = QVBoxLayout(upper_widget)
        bottom_layout.setContentsMargins(0, 8, 0, 0)
        bottom_layout.setSpacing(8)

        # --- Path pattern section (collapsible; header is the apply toggle) ---
        self._use_pattern_check = QCheckBox(self.tr("Apply path pattern to metadata extraction"))
        self._use_pattern_check.setChecked(True)
        bottom_layout.addWidget(self._use_pattern_check)

        pattern_group = QWidget()
        pattern_group.setVisible(True)
        self._use_pattern_check.toggled.connect(pattern_group.setVisible)
        pattern_layout = QVBoxLayout(pattern_group)
        pattern_layout.setContentsMargins(18, 0, 0, 0)

        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel(self.tr("Saved:")))
        self._saved_combo = QComboBox()
        self._saved_combo.setPlaceholderText(self.tr("— load a saved pattern —"))
        self._saved_combo.activated.connect(self._load_saved_pattern)
        saved_row.addWidget(self._saved_combo, stretch=1)
        save_btn = QPushButton(self.tr("Save…"))
        save_btn.clicked.connect(self._save_pattern)
        saved_row.addWidget(save_btn)
        delete_btn = QPushButton(self.tr("Delete"))
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
        self._pattern_edit.setPlaceholderText(self.tr("/{source}/{author}/{month}/{name}"))
        self._pattern_edit.textChanged.connect(self._update_preview)
        pattern_row.addWidget(self._pattern_edit)
        pattern_layout.addLayout(pattern_row)

        file_pick_row = QHBoxLayout()
        file_pick_row.addWidget(QLabel(self.tr("Test file:")))
        self._example_file_edit = QLineEdit()
        self._example_file_edit.setReadOnly(True)
        self._example_file_edit.setPlaceholderText(self.tr("click 'Random' to test against a file…"))
        self._example_file_edit.textChanged.connect(self._update_preview)
        file_pick_row.addWidget(self._example_file_edit, stretch=1)
        random_file_btn = QPushButton(self.tr("Random"))
        random_file_btn.clicked.connect(self._pick_random_file)
        file_pick_row.addWidget(random_file_btn)
        pattern_layout.addLayout(file_pick_row)

        self._preview_label = QLabel("")
        self._preview_label.setTextFormat(Qt.TextFormat.RichText)
        self._preview_label.setWordWrap(True)
        self._preview_label.setStyleSheet("color: #555; font-style: italic;")
        pattern_layout.addWidget(self._preview_label)

        bottom_layout.addWidget(pattern_group)

        # --- Crawl options ---
        self._generate_thumbs_check = QCheckBox(self.tr("Generate thumbnails"))
        self._generate_thumbs_check.setChecked(True)
        bottom_layout.addWidget(self._generate_thumbs_check)

        self._force_rescan_check = QCheckBox(self.tr("Force rescan (re-extract all archives regardless of hash)"))
        bottom_layout.addWidget(self._force_rescan_check)

        # --- Metadata overrides (collapsible) ---
        overrides_section = _CollapsibleSection("Override metadata (leave blank to auto-detect)")
        overrides_inner = QWidget()
        grid = QGridLayout(overrides_inner)
        grid.setContentsMargins(0, 0, 0, 0)
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

        overrides_section.add_widget(overrides_inner)
        bottom_layout.addWidget(overrides_section)

        # --- Status / progress ---
        self._status_label = QLabel()
        bottom_layout.addWidget(self._status_label)

        self._current_folder_label = QLabel()
        self._current_folder_label.setWordWrap(True)
        self._current_folder_label.setStyleSheet("color: #555; font-style: italic;")
        bottom_layout.addWidget(self._current_folder_label)

        self._phase_label = QLabel()
        self._phase_label.setWordWrap(True)
        self._phase_label.setStyleSheet("color: #888; font-style: italic;")
        bottom_layout.addWidget(self._phase_label)

        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bottom_layout.addWidget(self._progress)

        # --- Results table (in collapsible section, below an inner splitter) ---
        self._changes_section = _CollapsibleSection("Changes")
        self._changes_section.set_expanded(True)
        self._changes_prev_height = 0
        self._changes_section.toggled.connect(self._on_changes_toggled)
        changes_section = self._changes_section
        changes_inner = QWidget()
        changes_v = QVBoxLayout(changes_inner)
        changes_v.setContentsMargins(0, 0, 0, 0)
        changes_v.setSpacing(4)

        self._table = QTableWidget(0, _NCOLS)
        self._table.setHorizontalHeaderLabels([self.tr("Filename"), self.tr("File Path"), self.tr("Change")])
        self._table.horizontalHeader().setSectionResizeMode(_COL_FILENAME, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(_COL_PATH,     QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(_COL_CHANGE,   QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        changes_v.addWidget(self._table, stretch=1)

        self._counts_label = QLabel(self.tr("New: 0  |  Updated: 0  |  Unchanged: 0  |  Moved: 0  |  Deleted: 0"))
        self._counts_label.setStyleSheet(
            "QLabel { "
            "background-color: palette(window); "
            "border-top: 1px solid palette(mid); "
            "padding: 4px 6px; "
            "font-family: monospace; "
            "}"
        )
        changes_v.addWidget(self._counts_label)
        changes_section.add_widget(changes_inner)

        lower_widget = QWidget()
        lower_layout = QVBoxLayout(lower_widget)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.addWidget(changes_section)

        bottom_widget.addWidget(upper_widget)
        bottom_widget.addWidget(lower_widget)
        bottom_widget.setStretchFactor(0, 0)
        bottom_widget.setStretchFactor(1, 1)
        self._bottom_splitter = bottom_widget
        self._changes_lower = lower_widget

        self._splitter.addWidget(bottom_widget)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)

        # --- Control buttons ---
        from app.ui.config_dialog import get_color_scheme as _get_cs
        _v = "light" if "light" in _get_cs().lower() else "dark"

        def _icon(name: str) -> QIcon:
            p = _ICONS_DIR / f"{name}_{_v}.svg"
            return QIcon(str(p)) if p.exists() else QIcon()

        btn_row = QHBoxLayout()
        self._start_btn = QPushButton(_icon("crawler_start"), self.tr("Start Crawl"))
        self._start_btn.clicked.connect(self._start_crawl)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton(_icon("crawler_stop"), self.tr("Stop"))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_crawl)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()

        self._bg_btn = QPushButton(_icon("crawler_bg"), self.tr("Run in Background"))
        self._bg_btn.setEnabled(False)
        self._bg_btn.clicked.connect(self.close_requested)
        btn_row.addWidget(self._bg_btn)

        self._close_btn = QPushButton(_icon("crawler_close"), self.tr("Close"))
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
        self._resize_folder_section()

    def _on_changes_toggled(self, expanded: bool) -> None:
        sizes = self._bottom_splitter.sizes()
        if len(sizes) != 2:
            return
        total = sum(sizes)
        header_h = self._changes_section._toggle.sizeHint().height() + 4
        if expanded:
            self._changes_lower.setMaximumHeight(16777215)
            restore = self._changes_prev_height or max(total // 2, 200)
            lower = min(restore, max(total - 50, header_h))
        else:
            self._changes_prev_height = sizes[1]
            self._changes_lower.setMaximumHeight(header_h)
            lower = header_h
        self._bottom_splitter.setSizes([max(0, total - lower), lower])

    def _resize_folder_section(self) -> None:
        """Size the splitter top so the folder list fits min(N, 5) rows."""
        rows = max(1, min(len(self._all_folders), 5))
        row_h = self._folder_list.sizeHintForRow(0) if self._folder_list.count() else (
            self._folder_list.fontMetrics().height() + 6
        )
        list_h = rows * row_h + 2 * self._folder_list.frameWidth() + 4

        top_w = self._splitter.widget(0)
        lay = top_w.layout()
        extras = 0
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item.widget() is self._folder_list:
                continue
            extras += item.sizeHint().height() + lay.spacing()
        top_h = list_h + extras + lay.contentsMargins().top() + lay.contentsMargins().bottom()

        total = max(self._splitter.height(), top_h + 200)
        self._splitter.setSizes([top_h, max(200, total - top_h)])

    def _append_folder_item(self, path: str) -> None:
        item = QListWidgetItem(path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        self._folder_list.addItem(item)

    def _refresh_folder_status(self) -> None:
        n = len(self._all_folders)
        if n == 0:
            self._folder_status_label.setText(
                self.tr("No folders yet. Use 'Add Folder…' to add a folder to crawl.")
            )
            self._start_btn.setEnabled(False)
        else:
            self._folder_status_label.setText(f"{n} folder{'s' if n != 1 else ''} configured.")
            self._start_btn.setEnabled(True)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.tr("Add Folder to Crawl"))
        if not path:
            return
        if any(p == path for p, _ in self._all_folders):
            QMessageBox.information(self, self.tr("Already Listed"), f"{path}\nis already in the list.")
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
        self._resize_folder_section()

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
        self._resize_folder_section()

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
            QMessageBox.information(self, self.tr("Save Pattern"), self.tr("Enter a pattern first."))
            return
        name, ok = QInputDialog.getText(self, self.tr("Save Pattern"), self.tr("Pattern name:"))
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
            self._preview_label.setText(self.tr("<i>no match</i>"))
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
            self._status_label.setText(self.tr("No folders selected — tick at least one folder."))
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
        self._worker.phase.connect(self._on_phase)
        self._worker.progress.connect(self._on_progress)
        self._worker.counts_changed.connect(self._on_counts_changed)
        self._worker.archive_result.connect(self._on_archive_result)
        self._worker.file_moved.connect(self._on_file_moved)
        self._worker.file_deleted.connect(self._on_file_deleted)
        self._worker.all_finished.connect(self._on_finished)
        self._worker.start()

    def crawl_single_path(self, path: str) -> None:
        """Select the registered crawled folder that contains *path* and start crawling."""
        if self._worker is not None:
            QMessageBox.information(self, self.tr("Crawler Running"),
                self.tr("A crawl is already in progress. Wait for it to finish before starting another."))
            return
        self._load_folders()
        best_idx = -1
        best_len = -1
        for i, (folder_path, _) in enumerate(self._all_folders):
            if path == folder_path or path.startswith(folder_path + "/"):
                if len(folder_path) > best_len:
                    best_len = len(folder_path)
                    best_idx = i
        if best_idx == -1:
            QMessageBox.information(self, self.tr("Crawler"),
                f"No registered crawled folder contains:\n{path}\n\n"
                "Add it via the Crawler window first.")
            return
        for i in range(self._folder_list.count()):
            state = Qt.CheckState.Checked if i == best_idx else Qt.CheckState.Unchecked
            self._folder_list.item(i).setCheckState(state)
        self._start_crawl()

    def _stop_crawl(self) -> None:
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    def _on_phase(self, message: str) -> None:
        self._phase_label.setText(message)

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

    def _on_archive_result(self, folder: str, archive_path: str, status: str) -> None:
        if status not in ("new", "updated"):
            return
        change = "New" if status == "new" else "Updated"
        self._add_table_row(Path(archive_path).name, archive_path, change)

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
        self._phase_label.clear()
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
