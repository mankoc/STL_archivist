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

_PRESUPPORTED_RE = re.compile(r"[_ -]supported", re.IGNORECASE)


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
