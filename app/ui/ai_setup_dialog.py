"""
Setup dialog for the AI tagging feature shown when the ONNX model is missing.

The actual model is created by tools/create_ai_model.py (a separate script
that requires PyTorch).  This dialog simply explains what needs to be done
and lets the user open the data directory for convenience.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)


class AISetupDialog(QDialog):
    """Shown when the Qwen2-VL ONNX model has not yet been created."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Tagging — Model Not Found")
        self.setMinimumWidth(540)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)

        root.addWidget(QLabel("<b>The Qwen2-VL ONNX model has not been created yet.</b>"))

        try:
            from app.utils.ai_runtime import ai_cache_dir
            cache = ai_cache_dir()
            cache_str = str(cache) if cache else "&lt;data_dir&gt;/ai_cache"
        except Exception:
            cache_str = "&lt;data_dir&gt;/ai_cache"

        converter_path = Path(sys.argv[0]).parent / "tools" / "create_ai_model.py"

        info = QLabel(
            "The AI tagger needs the Qwen2-VL-2B ONNX model downloaded once.<br>"
            "Run the following command in a terminal (any Python with huggingface-hub):<br><br>"
            "<pre style='background:#1e1e1e;color:#d4d4d4;padding:8px;border-radius:4px'>"
            "pip install huggingface-hub\n\n"
            f"python {converter_path} --output {cache_str}"
            "</pre><br>"
            "This downloads ~3.5 GB (fp16, GPU) or ~1.2 GB (q4, CPU) once.<br>"
            "Add <b>--variant q4f16</b> for a smaller GPU download (~1.5 GB).<br>"
            "Inference runs via onnxruntime — no PyTorch required."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        if cache:
            open_btn = QPushButton("Open data directory")
            open_btn.clicked.connect(lambda: self._open_dir(cache))
            btn_row.addWidget(open_btn)

        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

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
