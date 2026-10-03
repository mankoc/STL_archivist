"""
Application icons for PyQt6: Material Design Icons plus optional colour sets.

Maps logical icon names (roles) to Material Icons and handles light/dark theme
coloring.  The user can pick a colour icon set in Configuration → Appearance;
those live in ``resources/iconsets/<set_id>/<role>.svg`` (fetched once by
``setup_iconsets.py``) and are rendered with their own colours.  Any role a
set lacks falls back to the Material icon.
"""
from pathlib import Path
from urllib.request import urlopen
import logging

from PyQt6.QtGui import QIcon, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtCore import QByteArray, Qt

# Mapping of application icon names to Material Icon names
ICON_MAP = {
    # Main window
    "open_crawler": "settings",
    "update_folders": "refresh",
    "image_grid": "grid_view",
    "archive_browser": "photo_library",
    "toggle_images": "image",
    "toggle_info": "info",
    "find_dupes": "find_in_page",
    "find_content_dupes": "image_search",
    "find_by_image": "image_search",

    # Crawler controls
    "crawler_start": "play_arrow",
    "crawler_stop": "stop",
    "crawler_bg": "work",
    "crawler_close": "close",

    # Tree navigation
    "arrow_down": "expand_more",
    "arrow_up": "expand_less",
    "branch_closed": "folder",
    "branch_open": "folder_open",

    # Info icons
    "icon_author": "person",
    "icon_tag": "local_offer",
    "icon_type": "category",
    "icon_path": "folder_open",
    "icon_date": "calendar_today",
    "icon_sculptor": "person",
    "icon_category": "category",
    "icon_subcategory": "folder",
    "icon_release": "new_releases",
    "icon_scale": "straighten",
    "icon_presupports": "layers",
    "icon_warnings": "warning",
}

# Material Icons API endpoint
MATERIAL_ICONS_CDN = "https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined"
MATERIAL_ICONS_GITHUB = "https://raw.githubusercontent.com/google/material-design-icons/master/svg/production"

_CACHE_DIR = None
_COLOR_LIGHT = "#212121"  # Near-black for light themes
_COLOR_DARK = "#FFFFFF"   # White for dark themes


def set_cache_dir(cache_dir: Path | str) -> None:
    """Set the directory where Material Icons are cached."""
    global _CACHE_DIR
    _CACHE_DIR = Path(cache_dir)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def get_cache_dir() -> Path:
    """Get the Material Icons cache directory."""
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = Path(__file__).parent.parent.parent / "resources" / "material-icons"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def _get_material_icon_svg(icon_name: str) -> str | None:
    """
    Retrieve cached Material Icon SVG from local cache.

    All Material Design Icons are pre-cached locally via setup_material_icons.py
    to avoid network requests at runtime. Returns SVG content as string, or None
    if the icon is not found in the cache.
    """
    cache_dir = get_cache_dir()
    cache_file = cache_dir / f"{icon_name}.svg"

    # Return cached version if available
    if cache_file.exists():
        try:
            return cache_file.read_text(encoding="utf-8")
        except Exception as e:
            logging.warning(f"Failed to read cached icon {icon_name}: {e}")
            return None

    # Icon not in cache - return None and caller will use placeholder
    logging.debug(f"Material icon not cached: {icon_name}")
    return None


def _generate_placeholder_icon(icon_name: str, size: int = 24) -> QIcon:
    """Generate a placeholder QIcon when icon is not found."""
    from PyQt6.QtGui import QFont, QColor

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.lightGray)

    painter = QPainter(pixmap)
    painter.setFont(QFont("Arial", 6))
    painter.setPen(QColor(128, 128, 128))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, icon_name[:2].upper())
    painter.end()

    return QIcon(pixmap)


def _tint_svg(svg_content: str, color: str) -> str:
    """
    Tint SVG by adding fill attribute to path elements.

    Handles SVGs where color should be applied dynamically.
    """
    # Simple approach: inject fill style if not present
    if "fill=" not in svg_content and "style=" not in svg_content:
        # Add fill attribute to path/circle/etc elements
        svg_content = svg_content.replace(
            "<path ",
            f'<path fill="{color}" '
        ).replace(
            "<circle ",
            f'<circle fill="{color}" '
        ).replace(
            "<rect ",
            f'<rect fill="{color}" '
        )

    return svg_content


