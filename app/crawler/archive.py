"""
Helpers for opening 7z / zip / rar archives and streaming file contents
without full extraction to disk (memory-efficient).
"""
import io
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

import py7zr
import rarfile

logger = logging.getLogger(__name__)

# Per-file size caps applied when loading archive members into memory.
# Oversized entries are skipped with a warning instead of being read.
_SIZE_LIMIT_IMAGE = 50 * 1024 * 1024   # 50 MB – images / thumbnails
_SIZE_LIMIT_META  =  1 * 1024 * 1024   #  1 MB – info.json / .url files


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


ARCHIVE_SUFFIXES = {".7z", ".zip", ".rar"}

# Name of the per-folder metadata sidecar — never treated as an STL archive
METADATA_FILENAME = "METADATA.7z"

# Image extensions we consider as potential thumbnails
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Slicer / metadata extensions to record but not store
SLICER_SUFFIXES = {".lys", ".chitu", ".ctb", ".photon"}


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
    suffix = path.suffix.lower()
    members: list[tuple[str, int]] = []
    logger.debug("Computing hash for: %s", path.name)
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(str(path), "r") as zf:
                members = sorted(
                    (info.filename, info.CRC & 0xFFFFFFFF)
                    for info in zf.infolist()
                )
        elif suffix == ".7z":
            with py7zr.SevenZipFile(str(path), mode="r") as zf:
                members = sorted(
                    (f.filename, f.crc32 or 0)
                    for f in zf.list()
                )
        elif suffix == ".rar":
            if unrar_path:
                rarfile.UNRAR_TOOL = unrar_path
            with rarfile.RarFile(str(path), "r") as rf:
                members = sorted(
                    (info.filename, getattr(info, "CRC", 0) or 0)
                    for info in rf.infolist()
                    if not info.is_dir()
                )
    except Exception as exc:
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


def _open_zip(path: Path):
    return zipfile.ZipFile(str(path), "r")


def _open_7z(path: Path):
    return py7zr.SevenZipFile(str(path), mode="r")


def _open_rar(path: Path, unrar_path: str = ""):
    if unrar_path:
        rarfile.UNRAR_TOOL = unrar_path
    return rarfile.RarFile(str(path), "r")


def list_contents(archive_path: Path, unrar_path: str = "") -> list[str]:
    """Return a list of all file names inside the archive."""
    suffix = archive_path.suffix.lower()
    logger.debug("Listing contents of: %s", archive_path.name)
    try:
        if suffix == ".zip":
            with _open_zip(archive_path) as zf:
                names = zf.namelist()
        elif suffix == ".7z":
            with _open_7z(archive_path) as zf:
                names = zf.getnames()
        elif suffix == ".rar":
            with _open_rar(archive_path, unrar_path) as rf:
                names = rf.namelist()
        else:
            return []
        logger.debug("  %d member(s) in %s", len(names), archive_path.name)
        return names
    except PermissionError as exc:
        logger.warning("Permission denied reading archive %s: %s", archive_path.name, exc)
        return []
    except Exception as exc:
        logger.error("Failed to list contents of %s: %s", archive_path.name, exc, exc_info=True)
        return []


