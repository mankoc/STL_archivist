"""Optional learned image embeddings (CLIP via ONNX Runtime).

This adds *semantic* image similarity — matching the same model rendered from a
different angle, lighting, or recolour far better than the perceptual ``dhash``.
It is an **optional** feature: it needs ``onnxruntime`` and ``numpy`` installed
*and* a CLIP image-encoder ONNX model present.  When anything is missing the
module degrades gracefully — :func:`is_available` returns ``False`` and the UI
disables the embedding options instead of erroring.

Model lookup order (first existing wins):
  1. ``$STLLIBRARY_CLIP_MODEL`` — full path to the ``.onnx`` file
  2. ``<data_dir>/models/clip_image_encoder.onnx``
  3. ``<package_root>/resources/models/clip_image_encoder.onnx``

The model is expected to take a float32 tensor of shape ``(N, 3, 224, 224)``
(RGB, CLIP-normalised) and output ``(N, D)`` image embeddings; any embedding
dimension ``D`` works as long as every stored vector uses the same model.

Heavy imports (``numpy``, ``onnxruntime``) are deferred into functions so that
merely importing this module never pulls them in.
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

log = logging.getLogger(__name__)

MODEL_FILENAME = "clip_image_encoder.onnx"

# Stored alongside each embedding so swapping the model invalidates old vectors.
EMBED_MODEL_ID = "clip-vit-b32-onnx"

_INPUT_SIZE = 224
# CLIP image preprocessing normalisation constants.
_MEAN = (0.48145466, 0.4578275, 0.40821073)
_STD  = (0.26862954, 0.26130258, 0.27577711)

_session = None          # cached onnxruntime.InferenceSession
_session_tried = False   # don't retry a failed load on every call
_input_name: Optional[str] = None
_output_name: Optional[str] = None

# Images per GPU inference call.  GPU throughput scales roughly linearly with
# batch size up to ~64; CPU sees little benefit above 1.
INFERENCE_BATCH_SIZE = 32


def _deps_ok() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def model_path() -> Optional[Path]:
    """Return the path to the CLIP ONNX model, or None if not found."""
    candidates: list[Path] = []
    env = os.environ.get("STLLIBRARY_CLIP_MODEL")
    if env:
        candidates.append(Path(env))
    try:
        from app.ui.config_dialog import get_data_dir
        candidates.append(Path(get_data_dir()) / "models" / MODEL_FILENAME)
    except Exception:
        pass
    candidates.append(
        Path(__file__).parent.parent.parent / "resources" / "models" / MODEL_FILENAME
    )
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def is_available() -> bool:
    """True if embeddings can be computed (deps installed and model present)."""
    return _deps_ok() and model_path() is not None


def unavailable_reason() -> str:
    """Human-readable explanation of why embeddings are unavailable ('' if available)."""
    if not _deps_ok():
        return "Install 'onnxruntime' and 'numpy' to enable AI image embeddings."
    if model_path() is None:
        return (
            f"CLIP model not found. Place '{MODEL_FILENAME}' in "
            f"<data dir>/models/ or set the STLLIBRARY_CLIP_MODEL environment variable."
        )
    return ""


def _get_session():
    """Lazily build and cache the ONNX inference session (None if unavailable)."""
    global _session, _session_tried, _input_name, _output_name
    if _session is not None:
        return _session
    if _session_tried:
        return None
    _session_tried = True
    if not is_available():
        return None
    try:
        import onnxruntime as ort
        path = model_path()
        # Prefer GPU providers in priority order; ORT silently skips unavailable ones.
        providers = [
            "CUDAExecutionProvider",       # NVIDIA (requires onnxruntime-gpu)
            "DmlExecutionProvider",        # DirectML — Windows AMD/Intel/NVIDIA
            "CoreMLExecutionProvider",     # Apple Silicon / macOS
            "CPUExecutionProvider",        # always available
        ]
        sess = ort.InferenceSession(str(path), providers=providers)
        active = sess.get_providers()[0]
        _input_name = sess.get_inputs()[0].name
        _output_name = sess.get_outputs()[0].name
        _session = sess
        log.info("Loaded CLIP image encoder: %s (provider: %s)", path, active)
        return sess
    except Exception as exc:
        log.error("Failed to load CLIP ONNX model: %s", exc)
        return None


def _preprocess(jpeg_bytes: bytes):
    """Decode JPEG and return a CLIP-normalised (1, 3, 224, 224) float32 tensor."""
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    w, h = img.size
    scale = _INPUT_SIZE / min(w, h)
    nw, nh = max(_INPUT_SIZE, round(w * scale)), max(_INPUT_SIZE, round(h * scale))
    img = img.resize((nw, nh), Image.BICUBIC)
    left = (nw - _INPUT_SIZE) // 2
    top = (nh - _INPUT_SIZE) // 2
    img = img.crop((left, top, left + _INPUT_SIZE, top + _INPUT_SIZE))

    arr = np.asarray(img, dtype=np.float32) / 255.0          # H, W, C
    arr = (arr - np.array(_MEAN, dtype=np.float32)) / np.array(_STD, dtype=np.float32)
    arr = arr.transpose(2, 0, 1)[None, ...]                  # 1, C, H, W
    return np.ascontiguousarray(arr, dtype=np.float32)


def embed_jpeg(jpeg_bytes: bytes) -> "Optional[np.ndarray]":
    """Return an L2-normalised float32 embedding for *jpeg_bytes*, or None."""
    sess = _get_session()
    if sess is None or not jpeg_bytes:
        return None
    try:
        import numpy as np
        x = _preprocess(jpeg_bytes)
        out = sess.run([_output_name], {_input_name: x})[0]
        vec = np.asarray(out, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)
    except Exception as exc:
        log.debug("Embedding computation failed: %s", exc)
        return None


def embed_batch(jpeg_list: "list[bytes]") -> "list[Optional[np.ndarray]]":
    """Return L2-normalised embeddings for a list of JPEG byte strings.

    Images are stacked into a single ``(N, 3, 224, 224)`` tensor and run
    through the model in one call, which makes far better use of GPU
    parallelism than calling :func:`embed_jpeg` in a loop.

    Returns a list of the same length as *jpeg_list*; entries are ``None``
    for images that failed to preprocess or embed.
    """
    sess = _get_session()
    if sess is None or not jpeg_list:
        return [None] * len(jpeg_list)
    import numpy as np
    tensors: list = []
    indices: list[int] = []
    results: list = [None] * len(jpeg_list)
    for i, raw in enumerate(jpeg_list):
        if not raw:
            continue
        try:
            tensors.append(_preprocess(raw))   # (1, 3, 224, 224)
            indices.append(i)
        except Exception as exc:
            log.debug("Preprocess failed for image %d: %s", i, exc)
    if not tensors:
        return results
    try:
        batch = np.concatenate(tensors, axis=0)   # (N, 3, 224, 224)
        out = sess.run([_output_name], {_input_name: batch})[0]  # (N, D)
        vecs = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms > 0, norms, 1.0)
        vecs = vecs / norms
        for pos, idx in enumerate(indices):
            results[idx] = vecs[pos].astype(np.float32)
    except Exception as exc:
        log.debug("Batch embedding failed: %s", exc)
    return results


def to_blob(vec) -> bytes:
    """Serialise an embedding vector to raw float32 bytes for DB storage."""
    import numpy as np
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob) -> "Optional[np.ndarray]":
    """Deserialise raw float32 bytes back into a 1-D embedding array."""
    if not blob:
        return None
    try:
        import numpy as np
        return np.frombuffer(blob, dtype=np.float32)
    except Exception:
        return None
