"""
Setup dialog for the AI tagging feature.

Shown on the first "Generate" click when torch is not yet installed.
Lets the user choose CPU or GPU (CUDA 12.8) and downloads torch +
transformers + qwen-vl-utils into <data_dir>/ai_packages/ via pip.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QRadioButton, QVBoxLayout, QButtonGroup,
)

from app.utils.ai_runtime import INSTALL_VARIANTS, install_ai_packages, ai_packages_dir


# ---------------------------------------------------------------------------
# Background install worker
# ---------------------------------------------------------------------------

class _InstallWorker(QThread):
    progress = pyqtSignal(str)   # status message
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, variant: str) -> None:
        super().__init__()
        self._variant = variant

    def run(self) -> None:
        try:
            install_ai_packages(
                variant=self._variant,
                progress=lambda msg: self.progress.emit(msg),
            )
            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class AISetupDialog(QDialog):
    """Shown when AI packages have not been installed yet.

    Lets the user choose CPU or GPU and installs torch + transformers +
    qwen-vl-utils into the ai_packages directory.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Tagging — Install Packages")
        self.setMinimumWidth(500)
        self._worker: Optional[_InstallWorker] = None
        self._pkg_dir = ai_packages_dir()
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        title_lbl = QLabel("<b>AI packages are not installed yet.</b>")
        root.addWidget(title_lbl)

        desc = QLabel(
            "The AI tagger needs torch, transformers, and qwen-vl-utils. "
            "Choose a variant and click <b>Install</b> — packages are "
            "downloaded once into your data directory."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        # Variant radio buttons
        self._btn_group = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}
        for key, meta in INSTALL_VARIANTS.items():
            rb = QRadioButton(
                f"{meta['label']}  —  {meta['size']}  —  {meta['note']}"
            )
            self._btn_group.addButton(rb)
            self._radios[key] = rb
            root.addWidget(rb)

        # Default to CPU
        self._radios["cpu"].setChecked(True)

        # Where packages will be installed
        if self._pkg_dir:
            loc_lbl = QLabel(f"Install location: {self._pkg_dir}")
            loc_lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
            loc_lbl.setWordWrap(True)
            root.addWidget(loc_lbl)
        else:
            warn = QLabel(
                "⚠  No data directory configured. "
                "Open Settings → Data Directory first."
            )
            warn.setStyleSheet("color: #f59e0b;")
            warn.setWordWrap(True)
            root.addWidget(warn)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)   # indeterminate while installing
        self._progress_bar.setVisible(False)
        root.addWidget(self._progress_bar)

        # Status label
        self._status_lbl = QLabel()
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setVisible(False)
        root.addWidget(self._status_lbl)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if self._pkg_dir:
            open_btn = QPushButton("Open install folder")
            open_btn.clicked.connect(lambda: _open_dir(self._pkg_dir))
            btn_row.addWidget(open_btn)

        btn_row.addStretch()

        self._install_btn = QPushButton("Install")
        self._install_btn.setDefault(True)
        self._install_btn.setEnabled(self._pkg_dir is not None)
        self._install_btn.clicked.connect(self._start_install)
        btn_row.addWidget(self._install_btn)

        self._close_btn = QPushButton("Cancel")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _selected_variant(self) -> str:
        for key, rb in self._radios.items():
            if rb.isChecked():
                return key
        return "cpu"

    def _start_install(self) -> None:
        variant = self._selected_variant()
        self._worker = _InstallWorker(variant)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self._install_btn.setEnabled(False)
        for rb in self._radios.values():
            rb.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._status_lbl.setStyleSheet("color: #9ca3af;")
        self._status_lbl.setText("Starting installation…")
        self._status_lbl.setVisible(True)

        self._worker.start()

    def _on_progress(self, msg: str) -> None:
        self._status_lbl.setText(msg)

    def _on_finished(self) -> None:
        self._worker = None
        self._progress_bar.setRange(0, 1)
        self._progress_bar.setValue(1)
        self._status_lbl.setStyleSheet("color: #22c55e; font-weight: bold;")
        self._status_lbl.setText(
            "Installation complete! The AI tagger is ready."
        )
        self._close_btn.setText("Close")
        # Accept so _start_inference knows setup succeeded
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._worker = None
        self._progress_bar.setVisible(False)
        for rb in self._radios.values():
            rb.setEnabled(True)
        self._install_btn.setEnabled(self._pkg_dir is not None)
        self._status_lbl.setStyleSheet("color: #ef4444;")
        self._status_lbl.setText(f"Error: {msg}")

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(5000)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _open_dir(path) -> None:
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass
