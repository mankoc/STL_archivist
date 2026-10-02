"""Tests for the browser filter model (app.services.library_query)."""
import pytest
from sqlalchemy import insert, select

from app.database.db import get_models_view, session_scope
from app.database.models import (
    Category, Collection, Release, Scale, Sculptor, STLModel, Subcollection, Tag,
)
from app.services.library_query import (
    FACETS, FilterSpec, conditions, facet_scopes, path_id_select,
    prune_ancestor_paths, search_condition, table_where,
)
from app.ui.browser_common import _FILTER_PATH_IDS, _FILTER_PATH_IDS_NAME


@pytest.fixture
def lib():
    """Small library covering every facet, with NULLs where it matters."""
    with session_scope() as s:
        alice, bob = Sculptor(name="Alice"), Sculptor(name="Bob")
        comics = Collection(name="Comics")
        busts, minis = Category(name="Busts"), Category(name="Minis")
        may = Release(name="May")
        s28, s75 = Scale(name="28mm"), Scale(name="75mm")
        dragon, elf = Tag(name="dragon"), Tag(name="elf")
        s.add_all([alice, bob, comics, busts, minis, may, s28, s75, dragon, elf])
        s.flush()
        marvel = Subcollection(name="Marvel", collection_id=comics.id)
        s.add(marvel)
        s.flush()

        def m(n, path, **kw):
            tags = kw.pop("tags", [])
            scales = kw.pop("scales", [])
            row = STLModel(title=f"Model {n}", archive_path=path, archive_hash=f"h{n}", **kw)
            row.tags.extend(tags)
            row.scales.extend(scales)
            s.add(row)
            s.flush()
            return row.id

        ids = {
            1: m(1, "D:\\lib\\Alice\\dragon.7z", creator="Alice", source="patreon",
                 sculptor=alice, category=busts, release_year=2023, release_month=5,
                 tags=[dragon], scales=[s75], presupports="Presupported",
                 warning="Nested archives"),
            2: m(2, "D:/lib/Alice/elf.zip", creator="alice", source="Patreon",
                 sculptor=alice, category=minis, release_year=2023, release_month=6,
                 tags=[elf], scales=[s28], collection=comics, subcollection=marvel),
            3: m(3, "D:/lib/Bob/knight.rar", creator="Bob", source="kickstarter",
                 sculptor=bob, release_year=2022, tags=[dragon, elf], scales=[s28, s75],
                 release=may, warning="Archive without images\nNested archives"),
            4: m(4, "D:/lib/Bob/sub/troll.7z", creator=None, source=None,
                 release_year=None),
            5: m(5, "E:/other/orc.zip", creator="Carol", source="mmf",
                 release_year=2021, presupports="No"),
        }
        lk = dict(alice=alice.id, bob=bob.id, comics=comics.id, marvel=marvel.id,
                  busts=busts.id, minis=minis.id, may=may.id, s28=s28.id, s75=s75.id)
    return ids, lk


def _table_ids(spec: FilterSpec) -> set[int]:
    mv = get_models_view()
    stmt = select(mv.c.id)
    where = table_where(spec, mv)
    if where is not None:
        stmt = stmt.where(where)
    with session_scope() as s:
        return {r[0] for r in s.execute(stmt)}


def _model_ids(spec: FilterSpec, **kw) -> set[int]:
    stmt = select(STLModel.id).where(*conditions(spec, STLModel, **kw))
    with session_scope() as s:
        return {r[0] for r in s.execute(stmt)}


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


def test_model_columns_match_view_columns(lib):
    """The same spec gives the same rows over STLModel (facets) and models_view (table)."""
    _ids, lk = lib
    for spec, _expected in _specs(lk):
        assert _model_ids(spec) == _table_ids(spec), spec


def test_facet_scope_equals_spec_without_that_facet(lib):
    _ids, lk = lib
    spec = FilterSpec(
        include={"creator": ["alice", "bob"], "tag": ["elf"], "scale": [lk["s28"]]},
        exclude={"warning": ["Archive without images"]},
        has_value={"date"},
    )
    scopes = facet_scopes(spec)
    assert set(scopes) == set(FACETS)
    with session_scope() as s:
        for facet, sub in scopes.items():
            reduced = FilterSpec(
                include={k: v for k, v in spec.include.items() if k != facet},
                exclude={k: v for k, v in spec.exclude.items() if k != facet},
                has_value=spec.has_value, no_value=spec.no_value,
            )
            got = {r[0] for r in s.execute(sub)}
            assert got == _table_ids(reduced), facet


def test_facet_scopes_none_when_unfiltered(lib):
    assert facet_scopes(FilterSpec()) is None


def test_facet_scope_none_for_sole_active_section(lib):
    scopes = facet_scopes(FilterSpec(include={"tag": ["dragon"]}))
    assert scopes["tag"] is None            # nothing else constrains the Tag section
    assert scopes["creator"] is not None


def test_path_group_now_applies_to_facets_too(lib):
    """Regression: the has-value 'path' group used to be ignored by facet scopes."""
    scopes = facet_scopes(FilterSpec(no_value={"path"}))
    with session_scope() as s:
        assert {r[0] for r in s.execute(scopes["creator"])} == set()


def test_temp_table_path_mode_matches_like_mode(lib):
    _ids, lk = lib
    spec = FilterSpec(include={"path": ["D:/lib"], "tag": ["elf"]},
                      exclude={"path": ["D:/lib/Bob/sub"]})
    p_inc, p_exc = spec.pruned_paths()
    with session_scope() as s:
        conn = s.connection()
        conn.exec_driver_sql(
            f"CREATE TEMP TABLE IF NOT EXISTS {_FILTER_PATH_IDS_NAME} (id INTEGER PRIMARY KEY)")
        s.execute(insert(_FILTER_PATH_IDS).from_select(["id"], path_id_select(p_inc, p_exc)))
        temp_ids = {r[0] for r in s.execute(
            select(STLModel.id).where(*conditions(spec, STLModel,
                                                  path_ids=select(_FILTER_PATH_IDS.c.id))))}
    assert temp_ids == _table_ids(spec)


def test_search_condition(lib):
    ids, _lk = lib
    mv = get_models_view()
    with session_scope() as s:
        def hits(text):
            return {r[0] for r in s.execute(select(mv.c.id).where(search_condition(mv, text)))}
        assert hits("knight") == {ids[3]}         # path
        assert hits("model 1") == {ids[1]}        # title, case-insensitive
        assert hits("drag") == {ids[1], ids[3]}   # tag (and path of #1)
        assert hits("carol") == {ids[5]}          # creator


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
