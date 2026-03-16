"""
Tab 1 – Crawler controls: folder picker, start/stop, progress bar, log.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog,
    QProgressBar, QPlainTextEdit, QSizePolicy,
)

from app.crawler.crawler import CrawlerWorker


class CrawlerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: CrawlerWorker | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # --- Folder picker row ---
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Source folder:"))

        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("/mnt/f/STL")
        folder_row.addWidget(self._folder_edit, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)

        root.addLayout(folder_row)

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

    # ------------------------------------------------------------------
    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select STL folder")
        if path:
            self._folder_edit.setText(path)

    def _start_crawl(self):
        source = self._folder_edit.text().strip()
        if not source:
            self._log.appendPlainText("Please select a source folder first.")
            return

        self._log.clear()
        self._progress.setValue(0)
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

        self._worker = CrawlerWorker(source)
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
