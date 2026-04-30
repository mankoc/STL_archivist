"""
Tests for app.crawler.metadata — path inference, _parse_info_json, and
the full extract_metadata pipeline.
"""
import json
from pathlib import Path
from unittest.mock import patch

from app.crawler.metadata import (
    ModelMetadata,
    _infer_from_path,
    _parse_info_json,
    extract_metadata,
)


def test_infer_title_from_stem():
    p = Path("/mnt/f/STL/SomeAuthor/cyborg_hulk.7z")
    meta = _infer_from_path(p)
    assert meta.title == "cyborg hulk"


def test_infer_creator_after_distributor():
    """Creator is inferred only from the folder directly after a distributor folder."""
    p = Path("/mnt/f/STL/Patreon/CalebNefzen/Cyborg-Hulk.7z")
    meta = _infer_from_path(p)
    assert meta.distributor == "Patreon"
    assert meta.creator == "CalebNefzen"


def test_creator_not_inferred_without_distributor():
    """Without a distributor signal the creator should remain unset."""
    p = Path("/mnt/f/STL/CalebNefzen/Cyborg-Hulk.7z")
    meta = _infer_from_path(p)
    assert meta.creator is None


def test_infer_patreon_type():
    p = Path("/mnt/f/STL/Patreon/SomeArtist/model.zip")
    meta = _infer_from_path(p)
    assert meta.distributor == "Patreon"


def test_infer_date():
    p = Path("/mnt/f/STL/2024-03/SomeModel.7z")
    meta = _infer_from_path(p)
    assert meta.release_date == "2024-03"


# ---------------------------------------------------------------------------
# _infer_from_path — extended cases
# ---------------------------------------------------------------------------

def test_infer_from_path_with_source_dir():
    """Only path components relative to source_dir are inspected."""
    source = Path("/mnt/f/STL")
    p = Path("/mnt/f/STL/Patreon/ArtistName/model.7z")
    meta = _infer_from_path(p, source_dir=source)
    assert meta.distributor == "Patreon"
    assert meta.creator == "ArtistName"
    assert meta.title == "model"


def test_infer_from_path_date_in_folder_name():
    p = Path("/mnt/f/STL/Patreon/Artist/2024-05/model.7z")
    meta = _infer_from_path(p)
    assert meta.release_date == "2024-05"


def test_infer_from_path_date_underscore_separator():
    p = Path("/mnt/f/STL/2024_11/model.7z")
    meta = _infer_from_path(p)
    assert meta.release_date == "2024_11"


def test_infer_distributor_kickstarter():
    p = Path("/mnt/f/STL/Kickstarter/ArtistK/model.7z")
    meta = _infer_from_path(p)
    assert meta.distributor == "Kickstarter"


def test_infer_distributor_myminifactory():
    p = Path("/mnt/f/STL/MyMiniFactory/ArtistM/model.7z")
    meta = _infer_from_path(p)
    assert meta.distributor == "MyMiniFactory"


def test_infer_distributor_gumroad():
    p = Path("/mnt/f/STL/Gumroad/ArtistG/model.7z")
    meta = _infer_from_path(p)
    assert meta.distributor == "Gumroad"


def test_infer_distributor_thingiverse():
    p = Path("/mnt/f/STL/Thingiverse/ArtistT/model.7z")
    meta = _infer_from_path(p)
    assert meta.distributor == "Thingiverse"


def test_infer_distributor_printables():
    p = Path("/mnt/f/STL/Printables/ArtistP/model.7z")
    meta = _infer_from_path(p)
    assert meta.distributor == "Printables"


def test_infer_title_strips_leading_date_prefix():
    """Stems like '2024-09 Cyborg' should not include the date in the title."""
    p = Path("/tmp/2024-09 Cyborg Hulk.7z")
    meta = _infer_from_path(p)
    assert "2024" not in meta.title


# ---------------------------------------------------------------------------
# _parse_info_json
# ---------------------------------------------------------------------------

def test_parse_info_json_valid():
    raw = json.dumps({"name": "Hulk", "author": "Artist"}).encode()
    result = _parse_info_json(raw)
    assert result["name"] == "Hulk"
    assert result["author"] == "Artist"


def test_parse_info_json_invalid_returns_empty():
    assert _parse_info_json(b"not json {{{") == {}


def test_parse_info_json_empty_bytes_returns_empty():
    assert _parse_info_json(b"") == {}


def test_parse_info_json_latin1_decoded_with_replacement():
    raw = b'{"name": "caf\xe9"}'
    result = _parse_info_json(raw)
    assert "name" in result


# ---------------------------------------------------------------------------
# extract_metadata — full pipeline
# ---------------------------------------------------------------------------

def test_extract_metadata_info_json_overrides_path_title():
    info = {"name": "JSON Title", "author": "JSON Author"}
    contents = {"info.json": json.dumps(info).encode()}
    with patch("app.crawler.archive.find_url_file", return_value=None):
        meta = extract_metadata(Path("/tmp/path_title.zip"), contents=contents)
    assert meta.title == "JSON Title"
    assert meta.creator == "JSON Author"


def test_extract_metadata_info_json_tags():
    info = {"name": "Tagged", "tags": ["fantasy", "warrior"]}
    contents = {"info.json": json.dumps(info).encode()}
    with patch("app.crawler.archive.find_url_file", return_value=None):
        meta = extract_metadata(Path("/tmp/model.zip"), contents=contents)
    assert "fantasy" in meta.tags
    assert "warrior" in meta.tags


def test_extract_metadata_info_json_description_and_release():
    info = {"description": "A great model", "release": "Bundle March 2024"}
    contents = {"info.json": json.dumps(info).encode()}
    with patch("app.crawler.archive.find_url_file", return_value=None):
        meta = extract_metadata(Path("/tmp/model.zip"), contents=contents)
    assert meta.description == "A great model"
    assert meta.release == "Bundle March 2024"


def test_extract_metadata_url_from_contents():
    """URL found in archive contents is used when filesystem has nothing."""
    url_content = b"[InternetShortcut]\r\nURL=https://patreon.com/artist\r\n"
    contents = {"source.url": url_content}
    with patch("app.crawler.archive.find_url_file", return_value=None):
        meta = extract_metadata(Path("/tmp/model.zip"), contents=contents)
    assert meta.url == "https://patreon.com/artist"


def test_extract_metadata_info_json_url_overrides_url_file():
    """info.json URL has higher priority than the .url shortcut."""
    info = {"url": "https://info-json-url.com"}
    url_content = b"[InternetShortcut]\r\nURL=https://url-file.com\r\n"
    contents = {
        "info.json": json.dumps(info).encode(),
        "source.url": url_content,
    }
    with patch("app.crawler.archive.find_url_file", return_value=None):
        meta = extract_metadata(Path("/tmp/model.zip"), contents=contents)
    assert meta.url == "https://info-json-url.com"


def test_extract_metadata_no_contents_falls_back_to_path():
    p = Path("/mnt/STL/Patreon/ArtistX/2023-12/robot.zip")
    with patch("app.crawler.archive.find_url_file", return_value=None):
        meta = extract_metadata(p, contents={})
    assert meta.distributor == "Patreon"
    assert meta.creator == "ArtistX"
    assert meta.title == "robot"
    assert meta.release_date == "2023-12"


def test_extract_metadata_returns_model_metadata_instance():
    with patch("app.crawler.archive.find_url_file", return_value=None):
        meta = extract_metadata(Path("/tmp/any.zip"), contents={})
    assert isinstance(meta, ModelMetadata)
