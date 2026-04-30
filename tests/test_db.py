"""
Tests for database model creation, tag relationships, clear_db,
session rollback, cascade deletes, and repr methods.
"""
import pytest
from app.database.db import init_db, session_scope
from app.database.models import STLModel, Tag


@pytest.fixture(autouse=True)
def in_memory_db():
    """Use a fresh in-memory SQLite for each test."""
    init_db(":memory:")


def test_create_model():
    with session_scope() as session:
        m = STLModel(title="Test", archive_path="/tmp/test.7z", archive_hash="abc123")
        session.add(m)

    with session_scope() as session:
        m = session.query(STLModel).filter_by(title="Test").first()
        assert m is not None
        assert m.archive_hash == "abc123"


def test_tag_relationship():
    with session_scope() as session:
        m = STLModel(title="Tagged", archive_path="/tmp/tagged.7z", archive_hash="def")
        t = Tag(name="sci-fi")
        m.tags.append(t)
        session.add(m)

    with session_scope() as session:
        m = session.query(STLModel).filter_by(title="Tagged").first()
        assert len(m.tags) == 1
        assert m.tags[0].name == "sci-fi"


# ---------------------------------------------------------------------------
# clear_db
# ---------------------------------------------------------------------------

def test_clear_db_removes_all_rows():
    with session_scope() as session:
        session.add(STLModel(archive_path="/a.7z", archive_hash="x", title="A"))
        session.add(STLModel(archive_path="/b.7z", archive_hash="y", title="B"))

    from app.database.db import clear_db
    clear_db()

    with session_scope() as session:
        assert session.query(STLModel).count() == 0


def test_clear_db_schema_intact_after_clear():
    """Tables still exist after clear_db — we can insert new rows immediately."""
    from app.database.db import clear_db
    clear_db()

    with session_scope() as session:
        session.add(STLModel(archive_path="/c.7z", archive_hash="z", title="C"))

    with session_scope() as session:
        assert session.query(STLModel).count() == 1


# ---------------------------------------------------------------------------
# session_scope — rollback on exception
# ---------------------------------------------------------------------------

def test_session_scope_rolls_back_on_exception():
    try:
        with session_scope() as session:
            session.add(STLModel(archive_path="/x.7z", archive_hash="a", title="X"))
            raise ValueError("forced rollback")
    except ValueError:
        pass

    with session_scope() as session:
        assert session.query(STLModel).filter_by(archive_path="/x.7z").first() is None


# ---------------------------------------------------------------------------
# get_session before init_db raises RuntimeError
# ---------------------------------------------------------------------------

def test_get_session_before_init_raises(monkeypatch):
    import app.database.db as db_module
    monkeypatch.setattr(db_module, "_SessionFactory", None)
    with pytest.raises(RuntimeError, match="not initialised"):
        db_module.get_session()


# ---------------------------------------------------------------------------
# Cascade delete: Collection → Subcollection
# ---------------------------------------------------------------------------

def test_cascade_delete_collection_removes_subcollections():
    from app.database.models import Collection, Subcollection

    with session_scope() as session:
        col = Collection(name="Comics")
        sub = Subcollection(name="Marvel", collection=col)
        session.add(col)
        session.flush()
        session.add(sub)

    with session_scope() as session:
        col = session.query(Collection).filter_by(name="Comics").one()
        session.delete(col)

    with session_scope() as session:
        assert session.query(Subcollection).filter_by(name="Marvel").first() is None


# ---------------------------------------------------------------------------
# Multiple tags on one model
# ---------------------------------------------------------------------------

def test_multiple_tags_on_model():
    with session_scope() as session:
        m = STLModel(archive_path="/multi.7z", archive_hash="h", title="Multi")
        m.tags = [Tag(name="a"), Tag(name="b"), Tag(name="c")]
        session.add(m)

    with session_scope() as session:
        m = session.query(STLModel).filter_by(title="Multi").one()
        assert {t.name for t in m.tags} == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# __repr__ methods
# ---------------------------------------------------------------------------

def test_stlmodel_repr_contains_title():
    m = STLModel(archive_path="/t.7z", archive_hash="h", title="Widget")
    assert "Widget" in repr(m)


def test_tag_repr_contains_name():
    assert "fantasy" in repr(Tag(name="fantasy"))


def test_sculptor_repr_contains_name():
    from app.database.models import Sculptor
    assert "Artist" in repr(Sculptor(name="Artist"))


def test_category_repr_contains_name():
    from app.database.models import Category
    assert "Movies" in repr(Category(name="Movies"))


def test_release_repr_contains_name():
    from app.database.models import Release
    assert "March Drop" in repr(Release(name="March Drop"))
