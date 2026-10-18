"""Tests for app.services.maintenance (Qt-free maintenance operations)."""
from app.services import maintenance as mt
from app.utils.thumbnailer import get_thumbnail_bytes, write_raw_thumbnail
from tests import factory


def _add(n, path, *, ahash=None, size=None, missing=False):
    return factory.add_model(str(path), ahash or f"h{n}", title=f"m{n}", archive_size=size,
                             is_missing=int(missing))


def test_verify_archives_statuses(tmp_path):
    ok = tmp_path / "ok.7z"; ok.write_bytes(b"x" * 10)
    mod = tmp_path / "mod.7z"; mod.write_bytes(b"x" * 5)
    back = tmp_path / "back.7z"; back.write_bytes(b"y")
    ids = {
        "ok": _add(1, ok, size=10),
        "mod": _add(2, mod, size=10),
        "gone": _add(3, tmp_path / "gone.7z"),
        "flagged": _add(4, tmp_path / "flagged.7z", missing=True),
        "back": _add(5, back, missing=True),
    }
    logs, ticks = [], []
    rows = mt.verify_archives(progress=lambda c, t: ticks.append((c, t)), log=logs.append)
    status = {r["id"]: r["status"] for r in rows}
    assert status == {
        ids["ok"]: mt.STATUS_OK, ids["mod"]: mt.STATUS_MODIFIED,
        ids["gone"]: mt.STATUS_MISSING, ids["flagged"]: mt.STATUS_FLAGGED,
        ids["back"]: mt.STATUS_OK,
    }
    assert ticks[-1] == (5, 5)
    assert logs[-1].startswith("Done — 2 OK, 1 modified, 1 missing, 1 flagged")
    assert factory.row("models", ids["back"])["is_missing"] == 0      # flag cleared
    assert factory.row("models", ids["flagged"])["is_missing"] == 1


def test_verify_archives_stop_and_empty():
    assert mt.verify_archives() == []
    _add(1, "/nope/a.7z")
    logs = []
    assert mt.verify_archives(log=logs.append, should_stop=lambda: True) == []
    assert "Stopped by user." in logs


def test_find_duplicate_entries_by_hash_and_name_size():
    a = _add(1, "/x/a.7z", ahash="same")
    b = _add(2, "/y/a.7z", ahash="same")
    c = _add(3, "/x/c.zip", ahash="fallback:10:1", size=10)
    d = _add(4, "/z/c.zip", ahash="fallback:10:2", size=10)
    _add(5, "/x/unique.7z", ahash="u")
    _add(6, "/x/e.zip", ahash="fallback:7:1", size=7)    # same name, other size
    _add(7, "/y/e.zip", ahash="fallback:8:1", size=8)
    rows = mt.find_duplicate_entries()
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {a, b, c, d}
    assert by_id[a]["match_path"] == "/y/a.7z" and by_id[b]["match_path"] == "/x/a.7z"
    assert by_id[c]["match_path"] == "/z/c.zip"
    assert all(r["status"] == mt.STATUS_DUPLICATE for r in rows)


def test_prune_images_keeps_lowest_ids(tmp_library):
    m1 = _add(1, "/a.7z")
    m2 = _add(2, "/b.7z")
    imgs = {}
    for mid, count in ((m1, 4), (m2, 1)):
        for k in range(count):
            imgs.setdefault(mid, []).append(factory.add_image(mid, f"{mid}_{k}.jpg"))
    for ids in imgs.values():
        for iid in ids:
            write_raw_thumbnail(iid, b"\xff\xd8jpeg" + bytes([iid]))

    assert mt.prune_images(2, batch_size=1) == (1, 2)
    left = [r[0] for r in factory.rows("SELECT id FROM images WHERE model_id = ? ORDER BY id", m1)]
    assert left == imgs[m1][:2]
    assert get_thumbnail_bytes(imgs[m1][3]) is None
    assert get_thumbnail_bytes(imgs[m1][0]) is not None
    assert mt.prune_images(2) == (0, 0)
