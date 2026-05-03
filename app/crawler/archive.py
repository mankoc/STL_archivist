"""
Helpers for opening 7z / zip / rar archives and streaming file contents
without full extraction to disk (memory-efficient).

The public API (``archive_hash``, ``extract_archive_contents``, etc.) is
unchanged; internally all format-specific code is isolated in three
``ArchiveReader`` subclasses so the top-level functions contain no
format dispatch.
"""
import logging
import tempfile
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import py7zr
import rarfile

from app.constants import (
    ARCHIVE_SUFFIXES,
    IMAGE_SUFFIXES,
    METADATA_FILENAME,
    SLICER_SUFFIXES,
)

logger = logging.getLogger(__name__)

# Re-exports for callers that import from app.crawler.archive (kept for
# backwards compatibility — new code should import from app.constants).
__all__ = [
    "ARCHIVE_SUFFIXES", "IMAGE_SUFFIXES", "METADATA_FILENAME", "SLICER_SUFFIXES",
    "is_archive", "archive_hash", "iter_archives", "list_contents", "read_file",
    "extract_archive_contents", "read_url_file", "find_url_file",
]

# Per-file size caps applied when loading archive members into memory.
# Oversized entries are skipped with a warning instead of being read.
_SIZE_LIMIT_IMAGE = 50 * 1024 * 1024   # 50 MB – images / thumbnails
_SIZE_LIMIT_META  =  1 * 1024 * 1024   #  1 MB – info.json / .url files

# Exceptions that indicate a corrupt or unreadable archive (expected errors).
_ARCHIVE_ERRORS = (
    zipfile.BadZipFile,
    py7zr.Bad7zFile,
    rarfile.BadRarFile,
    rarfile.NotRarFile,
    rarfile.Error,
    OSError,
)


# ---------------------------------------------------------------------------
# Reader abstraction
# ---------------------------------------------------------------------------

@dataclass
class MemberInfo:
    """Catalog entry for a single archive member."""
    name: str
    crc: int
    size: int | None   # uncompressed size in bytes; None when not in catalog


class ArchiveReader(ABC):
    """Unified context-manager interface over ZIP, 7z, and RAR archives."""

    def __enter__(self) -> "ArchiveReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @abstractmethod
    def iter_members(self) -> Iterator[MemberInfo]:
        """Yield one :class:`MemberInfo` per archive member (catalog-only, no decompression)."""

    @abstractmethod
    def read_batch(self, names: list[str]) -> dict[str, bytes]:
        """Decompress and return the named members as ``{name: bytes}``.

        Members that cannot be read are silently omitted.
        """

    @abstractmethod
    def close(self) -> None:
        """Release the underlying file handle."""


class _ZipReader(ArchiveReader):
    def __init__(self, path: Path) -> None:
        self._zf = zipfile.ZipFile(str(path), "r")

    def iter_members(self) -> Iterator[MemberInfo]:
        for info in self._zf.infolist():
            yield MemberInfo(
                name=info.filename,
                crc=info.CRC & 0xFFFFFFFF,
                size=info.file_size,
            )

    def read_batch(self, names: list[str]) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for name in names:
            try:
                data = self._zf.read(name)
                if data:
                    result[name] = data
                    logger.debug("  Read %r (%d B)", name, len(data))
            except Exception as exc:
                logger.warning("Failed to read member %r: %s", name, exc)
        return result

    def close(self) -> None:
        self._zf.close()


class _SevenZReader(ArchiveReader):
    def __init__(self, path: Path) -> None:
        self._path = path
        self._zf = py7zr.SevenZipFile(str(path), mode="r")
        self._members_cache: list[MemberInfo] | None = None

    def iter_members(self) -> Iterator[MemberInfo]:
        if self._members_cache is None:
            self._members_cache = [
                MemberInfo(
                    name=f.filename,
                    crc=f.crc32 or 0,
                    size=getattr(f, "uncompressed", None),
                )
                for f in self._zf.list()
            ]
        yield from self._members_cache

    def read_batch(self, names: list[str]) -> dict[str, bytes]:
        if not names:
            return {}
        _shm = "/dev/shm" if Path("/dev/shm").is_dir() else None
        result: dict[str, bytes] = {}
        with tempfile.TemporaryDirectory(dir=_shm) as tmpdir:
            tmp = Path(tmpdir)
            self._zf.extract(path=tmpdir, targets=names)
            for filepath in sorted(tmp.rglob("*")):
                if not filepath.is_file():
                    continue
                name = filepath.relative_to(tmp).as_posix()
                data = filepath.read_bytes()
                if data:
                    result[name] = data
                    logger.debug("  Read %r (%d B)", name, len(data))
        return result

    def close(self) -> None:
        self._zf.close()


