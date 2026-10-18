"""Tests for the Mass Edit dialog's path-pattern application.

Covers ``MassEditorDialog._save()``: whether values parsed from the archive
path actually reach the models. The dialog is built offscreen and ``_save()``
called directly — no interaction is simulated.
"""
import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication

from tests import factory

PATTERN = "{release} - {title}"
PATHS = [
    r"F:\Library\01 - By Author\0 - Onicron\030 - Red riding hood.7z",
    r"F:\Library\01 - By Author\0 - Onicron\031 - Blue coat.7z",
]
PARSED_TITLES = ["Red riding hood", "Blue coat"]


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _models(titles=("old one", "old two")):
    return [factory.add_model(path, f"h{i}", title=title)
            for i, (path, title) in enumerate(zip(PATHS, titles))]


def _titles(ids):
    return [factory.row("models", i)["title"] for i in ids]


def _dialog(ids, pattern=PATTERN, overwrite=True):
    from app.ui.mass_editor_dialog import MassEditorDialog

    dlg = MassEditorDialog(ids)
    dlg._pattern_edit.setText(pattern)
    dlg._overwrite_cb.setChecked(overwrite)
    return dlg


# ---------------------------------------------------------------------------
# Parsing on save
# ---------------------------------------------------------------------------

def test_ok_applies_the_pattern_without_pressing_parse_all(qapp):
    """Regression: a typed pattern previewed fine but OK applied nothing.

    ``_path_results`` was only ever filled by the "Parse all paths" button, so
    typing a pattern and clicking OK was a silent no-op.
    """
    ids = _models()
    dlg = _dialog(ids)
    assert dlg._path_results == {}, "nothing parsed before save"

    dlg._save()

    assert _titles(ids) == PARSED_TITLES


def test_save_reparses_after_the_pattern_changes(qapp):
    """A pattern edited after pressing Parse all paths must still win."""
    ids = _models()
    dlg = _dialog(ids, pattern="{title} - {release}")
    dlg._parse_paths()                      # stale results for the wrong pattern
    dlg._pattern_edit.setText(PATTERN)      # ...then the user fixes the pattern

    dlg._save()

    assert _titles(ids) == PARSED_TITLES


def test_pressing_parse_all_first_still_works(qapp):
    ids = _models()
    dlg = _dialog(ids)
    dlg._parse_paths()
    assert len(dlg._path_results) == 2

    dlg._save()

    assert _titles(ids) == PARSED_TITLES


# ---------------------------------------------------------------------------
# Overwrite flag
# ---------------------------------------------------------------------------

def test_without_overwrite_existing_titles_survive(qapp):
    """Unchecked "Overwrite" fills only empty fields — by design."""
    ids = _models()
    dlg = _dialog(ids, overwrite=False)

    dlg._save()

    assert _titles(ids) == ["old one", "old two"]


def test_without_overwrite_empty_titles_are_filled(qapp):
    ids = _models(titles=("", ""))
    dlg = _dialog(ids, overwrite=False)

    dlg._save()

    assert _titles(ids) == PARSED_TITLES


# ---------------------------------------------------------------------------
# No pattern / no match
# ---------------------------------------------------------------------------

def test_empty_pattern_leaves_models_alone(qapp):
    ids = _models()
    dlg = _dialog(ids, pattern="")

    dlg._save()

    assert _titles(ids) == ["old one", "old two"]


def test_non_matching_pattern_asks_before_continuing(qapp, monkeypatch):
    """A pattern that matches nothing must say so rather than silently no-op."""
    from PyQt6.QtWidgets import QMessageBox

    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.Cancel),
    )

    ids = _models()
    dlg = _dialog(ids, pattern="ZZZ_{title}")
    dlg._save()

    assert asked, "the user should be warned that nothing matched"
    assert _titles(ids) == ["old one", "old two"], "Cancel must not change anything"


# ---------------------------------------------------------------------------
# Show parsed values
# ---------------------------------------------------------------------------

def test_parsed_value_rows_lists_every_selected_model(qapp):
    ids = _models()
    dlg = _dialog(ids)

    attrs, rows = dlg._parsed_value_rows()

    assert attrs == ["release_name", "title"]
    assert [path for path, _ in rows] == PATHS
    assert [ex["title"] for _p, ex in rows] == PARSED_TITLES
    assert [ex["release_name"] for _p, ex in rows] == ["030", "031"]


def test_parsed_value_rows_marks_non_matching_paths(qapp):
    """A path the pattern misses still gets a row, with nothing extracted."""
    odd = factory.add_model(r"F:\Lib\no-dash-here.7z", "hx", title="odd")
    ids = _models() + [odd]

    dlg = _dialog(ids)
    _attrs, rows = dlg._parsed_value_rows()

    assert len(rows) == 3
    assert rows[-1][1] == {}, "the unmatched path yields no values"
    assert all(rows[i][1] for i in (0, 1))


def test_parsed_value_rows_reflect_the_current_pattern(qapp):
    """Reopening the table after editing the pattern shows the new result."""
    ids = _models()
    dlg = _dialog(ids, pattern="{title} - {release}")
    first, _ = dlg._parsed_value_rows()
    dlg._pattern_edit.setText(PATTERN)

    attrs, rows = dlg._parsed_value_rows()

    assert first == ["title", "release_name"]
    assert attrs == ["release_name", "title"]
    assert [ex["title"] for _p, ex in rows] == PARSED_TITLES


def test_parsed_value_rows_empty_for_a_non_matching_pattern(qapp):
    ids = _models()
    dlg = _dialog(ids, pattern="ZZZ_{title}")

    attrs, rows = dlg._parsed_value_rows()

    assert attrs == []
    assert len(rows) == 2
    assert all(ex == {} for _p, ex in rows)


# ---------------------------------------------------------------------------
# Trim whitespace
# ---------------------------------------------------------------------------

def _make_padded():
    return factory.add_model(PATHS[0], "h0", title="  padded  ", creator=" Onicron ",
                             source=" patreon ", url="  http://x  ", description="   ")


def _fields(mid):
    m = factory.row("models", mid)
    return (m["title"], m["creator"], m["source"], m["url"], m["description"])


def test_trim_strips_text_fields(qapp):
    mid = _make_padded()
    dlg = _dialog([mid], pattern="")
    dlg._trim_cb.setChecked(True)

    dlg._save()

    assert _fields(mid) == ("padded", "Onicron", "patreon", "http://x", None)


def test_trim_empties_a_whitespace_only_field(qapp):
    mid = _make_padded()
    dlg = _dialog([mid], pattern="")
    dlg._trim_cb.setChecked(True)

    dlg._save()

    assert _fields(mid)[4] is None


def test_untrimmed_fields_are_left_alone(qapp):
    mid = _make_padded()
    dlg = _dialog([mid], pattern="")
    dlg._trim_cb.setChecked(False)

    dlg._save()

    assert _fields(mid) == ("  padded  ", " Onicron ", " patreon ", "  http://x  ", "   ")


def test_trim_runs_after_the_path_pattern(qapp):
    """A pattern that leaves stray spaces is cleaned up in the same operation."""
    ids = _models()
    dlg = _dialog(ids, pattern="{release} -{title}")   # leading space in title
    dlg._trim_cb.setChecked(True)

    dlg._save()

    assert _titles(ids) == PARSED_TITLES


def test_trim_without_it_leaves_the_pattern_spacing(qapp):
    ids = _models()
    dlg = _dialog(ids, pattern="{release} -{title}")
    dlg._trim_cb.setChecked(False)

    dlg._save()

    assert _titles(ids) == [f" {t}" for t in PARSED_TITLES]
