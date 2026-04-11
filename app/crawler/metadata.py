"""
Infer model metadata from:
  1. info.json content (highest priority)
  2. .url file inside the archive
  3. Archive file path / parent folder names
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.crawler.archive import find_url_file


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
    title: Optional[str] = None
    author: Optional[str] = None
    distributor: Optional[str] = None
    release_date: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    release: Optional[str] = None


def _parse_info_json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
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

    # Title: use the archive stem, clean up underscores/dashes, then strip any
    # leading date prefix (e.g. "2024-09 " or "2024 09 ") so it doesn't bleed
    # into the title when the filename starts with a date.
    stem = archive_path.stem
    title = re.sub(r"[_\-]+", " ", stem).strip()
    title = re.sub(r"^\d{4}[\s\-_]\d{2}\s*", "", title).strip()  # "2024-09 …"
    title = re.sub(r"^\d{4}\s+", "", title).strip()               # "2024 …"
    meta.title = title if title else re.sub(r"[_\-]+", " ", stem).strip()

    # Distributor / Type — scan left-to-right (shallowest first).
    # The folder immediately after the distributor folder is the author.
    distributor_idx = None
    for i, part in enumerate(parts[:-1]):  # exclude the filename itself
        for pattern, name in _DISTRIBUTOR_PATTERNS:
            if pattern.search(part):
                meta.distributor = name
                distributor_idx = i
                break
        if distributor_idx is not None:
            break

    # Author: folder immediately after the distributor folder (if present).
    # Without a distributor signal there is no reliable heuristic, so we leave
    # author empty rather than guessing from an arbitrary folder name.
    if distributor_idx is not None:
        author_idx = distributor_idx + 1
        if author_idx < len(parts) - 1:  # must not be the filename
            meta.author = parts[author_idx]

    # Date — scan all parts, deepest first for best specificity
    for part in reversed(parts):
        for pattern in _DATE_PATTERNS:
            m = pattern.search(part)
            if m:
                meta.release_date = m.group(1)
                break
        if meta.release_date:
            break

    return meta


def extract_metadata(archive_path: Path,
                     contents: dict[str, bytes] | None = None,
                     source_dir: Path | None = None) -> ModelMetadata:
    """
    Full metadata extraction pipeline for a single archive.
    Pass *contents* (from extract_archive_contents) to avoid re-opening the archive.
    Pass *source_dir* so that path inference uses only relative components.
    """
    meta = _infer_from_path(archive_path, source_dir=source_dir)

    # .url file — check archive contents first, then filesystem
    url_from_archive: str | None = None
    if contents:
        for name, data in contents.items():
            if Path(name.replace("\\", "/")).suffix.lower() == ".url":
                for line in data.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.lower().startswith("url="):
                        url_from_archive = line[4:].strip()
                        break
                if url_from_archive:
                    break

    url = url_from_archive or find_url_file(archive_path)
    if url:
        meta.url = url

    # info.json
    raw_json: bytes | None = None
    if contents:
        for name, data in contents.items():
            if Path(name.replace("\\", "/")).name.lower() == "info.json":
                raw_json = data
                break

    if raw_json:
        info = _parse_info_json(raw_json)
        if info.get("name"):
            meta.title = info["name"]
        if info.get("author"):
            meta.author = info["author"]
        if info.get("distributor"):
            meta.distributor = info["distributor"]
        if info.get("url"):
            meta.url = info["url"]
        if info.get("description"):
            meta.description = info["description"]
        if info.get("tags") and isinstance(info["tags"], list):
            meta.tags = [str(t) for t in info["tags"]]
        if info.get("release"):
            meta.release = str(info["release"])

    return meta
