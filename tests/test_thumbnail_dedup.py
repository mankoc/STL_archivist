"""Tests for content-addressed thumbnail storage and ``deduplicate_store()``.

The store holds each distinct payload once and maps image ids onto it, so these
check three things: reads stay byte-exact through the indirection, a store
written in the old layout still reads and converts, and deleting an image never
takes a blob another image is still using.
"""
import io

import pytest

import app.utils.thumbnailer as T


@pytest.fixture(autouse=True)
def store(tmp_path):
    T.init_thumbnail_store(tmp_path / "thumbs.lmdb")
    yield


def _jpeg(colour, size=(128, 128)) -> bytes:
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new("RGB", size, colour).save(buf, format="JPEG", quality=75)
    return buf.getvalue()


RED, GREEN, BLUE = (200, 30, 30), (30, 200, 30), (30, 30, 200)


def _write_legacy(image_id: int, data: bytes) -> None:
    """Write in the pre-dedup layout, to simulate an existing library."""
    T._write(lambda txn: txn.put(T._key(image_id), data))


# ---------------------------------------------------------------------------
# Content addressing on the write path
# ---------------------------------------------------------------------------

def test_identical_thumbnails_share_one_blob():
    data = _jpeg(RED)
    for image_id in (1, 2, 3):
        T.write_raw_thumbnail(image_id, data)

    stats = T.store_stats()
    assert stats["thumbnails"] == 3
    assert stats["blobs"] == 1


def test_different_thumbnails_get_their_own_blob():
    for image_id, colour in enumerate((RED, GREEN, BLUE), start=1):
        T.write_raw_thumbnail(image_id, _jpeg(colour))

    assert T.store_stats()["blobs"] == 3


def test_shared_thumbnails_read_back_exactly():
    red, green = _jpeg(RED), _jpeg(GREEN)
    T.write_raw_thumbnail(1, red)
    T.write_raw_thumbnail(2, red)
    T.write_raw_thumbnail(3, green)

    assert T.get_thumbnail_bytes(1) == red
    assert T.get_thumbnail_bytes(2) == red
    assert T.get_thumbnail_bytes(3) == green
    assert T.get_thumbnail_bytes_many([1, 2, 3]) == {1: red, 2: red, 3: green}


def test_save_thumbnail_also_deduplicates():
    raw = _jpeg(RED, size=(600, 600))
    assert T.save_thumbnail(raw, 1) == "1"
    assert T.save_thumbnail(raw, 2) == "2"

    assert T.store_stats()["blobs"] == 1
    assert T.get_thumbnail_bytes(1) == T.get_thumbnail_bytes(2)


# ---------------------------------------------------------------------------
# Deletion must not disturb other images
# ---------------------------------------------------------------------------

def test_deleting_one_image_keeps_the_shared_thumbnail():
    data = _jpeg(RED)
    T.write_raw_thumbnail(1, data)
    T.write_raw_thumbnail(2, data)

    T.delete_thumbnail(1)

    assert T.get_thumbnail_bytes(1) is None
    assert T.get_thumbnail_bytes(2) == data


def test_orphaned_blob_is_swept_only_once_unreferenced():
    data = _jpeg(RED)
    T.write_raw_thumbnail(1, data)
    T.write_raw_thumbnail(2, data)

    T.delete_thumbnail(1)
    assert T.deduplicate_store()["orphans_removed"] == 0, "still referenced by 2"

    T.delete_thumbnail(2)
    assert T.deduplicate_store()["orphans_removed"] == 1
    assert T.store_stats()["blobs"] == 0


# ---------------------------------------------------------------------------
# Converting a store written in the old layout
# ---------------------------------------------------------------------------

def test_legacy_entries_read_before_conversion():
    """A store that has not been deduplicated yet must still work."""
    data = _jpeg(RED)
    _write_legacy(7, data)

    assert T.get_thumbnail_bytes(7) == data
    assert T.store_stats()["legacy"] == 1


def test_deduplicate_converts_and_collapses_legacy_entries():
    payload = {i: _jpeg([RED, GREEN, BLUE][i % 3]) for i in range(1, 31)}
    for image_id, data in payload.items():
        _write_legacy(image_id, data)

    stats = T.deduplicate_store()

    assert stats["converted"] == 30
    assert stats["blobs_after"] == 3
    assert T.store_stats()["legacy"] == 0
    for image_id, data in payload.items():
        assert T.get_thumbnail_bytes(image_id) == data


