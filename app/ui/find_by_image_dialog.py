"""
Find by Image dialog — loads an image from disk, computes its perceptual hash
(dHash), and shows database models whose images are closest by Hamming distance.

Clicking a result shows its full thumbnail and model details in the side panel,
with a *Go to Model* button that selects the model in the main table.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QDoubleSpinBox, QComboBox, QSplitter, QVBoxLayout, QWidget, QFrame,
)

from app.services.image_similarity import MAX_RESULT_IMAGES
from app.utils.thumbnailer import pixmap_from_path
from app.utils import image_embedding as _emb
import numpy as np
from app.utils.image_hash import (
    compute_dhash_from_jpeg, dhash_available, format_dhash, parse_dhash,
    _hamming128_batch,
)
from app.utils.thumbnailer import make_thumbnail

logger = logging.getLogger(__name__)

_THUMB_SIZE       = 130
_CELL_WIDTH       = 156   # thumb + padding
_COLS             = 4
_MAX_DIST_DEFAULT = 16
_DETAIL_WIDTH     = 340


# ─────────────────────────────────────────────────────────────────────────────
# Result cell widget
# ─────────────────────────────────────────────────────────────────────────────

class _ResultCell(QFrame):
    """Thumbnail + distance badge + title for a single search result."""

    clicked   = pyqtSignal(object)   # emits the cell itself
    activated = pyqtSignal(int)      # double-click: emits model_id

    def __init__(self, model_id: int, thumbnail_path: Optional[str],
                 title: str, archive: str, score_text: str, parent=None) -> None:
        super().__init__(parent)
        self.model_id = model_id
        self.thumbnail_path = thumbnail_path
        self.score_text = score_text
        self.setObjectName("resultCell")
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(_CELL_WIDTH)
        self.setToolTip(
            f"{title or '(no title)'}\n{archive}\n{score_text}\n"
            + self.tr("Click for details, double-click to go to the model")
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        px = pixmap_from_path(thumbnail_path)
        thumb = QLabel()
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedHeight(_THUMB_SIZE)
        if not px.isNull():
            thumb.setPixmap(
                px.scaled(_THUMB_SIZE, _THUMB_SIZE,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        else:
            thumb.setText(self.tr("(no image)"))
        layout.addWidget(thumb)

        dist_lbl = QLabel(score_text)
        dist_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dist_lbl.setStyleSheet("font-size: 10px; color: gray;")
        layout.addWidget(dist_lbl)

        title_lbl = QLabel(title or "(no title)")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setFixedHeight(36)
        title_lbl.setStyleSheet("font-size: 10px;")
        layout.addWidget(title_lbl)

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(
            "QFrame#resultCell { border: 2px solid palette(highlight); }" if selected else ""
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.model_id)
        super().mouseDoubleClickEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Detail panel
# ─────────────────────────────────────────────────────────────────────────────

class _FitImageLabel(QLabel):
    """Shows a pixmap scaled down to fit the label; never enlarged past full size."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_source(self, px: QPixmap) -> None:
        self._source = px
        self._rescale()

    def sizeHint(self) -> QSize:
        # Not the pixmap's size: that would make the panel grow to fit the
        # image instead of the image shrinking to fit the panel.
        return QSize(_DETAIL_WIDTH - 20, 280)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source.isNull():
            return
        w, h = self.width(), self.height()
        if self._source.width() <= w and self._source.height() <= h:
            self.setPixmap(self._source)
        else:
            self.setPixmap(self._source.scaled(
                w, h, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))


