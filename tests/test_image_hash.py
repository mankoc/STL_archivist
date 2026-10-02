"""Tests for app.utils.image_hash (dHash + Hamming search)."""
import io
import random

import numpy as np
from PIL import Image

from app.utils.image_hash import (
    DHASH_HEX_LEN, _hamming, _hamming128_batch, compute_dhash_from_jpeg,
    format_dhash, parse_dhash,
)


def _jpeg(fill) -> bytes:
    img = Image.new("RGB", (64, 64))
    for x in range(64):
        for y in range(64):
            img.putpixel((x, y), fill(x, y))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def test_dhash_is_stable_and_discriminates():
    gradient = _jpeg(lambda x, y: (x * 4, x * 4, x * 4))
    flipped = _jpeg(lambda x, y: (255 - x * 4,) * 3)
    a, b = compute_dhash_from_jpeg(gradient), compute_dhash_from_jpeg(gradient)
    assert a is not None and a == b
    assert _hamming(a, compute_dhash_from_jpeg(flipped)) > 32


def test_dhash_rejects_garbage():
    assert compute_dhash_from_jpeg(b"not an image") is None


def test_format_and_parse_round_trip():
    value = (1 << 127) | 12345
    text = format_dhash(value)
    assert len(text) == DHASH_HEX_LEN
    assert parse_dhash(text) == value
    assert format_dhash(1) == "0" * (DHASH_HEX_LEN - 1) + "1"


def test_parse_rejects_old_width_and_junk():
    assert parse_dhash("f" * 16) is None          # legacy 64-bit hash
    assert parse_dhash("z" * DHASH_HEX_LEN) is None
    assert parse_dhash(42) is None
    assert parse_dhash(None) is None


def test_batch_hamming_matches_scalar():
    rng = random.Random(7)
    q = [rng.getrandbits(128) for _ in range(3)]
    db = [rng.getrandbits(128) for _ in range(20)]

    def split(vals):
        lo = np.array([v & ((1 << 64) - 1) for v in vals], dtype=np.uint64)
        hi = np.array([v >> 64 for v in vals], dtype=np.uint64)
        return lo, hi

    dist = _hamming128_batch(*split(q), *split(db))
    assert dist.shape == (3, 20)
    for i, a in enumerate(q):
        for j, b in enumerate(db):
            assert dist[i, j] == _hamming(a, b)
