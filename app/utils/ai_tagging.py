"""Pure tag/metadata post-processing helpers for the AI tagger.

These functions clean up and reshape the raw text a vision-language model emits
(de-duplicating stuttered words, splitting run-on phrases into atomic tags) and
build the prompts sent to the model. They have no Qt or database dependencies so
they can be unit-tested in isolation; the interactive dialog in
:mod:`app.ui.ai_tagger_dialog` imports them.
"""
from __future__ import annotations

import re


def dedupe_words(tag: str) -> str:
    """Drop repeated words within a single tag.

    Models sometimes stutter (e.g. ``"gothic gothic gothic horror"``); this
    keeps the first occurrence of each word (case-insensitive) in order, so the
    example collapses to ``"gothic horror"``.
    """
    seen: set[str] = set()
    words: list[str] = []
    for w in tag.split():
        key = w.lower()
        if key not in seen:
            seen.add(key)
            words.append(w)
    return " ".join(words)


# Connective words / punctuation used to split run-on, cumulative tags
# (e.g. "ghostly figure in suit with bow tie and gloves") into atomic ones.
_TAG_SPLIT_RE = re.compile(r"\s+(?:with|and|in|featuring|wearing|holding)\s+|[,;&/+]",
                           re.IGNORECASE)


def atomize_tag(tag: str) -> list[str]:
    """Split a descriptive phrase into atomic tags on connectives/punctuation.

    The model often emits cumulative phrases ("x", "x with y", "x with y and z");
    splitting on ``with`` / ``and`` / ``in`` / etc. turns them into the component
    concepts so they collapse to a clean set after de-duplication. Short noun
    phrases without a connective (e.g. "bow tie", "ghostly figure") are kept whole.
    """
    return [p.strip() for p in _TAG_SPLIT_RE.split(tag) if p.strip()]


def split_cumulative_tags(tags: list[str]) -> list[str]:
    """Break cumulative tag chains into the words each step adds.

    Even without connectives the model emits chains where each tag appends words
    to the previous one ("spectral", "spectral warrior", "spectral warrior
    gothic metal", …). Each tag is reduced to the words it adds over the longest
    *other* tag that is a contiguous word-prefix of it, so the chain becomes its
    individual additions ("spectral", "warrior", "gothic metal", …). Tags that no
    other tag prefixes (e.g. "dark fantasy") are kept whole.
    """
    word_seqs = [t.lower().split() for t in tags]
    result: list[str] = []
    seen: set[str] = set()
    for idx, (tag, words) in enumerate(zip(tags, word_seqs)):
        # longest other tag whose words are a strict prefix of this tag
        best = 0
        for j, ow in enumerate(word_seqs):
            n = len(ow)
            if j != idx and n < len(words) and words[:n] == ow and n > best:
                best = n
        new_tag = " ".join(tag.split()[best:])   # delta, original casing
        key = new_tag.lower()
        if new_tag and key not in seen:
            seen.add(key)
            result.append(new_tag)
    return result


def clean_tags(raw_tags: str, max_tags: int = 0) -> list[str]:
    """Turn the model's raw comma-separated tag string into a clean tag list.

    Applies, in order: comma split → :func:`atomize_tag` → :func:`dedupe_words`
    → case-insensitive de-duplication → :func:`split_cumulative_tags`, then caps
    the result at *max_tags* when that is a positive number.
    """
    seen: set[str] = set()
    tags: list[str] = []
    for raw in raw_tags.split(","):
        for piece in atomize_tag(raw):
            t = dedupe_words(piece)
            if t and t.lower() not in seen:
                seen.add(t.lower())
                tags.append(t)
    tags = split_cumulative_tags(tags)
    if max_tags and max_tags > 0:
        tags = tags[:max_tags]
    return tags


def format_known_meta(known_meta: dict[str, str] | None) -> str:
    """Render the model's existing catalogued metadata as a prompt hint block."""
    if not known_meta:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in known_meta.items())
    return (
        "Existing catalogued metadata for this model (extracted when the archive "
        "was added — it may be incomplete or inaccurate, so use it as a hint and "
        "correct it if the images or path clearly contradict it):\n"
        f"{lines}\n"
    )


