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

from app.crawler.archive import read_info_json, read_url_file


# Known distributor/source keywords that appear in folder names
_DISTRIBUTOR_PATTERNS = [
    (re.compile(r"\bpatreon\b", re.I), "Patreon"),
    (re.compile(r"\bkickstarter\b", re.I), "Kickstarter"),
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


def _parse_info_json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _infer_from_path(archive_path: Path) -> ModelMetadata:
    """Guess fields by looking at parent directory names and the archive stem."""
    meta = ModelMetadata()
    parts = list(archive_path.parts)  # all path components

    # Title: use the archive stem, clean up underscores/dashes
    stem = archive_path.stem
    meta.title = re.sub(r"[_\-]+", " ", stem).strip()

    # Scan path parts from deepest (closest to the file) to shallowest so that
    # the most-specific folder wins.  Example:
    #   /patreon/AuthorA/Kickstarters/model.zip  →  Kickstarter  (not Patreon)
    #   /patreon/AuthorA/model.zip               →  Patreon
    parts_deep_first = list(reversed(parts))  # [filename, immediate parent, …, root]

    # Distributor / Type — first hit walking up from the file wins
    for part in parts_deep_first:
        for pattern, name in _DISTRIBUTOR_PATTERNS:
            if pattern.search(part):
                meta.distributor = name
                break
        if meta.distributor:
            break

    # Date — same depth-first strategy
    for part in parts_deep_first:
        for pattern in _DATE_PATTERNS:
            m = pattern.search(part)
            if m:
                meta.release_date = m.group(1)
                break
        if meta.release_date:
            break

    # Author heuristic: the first folder below the root STL folder that is not
    # a year/date and not a known keyword tends to be an author name.
    # We look for a part that looks like a name (title-cased, not purely numeric).
    for part in reversed(parts[:-1]):  # exclude filename itself
        if re.match(r"^\d{4}", part):
            continue
        if any(p.search(part) for p, _ in _DISTRIBUTOR_PATTERNS):
            continue
        if len(part) > 2 and not part.startswith("."):
            meta.author = part
            break

    return meta


def extract_metadata(archive_path: Path) -> ModelMetadata:
    """
    Full metadata extraction pipeline for a single archive.
    Returns a ModelMetadata dataclass with best-effort fields populated.
    """
    meta = _infer_from_path(archive_path)

    # Override with data from .url file
    url_from_file = read_url_file(archive_path)
    if url_from_file:
        meta.url = url_from_file

    # Override / enrich with info.json
    raw_json = read_info_json(archive_path)
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

    return meta
