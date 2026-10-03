"""
Configuration dialog.
The active data-directory is persisted via QSettings (one global pointer).
All other settings are stored in a JSON file inside the data directory so
that each library has its own independent configuration.
"""
import functools
import json
import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QSettings, QSize, Qt, QT_TRANSLATE_NOOP, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QDialogButtonBox, QFileDialog, QMessageBox, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QApplication, QColorDialog, QInputDialog,
    QListWidget, QListWidgetItem, QStackedWidget, QSplitter, QWidget, QStyle,
    QTreeWidget, QTreeWidgetItem, QScrollArea, QFontComboBox, QKeySequenceEdit,
)

from app.core import settings as _core
# Non-visual settings live in app.core.settings; re-exported here so existing
# ``from app.ui.config_dialog import get_…`` imports keep working.
from app.core.settings import (  # noqa: F401
    MAX_THUMBS_DEFAULT, MAX_THUMB_SIZE_DEFAULT, DATA_DIR_DEFAULT,
    _load_library_settings, _save_library_settings, _library_settings_path,
    get_data_dir, get_unrar_path, get_db_path,
    get_max_thumbnails, get_max_thumb_size,
    get_allow_file_delete, get_use_trash_folder, get_trash_folder_path,
    get_file_sorting_enabled, get_max_workers,
    get_warn_presupported_stl, get_presupport_patterns,
    get_warn_no_images, get_warn_nested_archive, get_warn_no_printable,
    get_warn_low_compression, get_low_compression_threshold,
    get_logging_enabled, get_metadata_sidecar_enabled,
    get_metadata_thumbnails_enabled, get_store_stl_file_list,
    get_recurse_subdirs, get_ai_embeddings_enabled,
    get_ai_embeddings_during_crawl, get_ai_embeddings_batch_size,
    get_embed_search_batch_size,
)

DATA_DIR_KEY           = "config/data_dir"
UNRAR_TOOL_KEY         = "config/unrar_tool"

# The desktop app keeps the machine-wide values in QSettings.
_core.register_providers(
    data_dir=lambda: QSettings().value(DATA_DIR_KEY, DATA_DIR_DEFAULT),
    unrar_path=lambda: QSettings().value(UNRAR_TOOL_KEY, ""),
)

# Defaults for per-library settings (non-platform-specific)
THUMB_PAGE_SIZE_DEFAULT = 200
AI_TEMPERATURE_DEFAULT  = 0.2

# ── File manager presets ──────────────────────────────────────────────────────
FILE_MANAGER_SYSTEM           = "system"
FILE_MANAGER_DOUBLE_COMMANDER = "double_commander"
FILE_MANAGER_CUSTOM           = "custom"

# Values are marked with QT_TRANSLATE_NOOP so pylupdate6 extracts them; they are
# translated at the point of use (see _build_browser_config_page).
_FILE_MANAGER_DISPLAY: dict[str, str] = {
    FILE_MANAGER_SYSTEM:           QT_TRANSLATE_NOOP("config_dialog", "System Default"),
    FILE_MANAGER_DOUBLE_COMMANDER: QT_TRANSLATE_NOOP("config_dialog", "Double Commander"),
    FILE_MANAGER_CUSTOM:           QT_TRANSLATE_NOOP("config_dialog", "Custom…"),
}

# Default executable paths per preset per platform (used as placeholder / fallback)
_FILE_MANAGER_DEFAULTS: dict[str, dict[str, str]] = {
    FILE_MANAGER_DOUBLE_COMMANDER: {
        "win32":  r"C:\Program Files\Double Commander\doublecmd.exe",
        "linux":  "doublecmd",
        "darwin": "/Applications/Double Commander.app/Contents/MacOS/doublecmd",
    },
}


_PLATFORM = _core.PLATFORM_DEFAULTS

COLOR_SCHEME_DEFAULT = _PLATFORM["color_scheme"]

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


def get_color_scheme() -> str:
    return _load_library_settings().get("color_scheme", COLOR_SCHEME_DEFAULT)


TOOLBAR_ICON_TEXT = "icon_text"
TOOLBAR_ICON_ONLY = "icon_only"


def get_toolbar_style() -> str:
    """Return how main-toolbar buttons are shown: ``"icon_text"`` or ``"icon_only"``."""
    value = _load_library_settings().get("toolbar_style", TOOLBAR_ICON_TEXT)
    return value if value in (TOOLBAR_ICON_TEXT, TOOLBAR_ICON_ONLY) else TOOLBAR_ICON_TEXT


def is_material_theme(scheme_name: str) -> bool:
    """True when *scheme_name* (or the built-in theme a custom scheme is based on)
    is rendered by qt-material."""
    custom = _load_library_settings().get("custom_color_schemes", {})
    if scheme_name in custom:
        scheme_name = custom[scheme_name].get("base_theme", "System Default")
    return THEMES.get(scheme_name, "").startswith("material:")


def get_icon_set() -> str:
    """Return the configured icon set id (see ``app.utils.material_icons.ICON_SETS``)."""
    from app.utils.material_icons import DEFAULT_ICON_SET
    return str(_load_library_settings().get("icon_set", DEFAULT_ICON_SET))


def get_language() -> str:
    """Return the configured UI language code (e.g. ``"en"``, ``"de"``)."""
    from app.utils.i18n import DEFAULT_LANGUAGE
    return str(_load_library_settings().get("language", DEFAULT_LANGUAGE))


def _parse_qss_token(qss_text: str, token: str) -> str | None:
    """Extract a hex color from a QSS comment token like '--pair-a: #rrggbb'."""
    m = re.search(rf"--{re.escape(token)}:\s*(#[0-9a-fA-F]{{3,8}})", qss_text)
    return m.group(1) if m else None


@functools.lru_cache(maxsize=None)
def _theme_token_color(qss_path: str, token: str) -> str | None:
    """Return *token*'s color from a built-in theme file (memoised).

    Theme files ship with the application and do not change at runtime, and
    the UI color getters run inside paint code — so the file is read once.
    """
    path = Path(qss_path)
    if not path.exists():
        return None
    return _parse_qss_token(path.read_text(encoding="utf-8"), token)


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
            color = _theme_token_color(value, token)
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


def get_font_family() -> str:
    """Return the user-configured font family, or empty string for the theme default."""
    return str(_load_library_settings().get("font_family", ""))


def get_font_size() -> int:
    """Return the user-configured font size in points, or 0 for the theme default."""
    return int(_load_library_settings().get("font_size", 0))


def get_highlight_warning_rows() -> bool:
    """Whether to highlight rows with warnings in yellow."""
    return bool(_load_library_settings().get("highlight_warning_rows", True))


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


def get_filter_show_counts() -> bool:
    """Whether to show model counts next to each item in the filter pane."""
    return bool(_load_library_settings().get("filter_show_counts", True))


def get_filter_alpha_enabled() -> bool:
    """Whether to group large filter sections into alphabetical letter bins."""
    return bool(_load_library_settings().get("filter_alpha_enabled", True))


def get_filter_alpha_threshold() -> int:
    """Min number of items in a filter section before alphabetical grouping kicks in."""
    return int(_load_library_settings().get("filter_alpha_threshold", 50))


def get_ai_tagging_enabled() -> bool:
    """Whether the AI Tagging feature is enabled at all.

    When ``False`` the AI Tagging action is hidden from the toolbar, Tools
    menu, and the table's right-click menu.
    """
    return bool(_load_library_settings().get("ai_tagging_enabled", True))


def get_ai_temperature() -> float:
    """Sampling temperature for the AI tagging model.

    Lower values (0.0–0.2) keep tags/metadata literal and grounded in the
    images; higher values make the model more creative but more prone to
    hallucinated, by-association results.
    """
    return float(_load_library_settings().get("ai_temperature", AI_TEMPERATURE_DEFAULT))


def get_file_manager() -> str:
    """Return the configured file manager preset key."""
    return _load_library_settings().get("file_manager", FILE_MANAGER_SYSTEM)


def get_file_manager_path() -> str:
    """Return the configured executable path for non-system file manager presets."""
    return _load_library_settings().get("file_manager_path", "")


