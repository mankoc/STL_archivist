"""
Tab 1 – Crawler controls: folder picker, start/stop, progress bar, log.
"""
from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QPlainTextEdit, QSizePolicy,
    QComboBox, QInputDialog, QMessageBox, QApplication, QToolButton, QToolTip,
    QCheckBox,
)
from PyQt6.QtGui import QIcon, QCursor

from app.config import CrawlerConfig
from app.crawler.crawler import CrawlerWorker
from app.ui.config_dialog import get_max_thumbnails, get_max_thumb_size, get_unrar_path, get_max_workers
from app.utils.path_pattern import parse_path, PATTERN_HELP, ATTR_LABEL

_SAVED_PATTERNS_KEY      = "crawler/saved_patterns"
_LAST_PATTERN_KEY        = "crawler/last_pattern"
_LAST_FOLDER_KEY         = "crawler/last_folder"
_GENERATE_THUMBNAILS_KEY = "crawler/generate_thumbnails"
_SEP = ":::"


class CrawlerTab(QWidget):
    """Crawler control panel: folder picker, path-pattern editor, progress, and log.

    Signals:
        close_requested: Emitted when the user clicks the Close button, so the
            parent dialog (if any) can dismiss itself.
        progress_updated: Emitted with (current, total) when progress changes.
        crawl_finished: Emitted when the crawl completes.
    """

    close_requested = pyqtSignal()
    progress_updated = pyqtSignal(int, int)
    crawl_finished = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: CrawlerWorker | None = None
        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # --- Folder picker row ---
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Source folder:"))

        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("/mnt/f/STL")
        self._folder_edit.textChanged.connect(self._update_preview)
        folder_row.addWidget(self._folder_edit, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)

        root.addLayout(folder_row)

        # --- Path pattern group ---
        pattern_group = QGroupBox("Parse metadata from path (optional)")
        pattern_layout = QVBoxLayout(pattern_group)

        # Saved-patterns row
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

        # Pattern input
        pattern_row = QHBoxLayout()
        self._pattern_edit = QLineEdit()
        self._pattern_edit.setPlaceholderText("/{source}/{author}/{month}/{name}")
        self._pattern_edit.textChanged.connect(self._update_preview)
        pattern_row.addWidget(self._pattern_edit)
        pattern_layout.addLayout(pattern_row)

        # Random file for pattern preview
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

        # Live preview
        self._preview_label = QLabel("")
        self._preview_label.setTextFormat(Qt.TextFormat.RichText)
        self._preview_label.setWordWrap(True)
        self._preview_label.setStyleSheet("color: #555; font-style: italic;")
        pattern_layout.addWidget(self._preview_label)

        root.addWidget(pattern_group)

        # --- Progress bar ---
        self._progress = QProgressBar()
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        # --- Log output ---
        root.addWidget(QLabel("Log:"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self._log)

        # --- Crawl options ---
        self._generate_thumbs_check = QCheckBox("Generate thumbnails")
        self._generate_thumbs_check.setChecked(True)
        root.addWidget(self._generate_thumbs_check)

        # --- Control buttons ---
        style = QApplication.style()
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_MediaPlay), "Start Crawl"
        )
        self._start_btn.clicked.connect(self._start_crawl)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_MediaStop), "Stop"
        )
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_crawl)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()

        self._bg_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_ArrowDown), "Run in Background"
        )
        self._bg_btn.setEnabled(False)
        self._bg_btn.clicked.connect(self.close_requested)
        btn_row.addWidget(self._bg_btn)

        self._close_btn = QPushButton(
            style.standardIcon(style.StandardPixmap.SP_DialogCloseButton), "Close"
        )
        self._close_btn.clicked.connect(self.close_requested)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

        self._refresh_saved_combo()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        """Restore last-used folder, pattern, and thumbnail flag from QSettings."""
        s = QSettings()
        self._folder_edit.setText(s.value(_LAST_FOLDER_KEY, ""))
        self._pattern_edit.setText(s.value(_LAST_PATTERN_KEY, ""))
        self._generate_thumbs_check.setChecked(
            s.value(_GENERATE_THUMBNAILS_KEY, True, type=bool)
        )

    def _save_settings(self) -> None:
        """Persist the current folder, pattern, and thumbnail flag to QSettings."""
        s = QSettings()
        s.setValue(_LAST_FOLDER_KEY, self._folder_edit.text().strip())
        s.setValue(_LAST_PATTERN_KEY, self._pattern_edit.text().strip())
        s.setValue(_GENERATE_THUMBNAILS_KEY, self._generate_thumbs_check.isChecked())

    # ------------------------------------------------------------------
    # Saved-pattern helpers
    # ------------------------------------------------------------------
    def _saved_patterns(self) -> list[tuple[str, str]]:
        """Return all saved patterns as ``(name, pattern)`` pairs from QSettings."""
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
        folder = self._folder_edit.text().strip()
        if not folder:
            return
        from pathlib import Path
        from app.crawler.archive import is_archive
        files = [str(p) for p in Path(folder).rglob("*") if is_archive(p)]
        if files:
            self._example_file_edit.setText(random.choice(files))

    def _update_preview(self) -> None:
        pattern = self._pattern_edit.text().strip()
        source = self._folder_edit.text().strip()
        example = self._example_file_edit.text().strip() or source
        if not pattern or not example:
            self._preview_label.clear()
            return

        # Match pattern against path relative to the source folder
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
    def _browse_folder(self) -> None:
        """Open a directory picker and populate the source folder field."""
        start = self._folder_edit.text().strip() or "/"
        path = QFileDialog.getExistingDirectory(self, "Select STL folder", start)
        if path:
            self._folder_edit.setText(path)

    def _start_crawl(self) -> None:
        source = self._folder_edit.text().strip()
        if not source:
            self._log.appendPlainText("Please select a source folder first.")
            return

        self._save_settings()
        self._log.clear()
        self._progress.setValue(0)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._bg_btn.setEnabled(True)

        pattern = self._pattern_edit.text().strip()
        max_thumbs = get_max_thumbnails() if self._generate_thumbs_check.isChecked() else 0
        config = CrawlerConfig(
            max_thumbnails=max_thumbs,
            max_thumb_size=get_max_thumb_size(),
            unrar_path=get_unrar_path(),
            max_workers=get_max_workers(),
        )
        self._worker = CrawlerWorker(source, pattern=pattern, config=config)
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop_crawl(self) -> None:
        """Request the running crawler to stop after the current archive."""
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _on_log(self, msg: str) -> None:
        """Append a crawler log message to the log panel."""
        self._log.appendPlainText(msg)

    def _on_progress(self, current: int, total: int) -> None:
        """Update the progress bar with the current archive index and total."""
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")
        self.progress_updated.emit(current, total)

    def _on_finished(self) -> None:
        """Re-enable the Start button and clear the worker reference when done."""
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._bg_btn.setEnabled(False)
        self._worker = None
        self.crawl_finished.emit()
        self.close_requested.emit()
