"""
First-run setup dialog for the AI tagging feature.

Downloads torch + transformers into <data_dir>/ai_packages/.
The Qwen2-VL model (~4.4 GB) is downloaded separately on the first
"Generate" click inside AITaggerDialog (handled by from_pretrained).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QTextEdit,
)


class _InstallThread(QThread):
    progress = pyqtSignal(str)
    failed   = pyqtSignal(str)

    def run(self) -> None:
        try:
            from app.utils.ai_runtime import install_ai_packages
            install_ai_packages(progress=self.progress.emit)
        except Exception as exc:
            self.failed.emit(str(exc))


class AISetupDialog(QDialog):
    """Shown when AI packages are not yet installed in the data directory."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Tagging — First-Time Setup")
        self.setMinimumWidth(500)
        self.resize(540, 360)
        self._thread: _InstallThread | None = None
        self._had_error = False
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        root.addWidget(QLabel(
            "<b>AI tagging requires additional packages that are not yet installed.</b>"
        ))

        info = QLabel(
            "The following will be downloaded into your data directory:<br>"
            "&nbsp;&nbsp;• <b>PyTorch (CPU)</b> — ~300 MB<br>"
            "&nbsp;&nbsp;• <b>Transformers, accelerate, qwen-vl-utils</b> — ~100 MB<br>"
            "&nbsp;&nbsp;• <b>Qwen2-VL-2B model weights</b> — ~4.4 GB "
            "<i>(downloaded on the first Generate click)</i><br><br>"
            "Total one-time download: <b>~4.8 GB</b>. "
            "Everything is stored in your data folder and loaded instantly on future runs."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(info)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(90)
        self._log.setPlaceholderText("Installation log…")
        root.addWidget(self._log)

        self._bar = QProgressBar()
        self._bar.setRange(0, 0)   # indeterminate spinner
        self._bar.setVisible(False)
        root.addWidget(self._bar)

        btn_row = QHBoxLayout()

        self._install_btn = QPushButton("Download && Install")
        self._install_btn.setDefault(True)
        self._install_btn.clicked.connect(self._start)
        btn_row.addWidget(self._install_btn)

        self._close_btn = QPushButton("Cancel")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _start(self) -> None:
        self._install_btn.setEnabled(False)
        self._had_error = False
        self._bar.setVisible(True)
        self._log.clear()

        self._thread = _InstallThread(parent=self)
        self._thread.progress.connect(self._on_progress)
        self._thread.failed.connect(self._on_error)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, msg: str) -> None:
        self._log.append(msg)

    def _on_error(self, msg: str) -> None:
        self._had_error = True
        self._log.append(f"<span style='color:red'><b>Error:</b> {msg}</span>")

    def _on_finished(self) -> None:
        self._bar.setRange(0, 1)
        self._bar.setValue(1)
        if self._had_error:
            self._bar.setVisible(False)
            self._install_btn.setEnabled(True)
        else:
            self._log.append("<b style='color:green'>✓ Packages installed successfully.</b>")
            self._install_btn.setVisible(False)
            self._close_btn.setText("Continue")
            self._close_btn.clicked.disconnect()
            self._close_btn.clicked.connect(self.accept)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        # Prevent closing while install is running
        if self._thread and self._thread.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)
