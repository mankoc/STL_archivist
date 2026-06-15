"""
Setup dialog for the AI tagging feature.

Shown when the Qwen2-VL ONNX model has not been downloaded yet.
Lets the user pick a variant and download it directly from HuggingFace
using the huggingface_hub library (already bundled with transformers).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QVBoxLayout,
)

# ---------------------------------------------------------------------------
# HuggingFace constants (mirrors tools/create_ai_model.py)
# ---------------------------------------------------------------------------

_HF_REPO    = "onnx-community/Qwen2-VL-2B-Instruct"
_OUT_SUBDIR = "qwen2vl_onnx"

_BASE_PATTERNS = ["*.json", "*.txt", "*.tiktoken", "vocab.json", "merges.txt"]

_VARIANT_PATTERNS: dict[str, list[str]] = {
    "fp16":  [
        "onnx/vision_encoder_fp16.onnx",
        "onnx/vision_encoder_fp16.onnx_data",
        "onnx/embed_tokens_fp16.onnx",
        "onnx/decoder_model_merged_fp16.onnx",
        "onnx/decoder_model_merged_fp16.onnx_data",
    ],
    "q4f16": [
        "onnx/vision_encoder_q4f16.onnx",
        "onnx/vision_encoder_q4f16.onnx_data",
        "onnx/embed_tokens_q4f16.onnx",
        "onnx/decoder_model_merged_q4f16.onnx",
    ],
    "int8":  [
        "onnx/vision_encoder_int8.onnx",
        "onnx/embed_tokens_int8.onnx",
        "onnx/decoder_model_merged_int8.onnx",
    ],
    "q4":    [
        "onnx/vision_encoder_q4.onnx",
        "onnx/vision_encoder_q4.onnx_data",
        "onnx/embed_tokens_q4.onnx",
        "onnx/decoder_model_merged_q4.onnx",
    ],
}

_VARIANT_META = {
    "fp16":  ("~3.5 GB", "Best quality — GPU required (CUDA / DirectML)"),
    "q4f16": ("~1.5 GB", "Smaller GPU download — GPU required (CUDA / DirectML)"),
    "int8":  ("~2.2 GB", "CPU or GPU"),
    "q4":    ("~1.2 GB", "Smallest — CPU optimised"),
}


# ---------------------------------------------------------------------------
# Background download worker
# ---------------------------------------------------------------------------

class _DownloadWorker(QThread):
    # (bytes_done, bytes_total, current_file)
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    def __init__(self, output_dir: Path, variant: str) -> None:
        super().__init__()
        self._output_dir = output_dir
        self._variant    = variant
        self._cancelled  = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            from huggingface_hub import snapshot_download
            from tqdm import tqdm as _TqdmBase
        except ImportError as exc:
            self.error.emit(
                f"Required package missing: {exc.name}.\n"
                "Ensure huggingface_hub and tqdm are installed."
            )
            return

        worker = self

        class _QtTqdm(_TqdmBase):
            """tqdm subclass that forwards progress to the Qt signal."""

            def update(self, n: int = 1) -> None:
                super().update(n)
                if not worker._cancelled:
                    worker.progress.emit(
                        int(self.n),
                        int(self.total or 0),
                        str(self.desc or ""),
                    )

            # Silence all console output — Qt handles display
            def display(self, *args, **kwargs) -> None:
                pass

            def close(self) -> None:
                super().close()

        local_dir = self._output_dir / _OUT_SUBDIR
        local_dir.mkdir(parents=True, exist_ok=True)
        patterns  = _BASE_PATTERNS + _VARIANT_PATTERNS[self._variant]

        try:
            snapshot_download(
                _HF_REPO,
                local_dir=str(local_dir),
                allow_patterns=patterns,
                tqdm_class=_QtTqdm,
            )
        except Exception as exc:
            if not self._cancelled:
                self.error.emit(str(exc))
            return

        if not self._cancelled:
            self.finished.emit()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class AISetupDialog(QDialog):
    """Shown when the Qwen2-VL ONNX model has not been downloaded yet."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Tagging — Download Model")
        self.setMinimumWidth(520)
        self._worker: Optional[_DownloadWorker] = None

        try:
            from app.utils.ai_runtime import ai_cache_dir
            self._cache = ai_cache_dir()
        except Exception:
            self._cache = None

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        root.addWidget(QLabel("<b>The Qwen2-VL ONNX model has not been downloaded yet.</b>"))

        desc = QLabel(
            "The AI tagger needs the Qwen2-VL-2B model downloaded once "
            "from HuggingFace. Choose a variant and click <b>Download</b>."
        )
        desc.setWordWrap(True)
        root.addWidget(desc)

        # Variant picker
        var_row = QHBoxLayout()
        var_row.addWidget(QLabel("Variant:"))
        self._combo = QComboBox()
        for key, (size, hint) in _VARIANT_META.items():
            self._combo.addItem(f"{key}  —  {size}  —  {hint}", userData=key)
        self._combo.setCurrentIndex(0)   # fp16 default
        self._combo.currentIndexChanged.connect(self._on_variant_changed)
        var_row.addWidget(self._combo, stretch=1)
        root.addLayout(var_row)

        # Hint label (updates with combo)
        self._hint_lbl = QLabel()
        self._hint_lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")
        root.addWidget(self._hint_lbl)
        self._on_variant_changed()

        # Progress bar + status
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status_lbl = QLabel()
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setVisible(False)
        root.addWidget(self._status_lbl)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        if self._cache:
            open_btn = QPushButton("Open data directory")
            open_btn.clicked.connect(lambda: self._open_dir(self._cache))
            btn_row.addWidget(open_btn)

        btn_row.addStretch()

        self._cancel_btn = QPushButton("Cancel download")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._cancel_download)
        btn_row.addWidget(self._cancel_btn)

        self._download_btn = QPushButton("Download")
        self._download_btn.setDefault(True)
        self._download_btn.setEnabled(self._cache is not None)
        self._download_btn.clicked.connect(self._start_download)
        btn_row.addWidget(self._download_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._close_btn)

        root.addLayout(btn_row)

        if self._cache is None:
            warn = QLabel(
                "⚠ No data directory configured. "
                "Open Settings → Data Directory first."
            )
            warn.setStyleSheet("color: #f59e0b;")
            warn.setWordWrap(True)
            root.insertWidget(root.count() - 1, warn)

    # ------------------------------------------------------------------
    def _on_variant_changed(self) -> None:
        key = self._combo.currentData()
        if key:
            size, hint = _VARIANT_META[key]
            self._hint_lbl.setText(f"Download size: {size}  |  {hint}")

    # ------------------------------------------------------------------
    def _start_download(self) -> None:
        if self._cache is None:
            return

        variant = self._combo.currentData()
        self._worker = _DownloadWorker(self._cache, variant)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)

        self._download_btn.setEnabled(False)
        self._combo.setEnabled(False)
        self._cancel_btn.setVisible(True)
        self._progress.setValue(0)
        self._progress.setRange(0, 100)
        self._progress.setVisible(True)
        self._status_lbl.setStyleSheet("color: #9ca3af;")
        self._status_lbl.setText("Connecting to HuggingFace…")
        self._status_lbl.setVisible(True)

        self._worker.start()

    def _cancel_download(self) -> None:
        if self._worker:
            self._worker.cancel()
            self._worker.quit()
            self._worker.wait(3000)
            self._worker = None
        self._reset_ui()
        self._status_lbl.setText("Download cancelled.")
        self._status_lbl.setVisible(True)

    def _reset_ui(self) -> None:
        self._download_btn.setEnabled(self._cache is not None)
        self._combo.setEnabled(True)
        self._cancel_btn.setVisible(False)
        self._progress.setVisible(False)

    # ------------------------------------------------------------------
    def _on_progress(self, done: int, total: int, desc: str) -> None:
        if total > 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(int(done * 100 / total))
            done_mb  = done  / 1_048_576
            total_mb = total / 1_048_576
            size_str = f"{done_mb:.0f} MB / {total_mb:.0f} MB"
        else:
            # Unknown total — pulse
            self._progress.setRange(0, 0)
            size_str = f"{done / 1_048_576:.0f} MB"

        file_name = Path(desc).name if desc else ""
        if file_name:
            self._status_lbl.setText(f"Downloading {file_name}… ({size_str})")
        else:
            self._status_lbl.setText(f"Downloading… ({size_str})")

    def _on_finished(self) -> None:
        self._worker = None
        self._reset_ui()
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._progress.setVisible(True)
        self._status_lbl.setStyleSheet("color: #22c55e; font-weight: bold;")
        self._status_lbl.setText("Download complete! The AI tagger is ready.")
        self._status_lbl.setVisible(True)
        self._close_btn.setText("Close")
        # Accept so the caller knows setup succeeded
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._worker = None
        self._reset_ui()
        self._progress.setVisible(False)
        self._status_lbl.setStyleSheet("color: #ef4444;")
        self._status_lbl.setText(f"Error: {msg}")
        self._status_lbl.setVisible(True)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.quit()
            self._worker.wait(3000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    @staticmethod
    def _open_dir(path: Path) -> None:
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass
