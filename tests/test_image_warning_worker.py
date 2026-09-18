"""Tests for ``ImageWarningWorker`` — Database Maintenance → Check Images Warning.

Like ``test_rescan_worker``, this covers a QThread whose ``run()`` is plain data
logic (it rewrites ``STLModel.warning`` in place); no widgets are involved and
``run()`` is called directly rather than started as a thread.
"""
import io
import zipfile

import pytest

pytest.importorskip("PyQt6")

from app.config import CrawlerConfig
from app.crawler.rules import WARNING_NESTED, WARNING_NO_IMAGES, WARNING_PRESUPPORTED
from app.database.db import session_scope
from app.database.models import STLModel


def _png() -> bytes:
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new("RGB", (16, 16), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _zip(path, members: dict) -> "object":
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _model(archive_path, warning):
    with session_scope() as s:
        m = STLModel(title=str(archive_path), archive_path=str(archive_path),
                     archive_hash=str(archive_path), warning=warning)
        s.add(m)
        s.flush()
        return m.id


def _run(monkeypatch, config=None):
    """Run the worker synchronously; returns (cleared, added, skipped)."""
    import app.ui.settings_loader as loader
    from app.ui.db_maintenance_dialog import ImageWarningWorker

    monkeypatch.setattr(loader, "build_crawler_config",
                        lambda *a, **k: config or CrawlerConfig(max_workers=2))
    worker = ImageWarningWorker()
    result = []
    worker.check_finished.connect(lambda c, a, s: result.append((c, a, s)))
    worker.run()
    return result[0]


def _warning_of(model_id):
    with session_scope() as s:
        return s.get(STLModel, model_id).warning


# ---------------------------------------------------------------------------
# Clearing / adding
# ---------------------------------------------------------------------------

def test_clears_warning_when_archive_has_images(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "a.zip", {"p.png": _png(), "m.stl": b"s"}), WARNING_NO_IMAGES)
    assert _run(monkeypatch) == (1, 0, 0)
    assert _warning_of(mid) is None


def test_clears_warning_for_newly_supported_format(tmp_path, monkeypatch):
    """A .gif-only archive counts as having images now that .gif is recognised."""
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new("RGB", (16, 16), (4, 5, 6)).save(buf, format="GIF")
    mid = _model(_zip(tmp_path / "g.zip", {"p.gif": buf.getvalue()}), WARNING_NO_IMAGES)
    assert _run(monkeypatch) == (1, 0, 0)
    assert _warning_of(mid) is None


def test_adds_warning_when_archive_has_no_images(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "n.zip", {"m.stl": b"s", "readme.txt": b"x"}), None)
    assert _run(monkeypatch) == (0, 1, 0)
    assert _warning_of(mid) == WARNING_NO_IMAGES


def test_leaves_other_warning_lines_intact(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "m.zip", {"p.png": _png()}),
                 f"{WARNING_NESTED}\n{WARNING_NO_IMAGES}")
    _run(monkeypatch)
    assert _warning_of(mid) == WARNING_NESTED


def test_added_line_keeps_canonical_order(tmp_path, monkeypatch):
    """The new line is ordered as a fresh crawl would write it, not appended."""
    mid = _model(_zip(tmp_path / "o.zip", {"m.stl": b"s"}),
                 f"{WARNING_PRESUPPORTED}\n{WARNING_NESTED}")
    _run(monkeypatch)
    assert _warning_of(mid) == "\n".join(
        [WARNING_PRESUPPORTED, WARNING_NO_IMAGES, WARNING_NESTED]
    )


def test_no_change_leaves_record_alone(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "ok.zip", {"p.png": _png()}), None)
    assert _run(monkeypatch) == (0, 0, 0)
    assert _warning_of(mid) is None


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------

def test_warning_disabled_clears_but_never_adds(tmp_path, monkeypatch):
    """With the option off, stale lines are still cleaned up but none are added."""
    stale = _model(_zip(tmp_path / "has.zip", {"p.png": _png()}), WARNING_NO_IMAGES)
    bare  = _model(_zip(tmp_path / "none.zip", {"m.stl": b"s"}), None)

    cleared, added, _ = _run(monkeypatch, CrawlerConfig(warn_no_images=False, max_workers=2))

    assert (cleared, added) == (1, 0)
    assert _warning_of(stale) is None
    assert _warning_of(bare) is None


# ---------------------------------------------------------------------------
# Unreadable archives
# ---------------------------------------------------------------------------

def test_unreadable_archive_is_skipped_not_flagged(tmp_path, monkeypatch):
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"not a zip at all")
    mid = _model(bad, WARNING_NESTED)

    assert _run(monkeypatch) == (0, 0, 1)
    assert _warning_of(mid) == WARNING_NESTED


def test_missing_archive_is_skipped_not_flagged(tmp_path, monkeypatch):
    mid = _model(tmp_path / "gone.zip", None)
    assert _run(monkeypatch) == (0, 0, 1)
    assert _warning_of(mid) is None


def test_empty_database_finishes_cleanly(monkeypatch):
    assert _run(monkeypatch) == (0, 0, 0)
