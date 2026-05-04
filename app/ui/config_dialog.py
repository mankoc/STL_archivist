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
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QDialogButtonBox, QFileDialog, QMessageBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QApplication, QColorDialog, QInputDialog,
    QListWidget, QListWidgetItem, QStackedWidget, QSplitter, QWidget, QStyle,
    QTreeWidget, QTreeWidgetItem, QScrollArea, QFontComboBox,
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


# Hardcoded fallback defaults when no theme token or override is set.
_UI_COLOR_DEFAULTS: dict[str, str] = {
    "pair_a":            "",         # derives from QPalette if blank
    "pair_b":            "",         # derives from QPalette if blank
    "status_neutral":    "#888888",
    "status_ok":         "#4CAF50",
    "status_warn":       "#F44336",
    "warn_row_bg":       "#4a4a4a",
    "warn_row_fg":       "#FFD700",
    "missing_file":      "#d24646",
    "found_file":        "#3c9650",
    "match_highlight":   "#64c864",
    "selection_border":  "#1565c0",
    "selection_bg":      "#e3f2fd",
    "window_bg":         "",         # overrides QWidget background; blank = from theme
    "window_text":       "",         # overrides QWidget text color; blank = from theme
}

# Maps each key to the QSS comment-token name defined in .qss theme files.
_UI_COLOR_QSS_TOKENS: dict[str, str] = {
    "pair_a":            "pair-a",
    "pair_b":            "pair-b",
    "status_neutral":    "status-neutral",
    "status_ok":         "status-ok",
    "status_warn":       "status-warn",
    "warn_row_bg":       "warn-row-bg",
    "warn_row_fg":       "warn-row-fg",
    "missing_file":      "missing-file",
    "found_file":        "found-file",
    "match_highlight":   "match-highlight",
    "selection_border":  "selection-border",
    "selection_bg":      "selection-bg",
}


def _get_ui_color(key: str, settings: dict | None = None) -> str:
    """Resolve a UI color by key.

    Priority: per-session override → active custom scheme → QSS token → default.
    Pass *settings* to avoid a redundant JSON load when the caller already has it.
    """
    if settings is None:
        settings = _load_library_settings()

    # 1. Explicit per-session override stored in ui_color_overrides
    overrides = settings.get("ui_color_overrides", {})
    if overrides.get(key):
        return overrides[key]

    # 2. Legacy keys for pair colors (backwards compat with older settings files)
    if key == "pair_a" and settings.get("custom_pair_a"):
        return settings["custom_pair_a"]
    if key == "pair_b" and settings.get("custom_pair_b"):
        return settings["custom_pair_b"]

    # 3. Active custom scheme — also supports old pair_a/pair_b at top level
    scheme = settings.get("color_scheme", COLOR_SCHEME_DEFAULT)
    custom_schemes = settings.get("custom_color_schemes", {})
    base_scheme = scheme
    if scheme in custom_schemes:
        cs = custom_schemes[scheme]
        cs_overrides = cs.get("ui_color_overrides", {})
        if cs_overrides.get(key):
            return cs_overrides[key]
        # Old format stored pair_a/pair_b at top level of custom scheme
        if key in ("pair_a", "pair_b") and cs.get(key):
            return cs[key]
        base_scheme = cs.get("base_theme", "System Default")

    # 4. QSS comment token from the active built-in theme file
    token = _UI_COLOR_QSS_TOKENS.get(key)
    if token:
        value = THEMES.get(base_scheme, "")
        if value and not value.startswith("material:"):
            qss_path = Path(value)
            if qss_path.exists():
                color = _parse_qss_token(qss_path.read_text(encoding="utf-8"), token)
                if color:
                    return color

    return _UI_COLOR_DEFAULTS.get(key, "")


