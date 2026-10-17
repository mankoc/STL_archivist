#!/usr/bin/env python3
"""Build the Rust core (``rust/stl-py``) and install it as ``stl_native``.

Usage (from the project root, with the project's virtual environment):

    python tools/build_native.py            # optimised build (default)
    python tools/build_native.py --debug    # faster compile, slower code

The module is optional: without it the app runs its Python implementations.
Needs a Rust toolchain (rustup) and, on Windows, the Visual Studio C++ Build
Tools. See docs/rust-migration-plan.md.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "rust" / "stl-py" / "Cargo.toml"


def build(release: bool = True) -> int:
    """Run ``maturin develop`` into the environment of the running interpreter."""
    if shutil.which("cargo") is None:
        print("ERROR: cargo not found — install Rust from https://rustup.rs", file=sys.stderr)
        return 1
    # maturin installs into $VIRTUAL_ENV; point it at this interpreter's prefix
    # so `.venv\Scripts\python tools/build_native.py` targets that venv.
    env = dict(os.environ, VIRTUAL_ENV=sys.prefix)
    cmd = [sys.executable, "-m", "maturin", "develop", "--manifest-path", str(MANIFEST)]
    if release:
        cmd.append("--release")
    print("[native]", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--debug", action="store_true", help="unoptimised build")
    args = parser.parse_args()
    code = build(release=not args.debug)
    if code == 0:
        check = subprocess.run(
            [sys.executable, "-c", "import stl_native; print(stl_native.__version__)"],
            capture_output=True, text=True,
        )
        print(f"[native] stl_native {check.stdout.strip() or '(import failed)'} installed")
        code = check.returncode
    sys.exit(code)


if __name__ == "__main__":
    main()
