"""
Tests for database model creation and tag relationships.
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
