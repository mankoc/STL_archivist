"""Tests for app.utils.image_hash (dHash + Hamming search)."""
import io
import random

import numpy as np
from PIL import Image

import pytest

from app import native
from app.utils.image_hash import (
    DHASH_HEX_LEN, DHASH_STR_LEN, _hamming, _hamming128_batch, compute_dhash_from_jpeg,
    compute_dhashes, dhash_available, format_dhash, is_current_dhash, parse_dhash, split_dhashes,
)

needs_native = pytest.mark.skipif(not dhash_available(), reason="dHash needs the native core")


def _jpeg(fill) -> bytes:
    img = Image.new("RGB", (64, 64))
    for x in range(64):
        for y in range(64):
            img.putpixel((x, y), fill(x, y))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


@needs_native
def test_dhash_is_stable_and_discriminates():
    gradient = _jpeg(lambda x, y: (x * 4, x * 4, x * 4))
    flipped = _jpeg(lambda x, y: (255 - x * 4,) * 3)
    a, b = compute_dhash_from_jpeg(gradient), compute_dhash_from_jpeg(gradient)
    assert a is not None and a == b
    assert _hamming(a, compute_dhash_from_jpeg(flipped)) > 32


def test_dhash_rejects_garbage():
    assert compute_dhash_from_jpeg(b"not an image") is None


@needs_native
def test_batch_matches_single():
    imgs = [_jpeg(lambda x, y: (x * 4, y * 4, 0)), b"junk", _jpeg(lambda x, y: (y * 4,) * 3)]
    assert compute_dhashes(imgs) == [compute_dhash_from_jpeg(b) for b in imgs]
    assert compute_dhashes(imgs)[1] is None


def test_no_hash_without_native(monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    try:
        img = _jpeg(lambda x, y: (x * 4,) * 3)
        assert not dhash_available()
        assert compute_dhash_from_jpeg(img) is None
        assert compute_dhashes([img, img]) == [None, None]
    finally:
        monkeypatch.delenv("STL_NATIVE")
        native.reload()


def test_format_and_parse_round_trip():
    value = (1 << 127) | 12345
    text = format_dhash(value)
    assert len(text) == DHASH_STR_LEN and text.startswith("2:")
    assert is_current_dhash(text)
    assert parse_dhash(text) == value
    assert format_dhash(1) == "2:" + "0" * (DHASH_HEX_LEN - 1) + "1"
    hi, lo = split_dhashes([text])
    assert (int(hi[0]) << 64 | int(lo[0])) == value


def test_parse_rejects_old_formats_and_junk():
    assert parse_dhash("f" * 16) is None          # Pillow 64-bit hash
    assert parse_dhash("f" * 32) is None          # Pillow 128-bit hash (format 1)
    assert not is_current_dhash("f" * 32)
    assert parse_dhash("2:" + "z" * DHASH_HEX_LEN) is None
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