def build_tags_prompt(stems: list[str], archive_path: str,
                      known_meta: dict[str, str] | None = None) -> str:
    """Build the user prompt asking the model for a comma-separated tag list."""
    filenames_str = ", ".join(f'"{s}"' for s in stems) if stems else "(none)"
    intro = (
        f"These {len(stems)} images come from a single archive of 3D printable miniatures. "
        "They usually show the same model from different angles, but some may show a "
        "different model, separate parts or accessories, packaging, or promotional art. "
        "Identify and describe the main miniature; ignore unrelated images. "
        if len(stems) > 1 else
        "This image shows a 3D printable miniature model. "
    )
    return (
        intro
        + f'Full archive path: "{archive_path}". '
        + f"Image file names: {filenames_str}. "
        + format_known_meta(known_meta)
        + "Use the full path, filenames, and any existing metadata as hints alongside the images to identify the character or subject. "
        "Tag ONLY what is actually shown in the images. Do NOT add related characters, "
        "vehicles, companions, or items from the same franchise that do not appear "
        "(for example, do not tag other Star Wars characters just because a ship is from Star Wars). "
        "Output ONLY a comma-separated list of descriptive tags. "
        "Each tag must be a single, atomic concept — do NOT output cumulative or "
        "overlapping phrases. For example, output 'suit', 'bow tie', 'gloves' as "
        "separate tags, NOT 'figure in a suit with a bow tie and gloves'. Avoid the "
        "connecting words 'with', 'and', and 'in' inside a tag. "
        "Include: character name (if recognizable), franchise or source material, "
        "character type, genre, visual style, and any other relevant descriptors. "
        "Output ONLY the comma-separated tags, no other text."
    )


def build_meta_prompt(archive_path: str,
                      known_meta: dict[str, str] | None = None) -> str:
    """Build the user prompt asking the model for the fixed metadata field block."""
    return (
        "This is a 3D printable miniature model. "
        f'Full archive path: "{archive_path}". '
        + format_known_meta(known_meta)
        + "The archive may contain several images; some may show a different model, "
        "separate parts, packaging, or promotional art — base the metadata on the main miniature. "
        + "Based on the images, the full path (folder structure encodes sculptor, "
        "series, and source information), and any existing metadata above, output metadata in this EXACT format "
        "(one line per field, leave the value empty if unknown, no extra text):\n"
        "TITLE: <model title>\n"
        "CREATOR: <publisher, brand or author name>\n"
        "SCULPTOR: <artist or sculptor name>\n"
        "COLLECTION: <the MOST SPECIFIC applicable from: fantasy, sci-fi, comics, tv, movies, "
        "games, books, anime, real life, historic, cartoons, music and musicians, modelling, "
        "props, pin-ups. Prefer specific over generic — e.g. Batman is 'comics' (not 'fantasy'), "
        "a Star Wars trooper is 'movies', a Witcher figure is 'games'. Use 'fantasy'/'sci-fi' "
        "only for original/generic creatures with no known franchise.>\n"
        "SUBCOLLECTION: <the specific franchise, publisher, series or universe within the "
        "collection — e.g. DC or Marvel for comics, Star Wars for movies, Warhammer 40k for "
        "games. Empty if none/unknown.>\n"
        "SCALE: <ONLY if a scale such as 28mm, 32mm, 1/35 or 54mm is explicitly written in the path, "
        "filenames, or existing metadata above. A scale CANNOT be seen in an image — never guess or "
        "infer it. Leave empty if it is not stated in text.>\n"
        "SOURCE: <one of: patreon, kickstarter, myminifactory — or empty>\n"
        "YEAR: <4-digit release year — or empty>\n"
        "DESCRIPTION: <one sentence describing the model>\n"
        "Output ONLY these 9 lines."
    )