def get_pair_colors() -> tuple[str, str]:
    """Return two hex color strings for duplicate-pair row highlighting.

    Delegates to ``_get_ui_color``; falls back to QPalette when no color is set.
    """
    from PyQt6.QtGui import QPalette

    settings = _load_library_settings()
    a = _get_ui_color("pair_a", settings)
    b = _get_ui_color("pair_b", settings)
    if a and b:
        return a, b

    # Fallback: derive complementary colors from the active QPalette Highlight
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
    """Whether to warn when an archive contains supported STL files."""
    return bool(_load_library_settings().get("warn_presupported_stl", True))


def get_presupport_patterns() -> list[str]:
    """Return the regex patterns used to detect supported STL files."""
    from app.crawler.rules import DEFAULT_PRESUPPORT_PATTERNS
    return list(_load_library_settings().get("presupport_patterns", DEFAULT_PRESUPPORT_PATTERNS))


def get_supports_patterns() -> list[str]:
    """Alias for get_presupport_patterns — used by settings_loader."""
    return get_presupport_patterns()


def get_font_family() -> str:
    """Return the user-configured font family, or empty string for the theme default."""
    return str(_load_library_settings().get("font_family", ""))


def get_font_size() -> int:
    """Return the user-configured font size in points, or 0 for the theme default."""
    return int(_load_library_settings().get("font_size", 0))


def get_warn_supports() -> bool:
    """Whether to warn when an archive contains supported STL files (supports check)."""
    return bool(_load_library_settings().get("warn_supports", False))


def get_warn_no_images() -> bool:
    """Whether to warn when an archive contains no image files."""
    return bool(_load_library_settings().get("warn_no_images", True))


def get_warn_nested_archive() -> bool:
    """Whether to warn when an archive contains another compressed archive."""
    return bool(_load_library_settings().get("warn_nested_archive", True))


def get_warn_low_compression() -> bool:
    """Whether to warn when an archive has a low compression rate."""
    return bool(_load_library_settings().get("warn_low_compression", False))


def get_low_compression_threshold() -> float:
    """Compression rate threshold above which the low-compression warning fires."""
    return float(_load_library_settings().get("low_compression_threshold", 0.95))



def get_status_neutral_color() -> str:
    return _get_ui_color("status_neutral")

def get_status_ok_color() -> str:
    return _get_ui_color("status_ok")

def get_status_warn_color() -> str:
    return _get_ui_color("status_warn")

def get_warn_row_bg_color() -> str:
    return _get_ui_color("warn_row_bg")

def get_warn_row_fg_color() -> str:
    return _get_ui_color("warn_row_fg")

def get_missing_file_color() -> str:
    return _get_ui_color("missing_file")

def get_found_file_color() -> str:
    return _get_ui_color("found_file")

def get_match_highlight_color() -> str:
    return _get_ui_color("match_highlight")

def get_selection_border_color() -> str:
    return _get_ui_color("selection_border")

def get_selection_bg_color() -> str:
    return _get_ui_color("selection_bg")


def get_custom_color_schemes() -> dict:
    """Return the dict of user-saved custom color schemes."""
    return dict(_load_library_settings().get("custom_color_schemes", {}))


def get_debug_menu_enabled() -> bool:
    """Whether the Debug menu is visible in the main window."""
    return bool(_load_library_settings().get("debug_menu_enabled", False))


def get_logging_enabled() -> bool:
    """Whether file (and stderr) logging is active. Defaults to True."""
    return bool(_load_library_settings().get("logging_enabled", True))


def get_metadata_sidecar_enabled() -> bool:
    """Whether per-folder METADATA.7z sidecar files are created at all."""
    return bool(_load_library_settings().get("metadata_sidecar_enabled", True))


def get_metadata_thumbnails_enabled() -> bool:
    """Whether thumbnails are included in per-folder METADATA.7z sidecar files."""
    return bool(_load_library_settings().get("metadata_thumbnails", True))


def get_store_stl_file_list() -> bool:
    """Whether the crawler records the list of STL files contained in each archive."""
    return bool(_load_library_settings().get("store_stl_file_list", False))