class _RarReader(ArchiveReader):
    def __init__(self, path: Path, unrar_path: str = "") -> None:
        if unrar_path:
            rarfile.UNRAR_TOOL = unrar_path
        self._rf = rarfile.RarFile(str(path), "r")

    def iter_members(self) -> Iterator[MemberInfo]:
        for info in self._rf.infolist():
            if info.is_dir():
                continue
            yield MemberInfo(
                name=info.filename,
                crc=getattr(info, "CRC", 0) or 0,
                size=getattr(info, "file_size", None),
            )

    def read_batch(self, names: list[str]) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for name in names:
            try:
                data = self._rf.read(name)
                if data:
                    result[name] = data
                    logger.debug("  Read %r (%d B)", name, len(data))
            except Exception as exc:
                logger.warning("Failed to read member %r: %s", name, exc)
        return result

    def close(self) -> None:
        self._rf.close()


def open_archive(path: Path, unrar_path: str = "") -> ArchiveReader:
    """Open *path* and return the appropriate :class:`ArchiveReader`.

    Raises :exc:`ValueError` for unrecognised extensions.  Archive-format
    errors (``BadZipFile``, ``Bad7zFile``, etc.) propagate to the caller.
    """
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return _ZipReader(path)
    elif suffix == ".7z":
        return _SevenZReader(path)
    elif suffix == ".rar":
        return _RarReader(path, unrar_path)
    raise ValueError(f"Unsupported archive format: {path.suffix!r}")


# ---------------------------------------------------------------------------
# Public utilities
# ---------------------------------------------------------------------------

