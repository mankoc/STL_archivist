"""
Main application window — menu bar, toolbar, and tab widget.
"""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QToolBar,
    QApplication, QMessageBox,
)

from app.ui.browser_tab import BrowserTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("STL Database")
        self.resize(1280, 860)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        style = QApplication.style()
        SP    = style.StandardPixmap

        # ---- Tabs ----
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._browser_tab = BrowserTab()

        self._tabs.addTab(self._browser_tab, "Browser")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # ---- Shared actions ----
        self._act_refresh = QAction(
            style.standardIcon(SP.SP_BrowserReload), "Refresh", self)
        self._act_refresh.setShortcut(QKeySequence("F5"))
        self._act_refresh.setStatusTip("Reload data in the current tab")
        self._act_refresh.triggered.connect(self._refresh_current)

        self._act_open_folder = QAction(
            style.standardIcon(SP.SP_DirOpenIcon), "Open Crawler…", self)
        self._act_open_folder.setShortcut(QKeySequence.StandardKey.Open)
        self._act_open_folder.setStatusTip("Open the Crawler window")
        self._act_open_folder.triggered.connect(self._open_crawler)

        self._act_exit = QAction(
            style.standardIcon(SP.SP_DialogCloseButton), "Exit", self)
        self._act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        self._act_exit.setStatusTip("Quit STL Database")
        self._act_exit.triggered.connect(QApplication.quit)

        self._act_go_browser = QAction(
            style.standardIcon(SP.SP_FileDialogContentsView), "Browser", self)
        self._act_go_browser.setShortcut(QKeySequence("Ctrl+1"))
        self._act_go_browser.setStatusTip("Switch to Browser tab")
        self._act_go_browser.triggered.connect(
            lambda: self._tabs.setCurrentWidget(self._browser_tab))

        self._act_about = QAction(
            style.standardIcon(SP.SP_MessageBoxInformation), "About…", self)
        self._act_about.setStatusTip("About STL Database")
        self._act_about.triggered.connect(self._show_about)

        # ---- Menu bar ----
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        file_menu.addAction(self._act_open_folder)
        file_menu.addSeparator()
        file_menu.addAction(self._act_exit)

        view_menu = mb.addMenu("&View")
        view_menu.addAction(self._act_refresh)
        view_menu.addSeparator()
        view_menu.addAction(self._act_go_browser)

        debug_menu = mb.addMenu("&Debug")
        act_clear_db = QAction(
            style.standardIcon(SP.SP_TrashIcon), "Delete Database…", self)
        act_clear_db.setStatusTip("Permanently delete all records from the database")
        act_clear_db.triggered.connect(self._clear_database)
        debug_menu.addAction(act_clear_db)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self._act_about)

        # ---- Toolbar (reuses the same action objects) ----
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(22, 22))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        toolbar.addAction(self._act_open_folder)
        toolbar.addAction(self._act_refresh)
        toolbar.addSeparator()
        toolbar.addAction(self._act_go_browser)

        self.setStatusBar(QStatusBar())

    # ------------------------------------------------------------------
    def _refresh_current(self):
        widget = self._tabs.currentWidget()
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _on_tab_changed(self, index: int):
        widget = self._tabs.widget(index)
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _open_crawler(self):
        from app.ui.crawler_dialog import CrawlerDialog
        dlg = CrawlerDialog(parent=self)
        dlg.exec()
        # Refresh browser in case new models were crawled
        self._browser_tab.refresh()

    def _clear_database(self):
        from PyQt6.QtWidgets import QMessageBox
        from app.database.db import clear_db

        reply = QMessageBox.warning(
            self,
            "Delete Database",
            "This will permanently delete <b>all models, tags and images</b> "
            "from the database.<br><br>Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            clear_db()
            self._browser_tab.refresh()
            QMessageBox.information(self, "Delete Database", "Database cleared.")

    def _show_about(self):
        QMessageBox.about(
            self,
            "About STL Database",
            "<b>STL Database</b><br>"
            "A desktop application for cataloguing and browsing<br>"
            "3D-print model archives.<br><br>"
            "Built with Python + PyQt6 + SQLAlchemy.",
        )