def get_recurse_subdirs() -> bool:
    """Whether the crawler should recurse into subdirectories of the selected folder."""
    return bool(_load_library_settings().get("recurse_subdirs", True))


_ICONS_DIR = Path(__file__).parent.parent.parent / "resources" / "icons"


def _build_user_override_css(settings: dict) -> str:
    """Build a QSS snippet applying window-color and font overrides from *settings*."""
    overrides = settings.get("ui_color_overrides", {})
    window_bg   = overrides.get("window_bg", "")
    window_text = overrides.get("window_text", "")
    font_family = settings.get("font_family", "")
    font_size   = int(settings.get("font_size", 0))

    parts: list[str] = []
    if window_bg:
        parts.append(f"background-color: {window_bg};")
    if window_text:
        parts.append(f"color: {window_text};")
    if font_family:
        parts.append(f'font-family: "{font_family}";')
    if font_size > 0:
        parts.append(f"font-size: {font_size}px;")

    if not parts:
        return ""
    rule = " ".join(parts)
    return (
        f"QWidget {{ {rule} }}\n"
        f"QMainWindow {{ {rule} }}\n"
        f"QDialog {{ {rule} }}\n"
    )


def apply_color_scheme(scheme_name: str) -> None:
    """Apply a color scheme to the running QApplication."""
    app = QApplication.instance()
    if app is None:
        return
    settings = _load_library_settings()
    # Resolve user-saved custom schemes to their base built-in theme
    custom_schemes = settings.get("custom_color_schemes", {})
    if scheme_name in custom_schemes:
        scheme_name = custom_schemes[scheme_name].get("base_theme", "System Default")
    value = THEMES.get(scheme_name, "")
    override_css = _build_user_override_css(settings)
    if value.startswith("material:"):
        from qt_material import apply_stylesheet
        apply_stylesheet(app, theme=value[len("material:"):])
        if override_css:
            app.setStyleSheet(app.styleSheet() + "\n" + override_css)
    elif value and Path(value).exists():
        icons = str(_ICONS_DIR).replace("\\", "/")
        qss = Path(value).read_text(encoding="utf-8")
        qss = qss.replace("%(ICONS_DIR)s", icons)
        app.setStyleSheet(qss + "\n" + override_css)
    else:
        app.setStyleSheet(override_css)


