"""
Runtime utilities for the optional AI tagging feature.

Packages (torch, transformers, qwen-vl-utils) are installed via requirements.txt.
The Qwen2-VL model weights (~4.4 GB) are downloaded by HuggingFace on first use
into <data_dir>/ai_cache/.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


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


def ai_cache_dir() -> Optional[Path]:
    d = _get_data_dir()
    return (d / "ai_cache") if d else None


# ---------------------------------------------------------------------------
# Activation — called once at startup from main.py
# ---------------------------------------------------------------------------

def activate_ai_packages() -> None:
    """Point HuggingFace cache at the data directory."""
    cache = ai_cache_dir()
    if cache:
        os.environ.setdefault("HF_HOME", str(cache))


# ---------------------------------------------------------------------------
# Readiness check
# ---------------------------------------------------------------------------

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
