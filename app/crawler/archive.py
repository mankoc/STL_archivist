"""
Helpers for opening 7z / zip / rar archives and streaming file contents
without full extraction to disk (memory-efficient).
"""
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Generator, Optional

import py7zr
import rarfile


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
            with _open_7z(archive_path) as zf:
                data = zf.read([member_name])
                buf = data.get(member_name)
                if buf is not None:
                    return buf.read()
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
