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

_lmdb_env: Optional[lmdb.Environment] = None
_lmdb_dir: Optional[Path] = None

# 10 GB virtual address space reserved for the LMDB map.
# On Linux this is *not* pre-allocated; actual disk usage grows on demand.
_MAP_SIZE = 10 * 1024 ** 3


def _key(image_id: int) -> bytes:
    return image_id.to_bytes(8, "big")


def _parse_image_id(path_or_id: str) -> Optional[int]:
    """Extract an image ID from a stored thumbnail reference.

    Accepts the current string-ID format (``"42"``) and the legacy
    file-based format (``"0000/42.jpg"``).

    Args:
        path_or_id: The value stored in ``Image.thumbnail_path``.

    Returns:
        Integer image ID, or ``None`` if the string cannot be parsed.
    """
    if not path_or_id:
        return None
    if path_or_id.isdigit():
        return int(path_or_id)
    try:
        return int(Path(path_or_id).stem)
    except (ValueError, TypeError):
        return None


def init_thumbnail_store(base_dir: Path) -> None:
    """Open (or re-open) the LMDB thumbnail store at *base_dir*.

    Safe to call on every startup — it is a no-op when the store is already
    open at the same path.  Closes the previous environment first when the
    path changes (e.g. after the user switches data directories).

    Args:
        base_dir: Directory that will contain the LMDB data and lock files.
            Created automatically if it does not exist.
    """
    global _lmdb_env, _lmdb_dir
    new_path = Path(base_dir)
    if _lmdb_env is not None and _lmdb_dir == new_path:
        return
    if _lmdb_env is not None:
        _lmdb_env.close()
    _lmdb_dir = new_path
    _lmdb_dir.mkdir(parents=True, exist_ok=True)
    _lmdb_env = lmdb.open(str(_lmdb_dir), map_size=_MAP_SIZE)


def get_thumbnail_base_dir() -> Optional[Path]:
    """Return the LMDB store directory, or ``None`` if the store is not open."""
    return _lmdb_dir


def make_thumbnail(image_bytes: bytes, max_size: int = THUMB_SIZE_DEFAULT) -> Optional[bytes]:
    """Convert raw image bytes to resized JPEG bytes.

    Args:
        image_bytes: Raw image data in any format supported by Pillow.
        max_size: Maximum dimension (width *or* height) of the output thumbnail.

    Returns:
        JPEG-encoded bytes, or ``None`` if conversion fails for any reason.
    """
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
    """Resize *image_bytes* and write the result to LMDB under *image_id*.

    Args:
        image_bytes: Raw image data to convert.
        image_id: Primary key from the ``Image`` DB record — used as the LMDB key.
        max_size: Maximum thumbnail dimension in pixels.

    Returns:
        The image ID as a string (stored in ``Image.thumbnail_path``), or
        ``None`` if thumbnail conversion fails.

    Raises:
        RuntimeError: If ``init_thumbnail_store()`` has not been called yet.
    """
    if _lmdb_env is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")
    thumb_bytes = make_thumbnail(image_bytes, max_size)
    if thumb_bytes is None:
        return None
    with _lmdb_env.begin(write=True) as txn:
        txn.put(_key(image_id), thumb_bytes)
    return str(image_id)


def save_thumbnail_returning_bytes(
    image_bytes: bytes,
    image_id: int,
    max_size: int = THUMB_SIZE_DEFAULT,
) -> "Optional[tuple[str, bytes]]":
    """Like :func:`save_thumbnail` but also returns the stored JPEG bytes.

    Avoids the extra LMDB read that callers need when computing dHash
    immediately after saving.

    Returns:
        ``(rel_path, jpeg_bytes)`` on success, or ``None`` on failure.
    """
    if _lmdb_env is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")
    thumb_bytes = make_thumbnail(image_bytes, max_size)
    if thumb_bytes is None:
        return None
    with _lmdb_env.begin(write=True) as txn:
        txn.put(_key(image_id), thumb_bytes)
    return str(image_id), thumb_bytes


