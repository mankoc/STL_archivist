"""
Helpers for opening 7z / zip / rar archives and streaming file contents
without full extraction to disk (memory-efficient).
"""
import io
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Generator, Optional

import py7zr
import rarfile

# Per-file size caps applied when loading archive members into memory.
# Oversized entries are skipped with a warning instead of being read.
_SIZE_LIMIT_IMAGE = 50 * 1024 * 1024   # 50 MB – images / thumbnails
_SIZE_LIMIT_META  =  1 * 1024 * 1024   #  1 MB – info.json / .url files


def _member_size_limit(name: str) -> int:
    """Return the maximum number of bytes allowed for *name* in memory."""
    if Path(name.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES:
        return _SIZE_LIMIT_IMAGE
    return _SIZE_LIMIT_META


ARCHIVE_SUFFIXES = {".7z", ".zip", ".rar"}

# Image extensions we consider as potential thumbnails
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Slicer / metadata extensions to record but not store
SLICER_SUFFIXES = {".lys", ".chitu", ".ctb", ".photon"}


def is_archive(path: Path) -> bool:
    return path.suffix.lower() in ARCHIVE_SUFFIXES


def _open_zip(path: Path):
    return zipfile.ZipFile(str(path), "r")


def _open_7z(path: Path):
    return py7zr.SevenZipFile(str(path), mode="r")


def _open_rar(path: Path):
    from app.ui.config_dialog import get_unrar_path
    tool = get_unrar_path()
    if tool:
        rarfile.UNRAR_TOOL = tool
    return rarfile.RarFile(str(path), "r")


def list_contents(archive_path: Path) -> list[str]:
    """Return a list of all file names inside the archive."""
    suffix = archive_path.suffix.lower()
    try:
        if suffix == ".zip":
            with _open_zip(archive_path) as zf:
                return zf.namelist()
        elif suffix == ".7z":
            with _open_7z(archive_path) as zf:
                return zf.getnames()
        elif suffix == ".rar":
            with _open_rar(archive_path) as rf:
                return rf.namelist()
    except Exception:
        return []
    return []


def read_file(archive_path: Path, member_name: str) -> Optional[bytes]:
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
            with _open_rar(archive_path) as rf:
                return rf.read(member_name)
    except Exception:
        return None


def _image_suffix(name: str) -> str:
    """Return the lowercase suffix of a member name, normalising Windows backslashes."""
    return Path(name.replace("\\", "/")).suffix.lower()


def iter_images(archive_path: Path) -> Generator[tuple[str, bytes], None, None]:
    """
    Yield (filename, bytes) for every image file inside the archive.
    Opens the archive exactly once; normalises Windows backslash paths.
    """
    suffix = archive_path.suffix.lower()
    try:
        if suffix == ".zip":
            with _open_zip(archive_path) as zf:
                for name in zf.namelist():
                    if _image_suffix(name) in IMAGE_SUFFIXES:
                        try:
                            data = zf.read(name)
                            if data:
                                yield name, data
                        except Exception:
                            continue

        elif suffix == ".7z":
            # py7zr's in-memory read() silently returns empty buffers for
            # solid archives.  Use a RAM-backed temp dir (tmpfs on Linux/WSL)
            # so we never touch the real disk; fall back to default on others.
            _shm = "/dev/shm" if Path("/dev/shm").is_dir() else None
            with tempfile.TemporaryDirectory(dir=_shm) as tmpdir:
                tmp = Path(tmpdir)
                with _open_7z(archive_path) as zf:
                    image_names = [
                        n for n in zf.getnames()
                        if _image_suffix(n) in IMAGE_SUFFIXES
                    ]
                    if image_names:
                        zf.extract(path=tmpdir, targets=image_names)
                for name in image_names:
                    filepath = tmp / Path(name.replace("\\", "/"))
                    if filepath.exists():
                        data = filepath.read_bytes()
                        if data:
                            yield name, data

        elif suffix == ".rar":
            with _open_rar(archive_path) as rf:
                for name in rf.namelist():
                    if _image_suffix(name) in IMAGE_SUFFIXES:
                        try:
                            data = rf.read(name)
                            if data:
                                yield name, data
                        except Exception:
                            continue
    except Exception:
        return


def _is_wanted(name: str) -> bool:
    """True for the only file types we ever extract from an archive."""
    p = Path(name.replace("\\", "/"))
    return (
        p.suffix.lower() in IMAGE_SUFFIXES
        or p.suffix.lower() == ".url"
        or p.name.lower() == "info.json"
    )


def extract_archive_contents(archive_path: Path) -> dict[str, bytes]:
    """
    Open the archive exactly once and return a dict of {member_name: bytes}
    for every image, info.json and .url file found inside.
    All other members are ignored entirely.
    """
    suffix = archive_path.suffix.lower()
    result: dict[str, bytes] = {}
    try:
        if suffix == ".zip":
            with _open_zip(archive_path) as zf:
                for name in zf.namelist():
                    if _is_wanted(name):
                        limit = _member_size_limit(name)
                        file_size = zf.getinfo(name).file_size
                        if file_size > limit:
                            logging.warning(
                                "Skipping oversized member %r (%d B > %d B limit) in %s",
                                name, file_size, limit, archive_path,
                            )
                            continue
                        try:
                            data = zf.read(name)
                            if data:
                                result[name] = data
                        except Exception:
                            continue

        elif suffix == ".7z":
            _shm = "/dev/shm" if Path("/dev/shm").is_dir() else None
            with tempfile.TemporaryDirectory(dir=_shm) as tmpdir:
                tmp = Path(tmpdir)
                with _open_7z(archive_path) as zf:
                    wanted = [n for n in zf.getnames() if _is_wanted(n)]
                    if wanted:
                        zf.extract(path=tmpdir, targets=wanted)
                for name in wanted:
                    filepath = tmp / Path(name.replace("\\", "/"))
                    if filepath.exists():
                        limit = _member_size_limit(name)
                        file_size = filepath.stat().st_size
                        if file_size > limit:
                            logging.warning(
                                "Skipping oversized member %r (%d B > %d B limit) in %s",
                                name, file_size, limit, archive_path,
                            )
                            continue
                        data = filepath.read_bytes()
                        if data:
                            result[name] = data

        elif suffix == ".rar":
            with _open_rar(archive_path) as rf:
                for name in rf.namelist():
                    if _is_wanted(name):
                        limit = _member_size_limit(name)
                        file_size = rf.getinfo(name).file_size
                        if file_size > limit:
                            logging.warning(
                                "Skipping oversized member %r (%d B > %d B limit) in %s",
                                name, file_size, limit, archive_path,
                            )
                            continue
                        try:
                            data = rf.read(name)
                            if data:
                                result[name] = data
                        except Exception:
                            continue
    except Exception:
        pass
    return result


def read_info_json(archive_path: Path) -> Optional[bytes]:
    """Return raw bytes of info.json if present, else None."""
    names = list_contents(archive_path)
    for name in names:
        if Path(name.replace("\\", "/")).name.lower() == "info.json":
            return read_file(archive_path, name)
    return None


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
