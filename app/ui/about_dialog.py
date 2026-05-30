"""
About dialog — application info and license.
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QDialogButtonBox, QLabel, QTextBrowser,
)
from PyQt6.QtCore import Qt


_LICENSE_HTML = """
<h3>License</h3>
<p>
  Copyright &copy; 2026 mankoc<br><br>
  This work is licensed under the
  <b>Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International</b>
  (CC&nbsp;BY-NC-SA&nbsp;4.0) license.
</p>
<p>You are free to:</p>
<ul>
  <li><b>Share</b> &mdash; copy and redistribute the material in any medium or format</li>
  <li><b>Adapt</b> &mdash; remix, transform, and build upon the material</li>
</ul>
<p>Under the following terms:</p>
<ul>
  <li><b>Attribution</b> &mdash; You must give appropriate credit and indicate if changes were made.</li>
  <li><b>NonCommercial</b> &mdash; You may not use the material for commercial purposes.</li>
  <li><b>ShareAlike</b> &mdash; Adaptations must be distributed under the same license.</li>
</ul>
<p>
  Full license:
  <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/">
    creativecommons.org/licenses/by-nc-sa/4.0
  </a>
</p>
"""


class AboutDialog(QDialog):
    """About dialog showing the application version, description, and license."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("About stlLibrary"))
        self.resize(540, 420)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        from app import __version__
        header = QLabel(
            f"<b>stlLibrary</b> v{__version__}<br>"
            "A desktop application for cataloguing and browsing<br>"
            "3D-print model archives.<br><br>"
            "Built with Python + PyQt6 + SQLAlchemy."
        )
        header.setAlignment(Qt.AlignmentFlag.AlignLeft)
        header.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(header)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(_LICENSE_HTML)
        root.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
