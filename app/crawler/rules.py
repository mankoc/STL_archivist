"""
Scraping rules applied to archive member names during crawl.
"""
import logging
import re
from pathlib import Path

from app.constants import ARCHIVE_SUFFIXES, IMAGE_SUFFIXES

logger = logging.getLogger(__name__)


# Matches `body_supported`, `arm-supported`, `torso supported`. Used as the
# stem-level test for the "Presupported" classification.
_PRESUPPORTED_RE     = re.compile(r"[_ -]supported", re.IGNORECASE)

# Captures the integer in patterns like `28mm`, `32 mm`, `75_mm`.
_SCALE_MM_RE         = re.compile(r"\b(\d+)[_ ]?mm\b", re.IGNORECASE)

# Captures the denominator in patterns like `1/35`, `1-72`.
_SCALE_RATIO_RE      = re.compile(r"\b1[/\-](\d+)\b")

# Matches filenames like "body_presupported.stl", "torso presupported.stl",
# "supported.stl", "sup_dragon.stl" (the sup*.stl prefix form).
_WARN_PRESUP_STL_RE    = re.compile(r"(?:[ _]presupported\.stl$|^sup.*\.stl$)", re.IGNORECASE)

# Default regex patterns used by the "archive with supports" warning.
# Each pattern is applied (re.IGNORECASE) to the bare filename of every archive member.
#   Pattern 1 — "supported.stl" NOT preceded by "un" (so "unsupported" is excluded).
#   Pattern 2 — Any file whose name starts with "sup" (e.g. "sup_arm.stl").
DEFAULT_SUPPORTS_PATTERNS: list[str] = [
    r"(?<!un)supported\.stl$",
    r"^sup.*\.stl$",
]


def detect_presupport(names: list[str]) -> str:
    """Return the presupport classification for an archive given its member *names*.

    Rules are applied in priority order (first match wins):

    1. **Lychee** — archive contains a ``.lys`` or ``.lyt`` slicer file.
    2. **Chitubox** — archive contains a ``.chitu`` slicer file.
    3. **Presupported** — archive contains an ``.stl`` file whose stem matches
       ``[_ -]supported`` (e.g. ``body_supported.stl``, ``arm-supported.stl``).
    4. **No** — none of the above criteria matched.

    Args:
        names: List of archive member names (full paths as returned by the
            archive library).

    Returns:
        One of the strings ``"Lychee"``, ``"Chitubox"``, ``"Presupported"``,
        or ``"No"``.
    """
    has_chitu        = False
    has_presupported = False

    for name in names:
        p      = Path(name.replace("\\", "/"))
        suffix = p.suffix.lower()

        if suffix in (".lys", ".lyt"):        # rule 1 – Lychee (highest priority)
            logger.debug("  Presupport=Lychee (matched %r)", name)
            return "Lychee"
        if suffix == ".chitu":                # rule 2 – Chitubox
            logger.debug("  Presupport candidate Chitubox (matched %r)", name)
            has_chitu = True
        if suffix == ".stl" and _PRESUPPORTED_RE.search(p.stem):  # rule 3
            logger.debug("  Presupport candidate Presupported (matched stem %r)", p.stem)
            has_presupported = True

    if has_chitu:
        logger.debug("  Presupport=Chitubox")
        return "Chitubox"
    if has_presupported:
        logger.debug("  Presupport=Presupported")
        return "Presupported"
    logger.debug("  Presupport=No")
    return "No"                               # rule 4 – fallback


