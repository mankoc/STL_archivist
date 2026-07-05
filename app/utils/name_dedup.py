"""Pure helpers for finding similar-named lookup entries (merge-duplicates UI).

The Category/Collection/Subcategory/Subcollection editor dialogs each implemented
the same Levenshtein/Soundex pair-finding logic. That algorithm lives here so it
is written once and unit-tested; the dialogs keep their own (translatable) Qt/UI
code and only call into these functions. No Qt or DB dependencies.

Each entry is a ``(id, name, count)`` tuple. ``method`` is ``"Levenshtein"`` or
``"Soundex"`` (any other value is treated as Soundex).
"""
from __future__ import annotations

from app.utils.string_utils import levenshtein, soundex


def find_similar_pairs(items, method, threshold=2):
    """Return similar ``(id, name, count)`` entries as candidate merge pairs.

    Args:
        items: Iterable of ``(id, name, count)`` tuples.
        method: ``"Levenshtein"`` (pairs within edit distance *threshold*) or
            ``"Soundex"`` (pairs sharing a Soundex code).
        threshold: Maximum edit distance, used only for the Levenshtein method.

    Returns:
        For Levenshtein: ``[(item_a, item_b, str(distance)), …]`` sorted by
        distance. For Soundex: ``[(item_a, item_b, soundex_code), …]`` sorted by
        code.
    """
    items = list(items)
    pairs: list[tuple] = []
    if method == "Levenshtein":
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                d = levenshtein(items[i][1], items[j][1])
                if d <= threshold:
                    pairs.append((items[i], items[j], str(d)))
        pairs.sort(key=lambda x: int(x[2]))
    else:  # Soundex
        codes = {it[0]: soundex(it[1]) for it in items}
        groups: dict[str, list] = {}
        for it in items:
            groups.setdefault(codes[it[0]], []).append(it)
        for code, members in groups.items():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    pairs.append((members[i], members[j], code))
        pairs.sort(key=lambda x: x[2])
    return pairs


def find_similar_pairs_grouped(groups, method, threshold=2):
    """Like :func:`find_similar_pairs` but only pairs entries within the same parent.

    Used by the Subcategory/Subcollection editors, where duplicates are only
    meaningful inside a shared parent Category/Collection.

    Args:
        groups: Mapping of ``parent_id -> (parent_name, [(id, name, count), …])``.
        method: ``"Levenshtein"`` or ``"Soundex"`` (see :func:`find_similar_pairs`).
        threshold: Maximum edit distance for the Levenshtein method.

    Returns:
        ``[(item_a, item_b, parent_name, score), …]`` where *score* is the
        distance string (Levenshtein) or Soundex code, sorted by score.
    """
    pairs: list[tuple] = []
    if method == "Levenshtein":
        for parent_name, members in groups.values():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    d = levenshtein(members[i][1], members[j][1])
                    if d <= threshold:
                        pairs.append((members[i], members[j], parent_name, str(d)))
        pairs.sort(key=lambda x: int(x[3]))
    else:  # Soundex
        for parent_name, members in groups.values():
            sgroups: dict[str, list] = {}
            for m in members:
                sgroups.setdefault(soundex(m[1]), []).append(m)
            for smembers in sgroups.values():
                for i in range(len(smembers)):
                    for j in range(i + 1, len(smembers)):
                        pairs.append((smembers[i], smembers[j], parent_name,
                                      soundex(smembers[i][1])))
        pairs.sort(key=lambda x: x[3])
    return pairs
