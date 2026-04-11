"""
Configuration dialog.
The active data-directory is persisted via QSettings (one global pointer).
All other settings are stored in a JSON file inside the data directory so
that each library has its own independent configuration.
"""
import json
from pathlib import Path

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QDialogButtonBox, QFileDialog, QMessageBox, QSpinBox,
    QComboBox, QApplication,
)

DATA_DIR_KEY           = "config/data_dir"
DATA_DIR_DEFAULT       = str(Path.home() / ".stl_database")

# Defaults for per-library settings
MAX_THUMBS_DEFAULT     = 5
MAX_THUMB_SIZE_DEFAULT = 512
COLOR_SCHEME_DEFAULT   = "Windows 11 Dark"

_LIBRARY_SETTINGS_FILE = "settings.json"

# Map display name -> path relative to the package root (or "" for system default)
_THEMES_DIR = Path(__file__).parent.parent.parent / "resources" / "themes"
THEMES: dict[str, str] = {
    "System Default":  "",
    "VS Code Dark+":   str(_THEMES_DIR / "vscode_dark.qss"),
    "Windows 11 Dark": str(_THEMES_DIR / "win11_dark.qss"),
    "Windows 11 Light": str(_THEMES_DIR / "win11_light.qss"),
}


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


def get_color_scheme() -> str:
    return _load_library_settings().get("color_scheme", COLOR_SCHEME_DEFAULT)


_ICONS_DIR = Path(__file__).parent.parent.parent / "resources" / "icons"


def apply_color_scheme(scheme_name: str) -> None:
    """Apply a color scheme to the running QApplication."""
    app = QApplication.instance()
    if app is None:
        return
    qss_path = THEMES.get(scheme_name, "")
    if qss_path and Path(qss_path).exists():
        icons = str(_ICONS_DIR).replace("\\", "/")
        qss = Path(qss_path).read_text(encoding="utf-8")
        qss = qss.replace("%(ICONS_DIR)s", icons)
        app.setStyleSheet(qss)
    else:
        app.setStyleSheet("")


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.setMinimumWidth(480)
        self._original_scheme: str = get_color_scheme()
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

        # ---- Appearance group ----
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QFormLayout(appearance_group)

        self._theme_combo = QComboBox()
        for name in THEMES:
            self._theme_combo.addItem(name)
        self._theme_combo.currentTextChanged.connect(self._preview_theme)
        appearance_layout.addRow("Color scheme:", self._theme_combo)
        appearance_layout.addRow(
            "",
            QLabel("<small>Preview is applied immediately. Cancel reverts to the previous scheme.</small>"),
        )

        root.addWidget(appearance_group)
        root.addStretch()

        # ---- Buttons ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self._cancel)
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
    def _preview_theme(self, scheme_name: str) -> None:
        apply_color_scheme(scheme_name)

    # ------------------------------------------------------------------
    def _load(self):
        self._data_dir_edit.setText(get_data_dir())
        self._max_thumbs_spin.setValue(get_max_thumbnails())
        self._max_thumb_size_spin.setValue(get_max_thumb_size())

        scheme = get_color_scheme()
        idx = self._theme_combo.findText(scheme)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        else:
            self._theme_combo.setCurrentIndex(0)

    def _save(self):
        data_dir = self._data_dir_edit.text().strip() or DATA_DIR_DEFAULT
        old_dir = get_data_dir()

        # Save the data-directory pointer globally
        QSettings().setValue(DATA_DIR_KEY, data_dir)

        selected_scheme = self._theme_combo.currentText()

        # Save all other settings into the library's own settings file
        _save_library_settings(
            {
                "max_thumbnails": self._max_thumbs_spin.value(),
                "max_thumb_size": self._max_thumb_size_spin.value(),
                "color_scheme": selected_scheme,
            },
            data_dir=data_dir,
        )

        self._original_scheme = selected_scheme

        if data_dir != old_dir:
            QMessageBox.information(
                self,
                "Configuration",
                "Data directory updated. Please restart the application for the change to take effect.",
            )
        self.accept()

    def _cancel(self):
        apply_color_scheme(self._original_scheme)
        self.reject()
