"""Tests for the Qt-free settings provider in ``app.core.settings``."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import app.core.settings as core


@pytest.fixture(autouse=True)
def _reset_providers(monkeypatch):
    monkeypatch.setattr(core, "_data_dir_provider", None)
    monkeypatch.setattr(core, "_unrar_provider", None)
    monkeypatch.delenv(core.DATA_DIR_ENV, raising=False)
    monkeypatch.delenv(core.UNRAR_ENV, raising=False)


def test_defaults_without_provider_or_env():
    assert core.get_data_dir() == core.DATA_DIR_DEFAULT
    assert core.get_unrar_path() == ""


def test_provider_is_used_when_env_unset():
    core.register_providers(data_dir=lambda: "/from/provider", unrar_path=lambda: "/bin/unrar")
    assert core.get_data_dir() == "/from/provider"
    assert core.get_unrar_path() == "/bin/unrar"


def test_env_overrides_provider(monkeypatch):
    core.register_providers(data_dir=lambda: "/from/provider", unrar_path=lambda: "/bin/unrar")
    monkeypatch.setenv(core.DATA_DIR_ENV, "/from/env")
    monkeypatch.setenv(core.UNRAR_ENV, "/env/unrar")
    assert core.get_data_dir() == "/from/env"
    assert core.get_unrar_path() == "/env/unrar"


def test_empty_provider_value_falls_back_to_default():
    core.register_providers(data_dir=lambda: "")
    assert core.get_data_dir() == core.DATA_DIR_DEFAULT


def test_library_settings_read_from_env_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(core.DATA_DIR_ENV, str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"max_thumbnails": 9}), encoding="utf-8")
    assert core.get_max_thumbnails() == 9
    assert core.get_db_path() == str(Path(tmp_path) / "stlLibrary.db")


def test_save_then_load_round_trip(tmp_path):
    core._save_library_settings({"recurse_subdirs": False}, str(tmp_path))
    assert core._load_library_settings(str(tmp_path)) == {"recurse_subdirs": False}


def test_core_settings_imports_without_pyqt():
    """``app.core.settings`` must stay usable on a headless server."""
    code = (
        "import sys; sys.modules['PyQt6'] = None\n"
        "import app.core.settings as s\n"
        "s.build_crawler_config()\n"
    )
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run([sys.executable, "-c", code], cwd=root,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
