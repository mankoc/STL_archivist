"""Tests for ``PresupportWarningWorker`` — Database Maintenance → Check Presupport Warning.

Like its sibling ``test_image_warning_worker``, this covers a QThread whose
``run()`` is plain data logic (it rewrites ``STLModel.warning`` in place); no
widgets are involved and ``run()`` is called directly rather than started.
"""
import zipfile

import pytest

pytest.importorskip("PyQt6")

from app.config import CrawlerConfig
from app.crawler.rules import WARNING_NESTED, WARNING_PRESUPPORTED
from app.database.db import session_scope
from app.database.models import STLModel

STL = b"solid x\nendsolid x\n"


def _zip(path, members) -> object:
    """Write a zip containing *members*; a name ending in "/" becomes a folder entry."""
    with zipfile.ZipFile(path, "w") as z:
        for name in members:
            z.writestr(name, b"" if name.endswith("/") else STL)
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
    from app.ui.db_maintenance_dialog import PresupportWarningWorker

    monkeypatch.setattr(loader, "build_crawler_config",
                        lambda *a, **k: config or CrawlerConfig(max_workers=2))
    worker = PresupportWarningWorker()
    result = []
    worker.check_finished.connect(lambda c, a, s: result.append((c, a, s)))
    worker.run()
    return result[0]


def _warning_of(model_id):
    with session_scope() as s:
        return s.get(STLModel, model_id).warning


# ---------------------------------------------------------------------------
# Clearing warnings raised by the older, looser rule
# ---------------------------------------------------------------------------

def test_clears_warning_from_negated_folder(tmp_path, monkeypatch):
    """Regression: raw meshes under No_Supported/ were flagged as presupported."""
    mid = _model(_zip(tmp_path / "guts.zip", [
        "Guts/No_Supported/", "Guts/No_Supported/Arm_L_v1.stl", "Guts/render.jpg",
    ]), WARNING_PRESUPPORTED)

    assert _run(monkeypatch) == (1, 0, 0)
    assert _warning_of(mid) is None


def test_clears_warning_from_directory_entry(tmp_path, monkeypatch):
    """Regression: a folder entry named "… Presupported lys" was the only match."""
    mid = _model(_zip(tmp_path / "biker.zip", [
        "Biker/Biker Presupported lys/", "Biker/Biker Presupported lys/a.lys",
        "Biker/render.jpg",
    ]), WARNING_PRESUPPORTED)

    assert _run(monkeypatch) == (1, 0, 0)
    assert _warning_of(mid) is None


def test_clears_warning_from_non_stl_filename(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "dragon.zip",
                      ["Dragon - Presupported.jpg", "body.stl"]), WARNING_PRESUPPORTED)

    assert _run(monkeypatch) == (1, 0, 0)
    assert _warning_of(mid) is None


# ---------------------------------------------------------------------------
# Adding / preserving genuine warnings
# ---------------------------------------------------------------------------

def test_adds_warning_for_presupport_folder(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "hero.zip",
                      ["Hero/Supported/arm.stl", "Hero/render.jpg"]), None)

    assert _run(monkeypatch) == (0, 1, 0)
    assert _warning_of(mid) == WARNING_PRESUPPORTED


def test_leaves_genuine_warning_alone(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "g.zip", ["body_presupported.stl", "render.jpg"]),
                 WARNING_PRESUPPORTED)

    assert _run(monkeypatch) == (0, 0, 0)
    assert _warning_of(mid) == WARNING_PRESUPPORTED


def test_other_warning_lines_survive(tmp_path, monkeypatch):
    mid = _model(_zip(tmp_path / "k.zip", ["No_Supported/body.stl", "nested.zip"]),
                 f"{WARNING_PRESUPPORTED}\n{WARNING_NESTED}")

    _run(monkeypatch)
    assert _warning_of(mid) == WARNING_NESTED


