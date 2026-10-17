"""
Tests for app.utils.thumbnailer — LMDB thumbnail store.

Covers: _parse_image_id, make_thumbnail, save_thumbnail,
        get_thumbnail_bytes, delete_thumbnail, write_raw_thumbnail,
        and error paths when the store is not initialised.
"""
import io
import struct
import zlib
from pathlib import Path

import pytest

from app.utils.thumbnailer import (
    _parse_image_id,
    delete_thumbnail,
    get_thumbnail_bytes,
    init_thumbnail_store,
    make_thumbnail,
    save_thumbnail,
    write_raw_thumbnail,
)


# ---------------------------------------------------------------------------
# Fixture: fresh LMDB store per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def thumb_store(tmp_path):
    """Open a new LMDB store for each test so state never leaks."""
    init_thumbnail_store(tmp_path / "thumbs.lmdb")


# ---------------------------------------------------------------------------
# Minimal valid PNG helper (1×1 RGB)
# ---------------------------------------------------------------------------

def _minimal_png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _animated_gif(n_frames: int = 6, size=(400, 300), duration: int = 70) -> bytes:
    """Build an animated GIF with *n_frames* visually distinct frames."""
    from PIL import Image as PILImage
    frames = [PILImage.new("RGB", size, (i * 37 % 256, 60, 180)) for i in range(n_frames)]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=duration, loop=0)
    return buf.getvalue()


def _static_gif(size=(400, 300)) -> bytes:
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new("RGB", size, (12, 200, 90)).save(buf, format="GIF")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _parse_image_id
# ---------------------------------------------------------------------------

def test_parse_image_id_plain_numeric():
    assert _parse_image_id("42") == 42


def test_parse_image_id_legacy_path_format():
    assert _parse_image_id("0000/42.jpg") == 42


def test_parse_image_id_empty_string_returns_none():
    assert _parse_image_id("") is None


def test_parse_image_id_non_numeric_stem_returns_none():
    assert _parse_image_id("abc.jpg") is None


def test_parse_image_id_pure_non_numeric_string():
    assert _parse_image_id("not-a-number") is None


def test_parse_image_id_large_id():
    assert _parse_image_id("999999") == 999999


# ---------------------------------------------------------------------------
# make_thumbnail
# ---------------------------------------------------------------------------

def test_make_thumbnail_valid_image_returns_jpeg():
    result = make_thumbnail(_minimal_png())
    assert result is not None
    assert result[:2] == b"\xff\xd8"  # JPEG magic bytes


def test_make_thumbnail_invalid_bytes_returns_none():
    assert make_thumbnail(b"not an image at all") is None


def test_make_thumbnail_empty_bytes_returns_none():
    assert make_thumbnail(b"") is None


def test_make_thumbnail_decodes_avif():
    """AVIF is decoded by Pillow's built-in codec (Pillow >= 11.3)."""
    from PIL import Image as PILImage, features
    if not features.check("avif"):
        pytest.skip("Pillow built without AVIF support")

    buf = io.BytesIO()
    PILImage.new("RGB", (120, 90), (10, 120, 200)).save(buf, format="AVIF")

    result = make_thumbnail(buf.getvalue(), max_size=64)
    assert result is not None
    assert result[:2] == bytes.fromhex("ffd8")  # transcoded to JPEG
    with PILImage.open(io.BytesIO(result)) as thumb:
        assert max(thumb.size) <= 64


def test_make_thumbnail_animated_gif_stays_animated():
    """An animated GIF keeps its frames, timing, and loop flag."""
    from PIL import Image as PILImage
    from app.utils.thumbnailer import is_animated_thumbnail

    result = make_thumbnail(_animated_gif(n_frames=6, duration=70), max_size=128)
    assert result is not None
    assert is_animated_thumbnail(result)
    with PILImage.open(io.BytesIO(result)) as gif:
        assert gif.format == "GIF"
        assert gif.n_frames == 6
        assert gif.info.get("duration") == 70
        assert gif.info.get("loop") == 0
        assert max(gif.size) <= 128


def test_make_thumbnail_static_gif_becomes_jpeg():
    """A single-frame GIF is not worth animating — store it as JPEG."""
    from app.utils.thumbnailer import is_animated_thumbnail
    result = make_thumbnail(_static_gif(), max_size=128)
    assert result is not None
    assert not is_animated_thumbnail(result)
    assert result[:2] == bytes.fromhex("ffd8")


def test_make_thumbnail_animated_gif_over_frame_cap_falls_back_to_jpeg():
    """Past GIF_MAX_FRAMES the animation is dropped rather than stored."""
    from app.utils.thumbnailer import GIF_MAX_FRAMES, is_animated_thumbnail
    result = make_thumbnail(
        _animated_gif(n_frames=GIF_MAX_FRAMES + 1, size=(60, 40)), max_size=128
    )
    assert result is not None
    assert not is_animated_thumbnail(result)
    assert result[:2] == bytes.fromhex("ffd8")


def test_make_thumbnail_animate_false_flattens_gif():
    """animate=False always yields a still, for consumers that need one."""
    from app.utils.thumbnailer import is_animated_thumbnail
    result = make_thumbnail(_animated_gif(), max_size=128, animate=False)
    assert result is not None
    assert not is_animated_thumbnail(result)
    assert result[:2] == bytes.fromhex("ffd8")


