"""Unit tests for the pure tag/metadata helpers in :mod:`app.utils.ai_tagging`.

These functions are deliberately free of Qt and database dependencies so they
can be tested directly without spinning up the AI server or a GUI.
"""
from app.utils.ai_tagging import (
    atomize_tag,
    build_meta_prompt,
    build_tags_prompt,
    clean_tags,
    dedupe_words,
    format_known_meta,
    is_corrupted_output,
    split_cumulative_tags,
)


# --- is_corrupted_output ---------------------------------------------------

def test_is_corrupted_output_detects_many_question_marks():
    assert is_corrupted_output("??? ??? ??? ???") is True
    assert is_corrupted_output("?? ?? ?? ?? ?? ??") is True


def test_is_corrupted_output_ignores_few_question_marks():
    assert is_corrupted_output("dragon? fantasy? magic?") is False
    assert is_corrupted_output("What is this?") is False


def test_is_corrupted_output_detects_mostly_garbage():
    assert is_corrupted_output("\x00\x01\x02\x03\x04") is True
    assert is_corrupted_output("███████████") is True


def test_is_corrupted_output_handles_valid_output():
    assert is_corrupted_output("dragon, fantasy, medieval") is False
    assert is_corrupted_output("warrior in armor with sword") is False


def test_is_corrupted_output_empty_or_too_short():
    assert is_corrupted_output("") is False
    assert is_corrupted_output("a") is False
    assert is_corrupted_output("ab") is False


def test_is_corrupted_output_mixed_corruption():
    assert is_corrupted_output("dragon ????????? magic ???????") is True


# --- dedupe_words ----------------------------------------------------------

def test_dedupe_words_collapses_repeats():
    assert dedupe_words("gothic gothic gothic horror") == "gothic horror"


def test_dedupe_words_is_case_insensitive_keeps_first_casing():
    assert dedupe_words("Gothic gothic Horror") == "Gothic Horror"


def test_dedupe_words_empty():
    assert dedupe_words("") == ""


# --- atomize_tag -----------------------------------------------------------

def test_atomize_tag_splits_on_connectives():
    assert atomize_tag("figure in suit with bow tie and gloves") == [
        "figure", "suit", "bow tie", "gloves",
    ]


def test_atomize_tag_splits_on_punctuation():
    assert atomize_tag("sword; shield / axe + bow") == [
        "sword", "shield", "axe", "bow",
    ]


def test_atomize_tag_keeps_short_phrase_whole():
    assert atomize_tag("ghostly figure") == ["ghostly figure"]


def test_atomize_tag_drops_empty_fragments():
    assert atomize_tag("  ,  sword ,  ") == ["sword"]


# --- split_cumulative_tags -------------------------------------------------

def test_split_cumulative_chain_reduced_to_deltas():
    tags = ["spectral", "spectral warrior", "spectral warrior gothic"]
    assert split_cumulative_tags(tags) == ["spectral", "warrior", "gothic"]


def test_split_cumulative_keeps_unrelated_tags_whole():
    tags = ["spectral warrior", "dark fantasy"]
    assert split_cumulative_tags(tags) == ["spectral warrior", "dark fantasy"]


def test_split_cumulative_dedupes_resulting_deltas():
    # Both reduce to "warrior" after removing the shared "spectral" prefix.
    tags = ["spectral", "spectral warrior", "spectral warrior"]
    assert split_cumulative_tags(tags) == ["spectral", "warrior"]


# --- clean_tags (full pipeline) -------------------------------------------

def test_clean_tags_end_to_end():
    raw = "spectral, spectral warrior, gothic gothic horror, gothic horror"
    result = clean_tags(raw)
    assert result == ["spectral", "warrior", "gothic horror"]


def test_clean_tags_respects_max_tags():
    raw = "a, b, c, d, e"
    assert clean_tags(raw, max_tags=3) == ["a", "b", "c"]


def test_clean_tags_zero_max_means_no_limit():
    raw = "a, b, c, d, e"
    assert len(clean_tags(raw, max_tags=0)) == 5


def test_clean_tags_empty_string():
    assert clean_tags("") == []


# --- prompt builders -------------------------------------------------------

def test_format_known_meta_empty_returns_blank():
    assert format_known_meta(None) == ""
    assert format_known_meta({}) == ""


def test_format_known_meta_renders_fields():
    out = format_known_meta({"Title": "Goblin", "Sculptor": "Jane"})
    assert "- Title: Goblin" in out
    assert "- Sculptor: Jane" in out


def test_build_tags_prompt_single_vs_multi_image():
    single = build_tags_prompt(["a"], "/lib/x.zip")
    multi = build_tags_prompt(["a", "b", "c"], "/lib/x.zip")
    assert "This image shows" in single
    assert "These 3 images" in multi


def test_build_tags_prompt_includes_path_and_filenames():
    out = build_tags_prompt(["hero_front", "hero_back"], "/lib/Hero.zip")
    assert "/lib/Hero.zip" in out
    assert '"hero_front"' in out


def test_build_meta_prompt_includes_required_fields():
    out = build_meta_prompt("/lib/Hero.zip")
    for field in ("TITLE:", "CREATOR:", "SCULPTOR:", "COLLECTION:",
                  "SUBCOLLECTION:", "SCALE:", "SOURCE:", "YEAR:", "DESCRIPTION:"):
        assert field in out


def test_build_meta_prompt_embeds_known_meta():
    out = build_meta_prompt("/lib/Hero.zip", {"Title": "Goblin"})
    assert "- Title: Goblin" in out
