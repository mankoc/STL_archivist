"""
Configuration dialog.
Settings are persisted via QSettings (no separate config file needed).
"""
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QDialogButtonBox, QFileDialog, QMessageBox,
)

DB_PATH_KEY     = "config/db_path"
DB_PATH_DEFAULT = "stl_database.db"


def get_db_path() -> str:
    return QSettings().value(DB_PATH_KEY, DB_PATH_DEFAULT)


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.setMinimumWidth(480)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        # ---- Database group ----
        db_group = QGroupBox("Database")
        db_layout = QFormLayout(db_group)

        path_row = QHBoxLayout()
        self._db_path_edit = QLineEdit()
        self._db_path_edit.setPlaceholderText(DB_PATH_DEFAULT)
        path_row.addWidget(self._db_path_edit)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_db_path)
        path_row.addWidget(browse_btn)

        db_layout.addRow("Database file:", path_row)
        db_layout.addRow(
            "",
            QLabel(
                "<small>Absolute path recommended. "
                "A restart is required for path changes to take effect.</small>"
            ),
        )

        root.addWidget(db_group)
        root.addStretch()

        # ---- Buttons ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    def _browse_db_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose database file location",
            self._db_path_edit.text() or DB_PATH_DEFAULT,
            "SQLite database (*.db);;All files (*)",
        )
        if path:
            self._db_path_edit.setText(path)

    # ------------------------------------------------------------------
    def _load(self):
        self._db_path_edit.setText(get_db_path())

    def _save(self):
        path = self._db_path_edit.text().strip() or DB_PATH_DEFAULT
        old_path = get_db_path()
        QSettings().setValue(DB_PATH_KEY, path)
        if path != old_path:
            QMessageBox.information(
                self,
                "Configuration",
                "Database path updated. Please restart the application for the change to take effect.",
            )
        self.accept()
