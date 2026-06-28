"""Unit tests for the merge-duplicates pair-finding helpers."""
from app.utils.name_dedup import find_similar_pairs, find_similar_pairs_grouped
from app.utils.string_utils import soundex


# --- flat: Levenshtein -----------------------------------------------------

def test_flat_levenshtein_pairs_within_threshold():
    items = [(1, "cat", 5), (2, "car", 3), (3, "dog", 1)]
    pairs = find_similar_pairs(items, "Levenshtein", threshold=1)
    assert len(pairs) == 1
    a, b, score = pairs[0]
    assert {a[0], b[0]} == {1, 2}
    assert score == "1"


def test_flat_levenshtein_excludes_beyond_threshold():
    items = [(1, "cat", 1), (2, "dog", 1)]
    assert find_similar_pairs(items, "Levenshtein", threshold=2) == []


def test_flat_levenshtein_sorted_by_distance():
    items = [(1, "abcd", 1), (2, "abcf", 1), (3, "abcde", 1)]
    pairs = find_similar_pairs(items, "Levenshtein", threshold=2)
    scores = [int(p[2]) for p in pairs]
    assert scores == sorted(scores)


# --- flat: Soundex ---------------------------------------------------------

def test_flat_soundex_pairs_same_code():
    # Robert / Rupert share Soundex R163; Apple does not.
    items = [(1, "Robert", 2), (2, "Rupert", 1), (3, "Apple", 4)]
    pairs = find_similar_pairs(items, "Soundex", threshold=2)
    assert len(pairs) == 1
    a, b, code = pairs[0]
    assert {a[0], b[0]} == {1, 2}
    assert code == soundex("Robert")


def test_flat_soundex_no_pairs_when_all_distinct():
    items = [(1, "Robert", 1), (2, "Apple", 1), (3, "Xylophone", 1)]
    assert find_similar_pairs(items, "Soundex") == []


# --- grouped ---------------------------------------------------------------

def test_grouped_only_pairs_within_same_parent():
    groups = {
        10: ("Fantasy", [(1, "orc", 2), (2, "ork", 1)]),
        20: ("Sci-Fi", [(3, "orc", 5)]),   # same name, different parent
    }
    pairs = find_similar_pairs_grouped(groups, "Levenshtein", threshold=1)
    # The cross-parent "orc"/"orc" must NOT pair; only the in-parent orc/ork does.
    assert len(pairs) == 1
    a, b, parent_name, score = pairs[0]
    assert parent_name == "Fantasy"
    assert {a[0], b[0]} == {1, 2}


def test_grouped_includes_parent_name_and_soundex():
    groups = {10: ("Comics", [(1, "Robert", 1), (2, "Rupert", 1)])}
    pairs = find_similar_pairs_grouped(groups, "Soundex")
    assert len(pairs) == 1
    _, _, parent_name, code = pairs[0]
    assert parent_name == "Comics"
    assert code == soundex("Robert")


def test_grouped_empty():
    assert find_similar_pairs_grouped({}, "Levenshtein") == []