def test_added_line_keeps_canonical_order(tmp_path, monkeypatch):
    """The added line lands where a fresh crawl would put it, not appended."""
    mid = _model(_zip(tmp_path / "o.zip", ["Supported/arm.stl"]), WARNING_NESTED)

    _run(monkeypatch)
    assert _warning_of(mid) == f"{WARNING_PRESUPPORTED}\n{WARNING_NESTED}"


# ---------------------------------------------------------------------------
# Config flag
# ---------------------------------------------------------------------------

def test_warning_disabled_clears_but_never_adds(tmp_path, monkeypatch):
    stale = _model(_zip(tmp_path / "stale.zip", ["No_Supported/body.stl"]),
                   WARNING_PRESUPPORTED)
    genuine = _model(_zip(tmp_path / "genuine.zip", ["Supported/arm.stl"]), None)

    cleared, added, _ = _run(
        monkeypatch, CrawlerConfig(warn_presupported_stl=False, max_workers=2)
    )

    assert (cleared, added) == (1, 0)
    assert _warning_of(stale) is None
    assert _warning_of(genuine) is None


# ---------------------------------------------------------------------------
# Unreadable archives
# ---------------------------------------------------------------------------

def test_unreadable_archive_is_skipped_not_cleared(tmp_path, monkeypatch):
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"not a zip at all")
    mid = _model(bad, WARNING_PRESUPPORTED)

    assert _run(monkeypatch) == (0, 0, 1)
    assert _warning_of(mid) == WARNING_PRESUPPORTED


def test_missing_archive_is_skipped_not_cleared(tmp_path, monkeypatch):
    mid = _model(tmp_path / "gone.zip", WARNING_PRESUPPORTED)

    assert _run(monkeypatch) == (0, 0, 1)
    assert _warning_of(mid) == WARNING_PRESUPPORTED


def test_empty_database_finishes_cleanly(monkeypatch):
    assert _run(monkeypatch) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Retired "Archive with supports" line
# ---------------------------------------------------------------------------

def test_strips_retired_supports_line(tmp_path, monkeypatch):
    """The dropped warning is cleaned out of stored records."""
    from app.crawler.rules import RETIRED_WARNING_SUPPORTS

    mid = _model(_zip(tmp_path / "s.zip", ["No_Supported/body.stl"]),
                 RETIRED_WARNING_SUPPORTS)

    cleared, added, _ = _run(monkeypatch)
    assert (cleared, added) == (1, 0)
    assert _warning_of(mid) is None


def test_strips_retired_line_but_keeps_other_warnings(tmp_path, monkeypatch):
    from app.crawler.rules import RETIRED_WARNING_SUPPORTS

    mid = _model(_zip(tmp_path / "s.zip", ["No_Supported/body.stl", "nested.zip"]),
                 f"{RETIRED_WARNING_SUPPORTS}\n{WARNING_NESTED}")

    _run(monkeypatch)
    assert _warning_of(mid) == WARNING_NESTED


def test_strips_retired_line_even_when_archive_unreadable(tmp_path, monkeypatch):
    """Dead text needs no verification, so it goes even on a skipped record."""
    from app.crawler.rules import RETIRED_WARNING_SUPPORTS

    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"not a zip")
    mid = _model(bad, f"{RETIRED_WARNING_SUPPORTS}\n{WARNING_PRESUPPORTED}")

    cleared, _added, skipped = _run(monkeypatch)
    assert (cleared, skipped) == (1, 1)
    # The presupport line itself survives — it could not be re-checked.
    assert _warning_of(mid) == WARNING_PRESUPPORTED


def test_configured_patterns_drive_the_warning(tmp_path, monkeypatch):
    """The Configuration pattern list now actually feeds this warning."""
    mid = _model(_zip(tmp_path / "c.zip", ["hero_XYZ.stl", "render.jpg"]), None)

    _run(monkeypatch, CrawlerConfig(max_workers=2, warn_presupported_stl=True,
                                    presupport_patterns=[r"_XYZ\.stl$"]))
    assert _warning_of(mid) == WARNING_PRESUPPORTED
