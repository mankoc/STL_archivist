"""
Infer model metadata from:
  1. info.json content (highest priority)
  2. .url file inside the archive
  3. Archive file path / parent folder names
"""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.crawler.archive import find_url_file

logger = logging.getLogger(__name__)


# Known distributor/source keywords that appear in folder names
_DISTRIBUTOR_PATTERNS = [
    (re.compile(r"\bpatreons?\b", re.I), "Patreon"),
    (re.compile(r"\bkickstarters?\b", re.I), "Kickstarter"),
    (re.compile(r"\bmyminifactory\b", re.I), "MyMiniFactory"),
    (re.compile(r"\bartstation\b", re.I), "ArtStation"),
    (re.compile(r"\bgumroad\b", re.I), "Gumroad"),
    (re.compile(r"\bcults3d\b", re.I), "Cults3D"),
    (re.compile(r"\bcults\b", re.I), "Cults3D"),
    (re.compile(r"\bthingiverse\b", re.I), "Thingiverse"),
    (re.compile(r"\bprintables\b", re.I), "Printables"),
]

# Date patterns in path components: 2024-01, 2024_01, Jan2024, 2024
_DATE_PATTERNS = [
    re.compile(r"\b(\d{4}[-_]\d{2})\b"),   # 2024-01
    re.compile(r"\b(\d{4})\b"),             # 2024
]


@dataclass
class ModelMetadata:
    """Metadata extracted from a single STL archive.

    Fields are populated in priority order: ``info.json`` values override
    ``.url`` file values, which override path-inferred values.  Fields remain
    ``None`` when no source provided a value.

    Attributes:
        title: Model title / display name.
        creator: Creator or sculptor name.
        distributor: Publishing platform (e.g. ``"Patreon"``, ``"MyMiniFactory"``).
        release_date: Release date string as found in the archive path (e.g.
            ``"2024-01"``).  Not normalised.
        url: Source URL from a ``.url`` shortcut or ``info.json``.
        description: Free-text description from ``info.json``.
        tags: List of tag strings from ``info.json``.
        release: Release bundle name from ``info.json``.
    """

    title: Optional[str] = None
    creator: Optional[str] = None
    distributor: Optional[str] = None
    release_date: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    release: Optional[str] = None


def _parse_info_json(raw: bytes) -> dict:
    """Parse raw JSON bytes into a dict, returning ``{}`` on any error.

    Args:
        raw: Raw bytes read from an ``info.json`` archive member.

    Returns:
        Parsed dict, or an empty dict if decoding or parsing fails.
    """
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse info.json: %s", exc)
        return {}


def _infer_from_path(archive_path: Path,
                     source_dir: Path | None = None) -> ModelMetadata:
    """Guess fields by looking at parent directory names and the archive stem.

    When *source_dir* is provided only path components relative to it are
    considered, preventing OS-level folders (e.g. ``home``, ``Users``) from
    being mistakenly identified as the author.
    """
    meta = ModelMetadata()
    if source_dir is not None:
        try:
            rel = archive_path.relative_to(source_dir)
            parts = list(rel.parts)  # relative components including filename
        except ValueError:
            parts = list(archive_path.parts)
    else:
        parts = list(archive_path.parts)  # all path components including filename

    logger.debug("Path inference for: %s (parts: %s)", archive_path.name, parts)

    # Title: use the archive stem, clean up underscores/dashes, then strip any
    # leading date prefix (e.g. "2024-09 " or "2024 09 ") so it doesn't bleed
    # into the title when the filename starts with a date.
    stem = archive_path.stem
    title = re.sub(r"[_\-]+", " ", stem).strip()
    title = re.sub(r"^\d{4}[\s\-_]\d{2}\s*", "", title).strip()  # "2024-09 …"
    title = re.sub(r"^\d{4}\s+", "", title).strip()               # "2024 …"
    meta.title = title if title else re.sub(r"[_\-]+", " ", stem).strip()
    logger.debug("  Inferred title: %r", meta.title)

    # Distributor / Type — scan left-to-right (shallowest first).
    # The folder immediately after the distributor folder is the author.
    distributor_idx = None
    for i, part in enumerate(parts[:-1]):  # exclude the filename itself
        for pattern, name in _DISTRIBUTOR_PATTERNS:
            if pattern.search(part):
                meta.distributor = name
                distributor_idx = i
                logger.debug("  Distributor matched %r in part %r (index %d)",
                             name, part, i)
                break
        if distributor_idx is not None:
            break

    if distributor_idx is None:
        logger.debug("  No distributor pattern matched in path")

    # Author: folder immediately after the distributor folder (if present).
    # Without a distributor signal there is no reliable heuristic, so we leave
    # author empty rather than guessing from an arbitrary folder name.
    if distributor_idx is not None:
        author_idx = distributor_idx + 1
        if author_idx < len(parts) - 1:  # must not be the filename
            meta.creator = parts[author_idx]
            logger.debug("  Inferred author: %r (from path part at index %d)",
                         meta.creator, author_idx)

    # Date — scan all parts, deepest first for best specificity
    for part in reversed(parts):
        for pattern in _DATE_PATTERNS:
            m = pattern.search(part)
            if m:
                meta.release_date = m.group(1)
                logger.debug("  Inferred date: %r (from part %r)", meta.release_date, part)
                break
        if meta.release_date:
            break

    if not meta.release_date:
        logger.debug("  No date pattern matched in path")

    return meta


