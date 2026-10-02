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

# Lookup table: byte value → popcount.  Used by _hamming128_batch to count
# differing bits in 128-bit dHash fingerprints without a Python-level loop.
_BYTE_POPCNT = np.array([bin(i).count('1') for i in range(256)], dtype=np.int32)


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
    Nq, Ndb = len(lo_q), len(lo_db)
    xor_lo = lo_q[:, None] ^ lo_db[None, :]   # (Nq, Ndb) uint64
    xor_hi = hi_q[:, None] ^ hi_db[None, :]
    # View each uint64 as 8 bytes, look up per-byte popcount, then sum.
    lo_bits = _BYTE_POPCNT[xor_lo.view(np.uint8).reshape(Nq, Ndb, 8)].sum(axis=2)
    hi_bits = _BYTE_POPCNT[xor_hi.view(np.uint8).reshape(Nq, Ndb, 8)].sum(axis=2)
    return (lo_bits + hi_bits).astype(np.int32)
