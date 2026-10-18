"""Pure tag/metadata post-processing helpers for the AI tagger.

These functions clean up and reshape the raw text a vision-language model emits
(de-duplicating stuttered words, splitting run-on phrases into atomic tags) and
build the prompts sent to the model. They have no Qt or database dependencies so
they can be unit-tested in isolation; the interactive dialog in
:mod:`app.ui.ai_tagger_dialog` imports them.
"""
from __future__ import annotations

import re
from pathlib import Path

#: SOURCE values the tagger keeps.
VALID_SOURCES = {"patreon", "kickstarter", "myminifactory", "unknown"}


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


def is_corrupted_output(text: str) -> bool:
    """Detect if the model output is corrupted (mostly garbage/question marks).

    Returns True if the output appears to be corrupted state (many question marks,
    very few valid characters, etc.).
    """
    if not text or len(text) < 3:
        return False

    # Count question marks and other non-word characters
    text_lower = text.lower()
    question_count = text_lower.count("?")

    # If more than 30% of the content is question marks, it's corrupted
    if text and question_count / len(text) > 0.3:
        return True

    # If the output is mostly non-ASCII or control characters, it's corrupted
    valid_chars = sum(1 for c in text if c.isalnum() or c in " ,-./")
    if text and valid_chars / len(text) < 0.2:
        return True

    return False


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


def parse_meta(raw_meta: str, archive_path: str, stems: list[str],
               file_list: list[str] | None = None) -> dict[str, str]:
    """Parse the model's ``KEY: value`` lines into a dict.

    Empty, ``n/a``, ``none`` and ``unknown`` values are dropped. ``SCALE`` is
    text-derived, not visible in an image: it is replaced by the scales detected
    in the archive path, image file names and file list (so every scale is
    caught, e.g. both 32mm and 75mm), plus any model suggestion in a format the
    detector misses (e.g. "1:35") that the text backs up.
    """
    from app.crawler.rules import detect_scale

    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().upper()
            val = val.strip()
            if val and val.lower() not in ("n/a", "none", "unknown", ""):
                meta[key] = val

    def _norm_scale(s: str) -> str:
        return s.lower().replace(" ", "").replace("-", "/")

    scale_context = [archive_path, *stems, *(file_list or [])]
    detected = detect_scale(scale_context)
    have = {_norm_scale(s) for s in detected}
    final_scales = list(detected)
    nctx = _norm_scale(" ".join(scale_context))
    for tok in meta.get("SCALE", "").split(","):
        t = tok.strip()
        if t and _norm_scale(t) in nctx and _norm_scale(t) not in have:
            final_scales.append(t)
            have.add(_norm_scale(t))

    if final_scales:
        meta["SCALE"] = ", ".join(final_scales)
    else:
        meta.pop("SCALE", None)
    return meta


def normalize_meta(meta: dict[str, str]) -> dict:
    """Convert parsed metadata into validated, normalized field values."""
    fields = {
        "title":         meta.get("TITLE", "").strip(),
        "creator":       meta.get("CREATOR", "").strip(),
        "sculptor":      meta.get("SCULPTOR", "").strip(),
        "collection":    meta.get("COLLECTION", "").strip(),
        "subcollection": meta.get("SUBCOLLECTION", "").strip(),
        "scale":         meta.get("SCALE", "").strip(),
        "description":   meta.get("DESCRIPTION", "").strip(),
        "source":        "",
        "year":          None,
    }
    src = meta.get("SOURCE", "").strip().lower()
    if src in VALID_SOURCES:
        fields["source"] = src
    year = meta.get("YEAR", "").strip()[:4]
    if year.isdigit() and 1900 <= int(year) <= 2100:
        fields["year"] = int(year)
    return fields


#: Printable file names shown in the prompt, at most.
FILE_CAP = 60


def collect_known_meta(inputs: dict) -> dict[str, str]:
    """Existing catalogued metadata as an ordered {label: value} dict.

    *inputs* is :func:`app.services.model_edit.ai_model_inputs`; only non-empty
    fields are in it. The printable files inside the archive are strong hints
    for the subject and the scale, capped so the prompt stays small.
    """
    meta: dict[str, str] = dict(inputs["meta"])
    if inputs["tags"]:
        meta["Tags"] = ", ".join(inputs["tags"])
    file_names = [Path(f).name for f in inputs["files"]]
    if file_names:
        shown = ", ".join(file_names[:FILE_CAP])
        if len(file_names) > FILE_CAP:
            shown += f", …(+{len(file_names) - FILE_CAP} more)"
        meta["Files"] = shown
    return meta
