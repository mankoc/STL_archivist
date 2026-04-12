"""
Scraping rules applied to archive member names during crawl.
"""
import re
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────────
# Presupport detection
#
# Rules applied in priority order (first match wins):
#
#   1. Lychee        — archive contains one or more files with extension
#                      .lys or .lyt
#
#   2. Chitubox      — archive contains one or more files with extension
#                      .chitu
#
#   3. Presupported  — archive contains one or more .stl files whose stem
#                      contains an underscore, space, or dash followed by
#                      the word "supported"  (e.g. "body_supported.stl",
#                      "torso-supported.stl", "arm supported.stl")
#
#   4. No            — none of the above criteria matched
# ──────────────────────────────────────────────────────────────────────────────
# Scale detection
#
# Each member path (and the archive path itself) is scanned for two patterns:
#
#   • <integer>[_ ]?mm   →  "<integer> mm"   (e.g. 28mm, 32 mm, 75_mm)
#   • 1/<integer>        →  "1/<integer>"    (e.g. 1/35, 1/72)
#   • 1-<integer>        →  "1/<integer>"    (e.g. 1-35  → "1/35")
#
# Returns a de-duplicated list in order of first appearance.
# ──────────────────────────────────────────────────────────────────────────────

_PRESUPPORTED_RE = re.compile(r"[_ -]supported", re.IGNORECASE)
_SCALE_MM_RE     = re.compile(r"\b(\d+)[_ ]?mm\b", re.IGNORECASE)
_SCALE_RATIO_RE  = re.compile(r"\b1[/\-](\d+)\b")


def detect_presupport(names: list[str]) -> str:
    """Return the presupport value for an archive given its member *names*."""
    has_chitu        = False
    has_presupported = False

    for name in names:
        p      = Path(name.replace("\\", "/"))
        suffix = p.suffix.lower()

        if suffix in (".lys", ".lyt"):        # rule 1 – Lychee (highest priority)
            return "Lychee"
        if suffix == ".chitu":                # rule 2 – Chitubox
            has_chitu = True
        if suffix == ".stl" and _PRESUPPORTED_RE.search(p.stem):  # rule 3
            has_presupported = True

    if has_chitu:
        return "Chitubox"
    if has_presupported:
        return "Presupported"
    return "No"                               # rule 4 – fallback


def detect_scale(names: list[str]) -> list[str]:
    """
    Return a de-duplicated list of scale strings detected in *names*.

    Each entry in *names* is a file path (archive member or the archive path
    itself).  Only the final path component (stem) is scanned.

    Detected patterns:
      • ``<integer>[_ ]?mm``  →  ``"<integer> mm"``   (e.g. 28mm, 32 mm, 75_mm)
      • ``1/<integer>``       →  ``"1/<integer>"``     (e.g. 1/35, 1/72)
      • ``1-<integer>``       →  ``"1/<integer>"``     (e.g. 1-35  → "1/35")
    """
    seen:  set[str]  = set()
    found: list[str] = []

    for name in names:
        stem = Path(name.replace("\\", "/")).stem

        for m in _SCALE_MM_RE.finditer(stem):
            scale = f"{m.group(1)} mm"
            if scale not in seen:
                seen.add(scale)
                found.append(scale)

        for m in _SCALE_RATIO_RE.finditer(stem):
            scale = f"1/{m.group(1)}"
            if scale not in seen:
                seen.add(scale)
                found.append(scale)

    return found
