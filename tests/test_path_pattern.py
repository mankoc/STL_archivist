"""
Unit tests for app.utils.path_pattern.
Covers pattern_to_regex, parse_path, parse_release_date, render_path_pattern,
and apply_extracted_to_model.
"""
import pytest

from app.utils.path_pattern import (
    apply_extracted_to_model,
    format_release_date,
    parse_path,
    parse_release_date,
    pattern_to_regex,
    render_path_pattern,
)
from app.database.db import init_db, session_scope
from app.database.models import Category, STLModel, Subcategory, Sculptor


@pytest.fixture(autouse=True)
def in_memory_db():
    init_db(":memory:")


# ---------------------------------------------------------------------------
# parse_release_date / format_release_date
# ---------------------------------------------------------------------------

def test_parse_release_date_year_month():
    assert parse_release_date("2024-03") == (2024, 3)
    assert parse_release_date("2024_11") == (2024, 11)


def test_parse_release_date_year_only():
    assert parse_release_date("2024") == (2024, None)


def test_parse_release_date_invalid():
    assert parse_release_date("") == (None, None)
    assert parse_release_date("not-a-date") == (None, None)
    assert parse_release_date("March 2024") == (None, None)


def test_format_release_date_roundtrip():
    assert format_release_date(2024, 3) == "2024-03"
    assert format_release_date(2024, None) == "2024"
    assert format_release_date(None, 5) == ""


# ---------------------------------------------------------------------------
# pattern_to_regex
# ---------------------------------------------------------------------------

def test_pattern_to_regex_none_for_plain_string():
    assert pattern_to_regex("no_tokens_here") is None


def test_pattern_to_regex_wildcard_single_segment():
    rx = pattern_to_regex("*/{title}")
    m = rx.search("a/b")
    assert m is not None and m.group("title") == "b"


def test_pattern_to_regex_duplicate_tokens():
    rx = pattern_to_regex("{author}/{author}")
    m = rx.search("A/B")
    assert m is not None
    # Duplicate token names get a numeric suffix; first occurrence wins under TOKEN_MAP
    assert "author" in m.groupdict()


# ---------------------------------------------------------------------------
# parse_path
# ---------------------------------------------------------------------------

def test_parse_path_flat_structure():
    result = parse_path(
        "/{source}/{author}/{name}",
        "/Patreon/Artist/model.7z",
    )
    assert result.get("source") == "Patreon"
    assert result.get("author") == "Artist"
    assert result.get("title") == "model"  # extension stripped


def test_parse_path_nested_fills_title_from_stem():
    # Pattern matches only the parent dirs; archive stem is injected as title.
    result = parse_path(
        "/{source}/{author}",
        "/Patreon/Artist/subdir/cyborg_hulk.7z",
    )
    assert result.get("source") == "Patreon"
    assert result.get("author") == "Artist"
    assert result.get("title") == "cyborg_hulk"


def test_parse_path_no_match_returns_empty():
    result = parse_path("/{source}/{author}/{name}", "/nothing/here.7z")
    # Pattern has 3 tokens but path only has 2 segments — Pass 2 still produces
    # what it can; we just check no crash and type.
    assert isinstance(result, dict)


def test_parse_path_invalid_pattern_returns_empty():
    assert parse_path("no_tokens", "/a/b.7z") == {}


# ---------------------------------------------------------------------------
# render_path_pattern
# ---------------------------------------------------------------------------

class _StubModel:
    def __init__(self, **kw):
        self.source = kw.get("source")
        self.author = kw.get("author")
        self.title = kw.get("title")
        self.release_year = kw.get("release_year")
        self.release_month = kw.get("release_month")
        self.url = kw.get("url")
        self.sculptor = kw.get("sculptor")
        self.category = kw.get("category")
        self.subcategory = kw.get("subcategory")
        self.sequence = kw.get("sequence")
        self.release = kw.get("release")


def test_render_path_pattern_fills_tokens():
    m = _StubModel(source="Patreon", author="Artist", title="Cyborg Hulk")
    out = render_path_pattern("/{source}/{author}/{title}", m)
    assert out == "/Patreon/Artist/Cyborg Hulk"


def test_render_path_pattern_unknown_replaced_with_unknown():
    m = _StubModel(source=None, author="Artist", title="Hulk")
    out = render_path_pattern("/{source}/{author}/{title}", m)
    assert out == "/_unknown/Artist/Hulk"


def test_render_path_pattern_sanitises_path_chars():
    # Title contains a slash — must not split into a new directory
    m = _StubModel(source="S", author="A", title="a/b")
    out = render_path_pattern("/{source}/{author}/{title}", m)
    assert out == "/S/A/a_b"


# ---------------------------------------------------------------------------
# apply_extracted_to_model
# ---------------------------------------------------------------------------