def detect_warnings(
    names: list[str],
    check_presupported: bool = True,
    check_no_images: bool = True,
    check_nested_archive: bool = True,
    check_supports: bool = False,
    supports_patterns: list[str] | None = None,
) -> str:
    """Return a newline-separated string of warnings for an archive.

    Each check is independently enabled so callers can mirror the user's
    configuration flags without applying all checks unconditionally.

    Args:
        names: Archive member names as returned by ``list_contents``.
        check_presupported: Warn when the archive contains an STL matching
            ``[ _]presupported.stl`` or the ``sup*.stl`` pattern.
        check_no_images: Warn when the archive contains no recognisable image
            file (``.jpg``, ``.jpeg``, ``.png``, ``.webp``, ``.bmp``).
        check_nested_archive: Warn when the archive contains another compressed
            archive (``.zip``, ``.rar``, ``.7z``).
        check_supports: Warn when the archive contains files matching any of
            the patterns in *supports_patterns*.
        supports_patterns: List of regex strings (case-insensitive) applied to
            the bare filename of each archive member. Invalid patterns are
            silently skipped. Defaults to ``DEFAULT_SUPPORTS_PATTERNS`` when
            *check_supports* is ``True`` and this argument is ``None``.

    Returns:
        Newline-separated warning strings, or ``""`` if no warnings apply.
    """
    warnings: list[str] = []
    has_image            = False
    has_presup_stl       = False
    has_nested_archive   = False
    has_supports         = False

    # Pre-compile support patterns once so they are not recompiled per filename.
    compiled_support: list[re.Pattern] = []
    if check_supports:
        patterns = supports_patterns if supports_patterns is not None else DEFAULT_SUPPORTS_PATTERNS
        for p in patterns:
            try:
                compiled_support.append(re.compile(p, re.IGNORECASE))
            except re.error:
                logger.warning("Invalid supports pattern %r — skipped", p)

    for name in names:
        fname = Path(name.replace("\\", "/")).name
        suffix = Path(fname).suffix.lower()
        if check_no_images and suffix in IMAGE_SUFFIXES:
            has_image = True
        if check_presupported and _WARN_PRESUP_STL_RE.search(fname):
            has_presup_stl = True
        if check_nested_archive and suffix in ARCHIVE_SUFFIXES:
            has_nested_archive = True
        if compiled_support and not has_supports:
            for rx in compiled_support:
                if rx.search(fname):
                    has_supports = True
                    break

    if check_presupported and has_presup_stl:
        warnings.append("presupported stl")
        logger.debug("  Warning: presupported stl")
    if check_no_images and not has_image:
        warnings.append("Archive without images")
        logger.debug("  Warning: archive without images")
    if check_nested_archive and has_nested_archive:
        warnings.append("Archive inside archive")
        logger.debug("  Warning: archive inside archive")
    if has_supports:
        warnings.append("Archive with supports")
        logger.debug("  Warning: archive with supports")

    if not warnings:
        logger.debug("  No warnings")

    return "\n".join(warnings)


def detect_scale(names: list[str]) -> list[str]:
    """Return a de-duplicated list of scale strings detected in *names*.

    Each entry in *names* is a file path (archive member or the archive path
    itself).  Only the final path component (stem) is scanned.

    Detected patterns:

    - ``<integer>[_ ]?mm``  →  ``"<integer> mm"``   (e.g. ``28mm``, ``32 mm``, ``75_mm``)
    - ``1/<integer>``       →  ``"1/<integer>"``     (e.g. ``1/35``, ``1/72``)
    - ``1-<integer>``       →  ``"1/<integer>"``     (e.g. ``1-35`` → ``"1/35"``)

    Args:
        names: Paths to scan — typically the archive member list prepended with
            the archive file path itself so the archive name is also checked.

    Returns:
        De-duplicated list of scale strings in order of first appearance.
        Empty list when no scale is detected.
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
                logger.debug("  Scale detected: %r (from %r)", scale, stem)

        for m in _SCALE_RATIO_RE.finditer(stem):
            scale = f"1/{m.group(1)}"
            if scale not in seen:
                seen.add(scale)
                found.append(scale)
                logger.debug("  Scale detected: %r (from %r)", scale, stem)

    if not found:
        logger.debug("  No scale detected")
    return found
