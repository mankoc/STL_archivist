"""
Runtime utilities for the optional AI tagging feature.

AI tagging runs Qwen2.5-VL locally through llama.cpp's ``llama-server`` — a
native binary.  That means there are **no Python AI packages** (no torch, no
transformers) and therefore no Python-version / ABI constraints on the packaged
exe.  On first use the user downloads two things into the data directory:

  1. Prebuilt llama.cpp binaries (CPU or CUDA)   -> ``<data_dir>/ai/bin/``
  2. A Qwen2.5-VL GGUF model + vision projector   -> ``<data_dir>/ai/models/``

The app then launches ``llama-server`` as a subprocess and talks to its
OpenAI-compatible HTTP API — see :mod:`app.utils.ai_server`.
"""
from __future__ import annotations

import logging
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pinned versions — bump these to upgrade the runtime or model.
# ---------------------------------------------------------------------------

#: llama.cpp release tag for the prebuilt binaries.
LLAMACPP_BUILD = "b9789"
_LLAMACPP_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMACPP_BUILD}"

#: Hugging Face GGUF repo (maintained by the llama.cpp team) and the two files
#: we need: the quantized model and the f16 vision projector.
MODEL_REPO  = "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF"
MODEL_FILE  = "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"
MMPROJ_FILE = "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf"
_HF_BASE = f"https://huggingface.co/{MODEL_REPO}/resolve/main"

#: Human-readable model name (shown in the UI).
MODEL_LABEL = "Qwen2.5-VL-3B-Instruct (GGUF)"

#: Supported install variants and their llama.cpp binary assets.
INSTALL_VARIANTS: dict[str, dict] = {
    "cpu": {
        "label": "CPU",
        "size":  "~50 MB runtime + ~3 GB model",
        "note":  "Works on any machine. Inference is slower.",
        "binary": f"llama-{LLAMACPP_BUILD}-bin-win-cpu-x64.zip",
        "extra":  [],
    },
    "cuda": {
        "label": "GPU — NVIDIA (CUDA 12.4)",
        "size":  "~500 MB runtime + ~3 GB model",
        "note":  "Requires an NVIDIA GPU and a recent driver. Much faster.",
        "binary": f"llama-{LLAMACPP_BUILD}-bin-win-cuda-12.4-x64.zip",
        "extra":  [f"cudart-llama-bin-win-cuda-12.4-x64.zip"],
    },
}

_VARIANT_MARKER = "variant.txt"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _get_data_dir() -> Optional[Path]:
    try:
        from app.core.settings import get_data_dir
        d = get_data_dir()
        return Path(d) if d else None
    except Exception:
        return None


def ai_dir() -> Optional[Path]:
    d = _get_data_dir()
    return (d / "ai") if d else None


def bin_dir() -> Optional[Path]:
    d = ai_dir()
    return (d / "bin") if d else None


def models_dir() -> Optional[Path]:
    d = ai_dir()
    return (d / "models") if d else None


def llama_server_exe() -> Optional[Path]:
    """Return the path to ``llama-server.exe`` if installed, else ``None``."""
    b = bin_dir()
    if not b:
        return None
    direct = b / "llama-server.exe"
    if direct.exists():
        return direct
    # Some release zips nest the binaries in a subfolder.
    for p in b.rglob("llama-server.exe"):
        return p
    return None


def model_paths() -> "tuple[Optional[Path], Optional[Path]]":
    """Return ``(model_gguf, mmproj_gguf)`` paths (which may not exist yet)."""
    m = models_dir()
    if not m:
        return None, None
    return m / MODEL_FILE, m / MMPROJ_FILE


def installed_variant() -> Optional[str]:
    """Return the variant ("cpu"/"cuda") recorded at install time, or ``None``."""
    b = bin_dir()
    if not b:
        return None
    marker = b / _VARIANT_MARKER
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Readiness check
# ---------------------------------------------------------------------------

def is_ai_ready() -> bool:
    """True when the llama-server binary and both GGUF files are present."""
    exe = llama_server_exe()
    model, mmproj = model_paths()
    return bool(
        exe
        and model and model.exists()
        and mmproj and mmproj.exists()
    )


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path,
              progress: Callable[[str], None] | None, label: str) -> None:
    """Stream *url* to *dest* (atomic via a .part file) with progress updates."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "stlLibrary"})
    try:
        with urllib.request.urlopen(req) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            done = 0
            with open(tmp, "wb") as f:
                while True:
                    buf = resp.read(1 << 20)  # 1 MB chunks
                    if not buf:
                        break
                    f.write(buf)
                    done += len(buf)
                    if progress:
                        if total:
                            progress(f"{label}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")
                        else:
                            progress(f"{label}: {done / 1e6:.0f} MB")
        tmp.replace(dest)
    except BaseException:
        # Don't leave a partial .part file behind on network error or cancel.
        tmp.unlink(missing_ok=True)
        raise


def install_ai(variant: str = "cpu",
               progress: Callable[[str], None] | None = None) -> None:
    """Download llama.cpp binaries and the Qwen2.5-VL GGUF model.

    Args:
        variant:  One of the keys in :data:`INSTALL_VARIANTS` ("cpu" or "cuda").
        progress: Optional callback(message) for status updates.

    Raises:
        RuntimeError: If the data directory is not configured.
        ValueError:   If *variant* is unknown.
    """
    meta = INSTALL_VARIANTS.get(variant)
    if meta is None:
        raise ValueError(f"Unknown variant: {variant!r}")

    bd, md = bin_dir(), models_dir()
    if bd is None or md is None:
        raise RuntimeError(
            "Data directory is not configured.\n"
            "Open Settings and set a data directory first."
        )
    bd.mkdir(parents=True, exist_ok=True)
    md.mkdir(parents=True, exist_ok=True)

    # 1. llama.cpp binaries (+ optional CUDA runtime) extracted into bin/
    for asset in [meta["binary"], *meta["extra"]]:
        url = f"{_LLAMACPP_BASE}/{asset}"
        with tempfile.TemporaryDirectory() as td:
            zpath = Path(td) / asset
            if progress:
                progress(f"Downloading {asset}…")
            _download(url, zpath, progress, asset)
            if progress:
                progress(f"Extracting {asset}…")
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(bd)

    # Record which variant is installed so the server knows whether to offload
    # layers to the GPU.
    (bd / _VARIANT_MARKER).write_text(variant, encoding="utf-8")

    # 2. model files into models/ (skip any already present)
    model, mmproj = model_paths()
    for fname, dest in ((MODEL_FILE, model), (MMPROJ_FILE, mmproj)):
        if dest.exists():
            continue
        url = f"{_HF_BASE}/{fname}"
        if progress:
            progress(f"Downloading {fname}…")
        _download(url, dest, progress, fname)

    if progress:
        progress("Done.")
