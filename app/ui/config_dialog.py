"""
Configuration dialog.
The active data-directory is persisted via QSettings (one global pointer).
All other settings are stored in a JSON file inside the data directory so
that each library has its own independent configuration.
"""
import json
import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QDialogButtonBox, QFileDialog, QMessageBox, QSpinBox,
    QComboBox, QCheckBox, QApplication,
    QListWidget, QListWidgetItem, QStackedWidget, QSplitter, QWidget, QStyle,
    QTreeWidget, QTreeWidgetItem,
)

DATA_DIR_KEY           = "config/data_dir"
UNRAR_TOOL_KEY         = "config/unrar_tool"

# Defaults for per-library settings (non-platform-specific)
MAX_THUMBS_DEFAULT     = 5
MAX_THUMB_SIZE_DEFAULT = 512


def _platform_defaults() -> dict:
    """Return a dict of default values that differ by operating system.

    Keys
    ----
    data_dir        : str   — default location for the database and thumbnails
    color_scheme    : str   — initial color theme name (must be a key in THEMES)
    unrar_hint      : str   — placeholder text shown in the unrar path field
    max_workers     : int   — default number of parallel crawler threads
    """
    cpu = os.cpu_count() or 2
    # Use half the logical CPU count, clamped to [2, 8]
    workers = max(2, min(cpu // 2, 8))

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home()))
        data_dir       = str(Path(local_app_data) / "STL Database")
        color_scheme   = "Windows 11 Dark"
        unrar_hint     = (
            r"C:\Program Files\WinRAR\UnRAR.exe  "
            r"(or C:\Program Files (x86)\WinRAR\UnRAR.exe)"
        )
    elif sys.platform == "darwin":
        data_dir     = str(Path.home() / "Library" / "Application Support" / "STL Database")
        color_scheme = "System Default"
        unrar_hint   = "/opt/homebrew/bin/unrar  (or /usr/local/bin/unrar)"
    else:
        # Linux / BSD — follow XDG Base Directory spec
        xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        data_dir     = str(Path(xdg_data) / "stl_database")
        color_scheme = "System Default"
        unrar_hint   = "/usr/bin/unrar"

    return {
        "data_dir":     data_dir,
        "color_scheme": color_scheme,
        "unrar_hint":   unrar_hint,
        "max_workers":  workers,
    }


_PLATFORM = _platform_defaults()

DATA_DIR_DEFAULT     = _PLATFORM["data_dir"]
COLOR_SCHEME_DEFAULT = _PLATFORM["color_scheme"]

_LIBRARY_SETTINGS_FILE = "settings.json"

# Map display name -> path relative to the package root (or "" for system default).
# Values prefixed with "material:" are routed to qt_material instead of a .qss file.
_THEMES_DIR = Path(__file__).parent.parent.parent / "resources" / "themes"
THEMES: dict[str, str] = {
    "System Default":       "",
    "VS Code Dark+":        str(_THEMES_DIR / "vscode_dark.qss"),
    "Windows 11 Dark":      str(_THEMES_DIR / "win11_dark.qss"),
    "Windows 11 Light":     str(_THEMES_DIR / "win11_light.qss"),
    # ── Material Design (qt_material) ────────────────────────────────────────
    "Material Dark Blue":   "material:dark_blue.xml",
    "Material Dark Teal":   "material:dark_teal.xml",
    "Material Dark Purple": "material:dark_purple.xml",
    "Material Dark Amber":  "material:dark_amber.xml",
    "Material Dark Red":    "material:dark_red.xml",
    "Material Light Blue":  "material:light_blue_500.xml",
    "Material Light Teal":  "material:light_teal.xml",
    "Material Light Purple":"material:light_purple.xml",
    "Material Light Orange":"material:light_orange.xml",
}


def get_data_dir() -> str:
    return QSettings().value(DATA_DIR_KEY, DATA_DIR_DEFAULT)


def get_unrar_path() -> str:
    """Return the configured path to the unrar/rar executable, or '' if not set."""
    return QSettings().value(UNRAR_TOOL_KEY, "")


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


