"""Golden tests: the native ZIP/7z catalog must match Python's zipfile / py7zr exactly.

Member names decide the archive fingerprint (compatibility rule 1), so the
edge cases that change names are built by hand: CP437 names, backslashes,
NULs, Unicode-path extra fields (0x7075), comments and prepended data.
Wherever Python's reader raises, the native one must raise too.
"""
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

stl_native = pytest.importorskip("stl_native")

from app import native
from app.crawler import archive as A


def _python_catalog(path: Path):
    """(name, crc, size) as the Python readers produce them, native bypassed."""
    with A.open_archive(path) as ar:
        return [(m.name, m.crc, m.size) for m in ar.iter_members()]


def _raw_zip(entries, comment=b"", prepend=b""):
    """Build a stored (uncompressed) zip from raw parts.

    *entries*: (raw_name_bytes, data, flags, extra) tuples, written verbatim so
    names can be CP437, contain backslashes or NULs, or carry 0x7075 fields.
    """
    local, central = b"", b""
    for raw, data, flags, extra in entries:
        crc = zlib.crc32(data)
        offset = len(prepend) + len(local)
        local += struct.pack("<4s5H3L2H", b"PK\x03\x04", 20, flags, 0, 0, 0,
                             crc, len(data), len(data), len(raw), 0) + raw + data
        central += struct.pack("<4s4B4HL2L5H2L", b"PK\x01\x02", 20, 0, 20, 0, flags, 0, 0, 0,
                               crc, len(data), len(data), len(raw), len(extra), 0, 0, 0, 0,
                               offset) + raw + extra
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, len(entries), len(entries),
                      len(central), len(local), len(comment)) + comment
    return prepend + local + central + end


def _unicode_path(name: str, raw: bytes, version=1, crc=None):
    data = struct.pack("<BL", version, zlib.crc32(raw) if crc is None else crc) + name.encode("utf-8")
    return struct.pack("<HH", 0x7075, len(data)) + data


UTF8 = 1 << 11


ZIP_CASES = {
    "cp437 names": [("Ñandú café.stl".encode("cp437"), b"x", 0, b""),
                    (bytes(range(0x80, 0xA0)) + b".jpg", b"y", 0, b"")],
    "utf8 flag": [("ドラゴン/🐉 bust.stl".encode(), b"x", UTF8, b"")],
    "backslash names": [(b"renders\\front.jpg", b"x", 0, b""), (b"a\\b\\c.stl", b"y", 0, b"")],
    "nul in name": [(b"virus.jpg\x00.exe", b"x", 0, b"")],
    "unicode path field used": [(b"caf?.stl", b"x", 0, _unicode_path("café.stl", b"caf?.stl"))],
    "unicode path crc mismatch ignored": [(b"caf?.stl", b"x", 0,
                                           _unicode_path("café.stl", b"caf?.stl", crc=1))],
    "unicode path wrong version ignored": [(b"caf?.stl", b"x", 0,
                                            _unicode_path("café.stl", b"caf?.stl", version=2))],
    "unicode path with backslash": [(b"x.stl", b"x", 0, _unicode_path("dir\\x.stl", b"x.stl"))],
    "duplicate names": [(b"same.stl", b"a", 0, b""), (b"same.stl", b"bb", 0, b"")],
    "empty": [],
}


@pytest.mark.parametrize("case", sorted(ZIP_CASES))
def test_zip_catalog_matches_zipfile(tmp_path, case):
    path = tmp_path / "t.zip"
    path.write_bytes(_raw_zip(ZIP_CASES[case]))
    assert stl_native.archive_members(str(path)) == _python_catalog(path)
    assert stl_native.archive_hash_file(str(path)) == A._members_fingerprint_py(
        [(n, c) for n, c, _ in _python_catalog(path)])


def test_zip_comment_and_prepended_data(tmp_path):
    entries = [(b"model.stl", b"payload", 0, b""), (b"img.png", b"png", 0, b"")]
    for comment, prepend in ((b"a comment", b""), (b"", b"#!/bin/sh\nexit\n" * 50), (b"c" * 70000, b"")):
        path = tmp_path / "t.zip"
        path.write_bytes(_raw_zip(entries, comment[:65535], prepend))
        assert stl_native.archive_members(str(path)) == _python_catalog(path)