def _member_size_limit(name: str) -> int:
    """Return the maximum number of bytes we will load for *name* into memory.

    Caps are applied as a safety guard against accidentally extracting very large
    STL binary files that happen to share an image extension, or malformed/malicious
    archives with inflated member sizes.  Images get a generous 50 MB budget;
    metadata files (``info.json``, ``.url``) get 1 MB.
    """
    if Path(name.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES:
        return _SIZE_LIMIT_IMAGE
    return _SIZE_LIMIT_META


def is_archive(path: Path) -> bool:
    """Return ``True`` if *path* has a recognised archive extension."""
    return path.suffix.lower() in ARCHIVE_SUFFIXES


def archive_hash(path: Path, unrar_path: str = "") -> str:
    """
    Derive a stable fingerprint from the CRC values already stored in the
    archive's internal catalog — no member data is read or decompressed.

    Members are sorted by name before hashing so the result is independent
    of insertion order.  Falls back to a size+mtime fingerprint when the
    archive cannot be opened (e.g. truncated file).
    """
    import xxhash
    logger.debug("Computing hash for: %s", path.name)
    try:
        with open_archive(path, unrar_path) as ar:
            members = sorted(
                (m.name, m.crc) for m in ar.iter_members()
            )
    except _ARCHIVE_ERRORS as exc:
        stat = path.stat()
        fingerprint = f"fallback:{stat.st_size}:{stat.st_mtime_ns}"
        logger.warning(
            "Could not open %s for hashing — using fallback fingerprint (%s): %s",
            path.name, fingerprint, exc,
        )
        return fingerprint

    h = xxhash.xxh64()
    for name, crc in members:
        h.update(name.encode("utf-8", errors="replace"))
        h.update(crc.to_bytes(4, "little"))
    digest = h.hexdigest()
    logger.debug("Hash for %s: %s… (%d member(s))", path.name, digest[:12], len(members))
    return digest


def iter_archives(root: Path):
    """Yield every archive file under *root*, filtering on the fly.

    Using a generator avoids materialising non-archive paths into memory and
    lets callers begin processing the first archive before the full tree scan
    completes.

    METADATA.7z sidecar files are intentionally excluded so they are never
    treated as STL archives to be crawled.
    """
    for p in root.rglob("*"):
        if p.suffix.lower() in ARCHIVE_SUFFIXES and p.name != METADATA_FILENAME:
            yield p


def list_contents(archive_path: Path, unrar_path: str = "") -> list[str]:
    """Return a list of all file names inside the archive."""
    logger.debug("Listing contents of: %s", archive_path.name)
    try:
        with open_archive(archive_path, unrar_path) as ar:
            names = [m.name for m in ar.iter_members()]
        logger.debug("  %d member(s) in %s", len(names), archive_path.name)
        return names
    except PermissionError as exc:
        logger.warning("Permission denied reading archive %s: %s", archive_path.name, exc)
        return []
    except (ValueError, *_ARCHIVE_ERRORS) as exc:
        logger.error("Failed to list contents of %s: %s", archive_path.name, exc, exc_info=True)
        return []


def read_file(archive_path: Path, member_name: str, unrar_path: str = "") -> Optional[bytes]:
    """Read a single member from the archive and return its bytes."""
    try:
        with open_archive(archive_path, unrar_path) as ar:
            return ar.read_batch([member_name]).get(member_name)
    except Exception:
        return None


def _image_suffix(name: str) -> str:
    """Return the lowercase suffix of a member name, normalising Windows backslashes."""
    return Path(name.replace("\\", "/")).suffix.lower()


def _is_wanted(name: str) -> bool:
    """True for the only file types we ever extract from an archive."""
    p = Path(name.replace("\\", "/"))
    return (
        p.suffix.lower() in IMAGE_SUFFIXES
        or p.suffix.lower() == ".url"
        or p.name.lower() == "info.json"
    )


def extract_archive_contents(
    archive_path: Path,
    unrar_path: str = "",
    all_names_out: Optional[list] = None,
    stl_info_out: Optional[dict] = None,
) -> dict[str, bytes]:
    """
    Open the archive exactly once and return a dict of {member_name: bytes}
    for every image, info.json and .url file found inside.
    All other members are ignored entirely.

    When *all_names_out* is a list, it will be populated with every member
    name read from the archive's catalog.  Callers that also need the full
    member list (e.g. for rule/warning detection) can pass a list here to
    avoid opening the archive a second time.

    When *stl_info_out* is a dict, it will be populated with
    ``{member_name: uncompressed_size_bytes}`` for every ``.stl`` member
    found in the archive catalog.  Size may be ``None`` for formats where
    it is not available in the catalog header.
    """
    result: dict[str, bytes] = {}
    logger.debug("Extracting wanted files from: %s", archive_path.name)
    try:
        with open_archive(archive_path, unrar_path) as ar:
            all_members = list(ar.iter_members())
            all_names   = [m.name for m in all_members]

            if all_names_out is not None:
                all_names_out.extend(all_names)

            if stl_info_out is not None:
                for m in all_members:
                    if Path(m.name.replace("\\", "/")).suffix.lower() == ".stl":
                        stl_info_out[m.name] = m.size

            size_by_name = {m.name: m.size for m in all_members}
            wanted = [n for n in all_names if _is_wanted(n)]
            logger.debug("  %d total members, %d wanted", len(all_names), len(wanted))

            # Apply size limits before extraction where catalog size is known.
            to_read: list[str] = []
            for name in wanted:
                limit     = _member_size_limit(name)
                file_size = size_by_name.get(name)
                if file_size is not None and file_size > limit:
                    logger.warning(
                        "Skipping oversized member %r (%d B > %d B limit) in %s",
                        name, file_size, limit, archive_path,
                    )
                    continue
                to_read.append(name)

            batch = ar.read_batch(to_read)

            # Secondary size check for formats where catalog size was unavailable.
            for name, data in batch.items():
                limit = _member_size_limit(name)
                if len(data) > limit:
                    logger.warning(
                        "Skipping oversized member %r (%d B > %d B limit) in %s",
                        name, len(data), limit, archive_path,
                    )
                    continue
                result[name] = data

    except PermissionError as exc:
        logger.warning("Permission denied reading archive %s: %s", archive_path, exc)
    except (ValueError, *_ARCHIVE_ERRORS) as exc:
        logger.error("Failed to open archive %s: %s", archive_path, exc, exc_info=True)

    logger.debug("Extraction complete for %s — %d file(s) loaded", archive_path.name, len(result))
    return result


def read_url_file(archive_path: Path) -> Optional[str]:
    """Return the URL from a .url file inside the archive, if present."""
    names = list_contents(archive_path)
    for name in names:
        if Path(name.replace("\\", "/")).suffix.lower() == ".url":
            data = read_file(archive_path, name)
            if data:
                for line in data.decode("utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line.lower().startswith("url="):
                        return line[4:].strip()
    return None


def _parse_url_shortcut(path: Path) -> Optional[str]:
    """Parse a Windows Internet Shortcut (.url) file and return the URL."""
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.lower().startswith("url="):
                return line[4:].strip()
    except Exception:
        pass
    return None


def find_url_file(archive_path: Path) -> Optional[str]:
    """
    Search for a .url file for the given archive in this priority order:
      1. Inside the archive itself
      2. Same directory as the archive
      3. Subfolders of the archive's directory
      4. One level above the archive
      5. Subfolders of the directory one level above the archive
    Returns the first URL found, or None.
    """
    # 1. Inside the archive
    url = read_url_file(archive_path)
    if url:
        return url

    parent = archive_path.parent
    grandparent = parent.parent

    # 2. Same directory as archive
    for f in parent.glob("*.url"):
        url = _parse_url_shortcut(f)
        if url:
            return url

    # 3. Subfolders of archive's directory
    for f in parent.glob("*/*.url"):
        url = _parse_url_shortcut(f)
        if url:
            return url

    # 4. One level above the archive
    for f in grandparent.glob("*.url"):
        url = _parse_url_shortcut(f)
        if url:
            return url

    # 5. Subfolders of the directory one level above
    for f in grandparent.glob("*/*.url"):
        url = _parse_url_shortcut(f)
        if url:
            return url

    return None
