"""Tests for the filter pane's data (app.services.filter_tree) and its worker."""
import pytest

from app.services.filter_tree import filter_tree_data
from app.services.library_query import FilterSpec

from tests.test_library_query import lib  # noqa: F401  (fixture)

SHOW_ALL = {k: True for k in ("author", "tags", "source", "date", "sculptor", "collection",
                              "subcollection", "category", "release", "scale", "presupports",
                              "warnings", "path")}


def test_unscoped_counts_and_lists(lib):
    ids, lk = lib
    d = filter_tree_data(None, SHOW_ALL, True)
    c = d["counts"]
    assert d["scoped"] is False and d["scoped_counts"] == {}
    assert c[("creator", "Alice")] == 1 and c[("creator", "alice")] == 1
    assert c[("group", "creator")] == 4                      # NULL creator not counted
    assert c[("sculptor_id", lk["alice"])] == 2
    assert c[("date_month", (2023, 5))] == 1 and c[("date_year", 2023)] == 2
    assert c[("date_month", (2022, None))] == 1
    assert c[("warning", "Nested archives")] == 2 and c[("group", "warning")] == 2
    assert c[("tag", "dragon")] == 2 and c[("group", "tag")] == 3
    assert c[("scale_id", lk["s28"])] == 2 and c[("group", "scale")] == 3
    assert c[("filetype", ".7z")] == 2 and c[("group", "filetype")] == 5
    assert d["creators"] == ["Alice", "Bob", "Carol", "alice"]
    assert d["tags"] == ["dragon", "elf"]
    assert d["dates"] == [(2023, 5), (2023, 6), (2022, None), (2021, None)]
    assert d["filetypes"] == [".7z", ".rar", ".zip"]
    assert d["sub_by_col"] == [("Comics", lk["comics"], [("Marvel", lk["marvel"])])]
    assert d["path_counts"]["D:/lib"] == 4 and d["path_counts"]["D:/"] == 4
    assert d["path_counts"]["D:/lib/Bob/sub"] == 1
    assert "D:/lib/Bob/sub" in d["sorted_dirs"] and d["scoped_dirs"] is None


def test_scoped_counts_follow_the_other_filters(lib):
    ids, lk = lib
    spec = FilterSpec(include={"creator": ["ALICE"]})
    d = filter_tree_data(spec, SHOW_ALL, True)
    sc = d["scoped_counts"]
    assert d["scoped"] is True
    # Sections other than creator are counted over models 1 and 2 only.
    assert sc[("source", "patreon")] == 1 and sc[("source", "Patreon")] == 1
    assert sc[("group", "source")] == 2
    assert ("source", "kickstarter") not in sc
    assert sc[("tag", "elf")] == 1 and sc[("group", "tag")] == 2
    assert d["sources"] == ["Patreon", "patreon"]
    # The creator section itself is not scoped by the creator filter.
    assert d["creators"] == ["Alice", "Bob", "Carol", "alice"]


def test_path_filter_scopes_through_the_temp_table(lib):
    spec = FilterSpec(include={"path": ["D:/lib/Bob"]})
    d = filter_tree_data(spec, SHOW_ALL, True)
    assert d["scoped_counts"][("creator", "Bob")] == 1
    assert d["scoped_counts"][("group", "creator")] == 1     # troll has no creator
    assert d["scoped_dirs"] is None or "D:/lib/Bob/sub" in (d["scoped_dirs"] or [])


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_worker_payload(lib, qapp):
    from app.ui.browser_filter_tree import _FilterTreeWorker
    from app.database.filter_cache import invalidate_filter_cache, load_filter_cache

    invalidate_filter_cache()
    got = []

    def run(spec):
        w = _FilterTreeWorker(gen=7, spec=spec, show_flags=SHOW_ALL, show_counts=True,
                              alpha_enabled=False, alpha_threshold=0, checked_data=set(),
                              excluded_data=set(), expanded_sections=None)
        w.signals.ready.connect(lambda gen, payload: got.append((gen, payload)))
        w.run()
        return got[-1][1]

    scoped = run(FilterSpec(include={"creator": ["Bob"]}))
    assert scoped["scope_by_section"] is not None and load_filter_cache() is None
    plain = run(None)
    assert plain["scope_by_section"] is None and plain["folder_tree"]
    assert load_filter_cache() is not None                   # unscoped result cached
    assert got[-1][0] == 7
