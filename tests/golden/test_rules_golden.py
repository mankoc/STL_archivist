"""Golden tests: native crawler rules, path patterns and Soundex against Python.

Each native function must return exactly what the Python implementation it
replaces returns. Inputs mix hand-picked edge cases with random names built
from the pieces the rules look at (markers, negations, separators, suffixes).
"""
import random

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "detect_warnings"):
    pytest.skip("stl_native predates the rules port; rebuild it", allow_module_level=True)

from app.crawler import rules as R
from app.utils import path_pattern as P
from app.utils import string_utils as S

EDGE_NAMES = [
    "Presupported/body.stl", "PS/arm.stl", "Supported STL/leg.stl", "Props/x.stl", "PSD/x.stl",
    "supports/Unsupported/120MM_Unsup_Emerald.stl", "No_Supported/x.stl", "Un-Supported/a.stl",
    "Without Supports/b.stl", "No PS/c.stl", "Dino Supported/d.stl", "supports_unsupported/e.stl",
    "body_presupported.stl", "arm - Presupported.stl", "sup_arm.stl", "s_leg.stl", "[s]_leg.stl",
    "unsupported.stl", "superman.stl", "x_supported.stl", "x_supported.STL", "Dragon - Presupported.jpg",
    "Biker Mice from Mars Presupported lys", "a.lys", "b.LYT", "c.chitu", "d.chitubox", "e.ctp",
    "f.obj", "g.zip", "h.RAR", "i.7z", "j.avif", "k.GIF", "C:foo.stl", "C:sup_x.stl", "a.", ".stl",
    "x/.hidden.stl", "a/b/../c_supported.stl", "a//b.stl", "a/./sup_b.stl", "trail/", "..",
    "w\\x\\PS\\y.stl", "Hero_32mm_Base.stl", "75 mm/x.stl", "x 1-35 tank.stl", "1/72 plane.stl",
    "128mmx.stl", "2028mm", "٣٢mm.stl", "ſup_x.stl", "Ünsupported/x.stl", "İPS/x.stl", "32MM.zip",
    "a.tar.gz", "readme.txt", "",
]

PIECES = ["Presupported", "PS", "sup", "Supports", "unsup", "No", "Un", "Without", "Props", "body",
          "arm", "28mm", "32 mm", "1-35", "1/72", "Dragon", "x", "S", "[s]", "NoSupports", "wo"]
SEPS = [" ", "_", "-", "/", "\\", ".", " - "]
SUFFIXES = [".stl", ".STL", ".jpg", ".png", ".lys", ".ctp", ".zip", ".obj", ".txt", ""]


def _random_names(rng, n):
    out = []
    for _ in range(n):
        parts = [rng.choice(PIECES) for _ in range(rng.randint(1, 5))]
        name = "".join(p + rng.choice(SEPS) for p in parts[:-1]) + parts[-1] + rng.choice(SUFFIXES)
        out.append(name)
    return out


def _lists(seed=0, count=400):
    rng = random.Random(seed)
    lists = [[n] for n in EDGE_NAMES] + [EDGE_NAMES]
    lists += [_random_names(rng, rng.randint(0, 12)) for _ in range(count)]
    return lists


FLAGS = [(True, True, True, True), (True, False, True, False), (False, True, False, True)]
PATTERN_SETS = [None, R.DEFAULT_PRESUPPORT_PATTERNS, [r"^body", r"(?<!x)_arm\.stl$"], []]


@pytest.mark.parametrize("flags", FLAGS)
@pytest.mark.parametrize("patterns", PATTERN_SETS, ids=["default", "explicit", "custom", "none"])
def test_warnings_match(flags, patterns):
    for names in _lists():
        assert stl_native.detect_warnings(names, *flags, patterns) == \
            R._detect_warnings_py(names, *flags, presupport_patterns=patterns), names
        assert dict(stl_native.explain_warnings(names, *flags, patterns)) == \
            R._explain_warnings_py(names, *flags, presupport_patterns=patterns), names


def test_presupport_and_scale_match():
    for names in _lists(seed=1):
        assert stl_native.detect_presupport(names) == R._detect_presupport_py(names), names
        assert stl_native.detect_scale(names) == R._detect_scale_py(names), names


@pytest.mark.parametrize("pats", [[r"[[:alpha:]]_x"], [r"(?<name>sup)"], [r"\x{73}up"], [r"(?a)\w+_x"]])
def test_non_portable_patterns_use_python(pats, monkeypatch):
    # Not portable: the engines disagree on these without raising (or native rejects them);
    # the public functions must give Python's answer regardless.
    names = ["a_x.stl", "[_x.stl", "sup.stl", "x{73}up.stl", "é_x.stl", "pic.jpg"]
    called = []
    monkeypatch.setattr(stl_native, "detect_warnings", lambda *a: called.append(a))
    monkeypatch.setattr(stl_native, "explain_warnings", lambda *a: called.append(a))
    assert R.detect_warnings(names, presupport_patterns=pats) == \
        R._detect_warnings_py(names, presupport_patterns=pats)
    assert R.explain_warnings(names, presupport_patterns=pats) == \
        R._explain_warnings_py(names, presupport_patterns=pats)
    assert called == []


PATTERNS = ["/{source}/{creator}/*/{name}", "{creator}/{date} - {name}", "{creator}/{release}/{title}",
            "{name}", "* - {creator}", "{creator} - {name}", "{sculptor}/{collection}/{subcollection}/{name}",
            "/{source}/*/{creator}/{date}/{name}", "{creator:1}/{creator}/{name}", "{title} ({url})",
            "{creator}/{creator}/{name}", "{sequence}. {name}", "plain", "{unknown}/{name}", "*/*/{name}"]
SEGMENTS = ["Patreon", "Sanix", "2024-03", "2024_3", "2024", "Dragon", "Dragon - Bust", "Vengeance - 3dbyJT",
            "x (http)", "01. Hero", "..", "a.b", "STL", "ドラゴン", "c"]


def _paths(rng, n):
    out = ["", "Dragon.7z", "/lib/Patreon/Sanix/2024/Dragon.7z", r"F:\STL\Sanix\2024-03 - Dragon.zip"]
    for _ in range(n):
        segs = [rng.choice(SEGMENTS) for _ in range(rng.randint(1, 6))]
        lead = rng.choice(["", "/", "F:/", "F:\\"])
        out.append(lead + rng.choice(["/", "\\"]).join(segs) + rng.choice([".7z", ".zip", "", ".tar.gz"]))
    return out


def test_parse_path_matches():
    rng = random.Random(2)
    paths = _paths(rng, 600)
    for pattern in PATTERNS:
        for path in paths:
            # Same fields, values *and* order (dialogs show the fields in dict order).
            assert stl_native.parse_path(pattern, path) == list(P._parse_path_py(pattern, path).items()), \
                (pattern, path)


def test_soundex_matches():
    names = ["Müller", "Muller", "Robert", "Rupert", "Ashcraft", "Tymczak", "Pfister", "Honeyman", "ß-König",
             "ﬁsher", "", "123", "Ærøskøbing", "Łukasz", "O'Brien", "Lloyd", "ドラゴン", "ǅemal", "aaaa", "Bbbb"]
    rng = random.Random(3)
    names += ["".join(rng.choice("abcdefghijklmnopqrstuvwxyzÄÖÜéñ -'") for _ in range(rng.randint(0, 12)))
              for _ in range(2000)]
    for n in names:
        assert stl_native.soundex(n) == S._soundex_py(n), n
