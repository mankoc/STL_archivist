"""
Tests for filter cache functionality, including folder tree caching and building.
"""
import pytest

from app.database.filter_cache import (
    save_filter_cache,
    load_filter_cache,
    invalidate_filter_cache,
    get_folder_tree,
    build_folder_tree,
)
from tests import factory


# ---------------------------------------------------------------------------
# build_folder_tree
# ---------------------------------------------------------------------------

def test_build_folder_tree_empty():
    """Building a tree from an empty list returns an empty list."""
    tree = build_folder_tree([])
    assert tree == []


def test_build_folder_tree_single_folder():
    """A single folder creates one root node with no children."""
    tree = build_folder_tree(["C:/Archives"])
    assert len(tree) == 1
    assert tree[0]["name"] == "C:"
    assert tree[0]["path"] == "C:"
    assert len(tree[0]["children"]) == 1
    assert tree[0]["children"][0]["name"] == "Archives"


def test_build_folder_tree_nested():
    """Nested folders create a proper hierarchy."""
    folders = [
        "C:/Archives/Collection1/Subfolder1",
        "C:/Archives/Collection1/Subfolder2",
        "C:/Archives/Collection2",
    ]
    tree = build_folder_tree(folders)

    assert len(tree) == 1
    assert tree[0]["name"] == "C:"

    archives_node = tree[0]["children"][0]
    assert archives_node["name"] == "Archives"
    assert len(archives_node["children"]) == 2

    col1 = archives_node["children"][0]
    assert col1["name"] == "Collection1"
    assert len(col1["children"]) == 2

    col2 = archives_node["children"][1]
    assert col2["name"] == "Collection2"
    assert len(col2["children"]) == 0


def test_build_folder_tree_multiple_drives():
    """Multiple drive letters create multiple root nodes."""
    folders = [
        "C:/Archives",
        "D:/Models",
        "C:/Other",
    ]
    tree = build_folder_tree(folders)

    assert len(tree) == 2
    drives = sorted([node["name"] for node in tree])
    assert drives == ["C:", "D:"]


def test_build_folder_tree_normalized_separators():
    """Backslash separators are normalized to forward slashes."""
    folders = [
        "C:\\Archives\\Collection1",
        "C:/Archives/Collection1/Subfolder",
    ]
    tree = build_folder_tree(folders)

    # Both should be treated as the same path
    col1 = tree[0]["children"][0]["children"][0]
    assert col1["name"] == "Collection1"
    assert len(col1["children"]) == 1
    assert col1["children"][0]["name"] == "Subfolder"


def test_build_folder_tree_skips_empty_parts():
    """Double slashes and empty parts are skipped."""
    folders = ["C://Archives//Collection1"]
    tree = build_folder_tree(folders)

    # Should normalize to C:/Archives/Collection1
    assert tree[0]["children"][0]["name"] == "Archives"


def test_build_folder_tree_stores_full_path():
    """Each node stores its full normalized path in the 'path' field."""
    folders = ["C:/Archives/Collection1/Subfolder"]
    tree = build_folder_tree(folders)

    drive = tree[0]
    assert drive["path"] == "C:"

    archives = drive["children"][0]
    assert archives["path"] == "C:/Archives"

    collection = archives["children"][0]
    assert collection["path"] == "C:/Archives/Collection1"

    subfolder = collection["children"][0]
    assert subfolder["path"] == "C:/Archives/Collection1/Subfolder"


# ---------------------------------------------------------------------------
# save_filter_cache and load_filter_cache
# ---------------------------------------------------------------------------

def test_save_and_load_empty_cache():
    """Saving and loading an empty payload works."""
    payload = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": [],
        "folder_tree": [],
    }

    save_filter_cache(payload)

    loaded = load_filter_cache()
    assert loaded is not None
    assert loaded["folder_tree"] == []


