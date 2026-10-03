"""
Pre-download Material Design Icons and cache them locally.

This script downloads all Material Design Icons needed by the application
and caches them in resources/material-icons/ so they don't need to be
downloaded at runtime.
"""
from pathlib import Path
from urllib.request import urlopen
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Material Icons CDN - using jsDelivr CDN which hosts Material Design Icons
MATERIAL_ICONS_CDN = "https://cdn.jsdelivr.net/npm/@material-design-icons/svg/outlined"

# All Material Icon names used in the app (from material_icons.py ICON_MAP)
MATERIAL_ICONS_NEEDED = {
    "settings", "refresh", "grid_view", "photo_library", "image", "info", "find_in_page",
    "image_search", "play_arrow", "stop", "work", "close", "expand_more",
    "expand_less", "folder", "folder_open", "person", "local_offer",
    "category", "calendar_today", "new_releases", "straighten", "layers",
    "warning",
}

CACHE_DIR = Path(__file__).parent / "resources" / "material-icons"


def download_icon(icon_name: str) -> bool:
    """Download a single Material Design Icon and cache it."""
    cache_file = CACHE_DIR / f"{icon_name}.svg"

    # Skip if already cached
    if cache_file.exists():
        logger.info(f"  {icon_name}: already cached")
        return True

    try:
        url = f"{MATERIAL_ICONS_CDN}/{icon_name}.svg"
        logger.info(f"  Downloading {icon_name}...")

        with urlopen(url, timeout=10) as response:
            svg_content = response.read()

        cache_file.write_bytes(svg_content)
        logger.info(f"  {icon_name}: OK")
        return True

    except Exception as e:
        logger.error(f"  {icon_name}: FAILED - {e}")
        return False


def main():
    """Download all Material Design Icons needed by the app."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Cache directory: {CACHE_DIR}")

    success = 0
    failed = 0

    logger.info(f"Downloading {len(MATERIAL_ICONS_NEEDED)} Material Design Icons...")
    for icon_name in sorted(MATERIAL_ICONS_NEEDED):
        if download_icon(icon_name):
            success += 1
        else:
            failed += 1

    logger.info(f"\nDownload complete: {success} OK, {failed} FAILED")

    if failed == 0:
        logger.info("All Material Design Icons are cached and ready!")
    else:
        logger.warning(f"{failed} icons failed to download (app will generate placeholders)")


if __name__ == "__main__":
    main()
