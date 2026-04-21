"""
Thumbnail storage: LMDB key-value store, keyed by image ID.

Store location: <data_dir>/thumbnails.lmdb/
Keys:           image_id as big-endian 8-byte integer
Values:         JPEG bytes
"""
import io
from pathlib import Path
from typing import Optional

import lmdb
from PIL import Image

THUMB_SIZE_DEFAULT = 512
THUMB_QUALITY = 75

_env: Optional[lmdb.Environment] = None
_lmdb_path: Optional[Path] = None

_MAP_SIZE = 10 * 1024 ** 3  # 10 GB virtual address space (not pre-allocated on Linux)


def _key(image_id: int) -> bytes:
    return image_id.to_bytes(8, "big")


def _parse_image_id(path_or_id: str) -> Optional[int]:
    """Extract image ID from '42' (new) or '0000/42.jpg' (old file-based) format."""
    if not path_or_id:
        return None
    if path_or_id.isdigit():
        return int(path_or_id)
    try:
        return int(Path(path_or_id).stem)
    except (ValueError, TypeError):
        return None


def init_thumbnail_store(base_dir) -> None:
    """Call once at startup with the thumbnails LMDB directory path."""
    global _env, _lmdb_path
    new_path = Path(base_dir)
    if _env is not None and _lmdb_path == new_path:
        return  # already open at this path
    if _env is not None:
        _env.close()
    _lmdb_path = new_path
    _lmdb_path.mkdir(parents=True, exist_ok=True)
    _env = lmdb.open(str(_lmdb_path), map_size=_MAP_SIZE)


def get_thumbnail_base_dir() -> Optional[Path]:
    """Return the LMDB store path, or None if not initialised."""
    return _lmdb_path


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
    Resize image_bytes and write to LMDB.
    Returns the image ID as a string (stored in DB as thumbnail_path), or None on failure.
    """
    if _env is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")
    thumb_bytes = make_thumbnail(image_bytes, max_size)
    if thumb_bytes is None:
        return None
    with _env.begin(write=True) as txn:
        txn.put(_key(image_id), thumb_bytes)
    return str(image_id)


def write_raw_thumbnail(image_id: int, jpeg_bytes: bytes) -> Optional[str]:
    """
    Write already-processed JPEG bytes to LMDB without resizing.
    Used when importing thumbnails from METADATA.7z.
    Returns the image ID as a string, or None on failure.
    """
    if _env is None:
        return None
    try:
        with _env.begin(write=True) as txn:
            txn.put(_key(image_id), jpeg_bytes)
        return str(image_id)
    except lmdb.Error:
        return None


def delete_thumbnail(path_or_id) -> None:
    """
    Remove a thumbnail from LMDB.
    Accepts the new string ID format ('42'), old path format ('0000/42.jpg'), or an int.
    Silently ignores errors.
    """
    if _env is None:
        return
    if isinstance(path_or_id, int):
        image_id = path_or_id
    else:
        image_id = _parse_image_id(str(path_or_id))
    if image_id is None:
        return
    try:
        with _env.begin(write=True) as txn:
            txn.delete(_key(image_id))
    except lmdb.Error:
        pass


def get_thumbnail_bytes(image_id: int) -> Optional[bytes]:
    """Return raw JPEG bytes for the given image ID, or None if not found."""
    if _env is None:
        return None
    with _env.begin() as txn:
        return txn.get(_key(image_id))


def pixmap_from_path(path_or_id: Optional[str]):
    """
    Load a QPixmap for a stored thumbnail.
    Accepts the new string ID format ('42') or old path format ('0000/42.jpg').
    Returns empty QPixmap on failure.
    """
    from PyQt6.QtGui import QPixmap
    image_id = _parse_image_id(path_or_id) if path_or_id else None
    if image_id is None:
        return QPixmap()
    data = get_thumbnail_bytes(image_id)
    if not data:
        return QPixmap()
    px = QPixmap()
    px.loadFromData(data)
    return px
