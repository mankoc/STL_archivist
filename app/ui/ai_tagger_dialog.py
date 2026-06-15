"""
AI Tagger dialog — uses Qwen2-VL to suggest tags and metadata for a model.
Images are read from LMDB; inference runs on a background QThread.

Supports single or multiple models: when multiple model IDs are passed the
dialog navigates through them one by one.  Changes are written to the database
only when the user clicks "Save" / "Save & Next".
"""
from __future__ import annotations

import io
import warnings
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRect, QPoint, QSize
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QWidget,
    QSizePolicy, QFrame, QLayout, QTextEdit,
)

from app.database.db import session_scope, get_session
from app.database.models import STLModel, Tag, Category, Sculptor, Scale
from app.utils.thumbnailer import get_thumbnail_bytes


QWEN_ONNX_DIR = "qwen2vl_onnx"   # subdir inside ai_cache written by tools/create_ai_model.py
MAX_IMAGE_SIDE = 448              # PIL pre-resize limit per image before ONNX processing

_VALID_SOURCES = {"patreon", "kickstarter", "myminifactory", "unknown"}

# Qwen2-VL token IDs (fixed for this model family)
_IMAGE_TOKEN_ID        = 151655   # <|image_pad|>
_VISION_START_TOKEN_ID = 151652   # <|vision_start|>

# ONNX model constants (Qwen2-VL-2B)
_NUM_LAYERS   = 28
_NUM_KV_HEADS = 2
_HEAD_DIM     = 128

# Module-level session cache: reloaded when provider or variant changes
_cached_sessions: dict | None = None
_cached_proc     = None
_cached_img_proc = None
_cached_provider: str | None = None
_cached_variant:  str | None = None


# ---------------------------------------------------------------------------
# Helpers shared between prompt building and the inference thread
# ---------------------------------------------------------------------------

def _detect_variant(onnx_dir: "Path", is_cpu: bool) -> Optional[str]:
    """Return the first available ONNX variant that suits the provider."""
    gpu_order = ("fp16", "q4f16", "int8", "q4")
    cpu_order = ("q4", "int8", "q4f16", "fp16")
    for v in (cpu_order if is_cpu else gpu_order):
        if (onnx_dir / f"vision_encoder_{v}.onnx").exists():
            return v
    return None


def _preprocess_images_numpy(img_proc, pil_images: list) -> tuple:
    """Run Qwen2VLImageProcessor._preprocess per image; return (pixel_values, grid_thw)."""
    import numpy as np
    all_patches, all_grids = [], []
    for img in pil_images:
        patches, (gt, gh, gw) = img_proc._preprocess(
            [img],
            do_resize=True,
            size={"shortest_edge": img_proc.min_pixels, "longest_edge": img_proc.max_pixels},
            resample=img_proc.resample,
            do_rescale=True,
            rescale_factor=img_proc.rescale_factor,
            do_normalize=True,
            image_mean=img_proc.image_mean,
            image_std=img_proc.image_std,
            patch_size=img_proc.patch_size,
            temporal_patch_size=img_proc.temporal_patch_size,
            merge_size=img_proc.merge_size,
            do_convert_rgb=img_proc.do_convert_rgb,
        )
        all_patches.append(patches)
        all_grids.append([gt, gh, gw])
    return (
        np.concatenate(all_patches, axis=0).astype(np.float32),
        np.array(all_grids, dtype=np.int64),
    )


