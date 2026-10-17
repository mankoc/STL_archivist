#!/usr/bin/env python3
"""
Build the stlLibrary distribution package.

Usage:
    python build_dist.py [--version VERSION]

Produces:
    dist/stlLibrary-<version>.exe
    dist/stlLibrary-<version>.zip

The exe contains no AI packages.  On first "Generate" click the user is
prompted to download torch + transformers + qwen-vl-utils (~1.5 GB CPU or
~3 GB CUDA) into their data directory.
"""
import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _get_version() -> str:
    """Try to infer version from git tag, fall back to 'dev'."""
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return out
    except subprocess.CalledProcessError:
        pass
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, text=True,
        ).strip()
        return f"dev-{sha}"
    except subprocess.CalledProcessError:
        return "dev"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build stlLibrary distribution")
    parser.add_argument("--version", default=None, help="Override version string")
    parser.add_argument("--no-native", action="store_true",
                        help="Skip building the Rust core (the exe then uses the Python paths)")
    args = parser.parse_args()

    version = args.version or _get_version()
    print(f"[build] version: {version}")

    # 1. Clean previous build artifacts
    dist_dir = ROOT / "dist"
    build_dir = ROOT / "build"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)
    dist_dir.mkdir(exist_ok=True)

    # 2. Build the optional Rust core so PyInstaller bundles it (stl_native).
    if not args.no_native:
        print("[build] building native core…")
        from tools.build_native import build as build_native
        if build_native(release=True) != 0:
            print("WARNING: native core build failed — the exe will use the Python "
                  "implementations", file=sys.stderr)

    # 3. Build with PyInstaller
    print("[build] running PyInstaller…")
    _run([sys.executable, "-m", "PyInstaller", "stl_database.spec", "--noconfirm"])

    bare_exe = dist_dir / "stlLibrary.exe"
    if not bare_exe.exists():
        print("ERROR: PyInstaller did not produce dist/stlLibrary.exe", file=sys.stderr)
        sys.exit(1)

    # 4. Rename exe to versioned name
    versioned_exe = dist_dir / f"stlLibrary-{version}.exe"
    bare_exe.rename(versioned_exe)

    exe_mb = versioned_exe.stat().st_size / 1_048_576
    print(f"[build] exe: {versioned_exe.name}  ({exe_mb:.1f} MB)")

    # 5. Create zip
    zip_path = dist_dir / f"stlLibrary-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(versioned_exe, versioned_exe.name)

    zip_mb = zip_path.stat().st_size / 1_048_576
    print(f"[build] zip: {zip_path.name}  ({zip_mb:.1f} MB)")
    print(f"\n[build] done — artefacts in {dist_dir}")


if __name__ == "__main__":
    main()
