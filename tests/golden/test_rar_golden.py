"""Golden tests: the native RAR catalog and reads match rarfile.

Archives are built with WinRAR's ``Rar.exe`` (skipped when it is missing):
RAR5 (WinRAR 7 no longer writes RAR4; the reference gate covers it), stored /
compressed / solid, Unicode names, directories,
empty members, comments, encrypted headers and data, volumes.  For each,
the native catalog equals ``rarfile``'s, or the native side refuses
(``ValueError`` / ``OSError``) and the Python reader stays in charge.
"""
import os
import random
import subprocess
from pathlib import Path

import pytest

stl_native = pytest.importorskip("stl_native")

from app.crawler import archive as A

RAR = r"C:\Program Files\WinRAR\Rar.exe"
UNRAR = r"C:\Program Files\WinRAR\UnRAR.exe"
pytestmark = pytest.mark.skipif(not (os.path.isfile(RAR) and os.path.isfile(UNRAR)), reason="WinRAR not installed")

VARIANTS = {
    "rar5": [], "stored5": ["-m0"], "solid5": ["-s"], "best": ["-m5"], "comment": ["-zcomment.txt"], "headers": ["-hpsecret"],
    "data_pw": ["-psecret"], "volumes": ["-v20k"], "ctime": ["-tsc", "-tsa"], "hashes": ["-htb"],
}


def _tree(root: Path) -> None:
    rng = random.Random(4)
    (root / "Dragon Ñ").mkdir(parents=True)
    (root / "Dragon Ñ" / "body_presupported.stl").write_bytes(bytes(rng.getrandbits(8) for _ in range(30000)))
    (root / "Dragon Ñ" / "a.png").write_bytes(b"\x89PNG" + b"x" * 500)
    (root / "Dragon Ñ" / "empty.txt").write_bytes(b"")
    (root / "ドラゴン.stl").write_bytes(b"solid " * 300)
    (root / "info.json").write_text('{"name": "Dragon"}')
    (root / "sub dir").mkdir()
    (root / "sub dir" / "deep.url").write_bytes(b"[InternetShortcut]\nURL=http://x\n")


def _make(tmp_path: Path, name: str, switches: list[str]) -> Path:
    src = tmp_path / f"src_{name}"
    _tree(src)
    (src / "comment.txt").write_text("hello")
    out = tmp_path / f"{name}.rar"
    subprocess.run([RAR, "a", "-r", "-ep1", "-idq", "-y", *switches, str(out), str(src / "*")],
                   check=True, cwd=src, creationflags=0x08000000)
    if not out.exists():                 # volumes: name.part1.rar
        out = next(tmp_path.glob(f"{name}.part1.rar"))
    return out


@pytest.mark.parametrize("name", list(VARIANTS))
def test_rar_catalog_and_reads(tmp_path, name):
    path = _make(tmp_path, name, VARIANTS[name])
    with A._RarReader(path, UNRAR) as rr:
        try:
            py_members = [(m.name, m.crc, m.size) for m in rr.iter_members()]
        except Exception as exc:          # rarfile itself fails: the native side must not succeed
            py_members = exc
    try:
        rs_members = stl_native.archive_members(str(path))
    except (ValueError, OSError):
        assert name in ("comment", "volumes"), f"{name}: native refused a catalog"
        return
    assert rs_members == py_members, name
    names = [m[0] for m in rs_members] + ["missing.stl"]
    with A._RarReader(path, UNRAR) as rr:
        py_data = rr.read_batch(names)
    try:
        rs_data = stl_native.read_archive_members(str(path), names, UNRAR)
    except (ValueError, OSError):
        assert name in ("data_pw", "hashes"), f"{name}: native refused the reads"
        return
    assert rs_data == py_data, name
    if name not in ("headers",):
        assert py_data, name


def test_archive_module_uses_native_for_rar(tmp_path, monkeypatch):
    path = _make(tmp_path, "plain", [])
    monkeypatch.setattr(A, "_RarReader", None)     # the Python reader must not be needed
    assert A.list_contents(path, UNRAR)
    assert A.archive_hash(path, UNRAR) != ""
    assert A.read_file(path, "info.json", UNRAR) == b'{"name": "Dragon"}'