def test_deduplicate_reports_payload_savings():
    data = _jpeg(RED)
    for image_id in range(1, 21):
        _write_legacy(image_id, data)

    stats = T.deduplicate_store()

    assert stats["payload_before"] == 20 * len(data)
    assert stats["payload_after"] == len(data)


def test_deduplicate_is_idempotent():
    for image_id in range(1, 11):
        _write_legacy(image_id, _jpeg(RED))
    T.deduplicate_store()

    again = T.deduplicate_store()

    assert again["converted"] == 0
    assert again["orphans_removed"] == 0
    assert T.get_thumbnail_bytes(1) == _jpeg(RED)


def test_deduplicate_on_empty_store():
    stats = T.deduplicate_store()
    assert stats["thumbnails"] == 0
    assert stats["blobs_after"] == 0


# ---------------------------------------------------------------------------
# Progress and cancellation
# ---------------------------------------------------------------------------

def test_progress_reports_each_phase():
    for image_id in range(1, 21):
        _write_legacy(image_id, _jpeg(RED))

    seen = []
    T.deduplicate_store(progress=lambda done, total, phase: seen.append(phase))

    assert {"convert", "sweep", "compact"} <= set(seen)


def test_stopping_leaves_the_store_readable():
    payload = {i: _jpeg([RED, GREEN, BLUE][i % 3]) for i in range(1, 31)}
    for image_id, data in payload.items():
        _write_legacy(image_id, data)

    stats = T.deduplicate_store(should_stop=lambda: True)

    assert stats["stopped"] is True
    # Nothing converted, but every thumbnail still reads through the legacy path.
    for image_id, data in payload.items():
        assert T.get_thumbnail_bytes(image_id) == data


# ---------------------------------------------------------------------------
# Hash collisions
# ---------------------------------------------------------------------------

def test_colliding_payloads_are_kept_apart(monkeypatch):
    """A digest clash must never serve one image's bytes for another."""
    monkeypatch.setattr(T.xxhash, "xxh3_128_digest", lambda _data: b"\x00" * 16)

    red, green = _jpeg(RED), _jpeg(GREEN)
    T.write_raw_thumbnail(1, red)
    T.write_raw_thumbnail(2, green)

    assert T.get_thumbnail_bytes(1) == red
    assert T.get_thumbnail_bytes(2) == green
    assert T.store_stats()["blobs"] == 2, "colliding payloads need separate slots"


# ---------------------------------------------------------------------------
# Size reporting
# ---------------------------------------------------------------------------

def test_store_size_is_not_the_sparse_map_size():
    """Regression: st_size reports the whole 10 GB map on Windows."""
    T.write_raw_thumbnail(1, _jpeg(RED))

    size = T.store_size_bytes()

    assert 0 < size < T._MAP_SIZE


# ---------------------------------------------------------------------------
# Compaction progress
# ---------------------------------------------------------------------------

def test_compact_store_reports_progress():
    """Regression: the bar sat still through compaction, which looked frozen."""
    for image_id in range(1, 21):
        T.write_raw_thumbnail(image_id, _jpeg((image_id, 40, 90)))

    seen = []
    T.compact_store(progress=lambda done, total: seen.append((done, total)))

    assert seen, "compaction must report progress"
    assert seen[0][0] == 0, "starts at zero"
    assert seen[-1][0] == seen[-1][1], "ends complete"
    assert all(0 <= done <= total for done, total in seen), "never exceeds the total"


def test_compact_store_without_progress_still_works():
    data = _jpeg(RED)
    T.write_raw_thumbnail(1, data)

    before, after = T.compact_store()

    assert before > 0 and after > 0
    assert T.get_thumbnail_bytes(1) == data


def test_deduplicate_reports_compact_progress():
    for image_id in range(1, 21):
        _write_legacy(image_id, _jpeg(RED))

    seen = []
    T.deduplicate_store(progress=lambda done, total, phase: seen.append((phase, done, total)))

    compact = [(d, t) for phase, d, t in seen if phase == "compact"]
    assert compact, "the compact phase must report progress"
    assert compact[-1][0] == compact[-1][1], "compact phase ends complete"
