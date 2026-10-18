"""Tests for the browser filter model (app.services.library_query)."""
import pytest

from app.services.library_query import FilterSpec, page_rows, prune_ancestor_paths, table_ids
from tests import factory


@pytest.fixture
def lib():
    """Small library covering every facet, with NULLs where it matters."""
    lk = {k: factory.lookup(t, n) for k, t, n in [
        ("alice", "sculptors", "Alice"), ("bob", "sculptors", "Bob"),
        ("comics", "collections", "Comics"), ("busts", "categories", "Busts"),
        ("minis", "categories", "Minis"), ("may", "releases", "May"),
        ("s28", "scales", "28mm"), ("s75", "scales", "75mm")]}
    factory.lookup("tags", "dragon")
    factory.lookup("tags", "elf")
    lk["marvel"] = factory.insert("subcollections", name="Marvel", collection_id=lk["comics"])

    def m(n, path, **kw):
        return factory.add_model(path, f"h{n}", title=f"Model {n}", **kw)

    ids = {
        1: m(1, "D:\\lib\\Alice\\dragon.7z", creator="Alice", source="patreon",
             sculptor="Alice", category="Busts", release_year=2023, release_month=5,
             tags=("dragon",), scales=("75mm",), presupports="Presupported",
             warning="Nested archives"),
        2: m(2, "D:/lib/Alice/elf.zip", creator="alice", source="Patreon",
             sculptor="Alice", category="Minis", release_year=2023, release_month=6,
             tags=("elf",), scales=("28mm",), collection_id=lk["comics"],
             subcollection_id=lk["marvel"]),
        3: m(3, "D:/lib/Bob/knight.rar", creator="Bob", source="kickstarter",
             sculptor="Bob", release_year=2022, tags=("dragon", "elf"), scales=("28mm", "75mm"),
             release="May", warning="Archive without images\nNested archives"),
        4: m(4, "D:/lib/Bob/sub/troll.7z", creator=None, source=None, release_year=None),
        5: m(5, "E:/other/orc.zip", creator="Carol", source="mmf",
             release_year=2021, presupports="No"),
    }
    return ids, lk


def _table_ids(spec: FilterSpec, search: str = "") -> set[int]:
    return set(table_ids(spec, search=search, sort=None))


def _specs(lk):
    """(spec, expected model numbers) — expectations match the pre-refactor queries."""
    F = FilterSpec
    return [
        (F(), {1, 2, 3, 4, 5}),
        (F(include={"creator": ["ALICE"]}), {1, 2}),                  # case-insensitive
        (F(include={"source": ["patreon"]}), {1, 2}),
        (F(include={"tag": ["dragon"]}), {1, 3}),
        (F(exclude={"tag": ["dragon"]}), {2, 4, 5}),
        (F(include={"tag": ["dragon", "elf"]}), {1, 2, 3}),            # OR within facet
        (F(include={"path": ["D:/lib/Bob"]}), {3, 4}),
        (F(include={"path": ["D:/lib", "D:/lib/Bob"]}), {1, 2, 3, 4}),  # pruned
        (F(exclude={"path": ["D:/lib/Bob"]}), {1, 2, 5}),
        (F(include={"path": ["D:\\lib\\Alice"]}), {1, 2}),             # backslashes
        (F(include={"date": [(2023, None)]}), {1, 2}),
        (F(include={"date": [(2023, 5), (2022, None)]}), {1, 3}),
        (F(exclude={"date": [(2023, None)]}), {3, 5}),                 # NULL year drops out
        (F(include={"sculptor": [lk["alice"]]}), {1, 2}),
        (F(include={"collection": [lk["comics"]]}), {2}),
        (F(include={"subcollection": [lk["marvel"]]}), {2}),
        (F(include={"category": [lk["busts"], lk["minis"]]}), {1, 2}),
        (F(include={"release": [lk["may"]]}), {3}),
        (F(include={"scale": [lk["s28"]]}), {2, 3}),
        (F(exclude={"scale": [lk["s28"]]}), {1, 4, 5}),
        (F(include={"presupports": ["Presupported"]}), {1}),
        (F(include={"warning": ["Nested archives"]}), {1, 3}),
        (F(include={"warning": ["Archive without images"]}), {3}),
        (F(exclude={"warning": ["Archive without images"]}), {1}),     # NULL semantics kept
        (F(include={"filetype": [".7z"]}), {1, 4}),
        (F(exclude={"filetype": [".7z", ".zip"]}), {3}),
        (F(has_value={"tag"}), {1, 2, 3}),
        (F(no_value={"tag"}), {4, 5}),
        (F(has_value={"creator"}), {1, 2, 3, 5}),
        (F(no_value={"date"}), {4}),
        (F(has_value={"scale", "sculptor"}), {1, 2, 3}),
        (F(no_value={"presupports"}), {2, 3, 4}),
        (F(has_value={"path"}), {1, 2, 3, 4, 5}),
        (F(ids=[2, 5]), {2, 5}),
        (F(include={"creator": ["alice"], "tag": ["elf"]}), {2}),      # AND across facets
        (F(include={"sculptor": [lk["alice"]]}, exclude={"category": [lk["busts"]]}), {2}),
    ]


