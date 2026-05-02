import pytest
from pathlib import Path
import json

from app.database.db import init_db, session_scope
from app.database.models import STLModel
from app.ui.browser_tab import BrowserTab

@pytest.fixture
def mock_browser(monkeypatch, tmp_path):
    # Initialize an in-memory DB
    init_db(":memory:")
    
    # We create a dummy browser but mock its UI initialization to avoid Qt requirements
    monkeypatch.setattr("app.ui.browser_tab.BrowserTab._build_ui", lambda self: None)
    monkeypatch.setattr("app.ui.browser_tab.BrowserTab._restore_header_state", lambda self: None)
    monkeypatch.setattr("app.ui.browser_tab.BrowserTab.refresh", lambda self: None)
    
    # Needs a QApplication context for QWidget, so we just mock the method we care about testing directly 
    # since _delete_files_from_disk is a self-contained unit of logic.
    class MockBrowser:
        def refresh(self): pass
    
    browser = MockBrowser()
    
    # Monkeypatch the method onto our dummy class
    from app.ui.browser_tab import BrowserTab
    browser._delete_files_from_disk = BrowserTab._delete_files_from_disk.__get__(browser)
    
    return browser

def test_delete_files_permanently(mock_browser, monkeypatch, tmp_path):
    # Setup test file
    archive_file = tmp_path / "test_archive.zip"
    archive_file.write_text("dummy content")
    
    # Setup DB
    with session_scope() as session:
        model = STLModel(title="Test", archive_path=str(archive_file), archive_hash="abc")
        session.add(model)
        session.commit()
        model_id = model.id

    # Mock settings to permanently delete
    monkeypatch.setattr("app.ui.config_dialog.get_use_trash_folder", lambda: False)
    monkeypatch.setattr("app.ui.config_dialog.get_trash_folder_path", lambda: "")
    
    # Mock confirmation dialog to always say YES
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr("PyQt6.QtWidgets.QMessageBox.warning", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    # Perform deletion
    mock_browser._delete_files_from_disk([model_id])
    
    # Verify file is deleted
    assert not archive_file.exists()
    
    # Verify DB record is deleted
    with session_scope() as session:
        assert session.get(STLModel, model_id) is None

def test_move_files_to_trash(mock_browser, monkeypatch, tmp_path):
    # Setup test file
    archive_file = tmp_path / "test_trash_archive.zip"
    archive_file.write_text("dummy content")
    
    trash_dir = tmp_path / "trash"
    
    # Setup DB
    with session_scope() as session:
        model = STLModel(title="Test Trash", archive_path=str(archive_file), archive_hash="def")
        session.add(model)
        session.commit()
        model_id = model.id

    # Mock settings to use trash
    monkeypatch.setattr("app.ui.config_dialog.get_use_trash_folder", lambda: True)
    monkeypatch.setattr("app.ui.config_dialog.get_trash_folder_path", lambda: str(trash_dir))
    
    # Mock confirmation dialog to always say YES
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr("PyQt6.QtWidgets.QMessageBox.warning", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    # Perform deletion
    mock_browser._delete_files_from_disk([model_id])
    
    # Verify original file is gone
    assert not archive_file.exists()
    
    # Verify it moved to trash
    trashed_file = trash_dir / "test_trash_archive.zip"
    assert trashed_file.exists()
    assert trashed_file.read_text() == "dummy content"
    
    # Verify sidecar info was created
    sidecar_file = trash_dir / "test_trash_archive.zip.json"
    assert sidecar_file.exists()
    
    data = json.loads(sidecar_file.read_text())
    assert data["original_path"] == str(archive_file)
    assert data["title"] == "Test Trash"
    assert data["archive_hash"] == "def"
    assert "deleted_at" in data
    
    # Verify DB record is deleted
    with session_scope() as session:
        assert session.get(STLModel, model_id) is None