class ConfigDialog(QDialog):
    """Multi-page settings dialog.

    Sidebar sections:
      Database | Crawler > Warnings | File writing > Sidecar | Appearance | Debug

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
        self._ui_color_btns: dict[str, QPushButton] = {}
        self._ui_color_overrides: dict[str, str] = {}
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
        # 0=Database  1=Crawler  2=Warnings(child)  3=File writing  4=Appearance
        # 5=Sidecar(child)  6=Debug
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

        _item(self._section_tree, "Database",     sp.SP_DriveHDIcon,              0)
        crawler_item = _item(self._section_tree, "Crawler", sp.SP_FileDialogDetailedView, 1)
        _item(crawler_item,            "Warnings", sp.SP_MessageBoxWarning,        2)
        fw_item = _item(self._section_tree, "File writing", sp.SP_DesktopIcon,     3)
        _item(fw_item,                 "Sidecar",  sp.SP_DialogSaveButton,         5)
        _item(self._section_tree, "Appearance",   sp.SP_TitleBarNormalButton,      4)
        _item(self._section_tree, "Debug",        sp.SP_MessageBoxCritical,        6)

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
        self._stack.addWidget(self._build_debug_page())       # 6

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

        # Recurse option
        recurse_group = QGroupBox("Crawl Scope")
        recurse_form = QFormLayout(recurse_group)
        self._recurse_subdirs_check = QCheckBox("Recurse subdirectories")
        recurse_form.addRow("Recurse subdirs:", self._recurse_subdirs_check)
        recurse_form.addRow(
            "",
            QLabel(
                "<small>When enabled, the crawler will search the selected folder and all its subfolders. "
                "When disabled, only archives directly inside the selected folder are processed.</small>"
            ),
        )
        layout.addWidget(recurse_group)

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

        self._warn_presupported_check = QCheckBox("Archive contains supported STLs")
        form.addRow("", self._warn_presupported_check)
        form.addRow(
            "",
            QLabel(
                "<small>Warn when an archive contains STL files matching any of the "
                "patterns below. Each pattern is a Python regex applied "
                "(case-insensitively) to every archive member filename.</small>"
            ),
        )

        self._presupport_patterns_widget = QWidget()
        pp_layout = QVBoxLayout(self._presupport_patterns_widget)
        pp_layout.setContentsMargins(0, 2, 0, 0)
        pp_layout.setSpacing(4)

        self._presupport_patterns_list = QListWidget()
        self._presupport_patterns_list.setFixedHeight(80)
        pp_layout.addWidget(self._presupport_patterns_list)

        pp_btn_row = QHBoxLayout()
        pp_add_btn = QPushButton("Add pattern")
        pp_add_btn.clicked.connect(self._add_presupport_pattern)
        pp_rm_btn = QPushButton("Remove selected")
        pp_rm_btn.clicked.connect(self._remove_presupport_pattern)
        pp_btn_row.addWidget(pp_add_btn)
        pp_btn_row.addWidget(pp_rm_btn)
        pp_btn_row.addStretch()
        pp_layout.addLayout(pp_btn_row)

        form.addRow("", self._presupport_patterns_widget)
        self._warn_presupported_check.toggled.connect(self._presupport_patterns_widget.setEnabled)

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

        self._warn_low_compression_check = QCheckBox("Archive with low compression rate")
        form.addRow("", self._warn_low_compression_check)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("Threshold (compressed/uncompressed ratio):"))
        self._low_compression_threshold_spin = QDoubleSpinBox()
        self._low_compression_threshold_spin.setRange(0.50, 1.00)
        self._low_compression_threshold_spin.setSingleStep(0.01)
        self._low_compression_threshold_spin.setDecimals(2)
        self._low_compression_threshold_spin.setValue(0.95)
        self._low_compression_threshold_spin.setFixedWidth(80)
        threshold_row.addWidget(self._low_compression_threshold_spin)
        threshold_row.addStretch()
        form.addRow("", threshold_row)
        form.addRow(
            "",
            QLabel(
                "<small>Warn when compressed size ÷ uncompressed size exceeds this value "
                "(e.g. 0.95 = 95 %). Values near 1.0 indicate little or no compression.</small>"
            ),
        )
        self._warn_low_compression_check.toggled.connect(
            self._low_compression_threshold_spin.setEnabled
        )

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

    def _build_debug_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox("Debug")
        form = QFormLayout(group)

        self._debug_menu_check = QCheckBox("Show Debug menu")
        form.addRow("", self._debug_menu_check)
        form.addRow(
            "",
            QLabel(
                "<small>Shows the <b>Debug</b> menu in the menu bar, which gives "
                "access to destructive operations such as deleting the database "
                "and log files.</small>"
            ),
        )

        self._logging_enabled_check = QCheckBox("Enable logging")
        form.addRow("", self._logging_enabled_check)
        form.addRow(
            "",
            QLabel(
                "<small>Write application events and errors to the log file. "
                "Uncheck to silence all log output. "
                "Uncaught exceptions are always captured regardless of this setting.</small>"
            ),
        )

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
        # Outer page widget holds only the scroll area (no extra margins)
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(4, 4, 4, 4)

        # ---- Color Scheme ------------------------------------------------
        theme_group = QGroupBox("Color Scheme")
        form = QFormLayout(theme_group)
        self._theme_combo = QComboBox()
        for name in THEMES:
            self._theme_combo.addItem(name)
        self._theme_combo.currentTextChanged.connect(self._preview_theme)
        form.addRow("Scheme:", self._theme_combo)
        form.addRow(
            "",
            QLabel("<small>Preview is applied immediately. Cancel reverts to the previous scheme.</small>"),
        )
        layout.addWidget(theme_group)

        # ---- Duplicate Highlight Colors ----------------------------------
        pair_group = QGroupBox("Duplicate Highlight Colors")
        pair_form = QFormLayout(pair_group)
        self._add_color_row(pair_form, "pair_a", "Pair A:")
        self._add_color_row(pair_form, "pair_b", "Pair B:")
        pair_form.addRow(
            "",
            QLabel("<small>Row highlight colors used in the duplicate finder.</small>"),
        )
        layout.addWidget(pair_group)

        # ---- Status Indicator Colors -------------------------------------
        status_group = QGroupBox("Status Indicators")
        status_form = QFormLayout(status_group)
        self._add_color_row(status_form, "status_neutral", "Neutral:")
        self._add_color_row(status_form, "status_ok",      "OK:")
        self._add_color_row(status_form, "status_warn",    "Warning:")
        status_form.addRow(
            "",
            QLabel("<small>Circle indicators drawn on group items in the browser tree.</small>"),
        )
        layout.addWidget(status_group)

        # ---- Warning Row Colors ------------------------------------------
        warn_group = QGroupBox("Warning Rows")
        warn_form = QFormLayout(warn_group)
        self._add_color_row(warn_form, "warn_row_bg", "Background:")
        self._add_color_row(warn_form, "warn_row_fg", "Text:")
        warn_form.addRow(
            "",
            QLabel("<small>Row colors for archive entries flagged with a warning in the browser.</small>"),
        )
        layout.addWidget(warn_group)

        # ---- Maintenance View Colors -------------------------------------
        maint_group = QGroupBox("Maintenance View")
        maint_form = QFormLayout(maint_group)
        self._add_color_row(maint_form, "missing_file", "Missing file:")
        self._add_color_row(maint_form, "found_file",   "Found file:")
        maint_form.addRow(
            "",
            QLabel("<small>Row colors in the database maintenance / verification table.</small>"),
        )
        layout.addWidget(maint_group)

        # ---- Highlight Colors --------------------------------------------
        hl_group = QGroupBox("Highlights")
        hl_form = QFormLayout(hl_group)
        self._add_color_row(hl_form, "match_highlight",   "File match:")
        self._add_color_row(hl_form, "selection_border",  "Selection border:")
        self._add_color_row(hl_form, "selection_bg",      "Selection background:")
        hl_form.addRow(
            "",
            QLabel(
                "<small>File match: translucent tint on matched rows in the file-comparison table. "
                "Selection: thumbnail selection in the image grid.</small>"
            ),
        )
        layout.addWidget(hl_group)

        # ---- Window Colors ----------------------------------------------
        wc_group = QGroupBox("Window Colors")
        wc_form = QFormLayout(wc_group)
        self._add_color_row(wc_form, "window_bg",   "Background:")
        self._add_color_row(wc_form, "window_text", "Text:")
        wc_form.addRow(
            "",
            QLabel("<small>Override the background and text color for all windows. "
                   "Leave blank to use the active theme's colors.</small>"),
        )
        layout.addWidget(wc_group)

        # ---- Typography -------------------------------------------------
        font_group = QGroupBox("Typography")
        font_form = QFormLayout(font_group)

        self._font_family_combo = QFontComboBox()
        self._font_family_combo.setEditable(True)
        self._font_family_combo.setCurrentFont(QApplication.font())
        font_form.addRow("Font family:", self._font_family_combo)

        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(6, 48)
        self._font_size_spin.setValue(QApplication.font().pointSize())
        self._font_size_spin.setSuffix(" pt")
        self._font_size_spin.setSpecialValueText("(theme default)")
        self._font_size_spin.setMinimum(0)
        font_form.addRow("Font size:", self._font_size_spin)

        font_reset_btn = QPushButton("Reset to theme font")
        font_reset_btn.clicked.connect(self._reset_font)
        font_form.addRow("", font_reset_btn)

        font_form.addRow(
            "",
            QLabel("<small>Changes take effect after clicking OK. "
                   "Set size to 0 to use the theme default.</small>"),
        )
        layout.addWidget(font_group)

        # ---- Global actions ---------------------------------------------
        action_row = QHBoxLayout()
        reset_btn = QPushButton("Reset all to theme defaults")
        reset_btn.clicked.connect(self._reset_all_colors)
        save_scheme_btn = QPushButton("Save as color scheme…")
        save_scheme_btn.clicked.connect(self._save_as_color_scheme)
        action_row.addWidget(reset_btn)
        action_row.addWidget(save_scheme_btn)
        action_row.addStretch()
        layout.addLayout(action_row)
        layout.addStretch()

        scroll.setWidget(inner)
        page_layout.addWidget(scroll)
        return page

    # ------------------------------------------------------------------
    def _add_presupport_pattern(self) -> None:
        item = QListWidgetItem("new pattern")
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._presupport_patterns_list.addItem(item)
        self._presupport_patterns_list.editItem(item)

    def _remove_presupport_pattern(self) -> None:
        for item in self._presupport_patterns_list.selectedItems():
            self._presupport_patterns_list.takeItem(self._presupport_patterns_list.row(item))

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
    def _add_color_row(self, form: QFormLayout, key: str, label: str) -> None:
        """Add a swatch-button row to *form* for the given color *key*."""
        row = QHBoxLayout()
        btn = QPushButton()
        btn.setFixedSize(40, 20)
        btn.setFlat(True)
        btn.clicked.connect(lambda _checked, k=key: self._pick_ui_color(k))
        self._ui_color_btns[key] = btn
        row.addWidget(btn)
        row.addStretch()
        form.addRow(label, row)

    def _pick_ui_color(self, key: str) -> None:
        current = self._ui_color_overrides.get(key) or _get_ui_color(key)
        color = QColorDialog.getColor(
            QColor(current) if current else QColor(),
            self,
        )
        if color.isValid():
            self._ui_color_overrides[key] = color.name()
            self._refresh_ui_color_btn(key)

    def _refresh_ui_color_btn(self, key: str) -> None:
        btn = self._ui_color_btns.get(key)
        if btn is None:
            return
        color = self._ui_color_overrides.get(key) or _get_ui_color(key)
        if color:
            btn.setStyleSheet(f"background-color: {color}; border: 1px solid #888;")
        else:
            btn.setStyleSheet("border: 1px solid #888;")

    def _refresh_all_ui_color_btns(self) -> None:
        for key in self._ui_color_btns:
            self._refresh_ui_color_btn(key)

    def _reset_all_colors(self) -> None:
        """Clear all overrides so colors fall back to theme/QSS defaults."""
        self._ui_color_overrides.clear()
        self._refresh_all_ui_color_btns()

    def _reset_font(self) -> None:
        """Clear the font overrides back to theme defaults."""
        self._font_family_combo.setCurrentFont(QApplication.font())
        self._font_size_spin.setValue(0)

    # ------------------------------------------------------------------
    def _preview_theme(self, scheme_name: str) -> None:
        apply_color_scheme(scheme_name)
        # If a saved custom scheme is selected, load its color overrides into the pickers
        custom_schemes = _load_library_settings().get("custom_color_schemes", {})
        if scheme_name in custom_schemes:
            cs = custom_schemes[scheme_name]
            self._ui_color_overrides = dict(cs.get("ui_color_overrides", {}))
            # Handle old format where pair_a/pair_b were stored at top level
            for k in ("pair_a", "pair_b"):
                if k not in self._ui_color_overrides and cs.get(k):
                    self._ui_color_overrides[k] = cs[k]
            self._refresh_all_ui_color_btns()

    def _save_as_color_scheme(self) -> None:
        name, ok = QInputDialog.getText(self, "Save Color Scheme", "Scheme name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in THEMES:
            QMessageBox.warning(
                self, "Save Color Scheme",
                f"'{name}' is a built-in scheme and cannot be overwritten.",
            )
            return

        settings = _load_library_settings()
        custom_schemes = settings.get("custom_color_schemes", {})

        base_theme = self._theme_combo.currentText()
        if base_theme in custom_schemes:
            base_theme = custom_schemes[base_theme].get("base_theme", "System Default")

        custom_schemes[name] = {
            "base_theme":       base_theme,
            "ui_color_overrides": dict(self._ui_color_overrides),
        }
        settings["custom_color_schemes"] = custom_schemes
        _save_library_settings(settings)

        if self._theme_combo.findText(name) < 0:
            self._theme_combo.addItem(name)
        self._theme_combo.setCurrentText(name)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        """Populate all form fields from current QSettings / library settings."""
        self._data_dir_edit.setText(get_data_dir())
        self._max_thumbs_spin.setValue(get_max_thumbnails())
        self._max_thumb_size_spin.setValue(get_max_thumb_size())

        self._max_workers_spin.setValue(get_max_workers())
        self._file_sorting_check.setChecked(get_file_sorting_enabled())
        self._unrar_edit.setText(get_unrar_path())
        presupported_enabled = get_warn_presupported_stl()
        self._warn_presupported_check.setChecked(presupported_enabled)
        self._presupport_patterns_widget.setEnabled(presupported_enabled)
        self._presupport_patterns_list.clear()
        for pattern in get_presupport_patterns():
            item = QListWidgetItem(pattern)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._presupport_patterns_list.addItem(item)
        self._warn_no_images_check.setChecked(get_warn_no_images())
        self._warn_nested_archive_check.setChecked(get_warn_nested_archive())
        warn_low_compression = get_warn_low_compression()
        self._warn_low_compression_check.setChecked(warn_low_compression)
        self._low_compression_threshold_spin.setValue(get_low_compression_threshold())
        self._low_compression_threshold_spin.setEnabled(warn_low_compression)
        
        self._allow_file_delete_check.setChecked(get_allow_file_delete())
        self._use_trash_folder_check.setChecked(get_use_trash_folder())
        self._trash_folder_edit.setText(get_trash_folder_path())
        self._update_trash_enabled(get_allow_file_delete())
        
        sidecar_enabled = get_metadata_sidecar_enabled()
        self._metadata_sidecar_check.setChecked(sidecar_enabled)
        self._metadata_thumbnails_check.setEnabled(sidecar_enabled)
        self._metadata_thumbnails_check.setChecked(get_metadata_thumbnails_enabled())
        self._store_stl_list_check.setChecked(get_store_stl_file_list())
        self._recurse_subdirs_check.setChecked(get_recurse_subdirs())

        # Populate custom saved schemes into the dropdown (after a separator)
        custom_schemes = get_custom_color_schemes()
        if custom_schemes:
            # Remove any previously added custom entries first
            n_builtin = len(THEMES)
            while self._theme_combo.count() > n_builtin:
                self._theme_combo.removeItem(self._theme_combo.count() - 1)
            self._theme_combo.insertSeparator(n_builtin)
            for cs_name in custom_schemes:
                self._theme_combo.addItem(cs_name)

        scheme = get_color_scheme()
        idx = self._theme_combo.findText(scheme)
        if idx >= 0:
            self._theme_combo.setCurrentIndex(idx)
        else:
            self._theme_combo.setCurrentIndex(0)

        # Load all UI color overrides from settings
        settings = _load_library_settings()
        self._ui_color_overrides = dict(settings.get("ui_color_overrides", {}))
        # Migrate legacy custom_pair_a / custom_pair_b into the new dict
        for leg_key, new_key in (("custom_pair_a", "pair_a"), ("custom_pair_b", "pair_b")):
            if leg_key in settings and settings[leg_key] and new_key not in self._ui_color_overrides:
                self._ui_color_overrides[new_key] = settings[leg_key]
        self._refresh_all_ui_color_btns()

        # Load font settings
        family = settings.get("font_family", "")
        if family:
            self._font_family_combo.setCurrentFont(QFont(family))
        else:
            self._font_family_combo.setCurrentFont(QApplication.font())
        size = int(settings.get("font_size", 0))
        self._font_size_spin.setValue(size)

        self._debug_menu_check.setChecked(get_debug_menu_enabled())
        self._logging_enabled_check.setChecked(get_logging_enabled())

    def _save(self) -> None:
        """Persist all form values to QSettings and the library settings JSON."""
        data_dir = self._data_dir_edit.text().strip() or DATA_DIR_DEFAULT
        old_dir = get_data_dir()

        # Save the data-directory pointer and global tool paths
        QSettings().setValue(DATA_DIR_KEY, data_dir)
        QSettings().setValue(UNRAR_TOOL_KEY, self._unrar_edit.text().strip())

        selected_scheme = self._theme_combo.currentText()

        # Preserve existing custom_color_schemes (not edited here)
        existing = _load_library_settings()
        custom_color_schemes = existing.get("custom_color_schemes", {})

        # Save all other settings into the library's own settings file
        font_family = self._font_family_combo.currentFont().family()
        font_size   = self._font_size_spin.value()

        _save_library_settings(
            {
                "max_thumbnails":    self._max_thumbs_spin.value(),
                "max_thumb_size":    self._max_thumb_size_spin.value(),
                "max_workers":       self._max_workers_spin.value(),
                "color_scheme":      selected_scheme,
                "ui_color_overrides": self._ui_color_overrides,
                "custom_color_schemes": custom_color_schemes,
                "file_sorting_enabled": self._file_sorting_check.isChecked(),
                "warn_presupported_stl": self._warn_presupported_check.isChecked(),
                "presupport_patterns": [
                    self._presupport_patterns_list.item(i).text().strip()
                    for i in range(self._presupport_patterns_list.count())
                    if self._presupport_patterns_list.item(i).text().strip()
                ],
                "warn_no_images": self._warn_no_images_check.isChecked(),
                "warn_nested_archive": self._warn_nested_archive_check.isChecked(),
                "warn_low_compression": self._warn_low_compression_check.isChecked(),
                "low_compression_threshold": self._low_compression_threshold_spin.value(),
                "metadata_sidecar_enabled": self._metadata_sidecar_check.isChecked(),
                "metadata_thumbnails": self._metadata_thumbnails_check.isChecked(),
                "store_stl_file_list": self._store_stl_list_check.isChecked(),
                "recurse_subdirs": self._recurse_subdirs_check.isChecked(),
                "debug_menu_enabled": self._debug_menu_check.isChecked(),
                "logging_enabled":    self._logging_enabled_check.isChecked(),
                "allow_file_delete": self._allow_file_delete_check.isChecked(),
                "use_trash_folder": self._use_trash_folder_check.isChecked(),
                "trash_folder_path": self._trash_folder_edit.text().strip(),
                "font_family": font_family,
                "font_size":   font_size,
            },
            data_dir=data_dir,
        )

        # Re-apply appearance so font/color changes take effect immediately
        apply_color_scheme(selected_scheme)

        # Apply logging toggle immediately so the change doesn't require a restart
        import logging as _logging
        from app.utils.logger import setup_logging, disable_logging
        if self._logging_enabled_check.isChecked():
            if not _logging.getLogger().handlers:
                setup_logging(data_dir)
        else:
            disable_logging()

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
