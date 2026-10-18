"""Tests for lookup usage counts, prune_unused_lookups and the cell-edit service."""
import pytest

from app.database import native_db
from app.services import editing
from app.services.lookups import usage_count, usage_counts
from tests import factory


def _model(n: int, **kw) -> int:
    return factory.add_model(f"/lib/f{n % 2}/m{n}.7z", f"h{n}", title=f"m{n}", **kw)


# ---------------------------------------------------------------------------
# usage_counts
# ---------------------------------------------------------------------------

def test_usage_counts_fk_and_m2m():
    ids = [factory.lookup("categories", n) for n in ("a", "b", "unused")]
    ids += [factory.lookup("tags", n) for n in ("t1", "t2")]
    for i in range(3):
        _model(i, category="a", tags=("t1",))
    _model(3, category="b", tags=("t1", "t2"))

    cats = usage_counts("category")
    assert cats == {ids[0]: 3, ids[1]: 1}
    assert cats.get(ids[2], 0) == 0
    assert usage_counts("tag") == {ids[3]: 4, ids[4]: 1}
    assert usage_count("category", ids[0]) == 3
    assert usage_count("category", ids[2]) == 0
    assert usage_count("tag", ids[4]) == 1


def test_usage_counts_unknown_kind():
    with pytest.raises(KeyError):
        usage_counts("nope")


# ---------------------------------------------------------------------------
# prune_unused_lookups
# ---------------------------------------------------------------------------

def _names(table):
    return [r[0] for r in factory.rows(f"SELECT name FROM {table} ORDER BY id")]


def test_prune_unused_lookups_removes_only_orphans():
    used_col = factory.lookup("collections", "used col")
    factory.lookup("collections", "empty col")
    # A collection whose only child is an orphan subcollection goes too.
    orphan_parent = factory.lookup("collections", "orphan-sub parent")
    used_sub = factory.insert("subcollections", name="used", collection_id=used_col)
    factory.insert("subcollections", name="orphan", collection_id=orphan_parent)
    for table, name in [("sculptors", "orphan sculptor"), ("categories", "orphan cat"),
                        ("releases", "orphan rel"), ("scales", "orphan scale")]:
        factory.lookup(table, name)
    _model(1, collection_id=used_col, subcollection_id=used_sub, sculptor="used sculptor",
           scales=("28mm",))

    deleted = native_db.library().prune_unused_lookups()

    assert {k: v for k, v in deleted.items() if v} == {
        "subcollections": 1, "collections": 2, "sculptors": 1,
        "categories": 1, "releases": 1, "scales": 1,
    }
    assert _names("collections") == ["used col"]
    assert _names("subcollections") == ["used"]
    assert _names("sculptors") == ["used sculptor"]
    assert _names("scales") == ["28mm"]
    assert factory.count("categories") == 0
    assert factory.count("releases") == 0


def test_prune_unused_lookups_nothing_to_do():
    assert not any(native_db.library().prune_unused_lookups().values())


# ---------------------------------------------------------------------------
# editing service
# ---------------------------------------------------------------------------

def _edit(model_id, field, text):
    editing.apply_cell_edits([(model_id, field, text)])


def test_cell_edit_scalars_and_clearing():
    mid = _model(1)
    _edit(mid, "title", "  Dragon  ")
    _edit(mid, "date", "2023-05")
    _edit(mid, "sequence", "7")
    _edit(mid, "sequence", "not a number")       # ignored, keeps 7
    _edit(mid, "presupports", "")
    _edit(mid, "to_print", "1")
    m = factory.row("models", mid)
    assert m["title"] == "Dragon"
    assert (m["release_year"], m["release_month"]) == (2023, 5)
    assert m["sequence"] == 7
    assert m["presupports"] is None
    assert m["to_print"] == 1
    _edit(mid, "title", "   ")
    assert factory.row("models", mid)["title"] is None


def test_cell_edit_tags_normalised_and_deduplicated():
    mid = _model(1)
    _edit(mid, "tags", "Dragon, fantasy , DRAGON,,")
    assert factory.names(mid, "tags") == ["dragon", "fantasy"]
    assert factory.count("tags") == 2


def test_cell_edit_scales_keep_casing_and_dedupe():
    mid = _model(1)
    _edit(mid, "scale", "32mm, 1/35, 32MM")
    assert factory.names(mid, "scales") == ["1/35", "32mm"]


def test_cell_edit_lookups_reuse_existing_rows():
    mid = _model(1)
    factory.lookup("sculptors", "Alice")
    _edit(mid, "sculptor", "Alice")
    _edit(mid, "category", "Busts")
    _edit(mid, "release", "May 2024")
    assert [factory.lookup_name(mid, k) for k in ("sculptor", "category", "release")] == \
        ["Alice", "Busts", "May 2024"]
    assert factory.count("sculptors") == 1
    _edit(mid, "sculptor", "")
    assert factory.row("models", mid)["sculptor_id"] is None


def test_subcollection_is_scoped_to_collection():
    mid = _model(1)
    _edit(mid, "collection", "Comics")
    _edit(mid, "subcollection", "Marvel")
    m = factory.row("models", mid)
    assert (factory.lookup_name(mid, "collection"), factory.lookup_name(mid, "subcollection")) == \
        ("Comics", "Marvel")
    assert factory.row("subcollections", m["subcollection_id"])["collection_id"] == m["collection_id"]

    # Moving to another collection drops the now-foreign subcollection.
    _edit(mid, "collection", "Movies")
    assert factory.lookup_name(mid, "collection") == "Movies"
    assert factory.row("models", mid)["subcollection_id"] is None

    # Same subcollection name under the new collection is a separate row.
    _edit(mid, "subcollection", "Marvel")
    assert factory.count("subcollections", "name = 'Marvel'") == 2

    _edit(mid, "collection", "")
    m = factory.row("models", mid)
    assert m["collection_id"] is None and m["subcollection_id"] is None


def test_subcollection_without_collection_adopts_existing_parent():
    col = factory.lookup("collections", "Comics")
    factory.insert("subcollections", name="DC", collection_id=col)
    mid = _model(1)
    _edit(mid, "subcollection", "DC")
    assert (factory.lookup_name(mid, "collection"), factory.lookup_name(mid, "subcollection")) == \
        ("Comics", "DC")

    # Unknown name and no collection: nothing to hang it under, nothing created.
    m2 = _model(2)
    _edit(m2, "subcollection", "Nowhere")
    assert factory.row("models", m2)["subcollection_id"] is None
    assert factory.count("subcollections", "name = 'Nowhere'") == 0


def test_unknown_cell_field_raises():
    mid = _model(1)
    with pytest.raises(KeyError):
        _edit(mid, "archive_path", "/evil")


def test_apply_cell_edits_returns_folders():
    """``editing.apply_cell_edits``: one transaction, the touched folders sorted."""
    a = factory.add_model("F:/x/a.7z", "1", title="a")
    b = factory.add_model("F:/y/b.7z", "2", title="b")
    folders = editing.apply_cell_edits([(b, "title", "B"), (a, "creator", "Me"), (99, "title", "z")])
    assert [f.replace("\\", "/") for f in folders] == ["F:/x", "F:/y"]
    assert (factory.row("models", b)["title"], factory.row("models", a)["creator"]) == ("B", "Me")
