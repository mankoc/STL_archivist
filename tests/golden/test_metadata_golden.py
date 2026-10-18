"""Golden tests: native metadata inference matches app.crawler.metadata in Python."""
import json
import random
import zipfile
from pathlib import Path

import pytest

stl_native = pytest.importorskip("stl_native")
if not hasattr(stl_native, "extract_metadata"):
    pytest.skip("stl_native predates native metadata; rebuild it", allow_module_level=True)

from app.crawler import metadata as M


SEGMENTS = ["Patreon", "patreons", "Kickstarter Rewards", "MyMiniFactory", "Cults", "cults3d", "Gumroad",
            "Sanix", "2024-03", "2024_3", "2024", "2024年", "Dragon - Bust", "x_patreon_y", "patreon-x",
            "١٢٣٤", "Ünïcödé", "ArtStation", "STL", "e\u0301patreon", "thingiverse", "printables", "Kʜ"]
STEMS = ["Big_Dragon-Bust", "2024-09 Hero", "2024 09 Hero", "2024 Hero", "2024", "__", "-", "Hero",
         "١٢٣٤ x", "2024\u3000Hero", "2024\x1cHero", "a.b", ".hidden", "x__y--z"]
URLS = [b"[InternetShortcut]\r\nURL=http://a.example\r\n", b"url= http://b\n", b"URL=\n", b"nothing\n",
        b"\xff\xfeURL=http://c\n", b"  Url=x\x85URL=y", b""]
INFOS = [
    {"name": "N", "author": "A", "tags": ["t", 1, True, None], "release": 5},
    {"name": "", "creator": "C", "author": "A", "distributor": "D", "url": "u", "description": "d"},
    {"tags": [], "release": ""}, {"tags": "notalist", "release": 0}, {"name": 1}, [1, 2], {"tags": [1.5]},
    {"name": "x", "name2": "y"}, {"release": "R", "url": ""},
]


def _compare(archive_path, contents, source_dir):
    rs = M._extract_native(archive_path, contents, source_dir)
    try:
        py = M._extract_metadata_py(archive_path, contents, source_dir)
    except Exception:
        assert rs is None, "Python raises here; native must leave it to Python"
        return "fallback"
    if rs is None:
        return "fallback"
    assert rs == py, (archive_path, contents, source_dir)
    return "native"


def test_path_inference_matches():
    rng = random.Random(3)
    for _ in range(3000):
        parts = rng.sample(SEGMENTS, rng.randint(0, 4))
        stem = rng.choice(STEMS)
        root = rng.choice(["F:\\STL", "F:/stl", "\\\\srv\\share", "rel"])
        path = Path(root, *parts, stem + rng.choice([".7z", ".zip", ".rar"]))
        source = rng.choice([None, Path(root), Path(root.upper()), Path("G:/other")])
        contents = {"a.url": b"URL=http://x\n"}           # no filesystem search
        assert _compare(path, contents, source) == "native"


def test_url_and_info_json_match(tmp_path):
    rng = random.Random(5)
    outcomes = {"native": 0, "fallback": 0}
    for i in range(150):
        folder = tmp_path / f"f{i}"
        folder.mkdir()
        archive = folder / f"Model_{i}.zip"
        with zipfile.ZipFile(archive, "w") as z:
            if rng.random() < 0.5:
                z.writestr(rng.choice(["link.url", "sub/LINK.URL", "x.url"]), rng.choice(URLS))
            z.writestr("a.stl", b"solid")
        if rng.random() < 0.4:
            (folder / rng.choice(["b.url", "A.URL", "z.Url"])).write_bytes(rng.choice(URLS))
        if rng.random() < 0.4:
            links = folder / rng.choice(["Patreon-links", "x_links", "LINKS-LINKS"])
            links.mkdir()
            (links / rng.choice(["1.url", "Z.URL"])).write_bytes(rng.choice(URLS))
        contents = {}
        if rng.random() < 0.5:
            contents[rng.choice(["link.url", "deep/x.URL"])] = rng.choice(URLS)
        if rng.random() < 0.6:
            info = rng.choice(INFOS)
            raw = json.dumps(info).encode() if rng.random() < 0.9 else rng.choice([b"", b"{bad", b"NaN", b"\xef\xbb\xbf{}"])
            contents[rng.choice(["info.json", "sub/INFO.JSON"])] = raw
        contents["img.jpg"] = b"\xff\xd8"
        outcomes[_compare(archive, contents, tmp_path)] += 1
    assert outcomes["native"] > 100


def test_rar_without_url_falls_back(tmp_path):
    archive = tmp_path / "x.rar"
    archive.write_bytes(b"Rar!")
    assert M._extract_native(archive, {}, tmp_path) is None
    assert M.extract_metadata(archive, {}, tmp_path).title == "x"
