"""Tests for the selectable icon sets in app.utils.material_icons."""
import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

import app.utils.material_icons as mi


@pytest.fixture(autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    mi.set_active_icon_set(None)          # forget any cached choice


def test_every_bundled_set_covers_every_role():
    for set_id in mi.ICON_SETS:
        if set_id == mi.DEFAULT_ICON_SET:
            continue
        missing = [r for r in mi.ICON_MAP if mi.iconset_svg_path(set_id, r) is None]
        assert not missing, f"{set_id} lacks {missing} — re-run setup_iconsets.py"
        assert (mi.ICONSETS_DIR / set_id / "LICENSE.txt").is_file()


def test_available_sets_include_material_and_bundled_sets():
    assert set(mi.available_icon_sets()) == set(mi.ICON_SETS)


@pytest.mark.parametrize("set_id", list(mi.ICON_SETS))
def test_lookup_renders_non_null_icons(set_id):
    for role in mi.ICON_MAP:
        icon = mi.lookup_icon(role, icon_set=set_id)
        assert not icon.isNull(), (set_id, role)
        assert not icon.pixmap(24, 24).isNull()


def test_unknown_role_is_empty_icon():
    assert mi.lookup_icon("no_such_role", icon_set="fluent_color").isNull()


def test_active_set_read_from_settings_and_cached(monkeypatch):
    import app.core.settings as core
    monkeypatch.setattr(core, "_load_library_settings", lambda *a, **k: {"icon_set": "flat_color"})
    mi.set_active_icon_set(None)
    assert mi.active_icon_set() == "flat_color"
    monkeypatch.setattr(core, "_load_library_settings", lambda *a, **k: {"icon_set": "fluent_emoji"})
    assert mi.active_icon_set() == "flat_color"      # read once per run


def test_toolbar_style_setting(monkeypatch):
    import app.ui.config_dialog as cd
    for stored, expected in ((None, cd.TOOLBAR_ICON_TEXT), ("icon_only", cd.TOOLBAR_ICON_ONLY),
                             ("bogus", cd.TOOLBAR_ICON_TEXT)):
        s = {} if stored is None else {"toolbar_style": stored}
        monkeypatch.setattr(cd, "_load_library_settings", lambda *a, _s=s, **k: _s)
        assert cd.get_toolbar_style() == expected


def test_is_material_theme_resolves_custom_schemes(monkeypatch):
    import app.ui.config_dialog as cd
    monkeypatch.setattr(cd, "_load_library_settings", lambda *a, **k: {
        "custom_color_schemes": {"Mine": {"base_theme": "Material Dark Teal"}}})
    assert cd.is_material_theme("Material Dark Teal")
    assert cd.is_material_theme("Mine")
    assert not cd.is_material_theme("Windows 11 Dark")
    assert not cd.is_material_theme("System Default")


def test_unknown_or_missing_set_falls_back_to_material(monkeypatch):
    import app.core.settings as core
    monkeypatch.setattr(core, "_load_library_settings", lambda *a, **k: {"icon_set": "nope"})
    mi.set_active_icon_set(None)
    assert mi.active_icon_set() == mi.DEFAULT_ICON_SET
    assert mi.iconset_svg_path("nope", "icon_tag") is None
