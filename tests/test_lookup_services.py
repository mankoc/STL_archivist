"""Tests for lookup usage counts, prune_unused_lookups and the editing service."""
import pytest

from app.database.db import session_scope
from app.database.helpers import prune_unused_lookups
from app.database.models import (
    Category, Collection, Release, Scale, Sculptor, STLModel, Subcollection, Tag,
)
from app.services import editing
from app.services.lookups import usage_count, usage_counts


def _model(session, n: int, **kw) -> STLModel:
    m = STLModel(title=f"m{n}", archive_path=f"/lib/f{n % 2}/m{n}.7z",
                 archive_hash=f"h{n}", **kw)
    session.add(m)
    session.flush()
    return m


# ---------------------------------------------------------------------------
# usage_counts
# ---------------------------------------------------------------------------

def test_usage_counts_fk_and_m2m():
    with session_scope() as s:
        cat_a, cat_b = Category(name="a"), Category(name="b")
        unused = Category(name="unused")
        t1, t2 = Tag(name="t1"), Tag(name="t2")
        s.add_all([cat_a, cat_b, unused, t1, t2])
        s.flush()
        for i in range(3):
            m = _model(s, i, category=cat_a)
            m.tags.append(t1)
        m = _model(s, 3, category=cat_b)
        m.tags.extend([t1, t2])
        ids = (cat_a.id, cat_b.id, unused.id, t1.id, t2.id)

    with session_scope() as s:
        cats = usage_counts(s, "category")
        assert cats == {ids[0]: 3, ids[1]: 1}
        assert cats.get(ids[2], 0) == 0
        assert usage_counts(s, "tag") == {ids[3]: 4, ids[4]: 1}
        assert usage_count(s, "category", ids[0]) == 3
        assert usage_count(s, "category", ids[2]) == 0
        assert usage_count(s, "tag", ids[4]) == 1


def test_usage_counts_unknown_kind():
    with session_scope() as s, pytest.raises(KeyError):
        usage_counts(s, "nope")


# ---------------------------------------------------------------------------
# prune_unused_lookups
# ---------------------------------------------------------------------------

def test_prune_unused_lookups_removes_only_orphans():
    with session_scope() as s:
        used_col = Collection(name="used col")
        empty_col = Collection(name="empty col")
        # A collection whose only child is an orphan subcollection goes too.
        col_with_orphan_sub = Collection(name="orphan-sub parent")
        s.add_all([used_col, empty_col, col_with_orphan_sub])
        s.flush()
        used_sub = Subcollection(name="used", collection_id=used_col.id)
        orphan_sub = Subcollection(name="orphan", collection_id=col_with_orphan_sub.id)
        s.add_all([
            used_sub, orphan_sub,
            Sculptor(name="orphan sculptor"), Category(name="orphan cat"),
            Release(name="orphan rel"), Scale(name="orphan scale"),
        ])
        s.flush()
        used_sculptor, used_scale = Sculptor(name="used sculptor"), Scale(name="28mm")
        s.add_all([used_sculptor, used_scale])
        s.flush()
        m = _model(s, 1, collection=used_col, subcollection=used_sub, sculptor=used_sculptor)
        m.scales.append(used_scale)

    with session_scope() as s:
        deleted = prune_unused_lookups(s)

    assert deleted == {
        "subcollections": 1, "collections": 2, "sculptors": 1,
        "categories": 1, "releases": 1, "scales": 1,
    }
    with session_scope() as s:
        assert [c.name for c in s.query(Collection)] == ["used col"]
        assert [c.name for c in s.query(Subcollection)] == ["used"]
        assert [c.name for c in s.query(Sculptor)] == ["used sculptor"]
        assert [c.name for c in s.query(Scale)] == ["28mm"]
        assert s.query(Category).count() == 0
        assert s.query(Release).count() == 0


def test_prune_unused_lookups_nothing_to_do():
    with session_scope() as s:
        assert prune_unused_lookups(s) == {}


# ---------------------------------------------------------------------------
# editing service
# ---------------------------------------------------------------------------

def _fresh_model_id() -> int:
    with session_scope() as s:
        return _model(s, 1).id


def _edit(model_id, field, text):
    with session_scope() as s:
        editing.apply_cell_edit(s, s.get(STLModel, model_id), field, text)


def test_cell_edit_scalars_and_clearing():
    mid = _fresh_model_id()
    _edit(mid, "title", "  Dragon  ")
    _edit(mid, "date", "2023-05")
    _edit(mid, "sequence", "7")
    _edit(mid, "sequence", "not a number")       # ignored, keeps 7
    _edit(mid, "presupports", "")                # used to raise AttributeError
    _edit(mid, "to_print", "1")
    with session_scope() as s:
        m = s.get(STLModel, mid)
        assert m.title == "Dragon"
        assert (m.release_year, m.release_month) == (2023, 5)
        assert m.sequence == 7
        assert m.presupports is None
        assert m.to_print is True
    _edit(mid, "title", "   ")
    with session_scope() as s:
        assert s.get(STLModel, mid).title is None


