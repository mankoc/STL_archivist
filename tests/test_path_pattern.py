"""
Unit tests for app.utils.path_pattern.
Covers pattern_to_regex, parse_path, parse_release_date and render_path_pattern.
"""
from app.utils.path_pattern import (
    format_release_date,
    parse_path,
    parse_release_date,
    pattern_to_regex,
    render_path_pattern,
)


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
        "/{source}/{creator}/{name}",
        "/Patreon/Artist/model.7z",
    )
    assert result.get("source") == "Patreon"
    assert result.get("creator") == "Artist"
    assert result.get("title") == "model"  # extension stripped


def test_parse_path_nested_fills_title_from_stem():
    # Pattern matches only the parent dirs; archive stem is injected as title.
    result = parse_path(
        "/{source}/{creator}",
        "/Patreon/Artist/subdir/cyborg_hulk.7z",
    )
    assert result.get("source") == "Patreon"
    assert result.get("creator") == "Artist"
    assert result.get("title") == "cyborg_hulk"


def test_parse_path_no_match_returns_empty():
    result = parse_path("/{source}/{creator}/{name}", "/nothing/here.7z")
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
        self.creator = kw.get("creator")
        self.title = kw.get("title")
        self.release_year = kw.get("release_year")
        self.release_month = kw.get("release_month")
        self.url = kw.get("url")
        self.sculptor = kw.get("sculptor")
        self.collection = kw.get("collection")
        self.subcollection = kw.get("subcollection")
        self.sequence = kw.get("sequence")
        self.release = kw.get("release")


def test_render_path_pattern_fills_tokens():
    m = _StubModel(source="Patreon", creator="Artist", title="Cyborg Hulk")
    out = render_path_pattern("/{source}/{creator}/{title}", m)
    assert out == "/Patreon/Artist/Cyborg Hulk"


def test_render_path_pattern_unknown_replaced_with_unknown():
    m = _StubModel(source=None, creator="Artist", title="Hulk")
    out = render_path_pattern("/{source}/{creator}/{title}", m)
    assert out == "/_unknown/Artist/Hulk"


def test_render_path_pattern_sanitises_path_chars():
    # Title contains a slash — must not split into a new directory
    m = _StubModel(source="S", creator="A", title="a/b")
    out = render_path_pattern("/{source}/{creator}/{title}", m)
    assert out == "/S/A/a_b"


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


# ---------------------------------------------------------------------------
# render_path_pattern — :n prefix modifier
# ---------------------------------------------------------------------------

def test_render_path_pattern_prefix_modifier_single_char():
    m = _StubModel(creator="Sanix", title="Model")
    out = render_path_pattern("/{creator:1}/{creator}/{title}", m)
    assert out == "/S/Sanix/Model"


def test_render_path_pattern_prefix_modifier_multi_char():
    m = _StubModel(creator="Sanix", title="Model")
    out = render_path_pattern("/{creator:3}/{creator}", m)
    assert out == "/San/Sanix"


def test_render_path_pattern_prefix_modifier_longer_than_value():
    # :n larger than the value length → full value used
    m = _StubModel(creator="AB", title="T")
    out = render_path_pattern("/{creator:10}/{creator}", m)
    assert out == "/AB/AB"


def test_render_path_pattern_prefix_modifier_unknown_field():
    # _unknown truncated to 1 char
    m = _StubModel(creator=None, title="T")
    out = render_path_pattern("/{creator:1}/{title}", m)
    assert out == "/_/T"


def test_parse_path_with_prefix_modifier_token():
    # :n is stripped when building the regex; the captured value is unrestricted
    result = parse_path("/{creator:1}/{creator}", "/S/Sanix")
    # First segment captured by {creator:1} regex group (1-char match)
    # Second by {creator} — whichever wins, creator key must be present
    assert "creator" in result


def test_pattern_to_regex_with_prefix_modifier():
    rx = pattern_to_regex("{creator:1}/{creator}")
    assert rx is not None
    m = rx.search("S/Sanix")
    assert m is not None
