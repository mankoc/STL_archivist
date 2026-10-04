"""
Thumbnail storage: content-addressed LMDB key-value store.

Store location: <data_dir>/thumbnails.lmdb/

Identical thumbnails are held once and shared: an image id maps to a blob key
derived from the thumbnail's content hash, and the bytes live under that blob
key.  See the key-layout comment below.  The public API is still addressed by
``Image.id`` throughout, so callers never see the indirection.
"""
import io
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import threading
import time

import lmdb
import xxhash
from PIL import Image, ImageSequence

log = logging.getLogger(__name__)

THUMB_SIZE_DEFAULT = 512
THUMB_QUALITY = 75

# Animated-GIF thumbnails are stored as GIF rather than JPEG so the preview pane
# can play them.  Both caps below are safety valves: a long or high-frame-rate
# source would otherwise put megabytes per image into LMDB and leave the GUI
# thread decoding frames forever.  Exceeding either one falls back to a static
# first-frame JPEG, which is what every pre-existing thumbnail already is.
GIF_MAX_FRAMES = 60
GIF_MAX_BYTES = 2 * 1024 ** 2

_lmdb_env: Optional[lmdb.Environment] = None
_lmdb_dir: Optional[Path] = None

# 10 GB virtual address space reserved for the LMDB map.
# On Linux this is *not* pre-allocated; actual disk usage grows on demand.
# This is only the *initial* ceiling — _write() grows the map automatically
# when a write hits MDB_MAP_FULL, so the store is not capped at this size.
_MAP_SIZE = 10 * 1024 ** 3

# Amount to enlarge the LMDB map by each time a write fills it up.
_GROW_STEP = 2 * 1024 ** 3


# ── Key layout ───────────────────────────────────────────────────────────────
# The store is content-addressed so identical thumbnails are held once:
#
#   R<image_id:8>   -> the blob key that image's thumbnail lives under
#   D<digest:16>    -> the JPEG/GIF bytes themselves
#
# Both prefixes are ASCII letters, while a legacy key is a bare big-endian
# image id whose first byte is 0x00 for every id below 2**56 — so old and new
# keys cannot be confused, and a part-migrated store still reads correctly.
_REF_PREFIX  = b"R"
_BLOB_PREFIX = b"D"


def _key(image_id: int) -> bytes:
    """Legacy key: the bare image id, as written before content addressing."""
    return image_id.to_bytes(8, "big")


def _ref_key(image_id: int) -> bytes:
    return _REF_PREFIX + image_id.to_bytes(8, "big")


def _blob_key(digest: bytes, slot: int = 0) -> bytes:
    """Key for a blob's bytes.  *slot* disambiguates a hash collision."""
    return _BLOB_PREFIX + digest + (bytes([slot]) if slot else b"")


def _put_blob(txn, data: bytes) -> bytes:
    """Store *data* if new and return the blob key holding it.

    xxh3-128 is fast but not collision-resistant by construction, so a hash hit
    is confirmed by comparing the bytes.  On the (astronomically unlikely) event
    of a genuine collision the new blob goes to the next free slot rather than
    silently overwriting an unrelated thumbnail.
    """
    digest = xxhash.xxh3_128_digest(data)
    for slot in range(256):
        key = _blob_key(digest, slot)
        existing = txn.get(key)
        if existing is None:
            txn.put(key, data)
            return key
        if existing == data:
            return key
        log.warning("xxh3-128 collision on thumbnail digest %s slot %d",
                    digest.hex(), slot)
    # 256 distinct payloads sharing one digest: store uncollapsed rather than fail.
    key = _blob_key(digest, 255)
    txn.put(key, data)
    return key


def _put_thumbnail(txn, image_id: int, data: bytes) -> None:
    """Point *image_id* at *data*, sharing the blob with any identical thumbnail."""
    txn.put(_ref_key(image_id), _put_blob(txn, data))
    txn.delete(_key(image_id))   # drop the pre-dedup entry if one is still there


