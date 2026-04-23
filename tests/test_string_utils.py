"""
Tests for app.utils.string_utils — Levenshtein distance and Soundex.

Levenshtein: identity, empty strings, insertions, deletions, substitutions,
             classic example, symmetry, unicode.
Soundex: standard codes, padding, empty/non-alpha input, umlaut normalisation,
         consecutive-same-code compression, case insensitivity.
"""
import pytest

from app.utils.string_utils import levenshtein, soundex


# ---------------------------------------------------------------------------
# levenshtein
# ---------------------------------------------------------------------------

def test_levenshtein_identical_strings():
    assert levenshtein("abc", "abc") == 0


def test_levenshtein_both_empty():
    assert levenshtein("", "") == 0


def test_levenshtein_one_empty_a():
    assert levenshtein("", "abc") == 3


def test_levenshtein_one_empty_b():
    assert levenshtein("abc", "") == 3


def test_levenshtein_single_insertion():
    assert levenshtein("abc", "abcd") == 1


def test_levenshtein_single_deletion():
    assert levenshtein("abcd", "abc") == 1


def test_levenshtein_single_substitution():
    assert levenshtein("abc", "axc") == 1


def test_levenshtein_completely_different():
    assert levenshtein("abc", "xyz") == 3


def test_levenshtein_symmetric():
    a, b = "kitten", "sitting"
    assert levenshtein(a, b) == levenshtein(b, a)


def test_levenshtein_classic_kitten_sitting():
    assert levenshtein("kitten", "sitting") == 3


def test_levenshtein_unicode_accent():
    # "café" vs "cafe" — one substitution (é → e)
    assert levenshtein("café", "cafe") == 1


def test_levenshtein_single_char_strings():
    assert levenshtein("a", "a") == 0
    assert levenshtein("a", "b") == 1


def test_levenshtein_prefix():
    assert levenshtein("sunday", "saturday") == 3


# ---------------------------------------------------------------------------
# soundex
# ---------------------------------------------------------------------------

def test_soundex_always_four_chars():
    for word in ("Robert", "Rupert", "Smith", "Johnson", "A"):
        code = soundex(word)
        assert len(code) == 4, f"Expected 4 chars for {word!r}, got {code!r}"


def test_soundex_robert():
    assert soundex("Robert") == "R163"


def test_soundex_rupert_same_as_robert():
    # Robert and Rupert are phonetically similar — same Soundex code
    assert soundex("Rupert") == "R163"


def test_soundex_padding_single_letter():
    assert soundex("A") == "A000"


def test_soundex_empty_string_returns_zero_code():
    assert soundex("") == "0000"


def test_soundex_all_non_alpha_digits_returns_zero_code():
    assert soundex("123") == "0000"


def test_soundex_umlaut_equals_plain():
    # "Müller" normalised to "MULLER" → same code as "Muller"
    assert soundex("Müller") == soundex("Muller")


def test_soundex_consecutive_same_code_compressed():
    # Jackson: J(first), a→0, c/k/s all→2(collapse to one), o→0, n→5 → J500
    assert soundex("Jackson") == "J500"


def test_soundex_case_insensitive():
    assert soundex("robert") == soundex("ROBERT")


def test_soundex_first_letter_preserved():
    assert soundex("Smith")[0] == "S"
    assert soundex("Doe")[0] == "D"


def test_soundex_washington():
    # W252 is the standard code for Washington
    assert soundex("Washington") == "W252"


def test_soundex_only_vowels_pads_zeros():
    # 'Aeiou' — A is first letter, e/i/o/u all map to '0' → "A000"
    assert soundex("Aeiou") == "A000"
