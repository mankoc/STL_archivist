"""Tests for app.native: the Rust module is required and reports its absence clearly."""
import pytest

from app import native


@pytest.fixture(autouse=True)
def restore():
    yield
    native.reload()


def test_loaded():
    assert native.available()
    assert native.status().startswith("native core ")


def test_missing_module_raises_a_clear_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "stl_native":
            raise ImportError("No module named 'stl_native'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    native.reload()
    assert not native.available()
    assert native.status().startswith("native core unavailable: not installed")
    with pytest.raises(RuntimeError, match="build it with"):
        native.module()


def test_library_lock_refuses_a_second_owner(tmp_path):
    first = native.module().LibraryLock(str(tmp_path), "first owner")
    with pytest.raises(native.module().LibraryLocked, match="first owner"):
        native.module().LibraryLock(str(tmp_path), "second")
    first.release()
    assert native.module().LibraryLock(str(tmp_path), "second").held
