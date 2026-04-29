"""
Main application window — menu bar, toolbar, browser.
"""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QMainWindow, QStatusBar, QToolBar,
    QApplication, QMessageBox,
)

from app.ui.browser_tab import BrowserTab


class MainWindow(QMainWindow):
    """Main application window hosting the browser tab, menu bar, and toolbar."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("STL Database")
        self.resize(1280, 860)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        style = QApplication.style()
        SP    = style.StandardPixmap

        self._browser = BrowserTab()
        self.setCentralWidget(self._browser)
        self._browser.model_count_changed.connect(self._update_status_bar)

        # ---- Actions ----
        self._act_refresh = QAction(
            style.standardIcon(SP.SP_BrowserReload), "Refresh", self)
        self._act_refresh.setShortcut(QKeySequence("F5"))
        self._act_refresh.setStatusTip("Reload data from the database")
        self._act_refresh.triggered.connect(self._browser.refresh)

        self._act_open_crawler = QAction(
            style.standardIcon(SP.SP_DirOpenIcon), "Open Crawler…", self)
        self._act_open_crawler.setShortcut(QKeySequence.StandardKey.Open)
        self._act_open_crawler.setStatusTip("Open the Crawler window")
        self._act_open_crawler.triggered.connect(self._open_crawler)

        self._act_exit = QAction(
            style.standardIcon(SP.SP_DialogCloseButton), "Exit", self)
        self._act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        self._act_exit.setStatusTip("Quit STL Database")
        self._act_exit.triggered.connect(QApplication.quit)

        self._act_image_grid = QAction(
            style.standardIcon(SP.SP_FileDialogListView), "Image Grid", self)
        self._act_image_grid.setStatusTip("Show a grid of images for the current table")
        self._act_image_grid.triggered.connect(self._browser.show_image_grid)

        self._act_toggle_images = QAction(
            style.standardIcon(SP.SP_FileDialogContentsView), "Images", self)
        self._act_toggle_images.setCheckable(True)
        self._act_toggle_images.setChecked(True)
        self._act_toggle_images.setStatusTip("Show or hide the image preview pane")
        self._act_toggle_images.triggered.connect(self._toggle_image_pane)

        self._act_about = QAction(
            style.standardIcon(SP.SP_MessageBoxInformation), "About…", self)
        self._act_about.setStatusTip("About STL Database")
        self._act_about.triggered.connect(self._show_about)

        self._act_help = QAction(
            style.standardIcon(SP.SP_DialogHelpButton), "Help…", self)
        self._act_help.setShortcut(QKeySequence.StandardKey.HelpContents)
        self._act_help.setStatusTip("Open the help page")
        self._act_help.triggered.connect(self._show_help)

        # ---- Menu bar ----
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        file_menu.addAction(self._act_open_crawler)
        file_menu.addSeparator()
        act_config = QAction(
            style.standardIcon(SP.SP_FileDialogDetailedView), "Configuration…", self)
        act_config.setStatusTip("Open application configuration")
        act_config.triggered.connect(self._open_config)
        file_menu.addAction(act_config)
        file_menu.addSeparator()
        file_menu.addAction(self._act_exit)

        view_menu = mb.addMenu("&View")
        view_menu.addAction(self._act_refresh)
        view_menu.addAction(self._act_toggle_images)

        tools_menu = mb.addMenu("&Tools")
        self._act_gen_metadata = QAction(
            style.standardIcon(SP.SP_FileDialogDetailedView),
            "Generate / Update Metadata Sidecars…",
            self,
        )
        self._act_gen_metadata.setStatusTip(
            "Write or refresh METADATA.7z sidecar files for all folders in the database"
        )
        self._act_gen_metadata.triggered.connect(self._open_metadata_sidecar_dialog)
        tools_menu.addAction(self._act_gen_metadata)

        act_gen_thumbs = QAction(
            style.standardIcon(SP.SP_BrowserReload),
            "Generate Thumbnails for Selected…",
            self,
        )
        act_gen_thumbs.setStatusTip(
            "Generate thumbnails for the models currently selected in the table"
        )
        act_gen_thumbs.triggered.connect(self._browser.generate_thumbnails_for_selected)
        tools_menu.addAction(act_gen_thumbs)

        tools_menu.addSeparator()

        act_find_dupes = QAction(
            style.standardIcon(SP.SP_FileDialogContentsView),
            "Find Metadata Duplicates…",
            self,
        )
        act_find_dupes.setStatusTip(
            "Find models with identical or phonetically similar field values"
        )
        act_find_dupes.triggered.connect(self._open_find_duplicates_dialog)
        tools_menu.addAction(act_find_dupes)

        tools_menu.addSeparator()

        act_db_maintenance = QAction(
            style.standardIcon(SP.SP_FileDialogDetailedView),
            "Database Maintenance…",
            self,
        )
        act_db_maintenance.setStatusTip(
            "Verify archive files exist, relocate moved archives, or remove stale records"
        )
        act_db_maintenance.triggered.connect(self._open_db_maintenance_dialog)
        tools_menu.addAction(act_db_maintenance)

        stats_menu = mb.addMenu("&Statistics")
        act_statistics = QAction(
            style.standardIcon(SP.SP_FileDialogInfoView),
            "Show Statistics…",
            self,
        )
        act_statistics.setStatusTip("Show aggregated statistics about the database")
        act_statistics.triggered.connect(self._open_statistics_dialog)
        stats_menu.addAction(act_statistics)

        act_col_stats = QAction(
            style.standardIcon(SP.SP_FileDialogListView),
            "Column Distribution…",
            self,
        )
        act_col_stats.setStatusTip(
            "Show a bar chart of how models are distributed across any column"
        )
        act_col_stats.triggered.connect(self._open_column_stats_dialog)
        stats_menu.addAction(act_col_stats)

        self._debug_menu = mb.addMenu("&Debug")
        act_clear_db = QAction(
            style.standardIcon(SP.SP_TrashIcon), "Delete Database…", self)
        act_clear_db.setStatusTip("Permanently delete all records from the database")
        act_clear_db.triggered.connect(self._clear_database)
        self._debug_menu.addAction(act_clear_db)

        act_delete_logs = QAction(
            style.standardIcon(SP.SP_TrashIcon), "Delete Logs…", self)
        act_delete_logs.setStatusTip("Delete all log files from the data directory")
        act_delete_logs.triggered.connect(self._delete_logs)
        self._debug_menu.addAction(act_delete_logs)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self._act_help)
        help_menu.addSeparator()
        help_menu.addAction(self._act_about)

        # ---- Toolbar ----
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(36, 36))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(toolbar)

        toolbar.addAction(self._act_open_crawler)
        toolbar.addAction(self._act_refresh)
        toolbar.addAction(self._act_image_grid)
        toolbar.addAction(self._act_toggle_images)

        sb = QStatusBar()
        self.setStatusBar(sb)

        self._apply_debug_menu_visibility()

    # ------------------------------------------------------------------
    def _update_status_bar(self, count: int) -> None:
        """Update the status bar with the current visible model count."""
        self.statusBar().showMessage(f"{count} model{'s' if count != 1 else ''}")

    def _open_crawler(self) -> None:
        """Open the crawler dialog and refresh the browser when it closes."""
        from app.ui.crawler_dialog import CrawlerDialog
        CrawlerDialog(parent=self).exec()
        self._browser.refresh()

    def _open_config(self) -> None:
        """Open the configuration dialog and re-apply debug menu visibility."""
        from app.ui.config_dialog import ConfigDialog
        ConfigDialog(parent=self).exec()
        self._apply_debug_menu_visibility()

    def _open_metadata_sidecar_dialog(self) -> None:
        """Open the metadata sidecar generation dialog."""
        from app.ui.metadata_sidecar_dialog import MetadataSidecarDialog
        MetadataSidecarDialog(parent=self).exec()

    def _open_find_duplicates_dialog(self) -> None:
        """Open the find metadata duplicates dialog (non-modal so main table stays interactive)."""
        from app.ui.find_duplicates_dialog import FindDuplicatesDialog
        dlg = getattr(self, "_find_dupes_dlg", None)
        if dlg is None or not dlg.isVisible():
            dlg = FindDuplicatesDialog(parent=self)
            dlg.show_models_requested.connect(self._browser.show_model_ids)
            self._find_dupes_dlg = dlg
        dlg.raise_()
        dlg.activateWindow()
        dlg.show()

    def _open_db_maintenance_dialog(self) -> None:
        """Open the database maintenance dialog."""
        from app.ui.db_maintenance_dialog import DbMaintenanceDialog
        dialog = DbMaintenanceDialog(parent=self)
        dialog.exec()
        self._browser.refresh()

    def _open_statistics_dialog(self) -> None:
        """Open the database statistics dialog."""
        from app.ui.statistics_dialog import StatisticsDialog
        StatisticsDialog(parent=self).exec()

    def _open_column_stats_dialog(self) -> None:
        """Open the column distribution dialog."""
        from app.ui.column_stats_dialog import ColumnStatsDialog
        ColumnStatsDialog(parent=self).exec()

    def _apply_debug_menu_visibility(self) -> None:
        """Show or hide the Debug menu and sidecar action based on current settings."""
        from app.ui.config_dialog import get_debug_menu_enabled, get_metadata_sidecar_enabled
        self._debug_menu.menuAction().setVisible(get_debug_menu_enabled())
        self._act_gen_metadata.setEnabled(get_metadata_sidecar_enabled())

    def _clear_database(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Delete Database",
            "This will permanently delete <b>all models, tags and images</b> "
            "from the database.<br><br>Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from app.database.db import clear_db
            clear_db()
            self._browser.refresh()
            QMessageBox.information(self, "Delete Database", "Database cleared.")

    def _delete_logs(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Delete Logs",
            "This will permanently delete <b>all log files</b> "
            "(active log and compressed backups).<br><br>Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from app.utils.logger import delete_logs
            count = delete_logs()
            QMessageBox.information(
                self, "Delete Logs",
                f"Deleted {count} log file{'s' if count != 1 else ''}."
            )

    def _toggle_image_pane(self) -> None:
        """Toggle the image preview pane and sync the toolbar button state."""
        visible = self._browser.toggle_image_pane()
        self._act_toggle_images.setChecked(visible)

    def _show_help(self) -> None:
        """Open the help dialog."""
        from app.ui.help_dialog import HelpDialog
        HelpDialog(parent=self).exec()

    def _show_about(self) -> None:
        """Open the About dialog."""
        from app.ui.about_dialog import AboutDialog
        AboutDialog(parent=self).exec()
