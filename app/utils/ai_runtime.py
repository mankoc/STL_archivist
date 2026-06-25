"""
Runtime utilities for the optional AI tagging feature.

torch + torchvision + transformers + qwen-vl-utils are NOT bundled in the
exe.  On first use the user is prompted to download them via AISetupDialog,
which installs them into <data_dir>/ai_packages/ using pip.
activate_ai_packages() adds that directory to sys.path so imports work
transparently afterwards.

Supported variants:
  "cpu"   — plain torch from PyPI (~1.5 GB total with transformers)
  "cu128" — CUDA 12.8 wheels from download.pytorch.org (~3 GB total)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _get_data_dir() -> Optional[Path]:
    try:
        from app.ui.config_dialog import get_data_dir
        d = get_data_dir()
        return Path(d) if d else None
    except Exception:
        return None


def ai_packages_dir() -> Optional[Path]:
    d = _get_data_dir()
    return (d / "ai_packages") if d else None


def ai_cache_dir() -> Optional[Path]:
    d = _get_data_dir()
    return (d / "ai_cache") if d else None


# ---------------------------------------------------------------------------
# Activation — called once at startup and before every import of torch
# ---------------------------------------------------------------------------

def activate_ai_packages() -> None:
    """Add <data_dir>/ai_packages to sys.path and set HF_HOME.

    Safe to call before packages are installed — no-op in that case.
    Must be called before any `import torch`.
    """
    pkg = ai_packages_dir()
    if pkg and pkg.exists():
        p = str(pkg)
        if p not in sys.path:
            sys.path.insert(0, p)

    cache = ai_cache_dir()
    if cache:
        os.environ.setdefault("HF_HOME", str(cache))


# ---------------------------------------------------------------------------
# Readiness check
# ---------------------------------------------------------------------------

def is_ai_ready() -> bool:
    """Return True if torch, transformers, and qwen_vl_utils are importable."""
    activate_ai_packages()
    try:
        import torch          # noqa: F401
        import transformers   # noqa: F401
        import qwen_vl_utils  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# On-demand installer
# ---------------------------------------------------------------------------

#: Supported install variants and their metadata.
INSTALL_VARIANTS: dict[str, dict] = {
    "cpu": {
        "label":    "CPU",
        "size":     "~1.5 GB",
        "note":     "Works on any machine. Inference is slower.",
        "torch_index": None,
        "torch":    "torch",
        "tv":       "torchvision",
    },
    "cu128": {
        "label":    "GPU — CUDA 12.8 (NVIDIA)",
        "size":     "~3 GB",
        "note":     "Requires an NVIDIA GPU and driver ≥ 527.41.",
        "torch_index": "https://download.pytorch.org/whl/cu128",
        "torch":    "torch==2.11.0+cu128",
        "tv":       "torchvision==0.26.0+cu128",
    },
}

# Packages installed from PyPI for all variants
_COMMON_PACKAGES = [
    "transformers>=4.48.0",
    "accelerate>=0.33.0",
    "qwen-vl-utils",
]


def install_ai_packages(
    variant: str = "cpu",
    progress: Callable[[str], None] | None = None,
) -> None:
    """Download and install torch + AI packages into <data_dir>/ai_packages/.

    Installs:
      - torch + torchvision (CPU or CUDA 12.8)
      - transformers, accelerate, qwen-vl-utils

    Args:
        variant:  One of the keys in INSTALL_VARIANTS ("cpu" or "cu128").
        progress: Optional callback(message) for status updates.
    """
    pkg_dir = ai_packages_dir()
    if pkg_dir is None:
        raise RuntimeError(
            "Data directory is not configured.\n"
            "Open Settings and set a data directory first."
        )
    pkg_dir.mkdir(parents=True, exist_ok=True)

    meta = INSTALL_VARIANTS.get(variant)
    if meta is None:
        raise ValueError(f"Unknown variant: {variant!r}")

    def _run_pip(*args: str) -> None:
        import shutil
        import subprocess as _sp

        # Inside a PyInstaller bundle sys.executable is the app exe, not Python.
        # pip._internal is also partially frozen and unreliable there.
        # Always resolve the real Python interpreter via PATH.
        if getattr(sys, "frozen", False):
            python = shutil.which("python") or shutil.which("python3")
            if python is None:
                raise RuntimeError(
                    "Python not found on PATH.\n"
                    "Please ensure Python is installed and added to your PATH."
                )
        else:
            python = sys.executable

        result = _sp.run(
            [python, "-m", "pip"] + list(args),
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)

    target = str(pkg_dir)
    base_args = ["install", "--target", target, "--quiet", "--no-warn-script-location"]

    label = meta["label"]

    # Step 1: torch + torchvision
    if progress:
        progress(f"Downloading torch ({label})…")

    torch_args = list(base_args) + [meta["torch"], meta["tv"]]
    if meta["torch_index"]:
        # --no-deps avoids pulling CPU torch as a transitive dep on the cu128 index
        torch_args += ["--index-url", meta["torch_index"], "--no-deps"]

    _run_pip(*torch_args)

    # Step 2: transformers + accelerate + qwen-vl-utils (always from PyPI)
    if progress:
        progress("Downloading transformers and AI utilities…")

    _run_pip(*base_args, *_COMMON_PACKAGES)

    if progress:
        progress("Done.")

    activate_ai_packages()