@pytest.mark.parametrize("extra", [
    _unicode_path("bad", b"x.stl")[:6],                               # truncated 0x7075 field
    struct.pack("<HH", 0x7075, 6) + b"\x01" + struct.pack("<L", zlib.crc32(b"x.stl")) + b"\xff",
    struct.pack("<HH", 0x1234, 50) + b"short",                        # field longer than extra
])
def test_corrupt_extra_fields_fail_in_both(tmp_path, extra):
    path = tmp_path / "t.zip"
    path.write_bytes(_raw_zip([(b"x.stl", b"x", 0, extra)]))
    with pytest.raises(zipfile.BadZipFile):
        _python_catalog(path)
    with pytest.raises(ValueError):
        stl_native.archive_members(str(path))


def test_not_a_zip_fails_in_both(tmp_path):
    path = tmp_path / "t.zip"
    path.write_bytes(b"not a zip at all" * 10)
    with pytest.raises(zipfile.BadZipFile):
        _python_catalog(path)
    with pytest.raises(ValueError):
        stl_native.archive_members(str(path))


def test_real_zipfile_output(tmp_path):
    path = tmp_path / "t.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("model.stl", b"solid" * 1000)
        zf.writestr("Ñandú/ドラゴン.jpg", b"jpeg")
        zf.writestr("dir/", b"")
        zf.writestr("empty.txt", b"")
    assert stl_native.archive_members(str(path)) == _python_catalog(path)


def test_7z_catalog_matches_py7zr(tmp_path):
    import py7zr
    src = tmp_path / "src"
    (src / "renders").mkdir(parents=True)
    (src / "renders" / "front ドラゴン.jpg").write_bytes(b"jpeg" * 100)
    (src / "model.stl").write_bytes(b"solid" * 1000)
    (src / "empty.txt").write_bytes(b"")
    (src / "emptydir").mkdir()
    path = tmp_path / "t.7z"
    with py7zr.SevenZipFile(path, "w") as z:
        z.writeall(src, arcname="")
    native_cat = stl_native.archive_members(str(path))
    py_cat = _python_catalog(path)
    assert [(n, c) for n, c, _ in native_cat] == [(n, c) for n, c, _ in py_cat]
    assert stl_native.archive_hash_file(str(path)) == A._members_fingerprint_py(
        [(n, c) for n, c, _ in py_cat])


def test_rar_edge_cases(tmp_path):
    """A bare RAR signature lists as empty (as rarfile does); an SFX archive
    (signature not at the start) is left to Python.  tests/golden/test_rar_golden.py
    covers real archives."""
    path = tmp_path / "t.rar"
    path.write_bytes(b"Rar!\x1a\x07\x00")
    assert stl_native.archive_members(str(path)) == []
    sfx = tmp_path / "s.rar"
    sfx.write_bytes(b"MZ" + b"\0" * 100 + b"Rar!\x1a\x07\x00")
    with pytest.raises(ValueError):
        stl_native.archive_members(str(sfx))


# --- extraction: read_archive_members must equal read_batch ---------------------------

def _python_read(path: Path, names):
    with A.open_archive(path) as ar:
        return ar.read_batch(list(names))


@pytest.mark.parametrize("case", sorted(ZIP_CASES))
def test_zip_read_matches_zipfile(tmp_path, case):
    path = tmp_path / "t.zip"
    path.write_bytes(_raw_zip(ZIP_CASES[case]))
    names = [n for n, _, _ in _python_catalog(path)] + ["not/there.jpg"]
    assert stl_native.read_archive_members(str(path), names) == _python_read(path, names)


def test_zip_read_deflated_and_prepended(tmp_path):
    path = tmp_path / "t.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("renders/front.jpg", bytes(range(256)) * 400)
        zf.writestr("info.json", b'{"title": "x"}')
        zf.writestr("empty.png", b"")
        zf.writestr("stored.url", b"[InternetShortcut]\nURL=x", compress_type=zipfile.ZIP_STORED)
    for prepend in (b"", b"#!/bin/sh\n" * 30):
        p = tmp_path / f"p{len(prepend)}.zip"
        p.write_bytes(prepend + path.read_bytes())
        names = [n for n, _, _ in _python_catalog(p)]
        got = stl_native.read_archive_members(str(p), names)
        assert got == _python_read(p, names)
        assert set(got) == {"renders/front.jpg", "info.json", "stored.url"}


def test_zip_read_bad_crc_member_left_out(tmp_path):
    good, bad = b"good data", b"bad! data"
    raw = bytearray(_raw_zip([(b"a.jpg", good, 0, b""), (b"b.jpg", bad, 0, b"")]))
    i = raw.index(bad)
    raw[i] ^= 0xFF                                   # corrupt b.jpg's stored bytes
    path = tmp_path / "t.zip"
    path.write_bytes(bytes(raw))
    got = stl_native.read_archive_members(str(path), ["a.jpg", "b.jpg"])
    assert got == _python_read(path, ["a.jpg", "b.jpg"]) == {"a.jpg": good}


