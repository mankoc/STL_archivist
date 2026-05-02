import pytest
from app.ui.file_list_dialog import format_size
from app.ui.config_dialog import get_allow_file_delete, get_use_trash_folder, get_trash_folder_path, get_file_sorting_enabled

def test_format_size():
    # Test base cases
    assert format_size(None) == "Unknown"
    assert format_size(0) == "0 B"
    assert format_size(500) == "500 B"
    
    # Test KB
    assert format_size(1024) == "1.0 KB"
    assert format_size(1536) == "1.5 KB"
    assert format_size(1024 * 1024 - 1) == "1024.0 KB"
    
    # Test MB
    assert format_size(1048576) == "1.0 MB"
    assert format_size(1572864) == "1.5 MB"
    assert format_size(1024 * 1024 * 1024 - 1) == "1024.0 MB"
    
    # Test GB
    assert format_size(1073741824) == "1.00 GB"
    assert format_size(1610612736) == "1.50 GB"

def test_config_defaults(monkeypatch):
    """Test that UI settings helper functions return correct defaults."""
    monkeypatch.setattr("app.ui.config_dialog._load_library_settings", lambda *args, **kwargs: {})
    
    assert get_allow_file_delete() is False
    assert get_use_trash_folder() is True
    assert get_trash_folder_path() == ""
    assert get_file_sorting_enabled() is False

def test_config_overrides(monkeypatch):
    """Test that UI settings helper functions return saved settings correctly."""
    
    mock_settings = {
        "allow_file_delete": True,
        "use_trash_folder": False,
        "trash_folder_path": "/test/trash/path",
        "file_sorting_enabled": True
    }
    
    monkeypatch.setattr("app.ui.config_dialog._load_library_settings", lambda *args, **kwargs: mock_settings)
    
    assert get_allow_file_delete() is True
    assert get_use_trash_folder() is False
    assert get_trash_folder_path() == "/test/trash/path"
    assert get_file_sorting_enabled() is True