def test_save_and_load_with_folder_tree():
    """Saving and loading a payload with folder_tree preserves the tree."""
    tree = build_folder_tree([
        "C:/Archives/Collection1",
        "C:/Archives/Collection2",
    ])

    payload = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": ["C:/Archives/Collection1", "C:/Archives/Collection2"],
        "folder_tree": tree,
    }

    save_filter_cache(payload)

    loaded = load_filter_cache()
    assert loaded is not None
    assert len(loaded["folder_tree"]) == 1
    assert loaded["folder_tree"][0]["name"] == "C:"
    assert len(loaded["folder_tree"][0]["children"]) == 1
    assert len(loaded["folder_tree"][0]["children"][0]["children"]) == 2


def test_load_cache_returns_none_when_empty():
    """load_filter_cache returns None when no cache exists."""
    loaded = load_filter_cache()
    assert loaded is None


def test_invalidate_filter_cache():
    """invalidate_filter_cache removes the cached row."""
    payload = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": [],
        "folder_tree": [],
    }

    save_filter_cache(payload)
    assert load_filter_cache() is not None

    invalidate_filter_cache()
    assert load_filter_cache() is None


# ---------------------------------------------------------------------------
# get_folder_tree
# ---------------------------------------------------------------------------

def test_get_folder_tree_from_cache():
    """get_folder_tree retrieves the folder_tree from cached payload."""
    tree = build_folder_tree(["C:/Test"])

    payload = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": ["C:/Test"],
        "folder_tree": tree,
    }

    save_filter_cache(payload)

    cached_tree = get_folder_tree()
    assert cached_tree is not None
    assert len(cached_tree) == 1
    assert cached_tree[0]["name"] == "C:"


def test_get_folder_tree_returns_none_when_not_cached():
    """get_folder_tree returns None when cache doesn't exist."""
    cached_tree = get_folder_tree()
    assert cached_tree is None


def test_get_folder_tree_returns_none_when_empty_in_cache():
    """get_folder_tree returns None when folder_tree in cache is empty."""
    payload = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": [],
        "folder_tree": [],
    }

    save_filter_cache(payload)

    cached_tree = get_folder_tree()
    assert cached_tree is None


# ---------------------------------------------------------------------------
# Filter cache with database
# ---------------------------------------------------------------------------

def test_filter_cache_stored_in_database():
    """Saved cache is actually stored in the FilterCache table."""
    payload = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": [],
        "folder_tree": [],
    }

    save_filter_cache(payload)

    cache_row = factory.row("filter_cache", 1)
    assert cache_row is not None
    assert cache_row["payload"] is not None
    assert cache_row["updated_at"] is not None


def test_save_overwrites_existing_cache():
    """Saving a new payload overwrites the previous one."""
    payload1 = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": ["C:/First"],
        "folder_tree": build_folder_tree(["C:/First"]),
    }

    save_filter_cache(payload1)
    loaded1 = load_filter_cache()
    assert "C:" in str(loaded1["sorted_dirs"])

    payload2 = {
        "counts": {},
        "path_counts": {},
        "creators": [],
        "tags": [],
        "sources": [],
        "dates": [],
        "sculptors": [],
        "collections": [],
        "sub_by_col": [],
        "categories": [],
        "releases": [],
        "scales": [],
        "presupports": [],
        "sorted_dirs": ["D:/Second"],
        "folder_tree": build_folder_tree(["D:/Second"]),
    }

    save_filter_cache(payload2)
    loaded2 = load_filter_cache()
    assert loaded2["sorted_dirs"] == ["D:/Second"]


# ---------------------------------------------------------------------------
# Integration with STLModel
# ---------------------------------------------------------------------------

def test_folder_tree_from_database_archives():
    """Build folder tree from actual archive_folder values in database."""
    factory.add_model("/path/to/model1.zip", title="Model1", archive_folder="C:/Archives/Collection1")
    factory.add_model("/path/to/model2.zip", title="Model2", archive_folder="C:/Archives/Collection1")
    factory.add_model("/path/to/model3.zip", title="Model3", archive_folder="C:/Archives/Collection2")

    folders = factory.rows("SELECT DISTINCT archive_folder FROM models WHERE archive_folder IS NOT NULL")
    folder_list = [f[0] for f in folders]
    tree = build_folder_tree(folder_list)

    assert len(tree) == 1
    assert tree[0]["name"] == "C:"
    archives = tree[0]["children"][0]
    assert archives["name"] == "Archives"
    assert len(archives["children"]) == 2
