"""
Runtime utilities for the optional AI tagging feature.

On first use, packages (~400 MB) and the Qwen2-VL model (~4.4 GB) are
downloaded into <data_dir>/ai_packages/ and <data_dir>/ai_cache/.
No system-wide install or admin rights are required.

PyInstaller note:
    pip must be included in the frozen build so install_ai_packages() works
    inside the packaged .exe.  See stlLibrary.spec for the required
    collect_all('pip') additions.
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
# Activation — call once at app startup
# ---------------------------------------------------------------------------

def activate_ai_packages() -> None:
    """Add <data_dir>/ai_packages to sys.path and set HF_HOME.

    Safe to call before packages are installed — it is a no-op in that case.
    Must be called before any `import torch` / `import transformers`.
    """
    pkg = ai_packages_dir()
    if pkg and pkg.exists():
        p = str(pkg)
        if p not in sys.path:
            sys.path.insert(0, p)

    cache = ai_cache_dir()
    if cache:
        # HuggingFace will store downloaded models here
        os.environ["HF_HOME"] = str(cache)


def is_ai_ready() -> bool:
    """Return True if all required AI packages are importable."""
    activate_ai_packages()
    try:
        import torch          # noqa: F401
        import transformers   # noqa: F401
        import qwen_vl_utils  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Installer — runs inside a QThread (see AISetupDialog)
# ---------------------------------------------------------------------------

def install_ai_packages(
    progress: Callable[[str], None] | None = None,
) -> None:
    """Download and install AI packages into <data_dir>/ai_packages/.

    Uses pip bundled with the frozen app (stlLibrary.spec includes
    collect_all('pip')).  Falls back to a subprocess call when running from
    source so developers don't need a special build.

    Args:
        progress: optional callback(message) for status updates.
    """
    pkg_dir = ai_packages_dir()
    if pkg_dir is None:
        raise RuntimeError(
            "Data directory is not configured.\n"
            "Open Settings and set a data directory first."
        )
    pkg_dir.mkdir(parents=True, exist_ok=True)

    def _run_pip(*args: str) -> None:
        try:
            # Preferred path: pip bundled inside the frozen .exe
            from pip._internal.cli.main import main as _pip
            code = _pip(list(args))
            if code not in (0, None):
                raise RuntimeError(f"pip failed (exit code {code})")
        except ImportError:
            # Development fallback: call pip via subprocess
            import subprocess
            result = subprocess.run(
                [sys.executable, "-m", "pip"] + list(args),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)

    if progress:
        progress("Downloading PyTorch (CPU) — ~300 MB…")
    _run_pip(
        "install", "--target", str(pkg_dir),
        "--index-url", "https://download.pytorch.org/whl/cpu",
        "--quiet", "--no-warn-script-location",
        "torch", "torchvision",
    )

    if progress:
        progress("Downloading transformers & utilities — ~100 MB…")
    _run_pip(
        "install", "--target", str(pkg_dir),
        "--quiet", "--no-warn-script-location",
        "transformers>=4.48.0",
        "accelerate>=0.26.0",
        "qwen-vl-utils>=0.0.8",
    )

    activate_ai_packages()
