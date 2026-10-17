"""
Perceptual image hashing (128-bit dHash) and fast Hamming-distance search.

Pure Pillow + NumPy; no Qt.  Used by the crawler (hashing new thumbnails),
Database Maintenance (recompute hashes), Find by Image and the content
duplicate finder.  Moved here from ``app.ui.find_content_duplicates_dialog``
so business-logic code no longer imports the UI layer.
"""
import io
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


def split_dhashes(hex_hashes) -> "tuple[np.ndarray, np.ndarray]":
    """Turn 32-char hex dHashes into ``(hi, lo)`` uint64 arrays, without Python ints."""
    raw = bytes.fromhex("".join(hex_hashes))
    words = np.frombuffer(raw, dtype=">u8").reshape(-1, 2)
    return words[:, 0].astype(np.uint64), words[:, 1].astype(np.uint64)


# ─────────────────────────────────────────────────────────────────────────────
# Perceptual hashing — pure Pillow, no extra dependencies
# ─────────────────────────────────────────────────────────────────────────────

DHASH_SIZE    = 8                          # grid size per gradient direction
DHASH_BITS    = 2 * DHASH_SIZE * DHASH_SIZE  # 128 (horizontal + vertical)
DHASH_HEX_LEN = DHASH_BITS // 4              # 32 hex chars


def _dhash(jpeg_bytes: bytes, hash_size: int = DHASH_SIZE) -> Optional[int]:
    """Compute a 128-bit difference hash (horizontal + vertical) from JPEG bytes.

    The image is converted to greyscale and resized to
    (hash_size+1) × (hash_size+1) pixels.  Two ``hash_size²``-bit hashes are
    computed and concatenated: one comparing each pixel to its right neighbour
    (horizontal gradient) and one comparing each pixel to the one below it
    (vertical gradient).  Capturing both directions discriminates better than
    a horizontal-only hash, reducing false matches between images that share a
    horizontal gradient but differ vertically.

    Returns None if the image cannot be decoded.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
        img = img.resize((hash_size + 1, hash_size + 1), Image.LANCZOS)
        px  = img.load()
        result = 0
        bit = 0
        # Horizontal gradient: compare each pixel to the one to its right.
        for row in range(hash_size):
            for col in range(hash_size):
                if px[col, row] > px[col + 1, row]:
                    result |= 1 << bit
                bit += 1
        # Vertical gradient: compare each pixel to the one below it.
        for row in range(hash_size):
            for col in range(hash_size):
                if px[col, row] > px[col, row + 1]:
                    result |= 1 << bit
                bit += 1
        return result
    except Exception:
        return None


def compute_dhash_from_jpeg(jpeg_bytes: bytes) -> Optional[int]:
    """Public wrapper used by other modules to compute dHash from JPEG bytes."""
    return _dhash(jpeg_bytes)


def format_dhash(value: int) -> str:
    """Format a dHash integer as a fixed-width, zero-padded hex string.

    The fixed width (:data:`DHASH_HEX_LEN`) lets readers recognise hashes made
    by an older/narrower algorithm by their differing length and recompute them.
    """
    return f"{value:0{DHASH_HEX_LEN}x}"


def parse_dhash(stored) -> Optional[int]:
    """Parse a stored dHash, accepting only the current algorithm's bit width.

    Older 64-bit hashes (16 hex chars), integers, and malformed values return
    ``None`` so callers recompute rather than compare incompatible fingerprints.
    """
    if not isinstance(stored, str) or len(stored) != DHASH_HEX_LEN:
        return None
    try:
        return int(stored, 16)
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