def get_material_icon(icon_name: str, is_dark_mode: bool = False, size: int = 24) -> QIcon:
    """
    Get a Material Icon as QIcon with theme-appropriate coloring.

    Args:
        icon_name: Material icon name (e.g., 'refresh', 'settings')
        is_dark_mode: True for dark theme (white icons), False for light theme (dark icons)
        size: Icon size in pixels (default 24)

    Returns:
        QIcon object ready to use in PyQt6 widgets, or empty QIcon if not found
    """
    try:
        svg_content = _get_material_icon_svg(icon_name)
        if not svg_content:
            logging.warning(f"Material icon not found in cache: {icon_name}")
            return _generate_placeholder_icon(icon_name, size)

        # Apply theme coloring
        color = _COLOR_DARK if is_dark_mode else _COLOR_LIGHT
        tinted_svg = _tint_svg(svg_content, color)

        # Render SVG to QIcon
        renderer = QSvgRenderer(QByteArray(tinted_svg.encode("utf-8")))
        if not renderer.isValid():
            logging.warning(f"Failed to render SVG for icon: {icon_name}")
            return _generate_placeholder_icon(icon_name, size)

        # Render several sizes so toolbars asking for larger icons (36 px)
        # get a crisp image instead of a 24 px one.
        icon = QIcon()
        for px in sorted({size, *_RENDER_SIZES}):
            pixmap = QPixmap(px, px)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(pixmap)
        return icon
    except Exception as e:
        logging.error(f"Error loading Material Icon '{icon_name}': {e}")
        return _generate_placeholder_icon(icon_name, size)


# ---------------------------------------------------------------------------
# Selectable icon sets
# ---------------------------------------------------------------------------

DEFAULT_ICON_SET = "material"

#: set id -> display name (Configuration → Appearance → Icon set)
ICON_SETS: dict[str, str] = {
    "material":     "Material (monochrome)",
    "fluent_color": "Fluent Color",
    "fluent_emoji": "Fluent Emoji",
    "flat_color":   "Flat Color Icons",
}

ICONSETS_DIR = Path(__file__).parent.parent.parent / "resources" / "iconsets"

_RENDER_SIZES = (16, 24, 32, 48)
_active_set: str | None = None


def available_icon_sets() -> dict[str, str]:
    """Return the selectable sets whose files are present (Material always is)."""
    return {
        set_id: label for set_id, label in ICON_SETS.items()
        if set_id == DEFAULT_ICON_SET or (ICONSETS_DIR / set_id).is_dir()
    }


def active_icon_set() -> str:
    """The icon set chosen in Configuration, read once per run.

    Icons are built when widgets are created, so a change applies on restart.
    """
    global _active_set
    if _active_set is None:
        try:
            from app.core.settings import _load_library_settings
            chosen = _load_library_settings().get("icon_set", DEFAULT_ICON_SET)
        except Exception:
            chosen = DEFAULT_ICON_SET
        _active_set = chosen if chosen in available_icon_sets() else DEFAULT_ICON_SET
    return _active_set


def set_active_icon_set(set_id: str | None) -> None:
    """Override (or with ``None`` forget) the cached choice — for previews and tests."""
    global _active_set
    _active_set = set_id


def iconset_svg_path(set_id: str, app_icon_name: str) -> Path | None:
    """Path of *app_icon_name*'s SVG in a colour set, or ``None`` if absent."""
    if set_id == DEFAULT_ICON_SET:
        return None
    path = ICONSETS_DIR / set_id / f"{app_icon_name}.svg"
    return path if path.is_file() else None


def _render_color_svg(path: Path) -> QIcon | None:
    """Render a full-colour SVG (no tinting) at several sizes for crisp scaling."""
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        logging.warning(f"Failed to render colour icon: {path}")
        return None
    renderer.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
    icon = QIcon()
    for size in _RENDER_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def lookup_icon(app_icon_name: str, is_dark_mode: bool = False, icon_set: str | None = None) -> QIcon:
    """
    Look up and return the icon for an application icon name.

    This is the main entry point.  With a colour set active (or passed as
    *icon_set*), that set's SVG is used as-is; otherwise — or when the set
    has no icon for this role — the Material icon is themed for light/dark.

    Args:
        app_icon_name: Application icon name (e.g., 'open_crawler', 'toggle_images')
        is_dark_mode: True for dark theme, False for light theme (Material only)
        icon_set: Set id to use instead of the configured one (e.g. a preview)

    Returns:
        QIcon object, or empty QIcon if not found
    """
    set_id = icon_set or active_icon_set()
    color_path = iconset_svg_path(set_id, app_icon_name)
    if color_path is not None:
        icon = _render_color_svg(color_path)
        if icon is not None:
            return icon

    material_name = ICON_MAP.get(app_icon_name)
    if not material_name:
        return QIcon()

    return get_material_icon(material_name, is_dark_mode=is_dark_mode)
