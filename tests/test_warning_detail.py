"""Tests for ``_WarningDetailRunnable`` — the browser's Warning Details view.

Works out which archive members triggered each stored warning, and how big they
are. ``run()`` is called directly; no widgets are involved.
"""
import zipfile

import pytest

pytest.importorskip("PyQt6")

from app.config import CrawlerConfig
from app.crawler.rules import WARNING_NESTED, WARNING_NO_IMAGES, WARNING_PRESUPPORTED

STL = b"S"
ZIP_BYTES = b"PK\x05\x06" + b"\x00" * 18   # an empty but valid zip


def _archive(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Supported/arm.stl", STL * 300_000)
        z.writestr("body_presupported.stl", STL * 700_000)
        z.writestr("bonus.zip", ZIP_BYTES)
        z.writestr("extras.7z", ZIP_BYTES * 2)
        z.writestr("render.png", b"\x89PNG\r\n\x1a\n")
    return path


def _sections(path, warning, monkeypatch):
    import app.ui.settings_loader as loader
    from app.ui.browser_jobs import _WarningDetailRunnable

    monkeypatch.setattr(loader, "build_crawler_config",
                        lambda *a, **k: CrawlerConfig(max_workers=2))
    out = []
    runnable = _WarningDetailRunnable(str(path), warning)
    runnable.signals.ready.connect(lambda p, sec, err: out.append((sec, err)))
    runnable.run()
    return out[0]


def _files_for(sections, warning):
    return dict(next(files for w, files in sections if w == warning))


# ---------------------------------------------------------------------------
# Sizes
# ---------------------------------------------------------------------------

def test_presupported_files_carry_their_sizes(tmp_path, monkeypatch):
    zp = _archive(tmp_path / "m.zip")
    sections, error = _sections(zp, WARNING_PRESUPPORTED, monkeypatch)

    assert error == ""
    files = _files_for(sections, WARNING_PRESUPPORTED)
    assert files == {"Supported/arm.stl": 300_000, "body_presupported.stl": 700_000}


def test_nested_archives_carry_their_sizes(tmp_path, monkeypatch):
    zp = _archive(tmp_path / "m.zip")
    sections, _error = _sections(zp, WARNING_NESTED, monkeypatch)

    files = _files_for(sections, WARNING_NESTED)
    assert set(files) == {"bonus.zip", "extras.7z"}
    assert files["bonus.zip"] == len(ZIP_BYTES)
    assert files["extras.7z"] == len(ZIP_BYTES) * 2


def test_sizes_match_the_archive_catalog(tmp_path, monkeypatch):
    """The reported size is the catalog's uncompressed size for that member."""
    from app.crawler.archive import list_member_sizes

    zp = _archive(tmp_path / "m.zip")
    catalog = list_member_sizes(zp)
    sections, _error = _sections(
        zp, f"{WARNING_PRESUPPORTED}\n{WARNING_NESTED}", monkeypatch
    )

    for _warning, files in sections:
        for name, size in files:
            assert size == catalog[name]


def test_both_warnings_are_reported_separately(tmp_path, monkeypatch):
    zp = _archive(tmp_path / "m.zip")
    sections, _error = _sections(
        zp, f"{WARNING_PRESUPPORTED}\n{WARNING_NESTED}", monkeypatch
    )

    assert [w for w, _f in sections] == [WARNING_PRESUPPORTED, WARNING_NESTED]
    assert len(_files_for(sections, WARNING_PRESUPPORTED)) == 2
    assert len(_files_for(sections, WARNING_NESTED)) == 2


# ---------------------------------------------------------------------------
# Warnings with no files, and unreadable archives
# ---------------------------------------------------------------------------

def test_absence_warning_has_no_files(tmp_path, monkeypatch):
    zp = tmp_path / "noimg.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("body.stl", STL * 10)

    sections, error = _sections(zp, WARNING_NO_IMAGES, monkeypatch)

    assert error == ""
    assert sections == [(WARNING_NO_IMAGES, [])]


def test_unreadable_archive_reports_an_error(tmp_path, monkeypatch):
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"not a zip at all")

    sections, error = _sections(bad, WARNING_NESTED, monkeypatch)

    assert "could not be opened" in error
    assert sections == [(WARNING_NESTED, [])]


def test_missing_archive_reports_an_error(tmp_path, monkeypatch):
    sections, error = _sections(tmp_path / "gone.zip", WARNING_NESTED, monkeypatch)

    assert "no longer at the catalogued path" in error
    assert sections == [(WARNING_NESTED, [])]
