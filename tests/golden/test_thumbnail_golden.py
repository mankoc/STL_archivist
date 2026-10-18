"""Golden tests: native make_thumbnail against the Pillow implementation.

Thumbnails are display data, so the bytes may differ; what must match is the
output size (``Image.thumbnail``'s rounding), the format (JPEG, or GIF for an
animation) and the picture itself, within JPEG noise. Anything the native core
does not decode must raise ``ValueError`` so the Pillow path takes over.
"""
import io

import numpy as np
import pytest
from PIL import Image

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "make_thumbnail"):
    pytest.skip("stl_native predates make_thumbnail; rebuild it", allow_module_level=True)

from app.utils import thumbnailer as T


def _encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _gradient(w, h, mode="RGB"):
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :]
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None]
    rgb = np.stack([np.broadcast_to(x, (h, w)), np.broadcast_to(y, (h, w)),
                    np.broadcast_to((x + y) / 2, (h, w))], axis=-1).astype(np.uint8)
    return Image.fromarray(rgb, "RGB").convert(mode)


def _pixels(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float32)


SOURCES = {
    "jpeg rgb": lambda: _encode(_gradient(1600, 1067), "JPEG", quality=92),
    "jpeg odd size": lambda: _encode(_gradient(3001, 2001), "JPEG"),
    "jpeg grey": lambda: _encode(_gradient(900, 1400, "L"), "JPEG"),
    "jpeg small": lambda: _encode(_gradient(300, 200), "JPEG"),
    "png rgba": lambda: _encode(_gradient(1200, 700, "RGBA"), "PNG"),
    "png palette": lambda: _encode(_gradient(800, 800).convert("P"), "PNG"),
    "png grey": lambda: _encode(_gradient(640, 2000, "L"), "PNG"),
    "bmp": lambda: _encode(_gradient(1000, 300), "BMP"),
    "webp": lambda: _encode(_gradient(1300, 900), "WEBP", quality=90),
    "tiff": lambda: _encode(_gradient(1024, 1024), "TIFF"),
    "gif still": lambda: _encode(_gradient(900, 600).convert("P"), "GIF"),
}


@pytest.mark.parametrize("case", sorted(SOURCES))
@pytest.mark.parametrize("max_size", [512, 128])
def test_thumbnail_matches_pillow(case, max_size):
    raw = SOURCES[case]()
    py = T._make_thumbnail_py(raw, max_size)
    rs = stl_native.make_thumbnail(raw, max_size)
    assert rs[:3] == b"\xff\xd8\xff"                       # JPEG
    a, b = _pixels(py), _pixels(rs)
    assert a.shape == b.shape
    assert np.abs(a - b).mean() < 4.0                      # JPEG noise, not a different picture


def test_16_bit_png_is_scaled_not_clipped():
    # Pillow converts I;16 to RGB by clipping at 255, so a full-range 16-bit
    # greyscale PNG became almost white. The native core scales to 8 bits.
    grey8 = np.asarray(_gradient(700, 500, "L"), dtype=np.uint16)
    raw = _encode(Image.fromarray(grey8 * 257), "PNG")
    assert Image.open(io.BytesIO(raw)).mode.startswith("I")
    rs = stl_native.make_thumbnail(raw, 256)
    want = T._make_thumbnail_py(_encode(_gradient(700, 500, "L"), "PNG"), 256)
    assert np.abs(_pixels(rs) - _pixels(want)).mean() < 4.0


def _animation(frames, size=(400, 300)):
    imgs = [_gradient(*size).rotate(15 * k) for k in range(frames)]
    return _encode(imgs[0], "GIF", save_all=True, append_images=imgs[1:], duration=80, loop=0)


def test_animated_gif_stays_animated():
    raw = _animation(5)
    rs = stl_native.make_thumbnail(raw, 128)
    assert rs[:4] == b"GIF8"
    with Image.open(io.BytesIO(rs)) as im:
        assert im.n_frames == 5
        assert im.size == (128, 96)
    # animate=False flattens to the first frame, as a JPEG.
    still = stl_native.make_thumbnail(raw, 128, animate=False)
    assert still[:3] == b"\xff\xd8\xff"
    assert _pixels(still).shape == (96, 128, 3)


def test_too_many_frames_becomes_still():
    rs = stl_native.make_thumbnail(_animation(T.GIF_MAX_FRAMES + 1, (64, 48)), 64)
    assert rs[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize("raw", [
    b"",
    b"definitely not an image",
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,                   # truncated PNG
    _encode(_gradient(64, 64, "CMYK"), "JPEG"),             # CMYK JPEG: Pillow's conversion
], ids=["empty", "garbage", "truncated png", "cmyk jpeg"])
def test_unhandled_input_raises_for_the_pillow_fallback(raw):
    with pytest.raises(ValueError):
        stl_native.make_thumbnail(raw, 128)


def test_make_thumbnail_falls_back_to_pillow():
    cmyk = _encode(_gradient(300, 200, "CMYK"), "JPEG")
    assert T.make_thumbnail(cmyk, 128) == T._make_thumbnail_py(cmyk, 128)
    assert T.make_thumbnail(b"garbage", 128) is None