def _parse_qss_token(qss_text: str, token: str) -> str | None:
    """Extract a hex color from a QSS comment token like '--pair-a: #rrggbb'."""
    m = re.search(rf"--{re.escape(token)}:\s*(#[0-9a-fA-F]{{3,8}})", qss_text)
    return m.group(1) if m else None


def get_pair_colors() -> tuple[str, str]:
    """Return two hex color strings for duplicate-pair row highlighting.

    Colors are read from the active theme's QSS ``--pair-a`` / ``--pair-b``
    tokens.  For Material and System Default themes (no QSS file), colors are
    derived from the active QPalette's Highlight role so they adapt to the theme.
    """
    from PyQt6.QtGui import QColor, QPalette
    from PyQt6.QtWidgets import QApplication

    scheme = get_color_scheme()
    value  = THEMES.get(scheme, "")
    if value and not value.startswith("material:"):
        qss_path = Path(value)
        if qss_path.exists():
            qss = qss_path.read_text(encoding="utf-8")
            a = _parse_qss_token(qss, "pair-a")
            b = _parse_qss_token(qss, "pair-b")
            if a and b:
                return a, b

    # Fallback: derive from the active QPalette Highlight color
    palette   = QApplication.palette() if QApplication.instance() else QPalette()
    highlight = palette.color(QPalette.ColorRole.Highlight)
    h, s, v, _ = highlight.getHsvF()
    s2 = min(s * 0.8, 1.0)
    v2 = min(v * 0.55, 1.0)
    color_a = QColor.fromHsvF(h, s2, v2)
    color_b = QColor.fromHsvF((h + 0.5) % 1.0, s2, v2)
    return color_a.name(), color_b.name()


def get_allow_file_delete() -> bool:
    return bool(_load_library_settings().get("allow_file_delete", False))


def get_use_trash_folder() -> bool:
    return bool(_load_library_settings().get("use_trash_folder", True))


def get_trash_folder_path() -> str:
    return _load_library_settings().get("trash_folder_path", "")

def get_file_sorting_enabled() -> bool:
    return bool(_load_library_settings().get("file_sorting_enabled", False))


def get_max_workers() -> int:
    return int(_load_library_settings().get("max_workers", _PLATFORM["max_workers"]))


def get_warn_presupported_stl() -> bool:
    """Whether to warn when an archive contains presupported STL files."""
    return bool(_load_library_settings().get("warn_presupported_stl", True))


def get_warn_no_images() -> bool:
    """Whether to warn when an archive contains no image files."""
    return bool(_load_library_settings().get("warn_no_images", True))


def get_warn_nested_archive() -> bool:
    """Whether to warn when an archive contains another compressed archive."""
    return bool(_load_library_settings().get("warn_nested_archive", True))


def get_warn_supports() -> bool:
    """Whether to warn when an archive contains files matching the supports patterns."""
    return bool(_load_library_settings().get("warn_supports", False))


def get_supports_patterns() -> list[str]:
    """Return the list of regex patterns used by the 'archive with supports' warning."""
    from app.crawler.rules import DEFAULT_SUPPORTS_PATTERNS
    return list(_load_library_settings().get("supports_patterns", DEFAULT_SUPPORTS_PATTERNS))


def get_debug_menu_enabled() -> bool:
    """Whether the Debug menu is visible in the main window."""
    return bool(_load_library_settings().get("debug_menu_enabled", False))


def get_metadata_sidecar_enabled() -> bool:
    """Whether per-folder METADATA.7z sidecar files are created at all."""
    return bool(_load_library_settings().get("metadata_sidecar_enabled", True))


def get_metadata_thumbnails_enabled() -> bool:
    """Whether thumbnails are included in per-folder METADATA.7z sidecar files."""
    return bool(_load_library_settings().get("metadata_thumbnails", True))


def get_store_stl_file_list() -> bool:
    """Whether the crawler records the list of STL files contained in each archive."""
    return bool(_load_library_settings().get("store_stl_file_list", False))


_ICONS_DIR = Path(__file__).parent.parent.parent / "resources" / "icons"