def read_file(archive_path: Path, member_name: str, unrar_path: str = "") -> Optional[bytes]:
    """Read a single member from the archive and return its bytes."""
    suffix = archive_path.suffix.lower()
    try:
        if suffix == ".zip":
            with _open_zip(archive_path) as zf:
                return zf.read(member_name)
        elif suffix == ".7z":
            _shm = "/dev/shm" if Path("/dev/shm").is_dir() else None
            with tempfile.TemporaryDirectory(dir=_shm) as tmpdir:
                tmp = Path(tmpdir)
                with _open_7z(archive_path) as zf:
                    zf.extract(path=tmpdir, targets=[member_name])
                extracted = tmp / Path(member_name.replace("\\", "/"))
                if extracted.exists():
                    return extracted.read_bytes()
            return None
        elif suffix == ".rar":
            with _open_rar(archive_path, unrar_path) as rf:
                return rf.read(member_name)
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
) -> dict[str, bytes]:
    """
    Open the archive exactly once and return a dict of {member_name: bytes}
    for every image, info.json and .url file found inside.
    All other members are ignored entirely.

    When *all_names_out* is a list, it will be populated with every member
    name read from the archive's catalog.  Callers that also need the full
    member list (e.g. for rule/warning detection) can pass a list here to
    avoid opening the archive a second time.
    """
    suffix = archive_path.suffix.lower()
    result: dict[str, bytes] = {}
    logger.debug("Extracting wanted files from: %s (type: %s)", archive_path.name, suffix)
    try:
        if suffix == ".zip":
            with _open_zip(archive_path) as zf:
                all_names = zf.namelist()
                if all_names_out is not None:
                    all_names_out.extend(all_names)
                wanted = [n for n in all_names if _is_wanted(n)]
                logger.debug("  ZIP: %d total members, %d wanted", len(all_names), len(wanted))
                for name in wanted:
                    limit = _member_size_limit(name)
                    file_size = zf.getinfo(name).file_size
                    if file_size > limit:
                        logger.warning(
                            "Skipping oversized member %r (%d B > %d B limit) in %s",
                            name, file_size, limit, archive_path,
                        )
                        continue
                    try:
                        data = zf.read(name)
                        if data:
                            result[name] = data
                            logger.debug("  Read %r (%d B)", name, len(data))
                    except Exception as exc:
                        logger.warning("Failed to read member %r in %s: %s",
                                       name, archive_path.name, exc)
                        continue

        elif suffix == ".7z":
            _shm = "/dev/shm" if Path("/dev/shm").is_dir() else None
            with tempfile.TemporaryDirectory(dir=_shm) as tmpdir:
                tmp = Path(tmpdir)
                with _open_7z(archive_path) as zf:
                    all_names = zf.getnames()
                    if all_names_out is not None:
                        all_names_out.extend(all_names)
                    wanted = [n for n in all_names if _is_wanted(n)]
                    logger.debug("  7Z: %d total members, %d wanted", len(all_names), len(wanted))
                    if wanted:
                        zf.extract(path=tmpdir, targets=wanted)
                # Walk the extracted tree so files at any subfolder depth are found,
                # regardless of how the archive member names were encoded.
                for filepath in sorted(tmp.rglob("*")):
                    if not filepath.is_file():
                        continue
                    name = filepath.relative_to(tmp).as_posix()
                    if not _is_wanted(name):
                        continue
                    limit = _member_size_limit(name)
                    file_size = filepath.stat().st_size
                    if file_size > limit:
                        logger.warning(
                            "Skipping oversized member %r (%d B > %d B limit) in %s",
                            name, file_size, limit, archive_path,
                        )
                        continue
                    data = filepath.read_bytes()
                    if data:
                        result[name] = data
                        logger.debug("  Read %r (%d B)", name, len(data))

        elif suffix == ".rar":
            with _open_rar(archive_path, unrar_path) as rf:
                all_names = rf.namelist()
                if all_names_out is not None:
                    all_names_out.extend(all_names)
                wanted = [n for n in all_names if _is_wanted(n)]
                logger.debug("  RAR: %d total members, %d wanted", len(all_names), len(wanted))
                for name in wanted:
                    limit = _member_size_limit(name)
                    file_size = rf.getinfo(name).file_size
                    if file_size > limit:
                        logger.warning(
                            "Skipping oversized member %r (%d B > %d B limit) in %s",
                            name, file_size, limit, archive_path,
                        )
                        continue
                    try:
                        data = rf.read(name)
                        if data:
                            result[name] = data
                            logger.debug("  Read %r (%d B)", name, len(data))
                    except Exception as exc:
                        logger.warning("Failed to read member %r in %s: %s",
                                       name, archive_path.name, exc)
                        continue

    except PermissionError as exc:
        logger.warning("Permission denied reading archive %s: %s", archive_path, exc)
    except Exception as exc:
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
