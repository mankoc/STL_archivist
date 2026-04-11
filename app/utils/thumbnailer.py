"""
Thumbnail storage: save/load JPEG files from a structured directory.
Subdirectories hold at most FILES_PER_DIR files, named by image ID.

Layout:  <data_dir>/thumbnails/
              0000/   ← image IDs 0 – 4 999
              0001/   ← image IDs 5 000 – 9 999
              ...
"""
import io
from pathlib import Path
from typing import Optional

from PIL import Image

THUMB_SIZE_DEFAULT = 512
THUMB_QUALITY = 75
FILES_PER_DIR = 5_000

_base_dir: Optional[Path] = None


def init_thumbnail_store(base_dir) -> None:
    """Call once at startup with the thumbnails root directory."""
    global _base_dir
    _base_dir = Path(base_dir)
    _base_dir.mkdir(parents=True, exist_ok=True)


def thumbnail_rel_path(image_id: int) -> str:
    """Relative path stored in the DB, e.g. '0000/42.jpg'."""
    subfolder = image_id // FILES_PER_DIR
    return f"{subfolder:04d}/{image_id}.jpg"


def _full_path(rel_path: str) -> Optional[Path]:
    if _base_dir is None or not rel_path:
        return None
    return _base_dir / rel_path


def make_thumbnail(image_bytes: bytes, max_size: int = THUMB_SIZE_DEFAULT) -> Optional[bytes]:
    """Convert raw image bytes to resized JPEG bytes. Returns None on failure."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def save_thumbnail(image_bytes: bytes, image_id: int,
                   max_size: int = THUMB_SIZE_DEFAULT) -> Optional[str]:
    """
    Resize image_bytes and write to disk.
    Returns the relative path string (to store in DB), or None on failure.
    """
    if _base_dir is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")
    thumb_bytes = make_thumbnail(image_bytes, max_size)
    if thumb_bytes is None:
        return None
    rel = thumbnail_rel_path(image_id)
    dest = _base_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(thumb_bytes)
    return rel


def write_raw_thumbnail(image_id: int, jpeg_bytes: bytes) -> Optional[str]:
    """
    Write already-processed JPEG bytes to disk without resizing.
    Used by the DB migration to move existing blobs to files.
    Returns the relative path, or None on failure.
    """
    if _base_dir is None:
        return None
    rel = thumbnail_rel_path(image_id)
    dest = _base_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(jpeg_bytes)
        return rel
    except OSError:
        return None


def delete_thumbnail(rel_path: str) -> None:
    """Remove a thumbnail file if it exists. Silently ignores errors."""
    full = _full_path(rel_path)
    if full is None:
        return
    try:
        full.unlink(missing_ok=True)
    except OSError:
        pass


def pixmap_from_path(rel_path: Optional[str]):
    """Load a QPixmap from a stored relative thumbnail path. Returns empty QPixmap on failure."""
    from PyQt6.QtGui import QPixmap
    full = _full_path(rel_path)
    if full is None or not full.exists():
        return QPixmap()
    px = QPixmap()
    px.load(str(full))
    return px


def pixmap_from_bytes(thumbnail_bytes: bytes):
    """Load a QPixmap from raw JPEG bytes (kept for compatibility)."""
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtCore import QByteArray
    if not thumbnail_bytes:
        return QPixmap()
    ba = QByteArray(thumbnail_bytes)
    px = QPixmap()
    px.loadFromData(ba, "JPEG")
    return px