class _MatchDetail(QWidget):
    """Full thumbnail, model details and a Go to Model button for one result."""

    go_requested = pyqtSignal(int)   # model_id

    # (key, label) in display order; empty values hide their row.
    _FIELDS = (
        ("title", "Title"), ("creator", "Creator"), ("source", "Source"),
        ("sculptor_name", "Sculptor"), ("collection", "Collection"),
        ("category_name", "Category"), ("release_name", "Release"),
        ("released", "Released"), ("tags", "Tags"), ("image_count", "Images"),
        ("archive_size", "Size"), ("status", "Status"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model_id: Optional[int] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        hdr = QLabel(self.tr("<b>Selected Match</b>"))
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hdr)

        self._image = _FitImageLabel()
        self._image.setStyleSheet("background: palette(base); border: 1px solid palette(mid);")
        layout.addWidget(self._image, 1)

        self._score = QLabel()
        self._score.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._score.setStyleSheet("color: gray;")
        layout.addWidget(self._score)

        self._form = QFormLayout()
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._values: dict[str, QLabel] = {}
        for key, label in self._FIELDS:
            val = QLabel()
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._values[key] = val
            self._form.addRow(self.tr(label) + ":", val)
        # A path has no spaces to wrap at, so it gets a scrollable read-only field.
        self._archive = QLineEdit()
        self._archive.setReadOnly(True)
        self._archive.setFrame(False)
        self._form.addRow(self.tr("Archive:"), self._archive)
        layout.addLayout(self._form)

        self._go_btn = QPushButton(self.tr("Go to Model"))
        self._go_btn.setToolTip(self.tr("Show this model in the main table"))
        self._go_btn.clicked.connect(lambda: self.go_requested.emit(self._model_id))
        layout.addWidget(self._go_btn)

        self.clear()

    def clear(self) -> None:
        self._model_id = None
        self._image.set_source(QPixmap())
        self._image.clear()
        self._image.setText(self.tr("Click a result to see it here."))
        self._score.clear()
        for key, _label in self._FIELDS:
            self._set_row(key, "")
        self._archive.clear()
        self._go_btn.setEnabled(False)

    def show_match(self, model_id: int, thumbnail_path: Optional[str], score_text: str) -> None:
        from app.services.model_summary import model_summary
        from app.ui.browser_common import _fmt_size

        px = pixmap_from_path(thumbnail_path)   # full stored thumbnail, one LMDB read
        if px.isNull():
            self._image.set_source(QPixmap())
            self._image.clear()
            self._image.setText(self.tr("(no image)"))
        else:
            self._image.set_source(px)
        self._score.setText(score_text)

        info = model_summary(None, model_id)
        if info is None:
            self.clear()
            self._image.setText(self.tr("This model no longer exists."))
            return

        self._model_id = model_id
        collection = " / ".join(x for x in (info["collection_name"], info["subcollection_name"]) if x)
        released = ""
        if info["release_year"]:
            released = str(info["release_year"])
            if info["release_month"]:
                released += f"-{info['release_month']:02d}"
        values = {
            **info,
            "collection": collection,
            "released": released,
            "image_count": str(info["image_count"]),
            "archive_size": _fmt_size(info["archive_size"]) if info["archive_size"] else "",
            "status": self.tr("Missing on disk") if info["is_missing"] else "",
        }
        for key, _label in self._FIELDS:
            self._set_row(key, values.get(key) or "")
        self._archive.setText(info["archive_path"] or "")
        self._archive.setCursorPosition(0)
        self._archive.setToolTip(info["archive_path"] or "")
        self._go_btn.setEnabled(True)

    def _set_row(self, key: str, text: str) -> None:
        self._values[key].setText(str(text))
        self._form.setRowVisible(self._values[key], bool(text))


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class FindByImageDialog(QDialog):
    """Non-modal dialog: find database models by perceptual image similarity."""

    show_models_requested = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Find by Image"))
        self.setWindowFlag(Qt.WindowType.Window)
        self.resize(1240, 680)
        self._query_hash: Optional[int] = None
        self._query_raw:  Optional[bytes] = None
        self._query_embed = None
        self._dhash_outdated: int = 0
        self._selected_cell: Optional[_ResultCell] = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Control bar ───────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        ctrl.addWidget(QLabel(self.tr("Image:")))

        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText(self.tr("No image selected"))
        ctrl.addWidget(self._path_edit, 1)

        browse_btn = QPushButton(self.tr("Browse…"))
        browse_btn.clicked.connect(self._browse)
        ctrl.addWidget(browse_btn)

        # Matching method: perceptual hash (default) or learned embedding.
        from app.ui.config_dialog import get_ai_embeddings_enabled
        self._embed_available = _emb.is_available() and get_ai_embeddings_enabled()
        ctrl.addWidget(QLabel(self.tr("Method:")))
        self._method_combo = QComboBox()
        self._method_combo.addItem(self.tr("Perceptual hash (dHash)"), "dhash")
        self._method_combo.addItem(self.tr("AI embedding (CLIP)"), "embedding")
        if not self._embed_available:
            mi = self._method_combo.findData("embedding")
            self._method_combo.model().item(mi).setEnabled(False)
            self._method_combo.setItemData(
                mi,
                _emb.unavailable_reason() if not _emb.is_available()
                else self.tr("AI embeddings are disabled in Configuration → Browser"),
                Qt.ItemDataRole.ToolTipRole,
            )
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        ctrl.addWidget(self._method_combo)

        self._dist_label = QLabel(self.tr("Max distance:"))
        ctrl.addWidget(self._dist_label)
        self._dist_spin = QSpinBox()
        self._dist_spin.setRange(0, 64)
        self._dist_spin.setValue(_MAX_DIST_DEFAULT)
        self._dist_spin.setToolTip(
            self.tr("Hamming distance threshold (0 = exact match, 64 = very loose)")
        )
        ctrl.addWidget(self._dist_spin)

        self._sim_label = QLabel(self.tr("Min similarity:"))
        ctrl.addWidget(self._sim_label)
        self._sim_spin = QDoubleSpinBox()
        self._sim_spin.setRange(0.50, 1.00)
        self._sim_spin.setSingleStep(0.01)
        self._sim_spin.setDecimals(2)
        self._sim_spin.setValue(0.85)
        self._sim_spin.setToolTip(
            self.tr("Minimum cosine similarity between embeddings (1.00 = identical).")
        )
        ctrl.addWidget(self._sim_spin)
        self._sim_label.setVisible(False)
        self._sim_spin.setVisible(False)

        self._search_btn = QPushButton(self.tr("Search"))
        self._search_btn.setEnabled(False)
        self._search_btn.setDefault(True)
        self._search_btn.clicked.connect(self._run_search)
        ctrl.addWidget(self._search_btn)

        root.addLayout(ctrl)

        # ── Splitter: query image | results | selected match ──────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel — query image
        q_panel = QWidget()
        q_layout = QVBoxLayout(q_panel)
        q_layout.setContentsMargins(4, 4, 4, 4)
        q_layout.setSpacing(6)

        hdr = QLabel(self.tr("<b>Query Image</b>"))
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        q_layout.addWidget(hdr)

        self._query_img = QLabel(self.tr("No image loaded"))
        self._query_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._query_img.setFixedSize(_THUMB_SIZE, _THUMB_SIZE)
        self._query_img.setStyleSheet(
            "background: palette(base); border: 1px solid palette(mid);"
        )
        q_layout.addWidget(self._query_img, 0, Qt.AlignmentFlag.AlignHCenter)

        self._hash_lbl = QLabel()
        self._hash_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hash_lbl.setStyleSheet("font-size: 10px; color: gray;")
        q_layout.addWidget(self._hash_lbl)

        q_layout.addStretch()
        q_panel.setFixedWidth(_CELL_WIDTH)
        splitter.addWidget(q_panel)

        # Middle panel — results
        r_panel = QWidget()
        r_layout = QVBoxLayout(r_panel)
        r_layout.setContentsMargins(4, 4, 4, 4)
        r_layout.setSpacing(4)

        self._status_lbl = QLabel(self.tr("Load an image and click Search."))
        self._status_lbl.setWordWrap(True)
        r_layout.addWidget(self._status_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        r_layout.addWidget(self._scroll, 1)

        splitter.addWidget(r_panel)

        # Right panel — selected match
        self._detail = _MatchDetail()
        self._detail.go_requested.connect(self._go_to_model)
        splitter.addWidget(self._detail)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([_CELL_WIDTH, 1240 - _CELL_WIDTH - _DETAIL_WIDTH, _DETAIL_WIDTH])

        root.addWidget(splitter, 1)

    def _on_method_changed(self, *_args) -> None:
        is_embed = self._method_combo.currentData() == "embedding"
        self._dist_label.setVisible(not is_embed)
        self._dist_spin.setVisible(not is_embed)
        self._sim_label.setVisible(is_embed)
        self._sim_spin.setVisible(is_embed)

    # ------------------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Select Image"), str(Path.home()),
            self.tr("Images (*.png *.jpg *.jpeg *.webp *.avif *.bmp *.gif *.tif *.tiff)"),
        )
        if path:
            self._load_image(Path(path))

    def _load_image(self, path: Path) -> None:
        self._path_edit.setText(str(path))
        try:
            raw = path.read_bytes()
        except OSError as exc:
            self._status_lbl.setText(f"Error reading file: {exc}")
            self._query_hash = None
            self._search_btn.setEnabled(False)
            return

        h = compute_dhash_from_jpeg(raw)
        # Without the native core no dHash can be computed, but the embedding
        # search still works for any image Pillow can decode.
        decodable = h is not None or (not dhash_available() and make_thumbnail(raw) is not None)
        if not decodable:
            self._status_lbl.setText(
                self.tr("Could not compute hash — unsupported image format.")
            )
            self._query_hash = None
            self._search_btn.setEnabled(False)
            self._query_img.setText(self.tr("Cannot decode image"))
            self._hash_lbl.clear()
            return

        self._query_hash  = h
        self._query_raw   = raw
        self._query_embed = None   # computed lazily in _find_matches_embed when Search is clicked

        px = QPixmap()
        px.loadFromData(raw)
        if px.isNull():
            # Qt has no codec for some formats Pillow reads (AVIF, for one), so
            # fall back to the JPEG transcode used for thumbnails.
            jpeg = make_thumbnail(raw)
            if jpeg:
                px.loadFromData(jpeg)
        if not px.isNull():
            self._query_img.setPixmap(
                px.scaled(_THUMB_SIZE, _THUMB_SIZE,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self._query_img.setText(path.name)

        self._hash_lbl.setText(f"dHash: {format_dhash(h)}" if h is not None else "")
        self._search_btn.setEnabled(True)
        self._status_lbl.setText(self.tr("Image loaded. Click Search to find matches."))

    # ------------------------------------------------------------------
    def _run_search(self) -> None:
        if self._query_raw is None:
            return

        method = self._method_combo.currentData()
        if method != "embedding" and self._query_hash is None:
            self._status_lbl.setText(
                self.tr("Searching by perceptual hash needs the native core; "
                        "use the AI embedding method.")
            )
            return

        self._selected_cell = None
        self._detail.clear()
        old = self._scroll.takeWidget()
        if old:
            old.deleteLater()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if method == "embedding":
                thr = self._sim_spin.value()
                results, capped = self._find_matches_embed(thr)
                score_fmt = lambda v: f"Similarity: {v:.3f}"
                empty_msg = (
                    f"No matches found (similarity ≥ {thr:.2f}). "
                    "Run Database Maintenance → Compute Image Embeddings first."
                )
                criterion = f"similarity ≥ {thr:.2f}"
            else:
                max_dist = self._dist_spin.value()
                results, capped = self._find_matches(max_dist)
                score_fmt = lambda v: f"Distance: {v}"
                empty_msg = f"No matches found within distance {max_dist}."
                criterion = f"distance ≤ {max_dist}"
            if capped:
                found_msg = (
                    f"{len(results)} model(s) among the {MAX_RESULT_IMAGES} closest images "
                    f"({criterion}). More images match: tighten the threshold to narrow "
                    "the results."
                )
            else:
                found_msg = f"{len(results)} model(s) found ({criterion}), sorted by similarity."
        except Exception as exc:
            logger.exception("Find-by-image search failed")
            self._status_lbl.setText(f"Search failed: {exc}")
            QApplication.restoreOverrideCursor()
            return
        finally:
            QApplication.restoreOverrideCursor()

        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(8)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        if not results:
            if method == "dhash" and self._dhash_outdated > 0:
                empty_msg = self.tr(
                    "{n} image(s) were skipped because their hashes use an outdated format. "
                    "Run Database Maintenance → Recompute Image Hashes, then search again."
                ).replace("{n}", str(self._dhash_outdated))
            self._status_lbl.setText(empty_msg)
        else:
            suffix = ""
            if method == "dhash" and self._dhash_outdated > 0:
                suffix = self.tr(" ({n} image(s) with outdated hashes were skipped — "
                                 "Recompute Image Hashes for complete results)").replace(
                    "{n}", str(self._dhash_outdated))
            self._status_lbl.setText(found_msg + suffix)
            for i, (score, model_id, title, archive, thumb_path) in enumerate(results):
                cell = _ResultCell(model_id, thumb_path, title, archive, score_fmt(score))
                cell.clicked.connect(self._on_result_clicked)
                cell.activated.connect(self._go_to_model)
                grid.addWidget(cell, i // _COLS, i % _COLS)

        self._scroll.setWidget(container)

    def _find_matches(
        self, max_dist: int
    ) -> tuple[list[tuple[int, int, str, str, Optional[str]]], bool]:
        """Return ``(results, capped)``; results are (distance, model_id, title,
        archive, thumbnail_path) sorted by distance.

        Only the ``MAX_RESULT_IMAGES`` closest images within *max_dist* are
        considered (*capped* is True when more matched), and only the
        best-matching image per model is kept.  Uses vectorised numpy Hamming
        so the distance computation is a single C-level call rather than a
        Python loop over all rows.
        """
        query_hash = self._query_hash
        _U64 = (1 << 64) - 1
        qlo = np.array([query_hash & _U64], dtype=np.uint64)
        qhi = np.array([query_hash >> 64],  dtype=np.uint64)

        from app.services.model_views import image_hash_rows
        rows = image_hash_rows()

        # Parse stored hashes, skipping outdated (16-char) entries.
        outdated = 0
        valid_rows: list = []
        lo_list: list[int] = []
        hi_list: list[int] = []
        for model_id, dhash_hex, thumb_path, title, archive_path in rows:
            h = parse_dhash(dhash_hex)
            if h is None:
                outdated += 1
                continue
            lo_list.append(h & _U64)
            hi_list.append(h >> 64)
            valid_rows.append((model_id, thumb_path, title or "", archive_path))

        self._dhash_outdated = outdated
        if not valid_rows:
            return [], False

        lo_arr = np.array(lo_list, dtype=np.uint64)
        hi_arr = np.array(hi_list, dtype=np.uint64)
        dist_arr = _hamming128_batch(qlo, qhi, lo_arr, hi_arr)[0]  # shape (N,)

        hits = np.flatnonzero(dist_arr <= max_dist)
        capped = len(hits) > MAX_RESULT_IMAGES
        if capped:
            hits = hits[np.argsort(dist_arr[hits], kind="stable")[:MAX_RESULT_IMAGES]]

        best: dict[int, tuple[int, str, str, Optional[str]]] = {}
        for i in hits.tolist():
            model_id, thumb_path, title, archive_path = valid_rows[i]
            dist = int(dist_arr[i])
            if model_id not in best or dist < best[model_id][0]:
                archive_name = Path(archive_path).name if archive_path else ""
                best[model_id] = (dist, title, archive_name, thumb_path)

        out = [(d, mid, t, arch, tp) for mid, (d, t, arch, tp) in best.items()]
        out.sort(key=lambda x: x[0])
        return out, capped

    def _find_matches_embed(
        self, threshold: float
    ) -> tuple[list[tuple[float, int, str, str, Optional[str]]], bool]:
        """Return ``(results, capped)``, best image per model.

        Nearest-neighbour search runs inside SQLite (sqlite-vec), see
        :func:`app.services.image_similarity.find_similar_models`.
        """
        if self._query_raw is None:
            return [], False
        if self._query_embed is None:
            self._query_embed = _emb.embed_jpeg(self._query_raw)
        if self._query_embed is None:
            return [], False

        from app.services.image_similarity import find_similar_models
        return find_similar_models(None, self._query_embed, threshold)

    def _on_result_clicked(self, cell: _ResultCell) -> None:
        if self._selected_cell is not None:
            self._selected_cell.set_selected(False)
        self._selected_cell = cell
        cell.set_selected(True)
        self._detail.show_match(cell.model_id, cell.thumbnail_path, cell.score_text)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # A parented top-level window is "owned" on Windows and always stays
        # above its owner, so Go to Model could never bring the main window
        # forward.  Drop the window-level link; the Qt parent (and so the
        # dialog's lifetime) is unchanged.
        handle = self.windowHandle()
        if handle is not None and handle.transientParent() is not None:
            handle.setTransientParent(None)

    def _go_to_model(self, model_id: int) -> None:
        """Select *model_id* in the main table and bring the main window forward."""
        self.show_models_requested.emit([model_id])
        win = self.parentWidget()
        if win is not None:
            if win.isMinimized():
                win.showNormal()
            win.raise_()
            win.activateWindow()
