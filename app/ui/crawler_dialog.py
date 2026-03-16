"""
Crawler dialog — wraps CrawlerTab in a modal QDialog.
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QDialogButtonBox

from app.ui.crawler_tab import CrawlerTab


class CrawlerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Crawler")
        self.resize(700, 480)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._crawler_tab = CrawlerTab()
        layout.addWidget(self._crawler_tab)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
