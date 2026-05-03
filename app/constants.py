"""
Cross-cutting constants used by the crawler, UI, and services.

Anything appearing in two or more modules — file-extension sets, dialog
window sizes, theme color tokens — lives here so a single edit changes the
value everywhere.  Module-private values stay in their owning module.
"""

# ── Archive / file extensions ────────────────────────────────────────────────

ARCHIVE_SUFFIXES: frozenset[str] = frozenset({".7z", ".zip", ".rar"})
IMAGE_SUFFIXES:   frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
SLICER_SUFFIXES:  frozenset[str] = frozenset({".lys", ".chitu", ".ctb", ".photon"})

METADATA_FILENAME = "METADATA.7z"


# ── UI colors ────────────────────────────────────────────────────────────────

# Material Design red 700 — used for destructive-option callouts in the
# config dialog warning text.
CRITICAL_RED = "#d32f2f"

# Translucent green tint applied behind matched-pair rows in the file
# comparison table.  Tuple is (R, G, B, alpha) on 0–255 scale.
MATCH_HIGHLIGHT_RGBA: tuple[int, int, int, int] = (100, 200, 100, 50)


# ── Dialog window sizes ──────────────────────────────────────────────────────

# (width, height) in pixels for non-resizable / default-resize dialogs.
WINDOW_SIZES: dict[str, tuple[int, int]] = {
    "file_list":    (800, 600),
    "file_compare": (1200, 700),
}