def save_thumbnails_batch(
    items: "list[tuple[int, bytes, int]]",
) -> "list[tuple[int, Optional[str], Optional[bytes]]]":
    """Resize and write multiple thumbnails in a single LMDB write transaction.

    Args:
        items: List of ``(image_id, raw_bytes, max_size)`` triples.

    Returns:
        List of ``(image_id, rel_path, jpeg_bytes)`` in the same order.
        ``rel_path`` and ``jpeg_bytes`` are ``None`` when conversion failed.
    """
    if _lmdb_env is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")
    results: list[tuple[int, Optional[str], Optional[bytes]]] = []
    prepared: list[tuple[int, bytes]] = []
    for image_id, raw_bytes, max_size in items:
        thumb = make_thumbnail(raw_bytes, max_size)
        if thumb is None:
            results.append((image_id, None, None))
        else:
            prepared.append((image_id, thumb))
            results.append((image_id, str(image_id), thumb))
    if prepared:
        with _lmdb_env.begin(write=True) as txn:
            for image_id, thumb in prepared:
                txn.put(_key(image_id), thumb)
    return results


def write_raw_thumbnail(image_id: int, jpeg_bytes: bytes) -> Optional[str]:
    """Write already-processed JPEG bytes to LMDB without resizing.

    Used when importing thumbnails from a ``METADATA.7z`` sidecar where the
    images are already JPEG-encoded at the correct size.

    Args:
        image_id: Primary key of the ``Image`` DB record.
        jpeg_bytes: Pre-encoded JPEG data.

    Returns:
        The image ID as a string, or ``None`` on any LMDB error or if the
        store is not initialised.
    """
    if _lmdb_env is None:
        return None
    try:
        with _lmdb_env.begin(write=True) as txn:
            txn.put(_key(image_id), jpeg_bytes)
        return str(image_id)
    except lmdb.Error:
        return None


def delete_thumbnail(path_or_id: object) -> None:
    """Remove a thumbnail from LMDB.

    Accepts the current string-ID format (``"42"``), the legacy path format
    (``"0000/42.jpg"``), or a plain ``int``.  Silently ignores errors including
    a missing key — the operation is idempotent.

    Args:
        path_or_id: Thumbnail reference as stored in ``Image.thumbnail_path``,
            or a plain integer image ID.
    """
    if _lmdb_env is None:
        return
    if isinstance(path_or_id, int):
        image_id = path_or_id
    else:
        image_id = _parse_image_id(str(path_or_id))
    if image_id is None:
        return
    try:
        with _lmdb_env.begin(write=True) as txn:
            txn.delete(_key(image_id))
    except lmdb.Error:
        pass


def get_thumbnail_bytes(image_id: int) -> Optional[bytes]:
    """Return raw JPEG bytes for the given *image_id*, or ``None`` if not found."""
    if _lmdb_env is None:
        return None
    with _lmdb_env.begin() as txn:
        return txn.get(_key(image_id))


def get_thumbnail_bytes_many(image_ids) -> "dict[int, bytes]":
    """Return a mapping of image_id → JPEG bytes for all *image_ids* in one LMDB txn.

    Missing keys are omitted from the result.  Safe to call from any thread.
    """
    if _lmdb_env is None:
        return {}
    result: dict[int, bytes] = {}
    with _lmdb_env.begin() as txn:
        cursor = txn.cursor()
        for image_id in image_ids:
            data = cursor.get(_key(image_id))
            if data is not None:
                result[image_id] = data
    return result


def pixmap_from_path(path_or_id: Optional[str]):
    """Load a QPixmap for a stored thumbnail.

    Accepts the current string-ID format (``"42"``) or the legacy file-based
    format (``"0000/42.jpg"``).

    Args:
        path_or_id: Value stored in ``Image.thumbnail_path``, or ``None``.

    Returns:
        Loaded ``QPixmap``, or an empty ``QPixmap`` when the thumbnail cannot
        be found or parsed.
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
