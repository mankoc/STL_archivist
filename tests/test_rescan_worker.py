"""Tests for ``RescanWorker`` — the "Rescan Files" background job.

UI *interaction* isn't unit-tested in this project, but ``RescanWorker.run()``
is plain data logic that rewrites model records in place, so its warning
recomputation and its behaviour on an unreadable archive are covered here.
The worker's ``run()`` is called directly rather than started as a thread.
"""
import io
import zipfile

import pytest

pytest.importorskip("PyQt6")

from app.config import CrawlerConfig
from app.database.db import session_scope
from app.database.models import STLModel


def _zip_with_image(path):
    """Write a zip containing one PNG and one STL."""
    from PIL import Image as PILImage
    png = io.BytesIO()
    PILImage.new("RGB", (32, 24), (200, 30, 30)).save(png, format="PNG")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("renders/preview.png", png.getvalue())
        z.writestr("body.stl", b"solid x\nendsolid x\n")
    return path


def _make_model(archive_path, warning):
    with session_scope() as s:
        m = STLModel(title="t", archive_path=str(archive_path),
                     archive_hash="stale", warning=warning)
        s.add(m)
        s.flush()
        return m.id


def _run_rescan(monkeypatch, model_ids, config=None):
    """Run the worker synchronously with *config*, returning (errors, updated)."""
    import app.ui.settings_loader as loader
    from app.ui.browser_jobs import RescanWorker

    monkeypatch.setattr(loader, "build_crawler_config",
                        lambda *a, **k: config or CrawlerConfig())

    worker = RescanWorker(model_ids)
    errors, updated = [], []
    worker.file_error.connect(lambda fn, err: errors.append((fn, err)))
    worker.finished_ok.connect(updated.append)
    worker.run()
    return errors, updated


def _warning_of(model_id):
    with session_scope() as s:
        return s.get(STLModel, model_id).warning


# ---------------------------------------------------------------------------
# Warning recomputation
# ---------------------------------------------------------------------------

def test_rescan_clears_stale_no_images_warning(tmp_path, monkeypatch):
    """An archive that does contain images loses the 'no images' warning."""
    zp = _zip_with_image(tmp_path / "has_images.zip")
    mid = _make_model(zp, "Archive without images")

    errors, updated = _run_rescan(monkeypatch, [mid])

    assert errors == []
    assert updated == [1]
    assert _warning_of(mid) is None


def test_rescan_honours_no_printable_flag(tmp_path, monkeypatch):
    """check_no_printable follows the config instead of the function default."""
    from PIL import Image as PILImage
    png = io.BytesIO()
    PILImage.new("RGB", (32, 24), (1, 2, 3)).save(png, format="PNG")
    zp = tmp_path / "images_only.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("preview.png", png.getvalue())   # no printable files at all
    mid = _make_model(zp, None)

    _run_rescan(monkeypatch, [mid], CrawlerConfig(warn_no_printable=False))
    assert _warning_of(mid) is None

    _run_rescan(monkeypatch, [mid], CrawlerConfig(warn_no_printable=True))
    assert _warning_of(mid) == "Archive without printable files"


# ---------------------------------------------------------------------------
# Unreadable archive
# ---------------------------------------------------------------------------

def test_rescan_leaves_unreadable_archive_untouched(tmp_path, monkeypatch):
    """A corrupt/unopenable archive must not be rewritten from an empty member list."""
    bad = tmp_path / "unopenable.zip"
    bad.write_bytes(b"definitely not a zip")
    mid = _make_model(bad, "Nested archives")

    errors, updated = _run_rescan(monkeypatch, [mid])

    assert updated == [0], "an archive that could not be read is not an update"
    assert len(errors) == 1 and errors[0][0] == "unopenable.zip"
    assert _warning_of(mid) == "Nested archives", "existing metadata must survive"


def test_rescan_missing_file_reports_error(tmp_path, monkeypatch):
    mid = _make_model(tmp_path / "gone.zip", "Archive without images")

    errors, updated = _run_rescan(monkeypatch, [mid])

    assert updated == [0]
    assert errors == [("gone.zip", "Archive not found")]
    assert _warning_of(mid) == "Archive without images"
