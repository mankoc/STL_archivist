"""Golden tests: the Rust archive fingerprint must equal the Python one, bit for bit.

The stored ``archive_hash`` decides whether the crawler re-extracts an archive,
so any difference would make archives look changed (compatibility rule 1 in
docs/rust-migration-plan.md). Skipped when ``stl_native`` is not built.
"""
import random
import zipfile

import pytest

stl_native = pytest.importorskip("stl_native")

from app import native
from app.crawler.archive import _members_fingerprint_py, archive_hash, members_fingerprint

# Characters that stress encoding and ordering: ASCII, Latin-1, combining marks,
# CJK, astral-plane emoji, path separators, spaces and dots.
ALPHABET = (
    "abcXYZ019 ._-/\\"
    "ñÑüßéÈ"
    "éö"
    "ドラゴン龍한국"
    "🐉🗡️👾"
)


@pytest.fixture(autouse=True)
def native_enabled(monkeypatch):
    monkeypatch.delenv("STL_NATIVE", raising=False)
    native.reload()
    yield
    native.reload()


def _random_members(rnd: random.Random) -> list[tuple[str, int]]:
    names = ["".join(rnd.choice(ALPHABET) for _ in range(rnd.randint(0, 24)))
             for _ in range(rnd.randint(0, 40))]
    if names and rnd.random() < 0.3:
        names += rnd.sample(names, k=min(3, len(names)))      # duplicate names
    return [(n, rnd.randint(0, 2**32 - 1)) for n in names]


def test_random_member_lists_match():
    rnd = random.Random(20261017)
    for _ in range(5000):
        members = _random_members(rnd)
        assert stl_native.archive_fingerprint(members) == _members_fingerprint_py(members), members


def test_equal_names_sort_by_crc():
    members = [("same.stl", 7), ("same.stl", 3), ("a", 2**32 - 1), ("same.stl", 0)]
    assert stl_native.archive_fingerprint(members) == _members_fingerprint_py(members)


def test_lone_surrogate_falls_back_to_python():
    members = [("bad\udcff.stl", 1), ("ok.stl", 2)]
    with pytest.raises(UnicodeError):
        stl_native.archive_fingerprint(members)
    assert members_fingerprint(members) == _members_fingerprint_py(members)


def test_out_of_range_crc_is_rejected():
    with pytest.raises((OverflowError, TypeError)):
        stl_native.archive_fingerprint([("x", 2**32)])


def _zip(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        for k, name in enumerate(names):
            zf.writestr(name, f"payload {k}" * (k + 1))
    return path


def test_archive_hash_identical_with_and_without_native(tmp_path, monkeypatch):
    archives = [
        _zip(tmp_path / "plain.zip", ["model.stl", "renders/front.jpg", "info.json"]),
        _zip(tmp_path / "unicode.zip", ["Ñandú/ドラゴン 🐉.stl", "éclair.png"]),
        _zip(tmp_path / "nested.zip", ["inner.zip", "sub/dir/part.stl", "sub/dir/part.stl.bak"]),
        _zip(tmp_path / "empty.zip", []),
    ]
    with_native = [archive_hash(a) for a in archives]
    monkeypatch.setenv("STL_NATIVE", "0")
    native.reload()
    assert not native.available()
    without = [archive_hash(a) for a in archives]
    assert with_native == without
