"""
Fallback dialog shown when AI packages are missing from the environment.

With packages listed in requirements.txt this should never appear in a
correctly set-up environment.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox


class AISetupDialog(QDialog):
    """Shown when torch / transformers / qwen-vl-utils are not importable."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI Tagging — Packages Missing")
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        root.addWidget(QLabel("<b>Required AI packages are not installed.</b>"))

        msg = QLabel(
            "Install them by running:<br><br>"
            "<code>pip install -r requirements.txt</code><br><br>"
            "For NVIDIA GPU support, replace the torch line with:<br>"
            "<code>pip install torch torchvision "
            "--index-url https://download.pytorch.org/whl/cu124</code>"
        )
        msg.setWordWrap(True)
        msg.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(msg)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
