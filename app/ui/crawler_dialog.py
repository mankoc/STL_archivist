"""
Crawler dialog — wraps CrawlerTab in a modal QDialog.
"""
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from app.ui.crawler_tab import CrawlerTab


class CrawlerDialog(QDialog):
    """Modal dialog that wraps ``CrawlerTab`` for use from the main menu/toolbar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crawler")
        self.resize(700, 624)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._crawler_tab = CrawlerTab()
        self._crawler_tab.close_requested.connect(self.reject)
        layout.addWidget(self._crawler_tab)
