"""
Tests for app.crawler.archive utility functions.

Covers: is_archive, _is_wanted, _member_size_limit, iter_archives,
        list_contents, read_file, read_url_file, _parse_url_shortcut,
        find_url_file, extract_archive_contents (incl. all_names_out),
        archive_hash (stable, fallback, content-differentiation).
"""
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.crawler.archive import (
    METADATA_FILENAME,
    _is_wanted,
    _member_size_limit,
    _parse_url_shortcut,
    archive_hash,
    extract_archive_contents,
    find_url_file,
    is_archive,
    iter_archives,
    list_contents,
    read_file,
    read_url_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_zip(tmp_path: Path, name: str = "test.zip",
              members: list | None = None) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for member_name, data in (members or [("placeholder.txt", b"x")]):
            zf.writestr(member_name, data)
    p = tmp_path / name
    p.write_bytes(buf.getvalue())
    return p


# ---------------------------------------------------------------------------
# is_archive
# ---------------------------------------------------------------------------

def test_is_archive_zip():
    assert is_archive(Path("model.zip")) is True


def test_is_archive_7z():
    assert is_archive(Path("model.7z")) is True


def test_is_archive_rar():
    assert is_archive(Path("model.rar")) is True


def test_is_archive_case_insensitive():
    assert is_archive(Path("model.ZIP")) is True
    assert is_archive(Path("model.7Z")) is True
    assert is_archive(Path("model.RAR")) is True


def test_is_archive_stl_is_false():
    assert is_archive(Path("model.stl")) is False


def test_is_archive_jpg_is_false():
    assert is_archive(Path("preview.jpg")) is False


def test_is_archive_no_extension():
    assert is_archive(Path("README")) is False


# ---------------------------------------------------------------------------
# _is_wanted
# ---------------------------------------------------------------------------

def test_is_wanted_image_suffixes():
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        assert _is_wanted(f"preview{ext}") is True
        assert _is_wanted(f"PREVIEW{ext.upper()}") is True


def test_is_wanted_url_file():
    assert _is_wanted("source.url") is True


def test_is_wanted_info_json_at_root():
    assert _is_wanted("info.json") is True


def test_is_wanted_info_json_case_insensitive():
    assert _is_wanted("INFO.JSON") is True


def test_is_wanted_info_json_in_subdir():
    assert _is_wanted("subdir/info.json") is True


def test_is_wanted_stl_is_false():
    assert _is_wanted("model.stl") is False


def test_is_wanted_txt_is_false():
    assert _is_wanted("readme.txt") is False


def test_is_wanted_backslash_path_normalised():
    assert _is_wanted("subdir\\preview.jpg") is True


# ---------------------------------------------------------------------------
# _member_size_limit
# ---------------------------------------------------------------------------

def test_member_size_limit_image_is_large():
    from app.crawler.archive import _SIZE_LIMIT_IMAGE, _SIZE_LIMIT_META
    assert _member_size_limit("preview.jpg") == _SIZE_LIMIT_IMAGE
    assert _member_size_limit("THUMB.PNG") == _SIZE_LIMIT_IMAGE
    assert _member_size_limit("render.webp") == _SIZE_LIMIT_IMAGE


def test_member_size_limit_meta_is_small():
    from app.crawler.archive import _SIZE_LIMIT_META
    assert _member_size_limit("info.json") == _SIZE_LIMIT_META
    assert _member_size_limit("source.url") == _SIZE_LIMIT_META


# ---------------------------------------------------------------------------
# iter_archives
# ---------------------------------------------------------------------------

def test_iter_archives_finds_zip_and_7z(tmp_path):
    (tmp_path / "model.zip").write_bytes(b"PK")
    (tmp_path / "model.7z").write_bytes(b"7z")
    (tmp_path / "readme.txt").write_bytes(b"text")
    found = {p.name for p in iter_archives(tmp_path)}
    assert "model.zip" in found
    assert "model.7z" in found
    assert "readme.txt" not in found


def test_iter_archives_excludes_metadata_sidecar(tmp_path):
    (tmp_path / METADATA_FILENAME).write_bytes(b"7z")
    (tmp_path / "model.7z").write_bytes(b"7z")
    found = [p.name for p in iter_archives(tmp_path)]
    assert METADATA_FILENAME not in found
    assert "model.7z" in found


def test_iter_archives_recurses_into_subdirs(tmp_path):
    sub = tmp_path / "artist" / "release"
    sub.mkdir(parents=True)
    (sub / "deep.zip").write_bytes(b"PK")
    found = [p.name for p in iter_archives(tmp_path)]
    assert "deep.zip" in found


def test_iter_archives_empty_dir(tmp_path):
    assert list(iter_archives(tmp_path)) == []


# ---------------------------------------------------------------------------
# list_contents
# ---------------------------------------------------------------------------

def test_list_contents_zip_returns_all_members(tmp_path):
    p = _make_zip(tmp_path, members=[("a.txt", b"a"), ("b.jpg", b"b")])
    names = list_contents(p)
    assert set(names) == {"a.txt", "b.jpg"}


def test_list_contents_unknown_extension_returns_empty(tmp_path):
    p = tmp_path / "archive.xyz"
    p.write_bytes(b"garbage")
    assert list_contents(p) == []


def test_list_contents_corrupted_zip_returns_empty(tmp_path):
    p = tmp_path / "bad.zip"
    p.write_bytes(b"this is not a valid zip file")
    assert list_contents(p) == []


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def test_read_file_zip_returns_bytes(tmp_path):
    payload = b"\xff\xd8\xff" + b"\x00" * 20
    p = _make_zip(tmp_path, members=[("preview.jpg", payload)])
    data = read_file(p, "preview.jpg")
    assert data == payload


def test_read_file_missing_member_returns_none(tmp_path):
    p = _make_zip(tmp_path, members=[("a.txt", b"data")])
    assert read_file(p, "nonexistent.jpg") is None


# ---------------------------------------------------------------------------
# read_url_file
# ---------------------------------------------------------------------------

def test_read_url_file_from_zip(tmp_path):
    url_content = b"[InternetShortcut]\r\nURL=https://example.com/model\r\n"
    p = _make_zip(tmp_path, members=[("source.url", url_content)])
    assert read_url_file(p) == "https://example.com/model"


def test_read_url_file_case_insensitive_key(tmp_path):
    url_content = b"[InternetShortcut]\r\nurl=https://patreon.com/artist\r\n"
    p = _make_zip(tmp_path, members=[("link.url", url_content)])
    assert read_url_file(p) == "https://patreon.com/artist"


def test_read_url_file_not_in_archive_returns_none(tmp_path):
    p = _make_zip(tmp_path, members=[("model.stl", b"solid")])
    assert read_url_file(p) is None


# ---------------------------------------------------------------------------
# _parse_url_shortcut
# ---------------------------------------------------------------------------

def test_parse_url_shortcut_valid_file(tmp_path):
    f = tmp_path / "source.url"
    f.write_text("[InternetShortcut]\nURL=https://myminifactory.com/user\n")
    assert _parse_url_shortcut(f) == "https://myminifactory.com/user"


def test_parse_url_shortcut_no_url_line(tmp_path):
    f = tmp_path / "empty.url"
    f.write_text("[InternetShortcut]\n")
    assert _parse_url_shortcut(f) is None


def test_parse_url_shortcut_missing_file(tmp_path):
    assert _parse_url_shortcut(tmp_path / "nonexistent.url") is None


def test_parse_url_shortcut_leading_whitespace_stripped(tmp_path):
    f = tmp_path / "ws.url"
    f.write_text("[InternetShortcut]\n  URL=  https://cults3d.com/x  \n")
    result = _parse_url_shortcut(f)
    assert result == "https://cults3d.com/x"


# ---------------------------------------------------------------------------
# find_url_file
# ---------------------------------------------------------------------------

def test_find_url_file_inside_archive(tmp_path):
    """URL found in archive takes precedence over filesystem."""
    url_content = b"[InternetShortcut]\r\nURL=https://archive-url.com\r\n"
    p = _make_zip(tmp_path, members=[("link.url", url_content)])
    result = find_url_file(p)
    assert result == "https://archive-url.com"


def test_find_url_file_same_dir_fallback(tmp_path):
    """A .url file in the same directory is used when the archive has none."""
    p = _make_zip(tmp_path, members=[("model.stl", b"solid")])
    (tmp_path / "source.url").write_text(
        "[InternetShortcut]\nURL=https://cults3d.com/model\n"
    )
    result = find_url_file(p)
    assert result == "https://cults3d.com/model"


def test_find_url_file_grandparent_not_searched(tmp_path):
    """A .url file one level above the archive's parent is NOT returned (search is restricted)."""
    sub = tmp_path / "release"
    sub.mkdir()
    p = _make_zip(sub, members=[("model.stl", b"solid")])
    (tmp_path / "artist.url").write_text(
        "[InternetShortcut]\nURL=https://patreon.com/artist\n"
    )
    assert find_url_file(p) is None


def test_find_url_file_links_folder_fallback(tmp_path):
    """A .url file in a *-links sibling folder is used."""
    links_dir = tmp_path / "Patreon-links"
    links_dir.mkdir()
    (links_dir / "artist.url").write_text(
        "[InternetShortcut]\nURL=https://patreon.com/artist\n"
    )
    p = _make_zip(tmp_path, members=[("model.stl", b"solid")])
    result = find_url_file(p)
    assert result == "https://patreon.com/artist"


def test_find_url_file_returns_none_when_nothing_found(tmp_path):
    # Place the archive 3 levels deep so that grandparent lookups stay within
    # tmp_path and don't accidentally discover .url files from other tests.
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    p = _make_zip(deep, members=[("model.stl", b"solid")])
    assert find_url_file(p) is None


# ---------------------------------------------------------------------------
# extract_archive_contents — all_names_out
# ---------------------------------------------------------------------------

def test_extract_archive_contents_all_names_out(tmp_path):
    """all_names_out is populated with every member, not just wanted ones."""
    members = [
        ("preview.jpg", b"\xff\xd8\xff" + b"\x00" * 10),
        ("info.json",   b'{"name": "test"}'),
        ("model.stl",   b"solid test\n"),
    ]
    p = _make_zip(tmp_path, members=members)

    all_names: list[str] = []
    contents = extract_archive_contents(p, all_names_out=all_names)

    assert set(all_names) == {"preview.jpg", "info.json", "model.stl"}
    assert "preview.jpg" in contents
    assert "info.json" in contents
    assert "model.stl" not in contents


def test_extract_archive_contents_no_all_names_out_arg(tmp_path):
    """Calling without all_names_out still returns the wanted files."""
    p = _make_zip(tmp_path, members=[
        ("thumb.jpg", b"\xff\xd8\xff" + b"\x00" * 5),
        ("model.stl", b"solid"),
    ])
    contents = extract_archive_contents(p)
    assert "thumb.jpg" in contents
    assert "model.stl" not in contents


def test_extract_archive_contents_oversized_member_skipped(tmp_path):
    """Members exceeding the size limit are silently skipped."""
    p = _make_zip(tmp_path, members=[
        ("preview.jpg", b"\xff\xd8\xff" + b"\x00" * 100),
    ])
    with patch("app.crawler.archive._member_size_limit", return_value=0):
        contents = extract_archive_contents(p)
    assert "preview.jpg" not in contents


def test_extract_archive_contents_corrupted_archive_returns_empty(tmp_path):
    p = tmp_path / "bad.zip"
    p.write_bytes(b"not a valid archive")
    assert extract_archive_contents(p) == {}


def test_extract_archive_contents_empty_archive(tmp_path):
    p = _make_zip(tmp_path, members=[])
    assert extract_archive_contents(p) == {}


# ---------------------------------------------------------------------------
# archive_hash
# ---------------------------------------------------------------------------

def test_archive_hash_stable_across_calls(tmp_path):
    p = _make_zip(tmp_path, members=[("a.txt", b"alpha"), ("b.txt", b"beta")])
    h1 = archive_hash(p)
    h2 = archive_hash(p)
    assert h1 == h2
    assert isinstance(h1, str)
    assert len(h1) > 0


def test_archive_hash_differs_for_different_content(tmp_path):
    p1 = _make_zip(tmp_path, "a.zip", [("file.txt", b"content-one")])
    p2 = _make_zip(tmp_path, "b.zip", [("file.txt", b"content-two")])
    assert archive_hash(p1) != archive_hash(p2)


def test_archive_hash_fallback_for_corrupted_archive(tmp_path):
    p = tmp_path / "broken.zip"
    p.write_bytes(b"not a real zip")
    h = archive_hash(p)
    assert h.startswith("fallback:")
