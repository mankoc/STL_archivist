"""
Download the optional colour icon sets into resources/iconsets/.

Each set is stored as ``resources/iconsets/<set_id>/<role>.svg`` — one SVG per
application icon role (the keys of ``app.utils.material_icons.ICON_MAP``) —
plus the upstream LICENSE.  The app only reads these files; it never
downloads at runtime.  Re-run after adding a role or changing a mapping.

Sets (all MIT-licensed):
  fluent_color  Microsoft Fluent UI System Icons, "Color" style.  Roles without a
                colour version fall back to Fluent Emoji (same design family).
  fluent_emoji  Microsoft Fluent Emoji, "Flat" style.
  flat_color    Icons8 Flat Color Icons (MIT / Good Boy License).
"""
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

OUT_DIR = Path(__file__).parent / "resources" / "iconsets"

_FLUENT = "https://raw.githubusercontent.com/microsoft/fluentui-system-icons/main"
_EMOJI = "https://raw.githubusercontent.com/microsoft/fluentui-emoji/main"
_FLAT = "https://raw.githubusercontent.com/icons8/flat-color-icons/master"

LICENSES = {
    "fluent_color": [f"{_FLUENT}/LICENSE", f"{_EMOJI}/LICENSE"],
    "fluent_emoji": [f"{_EMOJI}/LICENSE"],
    "flat_color":   [f"{_FLAT}/LICENSE.md"],
}


def fluent(name: str) -> str:
    """Fluent System Icons colour SVG, by asset folder name (e.g. "Arrow Sync")."""
    snake = name.lower().replace(" ", "_")
    return f"{_FLUENT}/assets/{quote(name)}/SVG/ic_fluent_{snake}_24_color.svg"


def emoji(name: str) -> str:
    """Fluent Emoji flat SVG, by asset folder name (e.g. "File folder")."""
    snake = name.lower().replace(" ", "_").replace("-", "_")
    return f"{_EMOJI}/assets/{quote(name)}/Flat/{snake}_flat.svg"


def flat(name: str) -> str:
    """Icons8 Flat Color Icons SVG, by file stem (e.g. "opened_folder")."""
    return f"{_FLAT}/svg/{name}.svg"


# role -> (fluent_color, fluent_emoji, flat_color)
ROLES: dict[str, tuple[str, str, str]] = {
    # Main window / toolbar
    "open_crawler":       (fluent("Database"),        emoji("Spider"),                          flat("add_database")),
    "update_folders":     (fluent("Arrow Sync"),      emoji("Counterclockwise arrows button"),  flat("synchronize")),
    # The three image actions must stay visually distinct from each other.
    "image_grid":         (fluent("Apps"),            emoji("Film frames"),                     flat("grid")),
    "archive_browser":    (fluent("Camera"),          emoji("Camera"),                          flat("stack_of_photos")),
    "toggle_images":      (fluent("Image"),           emoji("Framed picture"),                  flat("picture")),
    "toggle_info":        (emoji("Information"),      emoji("Information"),                     flat("info")),
    "find_dupes":         (fluent("Document"),        emoji("Magnifying glass tilted left"),    flat("search")),
    "find_content_dupes": (fluent("Library"),         emoji("Card file box"),                   flat("view_details")),
    "find_by_image":      (fluent("Search Visual"),   emoji("Magnifying glass tilted right"),   flat("binoculars")),
    # Crawler controls
    "crawler_start":      (emoji("Play button"),      emoji("Play button"),                     flat("start")),
    "crawler_stop":       (emoji("Stop button"),      emoji("Stop button"),                     flat("cancel")),
    "crawler_bg":         (fluent("Briefcase"),       emoji("Briefcase"),                       flat("briefcase")),
    "crawler_close":      (fluent("Dismiss Circle"),  emoji("Cross mark"),                      flat("leave")),
    # Tree navigation
    "arrow_down":         (emoji("Down arrow"),       emoji("Down arrow"),                      flat("down")),
    "arrow_up":           (emoji("Up arrow"),         emoji("Up arrow"),                        flat("up")),
    "branch_closed":      (emoji("File folder"),      emoji("File folder"),                     flat("folder")),
    "branch_open":        (emoji("Open file folder"), emoji("Open file folder"),                flat("opened_folder")),
    # Filter-pane sections
    "icon_author":        (fluent("Person"),          emoji("Bust in silhouette"),              flat("businessman")),
    "icon_tag":           (emoji("Label"),            emoji("Label"),                           flat("bookmark")),
    "icon_type":          (fluent("Gift"),            emoji("Package"),                         flat("shop")),
    "icon_path":          (emoji("Open file folder"), emoji("Open file folder"),                flat("opened_folder")),
    "icon_date":          (fluent("Calendar"),        emoji("Spiral calendar"),                 flat("calendar")),
    "icon_sculptor":      (fluent("Paint Brush"),     emoji("Moai"),                            flat("edit_image")),
    "icon_category":      (fluent("Bookmark"),        emoji("Card index dividers"),             flat("template")),
    "icon_subcategory":   (emoji("File folder"),      emoji("File folder"),                     flat("folder")),
    "icon_release":       (fluent("Star"),            emoji("Sparkles"),                        flat("rating")),
    "icon_scale":         (emoji("Straight ruler"),   emoji("Straight ruler"),                  flat("ruler")),
    "icon_presupports":   (fluent("Wrench"),          emoji("Building construction"),           flat("engineering")),
    "icon_warnings":      (fluent("Warning"),         emoji("Warning"),                         flat("high_priority")),
}

SETS = ("fluent_color", "fluent_emoji", "flat_color")


def _fetch(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        return response.read()


def main() -> int:
    failed = 0
    for col, set_id in enumerate(SETS):
        target = OUT_DIR / set_id
        target.mkdir(parents=True, exist_ok=True)
        logger.info(f"{set_id}:")
        for role, urls in ROLES.items():
            try:
                (target / f"{role}.svg").write_bytes(_fetch(urls[col]))
            except Exception as exc:
                failed += 1
                logger.error(f"  {role}: FAILED ({urls[col]}) — {exc}")
        licenses = [_fetch(u).decode("utf-8") for u in LICENSES[set_id]]
        sources = "\n".join(f"{role}: {urls[col]}" for role, urls in ROLES.items())
        (target / "LICENSE.txt").write_text(
            "\n\n---\n\n".join(licenses) + "\n\n---\nSources:\n" + sources + "\n",
            encoding="utf-8",
        )
        logger.info(f"  {len(ROLES)} role(s) processed")
    logger.info("Done." if not failed else f"{failed} icon(s) failed — see above.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