def _compute_mrope_position_ids(input_ids_arr, grid_thw_arr, spatial_merge: int = 2):
    """
    Port of Qwen2VLForConditionalGeneration.get_rope_index() to numpy.

    Returns position_ids of shape [3, batch, seq_len] (int64).
    """
    import numpy as np
    batch, seq_len = input_ids_arr.shape
    pos = np.ones((3, batch, seq_len), dtype=np.int64)
    for b in range(batch):
        ids = input_ids_arr[b].tolist()
        # Count images by inspecting tokens after each vision_start
        vs_positions = [j for j, x in enumerate(ids) if x == _VISION_START_TOKEN_ID]
        img_nums = sum(
            1 for j in vs_positions
            if j + 1 < len(ids) and ids[j + 1] == _IMAGE_TOKEN_ID
        )
        parts: list = []
        st = 0
        img_idx = 0
        for _ in range(img_nums):
            try:
                ed = ids.index(_IMAGE_TOKEN_ID, st)
            except ValueError:
                break
            t = int(grid_thw_arr[img_idx, 0])
            gh = int(grid_thw_arr[img_idx, 1]) // spatial_merge
            gw = int(grid_thw_arr[img_idx, 2]) // spatial_merge
            img_idx += 1
            base = int(parts[-1].max()) + 1 if parts else 0
            # Text before this image (includes <|vision_start|>)
            if ed > st:
                tp = (np.arange(ed - st, dtype=np.int64) + base)[np.newaxis]
                parts.append(np.tile(tp, (3, 1)))
                base = int(parts[-1].max()) + 1
            # Image grid positions (t, h, w)
            ti = np.repeat(np.arange(t, dtype=np.int64), gh * gw)
            hi = np.tile(np.repeat(np.arange(gh, dtype=np.int64), gw), t)
            wi = np.tile(np.arange(gw, dtype=np.int64), gh * t)
            parts.append(np.stack([ti, hi, wi]) + base)
            st = ed + t * gh * gw
        # Remaining text (includes <|vision_end|> and the rest)
        if st < len(ids):
            base = int(parts[-1].max()) + 1 if parts else 0
            tp = (np.arange(len(ids) - st, dtype=np.int64) + base)[np.newaxis]
            parts.append(np.tile(tp, (3, 1)))
        if parts:
            pos[:, b, :] = np.concatenate(parts, axis=1)
    return pos


def _build_tags_prompt(stems: list[str], archive_path: str) -> str:
    filenames_str = ", ".join(f'"{s}"' for s in stems) if stems else "(none)"
    intro = (
        f"These {len(stems)} images all show the same 3D printable miniature model from different angles. "
        if len(stems) > 1 else
        "This image shows a 3D printable miniature model. "
    )
    return (
        intro
        + f'Full archive path: "{archive_path}". '
        + f"Image file names: {filenames_str}. "
        "Use the full path and filenames as hints alongside the images to identify the character or subject. "
        "Output ONLY a comma-separated list of descriptive tags. "
        "Include: character name (if recognizable), franchise or source material, "
        "character type, genre, visual style, and any other relevant descriptors. "
        "Output ONLY the comma-separated tags, no other text."
    )


def _build_meta_prompt(archive_path: str) -> str:
    return (
        "This is a 3D printable miniature model. "
        f'Full archive path: "{archive_path}". '
        "Based on the images and the full path (folder structure encodes sculptor, "
        "series, and source information), output metadata in this EXACT format "
        "(one line per field, leave the value empty if unknown, no extra text):\n"
        "TITLE: <model title>\n"
        "CREATOR: <publisher, brand or author name>\n"
        "SCULPTOR: <artist or sculptor name>\n"
        "CATEGORY: <one of: Fantasy, SciFi, Historical, Modern, Animals, Vehicle, Other>\n"
        "SCALE: <miniature scale, e.g. 28mm, 32mm, 1/35, 54mm — or empty>\n"
        "SOURCE: <one of: patreon, kickstarter, myminifactory — or empty>\n"
        "YEAR: <4-digit release year — or empty>\n"
        "DESCRIPTION: <one sentence describing the model>\n"
        "Output ONLY these 8 lines."
    )


# ---------------------------------------------------------------------------
# Flow layout for tag pills
# ---------------------------------------------------------------------------