def _get_thumbnail(txn, image_id: int) -> Optional[bytes]:
    """Read *image_id*, transparently handling not-yet-migrated entries."""
    blob_key = txn.get(_ref_key(image_id))
    if blob_key is not None:
        return txn.get(bytes(blob_key))
    return txn.get(_key(image_id))


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
    """Return the space ``data.mdb`` actually occupies, or 0 if not present.

    ``st_size`` is the wrong measure here.  LMDB sizes the file to its whole
    map up front, and on Windows that file is sparse — so the logical length is
    the 10 GB map regardless of how many thumbnails are stored, which made every
    "reclaimed N MB" report read as zero.  Blocks actually allocated are what
    matters, via ``st_blocks`` where the platform provides it and the NTFS valid
    data length otherwise.
    """
    if _lmdb_dir is None:
        return 0
    data_file = _lmdb_dir / "data.mdb"
    if not data_file.exists():
        return 0

    st = data_file.stat()
    blocks = getattr(st, "st_blocks", None)
    if blocks:                      # POSIX: 512-byte units, sparse-aware
        return blocks * 512

    if os.name == "nt":
        try:
            import re
            import subprocess
            out = subprocess.run(
                ["fsutil", "file", "queryvaliddata", str(data_file)],
                capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            match = re.search(r"0x([0-9a-fA-F]+)", out)
            if match:
                return int(match.group(1), 16)
        except Exception:
            log.debug("fsutil queryvaliddata failed; falling back to st_size")
    return st.st_size


def compact_store(progress=None) -> "tuple[int, int]":
    """Rewrite the thumbnail store defragmented, reclaiming space from deletions.

    LMDB never shrinks ``data.mdb`` on its own — deleted pages are reused but the
    file stays at its high-water mark. This writes a compacted copy and swaps it
    in, so the file shrinks to the size actually needed.

    Must not be called while other threads hold open transactions on the store.

    Args:
        progress: Optional ``callable(done_bytes, total_bytes)``.  ``env.copy()``
            is one blocking call with no callback of its own, so progress is
            derived by watching the copy grow on disk; the target is estimated
            from the live payload, which the copy ends up very close to.

    Returns:
        ``(size_before, size_after)`` in bytes.

    Raises:
        RuntimeError: If the store has not been initialised.
    """
    global _lmdb_env
    if _lmdb_env is None or _lmdb_dir is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")

    size_before = store_size_bytes()
    expected = store_stats()["payload_bytes"] or size_before or 1

    tmp_dir = _lmdb_dir.parent / (_lmdb_dir.name + ".compact_tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    stop_watch = threading.Event()

    def _watch() -> None:
        copy_file = tmp_dir / "data.mdb"
        while not stop_watch.wait(0.25):
            try:
                done = copy_file.stat().st_size
            except OSError:
                continue
            # The estimate can be a little under; never report past the end.
            progress(min(done, expected), expected)

    watcher = None
    if progress is not None:
        progress(0, expected)
        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()

    try:
        # Write a defragmented copy, then close, swap the data file in, and reopen.
        _lmdb_env.copy(str(tmp_dir), compact=True)
    finally:
        stop_watch.set()
        if watcher is not None:
            watcher.join(timeout=1.0)

    _lmdb_env.close()
    _lmdb_env = None

    os.replace(str(tmp_dir / "data.mdb"), str(_lmdb_dir / "data.mdb"))
    shutil.rmtree(tmp_dir, ignore_errors=True)

    init_thumbnail_store(_lmdb_dir)
    if progress is not None:
        progress(expected, expected)
    return size_before, store_size_bytes()


def store_stats() -> "dict[str, int]":
    """Count what the store holds without modifying it.

    Returns a dict with ``thumbnails`` (live image references), ``blobs``
    (distinct stored payloads), ``legacy`` (entries not yet content-addressed)
    and ``payload_bytes`` (total bytes across blobs and legacy entries).
    """
    if _lmdb_env is None:
        return {"thumbnails": 0, "blobs": 0, "legacy": 0, "payload_bytes": 0}

    thumbnails = blobs = legacy = payload = 0
    with _lmdb_env.begin(buffers=True) as txn:
        for key, value in txn.cursor():
            key = bytes(key)
            if key.startswith(_REF_PREFIX):
                thumbnails += 1
            elif key.startswith(_BLOB_PREFIX):
                blobs += 1
                payload += len(value)
            else:
                legacy += 1
                thumbnails += 1
                payload += len(value)
    return {"thumbnails": thumbnails, "blobs": blobs,
            "legacy": legacy, "payload_bytes": payload}


def deduplicate_store(progress=None, should_stop=None) -> "dict[str, int]":
    """Collapse identical thumbnails so each distinct payload is stored once.

    Converts any entry still keyed by bare image id into the content-addressed
    layout, then drops blobs nothing references any more (thumbnails deleted
    since the last run leave those behind by design).  Finishes by compacting,
    which is what actually returns the freed pages to the filesystem.

    Safe to re-run and safe to interrupt: each batch is committed on its own, a
    half-converted store still reads correctly, and stopping simply leaves the
    remainder for the next run.

    Args:
        progress: Optional ``callable(done, total, phase)`` for a progress bar.
        should_stop: Optional ``callable() -> bool`` polled between batches.

    Returns:
        Counts and sizes describing what changed — see the keys assembled below.

    Raises:
        RuntimeError: If the store has not been initialised.
    """
    if _lmdb_env is None or _lmdb_dir is None:
        raise RuntimeError("Thumbnail store not initialised. Call init_thumbnail_store() first.")

    def _report(done, total, phase):
        if progress is not None:
            progress(done, total, phase)

    def _stopped():
        return should_stop is not None and should_stop()

    size_before = store_size_bytes()
    before = store_stats()
    _BATCH = 500

    # ---- Phase 1: convert legacy entries, collapsing duplicates as we go ----
    converted = 0
    if before["legacy"]:
        total = before["legacy"]
        _report(0, total, "convert")
        while not _stopped():
            batch: list[tuple[int, bytes]] = []
            with _lmdb_env.begin(buffers=True) as txn:
                for key, value in txn.cursor():
                    key = bytes(key)
                    if key.startswith(_REF_PREFIX) or key.startswith(_BLOB_PREFIX):
                        continue
                    batch.append((int.from_bytes(key, "big"), bytes(value)))
                    if len(batch) >= _BATCH:
                        break
            if not batch:
                break

            def _put(txn, _batch=batch):
                for image_id, data in _batch:
                    _put_thumbnail(txn, image_id, data)
            _write(_put)
            converted += len(batch)
            _report(converted, total, "convert")

    # ---- Phase 2: drop blobs no reference points at -------------------------
    _report(0, 1, "sweep")
    live: set = set()
    orphans: list = []
    with _lmdb_env.begin(buffers=True) as txn:
        for key, value in txn.cursor():
            if bytes(key).startswith(_REF_PREFIX):
                live.add(bytes(value))
    with _lmdb_env.begin(buffers=True) as txn:
        for key, _value in txn.cursor():
            key = bytes(key)
            if key.startswith(_BLOB_PREFIX) and key not in live:
                orphans.append(key)

    for start in range(0, len(orphans), _BATCH):
        chunk = orphans[start:start + _BATCH]

        def _drop(txn, _chunk=chunk):
            for key in _chunk:
                txn.delete(key)
        _write(_drop)
        _report(min(start + _BATCH, len(orphans)), max(len(orphans), 1), "sweep")

    # ---- Phase 3: hand the freed pages back to the filesystem ---------------
    # Reported as a percentage: raw byte counts overflow the progress bar's int
    # range, and dividing down to MB loses every store smaller than 1 MB.
    compact_store(progress=lambda done, total:
                  _report(done * 100 // max(total, 1), 100, "compact"))

    after = store_stats()
    return {
        "thumbnails":     after["thumbnails"],
        "blobs_before":   before["blobs"] + before["legacy"],
        "blobs_after":    after["blobs"],
        "converted":      converted,
        "orphans_removed": len(orphans),
        "payload_before": before["payload_bytes"],
        "payload_after":  after["payload_bytes"],
        "size_before":    size_before,
        "size_after":     store_size_bytes(),
        "stopped":        bool(_stopped()),
    }


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


def _make_animated_gif(img: "Image.Image", max_size: int) -> Optional[bytes]:
    """Downscale an animated GIF, preserving its frames and timing.

    Pillow's GIF reader composites each frame onto the canvas as it seeks, so
    iterating and converting yields whole frames — partial inter-frame patches
    and disposal methods are already resolved.  Frames are flattened to RGB for
    the same reason the JPEG path is: the store has never carried alpha.

    Returns:
        GIF bytes, or ``None`` if the source is not worth animating (a single
        frame) or blows past :data:`GIF_MAX_FRAMES` / :data:`GIF_MAX_BYTES`.
    """
    frames: list = []
    durations: list[int] = []
    for frame in ImageSequence.Iterator(img):
        if len(frames) >= GIF_MAX_FRAMES:
            return None
        rgb = frame.convert("RGB")
        rgb.thumbnail((max_size, max_size), Image.LANCZOS)
        frames.append(rgb)
        durations.append(int(frame.info.get("duration", 100)))
    if len(frames) < 2:
        return None

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=durations, loop=int(img.info.get("loop", 0)),
        disposal=1, optimize=True,
    )
    data = buf.getvalue()
    return data if len(data) <= GIF_MAX_BYTES else None


def make_thumbnail(image_bytes: bytes, max_size: int = THUMB_SIZE_DEFAULT,
                   animate: bool = True) -> Optional[bytes]:
    """Convert raw image bytes to resized thumbnail bytes.

    Output is JPEG for everything except animated GIFs, which stay GIF so the
    preview pane can play them.  Callers that only need a still image can treat
    the result uniformly: ``QPixmap.loadFromData`` and Pillow both read frame 0
    of a GIF, so dHash, embeddings, and table cells behave as they always have.

    Args:
        image_bytes: Raw image data in any format supported by Pillow.
        max_size: Maximum dimension (width *or* height) of the output thumbnail.
        animate: When ``False``, always emit JPEG — an animated source is
            flattened to its first frame.  For consumers that must hand the
            bytes to something expecting a still image.

    Returns:
        JPEG bytes, GIF bytes for an animated GIF, or ``None`` if conversion
        fails for any reason.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            if animate and getattr(img, "is_animated", False):
                animated = _make_animated_gif(img, max_size)
                if animated is not None:
                    return animated
                img.seek(0)  # iterating above moved the cursor off frame 0
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=THUMB_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def is_animated_thumbnail(data: bytes) -> bool:
    """True when stored thumbnail *data* is a GIF that should be played, not shown.

    Checks the header only — no decode — so it is cheap enough for the GUI
    thread.  A single-frame GIF never reaches the store (``_make_animated_gif``
    rejects it), so the magic bytes alone are a sufficient test.
    """
    return len(data) >= 6 and data[:4] == b"GIF8"


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
    _write(lambda txn: _put_thumbnail(txn, image_id, thumb_bytes))
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
    _write(lambda txn: _put_thumbnail(txn, image_id, thumb_bytes))
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
                _put_thumbnail(txn, image_id, thumb)
        _write(_put)
    return results


def write_raw_thumbnails_batch(items: "list[tuple[int, bytes]]") -> None:
    """Write already-processed JPEG bytes for multiple images in one LMDB transaction."""
    if _lmdb_env is None or not items:
        return
    def _put(txn):
        for image_id, jpeg_bytes in items:
            if jpeg_bytes:
                _put_thumbnail(txn, image_id, jpeg_bytes)
    try:
        _write(_put)
    except lmdb.Error as exc:
        log.error("Failed to write %d thumbnails to LMDB: %s", len(items), exc)


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
        _write(lambda txn: _put_thumbnail(txn, image_id, jpeg_bytes))
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
    delete_thumbnails([path_or_id])


def delete_thumbnails(refs) -> None:
    """Remove many thumbnails in one LMDB write transaction.

    Takes the same references as :func:`delete_thumbnail`.  Use it whenever
    more than one image goes: every write transaction ends with a disk flush,
    which measured ~160 ms on a hard-disk data drive, so deleting a 20-image
    model one call at a time took over 3 s against a few ms batched.
    """
    if _lmdb_env is None:
        return
    image_ids = []
    for ref in refs:
        if isinstance(ref, int):
            image_ids.append(ref)
        elif ref and (image_id := _parse_image_id(str(ref))) is not None:
            image_ids.append(image_id)
    if not image_ids:
        return
    try:
        with _lmdb_env.begin(write=True) as txn:
            # Only the references go: a blob may still be shared with other
            # images.  Unreferenced blobs are reclaimed by deduplicate_store().
            for image_id in image_ids:
                txn.delete(_ref_key(image_id))
                txn.delete(_key(image_id))
    except lmdb.Error:
        pass


def get_thumbnail_bytes(image_id: int) -> Optional[bytes]:
    """Return raw JPEG bytes for the given *image_id*, or ``None`` if not found."""
    if _lmdb_env is None:
        return None
    with _lmdb_env.begin() as txn:
        return _get_thumbnail(txn, image_id)


def get_thumbnail_bytes_many(image_ids) -> "dict[int, bytes]":
    """Return a mapping of image_id → JPEG bytes for all *image_ids* in one LMDB txn.

    Missing keys are omitted from the result.  Safe to call from any thread.
    """
    if _lmdb_env is None:
        return {}
    result: dict[int, bytes] = {}
    with _lmdb_env.begin() as txn:
        for image_id in image_ids:
            data = _get_thumbnail(txn, image_id)
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
