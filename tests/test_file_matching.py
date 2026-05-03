"""
Tests for app.utils.file_matching.match_files.
"""
import types

from app.utils.file_matching import match_files


def _file(filename: str, size: int | None = None, archive_hash: str | None = None):
    """Build a minimal STLFile-like object without touching the DB."""
    f = types.SimpleNamespace(
        filename=filename,
        file_size=size,
        archive_hash=archive_hash,
    )
    return f


# ---------------------------------------------------------------------------
# Filename matching
# ---------------------------------------------------------------------------

def test_match_by_filename_pairs_identical_basenames():
    a = [_file("dir/model.stl"), _file("other.stl")]
    b = [_file("nested/model.stl"), _file("other.stl")]
    rows = match_files(a, b, key="filename")
    matched = [(l.filename, r.filename) for l, r in rows if l and r]
    assert ("dir/model.stl", "nested/model.stl") in matched
    assert ("other.stl", "other.stl") in matched


def test_match_by_filename_unmatched_appear_as_none():
    a = [_file("only_in_a.stl")]
    b = [_file("only_in_b.stl")]
    rows = match_files(a, b, key="filename")
    lefts  = [l.filename for l, r in rows if l and r is None]
    rights = [r.filename for l, r in rows if l is None and r]
    assert "only_in_a.stl" in lefts
    assert "only_in_b.stl" in rights


def test_match_by_filename_case_insensitive():
    a = [_file("MODEL.STL")]
    b = [_file("model.stl")]
    rows = match_files(a, b, key="filename")
    matched = [(l, r) for l, r in rows if l and r]
    assert len(matched) == 1


# ---------------------------------------------------------------------------
# Size matching
# ---------------------------------------------------------------------------

def test_match_by_size_pairs_equal_sizes():
    a = [_file("a.stl", size=1024)]
    b = [_file("b.stl", size=1024)]
    rows = match_files(a, b, key="size")
    assert len([r for r in rows if r[0] and r[1]]) == 1


def test_match_by_size_skips_none_size():
    a = [_file("a.stl", size=None)]
    b = [_file("b.stl", size=None)]
    rows = match_files(a, b, key="size")
    # Both have None size — nothing matched
    matched = [(l, r) for l, r in rows if l and r]
    assert matched == []


# ---------------------------------------------------------------------------
# Hash matching
# ---------------------------------------------------------------------------

def test_match_by_hash_pairs_identical_hashes():
    a = [_file("a.stl", archive_hash="abc123")]
    b = [_file("b.stl", archive_hash="abc123")]
    rows = match_files(a, b, key="hash")
    matched = [(l, r) for l, r in rows if l and r]
    assert len(matched) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_inputs():
    assert match_files([], [], key="filename") == []


def test_empty_b_all_unmatched():
    a = [_file("x.stl")]
    rows = match_files(a, [], key="filename")
    assert rows == [(a[0], None)]


def test_empty_a_all_unmatched():
    b = [_file("x.stl")]
    rows = match_files([], b, key="filename")
    assert rows == [(None, b[0])]


def test_greedy_first_match_wins():
    """First matching file in B is consumed; second stays unmatched."""
    a = [_file("x.stl")]
    b = [_file("x.stl"), _file("x.stl")]
    rows = match_files(a, b, key="filename")
    matched   = [(l, r) for l, r in rows if l and r]
    unmatched = [(l, r) for l, r in rows if l is None or r is None]
    assert len(matched) == 1
    assert len(unmatched) == 1
