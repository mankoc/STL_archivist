"""Tests for app.native: the optional Rust module never becomes a requirement."""
import pytest

from app import native
from app.crawler.archive import _members_fingerprint_py, members_fingerprint


@pytest.fixture(autouse=True)
def restore():
    yield
    native.reload()


def test_disabled_by_environment(monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "off")
    native.reload()
    assert native.module() is None and not native.available()
    assert native.status() == "native core unavailable: disabled by STL_NATIVE"


def test_python_fallback_when_disabled(monkeypatch):
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    members = [("b.stl", 2), ("a.stl", 1)]
    assert members_fingerprint(members) == _members_fingerprint_py(members)


def test_missing_module_is_not_an_error(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "stl_native":
            raise ImportError("No module named 'stl_native'")
        return real_import(name, *a, **k)

    monkeypatch.delenv("STL_NATIVE", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    native.reload()
    assert native.module() is None
    assert native.status().startswith("native core unavailable: not installed")