class _FlowLayout(QLayout):
    """Arranges child widgets left-to-right with automatic line wrapping."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._items: list = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._do_layout(QRect(0, 0, w, 0), test=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        sz = QSize()
        for item in self._items:
            sz = sz.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return QSize(sz.width() + m.left() + m.right(),
                     sz.height() + m.top() + m.bottom())

    def _do_layout(self, rect: QRect, test: bool) -> int:
        m = self.contentsMargins()
        x0 = rect.x() + m.left()
        y = rect.y() + m.top()
        right_edge = rect.right() - m.right()
        x, row_h = x0, 0

        for item in self._items:
            w, h = item.sizeHint().width(), item.sizeHint().height()
            if x + w > right_edge and row_h > 0:
                x, y, row_h = x0, y + row_h + 6, 0
            if not test:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x += w + 6
            row_h = max(row_h, h)

        return y + row_h - rect.y() + m.bottom()


# ---------------------------------------------------------------------------
# Inference worker (QThread subclass)
# ---------------------------------------------------------------------------

class _InferenceThread(QThread):
    status     = pyqtSignal(str, str)   # (text, css_color)
    tags_ready = pyqtSignal(list)
    meta_ready = pyqtSignal(dict)
    error      = pyqtSignal(str)

    def __init__(
        self,
        image_bytes_list: list[bytes],
        stems: list[str],
        archive_path: str,
    ) -> None:
        super().__init__()
        self._images       = image_bytes_list
        self._stems        = stems
        self._archive_path = archive_path

    def run(self) -> None:
        global _cached_sessions, _cached_proc, _cached_img_proc
        global _cached_provider, _cached_variant
        try:
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=UserWarning)

            try:
                import numpy as np
                import onnxruntime as ort
                from transformers import AutoProcessor
                from transformers.models.qwen2_vl.image_processing_qwen2_vl import (
                    Qwen2VLImageProcessor,
                )
                from PIL import Image
            except ModuleNotFoundError as missing:
                raise RuntimeError(
                    f"Required package missing: {missing.name}.\n\n"
                    "Re-install the application or contact support."
                ) from None

            from app.utils.ai_runtime import ai_cache_dir
            cache = ai_cache_dir()
            if cache is None:
                raise RuntimeError(
                    "Data directory is not configured.\n"
                    "Open Settings and set a data directory first."
                )

            model_dir = cache / QWEN_ONNX_DIR
            if not (model_dir / "config.json").exists():
                raise RuntimeError(
                    "Qwen2-VL ONNX model not found.\n\n"
                    f"Expected: {model_dir}\n\n"
                    "Run the downloader once:\n"
                    "  python tools/create_ai_model.py --output <data_dir>/ai_cache"
                )

            onnx_dir = model_dir / "onnx"

            # Pick best available execution provider
            available = ort.get_available_providers()
            if "CUDAExecutionProvider" in available:
                provider = "CUDAExecutionProvider"
                device_label = "GPU (CUDA)"
            elif "DmlExecutionProvider" in available:
                provider = "DmlExecutionProvider"
                device_label = "GPU (DirectML)"
            else:
                provider = "CPUExecutionProvider"
                device_label = "CPU"

            is_cpu = (provider == "CPUExecutionProvider")
            variant = _detect_variant(onnx_dir, is_cpu)
            if variant is None:
                raise RuntimeError(
                    "No compatible ONNX variant found.\n\n"
                    "GPU variants (fp16/q4f16) require CUDA or DirectML.\n"
                    "CPU-compatible variants (q4, int8) are also available:\n"
                    "  python tools/create_ai_model.py --output <data_dir>/ai_cache --variant q4"
                )

            if (
                _cached_sessions is None
                or _cached_provider != provider
                or _cached_variant != variant
            ):
                self.status.emit(f"Loading model on {device_label}…", "#f59e0b")

                so = ort.SessionOptions()
                so.log_severity_level = 3
                so.enable_mem_reuse = False   # required: KV cache grows between steps

                providers_list = [provider, "CPUExecutionProvider"]
                _cached_sessions = {
                    "vis": ort.InferenceSession(
                        str(onnx_dir / f"vision_encoder_{variant}.onnx"),
                        so, providers=providers_list,
                    ),
                    "emb": ort.InferenceSession(
                        str(onnx_dir / f"embed_tokens_{variant}.onnx"),
                        so, providers=providers_list,
                    ),
                    "dec": ort.InferenceSession(
                        str(onnx_dir / f"decoder_model_merged_{variant}.onnx"),
                        so, providers=providers_list,
                    ),
                }

                _cached_proc = AutoProcessor.from_pretrained(str(model_dir))

                # Use the slow image processor (pure numpy, no torch)
                _cached_img_proc = Qwen2VLImageProcessor(
                    min_pixels=3136,
                    max_pixels=12845056,
                    patch_size=14,
                    temporal_patch_size=2,
                    merge_size=2,
                )
                _cached_provider = provider
                _cached_variant  = variant

            sessions = _cached_sessions
            proc     = _cached_proc
            img_proc = _cached_img_proc
            dec_out_names = [o.name for o in sessions["dec"].get_outputs()]

            # Load and pre-resize images
            pil_images: list[Image.Image] = []
            for b in self._images:
                img = Image.open(io.BytesIO(b)).convert("RGB")
                img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
                pil_images.append(img)

            def _infer(prompt_text: str, max_new_tokens: int = 150) -> str:
                if pil_images:
                    pixel_values, grid_thw = _preprocess_images_numpy(img_proc, pil_images)
                else:
                    # Text-only: dummy image keeps processor template consistent
                    dummy = Image.new("RGB", (56, 56), (128, 128, 128))
                    pixel_values, grid_thw = _preprocess_images_numpy(img_proc, [dummy])

                imgs_for_template = pil_images if pil_images else [dummy]  # type: ignore[possibly-undefined]
                messages = [{"role": "user", "content":
                    [{"type": "image", "image": img} for img in imgs_for_template]
                    + [{"type": "text", "text": prompt_text}]}]
                text = proc.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                # Expand <|image_pad|> × n_tokens per image
                merge_len = img_proc.merge_size ** 2
                for thw in grid_thw:
                    n = int(thw.prod()) // merge_len
                    text = text.replace("<|image_pad|>", "<|image_pad|>" * n, 1)

                input_ids = np.array([proc.tokenizer.encode(text)], dtype=np.int64)

                # Vision encoder — process one image at a time.
                # The ONNX model's internal Split node is fixed for batch=1,
                # so batching multiple images causes a shape error.
                all_feats = []
                patch_offset = 0
                for thw in grid_thw:
                    n = int(thw[0]) * int(thw[1]) * int(thw[2])
                    feat = sessions["vis"].run(
                        ["image_features"],
                        {
                            "pixel_values": pixel_values[patch_offset : patch_offset + n],
                            "grid_thw": thw[np.newaxis],
                        },
                    )[0]
                    all_feats.append(feat)
                    patch_offset += n
                img_feats = np.concatenate(all_feats, axis=0) if all_feats else np.empty((0, 1536), dtype=np.float32)

                # Embed all tokens
                inputs_embeds = sessions["emb"].run(
                    ["inputs_embeds"], {"input_ids": input_ids}
                )[0]

                # Splice vision features into image token positions
                img_mask = input_ids[0] == _IMAGE_TOKEN_ID
                inputs_embeds[0, img_mask] = img_feats.astype(inputs_embeds.dtype)

                # Compute MROPE position IDs
                position_ids  = _compute_mrope_position_ids(input_ids, grid_thw)
                attention_mask = np.ones((1, input_ids.shape[1]), dtype=np.int64)

                # Empty initial KV cache
                empty_kv = np.zeros((1, _NUM_KV_HEADS, 0, _HEAD_DIM), dtype=np.float32)
                kvs = {
                    f"past_key_values.{l}.{k}": empty_kv
                    for l in range(_NUM_LAYERS) for k in ("key", "value")
                }

                # Prefill
                dec_out = sessions["dec"].run(
                    None,
                    {"inputs_embeds": inputs_embeds,
                     "attention_mask": attention_mask,
                     "position_ids": position_ids,
                     **kvs},
                )
                logits_idx = dec_out_names.index("logits")
                next_token = int(np.argmax(dec_out[logits_idx][0, -1]))
                next_pos   = int(position_ids.max()) + 1

                # Copy present tensors (onnxruntime may alias them internally)
                present = {
                    dec_out_names[i]: np.array(dec_out[i])
                    for i, n in enumerate(dec_out_names)
                    if n.startswith("present.")
                }

                eos = proc.tokenizer.eos_token_id
                generated = [next_token]
                prefill_len = input_ids.shape[1]

                # Autoregressive decode loop
                for step in range(max_new_tokens - 1):
                    if next_token == eos:
                        break
                    ne = sessions["emb"].run(
                        ["inputs_embeds"],
                        {"input_ids": np.array([[next_token]], dtype=np.int64)},
                    )[0]
                    past_len = prefill_len + step
                    am  = np.ones((1, past_len + 1), dtype=np.int64)
                    pis = np.full((3, 1, 1), next_pos + step, dtype=np.int64)
                    kv  = {
                        f"past_key_values.{l}.{k}": present[f"present.{l}.{k}"]
                        for l in range(_NUM_LAYERS) for k in ("key", "value")
                    }
                    do = sessions["dec"].run(
                        None,
                        {"inputs_embeds": ne, "attention_mask": am,
                         "position_ids": pis, **kv},
                    )
                    next_token = int(np.argmax(do[logits_idx][0, -1]))
                    generated.append(next_token)
                    present = {
                        dec_out_names[i]: np.array(do[i])
                        for i, n in enumerate(dec_out_names)
                        if n.startswith("present.")
                    }

                return proc.tokenizer.decode(generated, skip_special_tokens=True).strip()

            # Tags pass
            self.status.emit("Generating tags…", "#f59e0b")
            raw_tags = _infer(
                _build_tags_prompt(self._stems, self._archive_path), max_new_tokens=120
            )
            seen: set[str] = set()
            tags = [
                t
                for raw in raw_tags.split(",")
                if (t := raw.strip()) and t.lower() not in seen
                and not seen.add(t.lower())  # type: ignore[func-returns-value]
            ]
            self.tags_ready.emit(tags)

            # Metadata pass
            self.status.emit("Extracting metadata…", "#f59e0b")
            raw_meta = _infer(_build_meta_prompt(self._archive_path), max_new_tokens=200)
            meta: dict[str, str] = {}
            for line in raw_meta.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip().upper()
                    val = val.strip()
                    if val and val.lower() not in ("n/a", "none", "unknown", ""):
                        meta[key] = val
            self.meta_ready.emit(meta)

            self.status.emit("Done", "#22c55e")

        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class AITaggerDialog(QDialog):
    """Runs Qwen2-VL on the model's LMDB thumbnails and lets the user review
    AI-suggested tags and metadata before writing them to the database.

    Accepts a list of model IDs; when more than one is given the dialog
    navigates through them sequentially.  "Save" (single) / "Save & Next"
    (multi, not last) / "Save & Close" (multi, last) writes the current
    model's data.  "Skip" advances without saving.  "Cancel" closes the
    dialog immediately without saving anything.
    """

    def __init__(self, model_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self._model_ids      = list(model_ids)
        self._current_index  = 0
        self._thread: Optional[_InferenceThread] = None
        self._current_tags: list[str] = []
        self._any_saved      = False   # whether at least one model was saved

        # Per-model runtime data (populated by _load_model)
        self._model_id    = 0
        self._archive_path = ""
        self._image_data: list[tuple[bytes, str]] = []

        self.setMinimumWidth(540)
        self.resize(660, 640)
        self._build_ui()
        self._load_model(0)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Progress label (hidden for single-model use)
        self._progress_lbl = QLabel()
        self._progress_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._progress_lbl.setStyleSheet(
            "font-size: 12px; color: #9ca3af; padding: 2px 0;"
        )
        self._progress_lbl.setVisible(len(self._model_ids) > 1)
        root.addWidget(self._progress_lbl)

        # Status row
        status_row = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(18)
        self._status_dot.setStyleSheet("color: #6b7280;")
        self._status_lbl = QLabel("Click 'Generate' to run AI analysis")
        self._status_lbl.setStyleSheet("color: #6b7280;")
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_lbl, stretch=1)

        self._gen_btn = QPushButton("Generate AI Suggestions")
        self._gen_btn.setDefault(False)
        self._gen_btn.setAutoDefault(False)
        self._gen_btn.clicked.connect(self._start_inference)
        status_row.addWidget(self._gen_btn)
        root.addLayout(status_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        # Thumbnail strip
        THUMB_H = 90
        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setFixedHeight(THUMB_H + 4)
        self._thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_inner = QWidget()
        self._thumb_layout = QHBoxLayout(self._thumb_inner)
        self._thumb_layout.setContentsMargins(4, 2, 4, 2)
        self._thumb_layout.setSpacing(4)
        self._thumb_layout.addStretch()
        self._thumb_scroll.setWidget(self._thumb_inner)
        self._thumb_scroll.setVisible(False)
        root.addWidget(self._thumb_scroll)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(10)
        cl.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        # Metadata fields
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setSpacing(6)

        self._title_edit    = self._make_field(form, "Title:")
        self._creator_edit  = self._make_field(form, "Creator:")
        self._sculptor_edit = self._make_field(form, "Sculptor:")
        self._category_edit = self._make_field(form, "Category:")
        self._scale_edit    = self._make_field(form, "Scale:")
        self._source_edit   = self._make_field(
            form, "Source:", placeholder="patreon / kickstarter / myminifactory"
        )
        self._year_edit     = self._make_field(form, "Year:", placeholder="e.g. 2023")
        self._desc_edit     = self._make_textedit(form, "Description:")

        cl.addLayout(form)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        cl.addWidget(sep2)

        # Tags
        tags_hdr = QHBoxLayout()
        tags_lbl = QLabel("Suggested Tags:")
        tags_lbl.setStyleSheet("font-weight: bold;")
        tags_hdr.addWidget(tags_lbl)
        tags_hdr.addStretch()
        clr_btn = QPushButton("Clear all")
        clr_btn.setFixedHeight(22)
        clr_btn.setStyleSheet("font-size: 11px;")
        clr_btn.clicked.connect(self._clear_all_tags)
        tags_hdr.addWidget(clr_btn)
        cl.addLayout(tags_hdr)

        self._pills_widget = QWidget()
        self._pills_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        self._pills_layout = _FlowLayout(self._pills_widget)
        cl.addWidget(self._pills_widget)

        self._tags_placeholder = QLabel("No tags yet — click 'Generate' above")
        self._tags_placeholder.setStyleSheet("color: #9ca3af; font-style: italic;")
        cl.addWidget(self._tags_placeholder)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Add tag:"))
        self._add_tag_edit = QLineEdit()
        self._add_tag_edit.setPlaceholderText("type a tag and press Enter…")
        self._add_tag_edit.returnPressed.connect(self._add_custom_tag)
        add_row.addWidget(self._add_tag_edit, stretch=1)
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(30)
        add_btn.clicked.connect(self._add_custom_tag)
        add_row.addWidget(add_btn)
        cl.addLayout(add_row)
        cl.addStretch()

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep3)

        # Button row
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._cancel_btn)

        btn_row.addStretch()

        # "Skip" only shown in multi-model mode and not on last model
        self._skip_btn = QPushButton("Skip →")
        self._skip_btn.setToolTip("Move to the next model without saving")
        self._skip_btn.clicked.connect(self._skip)
        self._skip_btn.setVisible(False)
        btn_row.addWidget(self._skip_btn)

        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_and_advance)
        btn_row.addWidget(self._save_btn)

        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Per-model loading
    # ------------------------------------------------------------------

    def _load_model(self, index: int) -> None:
        """Stop any running inference, clear the form, and populate it with
        the model at *index* in self._model_ids."""
        self._stop_thread()

        # Clear fields
        for edit in (
            self._title_edit, self._creator_edit, self._sculptor_edit,
            self._category_edit, self._scale_edit, self._source_edit,
            self._year_edit,
        ):
            edit.clear()
        self._desc_edit.clear()
        self._current_tags.clear()
        self._rebuild_pills()
        self._set_status("Click 'Generate' to run AI analysis", "#6b7280")
        self._gen_btn.setEnabled(True)

        # Load model data from DB
        model_id = self._model_ids[index]
        session = get_session()
        try:
            m = session.get(STLModel, model_id)
            self._model_id    = model_id
            self._archive_path = m.archive_path or ""
            display_title     = m.title or Path(self._archive_path).stem
            self._image_data  = []
            for img in m.images:
                b = get_thumbnail_bytes(img.id)
                if b:
                    stem = Path(img.filename).stem if img.filename else f"img{img.id}"
                    self._image_data.append((b, stem))
        finally:
            session.close()

        # Rebuild thumbnail strip
        self._rebuild_thumbs()

        # Update title / progress
        n = len(self._model_ids)
        if n > 1:
            self.setWindowTitle(
                f"AI Tagging ({index + 1}/{n}) — {display_title}"
            )
            self._progress_lbl.setText(
                f"Model {index + 1} of {n}:  {display_title}"
            )
        else:
            self.setWindowTitle(f"AI Tagging — {display_title}")

        # Update navigation buttons
        is_last = (index == n - 1)
        multi   = n > 1
        self._skip_btn.setVisible(multi and not is_last)
        if multi:
            self._save_btn.setText("Save && Close" if is_last else "Save && Next →")
        else:
            self._save_btn.setText("Save")

        if not self._image_data:
            self._set_status(
                "No thumbnails in LMDB — will analyse path/filename only", "#6b7280"
            )

        # Auto-start inference in multi-model mode
        if multi:
            self._start_inference()

    # ------------------------------------------------------------------
    def _rebuild_thumbs(self) -> None:
        THUMB_H = 86
        # Clear existing thumbnails (leave the trailing stretch)
        while self._thumb_layout.count() > 1:
            item = self._thumb_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        for img_bytes, stem in self._image_data:
            px = QPixmap()
            px.loadFromData(img_bytes)
            if px.isNull():
                continue
            px = px.scaledToHeight(THUMB_H, Qt.TransformationMode.SmoothTransformation)
            lbl = QLabel()
            lbl.setPixmap(px)
            lbl.setFixedSize(px.width(), THUMB_H)
            lbl.setToolTip(stem)
            lbl.setStyleSheet("border: 1px solid #374151; border-radius: 3px;")
            self._thumb_layout.insertWidget(self._thumb_layout.count() - 1, lbl)

        self._thumb_scroll.setVisible(bool(self._image_data))

    # ------------------------------------------------------------------
    def _make_field(self, form: QFormLayout, label: str, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setClearButtonEnabled(True)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit)
        x = QPushButton("×")
        x.setFixedSize(24, 24)
        x.setToolTip("Clear")
        x.setStyleSheet(
            "QPushButton{border:none;color:#9ca3af;font-size:16px;padding:0}"
            "QPushButton:hover{color:#ef4444}"
        )
        x.clicked.connect(edit.clear)
        row.addWidget(x)
        w = QWidget()
        w.setLayout(row)
        form.addRow(label, w)
        return edit

    def _make_textedit(self, form: QFormLayout, label: str) -> QTextEdit:
        edit = QTextEdit()
        edit.setMaximumHeight(70)
        edit.setPlaceholderText("brief description…")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit)
        x = QPushButton("×")
        x.setFixedSize(24, 24)
        x.setToolTip("Clear")
        x.setStyleSheet(
            "QPushButton{border:none;color:#9ca3af;font-size:16px;padding:0}"
            "QPushButton:hover{color:#ef4444}"
        )
        x.clicked.connect(edit.clear)
        row.addWidget(x)
        w = QWidget()
        w.setLayout(row)
        form.addRow(label, w)
        return edit

    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str) -> None:
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color: {color};")
        self._status_dot.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------
    def _start_inference(self) -> None:
        if self._thread and self._thread.isRunning():
            return

        from app.utils.ai_runtime import is_ai_ready
        if not is_ai_ready():
            from app.ui.ai_setup_dialog import AISetupDialog
            dlg = AISetupDialog(parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if not is_ai_ready():
                self._set_status("Setup incomplete — packages not found.", "#ef4444")
                return

        self._gen_btn.setEnabled(False)
        self._set_status("Starting…", "#f59e0b")

        self._thread = _InferenceThread(
            [b for b, _ in self._image_data],
            [s for _, s in self._image_data],
            self._archive_path,
        )
        self._thread.status.connect(self._set_status)
        self._thread.tags_ready.connect(self._on_tags_ready)
        self._thread.meta_ready.connect(self._on_meta_ready)
        self._thread.error.connect(self._on_error)
        self._thread.finished.connect(lambda: self._gen_btn.setEnabled(True))
        self._thread.start()

    def _stop_thread(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None

    def _on_tags_ready(self, tags: list[str]) -> None:
        for tag in tags:
            self._add_tag(tag)

    def _on_meta_ready(self, meta: dict[str, str]) -> None:
        def _fill(edit: QLineEdit, key: str) -> None:
            if key in meta and not edit.text().strip():
                edit.setText(meta[key])

        _fill(self._title_edit,    "TITLE")
        _fill(self._creator_edit,  "CREATOR")
        _fill(self._sculptor_edit, "SCULPTOR")
        _fill(self._category_edit, "CATEGORY")
        _fill(self._scale_edit,    "SCALE")

        if "SOURCE" in meta and not self._source_edit.text().strip():
            src = meta["SOURCE"].strip().lower()
            if src in _VALID_SOURCES:
                self._source_edit.setText(src)

        if "YEAR" in meta and not self._year_edit.text().strip():
            year = meta["YEAR"].strip()[:4]
            if year.isdigit():
                self._year_edit.setText(year)

        if "DESCRIPTION" in meta and not self._desc_edit.toPlainText().strip():
            self._desc_edit.setPlainText(meta["DESCRIPTION"])

    def _on_error(self, msg: str) -> None:
        self._set_status(f"Error: {msg}", "#ef4444")
        self._gen_btn.setEnabled(True)

    # ------------------------------------------------------------------
    def _add_tag(self, tag: str) -> None:
        tag = tag.strip().lower()
        if not tag or tag in self._current_tags:
            return
        self._current_tags.append(tag)
        self._rebuild_pills()

    def _remove_tag(self, tag: str) -> None:
        if tag in self._current_tags:
            self._current_tags.remove(tag)
        self._rebuild_pills()

    def _clear_all_tags(self) -> None:
        self._current_tags.clear()
        self._rebuild_pills()

    def _add_custom_tag(self) -> None:
        tag = self._add_tag_edit.text().strip()
        if tag:
            self._add_tag(tag)
            self._add_tag_edit.clear()

    def _rebuild_pills(self) -> None:
        while self._pills_layout.count():
            item = self._pills_layout.takeAt(0)
            if item and item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        for tag in self._current_tags:
            btn = QPushButton(f" {tag}  ×")
            btn.setStyleSheet(
                "QPushButton{"
                "border:1px solid #6b7280;border-radius:10px;"
                "padding:2px 8px;font-size:11px;background:transparent}"
                "QPushButton:hover{"
                "background:#fee2e2;border-color:#ef4444;color:#b91c1c}"
            )
            btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _=False, t=tag: self._remove_tag(t))
            self._pills_layout.addWidget(btn)

        self._tags_placeholder.setVisible(not self._current_tags)
        self._pills_layout.invalidate()
        self._pills_widget.updateGeometry()
        self._pills_widget.adjustSize()

    # ------------------------------------------------------------------
    def _commit_current(self) -> None:
        """Write the current form values to the database."""
        title         = self._title_edit.text().strip()
        creator       = self._creator_edit.text().strip()
        sculptor_name = self._sculptor_edit.text().strip()
        category_name = self._category_edit.text().strip()
        scale_name    = self._scale_edit.text().strip()
        source        = self._source_edit.text().strip().lower()
        year_str      = self._year_edit.text().strip()
        description   = self._desc_edit.toPlainText().strip()

        year: Optional[int] = None
        if year_str.isdigit():
            y = int(year_str)
            if 1900 <= y <= 2100:
                year = y
        if source not in _VALID_SOURCES:
            source = ""

        with session_scope() as session:
            model: Optional[STLModel] = session.get(STLModel, self._model_id)
            if model is None:
                return

            if title:
                model.title = title
            if creator:
                model.creator = creator
            if year is not None:
                model.release_year = year
            if description:
                model.description = description
            if source:
                model.source = source

            if sculptor_name:
                sc = session.query(Sculptor).filter_by(name=sculptor_name).first()
                if sc is None:
                    sc = Sculptor(name=sculptor_name)
                    session.add(sc)
                    session.flush()
                model.sculptor = sc

            if category_name:
                cat = session.query(Category).filter_by(name=category_name).first()
                if cat is None:
                    cat = Category(name=category_name)
                    session.add(cat)
                    session.flush()
                model.category = cat

            if scale_name:
                scl = session.query(Scale).filter_by(name=scale_name).first()
                if scl is None:
                    scl = Scale(name=scale_name)
                    session.add(scl)
                    session.flush()
                model.scale = scl

            existing_lower = {t.name.lower() for t in model.tags}
            for tag_name in self._current_tags:
                if tag_name not in existing_lower:
                    tag_obj = session.query(Tag).filter_by(name=tag_name).first()
                    if tag_obj is None:
                        tag_obj = Tag(name=tag_name)
                        session.add(tag_obj)
                        session.flush()
                    model.tags.append(tag_obj)

        self._any_saved = True

    def _save_and_advance(self) -> None:
        """Save the current model then move to the next, or close if finished."""
        self._commit_current()
        next_index = self._current_index + 1
        if next_index < len(self._model_ids):
            self._current_index = next_index
            self._load_model(next_index)
        else:
            self.accept()

    def _skip(self) -> None:
        """Advance to the next model without saving the current one."""
        next_index = self._current_index + 1
        if next_index < len(self._model_ids):
            self._current_index = next_index
            self._load_model(next_index)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        global _cached_sessions, _cached_proc, _cached_img_proc
        global _cached_provider, _cached_variant
        self._stop_thread()
        _cached_sessions = None
        _cached_proc     = None
        _cached_img_proc = None
        _cached_provider = None
        _cached_variant  = None
        super().closeEvent(event)
