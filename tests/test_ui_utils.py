import pytest
from app.ui.file_list_dialog import format_size
from app.core.settings import get_allow_file_delete, get_use_trash_folder, get_trash_folder_path, get_file_sorting_enabled

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
    monkeypatch.setattr("app.core.settings._load_library_settings", lambda *args, **kwargs: {})
    
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
    
    monkeypatch.setattr("app.core.settings._load_library_settings", lambda *args, **kwargs: mock_settings)
    
    assert get_allow_file_delete() is True
    assert get_use_trash_folder() is False
    assert get_trash_folder_path() == "/test/trash/path"
    assert get_file_sorting_enabled() is True


# ---------------------------------------------------------------------------
# Presupport pattern migration
# ---------------------------------------------------------------------------

def test_inert_legacy_pattern_list_is_upgraded(monkeypatch):
    """A saved list identical to the old default was never a deliberate choice.

    Those patterns drove only the retired "Archive with supports" warning, so a
    library still carrying them is upgraded to the current default rather than
    losing the "presupported" filename rule the old list has no pattern for.
    """
    import app.core.settings as cfg
    from app.crawler.rules import DEFAULT_PRESUPPORT_PATTERNS, LEGACY_PRESUPPORT_PATTERNS

    monkeypatch.setattr(cfg, "_load_library_settings",
                        lambda: {"presupport_patterns": list(LEGACY_PRESUPPORT_PATTERNS)})
    assert cfg.get_presupport_patterns() == list(DEFAULT_PRESUPPORT_PATTERNS)


def test_customised_pattern_list_is_left_alone(monkeypatch):
    import app.core.settings as cfg

    custom = [r"_mine\.stl$"]
    monkeypatch.setattr(cfg, "_load_library_settings",
                        lambda: {"presupport_patterns": list(custom)})
    assert cfg.get_presupport_patterns() == custom


def test_unset_pattern_list_falls_back_to_default(monkeypatch):
    import app.core.settings as cfg
    from app.crawler.rules import DEFAULT_PRESUPPORT_PATTERNS

    monkeypatch.setattr(cfg, "_load_library_settings", lambda: {})
    assert cfg.get_presupport_patterns() == list(DEFAULT_PRESUPPORT_PATTERNS)


# ---------------------------------------------------------------------------
# build_crawler_config
# ---------------------------------------------------------------------------

def test_build_crawler_config_actually_runs():
    """Call the real factory, not a monkeypatched stand-in.

    Every worker test replaces ``build_crawler_config`` with a stub, so a
    missing import or a renamed getter inside it goes unnoticed until the app
    starts a crawl. This calls it for real so that fails here instead.
    """
    from app.config import CrawlerConfig
    from app.ui.settings_loader import build_crawler_config

    cfg = build_crawler_config()
    assert isinstance(cfg, CrawlerConfig)
    assert cfg.presupport_patterns, "presupport patterns must never be empty"
    assert cfg.max_workers >= 1


def test_build_crawler_config_honours_its_arguments():
    from app.ui.settings_loader import build_crawler_config

    cfg = build_crawler_config(max_thumbnails=0, force_rescan=True)
    assert cfg.max_thumbnails == 0
    assert cfg.force_rescan is True