def extract_metadata(
    archive_path: Path,
    contents: Optional[dict[str, bytes]] = None,
    source_dir: Optional[Path] = None,
) -> ModelMetadata:
    """Run the full metadata extraction pipeline for a single archive.

    Sources are consulted in priority order (each overrides the previous):
    1. Path inference — title, date, and optionally distributor/author from
       the folder structure.
    2. ``.url`` files — URL from inside the archive, then from the filesystem.
    3. ``info.json`` — all fields; highest priority.

    Args:
        archive_path: Absolute path to the archive file.
        contents: Pre-extracted file contents from ``extract_archive_contents()``.
            Pass this to avoid reopening the archive.  When ``None``, the
            filesystem is still searched for ``.url`` files.
        source_dir: Root of the crawl; only path components relative to this
            are used for inference, preventing OS-level folders from being
            mistaken for metadata.

    Returns:
        Populated ``ModelMetadata`` instance.
    """
    logger.debug("Extracting metadata for: %s", archive_path.name)
    meta = _infer_from_path(archive_path, source_dir=source_dir)

    # .url file — check archive contents first, then filesystem
    url_from_archive: str | None = None
    if contents:
        for name, data in contents.items():
            if Path(name.replace("\\", "/")).suffix.lower() == ".url":
                logger.debug("  Found .url file in archive contents: %r", name)
                for line in data.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.lower().startswith("url="):
                        url_from_archive = line[4:].strip()
                        logger.debug("  URL from archive .url file: %r", url_from_archive)
                        break
                if url_from_archive:
                    break

    if not url_from_archive:
        logger.debug("  No .url in archive contents — searching filesystem")
    url = url_from_archive or find_url_file(archive_path)
    if url:
        meta.url = url
        logger.debug("  URL resolved: %r", url)
    else:
        logger.debug("  No URL found for %s", archive_path.name)

    # info.json
    raw_json: bytes | None = None
    if contents:
        for name, data in contents.items():
            if Path(name.replace("\\", "/")).name.lower() == "info.json":
                raw_json = data
                logger.debug("  Found info.json in archive contents (%d B): %r", len(data), name)
                break

    if raw_json:
        info = _parse_info_json(raw_json)
        logger.debug("  Parsed info.json keys: %s", list(info.keys()))
        overrides = []
        if info.get("name"):
            meta.title = info["name"]
            overrides.append(f"title={meta.title!r}")
        if info.get("creator") or info.get("author"):
            meta.creator = info.get("creator") or info.get("author")
            overrides.append(f"creator={meta.creator!r}")
        if info.get("distributor"):
            meta.distributor = info["distributor"]
            overrides.append(f"distributor={meta.distributor!r}")
        if info.get("url"):
            meta.url = info["url"]
            overrides.append(f"url={meta.url!r}")
        if info.get("description"):
            meta.description = info["description"]
            overrides.append("description=<set>")
        if info.get("tags") and isinstance(info["tags"], list):
            meta.tags = [str(t) for t in info["tags"]]
            overrides.append(f"tags={meta.tags}")
        if info.get("release"):
            meta.release = str(info["release"])
            overrides.append(f"release={meta.release!r}")
        if overrides:
            logger.debug("  info.json overrides: %s", ", ".join(overrides))
        else:
            logger.debug("  info.json contained no recognised fields")
    else:
        logger.debug("  No info.json found for %s", archive_path.name)

    logger.debug(
        "  Final metadata — title=%r author=%r source=%r date=%r tags=%s url=%r",
        meta.title, meta.creator, meta.distributor, meta.release_date,
        meta.tags, meta.url,
    )
    return meta
