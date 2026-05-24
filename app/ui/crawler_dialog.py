"""
Crawler dialog — wraps CrawlerTab in a modal QDialog.
"""
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout

from app.ui.crawler_tab import CrawlerTab


class CrawlerDialog(QDialog):
    """Modal dialog that wraps ``CrawlerTab`` for use from the main menu/toolbar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Crawler")
        self.resize(700, 749)
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._crawler_tab = CrawlerTab()
        self._crawler_tab.close_requested.connect(self.reject)
        layout.addWidget(self._crawler_tab)

    def changeEvent(self, event: QEvent) -> None:
        if (event.type() == QEvent.Type.WindowStateChange
                and bool(self.windowState() & Qt.WindowState.WindowMinimized)
                and self._crawler_tab._bg_btn.isEnabled()):
            self.setWindowState(Qt.WindowState.WindowNoState)
            self._crawler_tab.close_requested.emit()
            return
        super().changeEvent(event)