def get_thumb_page_size() -> int:
    """Return the max images shown per page in the image panel before pagination kicks in."""
    return int(_load_library_settings().get("thumb_page_size", THUMB_PAGE_SIZE_DEFAULT))


# Hotkey action IDs, display labels, and defaults
HOTKEY_DEFAULTS: dict[str, str] = {
    "edit_model":      "E",
    "mass_edit":       "Ctrl+M",
    "refresh":         "F5",
    "apply_filter":    "Return",
    "delete_from_db":  "Delete",
    "open_folder":     "Ctrl+Return",
    "show_files":      "Ctrl+Shift+F",
    "image_grid":      "Ctrl+G",
    "toggle_images":   "Ctrl+I",
    "open_crawler":    "Ctrl+O",
    "update_folders":  "Ctrl+Shift+R",
    "find_duplicates": "Ctrl+D",
    "archive_browser": "Ctrl+B",
}

# Values are marked with QT_TRANSLATE_NOOP so pylupdate6 extracts them; they are
# translated at the point of use (see _build_hotkeys_page).
HOTKEY_LABELS: dict[str, str] = {
    "edit_model":      QT_TRANSLATE_NOOP("config_dialog", "Edit model"),
    "mass_edit":       QT_TRANSLATE_NOOP("config_dialog", "Mass edit selected"),
    "refresh":         QT_TRANSLATE_NOOP("config_dialog", "Refresh table"),
    "apply_filter":    QT_TRANSLATE_NOOP("config_dialog", "Apply filter"),
    "delete_from_db":  QT_TRANSLATE_NOOP("config_dialog", "Delete from DB"),
    "open_folder":     QT_TRANSLATE_NOOP("config_dialog", "Open containing folder"),
    "show_files":      QT_TRANSLATE_NOOP("config_dialog", "Show archive files"),
    "image_grid":      QT_TRANSLATE_NOOP("config_dialog", "Image grid"),
    "toggle_images":   QT_TRANSLATE_NOOP("config_dialog", "Toggle image pane"),
    "open_crawler":    QT_TRANSLATE_NOOP("config_dialog", "Open Crawler"),
    "update_folders":  QT_TRANSLATE_NOOP("config_dialog", "Update crawled folders"),
    "find_duplicates": QT_TRANSLATE_NOOP("config_dialog", "Find metadata duplicates"),
    "archive_browser": QT_TRANSLATE_NOOP("config_dialog", "Archive image browser"),
}


def get_hotkeys() -> dict[str, str]:
    """Return the configured hotkeys dict (action_id → key sequence string)."""
    stored = _load_library_settings().get("hotkeys", {})
    return {k: stored.get(k, v) for k, v in HOTKEY_DEFAULTS.items()}


# ── Notification settings ─────────────────────────────────────────────────────

def get_notifications_enabled() -> bool:
    return bool(_load_library_settings().get("notifications_enabled", True))


def get_notification_sound() -> str:
    """Return ``"beep"`` (system beep) or ``"custom"`` (play a file)."""
    return str(_load_library_settings().get("notification_sound", "beep"))


def get_notification_sound_file() -> str:
    return str(_load_library_settings().get("notification_sound_file", ""))


def get_notify_crawl() -> bool:
    return bool(_load_library_settings().get("notify_crawl", True))


def get_notify_duplicates() -> bool:
    return bool(_load_library_settings().get("notify_duplicates", True))


def get_notify_maintenance() -> bool:
    return bool(_load_library_settings().get("notify_maintenance", True))


def get_notify_sidecar() -> bool:
    return bool(_load_library_settings().get("notify_sidecar", True))


def get_notify_backup() -> bool:
    return bool(_load_library_settings().get("notify_backup", True))


def open_folder_with_file_manager(folder: str) -> None:
    """Open *folder* in the configured file manager."""
    from PyQt6.QtWidgets import QMessageBox
    manager = get_file_manager()
    if manager == FILE_MANAGER_SYSTEM:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        return

    exe = get_file_manager_path()
    if not exe and manager in _FILE_MANAGER_DEFAULTS:
        plat = sys.platform if sys.platform in ("win32", "darwin") else "linux"
        exe = _FILE_MANAGER_DEFAULTS[manager].get(plat, "")

    if not exe:
        QMessageBox.warning(None, QCoreApplication.translate("config_dialog", "Open Folder"), QCoreApplication.translate("config_dialog", "No file manager executable configured."))
        return

    import subprocess
    try:
        subprocess.Popen([exe, folder])
    except FileNotFoundError:
        QMessageBox.warning(
            None, QCoreApplication.translate("config_dialog", "Open Folder"),
            f"Executable not found:\n{exe}\n\nCheck the path in Settings → Browser.",
        )
    except Exception as exc:
        QMessageBox.warning(None, QCoreApplication.translate("config_dialog", "Open Folder"), f"Failed to open folder:\n{exc}")


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


_CLIP_DOWNLOAD_URL = (
    "https://huggingface.co/Xenova/clip-vit-base-patch32"
    "/resolve/main/onnx/vision_model.onnx"
)


