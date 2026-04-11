"""
Configuration dialog.
The active data-directory is persisted via QSettings (one global pointer).
All other settings are stored in a JSON file inside the data directory so
that each library has its own independent configuration.
"""
import json
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QDialogButtonBox, QFileDialog, QMessageBox, QSpinBox,
)

from pathlib import Path

DATA_DIR_KEY           = "config/data_dir"
DATA_DIR_DEFAULT       = str(Path.home() / ".stl_database")

# Defaults for per-library settings
MAX_THUMBS_DEFAULT     = 5
MAX_THUMB_SIZE_DEFAULT = 512

_LIBRARY_SETTINGS_FILE = "settings.json"


def get_data_dir() -> str:
    return QSettings().value(DATA_DIR_KEY, DATA_DIR_DEFAULT)


def get_db_path() -> str:
    return str(Path(get_data_dir()) / "stl_database.db")


def _library_settings_path(data_dir: str | None = None) -> Path:
    return Path(data_dir or get_data_dir()) / _LIBRARY_SETTINGS_FILE


def _load_library_settings(data_dir: str | None = None) -> dict:
    path = _library_settings_path(data_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_library_settings(settings: dict, data_dir: str | None = None) -> None:
    path = _library_settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def get_max_thumbnails() -> int:
    return int(_load_library_settings().get("max_thumbnails", MAX_THUMBS_DEFAULT))


def get_max_thumb_size() -> int:
    return int(_load_library_settings().get("max_thumb_size", MAX_THUMB_SIZE_DEFAULT))


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
        self._data_dir_edit = QLineEdit()
        self._data_dir_edit.setPlaceholderText(DATA_DIR_DEFAULT)
        path_row.addWidget(self._data_dir_edit)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_data_dir)
        path_row.addWidget(browse_btn)

        db_layout.addRow("Data directory:", path_row)
        db_layout.addRow(
            "",
            QLabel(
                "<small>All database files are stored in this directory. "
                "A restart is required for changes to take effect.</small>"
            ),
        )

        root.addWidget(db_group)

        # ---- Crawler group ----
        crawler_group = QGroupBox("Crawler")
        crawler_layout = QFormLayout(crawler_group)

        self._max_thumbs_spin = QSpinBox()
        self._max_thumbs_spin.setRange(0, 50)
        self._max_thumbs_spin.setSuffix(" image(s)")
        crawler_layout.addRow("Max thumbnails per model:", self._max_thumbs_spin)
        crawler_layout.addRow(
            "",
            QLabel("<small>Maximum number of preview images extracted from each archive.</small>"),
        )

        self._max_thumb_size_spin = QSpinBox()
        self._max_thumb_size_spin.setRange(64, 4096)
        self._max_thumb_size_spin.setSingleStep(64)
        self._max_thumb_size_spin.setSuffix(" px")
        crawler_layout.addRow("Max image size:", self._max_thumb_size_spin)
        crawler_layout.addRow(
            "",
            QLabel("<small>Longest side in pixels when storing images (width & height capped at this value).</small>"),
        )

        root.addWidget(crawler_group)
        root.addStretch()

        # ---- Buttons ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    def _browse_data_dir(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose data directory",
            self._data_dir_edit.text() or DATA_DIR_DEFAULT,
        )
        if path:
            self._data_dir_edit.setText(path)

    # ------------------------------------------------------------------
    def _load(self):
        self._data_dir_edit.setText(get_data_dir())
        self._max_thumbs_spin.setValue(get_max_thumbnails())
        self._max_thumb_size_spin.setValue(get_max_thumb_size())

    def _save(self):
        data_dir = self._data_dir_edit.text().strip() or DATA_DIR_DEFAULT
        old_dir = get_data_dir()

        # Save the data-directory pointer globally
        QSettings().setValue(DATA_DIR_KEY, data_dir)

        # Save all other settings into the library's own settings file
        _save_library_settings(
            {
                "max_thumbnails": self._max_thumbs_spin.value(),
                "max_thumb_size": self._max_thumb_size_spin.value(),
            },
            data_dir=data_dir,
        )

        if data_dir != old_dir:
            QMessageBox.information(
                self,
                "Configuration",
                "Data directory updated. Please restart the application for the change to take effect.",
            )
        self.accept()
