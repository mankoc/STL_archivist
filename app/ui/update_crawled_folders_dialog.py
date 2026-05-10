"""
Dialog that re-crawls stored CrawledFolder records to pick up new and
modified archives, using the pattern saved with each folder.

The user can select which folders to re-crawl via a checkbox list.
"""
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QPushButton,
    QSizePolicy, QVBoxLayout,
)

from app.crawler.crawler import CrawlerWorker
from app.database.db import session_scope
from app.database.models import CrawledFolder


class _UpdateAllWorker(QThread):
    """Runs one CrawlerWorker per folder sequentially, waiting for each to finish."""

    log            = pyqtSignal(str)
    folder_started = pyqtSignal(str, int, int)  # path, index, total
    progress       = pyqtSignal(int, int)        # current archive, total archives
    counts_changed = pyqtSignal(int, int, int)   # new, updated, unchanged (accumulated)
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
        total_new = total_updated = total_unchanged = 0
        total = len(self._folders)
        for idx, (path, pattern) in enumerate(self._folders, start=1):
            if self._stop_requested:
                break
            self.folder_started.emit(path, idx, total)
            worker = CrawlerWorker(path, pattern=pattern or "", config=self._config)
            self._current_worker = worker

            # Capture folder offsets so the lambda closes over the right values.
            _off_n, _off_u, _off_unc = total_new, total_updated, total_unchanged

            # DirectConnection so signals from worker's thread bypass
            # _UpdateAllWorker's (blocked) event loop and reach the main thread directly.
            worker.log.connect(self.log, Qt.ConnectionType.DirectConnection)
            worker.progress.connect(self.progress, Qt.ConnectionType.DirectConnection)
            worker.counts_changed.connect(
                lambda n, u, unc, on=_off_n, ou=_off_u, ounc=_off_unc:
                    self.counts_changed.emit(on + n, ou + u, ounc + unc),
                Qt.ConnectionType.DirectConnection,
            )

            worker.start()
            worker.wait()  # block this thread until the folder finishes

            total_new       += worker._stats_new
            total_updated   += worker._stats_updated
            total_unchanged += worker._stats_unchanged
            self._current_worker = None

        self.all_finished.emit()


class UpdateCrawledFoldersDialog(QDialog):
    """Shows a folder checklist and crawl progress for re-crawling stored CrawledFolder entries."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update Crawled Folders")
        self.resize(700, 620)
        self._worker: _UpdateAllWorker | None = None
        self._all_folders: list[tuple[str, str | None]] = []
        self._build_ui()
        self._load_folders()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Folder selection list ---
        layout.addWidget(QLabel("Select folders to re-crawl:"))

        self._folder_list = QListWidget()
        self._folder_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._folder_list.setMinimumHeight(160)
        layout.addWidget(self._folder_list)

        sel_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all)
        sel_row.addWidget(select_all_btn)
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self._deselect_all)
        sel_row.addWidget(deselect_all_btn)
        sel_row.addStretch()
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

        layout.addWidget(QLabel("Log:"))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._log)

        self._counts_label = QLabel("New: 0  |  Updated: 0  |  Unchanged: 0")
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

        self._start_btn = QPushButton(style.standardIcon(SP.SP_MediaPlay), "Start")
        self._start_btn.clicked.connect(self._start)
        btn_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton(style.standardIcon(SP.SP_MediaStop), "Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self._stop_btn)

        btn_row.addStretch()

        self._close_btn = QPushButton(style.standardIcon(SP.SP_DialogCloseButton), "Close")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        layout.addLayout(btn_row)

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
            item = QListWidgetItem(path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._folder_list.addItem(item)

        n = len(self._all_folders)
        if n == 0:
            self._status_label.setText(
                "No crawled folders stored yet. "
                "Run the Crawler on a folder first to register it."
            )
            self._start_btn.setEnabled(False)
        else:
            self._status_label.setText(f"{n} folder{'s' if n != 1 else ''} available.")

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

    def _start(self) -> None:
        from app.ui.config_dialog import get_max_thumbnails
        from app.ui.settings_loader import build_crawler_config

        folders = self._selected_folders()
        if not folders:
            self._status_label.setText("No folders selected — tick at least one folder to re-crawl.")
            return

        self._log.clear()
        self._progress.setValue(0)
        self._counts_label.setText("New: 0  |  Updated: 0  |  Unchanged: 0")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._close_btn.setEnabled(False)
        self._folder_list.setEnabled(False)

        n = len(folders)
        self._status_label.setText(f"Re-crawling {n} folder{'s' if n != 1 else ''}…")

        config = build_crawler_config(max_thumbnails=get_max_thumbnails())
        config.log_unchanged = False
        self._worker = _UpdateAllWorker(folders, config=config, parent=self)
        self._worker.log.connect(self._on_log)
        self._worker.folder_started.connect(self._on_folder_started)
        self._worker.progress.connect(self._on_progress)
        self._worker.counts_changed.connect(self._on_counts_changed)
        self._worker.all_finished.connect(self._on_finished)
        self._worker.start()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        self._stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------
    def _on_log(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_folder_started(self, path: str, idx: int, total: int) -> None:
        self._status_label.setText(f"Folder {idx} / {total}")
        self._folder_label.setText(path)
        self._progress.setMaximum(0)  # indeterminate until first archive progress
        self._progress.setValue(0)

    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setMaximum(total)
        self._progress.setValue(current)
        self._progress.setFormat(f"{current} / {total}")

    def _on_counts_changed(self, new: int, updated: int, unchanged: int) -> None:
        self._counts_label.setText(
            f"New: {new}  |  Updated: {updated}  |  Unchanged: {unchanged}"
        )

    def _on_finished(self) -> None:
        n = len(self._selected_folders()) if self._worker else 0
        self._status_label.setText(
            f"Update complete — {n} folder{'s' if n != 1 else ''} processed."
        )
        self._folder_label.clear()
        self._progress.setMaximum(1)
        self._progress.setValue(1)
        self._progress.setFormat("done")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._close_btn.setEnabled(True)
        self._folder_list.setEnabled(True)
        self._worker = None

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait()
        super().closeEvent(event)