class _ClipDownloadWorker(QThread):
    """Downloads the CLIP ONNX vision model from Hugging Face in a background thread."""

    progress = pyqtSignal(int, int)   # (bytes_done, total_bytes)
    download_finished = pyqtSignal(bool, str)  # (success, message)

    def __init__(self, dest: "Path", parent=None) -> None:
        super().__init__(parent)
        self._dest = dest
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        import urllib.request
        from pathlib import Path

        dest = Path(self._dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".onnx.part")

        try:
            req = urllib.request.Request(
                _CLIP_DOWNLOAD_URL,
                headers={"User-Agent": "stlLibrary/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                done = 0
                chunk = 1 << 16  # 64 KB
                with open(tmp, "wb") as f:
                    while not self._cancelled:
                        data = resp.read(chunk)
                        if not data:
                            break
                        f.write(data)
                        done += len(data)
                        self.progress.emit(done, total)

            if self._cancelled:
                tmp.unlink(missing_ok=True)
                self.download_finished.emit(False, "Download cancelled.")
                return

            tmp.rename(dest)
            self.download_finished.emit(True, f"Model saved to {dest}")
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            self.download_finished.emit(False, f"Download failed: {exc}")


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
        self.setWindowTitle(self.tr("Configuration"))
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
        # 5=Sidecar(child)  6=Debug  7=Browser  8=Hotkeys  9=Language  10=Notifications
        # 11=AI
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

        _item(self._section_tree, self.tr("Database"),      sp.SP_DriveHDIcon,            0)
        _item(self._section_tree, self.tr("Language"),      sp.SP_DialogHelpButton,       9)
        crawler_item = _item(self._section_tree, self.tr("Crawler"), sp.SP_FileDialogDetailedView, 1)
        _item(crawler_item,            self.tr("Warnings"),  sp.SP_MessageBoxWarning,      2)
        fw_item = _item(self._section_tree, self.tr("File writing"), sp.SP_DesktopIcon,   3)
        _item(fw_item,                 self.tr("Sidecar"),   sp.SP_DialogSaveButton,       5)
        _item(self._section_tree, self.tr("Browser"),       sp.SP_FileDialogListView,      7)
        _item(self._section_tree, self.tr("AI"),            sp.SP_FileDialogContentsView, 11)
        _item(self._section_tree, self.tr("Appearance"),    sp.SP_TitleBarNormalButton,    4)
        _item(self._section_tree, self.tr("Hotkeys"),       sp.SP_MediaVolumeMuted,        8)
        _item(self._section_tree, self.tr("Notifications"), sp.SP_MediaVolume,            10)
        _item(self._section_tree, self.tr("Debug"),         sp.SP_MessageBoxCritical,      6)

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

        self._stack.addWidget(self._build_database_page())        # 0
        self._stack.addWidget(self._build_crawler_page())         # 1
        self._stack.addWidget(self._build_warnings_page())        # 2
        self._stack.addWidget(self._build_file_writing_page())    # 3
        self._stack.addWidget(self._build_appearance_page())      # 4
        self._stack.addWidget(self._build_sidecar_page())         # 5
        self._stack.addWidget(self._build_debug_page())           # 6
        self._stack.addWidget(self._build_browser_config_page())  # 7
        self._stack.addWidget(self._build_hotkeys_page())         # 8
        self._stack.addWidget(self._build_language_page())        # 9
        self._stack.addWidget(self._build_notifications_page())   # 10
        self._stack.addWidget(self._build_ai_page())              # 11

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
        group = QGroupBox(self.tr("Database"))
        form = QFormLayout(group)

        path_row = QHBoxLayout()
        self._data_dir_edit = QLineEdit()
        self._data_dir_edit.setPlaceholderText(DATA_DIR_DEFAULT)
        path_row.addWidget(self._data_dir_edit)

        browse_btn = QPushButton(self.tr("Browse…"))
        browse_btn.clicked.connect(self._browse_data_dir)
        path_row.addWidget(browse_btn)

        form.addRow(self.tr("Data directory:"), path_row)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>All database files are stored in this directory. "
                "A restart is required for changes to take effect.</small>")
            ),
        )

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_crawler_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox(self.tr("Crawler"))
        form = QFormLayout(group)

        self._max_thumbs_spin = QSpinBox()
        self._max_thumbs_spin.setRange(0, 50)
        self._max_thumbs_spin.setSuffix(self.tr(" image(s)"))
        form.addRow(self.tr("Max thumbnails per model:"), self._max_thumbs_spin)
        form.addRow(
            "",
            QLabel(self.tr("<small>Maximum number of preview images extracted from each archive.</small>")),
        )

        self._max_thumb_size_spin = QSpinBox()
        self._max_thumb_size_spin.setRange(64, 4096)
        self._max_thumb_size_spin.setSingleStep(64)
        self._max_thumb_size_spin.setSuffix(self.tr(" px"))
        form.addRow(self.tr("Max image size:"), self._max_thumb_size_spin)
        form.addRow(
            "",
            QLabel(self.tr("<small>Longest side in pixels when storing images (width & height capped at this value).</small>")),
        )

        self._max_workers_spin = QSpinBox()
        self._max_workers_spin.setRange(1, 32)
        self._max_workers_spin.setSuffix(self.tr(" thread(s)"))
        form.addRow(self.tr("Parallel workers:"), self._max_workers_spin)
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

        unrar_browse_btn = QPushButton(self.tr("Browse…"))
        unrar_browse_btn.clicked.connect(self._browse_unrar)
        unrar_row.addWidget(unrar_browse_btn)

        form.addRow(self.tr("RAR tool:"), unrar_row)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Path to <b>unrar.exe</b> or <b>rar.exe</b> used to extract .rar archives. "
                "Leave blank to use the one on your PATH.</small>")
            ),
        )

        layout.addWidget(group)

        # Recurse option
        recurse_group = QGroupBox(self.tr("Crawl Scope"))
        recurse_form = QFormLayout(recurse_group)
        self._recurse_subdirs_check = QCheckBox(self.tr("Recurse subdirectories"))
        recurse_form.addRow(self.tr("Recurse subdirs:"), self._recurse_subdirs_check)
        recurse_form.addRow(
            "",
            QLabel(
                self.tr("<small>When enabled, the crawler will search the selected folder and all its subfolders. "
                "When disabled, only archives directly inside the selected folder are processed.</small>")
            ),
        )
        layout.addWidget(recurse_group)

        file_info_group = QGroupBox(self.tr("Full File Info"))
        file_info_form = QFormLayout(file_info_group)

        self._store_stl_list_check = QCheckBox(self.tr("Store STL, OBJ, LYS, and Chitubox file list"))
        file_info_form.addRow("", self._store_stl_list_check)
        file_info_form.addRow(
            "",
            QLabel(
                self.tr("<small>When enabled, the crawler records the name and uncompressed "
                "size of every <b>.stl</b> file found inside each archive. "
                "Stored in the <b>stl_files</b> database table. "
                "Archives already in the database that are missing file information "
                "will be automatically re-crawled to populate the missing entries.</small>")
            ),
        )

        layout.addWidget(file_info_group)
        layout.addStretch()
        return page

    def _build_warnings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox(self.tr("Crawler Warnings"))
        form  = QFormLayout(group)

        self._warn_presupported_check = QCheckBox(self.tr("Archive contains presupported STLs"))
        form.addRow("", self._warn_presupported_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Warn when an archive contains a presupported <b>.stl</b>. A mesh "
                "counts when its filename matches one of the patterns below, or when it sits in "
                "a folder that marks its contents as presupported (<i>Presupported/</i>, "
                "<i>PS/</i>, <i>Supported STL/</i>). Folders and filenames that negate the "
                "marker (<i>Unsupported/</i>, <i>No_Supported/</i>, <i>…_Unsup_…</i>) are never "
                "flagged. Each pattern is a Python regex applied case-insensitively to the "
                "filename of every .stl member.</small>")
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
        pp_add_btn = QPushButton(self.tr("Add pattern"))
        pp_add_btn.clicked.connect(self._add_presupport_pattern)
        pp_rm_btn = QPushButton(self.tr("Remove selected"))
        pp_rm_btn.clicked.connect(self._remove_presupport_pattern)
        pp_btn_row.addWidget(pp_add_btn)
        pp_btn_row.addWidget(pp_rm_btn)
        pp_btn_row.addStretch()
        pp_layout.addLayout(pp_btn_row)

        form.addRow("", self._presupport_patterns_widget)
        self._warn_presupported_check.toggled.connect(self._presupport_patterns_widget.setEnabled)

        self._warn_no_images_check = QCheckBox(self.tr("Archive contains images"))
        form.addRow("", self._warn_no_images_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Warn when an archive contains no image files "
                "(.jpg, .png, .webp, …).</small>")
            ),
        )

        self._warn_nested_archive_check = QCheckBox(self.tr("Nested archives"))
        form.addRow("", self._warn_nested_archive_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Warn when an archive contains another compressed archive "
                "(.zip, .rar, .7z).</small>")
            ),
        )

        self._warn_no_printable_check = QCheckBox(self.tr("Archive without printable files"))
        form.addRow("", self._warn_no_printable_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Warn when an archive contains no printable model files "
                "(.stl, .obj, .lys, .chitubox).</small>")
            ),
        )

        self._warn_low_compression_check = QCheckBox(self.tr("Archive with low compression rate"))
        form.addRow("", self._warn_low_compression_check)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel(self.tr("Threshold (compressed/uncompressed ratio):")))
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
                self.tr("<small>Warn when compressed size ÷ uncompressed size exceeds this value "
                "(e.g. 0.95 = 95 %). Values near 1.0 indicate little or no compression.</small>")
            ),
        )
        self._warn_low_compression_check.toggled.connect(
            self._low_compression_threshold_spin.setEnabled
        )

        form.addRow(QLabel(""))   # QFormLayout has no addSpacing(); a blank row spaces it
        self._highlight_warning_rows_check = QCheckBox(self.tr("Highlight rows with warnings"))
        form.addRow("", self._highlight_warning_rows_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>When enabled, rows containing items with warnings are highlighted in yellow.</small>")
            ),
        )

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_sidecar_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        
        # We now group sidecar inside "File writing" logical section, but it is its own page.
        group = QGroupBox(self.tr("Metadata Sidecar (METADATA.7z)"))
        form  = QFormLayout(group)

        self._metadata_sidecar_check = QCheckBox(self.tr("Enable metadata sidecar"))
        form.addRow("", self._metadata_sidecar_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>When enabled, a <b>METADATA.7z</b> sidecar file is created "
                "in each folder after crawling. This allows the library to be "
                "restored on a new machine without re-crawling every archive. "
                "Disable to prevent any METADATA.7z files from being written.</small>")
            ),
        )

        self._metadata_thumbnails_check = QCheckBox(self.tr("Include thumbnails"))
        form.addRow("", self._metadata_thumbnails_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>When enabled, each folder's <b>METADATA.7z</b> sidecar will "
                "contain thumbnail images alongside the metadata JSON. "
                "Disable to save disk space; thumbnails will be omitted from the "
                "sidecar and not restored when importing on a new machine.</small>")
            ),
        )

        self._metadata_sidecar_check.toggled.connect(self._metadata_thumbnails_check.setEnabled)

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_debug_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox(self.tr("Debug"))
        form = QFormLayout(group)

        self._debug_menu_check = QCheckBox(self.tr("Show Debug menu"))
        form.addRow("", self._debug_menu_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Shows the <b>Debug</b> menu in the menu bar, which gives "
                "access to destructive operations such as deleting the database "
                "and log files.</small>")
            ),
        )

        self._logging_enabled_check = QCheckBox(self.tr("Enable logging"))
        form.addRow("", self._logging_enabled_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Write application events and errors to the log file. "
                "Uncheck to silence all log output. "
                "Uncaught exceptions are always captured regardless of this setting.</small>")
            ),
        )

        self._sql_profiling_check = QCheckBox(self.tr("Log SQL statement timings"))
        form.addRow("", self._sql_profiling_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>Logs the duration of every SQL statement. "
                "Statements taking 100 ms or longer are also written at WARNING level "
                "for easy <tt>grep</tt>. Takes effect immediately without restarting.</small>")
            ),
        )

        layout.addWidget(group)
        layout.addStretch()
        return page

    def _build_hotkeys_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox(self.tr("Keyboard shortcuts"))
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._hotkey_edits: dict[str, QKeySequenceEdit] = {}
        for action_id, label in HOTKEY_LABELS.items():
            label = QCoreApplication.translate("config_dialog", label)
            editor = QKeySequenceEdit()
            editor.setToolTip(self.tr('Press the key combination you want to use for "%s"') % label)
            self._hotkey_edits[action_id] = editor
            form.addRow(label + ":", editor)

        reset_btn = QPushButton(self.tr("Reset to defaults"))
        reset_btn.setFlat(True)
        reset_btn.clicked.connect(self._reset_hotkeys)
        form.addRow("", reset_btn)

        layout.addWidget(group)
        layout.addWidget(
            QLabel(
                self.tr("<small>Changes take effect after clicking OK. "
                "Restart the application if shortcuts do not update immediately.</small>")
            )
        )
        layout.addStretch()
        return page

    def _reset_hotkeys(self) -> None:
        for action_id, default in HOTKEY_DEFAULTS.items():
            editor = self._hotkey_edits.get(action_id)
            if editor:
                from PyQt6.QtGui import QKeySequence
                editor.setKeySequence(QKeySequence(default))

    def _build_browser_config_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox(self.tr("Filter pane"))
        form = QFormLayout(group)

        self._filter_show_counts_check = QCheckBox(self.tr("Show model counts in filter pane"))
        form.addRow(self.tr("Counts:"), self._filter_show_counts_check)

        self._filter_alpha_enabled_check = QCheckBox(self.tr("Group large sections into letter bins (A, B, C, …)"))
        self._filter_alpha_enabled_check.toggled.connect(self._update_alpha_threshold_enabled)
        form.addRow(self.tr("Letter bins:"), self._filter_alpha_enabled_check)

        self._filter_alpha_threshold_spin = QSpinBox()
        self._filter_alpha_threshold_spin.setRange(5, 10000)
        self._filter_alpha_threshold_spin.setSingleStep(5)
        self._filter_alpha_threshold_spin.setSuffix(self.tr(" items"))
        self._filter_alpha_threshold_spin.setToolTip(
            self.tr("Split a filter section into A/B/C… groups when it has more than this many entries.")
        )
        form.addRow(self.tr("Split above:"), self._filter_alpha_threshold_spin)

        layout.addWidget(group)

        fm_group = QGroupBox(self.tr("File Manager"))
        fm_form = QFormLayout(fm_group)

        self._file_manager_combo = QComboBox()
        for key, display in _FILE_MANAGER_DISPLAY.items():
            self._file_manager_combo.addItem(QCoreApplication.translate("config_dialog", display), key)
        self._file_manager_combo.currentIndexChanged.connect(self._update_file_manager_path_visibility)
        fm_form.addRow(self.tr("Open folder with:"), self._file_manager_combo)

        fm_path_row = QHBoxLayout()
        self._file_manager_path_edit = QLineEdit()
        fm_path_row.addWidget(self._file_manager_path_edit)
        fm_path_browse_btn = QPushButton(self.tr("Browse…"))
        fm_path_browse_btn.clicked.connect(self._browse_file_manager)
        fm_path_row.addWidget(fm_path_browse_btn)
        self._file_manager_path_widget = QWidget()
        self._file_manager_path_widget.setLayout(fm_path_row)
        fm_form.addRow(self.tr("Executable:"), self._file_manager_path_widget)

        layout.addWidget(fm_group)

        img_group = QGroupBox(self.tr("Image panel"))
        img_form = QFormLayout(img_group)

        self._thumb_page_size_spin = QSpinBox()
        self._thumb_page_size_spin.setRange(10, 10000)
        self._thumb_page_size_spin.setSingleStep(50)
        self._thumb_page_size_spin.setSuffix(self.tr(" images"))
        self._thumb_page_size_spin.setToolTip(
            self.tr("When a model has more images than this, the image panel paginates instead of showing them all at once.")
        )
        img_form.addRow(self.tr("Paginate above:"), self._thumb_page_size_spin)

        layout.addWidget(img_group)
        layout.addStretch()
        return page

    def _build_ai_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # ── Master toggle ────────────────────────────────────────────────
        self._ai_tagging_check = QCheckBox(self.tr("Enable AI tagging"))
        self._ai_tagging_check.setToolTip(self.tr(
            "Enable the AI Tagging feature. When disabled, the AI Tagging action "
            "is hidden from the toolbar, the Tools menu, and the table's "
            "right-click menu."
        ))
        self._ai_tagging_check.toggled.connect(self._update_ai_tagging_enabled)
        layout.addWidget(self._ai_tagging_check)
        layout.addWidget(
            QLabel(self.tr("<small>Generate tags and metadata from a model's images using a "
                   "local vision-language model (Qwen2.5-VL via llama-server). "
                   "Disable to hide the feature entirely.</small>"))
        )

        # ── AI Tagging settings ──────────────────────────────────────────
        self._ai_tag_group = QGroupBox(self.tr("AI Tagging"))
        ai_tag_form = QFormLayout(self._ai_tag_group)

        self._ai_temperature_spin = QDoubleSpinBox()
        self._ai_temperature_spin.setRange(0.0, 2.0)
        self._ai_temperature_spin.setSingleStep(0.05)
        self._ai_temperature_spin.setDecimals(2)
        self._ai_temperature_spin.setToolTip(self.tr(
            "Sampling temperature passed to the AI tagging model. "
            "Lower values (0.0–0.2) make tags and metadata more literal and "
            "grounded in the images; higher values make the model more creative "
            "but more prone to hallucinated, by-association results."
        ))
        ai_tag_form.addRow(self.tr("Model temperature:"), self._ai_temperature_spin)
        ai_tag_form.addRow(
            "",
            QLabel(self.tr("<small>Used when generating tags and metadata in the AI Tagging dialog. "
                   "Default: 0.2.</small>")),
        )

        layout.addWidget(self._ai_tag_group)

        # ── Image similarity (embeddings) ────────────────────────────────
        sim_group = QGroupBox(self.tr("Image similarity (embeddings)"))
        sim_form = QFormLayout(sim_group)

        self._ai_embeddings_check = QCheckBox(self.tr("Enable AI image embeddings (CLIP)"))
        self._ai_embeddings_check.setToolTip(self.tr(
            "Enable the AI embedding (CLIP) method in image similarity search. "
            "Requires onnxruntime, numpy, and a CLIP ONNX model file."
        ))
        self._ai_embeddings_check.toggled.connect(self._update_ai_embeddings_enabled)
        sim_form.addRow("", self._ai_embeddings_check)

        self._ai_embeddings_crawl_check = QCheckBox(self.tr("Compute embeddings during crawl"))
        self._ai_embeddings_crawl_check.setToolTip(self.tr(
            "Automatically compute AI image embeddings for new thumbnails during each crawl. "
            "Slows crawl speed when enabled."
        ))
        sim_form.addRow("", self._ai_embeddings_crawl_check)

        self._ai_batch_size_spin = QSpinBox()
        self._ai_batch_size_spin.setRange(1, 512)
        self._ai_batch_size_spin.setSingleStep(8)
        self._ai_batch_size_spin.setToolTip(self.tr(
            "Number of images sent to the CLIP model in a single inference call. "
            "Higher values improve GPU throughput but use more VRAM. "
            "Reduce if you get out-of-memory errors."
        ))
        sim_form.addRow(self.tr("Batch size:"), self._ai_batch_size_spin)

        self._embed_search_batch_spin = QSpinBox()
        self._embed_search_batch_spin.setRange(100, 10000)
        self._embed_search_batch_spin.setSingleStep(100)
        self._embed_search_batch_spin.setSuffix(self.tr(" rows"))
        self._embed_search_batch_spin.setToolTip(self.tr(
            "Number of image embeddings scored per batch during Find by Image (CLIP). "
            "Lower values use less RAM; higher values improve BLAS throughput. "
            "Default: 1000."
        ))
        sim_form.addRow(self.tr("Search batch:"), self._embed_search_batch_spin)

        from app.utils import image_embedding as _emb
        self._clip_model_present = _emb.is_available()
        self._clip_dl_worker: _ClipDownloadWorker | None = None

        dl_row = QHBoxLayout()
        self._clip_dl_btn = QPushButton(self.tr("Download CLIP model (~90 MB)"))
        self._clip_dl_btn.setToolTip(self.tr(
            "Download the CLIP ViT-B/32 image encoder (Xenova, ONNX) from Hugging Face "
            "and save it to the models folder inside your data directory."
        ))
        self._clip_dl_btn.clicked.connect(self._start_clip_download)
        dl_row.addWidget(self._clip_dl_btn)
        self._clip_dl_cancel_btn = QPushButton(self.tr("Cancel"))
        self._clip_dl_cancel_btn.setVisible(False)
        self._clip_dl_cancel_btn.clicked.connect(self._cancel_clip_download)
        dl_row.addWidget(self._clip_dl_cancel_btn)
        sim_form.addRow("", dl_row)

        self._clip_dl_status = QLabel()
        self._clip_dl_status.setWordWrap(True)
        sim_form.addRow("", self._clip_dl_status)

        self._refresh_clip_dl_button()

        layout.addWidget(sim_group)
        layout.addStretch()
        return page

    def _update_ai_tagging_enabled(self, enabled: bool) -> None:
        self._ai_tag_group.setEnabled(enabled)

    def _refresh_clip_dl_button(self) -> None:
        from app.utils import image_embedding as _emb
        model_found = _emb.model_path() is not None
        deps_ok     = _emb.is_available()   # deps + model
        enabled     = self._ai_embeddings_check.isChecked()
        downloading = self._clip_dl_worker is not None and self._clip_dl_worker.isRunning()

        if not enabled:
            self._clip_dl_btn.setVisible(False)
            self._clip_dl_cancel_btn.setVisible(False)
            self._clip_dl_status.clear()
        elif model_found and deps_ok:
            self._clip_dl_btn.setVisible(False)
            self._clip_dl_cancel_btn.setVisible(False)
            self._clip_dl_status.setText(
                f'<span style="color:green;">✓ {self.tr("CLIP model is installed.")}</span>'
            )
        elif model_found and not deps_ok:
            # Model file is present but runtime deps are missing.
            self._clip_dl_btn.setVisible(False)
            self._clip_dl_cancel_btn.setVisible(False)
            self._clip_dl_status.setText(
                f'<span style="color:orange;">⚠ {self.tr("Model found but onnxruntime / numpy are not installed.")}<br>'
                f'Run: <code>pip install onnxruntime numpy</code></span>'
            )
        else:
            self._clip_dl_btn.setVisible(not downloading)
            self._clip_dl_cancel_btn.setVisible(downloading)
            if not downloading:
                self._clip_dl_status.setText(
                    self.tr("CLIP model not found. Download it to enable AI embeddings.")
                )

    def _start_clip_download(self) -> None:
        from app.utils import image_embedding as _emb
        dest = _emb.default_model_dest()
        if dest is None:
            self._clip_dl_status.setText(
                self.tr("Cannot determine data directory. Set it in Configuration → Database first.")
            )
            return
        self._clip_dl_worker = _ClipDownloadWorker(dest, parent=self)
        self._clip_dl_worker.progress.connect(self._on_clip_dl_progress)
        self._clip_dl_worker.download_finished.connect(self._on_clip_dl_finished)
        self._clip_dl_worker.start()
        self._refresh_clip_dl_button()

    def _cancel_clip_download(self) -> None:
        if self._clip_dl_worker:
            self._clip_dl_worker.cancel()

    def _on_clip_dl_progress(self, done: int, total: int) -> None:
        if total > 0:
            pct = int(done * 100 / total)
            mb_done = done / 1_048_576
            mb_total = total / 1_048_576
            self._clip_dl_status.setText(
                f"{pct}%  ({mb_done:.1f} / {mb_total:.1f} MB)"
            )
        else:
            self._clip_dl_status.setText(f"{done / 1_048_576:.1f} MB downloaded…")

    def _on_clip_dl_finished(self, success: bool, message: str) -> None:
        from app.utils import image_embedding as _emb
        _emb.reset_session()
        self._clip_dl_worker = None
        color = "green" if success else "red"
        self._clip_dl_status.setText(f'<span style="color:{color};">{message}</span>')
        self._refresh_clip_dl_button()

    def _update_ai_embeddings_enabled(self, enabled: bool) -> None:
        self._ai_embeddings_crawl_check.setEnabled(enabled)
        self._ai_batch_size_spin.setEnabled(enabled)
        self._refresh_clip_dl_button()

    def _update_alpha_threshold_enabled(self, enabled: bool) -> None:
        self._filter_alpha_threshold_spin.setEnabled(enabled)

    def _update_file_manager_path_visibility(self) -> None:
        key = self._file_manager_combo.currentData()
        show = key != FILE_MANAGER_SYSTEM
        self._file_manager_path_widget.setVisible(show)
        if show:
            plat = sys.platform if sys.platform in ("win32", "darwin") else "linux"
            default = _FILE_MANAGER_DEFAULTS.get(key, {}).get(plat, "")
            self._file_manager_path_edit.setPlaceholderText(default or "Path to executable…")

    def _browse_file_manager(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select file manager executable"),
            self._file_manager_path_edit.text() or "",
            "Executables (*.exe);;All files (*)" if sys.platform == "win32" else "All files (*)",
        )
        if path:
            self._file_manager_path_edit.setText(path)

    def _build_file_writing_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox(self.tr("File writing"))
        form = QFormLayout(group)

        self._file_sorting_check = QCheckBox(self.tr("Enable file sorting"))
        form.addRow(self.tr("File sorting:"), self._file_sorting_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>When enabled, a <b>Sort files into directory…</b> option "
                "appears in the table right-click menu, allowing selected archive "
                "files to be moved and renamed using a path pattern.</small>")
            ),
        )

        self._allow_file_delete_check = QCheckBox(self.tr("Allow file delete"))
        self._allow_file_delete_check.setStyleSheet("color: red; font-weight: bold;")
        self._allow_file_delete_check.clicked.connect(self._on_allow_delete_clicked)
        form.addRow(self.tr("Delete:"), self._allow_file_delete_check)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>When enabled, a <b>Delete Archive File from Disk…</b> option "
                "appears in the table right-click menu. This deletes the physical file.</small>")
            ),
        )

        self._use_trash_folder_check = QCheckBox(self.tr("Use trash folder"))
        form.addRow(self.tr("Trash:"), self._use_trash_folder_check)

        trash_row = QHBoxLayout()
        self._trash_folder_edit = QLineEdit()
        self._trash_folder_edit.setPlaceholderText(self.tr("/path/to/trash"))
        trash_row.addWidget(self._trash_folder_edit)

        trash_browse_btn = QPushButton(self.tr("Browse…"))
        trash_browse_btn.clicked.connect(self._browse_trash_folder)
        trash_row.addWidget(trash_browse_btn)

        form.addRow("", trash_row)
        form.addRow(
            "",
            QLabel(
                self.tr("<small>If enabled, files are moved here instead of being permanently "
                "deleted. A sidecar file with recovery info is created alongside.</small>")
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
        dlg.setWindowTitle(self.tr("DANGER: File Deletion"))
        
        l = QVBoxLayout(dlg)
        icon_lbl = QLabel()
        style = QApplication.style()
        icon_lbl.setPixmap(style.standardPixmap(style.StandardPixmap.SP_MessageBoxCritical))
        
        text_lbl = QLabel(
            self.tr("<span style='color: #d32f2f; font-weight: bold;'>Enabling this option allows the application to physically DELETE your archive files from the disk.</span><br><br>"
            "This could lead to permanent loss of data. Use with extreme caution!")
        )
        text_lbl.setWordWrap(True)
        
        h = QHBoxLayout()
        h.addWidget(icon_lbl)
        h.addWidget(text_lbl, stretch=1)
        l.addLayout(h)
        
        confirm_cb = QCheckBox(self.tr("I understand this and want to continue"))
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
        path = QFileDialog.getExistingDirectory(self, self.tr("Choose trash directory"), self._trash_folder_edit.text() or "")
        if path:
            self._trash_folder_edit.setText(path)

    def _build_language_page(self) -> QWidget:
        from app.utils import i18n
        page = QWidget()
        layout = QVBoxLayout(page)
        group = QGroupBox(self.tr("Language"))
        form = QFormLayout(group)

        self._language_combo = QComboBox()
        for code, display in i18n.available_languages():
            self._language_combo.addItem(display, code)
        form.addRow(self.tr("Language:"), self._language_combo)
        form.addRow(
            "",
            QLabel(self.tr("<small>Choose the interface language. Restart the application "
                           "for the change to fully take effect.</small>")),
        )

        layout.addWidget(group)
        layout.addStretch()
        return page

    # Sample of icon roles shown in the Appearance → Icons preview strip.
    _ICON_PREVIEW_ROLES = (
        "open_crawler", "update_folders", "toggle_images", "find_dupes",
        "crawler_start", "crawler_stop", "icon_author", "icon_tag",
        "icon_date", "icon_path", "icon_scale", "icon_warnings",
    )

    def _preview_icon_set(self, *_args) -> None:
        """Show the selected icon set in the preview strip (no restart needed)."""
        from app.utils.material_icons import lookup_icon
        set_id = self._icon_set_combo.currentData() or "material"
        is_dark = "dark" in self._theme_combo.currentText().lower()
        for role, lbl in zip(self._ICON_PREVIEW_ROLES, self._icon_preview_labels):
            lbl.setPixmap(lookup_icon(role, is_dark_mode=is_dark, icon_set=set_id).pixmap(24, 24))
            lbl.setToolTip(role)

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
        theme_group = QGroupBox(self.tr("Color Scheme"))
        form = QFormLayout(theme_group)
        self._theme_combo = QComboBox()
        for name in THEMES:
            self._theme_combo.addItem(name)
        self._theme_combo.currentTextChanged.connect(self._preview_theme)
        form.addRow(self.tr("Scheme:"), self._theme_combo)
        form.addRow(
            "",
            QLabel(self.tr("<small>Preview is applied immediately. Cancel reverts to the previous scheme.</small>")),
        )
        layout.addWidget(theme_group)

        # ---- Icon Set ----------------------------------------------------
        from app.utils.material_icons import available_icon_sets
        icon_group = QGroupBox(self.tr("Icons"))
        icon_form = QFormLayout(icon_group)
        self._icon_set_combo = QComboBox()
        for set_id, label in available_icon_sets().items():
            self._icon_set_combo.addItem(label, set_id)
        self._icon_set_combo.currentIndexChanged.connect(self._preview_icon_set)
        icon_form.addRow(self.tr("Icon set:"), self._icon_set_combo)
        self._toolbar_style_combo = QComboBox()
        self._toolbar_style_combo.addItem(self.tr("Icon and text"), TOOLBAR_ICON_TEXT)
        self._toolbar_style_combo.addItem(self.tr("Icon only"), TOOLBAR_ICON_ONLY)
        self._toolbar_style_combo.setToolTip(
            self.tr("Main toolbar buttons. With icons only, hover a button to see its name.")
        )
        icon_form.addRow(self.tr("Toolbar buttons:"), self._toolbar_style_combo)
        self._icon_preview_row = QHBoxLayout()
        self._icon_preview_labels: list[QLabel] = []
        for _ in self._ICON_PREVIEW_ROLES:
            lbl = QLabel()
            lbl.setFixedSize(28, 28)
            self._icon_preview_labels.append(lbl)
            self._icon_preview_row.addWidget(lbl)
        self._icon_preview_row.addStretch()
        icon_form.addRow(self.tr("Preview:"), self._icon_preview_row)
        icon_form.addRow(
            "",
            QLabel(self.tr("<small>The icon set (toolbar, crawler and filter-pane icons) "
                           "takes effect after restarting the application; "
                           "the toolbar button style applies immediately.</small>")),
        )
        layout.addWidget(icon_group)
        # Monochrome Material icons are drawn black or white per theme.
        self._theme_combo.currentTextChanged.connect(self._preview_icon_set)

        # ---- Duplicate Highlight Colors ----------------------------------
        pair_group = QGroupBox(self.tr("Duplicate Highlight Colors"))
        pair_form = QFormLayout(pair_group)
        self._add_color_row(pair_form, "pair_a", "Pair A:")
        self._add_color_row(pair_form, "pair_b", "Pair B:")
        pair_form.addRow(
            "",
            QLabel(self.tr("<small>Row highlight colors used in the duplicate finder.</small>")),
        )

        # ---- Status Indicator Colors -------------------------------------
        status_group = QGroupBox(self.tr("Status Indicators"))
        status_form = QFormLayout(status_group)
        self._add_color_row(status_form, "status_neutral", "Neutral:")
        self._add_color_row(status_form, "status_ok",      "OK:")
        self._add_color_row(status_form, "status_warn",    "Warning:")
        status_form.addRow(
            "",
            QLabel(self.tr("<small>Circle indicators drawn on group items in the browser tree.</small>")),
        )

        # ---- Warning Row Colors ------------------------------------------
        warn_group = QGroupBox(self.tr("Warning Rows"))
        warn_form = QFormLayout(warn_group)
        self._add_color_row(warn_form, "warn_row_bg", "Background:")
        self._add_color_row(warn_form, "warn_row_fg", "Text:")
        warn_form.addRow(
            "",
            QLabel(self.tr("<small>Row colors for archive entries flagged with a warning in the browser.</small>")),
        )

        # ---- Maintenance View Colors -------------------------------------
        maint_group = QGroupBox(self.tr("Maintenance View"))
        maint_form = QFormLayout(maint_group)
        self._add_color_row(maint_form, "missing_file", "Missing file:")
        self._add_color_row(maint_form, "found_file",   "Found file:")
        maint_form.addRow(
            "",
            QLabel(self.tr("<small>Row colors in the database maintenance / verification table.</small>")),
        )

        # ---- Highlight Colors --------------------------------------------
        hl_group = QGroupBox(self.tr("Highlights"))
        hl_form = QFormLayout(hl_group)
        self._add_color_row(hl_form, "match_highlight",   "File match:")
        self._add_color_row(hl_form, "selection_border",  "Selection border:")
        self._add_color_row(hl_form, "selection_bg",      "Selection background:")
        hl_form.addRow(
            "",
            QLabel(
                self.tr("<small>File match: translucent tint on matched rows in the file-comparison table. "
                "Selection: thumbnail selection in the image grid.</small>")
            ),
        )

        # ---- Window Colors ----------------------------------------------
        wc_group = QGroupBox(self.tr("Window Colors"))
        wc_form = QFormLayout(wc_group)
        self._add_color_row(wc_form, "window_bg",   "Background:")
        self._add_color_row(wc_form, "window_text", "Text:")
        wc_form.addRow(
            "",
            QLabel(self.tr("<small>Override the background and text color for all windows. "
                   "Leave blank to use the active theme's colors.</small>")),
        )

        # ---- Custom colors: collapsible section, 3-column grid -----------
        from app.ui.crawler_tab import _CollapsibleSection
        colors_section = _CollapsibleSection(self.tr("Custom Colors"))
        color_grid = QGridLayout()
        color_grid.setContentsMargins(0, 0, 0, 0)
        color_groups = (pair_group, status_group, warn_group,
                        maint_group, hl_group, wc_group)
        for grp in color_groups:
            # Let the long description labels wrap so each group's size hint
            # doesn't force the page wider than the window; with equal column
            # stretch the three columns then scale with the window width.
            for lbl in grp.findChildren(QLabel):
                lbl.setWordWrap(True)
            grp.setMinimumWidth(0)
        for idx, grp in enumerate(color_groups):
            color_grid.addWidget(grp, idx // 3, idx % 3,
                                 Qt.AlignmentFlag.AlignTop)
        for col in range(3):
            color_grid.setColumnStretch(col, 1)
        colors_section.add_layout(color_grid)

        # Reset / save actions apply to the custom colors above
        action_row = QHBoxLayout()
        reset_btn = QPushButton(self.tr("Reset all to theme defaults"))
        reset_btn.clicked.connect(self._reset_all_colors)
        save_scheme_btn = QPushButton(self.tr("Save as color scheme…"))
        save_scheme_btn.clicked.connect(self._save_as_color_scheme)
        action_row.addWidget(reset_btn)
        action_row.addWidget(save_scheme_btn)
        action_row.addStretch()
        colors_section.add_layout(action_row)

        layout.addWidget(colors_section)

        # ---- Typography -------------------------------------------------
        font_group = QGroupBox(self.tr("Typography"))
        font_form = QFormLayout(font_group)

        self._font_family_combo = QFontComboBox()
        self._font_family_combo.setEditable(True)
        self._font_family_combo.setCurrentFont(QApplication.font())
        font_form.addRow(self.tr("Font family:"), self._font_family_combo)

        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(6, 48)
        self._font_size_spin.setValue(QApplication.font().pointSize())
        self._font_size_spin.setSuffix(self.tr(" pt"))
        self._font_size_spin.setSpecialValueText(self.tr("(theme default)"))
        self._font_size_spin.setMinimum(0)
        font_form.addRow(self.tr("Font size:"), self._font_size_spin)

        font_reset_btn = QPushButton(self.tr("Reset to theme font"))
        font_reset_btn.clicked.connect(self._reset_font)
        font_form.addRow("", font_reset_btn)

        font_form.addRow(
            "",
            QLabel(self.tr("<small>Changes take effect after clicking OK. "
                   "Set size to 0 to use the theme default.</small>")),
        )
        layout.addWidget(font_group)

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
            self.tr("Choose data directory"),
            self._data_dir_edit.text() or DATA_DIR_DEFAULT,
        )
        if path:
            self._data_dir_edit.setText(path)

    def _browse_unrar(self) -> None:
        """Open a file picker to locate the unrar/rar executable."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select unrar or rar executable"),
            self._unrar_edit.text() or "",
            self.tr("Executables (*.exe);;All files (*)"),
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
        name, ok = QInputDialog.getText(self, self.tr("Save Color Scheme"), self.tr("Scheme name:"))
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in THEMES:
            QMessageBox.warning(
                self, self.tr("Save Color Scheme"),
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
    def _build_notifications_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        # ── Sound settings ────────────────────────────────────────────────────
        sound_group = QGroupBox(self.tr("Sound"))
        sound_form = QFormLayout(sound_group)

        self._notif_enabled_check = QCheckBox(
            self.tr("Enable completion notifications")
        )
        sound_form.addRow("", self._notif_enabled_check)

        self._notif_sound_combo = QComboBox()
        self._notif_sound_combo.addItem(self.tr("System beep"), "beep")
        self._notif_sound_combo.addItem(self.tr("Custom sound file…"), "custom")
        self._notif_sound_combo.currentIndexChanged.connect(
            self._update_notif_file_row
        )
        sound_form.addRow(self.tr("Sound:"), self._notif_sound_combo)

        file_row = QHBoxLayout()
        self._notif_file_edit = QLineEdit()
        self._notif_file_edit.setPlaceholderText(
            self.tr("Path to a .wav or .mp3 file")
        )
        file_row.addWidget(self._notif_file_edit)

        browse_sound_btn = QPushButton(self.tr("Browse…"))
        browse_sound_btn.clicked.connect(self._browse_notification_sound)
        file_row.addWidget(browse_sound_btn)

        test_btn = QPushButton(self.tr("Test"))
        test_btn.clicked.connect(self._test_notification_sound)
        file_row.addWidget(test_btn)

        self._notif_file_row_label = QLabel(self.tr("File:"))
        self._notif_file_row_widget = QWidget()
        self._notif_file_row_widget.setLayout(file_row)
        sound_form.addRow(self._notif_file_row_label, self._notif_file_row_widget)

        layout.addWidget(sound_group)

        # ── Per-operation toggles ─────────────────────────────────────────────
        ops_group = QGroupBox(self.tr("Notify on completion of:"))
        ops_layout = QVBoxLayout(ops_group)

        self._notify_crawl_check      = QCheckBox(self.tr("Crawl"))
        self._notify_dupes_check      = QCheckBox(self.tr("Duplicate search"))
        self._notify_maint_check      = QCheckBox(self.tr("Database maintenance"))
        self._notify_sidecar_check    = QCheckBox(self.tr("Sidecar generation"))
        self._notify_backup_check     = QCheckBox(self.tr("Backup / Restore"))

        for chk in (
            self._notify_crawl_check,
            self._notify_dupes_check,
            self._notify_maint_check,
            self._notify_sidecar_check,
            self._notify_backup_check,
        ):
            ops_layout.addWidget(chk)

        layout.addWidget(ops_group)
        layout.addStretch()
        self._update_notif_file_row()
        return page

    def _update_notif_file_row(self) -> None:
        is_custom = self._notif_sound_combo.currentData() == "custom"
        self._notif_file_row_label.setVisible(is_custom)
        self._notif_file_row_widget.setVisible(is_custom)

    def _browse_notification_sound(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select Sound File"),
            str(Path(self._notif_file_edit.text()).parent)
            if self._notif_file_edit.text()
            else str(Path.home()),
            self.tr("Audio files (*.wav *.mp3 *.ogg *.oga *.flac);;All files (*)"),
        )
        if path:
            self._notif_file_edit.setText(path)

    def _test_notification_sound(self) -> None:
        from app.utils.sound import play_test_sound
        play_test_sound(
            self._notif_sound_combo.currentData() or "beep",
            self._notif_file_edit.text().strip(),
        )

    def _load(self) -> None:
        """Populate all form fields from current QSettings / library settings."""
        self._data_dir_edit.setText(get_data_dir())
        lang_idx = self._language_combo.findData(get_language())
        self._language_combo.setCurrentIndex(lang_idx if lang_idx >= 0 else 0)
        self._max_thumbs_spin.setValue(get_max_thumbnails())
        self._max_thumb_size_spin.setValue(get_max_thumb_size())

        self._max_workers_spin.setValue(get_max_workers())
        self._filter_show_counts_check.setChecked(get_filter_show_counts())
        alpha_enabled = get_filter_alpha_enabled()
        self._filter_alpha_enabled_check.setChecked(alpha_enabled)
        self._filter_alpha_threshold_spin.setValue(get_filter_alpha_threshold())
        self._filter_alpha_threshold_spin.setEnabled(alpha_enabled)
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
        self._warn_no_printable_check.setChecked(get_warn_no_printable())
        warn_low_compression = get_warn_low_compression()
        self._warn_low_compression_check.setChecked(warn_low_compression)
        self._low_compression_threshold_spin.setValue(get_low_compression_threshold())
        self._low_compression_threshold_spin.setEnabled(warn_low_compression)
        self._highlight_warning_rows_check.setChecked(get_highlight_warning_rows())

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

        icon_idx = self._icon_set_combo.findData(get_icon_set())
        self._icon_set_combo.setCurrentIndex(icon_idx if icon_idx >= 0 else 0)
        self._preview_icon_set()
        tb_idx = self._toolbar_style_combo.findData(get_toolbar_style())
        self._toolbar_style_combo.setCurrentIndex(tb_idx if tb_idx >= 0 else 0)

        # Load font settings
        family = settings.get("font_family", "")
        if family:
            self._font_family_combo.setCurrentFont(QFont(family))
        else:
            self._font_family_combo.setCurrentFont(QApplication.font())
        size = int(settings.get("font_size", 0))
        self._font_size_spin.setValue(size)

        manager = get_file_manager()
        idx = self._file_manager_combo.findData(manager)
        self._file_manager_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._file_manager_path_edit.setText(get_file_manager_path())
        self._update_file_manager_path_visibility()
        self._thumb_page_size_spin.setValue(get_thumb_page_size())
        ai_tagging_enabled = get_ai_tagging_enabled()
        self._ai_tagging_check.setChecked(ai_tagging_enabled)
        self._ai_tag_group.setEnabled(ai_tagging_enabled)
        ai_embed_enabled = get_ai_embeddings_enabled()
        self._ai_embeddings_check.setChecked(ai_embed_enabled)
        self._ai_embeddings_crawl_check.setChecked(get_ai_embeddings_during_crawl())
        self._ai_embeddings_crawl_check.setEnabled(ai_embed_enabled)
        self._ai_batch_size_spin.setValue(get_ai_embeddings_batch_size())
        self._ai_batch_size_spin.setEnabled(ai_embed_enabled)
        self._ai_temperature_spin.setValue(get_ai_temperature())
        self._embed_search_batch_spin.setValue(get_embed_search_batch_size())
        self._refresh_clip_dl_button()

        self._notif_enabled_check.setChecked(get_notifications_enabled())
        notif_sound_idx = self._notif_sound_combo.findData(get_notification_sound())
        self._notif_sound_combo.setCurrentIndex(notif_sound_idx if notif_sound_idx >= 0 else 0)
        self._notif_file_edit.setText(get_notification_sound_file())
        self._update_notif_file_row()
        self._notify_crawl_check.setChecked(get_notify_crawl())
        self._notify_dupes_check.setChecked(get_notify_duplicates())
        self._notify_maint_check.setChecked(get_notify_maintenance())
        self._notify_sidecar_check.setChecked(get_notify_sidecar())
        self._notify_backup_check.setChecked(get_notify_backup())

        self._debug_menu_check.setChecked(get_debug_menu_enabled())
        self._logging_enabled_check.setChecked(get_logging_enabled())
        from PyQt6.QtCore import QSettings as _QSettings
        self._sql_profiling_check.setChecked(
            _QSettings().value("debug/sql_profiling", False, type=bool)
        )

        # Load hotkeys
        from PyQt6.QtGui import QKeySequence
        stored_hotkeys = get_hotkeys()
        for action_id, editor in self._hotkey_edits.items():
            editor.setKeySequence(QKeySequence(stored_hotkeys.get(action_id, HOTKEY_DEFAULTS.get(action_id, ""))))

    def _save(self) -> None:
        """Persist all form values to QSettings and the library settings JSON."""
        data_dir = self._data_dir_edit.text().strip() or DATA_DIR_DEFAULT
        old_dir = get_data_dir()
        old_language = get_language()
        new_language = self._language_combo.currentData() or "en"
        old_icon_set = get_icon_set()
        new_icon_set = self._icon_set_combo.currentData() or "material"

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
                "language":          new_language,
                "color_scheme":      selected_scheme,
                "icon_set":          new_icon_set,
                "toolbar_style":     self._toolbar_style_combo.currentData() or TOOLBAR_ICON_TEXT,
                "ui_color_overrides": self._ui_color_overrides,
                "custom_color_schemes": custom_color_schemes,
                "filter_show_counts": self._filter_show_counts_check.isChecked(),
                "filter_alpha_enabled": self._filter_alpha_enabled_check.isChecked(),
                "filter_alpha_threshold": self._filter_alpha_threshold_spin.value(),
                "file_sorting_enabled": self._file_sorting_check.isChecked(),
                "warn_presupported_stl": self._warn_presupported_check.isChecked(),
                "presupport_patterns": [
                    self._presupport_patterns_list.item(i).text().strip()
                    for i in range(self._presupport_patterns_list.count())
                    if self._presupport_patterns_list.item(i).text().strip()
                ],
                "warn_no_images": self._warn_no_images_check.isChecked(),
                "warn_nested_archive": self._warn_nested_archive_check.isChecked(),
                "warn_no_printable": self._warn_no_printable_check.isChecked(),
                "warn_low_compression": self._warn_low_compression_check.isChecked(),
                "low_compression_threshold": self._low_compression_threshold_spin.value(),
                "highlight_warning_rows": self._highlight_warning_rows_check.isChecked(),
                "metadata_sidecar_enabled": self._metadata_sidecar_check.isChecked(),
                "metadata_thumbnails": self._metadata_thumbnails_check.isChecked(),
                "store_stl_file_list": self._store_stl_list_check.isChecked(),
                "recurse_subdirs": self._recurse_subdirs_check.isChecked(),
                "debug_menu_enabled":       self._debug_menu_check.isChecked(),
                "logging_enabled":          self._logging_enabled_check.isChecked(),
                "embed_search_batch_size":  self._embed_search_batch_spin.value(),
                "allow_file_delete": self._allow_file_delete_check.isChecked(),
                "use_trash_folder": self._use_trash_folder_check.isChecked(),
                "trash_folder_path": self._trash_folder_edit.text().strip(),
                "font_family":        font_family,
                "font_size":          font_size,
                "file_manager":       self._file_manager_combo.currentData(),
                "file_manager_path":  self._file_manager_path_edit.text().strip(),
                "thumb_page_size":    self._thumb_page_size_spin.value(),
                "ai_tagging_enabled":         self._ai_tagging_check.isChecked(),
                "ai_embeddings_enabled":      self._ai_embeddings_check.isChecked(),
                "ai_embeddings_during_crawl": self._ai_embeddings_crawl_check.isChecked(),
                "ai_embeddings_batch_size":   self._ai_batch_size_spin.value(),
                "ai_temperature":             self._ai_temperature_spin.value(),
                "hotkeys": {
                    action_id: editor.keySequence().toString()
                    for action_id, editor in self._hotkey_edits.items()
                },
                "notifications_enabled":   self._notif_enabled_check.isChecked(),
                "notification_sound":      self._notif_sound_combo.currentData(),
                "notification_sound_file": self._notif_file_edit.text().strip(),
                "notify_crawl":            self._notify_crawl_check.isChecked(),
                "notify_duplicates":       self._notify_dupes_check.isChecked(),
                "notify_maintenance":      self._notify_maint_check.isChecked(),
                "notify_sidecar":          self._notify_sidecar_check.isChecked(),
                "notify_backup":           self._notify_backup_check.isChecked(),
            },
            data_dir=data_dir,
        )

        # Re-apply appearance so font/color changes take effect immediately
        apply_color_scheme(selected_scheme)

        # SQL profiling is read per-statement by db.py from QSettings — write it there.
        from PyQt6.QtCore import QSettings as _QSettings
        _QSettings().setValue("debug/sql_profiling", self._sql_profiling_check.isChecked())

        # Apply logging toggle immediately so the change doesn't require a restart
        import logging as _logging
        from app.utils.logger import setup_logging, disable_logging
        if self._logging_enabled_check.isChecked():
            if not _logging.getLogger().handlers:
                setup_logging(data_dir)
        else:
            disable_logging()

        self._original_scheme = selected_scheme

        # Install the chosen language now so newly opened dialogs pick it up;
        # already-built widgets need a restart to retranslate fully.
        if new_language != old_language:
            from app.utils import i18n
            i18n.install_language(new_language)

        if data_dir != old_dir:
            QMessageBox.information(
                self,
                self.tr("Configuration"),
                self.tr("Data directory updated. Please restart the application for the change to take effect."),
            )
        elif new_language != old_language:
            QMessageBox.information(
                self,
                self.tr("Configuration"),
                self.tr("Language updated. Please restart the application for the change to fully take effect."),
            )
        elif new_icon_set != old_icon_set:
            QMessageBox.information(
                self,
                self.tr("Configuration"),
                self.tr("Icon set updated. Please restart the application for the change to take effect."),
            )
        self.accept()

    def _cancel(self):
        apply_color_scheme(self._original_scheme)
        self.reject()
