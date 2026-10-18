"""Tests for services.maintenance.recompute_dhashes (format-2 perceptual hashes)."""
import io

import numpy as np
import pytest
from PIL import Image as PILImage

from app.services.maintenance import recompute_dhashes
from app.utils.image_hash import compute_dhash_from_jpeg, dhash_available, format_dhash, is_current_dhash
from app.utils.thumbnailer import write_raw_thumbnail
from tests import factory


def _jpeg(seed: int) -> bytes:
    px = np.random.default_rng(seed).integers(0, 255, (24, 32, 3), dtype=np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(px).resize((96, 72)).save(buf, "JPEG")
    return buf.getvalue()


def _library():
    """Images: current hash, Pillow-format hash, no hash, no thumbnail. Returns their ids."""
    mid = factory.add_model("/lib/m.7z", "h", title="M", archive_size=1)
    ids = [factory.add_image(mid, f"{k}.jpg", dhash=d)
           for k, d in enumerate([format_dhash(5), "f" * 32, None, None])]
    for k, image_id in enumerate(ids[:3]):
        factory.execute("UPDATE images SET thumbnail_path = ? WHERE id = ?", str(image_id), image_id)
        write_raw_thumbnail(image_id, _jpeg(k))
    return ids


def _hashes(ids):
    return [factory.row("images", i)["dhash"] for i in ids]


@pytest.mark.skipif(not dhash_available(), reason="dHash needs the native core")
def test_missing_and_outdated_hashes_are_filled(thumb_store):
    ids = _library()
    logs = []
    assert recompute_dhashes(log=logs.append) == (2, 1)      # Pillow hash + none; no thumbnail
    current, old, none, no_thumb = _hashes(ids)
    assert current == format_dhash(5)                         # left alone
    assert is_current_dhash(old) and old == format_dhash(compute_dhash_from_jpeg(_jpeg(1)))
    assert none == format_dhash(compute_dhash_from_jpeg(_jpeg(2)))
    assert no_thumb is None
    assert recompute_dhashes() == (0, 1)                      # only the thumbnail-less one is left


@pytest.mark.skipif(not dhash_available(), reason="dHash needs the native core")
def test_recompute_all_and_stop(thumb_store):
    ids = _library()
    assert recompute_dhashes(recompute_all=True) == (3, 1)
    assert _hashes(ids)[0] == format_dhash(compute_dhash_from_jpeg(_jpeg(0)))
    assert recompute_dhashes(recompute_all=True, should_stop=lambda: True) == (0, 0)
