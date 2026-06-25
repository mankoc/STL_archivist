"""
Thumbnail storage: LMDB key-value store, keyed by image ID.

Store location: <data_dir>/thumbnails.lmdb/
Keys:           image_id as big-endian 8-byte integer
Values:         JPEG bytes
"""
import io
import os
import shutil
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
# This is only the *initial* ceiling — _write() grows the map automatically
# when a write hits MDB_MAP_FULL, so the store is not capped at this size.
_MAP_SIZE = 10 * 1024 ** 3

# Amount to enlarge the LMDB map by each time a write fills it up.
_GROW_STEP = 2 * 1024 ** 3


def _key(image_id: int) -> bytes:
    return image_id.to_bytes(8, "big")


def _write(put_fn) -> None:
    """Run *put_fn(txn)* in a write transaction, growing the map on MDB_MAP_FULL.

    LMDB has a fixed map size; once the data reaches it, every write raises
    :class:`lmdb.MapFullError`.  We catch that, enlarge the map via
    ``set_mapsize`` (safe with no open transactions — the failed one has already
    aborted), and retry until the write succeeds.
    """
    while True:
        try:
            with _lmdb_env.begin(write=True) as txn:
                put_fn(txn)
            return
        except lmdb.MapFullError:
            new_size = _lmdb_env.info()["map_size"] + _GROW_STEP
            _lmdb_env.set_mapsize(new_size)


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
    # Open with a map at least as large as the data already on disk (plus a
    # grow-step of headroom), so a store that previously grew past _MAP_SIZE
    # reopens without immediately hitting MDB_MAP_FULL on the first write.
    data_file = _lmdb_dir / "data.mdb"
    existing = data_file.stat().st_size if data_file.exists() else 0
    map_size = max(_MAP_SIZE, existing + _GROW_STEP)
    _lmdb_env = lmdb.open(str(_lmdb_dir), map_size=map_size)


def get_thumbnail_base_dir() -> Optional[Path]:
    """Return the LMDB store directory, or ``None`` if the store is not open."""
    return _lmdb_dir


def store_size_bytes() -> int:
    """Return the on-disk size of the LMDB data file, or 0 if not present."""
    if _lmdb_dir is None:
        return 0
    data_file = _lmdb_dir / "data.mdb"
    return data_file.stat().st_size if data_file.exists() else 0


def compact_store() -> "tuple[int, int]":
    """Rewrite the thumbnail store defragmented, reclaiming space from deletions.

    LMDB never shrinks ``data.mdb`` on its own — deleted pages are reused but the
    file stays at its high-water mark. This writes a compacted copy and swaps it
    in, so the file shrinks to the size actually needed.

    Must not be called while other threads hold open transactions on the store.

    Returns:
        ``(size_before, size_after)`` in bytes.

    Raises:
        RuntimeError: If the store has not been initialised.
    """
    global _lmdb_env
    if _lmdb_env is None or _lmdb_dir is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")

    size_before = store_size_bytes()

    tmp_dir = _lmdb_dir.parent / (_lmdb_dir.name + ".compact_tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Write a defragmented copy, then close, swap the data file in, and reopen.
    _lmdb_env.copy(str(tmp_dir), compact=True)
    _lmdb_env.close()
    _lmdb_env = None

    os.replace(str(tmp_dir / "data.mdb"), str(_lmdb_dir / "data.mdb"))
    shutil.rmtree(tmp_dir, ignore_errors=True)

    init_thumbnail_store(_lmdb_dir)
    return size_before, store_size_bytes()


def pdf_first_page_to_image_bytes(pdf_bytes: bytes) -> Optional[bytes]:
    """Render the first page of a PDF to raw PNG bytes using PyMuPDF.

    Returns ``None`` when PyMuPDF is unavailable or the PDF cannot be opened.
    """
    try:
        import fitz  # pymupdf
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0:
            return None
        page = doc.load_page(0)
        mat = fitz.Matrix(2.0, 2.0)  # 2× zoom for a crisp thumbnail source
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")
    except Exception:
        return None


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
    _write(lambda txn: txn.put(_key(image_id), thumb_bytes))
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
    _write(lambda txn: txn.put(_key(image_id), thumb_bytes))
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
        def _put(txn):
            for image_id, thumb in prepared:
                txn.put(_key(image_id), thumb)
        _write(_put)
    return results


def write_raw_thumbnails_batch(items: "list[tuple[int, bytes]]") -> None:
    """Write already-processed JPEG bytes for multiple images in one LMDB transaction."""
    if _lmdb_env is None or not items:
        return
    def _put(txn):
        for image_id, jpeg_bytes in items:
            if jpeg_bytes:
                txn.put(_key(image_id), jpeg_bytes)
    try:
        _write(_put)
    except lmdb.Error:
        pass


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
        _write(lambda txn: txn.put(_key(image_id), jpeg_bytes))
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
