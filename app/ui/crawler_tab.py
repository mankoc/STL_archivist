"""
Tab 1 – Crawler controls: folder picker, start/stop, progress bar, log.
"""
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QPlainTextEdit, QSizePolicy,
    QComboBox, QInputDialog, QMessageBox,
)

from app.crawler.crawler import CrawlerWorker
from app.utils.path_pattern import parse_path, PATTERN_HELP, ATTR_LABEL

_SAVED_PATTERNS_KEY = "crawler/saved_patterns"
_LAST_PATTERN_KEY   = "crawler/last_pattern"
_LAST_FOLDER_KEY    = "crawler/last_folder"
_SEP = ":::"


class CrawlerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: CrawlerWorker | None = None
        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------
    def _build_ui(self):
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

        help_label = QLabel(PATTERN_HELP)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        help_label.setWordWrap(True)
        pattern_layout.addWidget(help_label)

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
        pattern_layout.addLayout(saved_row)

        # Pattern input
        pattern_row = QHBoxLayout()
        self._pattern_edit = QLineEdit()
        self._pattern_edit.setPlaceholderText("/mnt/f/{type}/{author}/{month}/{name}")
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

        # --- Control buttons ---
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Crawl")
        self._start_btn.clicked.connect(self._start_crawl)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_crawl)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        self._refresh_saved_combo()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _load_settings(self):
        s = QSettings()
        self._folder_edit.setText(s.value(_LAST_FOLDER_KEY, ""))
        self._pattern_edit.setText(s.value(_LAST_PATTERN_KEY, ""))

    def _save_settings(self):
        s = QSettings()
        s.setValue(_LAST_FOLDER_KEY, self._folder_edit.text().strip())
        s.setValue(_LAST_PATTERN_KEY, self._pattern_edit.text().strip())

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
        example = self._example_file_edit.text().strip() or self._folder_edit.text().strip()
        if not pattern or not example:
            self._preview_label.clear()
            return

        extracted = parse_path(pattern, example)
        if not extracted:
            self._preview_label.setText("<i>no match</i>")
            return

        parts = [
            f"<b>{ATTR_LABEL.get(attr, attr)}:</b>&nbsp;{val}"
            for attr, val in extracted.items()
        ]
        self._preview_label.setText("&nbsp;&nbsp;→&nbsp;&nbsp;".join(parts))

    # ------------------------------------------------------------------
    def _browse_folder(self):
        start = self._folder_edit.text().strip() or "/"
        path = QFileDialog.getExistingDirectory(self, "Select STL folder", start)
        if path:
            self._folder_edit.setText(path)

    def _start_crawl(self):
        source = self._folder_edit.text().strip()
        if not source:
            self._log.appendPlainText("Please select a source folder first.")
            return

        self._save_settings()
        self._log.clear()
        self._progress.setValue(0)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        pattern = self._pattern_edit.text().strip()
        self._worker = CrawlerWorker(source, pattern=pattern)
        self._worker.log.connect(self._on_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _stop_crawl(self):
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _on_log(self, msg: str):
        self._log.appendPlainText(msg)

    def _on_progress(self, current: int, total: int):
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")

    def _on_finished(self):
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._worker = None
