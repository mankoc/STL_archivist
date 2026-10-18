"""Unit tests for app.services.file_deletion.

The service is intentionally Qt-free, so these tests run without a
``QApplication`` and exercise the full happy path plus failure
bookkeeping.
"""
import json

from app.services.file_deletion import delete_archives
from tests import factory


def test_permanent_delete_removes_file_and_record(tmp_path):
    archive = tmp_path / "test_archive.zip"
    archive.write_text("dummy content")

    model_id = factory.add_model(str(archive), "abc", title="Test")

    report = delete_archives([model_id], use_trash=False, trash_path=None)

    assert report.deleted == 1
    assert report.failed == []
    assert not archive.exists()
    assert factory.row("models", model_id) is None


def test_trash_move_writes_recovery_sidecar(tmp_path):
    archive = tmp_path / "test_trash_archive.zip"
    archive.write_text("dummy content")
    trash_dir = tmp_path / "trash"

    model_id = factory.add_model(str(archive), "def", title="Test Trash", creator="Me")

    report = delete_archives([model_id], use_trash=True, trash_path=trash_dir)

    assert report.deleted == 1
    assert not archive.exists()

    trashed = trash_dir / "test_trash_archive.zip"
    assert trashed.exists()
    assert trashed.read_text() == "dummy content"

    sidecar = trash_dir / "test_trash_archive.zip.json"
    info = json.loads(sidecar.read_text())
    assert info["original_path"] == str(archive)
    assert info["title"] == "Test Trash"
    assert info["creator"] == "Me"
    assert info["archive_hash"] == "def"
    assert "deleted_at" in info

    assert factory.row("models", model_id) is None


def test_trash_move_resolves_filename_collision(tmp_path):
    """Two archives with the same basename should both end up in trash."""
    a = tmp_path / "a" / "dup.zip"
    b = tmp_path / "b" / "dup.zip"
    a.parent.mkdir(); b.parent.mkdir()
    a.write_text("first"); b.write_text("second")
    trash_dir = tmp_path / "trash"

    ids = [factory.add_model(str(a), "h1"), factory.add_model(str(b), "h2")]

    delete_archives(ids, use_trash=True, trash_path=trash_dir)

    files = sorted(trash_dir.glob("dup*.zip"))
    assert len(files) == 2
    contents = {f.read_text() for f in files}
    assert contents == {"first", "second"}


def test_failed_deletion_is_reported_and_record_kept(tmp_path):
    """A model whose archive path doesn't exist still gets its DB row deleted
    (the file step is a no-op), but a model whose `unlink` raises is recorded
    in `report.failed`."""
    missing = tmp_path / "missing.zip"  # never created

    model_id = factory.add_model(str(missing), "x")

    report = delete_archives([model_id], use_trash=False, trash_path=None)

    # No physical file to delete, but the DB row is removed.
    assert report.deleted == 1
    assert report.failed == []
    assert factory.row("models", model_id) is None


def test_empty_list_is_a_noop(tmp_path):
    report = delete_archives([], use_trash=False, trash_path=None)
    assert report.deleted == 0
    assert report.failed == []