def apply_color_scheme(scheme_name: str) -> None:
    """Apply a color scheme to the running QApplication."""
    app = QApplication.instance()
    if app is None:
        return
    value = THEMES.get(scheme_name, "")
    if value.startswith("material:"):
        from qt_material import apply_stylesheet
        apply_stylesheet(app, theme=value[len("material:"):])
    elif value and Path(value).exists():
        icons = str(_ICONS_DIR).replace("\\", "/")
        qss = Path(value).read_text(encoding="utf-8")
        qss = qss.replace("%(ICONS_DIR)s", icons)
        app.setStyleSheet(qss)
    else:
        app.setStyleSheet("")


class ConfigDialog(QDialog):
    """Multi-page settings dialog.

    Sidebar sections:
      Database | Crawler > Warnings | Browser | Appearance | Sidecar

    Settings are persisted in two places:
    - ``DATA_DIR_KEY`` and ``UNRAR_TOOL_KEY`` in ``QSettings`` (global, cross-library).
    - All other settings in a ``settings.json`` file inside the data directory
      (per-library, so each collection can have independent configuration).

    Cancelling the dialog reverts any colour-scheme preview applied during the session.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuration")
        self.setMinimumWidth(580)
        self.setMinimumHeight(300)
        self._original_scheme: str = get_color_scheme()
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # ---- Two-pane area ----
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left: section tree
        # Page indices must match the order pages are added to self._stack below.
        # 0=Database  1=Crawler  2=Warnings(child)  3=Browser  4=Appearance  5=Sidecar
        self._section_tree = QTreeWidget()
        self._section_tree.setFixedWidth(155)
        self._section_tree.setHeaderHidden(True)
        self._section_tree.setIconSize(QSize(20, 20))
        self._section_tree.setRootIsDecorated(True)

        sp    = QStyle.StandardPixmap
        style = QApplication.style()

        def _item(parent, label: str, pixmap, page_idx: int) -> QTreeWidgetItem:
            item = QTreeWidgetItem(parent, [label])
            item.setIcon(0, style.standardIcon(pixmap))
            item.setData(0, Qt.ItemDataRole.UserRole, page_idx)
            return item

        _item(self._section_tree, "Database",   sp.SP_DriveHDIcon,            0)
        crawler_item = _item(self._section_tree, "Crawler", sp.SP_FileDialogDetailedView, 1)
        _item(crawler_item,          "Warnings", sp.SP_MessageBoxWarning,      2)
        fw_item = _item(self._section_tree, "File writing", sp.SP_DesktopIcon,             3)
        _item(fw_item,               "Sidecar",  sp.SP_DialogSaveButton,        5)
        _item(self._section_tree, "Appearance", sp.SP_TitleBarNormalButton,    4)

        crawler_item.setExpanded(True)
        fw_item.setExpanded(True)
        self._section_tree.currentItemChanged.connect(self._on_section_changed)

        # Right: stacked pages (indices must match the numbers assigned above)
        self._stack = QStackedWidget()

        splitter.addWidget(self._section_tree)
        splitter.addWidget(self._stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

        self._stack.addWidget(self._build_database_page())    # 0
        self._stack.addWidget(self._build_crawler_page())     # 1
        self._stack.addWidget(self._build_warnings_page())    # 2
        self._stack.addWidget(self._build_browser_page())     # 3
        self._stack.addWidget(self._build_appearance_page())  # 4
        self._stack.addWidget(self._build_sidecar_page())     # 5

        self._section_tree.setCurrentItem(
            self._section_tree.topLevelItem(0)
        )

        # ---- Buttons ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self._cancel)
        root.addWidget(buttons)

    def _on_section_changed(self, current: QTreeWidgetItem, _previous) -> None:
        if current is None:
            return
        page_idx = current.data(0, Qt.ItemDataRole.UserRole)
        if page_idx is not None:
            self._stack.setCurrentIndex(page_idx)

    # ---- Page builders ------------------------------------------------

    def _build_database_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Database")
        form = QFormLayout(group)

        path_row = QHBoxLayout()
        self._data_dir_edit = QLineEdit()
        self._data_dir_edit.setPlaceholderText(DATA_DIR_DEFAULT)
        path_row.addWidget(self._data_dir_edit)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_data_dir)
        path_row.addWidget(browse_btn)

        form.addRow("Data directory:", path_row)
        form.addRow(
            "",
            QLabel(
                "<small>All database files are stored in this directory. "
                "A restart is required for changes to take effect.</small>"
            ),
        )

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_crawler_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Crawler")
        form = QFormLayout(group)

        self._max_thumbs_spin = QSpinBox()
        self._max_thumbs_spin.setRange(0, 50)
        self._max_thumbs_spin.setSuffix(" image(s)")
        form.addRow("Max thumbnails per model:", self._max_thumbs_spin)
        form.addRow(
            "",
            QLabel("<small>Maximum number of preview images extracted from each archive.</small>"),
        )

        self._max_thumb_size_spin = QSpinBox()
        self._max_thumb_size_spin.setRange(64, 4096)
        self._max_thumb_size_spin.setSingleStep(64)
        self._max_thumb_size_spin.setSuffix(" px")
        form.addRow("Max image size:", self._max_thumb_size_spin)
        form.addRow(
            "",
            QLabel("<small>Longest side in pixels when storing images (width & height capped at this value).</small>"),
        )

        self._max_workers_spin = QSpinBox()
        self._max_workers_spin.setRange(1, 32)
        self._max_workers_spin.setSuffix(" thread(s)")
        form.addRow("Parallel workers:", self._max_workers_spin)
        form.addRow(
            "",
            QLabel(
                f"<small>Number of threads used to hash and extract archives in parallel. "
                f"Default for this machine: <b>{_PLATFORM['max_workers']}</b> "
                f"({os.cpu_count() or '?'} logical CPU(s) detected).</small>"
            ),
        )

        unrar_row = QHBoxLayout()
        self._unrar_edit = QLineEdit()
        self._unrar_edit.setPlaceholderText(_PLATFORM["unrar_hint"])
        unrar_row.addWidget(self._unrar_edit)

        unrar_browse_btn = QPushButton("Browse…")
        unrar_browse_btn.clicked.connect(self._browse_unrar)
        unrar_row.addWidget(unrar_browse_btn)

        form.addRow("RAR tool:", unrar_row)
        form.addRow(
            "",
            QLabel(
                "<small>Path to <b>unrar.exe</b> or <b>rar.exe</b> used to extract .rar archives. "
                "Leave blank to use the one on your PATH.</small>"
            ),
        )

        layout.addWidget(group)

        file_info_group = QGroupBox("Full File Info")
        file_info_form = QFormLayout(file_info_group)

        self._store_stl_list_check = QCheckBox("Store STL, OBJ, LYS, and Chitubox file list")
        file_info_form.addRow("", self._store_stl_list_check)
        file_info_form.addRow(
            "",
            QLabel(
                "<small>When enabled, the crawler records the name and uncompressed "
                "size of every <b>.stl</b> file found inside each archive. "
                "Stored in the <b>stl_files</b> database table. "
                "Archives already in the database that are missing file information "
                "will be automatically re-crawled to populate the missing entries.</small>"
            ),
        )

        layout.addWidget(file_info_group)
        layout.addStretch()
        return page

    def _build_warnings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Crawler Warnings")
        form  = QFormLayout(group)

        self._warn_presupported_check = QCheckBox("Archive have presupported stls")
        form.addRow("", self._warn_presupported_check)
        form.addRow(
            "",
            QLabel(
                "<small>Warn when an archive contains STL files matching "
                "<b>[ |_]presupported.stl</b> or <b>sup*.stl</b>.</small>"
            ),
        )

        self._warn_no_images_check = QCheckBox("Archive contains images")
        form.addRow("", self._warn_no_images_check)
        form.addRow(
            "",
            QLabel(
                "<small>Warn when an archive contains no image files "
                "(.jpg, .png, .webp, …).</small>"
            ),
        )

        self._warn_nested_archive_check = QCheckBox("Archive inside archive")
        form.addRow("", self._warn_nested_archive_check)
        form.addRow(
            "",
            QLabel(
                "<small>Warn when an archive contains another compressed archive "
                "(.zip, .rar, .7z).</small>"
            ),
        )

        self._warn_supports_check = QCheckBox("Archive with supports")
        form.addRow("", self._warn_supports_check)
        form.addRow(
            "",
            QLabel(
                "<small>Warn when an archive contains files matching any of the "
                "patterns below. Each pattern is a Python regex applied "
                "(case-insensitively) to every archive member filename.</small>"
            ),
        )

        # Container widget so the list + buttons can be enabled/disabled together.
        self._supports_patterns_widget = QWidget()
        sup_layout = QVBoxLayout(self._supports_patterns_widget)
        sup_layout.setContentsMargins(0, 2, 0, 0)
        sup_layout.setSpacing(4)

        self._supports_patterns_list = QListWidget()
        self._supports_patterns_list.setFixedHeight(90)
        sup_layout.addWidget(self._supports_patterns_list)

        sup_btn_row = QHBoxLayout()
        sup_add_btn = QPushButton("Add pattern")
        sup_add_btn.clicked.connect(self._add_supports_pattern)
        sup_rm_btn = QPushButton("Remove selected")
        sup_rm_btn.clicked.connect(self._remove_supports_pattern)
        sup_btn_row.addWidget(sup_add_btn)
        sup_btn_row.addWidget(sup_rm_btn)
        sup_btn_row.addStretch()
        sup_layout.addLayout(sup_btn_row)

        form.addRow("", self._supports_patterns_widget)
        self._warn_supports_check.toggled.connect(self._supports_patterns_widget.setEnabled)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_sidecar_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        
        # We now group sidecar inside "File writing" logical section, but it is its own page.
        group = QGroupBox("Metadata Sidecar (METADATA.7z)")
        form  = QFormLayout(group)

        self._metadata_sidecar_check = QCheckBox("Enable metadata sidecar")
        form.addRow("", self._metadata_sidecar_check)
        form.addRow(
            "",
            QLabel(
                "<small>When enabled, a <b>METADATA.7z</b> sidecar file is created "
                "in each folder after crawling. This allows the library to be "
                "restored on a new machine without re-crawling every archive. "
                "Disable to prevent any METADATA.7z files from being written.</small>"
            ),
        )

        self._metadata_thumbnails_check = QCheckBox("Include thumbnails")
        form.addRow("", self._metadata_thumbnails_check)
        form.addRow(
            "",
            QLabel(
                "<small>When enabled, each folder's <b>METADATA.7z</b> sidecar will "
                "contain thumbnail images alongside the metadata JSON. "
                "Disable to save disk space; thumbnails will be omitted from the "
                "sidecar and not restored when importing on a new machine.</small>"
            ),
        )

        self._metadata_sidecar_check.toggled.connect(self._metadata_thumbnails_check.setEnabled)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_browser_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("File writing")
        form = QFormLayout(group)

        self._file_sorting_check = QCheckBox("Enable file sorting")
        form.addRow("File sorting:", self._file_sorting_check)
        form.addRow(
            "",
            QLabel(
                "<small>When enabled, a <b>Sort files into directory…</b> option "
                "appears in the table right-click menu, allowing selected archive "
                "files to be moved and renamed using a path pattern.</small>"
            ),
        )

        self._allow_file_delete_check = QCheckBox("Allow file delete")
        self._allow_file_delete_check.setStyleSheet("color: red; font-weight: bold;")
        self._allow_file_delete_check.clicked.connect(self._on_allow_delete_clicked)
        form.addRow("Delete:", self._allow_file_delete_check)
        form.addRow(
            "",
            QLabel(
                "<small>When enabled, a <b>Delete Archive File from Disk…</b> option "
                "appears in the table right-click menu. This deletes the physical file.</small>"
            ),
        )

        self._use_trash_folder_check = QCheckBox("Use trash folder")
        form.addRow("Trash:", self._use_trash_folder_check)

        trash_row = QHBoxLayout()
        self._trash_folder_edit = QLineEdit()
        self._trash_folder_edit.setPlaceholderText("/path/to/trash")
        trash_row.addWidget(self._trash_folder_edit)

        trash_browse_btn = QPushButton("Browse…")
        trash_browse_btn.clicked.connect(self._browse_trash_folder)
        trash_row.addWidget(trash_browse_btn)

        form.addRow("", trash_row)
        form.addRow(
            "",
            QLabel(
                "<small>If enabled, files are moved here instead of being permanently "
                "deleted. A sidecar file with recovery info is created alongside.</small>"
            ),
        )
        
        self._allow_file_delete_check.toggled.connect(self._update_trash_enabled)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _update_trash_enabled(self, checked: bool) -> None:
        self._use_trash_folder_check.setEnabled(checked)
        self._trash_folder_edit.setEnabled(checked)
        
    def _on_allow_delete_clicked(self, checked: bool) -> None:
        if not checked:
            return
            
        # Uncheck it temporarily, only check if they accept the warning
        self._allow_file_delete_check.setChecked(False)
        
        # Build custom warning dialog with a checkbox
        dlg = QDialog(self)
        dlg.setWindowTitle("DANGER: File Deletion")
        
        l = QVBoxLayout(dlg)
        icon_lbl = QLabel()
        style = QApplication.style()
        icon_lbl.setPixmap(style.standardPixmap(style.StandardPixmap.SP_MessageBoxCritical))
        
        text_lbl = QLabel(
            "<span style='color: #d32f2f; font-weight: bold;'>Enabling this option allows the application to physically DELETE your archive files from the disk.</span><br><br>"
            "This could lead to permanent loss of data. Use with extreme caution!"
        )
        text_lbl.setWordWrap(True)
        
        h = QHBoxLayout()
        h.addWidget(icon_lbl)
        h.addWidget(text_lbl, stretch=1)
        l.addLayout(h)
        
        confirm_cb = QCheckBox("I understand this and want to continue")
        confirm_cb.setChecked(False)
        l.addWidget(confirm_cb)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        confirm_cb.toggled.connect(lambda state: btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(state))
        
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        l.addWidget(btns)
        
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._allow_file_delete_check.setChecked(True)

    def _browse_trash_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose trash directory", self._trash_folder_edit.text() or "")
        if path:
            self._trash_folder_edit.setText(path)

    def _build_appearance_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Appearance")
        form = QFormLayout(group)

        self._theme_combo = QComboBox()
        for name in THEMES:
            self._theme_combo.addItem(name)
        self._theme_combo.currentTextChanged.connect(self._preview_theme)
        form.addRow("Color scheme:", self._theme_combo)
        form.addRow(
            "",
            QLabel("<small>Preview is applied immediately. Cancel reverts to the previous scheme.</small>"),
        )

        layout.addWidget(group)

        adv_group = QGroupBox("Advanced")
        adv_form  = QFormLayout(adv_group)

        self._debug_menu_check = QCheckBox("Show Debug menu")
        adv_form.addRow("", self._debug_menu_check)
        adv_form.addRow(
            "",
            QLabel(
                "<small>Shows the <b>Debug</b> menu in the menu bar, which gives "
                "access to destructive operations such as deleting the database "
                "and log files.</small>"
            ),
        )

        layout.addWidget(adv_group)
        layout.addStretch()
        return page

    # ------------------------------------------------------------------
    def _add_supports_pattern(self) -> None:
        """Append a new editable pattern row and immediately open it for editing."""
        item = QListWidgetItem("new pattern")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._supports_patterns_list.addItem(item)
        self._supports_patterns_list.editItem(item)

    def _remove_supports_pattern(self) -> None:
        """Remove all currently selected rows from the pattern list."""
        for item in self._supports_patterns_list.selectedItems():
            self._supports_patterns_list.takeItem(self._supports_patterns_list.row(item))

    # ------------------------------------------------------------------
    def _browse_data_dir(self) -> None:
        """Open a directory picker and set the data-directory field."""
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose data directory",
            self._data_dir_edit.text() or DATA_DIR_DEFAULT,
        )
        if path:
            self._data_dir_edit.setText(path)

    def _browse_unrar(self) -> None:
        """Open a file picker to locate the unrar/rar executable."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select unrar or rar executable",
            self._unrar_edit.text() or "",
            "Executables (*.exe);;All files (*)",
        )
        if path:
            self._unrar_edit.setText(path)

    # ------------------------------------------------------------------
    def _preview_theme(self, scheme_name: str) -> None:
        apply_color_scheme(scheme_name)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        """Populate all form fields from current QSettings / library settings."""
        self._data_dir_edit.setText(get_data_dir())
        self._max_thumbs_spin.setValue(get_max_thumbnails())
        self._max_thumb_size_spin.setValue(get_max_thumb_size())

        self._max_workers_spin.setValue(get_max_workers())
        self._file_sorting_check.setChecked(get_file_sorting_enabled())
        self._unrar_edit.setText(get_unrar_path())
        self._warn_presupported_check.setChecked(get_warn_presupported_stl())
        self._warn_no_images_check.setChecked(get_warn_no_images())
        self._warn_nested_archive_check.setChecked(get_warn_nested_archive())
        
        self._allow_file_delete_check.setChecked(get_allow_file_delete())
        self._use_trash_folder_check.setChecked(get_use_trash_folder())
        self._trash_folder_edit.setText(get_trash_folder_path())
        self._update_trash_enabled(get_allow_file_delete())
        
        supports_enabled = get_warn_supports()
        self._warn_supports_check.setChecked(supports_enabled)
        self._supports_patterns_widget.setEnabled(supports_enabled)
        self._supports_patterns_list.clear()
        for pattern in get_supports_patterns():
            item = QListWidgetItem(pattern)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._supports_patterns_list.addItem(item)
        sidecar_enabled = get_metadata_sidecar_enabled()
        self._metadata_sidecar_check.setChecked(sidecar_enabled)
        self._metadata_thumbnails_check.setEnabled(sidecar_enabled)
        self._metadata_thumbnails_check.setChecked(get_metadata_thumbnails_enabled())
        self._store_stl_list_check.setChecked(get_store_stl_file_list())

        scheme = get_color_scheme()
        idx = self._theme_combo.findText(scheme)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        else:
            self._theme_combo.setCurrentIndex(0)

        self._debug_menu_check.setChecked(get_debug_menu_enabled())

    def _save(self) -> None:
        """Persist all form values to QSettings and the library settings JSON."""
        data_dir = self._data_dir_edit.text().strip() or DATA_DIR_DEFAULT
        old_dir = get_data_dir()

        # Save the data-directory pointer and global tool paths
        QSettings().setValue(DATA_DIR_KEY, data_dir)
        QSettings().setValue(UNRAR_TOOL_KEY, self._unrar_edit.text().strip())

        selected_scheme = self._theme_combo.currentText()

        # Save all other settings into the library's own settings file
        _save_library_settings(
            {
                "max_thumbnails": self._max_thumbs_spin.value(),
                "max_thumb_size": self._max_thumb_size_spin.value(),
                "max_workers":    self._max_workers_spin.value(),
                "color_scheme": selected_scheme,
                "file_sorting_enabled": self._file_sorting_check.isChecked(),
                "warn_presupported_stl": self._warn_presupported_check.isChecked(),
                "warn_no_images": self._warn_no_images_check.isChecked(),
                "warn_nested_archive": self._warn_nested_archive_check.isChecked(),
                "warn_supports": self._warn_supports_check.isChecked(),
                "supports_patterns": [
                    self._supports_patterns_list.item(i).text().strip()
                    for i in range(self._supports_patterns_list.count())
                    if self._supports_patterns_list.item(i).text().strip()
                ],
                "metadata_sidecar_enabled": self._metadata_sidecar_check.isChecked(),
                "metadata_thumbnails": self._metadata_thumbnails_check.isChecked(),
                "store_stl_file_list": self._store_stl_list_check.isChecked(),
                "debug_menu_enabled": self._debug_menu_check.isChecked(),
                "allow_file_delete": self._allow_file_delete_check.isChecked(),
                "use_trash_folder": self._use_trash_folder_check.isChecked(),
                "trash_folder_path": self._trash_folder_edit.text().strip(),
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
