"""
Find by Image dialog — loads an image from disk, computes its perceptual hash
(dHash), and shows database models whose images are closest by Hamming distance.
"""
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QVBoxLayout, QWidget, QFrame,
)

from app.database.db import session_scope
from app.database.models import Image as DBImage, STLModel
from app.utils.thumbnailer import pixmap_from_path
from app.ui.find_content_duplicates_dialog import compute_dhash_from_jpeg

logger = logging.getLogger(__name__)

_THUMB_SIZE       = 130
_CELL_WIDTH       = 156   # thumb + padding
_COLS             = 4
_MAX_DIST_DEFAULT = 10


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ─────────────────────────────────────────────────────────────────────────────
# Result cell widget
# ─────────────────────────────────────────────────────────────────────────────

class _ResultCell(QFrame):
    """Thumbnail + distance badge + title for a single search result."""

    clicked = pyqtSignal(int)   # emits model_id

    def __init__(self, model_id: int, thumbnail_path: Optional[str],
                 title: str, archive: str, distance: int, parent=None) -> None:
        super().__init__(parent)
        self._model_id = model_id
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(_CELL_WIDTH)
        self.setToolTip(
            f"{title or '(no title)'}\n{archive}\nDistance: {distance}\n"
            "Click to show in browser"
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
            thumb.setText("(no image)")
        layout.addWidget(thumb)

        dist_lbl = QLabel(f"Distance: {distance}")
        dist_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dist_lbl.setStyleSheet("font-size: 10px; color: gray;")
        layout.addWidget(dist_lbl)

        title_lbl = QLabel(title or "(no title)")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setFixedHeight(36)
        title_lbl.setStyleSheet("font-size: 10px;")
        layout.addWidget(title_lbl)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._model_id)
        super().mousePressEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class FindByImageDialog(QDialog):
    """Non-modal dialog: find database models by perceptual image similarity."""

    show_models_requested = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find by Image")
        self.setWindowFlag(Qt.WindowType.Window)
        self.resize(940, 640)
        self._query_hash: Optional[int] = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Control bar ───────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)
        ctrl.addWidget(QLabel("Image:"))

        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("No image selected")
        ctrl.addWidget(self._path_edit, 1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        ctrl.addWidget(browse_btn)

        ctrl.addWidget(QLabel("Max distance:"))
        self._dist_spin = QSpinBox()
        self._dist_spin.setRange(0, 32)
        self._dist_spin.setValue(_MAX_DIST_DEFAULT)
        self._dist_spin.setToolTip(
            "Hamming distance threshold (0 = exact match, 32 = very loose)"
        )
        ctrl.addWidget(self._dist_spin)

        self._search_btn = QPushButton("Search")
        self._search_btn.setEnabled(False)
        self._search_btn.setDefault(True)
        self._search_btn.clicked.connect(self._run_search)
        ctrl.addWidget(self._search_btn)

        root.addLayout(ctrl)

        # ── Splitter: query image left, results right ─────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel — query image
        q_panel = QWidget()
        q_layout = QVBoxLayout(q_panel)
        q_layout.setContentsMargins(4, 4, 4, 4)
        q_layout.setSpacing(6)

        hdr = QLabel("<b>Query Image</b>")
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        q_layout.addWidget(hdr)

        self._query_img = QLabel("No image loaded")
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

        # Right panel — results
        r_panel = QWidget()
        r_layout = QVBoxLayout(r_panel)
        r_layout.setContentsMargins(4, 4, 4, 4)
        r_layout.setSpacing(4)

        self._status_lbl = QLabel("Load an image and click Search.")
        r_layout.addWidget(self._status_lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        r_layout.addWidget(self._scroll, 1)

        splitter.addWidget(r_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, 1)

    # ------------------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)",
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
        if h is None:
            self._status_lbl.setText(
                "Could not compute hash — unsupported image format."
            )
            self._query_hash = None
            self._search_btn.setEnabled(False)
            self._query_img.setText("Cannot decode image")
            self._hash_lbl.clear()
            return

        self._query_hash = h

        px = QPixmap()
        px.loadFromData(raw)
        if not px.isNull():
            self._query_img.setPixmap(
                px.scaled(_THUMB_SIZE, _THUMB_SIZE,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        else:
            self._query_img.setText(path.name)

        self._hash_lbl.setText(f"dHash: {h:016x}")
        self._search_btn.setEnabled(True)
        self._status_lbl.setText("Image loaded. Click Search to find matches.")

    # ------------------------------------------------------------------
    def _run_search(self) -> None:
        if self._query_hash is None:
            return

        max_dist = self._dist_spin.value()

        old = self._scroll.takeWidget()
        if old:
            old.deleteLater()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            results = self._find_matches(max_dist)
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
            self._status_lbl.setText(
                f"No matches found within distance {max_dist}."
            )
        else:
            self._status_lbl.setText(
                f"{len(results)} model(s) found (distance ≤ {max_dist}),"
                " sorted by similarity."
            )
            for i, (dist, model_id, title, archive, thumb_path) in enumerate(results):
                cell = _ResultCell(model_id, thumb_path, title, archive, dist)
                cell.clicked.connect(self._on_result_clicked)
                grid.addWidget(cell, i // _COLS, i % _COLS)

        self._scroll.setWidget(container)

    def _find_matches(
        self, max_dist: int
    ) -> list[tuple[int, int, str, str, Optional[str]]]:
        """Return (distance, model_id, title, archive, thumbnail_path) sorted by distance.

        Deduplicates by model_id — only the best-matching image per model is kept.
        """
        query_hash = self._query_hash
        # best[model_id] = (distance, title, archive_path, thumbnail_path)
        best: dict[int, tuple[int, str, str, Optional[str]]] = {}

        with session_scope() as session:
            rows = (
                session.query(
                    DBImage.model_id,
                    DBImage.dhash,
                    DBImage.thumbnail_path,
                    STLModel.title,
                    STLModel.archive_path,
                )
                .join(STLModel, DBImage.model_id == STLModel.id)
                .filter(DBImage.dhash.isnot(None))
                .all()
            )

        for model_id, dhash_hex, thumb_path, title, archive_path in rows:
            try:
                h = int(dhash_hex, 16)
            except (ValueError, TypeError):
                continue
            dist = _hamming(query_hash, h)
            if dist <= max_dist:
                if model_id not in best or dist < best[model_id][0]:
                    archive_name = (
                        Path(archive_path).name if archive_path else ""
                    )
                    best[model_id] = (dist, title or "", archive_name, thumb_path)

        out = [
            (d, mid, t, arch, tp)
            for mid, (d, t, arch, tp) in best.items()
        ]
        out.sort(key=lambda x: x[0])
        return out

    def _on_result_clicked(self, model_id: int) -> None:
        self.show_models_requested.emit([model_id])
