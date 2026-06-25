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
    QSizePolicy, QFrame, QLayout, QTextEdit, QMessageBox,
)

from app.database.db import session_scope, get_session
from app.database.models import STLModel, Tag, Category, Sculptor, Scale
from app.utils.thumbnailer import get_thumbnail_bytes


QWEN_MODEL = "Qwen/Qwen2-VL-2B-Instruct"
MAX_VISUAL_TOKENS = 600
PATCH = 28

_VALID_SOURCES = {"patreon", "kickstarter", "myminifactory", "unknown"}

# Module-level model cache: keyed by device string.
# Keeping the model alive here prevents CUDA tensor destructors from running
# off the main thread (which causes glibc double-free / heap corruption).
_cached_model = None
_cached_proc  = None
_cached_device: str | None = None


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _image_size_for(n: int) -> int:
    tokens_each = MAX_VISUAL_TOKENS // max(n, 1)
    side = int((tokens_each ** 0.5) * PATCH)
    return max(224, min(512, (side // PATCH) * PATCH))


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
# Inference / commit core (shared by the interactive dialog and batch mode)
# ---------------------------------------------------------------------------

def _generate_suggestions(
    image_bytes_list: list[bytes],
    stems: list[str],
    archive_path: str,
    status_cb=None,
    on_tags=None,
    on_meta=None,
) -> "tuple[list[str], dict[str, str]]":
    """Run Qwen2-VL and return ``(tags, metadata)`` for one model.

    *status_cb(text, css_color)* is called with progress updates when provided.
    *on_tags(list)* / *on_meta(dict)* are called as soon as each result is ready
    so callers can stream them to the UI. Raises ``RuntimeError`` with a
    user-facing message if the AI packages are not installed.
    """
    global _cached_model, _cached_proc, _cached_device

    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)

    from app.utils.ai_runtime import activate_ai_packages
    activate_ai_packages()

    try:
        import torch
        from PIL import Image
        from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
        from qwen_vl_utils import process_vision_info
    except ModuleNotFoundError as missing:
        raise RuntimeError(
            f"AI tagging requires additional packages ({missing.name} is not installed).\n\n"
            "Click 'Generate' again to open the install dialog, or run\n"
            "  pip install torch torchvision transformers accelerate qwen-vl-utils\n"
            "and restart the application."
        ) from None

    device = "cuda" if torch.cuda.is_available() else "cpu"

    if _cached_model is None or _cached_device != device:
        if status_cb:
            status_cb("Loading Qwen model…", "#f59e0b")
        _cached_proc = AutoProcessor.from_pretrained(QWEN_MODEL)
        _cached_model = Qwen2VLForConditionalGeneration.from_pretrained(
            QWEN_MODEL,
            torch_dtype=torch.float16 if device == "cuda" else torch.float32,
            device_map=device,
        )
        _cached_model.eval()
        _cached_device = device

    proc, model = _cached_proc, _cached_model

    pil_images: list = []
    if image_bytes_list:
        size = _image_size_for(len(image_bytes_list))
        for b in image_bytes_list:
            img = Image.open(io.BytesIO(b)).convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            pil_images.append(img)

    def _infer(prompt_text: str, max_tokens: int = 150) -> str:
        content = [{"type": "image", "image": img} for img in pil_images]
        content.append({"type": "text", "text": prompt_text})
        messages = [{"role": "user", "content": content}]
        text = proc.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        img_in, vid_in = process_vision_info(messages)
        inputs = proc(
            text=[text],
            images=img_in if img_in else None,
            videos=vid_in if vid_in else None,
            padding=True,
            return_tensors="pt",
        ).to(device)
        with torch.no_grad():
            ids = model.generate(**inputs, max_new_tokens=max_tokens)
        trimmed = ids[0][inputs.input_ids.shape[1]:]
        result = proc.decode(trimmed, skip_special_tokens=True).strip()
        del inputs, ids, trimmed
        if device == "cuda":
            torch.cuda.empty_cache()
        return result

    if status_cb:
        status_cb("Generating tags…", "#f59e0b")
    raw_tags = _infer(_build_tags_prompt(stems, archive_path), max_tokens=120)
    seen: set[str] = set()
    tags = [
        t
        for raw in raw_tags.split(",")
        if (t := raw.strip()) and t.lower() not in seen and not seen.add(t.lower())
    ]
    if on_tags:
        on_tags(tags)

    if status_cb:
        status_cb("Extracting metadata…", "#f59e0b")
    raw_meta = _infer(_build_meta_prompt(archive_path), max_tokens=200)
    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().upper()
            val = val.strip()
            if val and val.lower() not in ("n/a", "none", "unknown", ""):
                meta[key] = val
    if on_meta:
        on_meta(meta)

    return tags, meta


def _load_model_inputs(model_id: int) -> "tuple[list[bytes], list[str], str, str]":
    """Return ``(image_bytes, stems, archive_path, display_title)`` for *model_id*."""
    session = get_session()
    try:
        m = session.get(STLModel, model_id)
        if m is None:
            return [], [], "", f"#{model_id}"
        archive_path = m.archive_path or ""
        title = m.title or Path(archive_path).stem
        images: list[bytes] = []
        stems: list[str] = []
        for img in m.images:
            b = get_thumbnail_bytes(img.id)
            if b:
                images.append(b)
                stems.append(Path(img.filename).stem if img.filename else f"img{img.id}")
        return images, stems, archive_path, title
    finally:
        session.close()


def _normalize_meta(meta: dict[str, str]) -> dict:
    """Convert a raw Qwen metadata dict into validated, normalized field values."""
    fields = {
        "title":       meta.get("TITLE", "").strip(),
        "creator":     meta.get("CREATOR", "").strip(),
        "sculptor":    meta.get("SCULPTOR", "").strip(),
        "category":    meta.get("CATEGORY", "").strip(),
        "scale":       meta.get("SCALE", "").strip(),
        "description": meta.get("DESCRIPTION", "").strip(),
        "source":      "",
        "year":        None,
    }
    src = meta.get("SOURCE", "").strip().lower()
    if src in _VALID_SOURCES:
        fields["source"] = src
    year = meta.get("YEAR", "").strip()[:4]
    if year.isdigit() and 1900 <= int(year) <= 2100:
        fields["year"] = int(year)
    return fields


def _commit_model_data(model_id: int, fields: dict, tags: list[str]) -> bool:
    """Write normalized *fields* and *tags* to *model_id*.

    Existing values are preserved: scalar fields are only set when non-empty and
    tags are merged (never removed). Returns True if the model existed.
    """
    with session_scope() as session:
        model: Optional[STLModel] = session.get(STLModel, model_id)
        if model is None:
            return False

        if fields["title"]:
            model.title = fields["title"]
        if fields["creator"]:
            model.creator = fields["creator"]
        if fields["year"] is not None:
            model.release_year = fields["year"]
        if fields["description"]:
            model.description = fields["description"]
        if fields["source"]:
            model.source = fields["source"]

        if fields["sculptor"]:
            sc = session.query(Sculptor).filter_by(name=fields["sculptor"]).first()
            if sc is None:
                sc = Sculptor(name=fields["sculptor"])
                session.add(sc)
                session.flush()
            model.sculptor = sc

        if fields["category"]:
            cat = session.query(Category).filter_by(name=fields["category"]).first()
            if cat is None:
                cat = Category(name=fields["category"])
                session.add(cat)
                session.flush()
            model.category = cat

        if fields["scale"]:
            scl = session.query(Scale).filter_by(name=fields["scale"]).first()
            if scl is None:
                scl = Scale(name=fields["scale"])
                session.add(scl)
                session.flush()
            model.scale = scl

        existing_lower = {t.name.lower() for t in model.tags}
        for tag_name in tags:
            tn = tag_name.strip().lower()
            if tn and tn not in existing_lower:
                tag_obj = session.query(Tag).filter_by(name=tn).first()
                if tag_obj is None:
                    tag_obj = Tag(name=tn)
                    session.add(tag_obj)
                    session.flush()
                model.tags.append(tag_obj)
                existing_lower.add(tn)
    return True


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
        try:
            _generate_suggestions(
                self._images, self._stems, self._archive_path,
                self.status.emit, self.tags_ready.emit, self.meta_ready.emit,
            )
            self.status.emit("Done", "#22c55e")
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Batch worker — process every model and commit automatically
# ---------------------------------------------------------------------------

class _BatchThread(QThread):
    # model_started carries an opaque payload (dict) so the dialog can display
    # the current model's thumbnails without re-querying the database.
    model_started = pyqtSignal(int, int, object)   # (current, total, payload)
    status        = pyqtSignal(str, str)           # (text, css_color)
    tags_ready    = pyqtSignal(list)
    meta_ready    = pyqtSignal(dict)
    error         = pyqtSignal(str)
    completed     = pyqtSignal(int, int)           # (saved, total)

    def __init__(self, model_ids: list[int]) -> None:
        super().__init__()
        self._model_ids = list(model_ids)
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        saved = 0
        total = len(self._model_ids)
        for i, model_id in enumerate(self._model_ids, 1):
            if self._stop_requested:
                break
            images, stems, archive_path, title = _load_model_inputs(model_id)
            self.model_started.emit(i, total, {
                "index":        i - 1,
                "model_id":     model_id,
                "images":       images,
                "stems":        stems,
                "archive_path": archive_path,
                "title":        title,
            })
            try:
                tags, meta = _generate_suggestions(
                    images, stems, archive_path,
                    self.status.emit, self.tags_ready.emit, self.meta_ready.emit,
                )
            except Exception as exc:
                self.error.emit(f"{title}: {exc}")
                if i == 1:
                    # First model failed — almost certainly a setup/package
                    # problem that would repeat for every model, so abort.
                    break
                continue
            if _commit_model_data(model_id, _normalize_meta(meta), tags):
                saved += 1
        self.completed.emit(saved, total)


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
        self._batch_thread: Optional[_BatchThread] = None
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

        self._process_all_btn = QPushButton(
            f"Process All ({len(self._model_ids)})"
        )
        self._process_all_btn.setToolTip(
            "Run AI on every model and save the results automatically, without review"
        )
        self._process_all_btn.clicked.connect(self._on_process_all_clicked)
        btn_row.addWidget(self._process_all_btn)

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

    def _clear_form(self) -> None:
        """Empty every metadata field and clear the tag pills."""
        for edit in (
            self._title_edit, self._creator_edit, self._sculptor_edit,
            self._category_edit, self._scale_edit, self._source_edit,
            self._year_edit,
        ):
            edit.clear()
        self._desc_edit.clear()
        self._current_tags.clear()
        self._rebuild_pills()

    def _load_model(self, index: int) -> None:
        """Stop any running inference, clear the form, and populate it with
        the model at *index* in self._model_ids."""
        self._stop_thread()

        self._clear_form()
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
        if self._thread is not None:
            # Drop the slot connections first so a result arriving from the
            # outgoing run does not land in the now-current model's fields.
            try:
                self._thread.tags_ready.disconnect()
                self._thread.meta_ready.disconnect()
                self._thread.status.disconnect()
                self._thread.error.disconnect()
            except (TypeError, RuntimeError):
                pass
            # generate() cannot be interrupted — wait for it to finish rather
            # than destroying a still-running QThread (which crashes the app).
            if self._thread.isRunning():
                self._thread.wait()
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
        source = self._source_edit.text().strip().lower()
        year_str = self._year_edit.text().strip()
        year: Optional[int] = None
        if year_str.isdigit() and 1900 <= int(year_str) <= 2100:
            year = int(year_str)

        fields = {
            "title":       self._title_edit.text().strip(),
            "creator":     self._creator_edit.text().strip(),
            "sculptor":    self._sculptor_edit.text().strip(),
            "category":    self._category_edit.text().strip(),
            "scale":       self._scale_edit.text().strip(),
            "description": self._desc_edit.toPlainText().strip(),
            "source":      source if source in _VALID_SOURCES else "",
            "year":        year,
        }
        if _commit_model_data(self._model_id, fields, self._current_tags):
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
    # Batch "Process All"
    # ------------------------------------------------------------------
    def _on_process_all_clicked(self) -> None:
        # While a batch is running this button acts as "Stop".
        if self._batch_thread and self._batch_thread.isRunning():
            self._batch_thread.stop()
            self._set_status("Stopping after current model…", "#f59e0b")
            self._process_all_btn.setEnabled(False)
            return

        from app.utils.ai_runtime import is_ai_ready
        if not is_ai_ready():
            from app.ui.ai_setup_dialog import AISetupDialog
            dlg = AISetupDialog(parent=self)
            if dlg.exec() != QDialog.DialogCode.Accepted or not is_ai_ready():
                self._set_status("Setup incomplete — packages not found.", "#ef4444")
                return

        n = len(self._model_ids)
        reply = QMessageBox.question(
            self, "Process All",
            f"Run AI tagging on all {n} model(s) and save the results automatically, "
            "without reviewing each one?\n\n"
            "Existing values are kept — only empty fields are filled and new tags added.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Wait for any in-flight single-model inference to fully finish before
        # starting the batch — two concurrent generate() calls on the shared
        # cached model would corrupt its state. This briefly blocks the UI.
        if self._thread and self._thread.isRunning():
            self._set_status("Waiting for current analysis to finish…", "#f59e0b")
        self._stop_thread()

        self._set_batch_busy(True)
        self._progress_lbl.setVisible(True)

        self._batch_thread = _BatchThread(self._model_ids)
        self._batch_thread.model_started.connect(self._on_batch_model_started)
        self._batch_thread.status.connect(self._set_status)
        self._batch_thread.tags_ready.connect(self._on_tags_ready)
        self._batch_thread.meta_ready.connect(self._on_meta_ready)
        self._batch_thread.error.connect(self._on_batch_error)
        self._batch_thread.completed.connect(self._on_batch_finished)
        self._batch_thread.start()

    def _set_batch_busy(self, busy: bool) -> None:
        """Enable/disable controls while a batch run is in progress."""
        self._gen_btn.setEnabled(not busy)
        self._save_btn.setEnabled(not busy)
        self._skip_btn.setEnabled(not busy and self._skip_btn.isVisible())
        self._cancel_btn.setEnabled(not busy)
        self._process_all_btn.setText("Stop" if busy else f"Process All ({len(self._model_ids)})")
        self._process_all_btn.setEnabled(True)

    def _on_batch_model_started(self, current: int, total: int, payload: dict) -> None:
        """Display the model the batch is about to analyse (thumbnails + clear form)."""
        self._current_index = payload["index"]
        self._model_id      = payload["model_id"]
        self._archive_path  = payload["archive_path"]
        self._image_data    = list(zip(payload["images"], payload["stems"]))

        self._clear_form()
        self._rebuild_thumbs()

        title = payload["title"]
        self.setWindowTitle(f"AI Tagging ({current}/{total}) — {title}")
        self._progress_lbl.setVisible(True)
        self._progress_lbl.setText(f"Processing {current} of {total}:  {title}")

    def _on_batch_error(self, msg: str) -> None:
        self._set_status(f"Error: {msg}", "#ef4444")

    def _on_batch_finished(self, saved: int, total: int) -> None:
        # This slot runs (queued) as run() returns; wait() lets the thread fully
        # terminate before we drop the reference, avoiding a destroy-while-running.
        if self._batch_thread is not None:
            self._batch_thread.wait()
        self._batch_thread = None
        self._set_batch_busy(False)
        if saved:
            self._any_saved = True
        self._set_status(f"Processed {saved} of {total} model(s).", "#22c55e")
        QMessageBox.information(
            self, "Process All",
            f"Saved AI suggestions for {saved} of {total} model(s)."
        )

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        global _cached_model, _cached_proc, _cached_device
        self._stop_thread()
        if self._batch_thread and self._batch_thread.isRunning():
            self._batch_thread.stop()
            self._batch_thread.wait(60000)
        try:
            import gc
            import torch
            cuda = torch.cuda.is_available()
            if _cached_model is not None and cuda:
                # Move all parameters off the GPU before dropping the reference
                # so VRAM is reclaimed immediately rather than waiting for the
                # Python GC to get around to it.
                _cached_model.cpu()
                if hasattr(_cached_model, 'visual'):
                    _cached_model.visual.cpu()
            _cached_model  = None
            _cached_proc   = None
            _cached_device = None
            gc.collect()
            if cuda:
                torch.cuda.synchronize()
                torch.cuda.empty_cache()
        except Exception:
            pass
        super().closeEvent(event)