def test_table_query_expected_rows(lib):
    ids, lk = lib
    for spec, expected in _specs(lk):
        assert _table_ids(spec) == {ids[n] for n in expected}, spec


def test_search(lib):
    ids, _lk = lib
    assert _table_ids(FilterSpec(), "knight") == {ids[3]}         # path
    assert _table_ids(FilterSpec(), "model 1") == {ids[1]}        # title, case-insensitive
    assert _table_ids(FilterSpec(), "drag") == {ids[1], ids[3]}   # tag (and path of #1)
    assert _table_ids(FilterSpec(), "carol") == {ids[5]}          # creator


def test_spec_validation_and_normalisation():
    with pytest.raises(ValueError):
        FilterSpec(include={"nope": [1]})
    with pytest.raises(ValueError):
        FilterSpec(has_value={"nope"})
    spec = FilterSpec(include={"tag": []}, exclude={"creator": None or []})
    assert spec.is_empty()
    assert not spec.facet_active("tag")


def test_prune_ancestor_paths():
    assert prune_ancestor_paths(["a/b/c", "a/b", "a\\x\\", "ab"]) == ["a/b", "a/x", "ab"]


# ---------------------------------------------------------------------------
# table_ids / page_rows (what the browser table model loads)
# ---------------------------------------------------------------------------

def test_table_ids_order_nulls_last_and_ties_by_id(lib):
    ids, _lk = lib
    n = {v: k for k, v in ids.items()}
    by_creator = [n[i] for i in table_ids(FilterSpec(), sort=("creator", False))]
    assert by_creator == [1, 3, 5, 2, 4]          # "Alice" < "Bob" < "Carol" < "alice", NULL last
    desc = [n[i] for i in table_ids(FilterSpec(), sort=("creator", True))]
    assert desc == [2, 5, 3, 1, 4]
    by_presupports = [n[i] for i in table_ids(FilterSpec(), sort=("presupports", False))]
    assert by_presupports == [5, 1, 2, 3, 4]              # "No" < "Presupported", NULLs last by id
    with pytest.raises(ValueError):
        table_ids(FilterSpec(), sort=("tags", False))


def test_table_ids_filter_search_and_stop(lib):
    ids, _lk = lib
    n = {v: k for k, v in ids.items()}
    got = table_ids(FilterSpec(include={"creator": ["alice"]}), search="elf")
    assert [n[i] for i in got] == [2]
    assert {n[i] for i in table_ids(FilterSpec(), search="DRAGON", sort=None)} == {1, 3}
    assert table_ids(FilterSpec(), should_stop=lambda: True) is None


def test_page_rows_follow_the_given_order(lib):
    ids, lk = lib
    rows = page_rows([ids[3], 999_999, ids[1]], {ids[1]: 4})
    assert [r[0] for r in rows] == [ids[3], ids[1]]
    knight = rows[0]
    assert knight[19] == "dragon, elf" and knight[18] == "28mm, 75mm"
    assert knight[17] == "May" and knight[20] == -1 and knight[21] is False
    assert rows[1][20] == 4 and rows[1][13] == "Alice" and rows[1][16] == "Busts"