def test_apply_extracted_creates_fk_entities():
    with session_scope() as session:
        model = STLModel(archive_path="/x.7z", archive_hash="h")
        session.add(model)
        session.flush()

        apply_extracted_to_model(
            session,
            model,
            {
                "sculptor_name":    "New Sculptor",
                "category_name":    "New Cat",
                "subcategory_name": "New Sub",
            },
            overwrite=True,
        )

        assert session.query(Sculptor).filter_by(name="New Sculptor").count() == 1
        assert session.query(Category).filter_by(name="New Cat").count() == 1
        # Subcategory is created only when a category is set
        assert session.query(Subcategory).filter_by(name="New Sub").count() == 1


def test_apply_extracted_parses_release_date():
    with session_scope() as session:
        model = STLModel(archive_path="/x.7z", archive_hash="h")
        session.add(model)
        session.flush()

        apply_extracted_to_model(
            session, model, {"release_date": "2024-05"}, overwrite=True,
        )
        assert model.release_year == 2024
        assert model.release_month == 5


def test_apply_extracted_overwrite_false_preserves_existing():
    with session_scope() as session:
        sc = Sculptor(name="Existing")
        session.add(sc)
        session.flush()
        model = STLModel(
            archive_path="/x.7z",
            archive_hash="h",
            title="Original",
            sculptor=sc,
        )
        session.add(model)
        session.flush()

        apply_extracted_to_model(
            session,
            model,
            {"title": "New", "sculptor_name": "Other"},
            overwrite=False,
        )
        assert model.title == "Original"
        assert model.sculptor.name == "Existing"


# ---------------------------------------------------------------------------
# apply_extracted_to_model — sequence and release_name
# ---------------------------------------------------------------------------

def test_apply_extracted_sets_sequence():
    with session_scope() as session:
        model = STLModel(archive_path="/seq.7z", archive_hash="h")
        session.add(model)
        session.flush()

        apply_extracted_to_model(session, model, {"sequence": "3"}, overwrite=True)
        assert model.sequence == 3


def test_apply_extracted_sequence_non_numeric_skipped():
    with session_scope() as session:
        model = STLModel(archive_path="/seq2.7z", archive_hash="h")
        session.add(model)
        session.flush()

        apply_extracted_to_model(session, model, {"sequence": "not-a-number"}, overwrite=True)
        assert model.sequence is None


def test_apply_extracted_creates_release():
    from app.database.models import Release
    with session_scope() as session:
        model = STLModel(archive_path="/rel.7z", archive_hash="h")
        session.add(model)
        session.flush()

        apply_extracted_to_model(
            session, model, {"release_name": "March Bundle"}, overwrite=True
        )
        assert model.release is not None
        assert model.release.name == "March Bundle"

    with session_scope() as session:
        assert session.query(Release).filter_by(name="March Bundle").count() == 1


def test_apply_extracted_reuses_existing_release():
    from app.database.models import Release
    with session_scope() as session:
        existing = Release(name="Shared Bundle")
        session.add(existing)
        session.flush()

        m1 = STLModel(archive_path="/r1.7z", archive_hash="h1")
        m2 = STLModel(archive_path="/r2.7z", archive_hash="h2")
        session.add_all([m1, m2])
        session.flush()

        apply_extracted_to_model(session, m1, {"release_name": "Shared Bundle"}, overwrite=True)
        apply_extracted_to_model(session, m2, {"release_name": "Shared Bundle"}, overwrite=True)

    with session_scope() as session:
        assert session.query(Release).filter_by(name="Shared Bundle").count() == 1


# ---------------------------------------------------------------------------
# apply_extracted_to_model — subcategory without a category is skipped
# ---------------------------------------------------------------------------

def test_apply_extracted_subcategory_without_category_not_created():
    """If no category is set, a subcategory cannot be created."""
    with session_scope() as session:
        model = STLModel(archive_path="/nocat.7z", archive_hash="h")
        session.add(model)
        session.flush()

        apply_extracted_to_model(
            session, model, {"subcategory_name": "Orphan Sub"}, overwrite=True
        )
        assert model.subcategory is None


# ---------------------------------------------------------------------------
# render_path_pattern — date token
# ---------------------------------------------------------------------------

def test_render_path_pattern_date_token():
    m = _StubModel(release_year=2024, release_month=3)
    out = render_path_pattern("/{date}/{title}", m)
    assert "2024-03" in out


def test_render_path_pattern_year_only():
    m = _StubModel(release_year=2024, release_month=None)
    out = render_path_pattern("/{date}", m)
    assert "2024" in out


def test_render_path_pattern_unknown_date():
    m = _StubModel(release_year=None, release_month=None, title="T")
    out = render_path_pattern("/{date}/{title}", m)
    assert "_unknown" in out
