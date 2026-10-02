"""Tests for app.services.maintenance (Qt-free maintenance operations)."""
from app.database.db import session_scope
from app.database.models import Image, STLModel
from app.services import maintenance as mt
from app.utils.thumbnailer import get_thumbnail_bytes, write_raw_thumbnail


def _add(session, n, path, *, ahash=None, size=None, missing=False):
    m = STLModel(title=f"m{n}", archive_path=str(path), archive_hash=ahash or f"h{n}",
                 archive_size=size, is_missing=missing)
    session.add(m)
    session.flush()
    return m.id


def test_verify_archives_statuses(tmp_path):
    ok = tmp_path / "ok.7z"; ok.write_bytes(b"x" * 10)
    mod = tmp_path / "mod.7z"; mod.write_bytes(b"x" * 5)
    back = tmp_path / "back.7z"; back.write_bytes(b"y")
    with session_scope() as s:
        ids = {
            "ok": _add(s, 1, ok, size=10),
            "mod": _add(s, 2, mod, size=10),
            "gone": _add(s, 3, tmp_path / "gone.7z"),
            "flagged": _add(s, 4, tmp_path / "flagged.7z", missing=True),
            "back": _add(s, 5, back, missing=True),
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
    with session_scope() as s:
        assert s.get(STLModel, ids["back"]).is_missing is False     # flag cleared
        assert s.get(STLModel, ids["flagged"]).is_missing is True


def test_verify_archives_stop_and_empty():
    assert mt.verify_archives() == []
    with session_scope() as s:
        _add(s, 1, "/nope/a.7z")
    logs = []
    assert mt.verify_archives(log=logs.append, should_stop=lambda: True) == []
    assert "Stopped by user." in logs


def test_find_duplicate_entries_by_hash_and_name_size():
    with session_scope() as s:
        a = _add(s, 1, "/x/a.7z", ahash="same")
        b = _add(s, 2, "/y/a.7z", ahash="same")
        c = _add(s, 3, "/x/c.zip", ahash="fallback:10:1", size=10)
        d = _add(s, 4, "/z/c.zip", ahash="fallback:10:2", size=10)
        _add(s, 5, "/x/unique.7z", ahash="u")
        _add(s, 6, "/x/e.zip", ahash="fallback:7:1", size=7)    # same name, other size
        _add(s, 7, "/y/e.zip", ahash="fallback:8:1", size=8)
    rows = mt.find_duplicate_entries()
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {a, b, c, d}
    assert by_id[a]["match_path"] == "/y/a.7z" and by_id[b]["match_path"] == "/x/a.7z"
    assert by_id[c]["match_path"] == "/z/c.zip"
    assert all(r["status"] == mt.STATUS_DUPLICATE for r in rows)


def test_prune_images_keeps_lowest_ids(tmp_library):
    with session_scope() as s:
        m1 = _add(s, 1, "/a.7z")
        m2 = _add(s, 2, "/b.7z")
        imgs = {}
        for mid, count in ((m1, 4), (m2, 1)):
            for k in range(count):
                img = Image(model_id=mid, filename=f"{mid}_{k}.jpg")
                s.add(img)
                s.flush()
                imgs.setdefault(mid, []).append(img.id)
    for ids in imgs.values():
        for iid in ids:
            write_raw_thumbnail(iid, b"\xff\xd8jpeg" + bytes([iid]))

    assert mt.prune_images(2, batch_size=1) == (1, 2)
    with session_scope() as s:
        left = sorted(i for (i,) in s.query(Image.id).filter(Image.model_id == m1))
    assert left == imgs[m1][:2]
    assert get_thumbnail_bytes(imgs[m1][3]) is None
    assert get_thumbnail_bytes(imgs[m1][0]) is not None
    assert mt.prune_images(2) == (0, 0)