def test_is_animated_thumbnail_rejects_jpeg_and_short_input():
    from app.utils.thumbnailer import is_animated_thumbnail
    assert is_animated_thumbnail(make_thumbnail(_minimal_png())) is False
    assert is_animated_thumbnail(b"") is False
    assert is_animated_thumbnail(b"GIF") is False


def test_save_thumbnail_round_trips_animated_gif():
    """LMDB stores the GIF byte-for-byte, so it still animates after a reload."""
    from PIL import Image as PILImage
    from app.utils.thumbnailer import is_animated_thumbnail

    assert save_thumbnail(_animated_gif(n_frames=4), 7, max_size=96) == "7"
    stored = get_thumbnail_bytes(7)
    assert is_animated_thumbnail(stored)
    with PILImage.open(io.BytesIO(stored)) as gif:
        assert gif.n_frames == 4


def test_make_thumbnail_respects_max_size():
    from PIL import Image as PILImage
    large = PILImage.new("RGB", (1000, 800))
    buf = io.BytesIO()
    large.save(buf, format="PNG")

    result = make_thumbnail(buf.getvalue(), max_size=64)
    assert result is not None

    with PILImage.open(io.BytesIO(result)) as thumb:
        assert max(thumb.size) <= 64


def test_make_thumbnail_default_size_fits_within_512():
    from PIL import Image as PILImage
    large = PILImage.new("RGB", (2000, 1500))
    buf = io.BytesIO()
    large.save(buf, format="PNG")

    result = make_thumbnail(buf.getvalue())
    assert result is not None

    with PILImage.open(io.BytesIO(result)) as thumb:
        assert max(thumb.size) <= 512


# ---------------------------------------------------------------------------
# save_thumbnail / get_thumbnail_bytes — round trip
# ---------------------------------------------------------------------------

def test_save_thumbnail_returns_id_string():
    path = save_thumbnail(_minimal_png(), image_id=1)
    assert path == "1"


def test_save_and_get_thumbnail_roundtrip():
    save_thumbnail(_minimal_png(), image_id=10)
    data = get_thumbnail_bytes(10)
    assert data is not None
    assert len(data) > 0


def test_get_thumbnail_bytes_missing_returns_none():
    assert get_thumbnail_bytes(99999) is None


def test_save_thumbnail_invalid_image_returns_none():
    result = save_thumbnail(b"invalid image data", image_id=20)
    assert result is None


def test_save_thumbnail_overwrites_existing():
    save_thumbnail(_minimal_png(), image_id=5)
    first = get_thumbnail_bytes(5)
    save_thumbnail(_minimal_png(), image_id=5)
    second = get_thumbnail_bytes(5)
    assert first is not None and second is not None


# ---------------------------------------------------------------------------
# delete_thumbnail
# ---------------------------------------------------------------------------

def test_delete_thumbnail_by_string_id():
    save_thumbnail(_minimal_png(), image_id=30)
    assert get_thumbnail_bytes(30) is not None
    delete_thumbnail("30")
    assert get_thumbnail_bytes(30) is None


def test_delete_thumbnail_by_integer_id():
    save_thumbnail(_minimal_png(), image_id=31)
    delete_thumbnail(31)
    assert get_thumbnail_bytes(31) is None


def test_delete_thumbnail_legacy_path_format():
    save_thumbnail(_minimal_png(), image_id=32)
    delete_thumbnail("0000/32.jpg")
    assert get_thumbnail_bytes(32) is None


def test_delete_thumbnail_missing_key_is_noop():
    delete_thumbnail("9999")  # must not raise


def test_delete_thumbnail_invalid_id_is_noop():
    delete_thumbnail("not-an-id")  # must not raise


# ---------------------------------------------------------------------------
# write_raw_thumbnail
# ---------------------------------------------------------------------------

def test_write_raw_thumbnail_stores_bytes():
    fake_jpeg = b"\xff\xd8\xff" + b"\x00" * 50
    result = write_raw_thumbnail(40, fake_jpeg)
    assert result == "40"
    assert get_thumbnail_bytes(40) == fake_jpeg


def test_write_raw_thumbnail_returns_none_when_uninitialised(monkeypatch):
    import app.utils.thumbnailer as t
    monkeypatch.setattr(t, "_store", None)
    result = write_raw_thumbnail(41, b"\xff\xd8\xff")
    assert result is None


# ---------------------------------------------------------------------------
# save_thumbnail raises when store is not initialised
# ---------------------------------------------------------------------------

def test_save_thumbnail_raises_without_init(monkeypatch):
    import app.utils.thumbnailer as t
    monkeypatch.setattr(t, "_store", None)
    with pytest.raises(RuntimeError, match="not initialised"):
        save_thumbnail(_minimal_png(), image_id=50)


# ---------------------------------------------------------------------------
# get_thumbnail_bytes returns None when store is not initialised
# ---------------------------------------------------------------------------

def test_get_thumbnail_bytes_without_init_returns_none(monkeypatch):
    import app.utils.thumbnailer as t
    monkeypatch.setattr(t, "_store", None)
    assert get_thumbnail_bytes(1) is None


# ---------------------------------------------------------------------------
# delete_thumbnail is noop when store is not initialised
# ---------------------------------------------------------------------------

def test_delete_thumbnail_without_init_is_noop(monkeypatch):
    import app.utils.thumbnailer as t
    monkeypatch.setattr(t, "_store", None)
    delete_thumbnail("1")  # must not raise