def test_cell_edit_tags_normalised_and_deduplicated():
    mid = _fresh_model_id()
    _edit(mid, "tags", "Dragon, fantasy , DRAGON,,")
    with session_scope() as s:
        assert sorted(t.name for t in s.get(STLModel, mid).tags) == ["dragon", "fantasy"]
        assert s.query(Tag).count() == 2


def test_cell_edit_scales_keep_casing_and_dedupe():
    mid = _fresh_model_id()
    _edit(mid, "scale", "32mm, 1/35, 32MM")
    with session_scope() as s:
        assert sorted(x.name for x in s.get(STLModel, mid).scales) == ["1/35", "32mm"]


def test_cell_edit_lookups_reuse_existing_rows():
    mid = _fresh_model_id()
    with session_scope() as s:
        s.add(Sculptor(name="Alice"))
    _edit(mid, "sculptor", "Alice")
    _edit(mid, "category", "Busts")
    _edit(mid, "release", "May 2024")
    with session_scope() as s:
        m = s.get(STLModel, mid)
        assert (m.sculptor.name, m.category.name, m.release.name) == ("Alice", "Busts", "May 2024")
        assert s.query(Sculptor).count() == 1
    _edit(mid, "sculptor", "")
    with session_scope() as s:
        assert s.get(STLModel, mid).sculptor is None


def test_subcollection_is_scoped_to_collection():
    mid = _fresh_model_id()
    _edit(mid, "collection", "Comics")
    _edit(mid, "subcollection", "Marvel")
    with session_scope() as s:
        m = s.get(STLModel, mid)
        assert (m.collection.name, m.subcollection.name) == ("Comics", "Marvel")
        assert m.subcollection.collection_id == m.collection_id

    # Moving to another collection drops the now-foreign subcollection.
    _edit(mid, "collection", "Movies")
    with session_scope() as s:
        m = s.get(STLModel, mid)
        assert m.collection.name == "Movies" and m.subcollection is None

    # Same subcollection name under the new collection is a separate row.
    _edit(mid, "subcollection", "Marvel")
    with session_scope() as s:
        assert s.query(Subcollection).filter_by(name="Marvel").count() == 2

    _edit(mid, "collection", "")
    with session_scope() as s:
        m = s.get(STLModel, mid)
        assert m.collection is None and m.subcollection is None


def test_subcollection_without_collection_adopts_existing_parent():
    with session_scope() as s:
        col = Collection(name="Comics")
        s.add(col)
        s.flush()
        s.add(Subcollection(name="DC", collection_id=col.id))
    mid = _fresh_model_id()
    _edit(mid, "subcollection", "DC")
    with session_scope() as s:
        m = s.get(STLModel, mid)
        assert (m.collection.name, m.subcollection.name) == ("Comics", "DC")

    # Unknown name and no collection: nothing to hang it under, nothing created.
    with session_scope() as s:
        m2 = _model(s, 2)
        editing.apply_cell_edit(s, m2, "subcollection", "Nowhere")
        assert m2.subcollection is None
        assert s.query(Subcollection).filter_by(name="Nowhere").count() == 0


def test_assign_tags_merge_mode_keeps_existing():
    mid = _fresh_model_id()
    _edit(mid, "tags", "a, b")
    with session_scope() as s:
        m = s.get(STLModel, mid)
        editing.assign_tags(s, m, ["B", "c"], replace=False)
    with session_scope() as s:
        assert sorted(t.name for t in s.get(STLModel, mid).tags) == ["a", "b", "c"]


def test_unknown_cell_field_raises():
    mid = _fresh_model_id()
    with pytest.raises(KeyError):
        _edit(mid, "archive_path", "/evil")


def test_apply_cell_edits_returns_folders():
    """``editing.apply_cell_edits``: one transaction, the touched folders sorted."""
    with session_scope() as s:
        s.add_all([STLModel(title="a", archive_path="F:/x/a.7z", archive_hash="1"),
                   STLModel(title="b", archive_path="F:/y/b.7z", archive_hash="2")])
    folders = editing.apply_cell_edits([(2, "title", "B"), (1, "creator", "Me"), (99, "title", "z")])
    assert [f.replace("\\", "/") for f in folders] == ["F:/x", "F:/y"]
    with session_scope() as s:
        assert (s.get(STLModel, 2).title, s.get(STLModel, 1).creator) == ("B", "Me")
