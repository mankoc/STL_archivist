"""
AI Tagger dialog — uses Qwen2.5-VL to suggest tags and metadata for a model.

Inference runs locally via llama.cpp's ``llama-server`` (a native subprocess,
no torch / Python AI packages) — see :mod:`app.utils.ai_server`.  Images are
read from LMDB and sent as base64 to the server's OpenAI-compatible API.

Supports single or multiple models: when multiple model IDs are passed the
dialog navigates through them one by one.  Changes are written to the database
only when the user clicks "Save" / "Save & Next".
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRect, QPoint, QSize, QSettings
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QWidget,
    QSizePolicy, QFrame, QLayout, QTextEdit, QMessageBox, QCheckBox, QSpinBox,
)

_MAX_TAGS_KEY = "ai_tagger/max_tags"   # QSettings key; 0 = no limit

from app.services import model_edit
from app.utils.thumbnailer import get_thumbnail_bytes
from app.utils.ai_tagging import (
    VALID_SOURCES as _VALID_SOURCES,
    build_meta_prompt as _build_meta_prompt,
    build_tags_prompt as _build_tags_prompt,
    clean_tags as _clean_tags,
    collect_known_meta as _collect_known_meta,
    normalize_meta as _normalize_meta,
    parse_meta as _parse_meta,
)


# ---------------------------------------------------------------------------
# Inference / commit core (shared by the interactive dialog and batch mode)
# ---------------------------------------------------------------------------

def _generate_suggestions(
    image_bytes_list: list[bytes],
    stems: list[str],
    archive_path: str,
    known_meta: dict[str, str] | None = None,
    file_list: list[str] | None = None,
    max_tags: int = 0,
    status_cb=None,
    on_tags=None,
    on_meta=None,
) -> "tuple[list[str], dict[str, str]]":
    """Run Qwen2.5-VL via llama-server and return ``(tags, metadata)``.

    *status_cb(text, css_color)* is called with progress updates when provided.
    *on_tags(list)* / *on_meta(dict)* are called as soon as each result is ready
    so callers can stream them to the UI. Raises ``RuntimeError`` (with a
    user-facing message) if the AI components are not installed or the server
    cannot be started.

    If the model output is detected as corrupted (mostly question marks or garbage),
    the server is restarted and inference is retried once.
    """
    from app.utils import ai_server
    from app.ui.config_dialog import get_ai_temperature
    from app.utils.ai_tagging import is_corrupted_output

    temperature = get_ai_temperature()

    # Starts llama-server (loading the model) on first use; reused afterwards.
    ai_server.ensure_running(status_cb)

    max_retries = 1
    for attempt in range(max_retries + 1):
        if status_cb:
            status_cb("Generating tags…", "#f59e0b")
        raw_tags = ai_server.chat(
            image_bytes_list, _build_tags_prompt(stems, archive_path, known_meta),
            max_tokens=120, temperature=temperature,
        )

        # Check if tags are corrupted; if so and we have retries left, restart and retry
        if is_corrupted_output(raw_tags) and attempt < max_retries:
            if status_cb:
                status_cb("Model in bad state — reloading…", "#f59e0b")
            ai_server.stop()
            ai_server.ensure_running(status_cb)
            continue

        tags = _clean_tags(raw_tags, max_tags)
        if on_tags:
            on_tags(tags)
        break

    if status_cb:
        status_cb("Extracting metadata…", "#f59e0b")

    for attempt in range(max_retries + 1):
        raw_meta = ai_server.chat(
            image_bytes_list, _build_meta_prompt(archive_path, known_meta),
            max_tokens=200, temperature=temperature,
        )

        # Check if metadata is corrupted; if so and we have retries left, restart and retry
        if is_corrupted_output(raw_meta) and attempt < max_retries:
            if status_cb:
                status_cb("Model in bad state — reloading…", "#f59e0b")
            ai_server.stop()
            ai_server.ensure_running(status_cb)
            continue
        break

    # Scale is text-derived (parse_meta detects it from the path, image names and
    # file list); the model routinely under-reports it.
    meta = _parse_meta(raw_meta, archive_path, stems, file_list)

    if on_meta:
        on_meta(meta)

    return tags, meta


def _load_model_inputs(
    model_id: int,
) -> "tuple[list[bytes], list[str], str, str, dict[str, str], list[str]]":
    """Return ``(image_bytes, stems, archive_path, display_title, known_meta, file_list)``."""
    inputs = model_edit.ai_model_inputs(model_id)
    if inputs is None:
        return [], [], "", f"#{model_id}", {}, []
    archive_path = inputs["archive_path"]
    title = inputs["title"] or Path(archive_path).stem
    images: list[bytes] = []
    stems: list[str] = []
    for image_id, filename in inputs["images"]:
        b = get_thumbnail_bytes(image_id)
        if b:
            images.append(b)
            stems.append(Path(filename).stem if filename else f"img{image_id}")
    return images, stems, archive_path, title, _collect_known_meta(inputs), list(inputs["files"])


def _commit_model_data(model_id: int, fields: dict, tags: list[str],
                       overwrite: bool = False) -> bool:
    """Write normalized *fields* and *tags* to *model_id*.

    When *overwrite* is False (the default) existing values are preserved: a
    scalar / relationship field is only set when it is currently empty. When
    *overwrite* is True any non-empty suggested value replaces the current one.
    Tags are always merged (never removed). Returns True if the model existed.
    """
    return model_edit.ai_commit(model_id, fields, tags, overwrite)


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
        known_meta: dict[str, str] | None = None,
        file_list: list[str] | None = None,
        max_tags: int = 0,
    ) -> None:
        super().__init__()
        self._images       = image_bytes_list
        self._stems        = stems
        self._archive_path = archive_path
        self._known_meta   = known_meta or {}
        self._file_list    = file_list or []
        self._max_tags     = max_tags

    def run(self) -> None:
        try:
            _generate_suggestions(
                self._images, self._stems, self._archive_path, self._known_meta,
                self._file_list, self._max_tags,
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

    def __init__(self, model_ids: list[int], overwrite: bool = False,
                 ignore_meta: bool = False, max_tags: int = 0) -> None:
        super().__init__()
        self._model_ids = list(model_ids)
        self._overwrite = overwrite
        self._ignore_meta = ignore_meta
        self._max_tags = max_tags
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        saved = 0
        total = len(self._model_ids)
        for i, model_id in enumerate(self._model_ids, 1):
            if self._stop_requested:
                break
            images, stems, archive_path, title, known_meta, file_list = _load_model_inputs(model_id)
            if self._ignore_meta:
                known_meta = None
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
                    images, stems, archive_path, known_meta, file_list, self._max_tags,
                    self.status.emit, self.tags_ready.emit, self.meta_ready.emit,
                )
            except Exception as exc:
                self.error.emit(f"{title}: {exc}")
                if i == 1:
                    # First model failed — almost certainly a setup/package
                    # problem that would repeat for every model, so abort.
                    break
                continue
            if _commit_model_data(model_id, _normalize_meta(meta), tags,
                                  self._overwrite):
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
        self._known_meta: dict[str, str] = {}
        self._file_list: list[str] = []

        self.setMinimumWidth(540)
        self.setMinimumHeight(720)
        self.resize(660, 900)
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

        self._title_edit         = self._make_field(form, "Title:")
        self._creator_edit       = self._make_field(form, "Creator:")
        self._sculptor_edit      = self._make_field(form, "Sculptor:")
        self._collection_edit    = self._make_field(form, "Collection:")
        self._subcollection_edit = self._make_field(form, "Subcollection:")
        self._scale_edit         = self._make_field(form, "Scale:")
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

        # Overwrite option
        self._overwrite_chk = QCheckBox("Overwrite existing values")
        self._overwrite_chk.setToolTip(
            "When checked, AI suggestions replace the current field values.\n"
            "When unchecked, only empty fields are filled.\n"
            "Tags are always merged (never removed)."
        )
        root.addWidget(self._overwrite_chk)

        # Ignore existing metadata option
        self._ignore_meta_chk = QCheckBox("Ignore existing metadata (use images + path only)")
        self._ignore_meta_chk.setToolTip(
            "When checked, the model's existing catalogued metadata and file list are\n"
            "NOT sent to the AI. Only the images, archive path, and image filenames\n"
            "are used as input — useful to re-tag from scratch without prior bias."
        )
        root.addWidget(self._ignore_meta_chk)

        # Max tags limit
        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("Max tags:"))
        self._max_tags_spin = QSpinBox()
        self._max_tags_spin.setRange(0, 100)
        self._max_tags_spin.setSpecialValueText("No limit")   # shown when value == 0
        self._max_tags_spin.setValue(int(QSettings().value(_MAX_TAGS_KEY, 20)))
        self._max_tags_spin.setToolTip(
            "Keep at most this many AI-generated tags (the first N after cleanup).\n"
            "Set to 0 (No limit) to keep all. You can still add tags manually."
        )
        self._max_tags_spin.valueChanged.connect(self._on_max_tags_changed)
        max_row.addWidget(self._max_tags_spin)
        max_row.addStretch()
        root.addLayout(max_row)

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
            self._collection_edit, self._subcollection_edit, self._scale_edit,
            self._source_edit, self._year_edit,
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
        inputs = model_edit.ai_model_inputs(model_id) or {
            "archive_path": "", "title": None, "meta": [], "tags": [], "images": [], "files": []}
        self._model_id    = model_id
        self._archive_path = inputs["archive_path"]
        display_title     = inputs["title"] or Path(self._archive_path).stem
        self._image_data  = []
        for image_id, filename in inputs["images"]:
            b = get_thumbnail_bytes(image_id)
            if b:
                stem = Path(filename).stem if filename else f"img{image_id}"
                self._image_data.append((b, stem))
        self._known_meta = _collect_known_meta(inputs)
        self._file_list = list(inputs["files"])

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
            None if self._ignore_meta_chk.isChecked() else self._known_meta,
            self._file_list,
            self._max_tags_spin.value(),
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

        _fill(self._title_edit,         "TITLE")
        _fill(self._creator_edit,       "CREATOR")
        _fill(self._sculptor_edit,      "SCULPTOR")
        _fill(self._collection_edit,    "COLLECTION")
        _fill(self._subcollection_edit, "SUBCOLLECTION")
        _fill(self._scale_edit,         "SCALE")

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
    def _on_max_tags_changed(self, value: int) -> None:
        """Persist the limit and immediately prune the shown tags to match."""
        QSettings().setValue(_MAX_TAGS_KEY, value)
        if value > 0 and len(self._current_tags) > value:
            del self._current_tags[value:]
            self._rebuild_pills()

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
            "title":         self._title_edit.text().strip(),
            "creator":       self._creator_edit.text().strip(),
            "sculptor":      self._sculptor_edit.text().strip(),
            "collection":    self._collection_edit.text().strip(),
            "subcollection": self._subcollection_edit.text().strip(),
            "scale":         self._scale_edit.text().strip(),
            "description":   self._desc_edit.toPlainText().strip(),
            "source":        source if source in _VALID_SOURCES else "",
            "year":          year,
        }
        if _commit_model_data(self._model_id, fields, self._current_tags,
                              self._overwrite_chk.isChecked()):
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
        overwrite = self._overwrite_chk.isChecked()
        policy = (
            "Existing values are replaced by the AI suggestions (tags are merged)."
            if overwrite else
            "Existing values are kept — only empty fields are filled and new tags added."
        )
        reply = QMessageBox.question(
            self, "Process All",
            f"Run AI tagging on all {n} model(s) and save the results automatically, "
            "without reviewing each one?\n\n"
            + policy,
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

        self._batch_thread = _BatchThread(
            self._model_ids, overwrite, self._ignore_meta_chk.isChecked(),
            self._max_tags_spin.value(),
        )
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
        self._overwrite_chk.setEnabled(not busy)
        self._ignore_meta_chk.setEnabled(not busy)
        self._max_tags_spin.setEnabled(not busy)
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
        # Batch run is done — close the dialog once the user dismisses the notice.
        self.accept()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._stop_thread()
        if self._batch_thread and self._batch_thread.isRunning():
            self._batch_thread.stop()
            self._batch_thread.wait(60000)
        # Shut the llama-server down to free the memory the model holds; it is
        # relaunched (model reload) the next time the dialog is opened.
        try:
            from app.utils import ai_server
            ai_server.stop()
        except Exception:
            pass
        super().closeEvent(event)
