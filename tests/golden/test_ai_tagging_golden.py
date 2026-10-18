"""Golden test: the native AI tagger text handling (``stl_core::ai_tagging``,
used by the server) against ``app.utils.ai_tagging``."""
import random

import pytest

stl_native = pytest.importorskip("stl_native")

from app.utils import ai_tagging as T  # noqa: E402

_WORDS = ["Batman", "batman", "cape", "dark", "Dark", "knight", "gothic", "horror", "with", "and", "in",
          "featuring", "wearing", "holding", "WITH", "spectral", "warrior", "metal", "bow", "tie", "suit",
          "Señor", "dragón", "龍", "Ørn", "x", "??", "#", "@@", "28mm", "1/35", "32 mm", "1-35", "1:35"]
_SEPS = [" ", " ", " ", ", ", "; ", " & ", "/", " + ", "  ", "\t", ",", " with ", " and "]


def _text(r: random.Random, n: int) -> str:
    out = []
    for _ in range(n):
        out.append(r.choice(_WORDS))
        out.append(r.choice(_SEPS))
    return "".join(out).strip(" ") if r.random() < 0.7 else "".join(out)


@pytest.mark.parametrize("seed", range(300))
def test_tags_match_python(seed):
    r = random.Random(seed)
    raw = _text(r, r.randint(0, 30))
    max_tags = r.choice([0, 0, 3, 10])
    assert stl_native.ai_clean_tags(raw, max_tags) == T.clean_tags(raw, max_tags), raw
    assert stl_native.ai_is_corrupted(raw) == T.is_corrupted_output(raw), raw


_KEYS = ["TITLE", "title", "Creator", "SCULPTOR", "COLLECTION", "SUBCOLLECTION", "SCALE", "SOURCE", "YEAR",
         "DESCRIPTION", " year ", "Note"]
_VALUES = ["Batman", "n/a", "None", "unknown", "", "  ", "Patreon", "kickstarter", "MyMiniFactory", "2023",
           "1899", "2101", "20x5", "2023 (approx)", "28mm", "32 mm, 1:35", "75mm, 1/35", "1-35", "54 mm",
           "A caped hero: dark", "Señor Dragón"]


@pytest.mark.parametrize("seed", range(300))
def test_metadata_matches_python(seed):
    r = random.Random(seed)
    lines = [f"{r.choice(_KEYS)}:{r.choice(['', ' ', '  '])}{r.choice(_VALUES)}" for _ in range(r.randint(0, 12))]
    if r.random() < 0.3:
        lines.append("no colon here")
    raw = r.choice(["\n", "\r\n", "\r", " ", "\x0b", "\x85", "\n\n"]).join(lines)
    path = r.choice(["D:/STL/Studio/Batman 32mm.zip", "D:\\STL\\Hero 1-35\\pack.7z", "/srv/stl/x 75 mm.zip", "plain.zip"])
    stems = r.sample(["front", "back 28mm", "scale 1_35", "img3", "Side 54mm"], r.randint(0, 3))
    files = r.sample(["body 32mm.stl", "parts/arm 1-35.stl", "base.stl", "x 1:35 base.stl", "head75mm.stl"], r.randint(0, 4))

    want = T.parse_meta(raw, path, stems, files)
    got_meta, got_fields = stl_native.ai_parse_meta(raw, path, stems, files)
    assert dict(got_meta) == want and [k for k, _ in got_meta] == list(want), raw
    f = T.normalize_meta(want)
    keys = ("title", "creator", "sculptor", "collection", "subcollection", "scale", "description", "source", "year")
    assert dict(zip(keys, got_fields)) == f, raw


@pytest.mark.parametrize("seed", range(50))
def test_prompts_match_python(seed):
    r = random.Random(seed)
    stems = r.sample(["front", "back", "Señor", "img7"], r.randint(0, 4))
    path = r.choice(["D:/STL/Studio/Batman.zip", "/srv/stl/x.zip"])
    meta = {k: v for k, v in r.sample([("Title", "Batman"), ("Creator", "DC"), ("Tags", "hero, cape"),
                                       ("Files", "a.stl, b.stl")], r.randint(0, 4))}
    tags_prompt, meta_prompt = stl_native.ai_prompts(stems, path, list(meta.items()))
    assert tags_prompt == T.build_tags_prompt(stems, path, meta or None)
    assert meta_prompt == T.build_meta_prompt(path, meta or None)
