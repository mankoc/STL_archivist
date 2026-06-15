"""
Runtime utilities for the optional AI tagging feature.

The AI tagger requires a pre-downloaded ONNX model created by
tools/create_ai_model.py.  Run that script once to download
Qwen2-VL-2B-Instruct ONNX weights to <data_dir>/ai_cache/qwen2vl_onnx/.

Inference uses raw onnxruntime sessions — no PyTorch required at runtime.
GPU: CUDAExecutionProvider (Linux, if cuDNN available) or DmlExecutionProvider
     (Windows, any GPU via DirectML).  CPU fallback is always available.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

QWEN_ONNX_SUBDIR = "qwen2vl_onnx"


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


def ai_model_dir() -> Optional[Path]:
    cache = ai_cache_dir()
    return (cache / QWEN_ONNX_SUBDIR) if cache else None


# ---------------------------------------------------------------------------
# CUDA shared-library preload (Linux only)
#
# onnxruntime's CUDAExecutionProvider needs the CUDA/cuDNN .so files to be
# loaded with RTLD_GLOBAL *before* onnxruntime is imported; LD_LIBRARY_PATH
# cannot be changed once the process is running.  We scan the nvidia.* pip
# packages installed alongside onnxruntime-gpu and preload them here.
# ---------------------------------------------------------------------------

_cuda_preloaded = False


def _preload_cuda_libs() -> None:
    global _cuda_preloaded
    if _cuda_preloaded:
        return
    _cuda_preloaded = True

    import ctypes
    import site as _site
    import sysconfig

    # Find site-packages directory
    site_dirs = _site.getsitepackages() or []
    purelib = sysconfig.get_path("purelib")
    if purelib:
        site_dirs = [purelib] + site_dirs

    # Ordered list of nvidia sub-packages to preload; exclude cu13 (ABI mismatch)
    _PKG_ORDER = [
        "nvidia/cuda_runtime",
        "nvidia/cublas",
        "nvidia/cufft",
        "nvidia/curand",
        "nvidia/cuda_nvrtc",
        "nvidia/cudnn",
    ]

    for site_dir in site_dirs:
        if not os.path.isdir(site_dir):
            continue
        for pkg in _PKG_ORDER:
            lib_dir = os.path.join(site_dir, pkg, "lib")
            if not os.path.isdir(lib_dir):
                continue
            for fname in sorted(os.listdir(lib_dir)):
                if not (fname.endswith(".so") or ".so." in fname):
                    continue
                fpath = os.path.join(lib_dir, fname)
                try:
                    ctypes.CDLL(fpath, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass
        break  # only need the first valid site-packages


# ---------------------------------------------------------------------------
# Activation — called once at startup from main.py
# ---------------------------------------------------------------------------

def activate_ai_packages() -> None:
    """Point HuggingFace cache at the data directory and preload CUDA libs."""
    cache = ai_cache_dir()
    if cache:
        os.environ.setdefault("HF_HOME", str(cache))

    if os.name != "nt":  # Linux/macOS: preload CUDA .so files
        _preload_cuda_libs()


# ---------------------------------------------------------------------------
# Readiness check
# ---------------------------------------------------------------------------

def is_ai_ready() -> bool:
    """Return True if the ONNX model and required packages are present."""
    activate_ai_packages()

    try:
        import onnxruntime       # noqa: F401
        from transformers import AutoTokenizer  # noqa: F401
    except ImportError:
        return False

    model_dir = ai_model_dir()
    if model_dir is None or not (model_dir / "config.json").exists():
        return False

    # At least one ONNX file must exist
    onnx_dir = model_dir / "onnx"
    if not onnx_dir.is_dir():
        return False
    return any(onnx_dir.glob("*.onnx"))