def test_zip_read_duplicate_names_last_wins(tmp_path):
    path = tmp_path / "t.zip"
    path.write_bytes(_raw_zip([(b"same.jpg", b"first", 0, b""), (b"same.jpg", b"second", 0, b"")]))
    assert stl_native.read_archive_members(str(path), ["same.jpg"]) == _python_read(path, ["same.jpg"])


def test_zip_read_other_methods_left_to_python(tmp_path):
    path = tmp_path / "t.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_BZIP2) as zf:
        zf.writestr("a.jpg", b"x" * 1000)
    with pytest.raises(ValueError):
        stl_native.read_archive_members(str(path), ["a.jpg"])
    # ...while a catalog-only call and unrelated names still work natively.
    assert stl_native.read_archive_members(str(path), ["other.jpg"]) == {}


def _make_7z(tmp_path, empty_last=True, **kw):
    """Solid 7z with a directory and an empty file.

    7-Zip writes entries without data after the ones with data; py7zr keeps
    insertion order, so ``empty_last=False`` puts them inside the solid block.
    """
    import py7zr
    src = tmp_path / "src"
    (src / "renders").mkdir(parents=True)
    (src / "model.stl").write_bytes(b"solid" * 20000)
    (src / "renders" / "front ドラゴン.jpg").write_bytes(bytes(range(256)) * 300)
    (src / "renders" / "back.png").write_bytes(b"png" * 1000)
    (src / "info.json").write_bytes(b'{"a": 1}')
    (src / "empty.jpg").write_bytes(b"")
    entries = sorted(src.rglob("*"))
    if empty_last:
        entries.sort(key=lambda p: p.is_dir() or p.stat().st_size == 0)
    path = tmp_path / "t.7z"
    with py7zr.SevenZipFile(path, "w", **kw) as z:
        for p in entries:
            z.write(p, p.relative_to(src).as_posix())
    return path


SEVENZ_NAMES = ["renders/front ドラゴン.jpg", "info.json", "empty.jpg", "renders/back.png", "missing.jpg"]


@pytest.mark.parametrize("filters", [None, [{"id": 0x21}]], ids=["default", "lzma2"])
def test_7z_read_matches_py7zr(tmp_path, filters):
    path = _make_7z(tmp_path, **({"filters": filters} if filters else {}))
    got = stl_native.read_archive_members(str(path), SEVENZ_NAMES)
    assert got == _python_read(path, SEVENZ_NAMES)
    assert set(got) == {"renders/front ドラゴン.jpg", "info.json", "renders/back.png"}
    assert stl_native.read_archive_members(str(path), ["info.json"]) == {"info.json": b'{"a": 1}'}


def test_7z_empty_entries_inside_solid_block_left_to_python(tmp_path):
    # sevenz-rust2's BlockDecoder would silently drop the block's last files.
    path = _make_7z(tmp_path, empty_last=False)
    with pytest.raises(ValueError):
        stl_native.read_archive_members(str(path), SEVENZ_NAMES)
    assert len(A._read_members(path, SEVENZ_NAMES)) == 3      # via the Python reader


def test_7z_read_corrupt_raises(tmp_path):
    path = _make_7z(tmp_path)
    raw = bytearray(path.read_bytes())
    for k in range(40, 400):
        raw[k] ^= 0x55                               # damage the packed stream
    path.write_bytes(bytes(raw))
    with pytest.raises((OSError, ValueError)):
        stl_native.read_archive_members(str(path), ["renders/back.png", "info.json"])


@pytest.mark.parametrize("kind", ["zip", "7z"])
def test_extract_archive_contents_same_with_and_without_native(tmp_path, monkeypatch, kind):
    if kind == "zip":
        path = tmp_path / "t.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("model.stl", b"solid" * 1000)
            zf.writestr("img/a.jpg", b"\xff\xd8" + bytes(500))
            zf.writestr("info.json", b"{}")
    else:
        path = _make_7z(tmp_path)

    def run():
        names, stl, size = [], {}, []
        data = A.extract_archive_contents(path, all_names_out=names, stl_info_out=stl,
                                          uncompressed_size_out=size)
        return data, names, stl

    with_native = run()
    assert len(with_native[0]) == (2 if kind == "zip" else 3)
    monkeypatch.setattr(native, "module", lambda: None)    # the Python readers
    assert run()[0] == with_native[0]
    assert sorted(run()[1]) == sorted(with_native[1])
