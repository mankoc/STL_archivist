"""Golden test: the native CLIP preprocessing (``stl_core::clip``) against
``image_embedding._preprocess``.

Both produce the encoder's (3, 224, 224) input. Pillow's BICUBIC and the
native Catmull-Rom convolution round differently, so the tensors are compared
with a tolerance: vectors from either side are stored under the same model id
(the server computes embeddings natively, the desktop app in Python).
"""
import io

import numpy as np
import pytest
from PIL import Image

stl_native = pytest.importorskip("stl_native")

from app.utils.image_embedding import _preprocess  # noqa: E402


def _image(w, h, fmt, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([xx * 255 / max(w - 1, 1), yy * 255 / max(h - 1, 1), (xx + yy) % 256], -1)
    noise = rng.integers(-30, 30, (h, w, 3))
    px = np.clip(base + noise, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(px).save(buf, fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    return buf.getvalue()


CASES = [(512, 384, "JPEG"), (384, 512, "JPEG"), (224, 224, "PNG"), (225, 500, "PNG"),
         (1000, 224, "JPEG"), (100, 80, "PNG"), (333, 251, "JPEG"), (640, 360, "GIF")]


@pytest.mark.parametrize("w,h,fmt", CASES)
def test_tensors_match_python(w, h, fmt):
    data = _image(w, h, fmt, seed=w + h)
    py = _preprocess(data)[0]
    rs = np.frombuffer(stl_native.clip_preprocess(data), dtype=np.float32).reshape(3, 224, 224)
    diff = np.abs(py - rs)
    # One 8-bit step is 1/255/0.27 ≈ 0.015 in normalised units: pixels agree
    # within a step or two; a few edge pixels of a near-identity downscale
    # (225 → 224) differ more, which does not move the embedding.
    assert diff.mean() < 0.01, diff.mean()
    assert np.percentile(diff, 99) < 0.05, np.percentile(diff, 99)
    assert diff.max() < 0.3, diff.max()
    cos = float((py * rs).sum() / np.linalg.norm(py) / np.linalg.norm(rs))
    assert cos > 0.9999, cos


def test_unreadable_image_raises():
    with pytest.raises(ValueError):
        stl_native.clip_preprocess(b"not an image")
