"""
Perceptual image hashing (128-bit dHash) and fast Hamming-distance search.

NumPy, plus the native core for computing hashes; no Qt.  Used by the crawler (hashing new thumbnails),
Database Maintenance (recompute hashes), Find by Image and the content
duplicate finder.  Moved here from ``app.ui.find_content_duplicates_dialog``
so business-logic code no longer imports the UI layer.
"""
from typing import Optional

import numpy as np

# Lookup table: byte value → popcount, for NumPy < 2.0 (no np.bitwise_count).
_BYTE_POPCNT = np.array([bin(i).count('1') for i in range(256)], dtype=np.int32)


def popcount64(x: "np.ndarray") -> "np.ndarray":
    """Set bits per element of a uint64 array, as uint8.

    ``np.bitwise_count`` (NumPy >= 2.0) is a single SIMD ufunc; the fallback
    views each word as 8 bytes and sums a lookup table, several times slower.
    """
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    x = np.ascontiguousarray(x)
    return _BYTE_POPCNT[x.view(np.uint8).reshape(x.shape + (8,))].sum(axis=-1).astype(np.uint8)


def split_dhashes(hashes) -> "tuple[np.ndarray, np.ndarray]":
    """Turn stored dHashes (see :func:`format_dhash`) into ``(hi, lo)`` uint64 arrays."""
    p = len(DHASH_PREFIX)
    raw = bytes.fromhex("".join(h[p:] for h in hashes))
    words = np.frombuffer(raw, dtype=">u8").reshape(-1, 2)
    return words[:, 0].astype(np.uint64), words[:, 1].astype(np.uint64)


# ─────────────────────────────────────────────────────────────────────────────
# Perceptual hashing — format 2, computed by the native core
# ─────────────────────────────────────────────────────────────────────────────

DHASH_SIZE    = 8                          # grid size per gradient direction
DHASH_BITS    = 2 * DHASH_SIZE * DHASH_SIZE  # 128 (horizontal + vertical)
DHASH_HEX_LEN = DHASH_BITS // 4              # 32 hex digits
# Stored hashes are "2:" + 32 hex digits. Format 2 is computed by the native core
# (rust/stl-core/src/dhash.rs). Older formats (16 or 32 bare hex digits, from
# Pillow) have a different length, so length checks treat them as missing.
DHASH_PREFIX  = "2:"
DHASH_STR_LEN = len(DHASH_PREFIX) + DHASH_HEX_LEN   # 34


def _dhash(image_bytes: bytes, hash_size: int = DHASH_SIZE) -> Optional[int]:
    """Format-2 dHash of encoded image bytes, or ``None``.

    A 128-bit difference hash: the image is shrunk as a thumbnail would be,
    resized (Lanczos) to a 9 × 9 grid of 8-bit luma, and each cell compared
    with its right neighbour (bits 0–63) and the one below (bits 64–127).

    Only the native core computes it: its decoders differ from Pillow's, so a
    Python version could not produce the same bits, and one library must not
    mix two algorithms. Without the native core (or for undecodable data) this
    returns ``None`` and the image simply has no hash.
    """
    from app import native
    mod = native.module()
    if mod is None or hash_size != DHASH_SIZE or not image_bytes:
        return None
    try:
        return mod.dhash(image_bytes)
    except (ValueError, TypeError, AttributeError):
        return None


def compute_dhash_from_jpeg(jpeg_bytes: bytes) -> Optional[int]:
    """Public wrapper: format-2 dHash of image bytes (any decodable format)."""
    return _dhash(jpeg_bytes)


def compute_dhashes(images: "list[bytes]") -> "list[Optional[int]]":
    """:func:`_dhash` over many images at once, in parallel natively."""
    from app import native
    mod = native.module()
    if mod is None:
        return [None] * len(images)
    try:
        return mod.dhash_many([bytes(b) for b in images])
    except (ValueError, TypeError, AttributeError):
        return [_dhash(b) for b in images]


def dhash_available() -> bool:
    """True when hashes can be computed (the native core is loaded)."""
    from app import native
    return native.module() is not None and hasattr(native.module(), "dhash")


def is_current_dhash(stored) -> bool:
    """True for a stored hash in the current format (see :data:`DHASH_PREFIX`)."""
    return isinstance(stored, str) and len(stored) == DHASH_STR_LEN and stored.startswith(DHASH_PREFIX)


def format_dhash(value: int) -> str:
    """Format a dHash integer for storage: ``"2:"`` + 32 zero-padded hex digits.

    The prefix and fixed width let readers recognise hashes made by an older
    algorithm by their differing length and recompute them.
    """
    return f"{DHASH_PREFIX}{value:0{DHASH_HEX_LEN}x}"


def parse_dhash(stored) -> Optional[int]:
    """Parse a stored dHash, accepting only the current format.

    Older Pillow hashes (16 or 32 bare hex digits), integers and malformed
    values return ``None`` so callers recompute rather than compare
    incompatible fingerprints.
    """
    if not is_current_dhash(stored):
        return None
    try:
        return int(stored[len(DHASH_PREFIX):], 16)
    except ValueError:
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _hamming128_batch(
    lo_q: "np.ndarray",
    hi_q: "np.ndarray",
    lo_db: "np.ndarray",
    hi_db: "np.ndarray",
) -> "np.ndarray":
    """Hamming distance from a chunk of query hashes to *all* DB hashes.

    Parameters
    ----------
    lo_q, hi_q : (Nq,) uint64 — low/high 64 bits of query hashes
    lo_db, hi_db : (Ndb,) uint64 — low/high 64 bits of DB hashes

    Returns
    -------
    (Nq, Ndb) int32 — pairwise Hamming distances
    """
    lo_bits = popcount64(lo_q[:, None] ^ lo_db[None, :])   # (Nq, Ndb)
    hi_bits = popcount64(hi_q[:, None] ^ hi_db[None, :])
    return lo_bits.astype(np.int32) + hi_bits
