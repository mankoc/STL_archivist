"""
Image grid dialog — shows a grid of thumbnails for a list of models.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QScrollArea, QWidget, QGridLayout,
    QLabel, QFrame, QDialogButtonBox, QPushButton, QSlider,
)

from app.database.db import get_session
from app.database.models import STLModel
from app.utils.thumbnailer import pixmap_from_path

THUMB_SIZE_DEFAULT = 150
THUMB_SIZE_MIN = 60
THUMB_SIZE_MAX = 300
COLS = 5


class _GridImageViewer(QDialog):
    """Full-size image viewer with previous/next navigation for the grid thumbnails.

    Keyboard shortcuts: Left/Right arrows navigate, Escape closes.
    """

    def __init__(self, entries: list[tuple[QPixmap, str]], start_index: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Viewer")
        self.setModal(True)
        self._entries = entries
        self._index = start_index

        screen = self.screen().availableGeometry()
        self._max_w = int(screen.width() * 0.9)
        self._max_h = int(screen.height() * 0.9)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._image_lbl = QLabel()
        self._image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._image_lbl.mousePressEvent = lambda _: self.accept()
        layout.addWidget(self._image_lbl, 1)

        nav_row = QHBoxLayout()
        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.clicked.connect(self._go_prev)
        nav_row.addWidget(self._prev_btn)

        self._counter_lbl = QLabel()
        self._counter_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_row.addWidget(self._counter_lbl, 1)

        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._go_next)
        nav_row.addWidget(self._next_btn)
        layout.addLayout(nav_row)

        QShortcut(QKeySequence(Qt.Key.Key_Left),  self, self._go_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._go_next)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.accept)

        self._show_current()

    def _show_current(self) -> None:
        """Display the image at the current index and update navigation state."""
        px, title = self._entries[self._index]
        scaled = px.scaled(self._max_w, self._max_h,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self._image_lbl.setPixmap(scaled)
        self.setWindowTitle(title or "Image Viewer")
        self.resize(scaled.width(), scaled.height() + 48)
        total = len(self._entries)
        self._counter_lbl.setText(f"{self._index + 1} / {total}")
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < total - 1)

    def _go_prev(self) -> None:
        """Move to the previous image if one exists."""
        if self._index > 0:
            self._index -= 1
            self._show_current()

    def _go_next(self) -> None:
        """Move to the next image if one exists."""
        if self._index < len(self._entries) - 1:
            self._index += 1
            self._show_current()


class ImageGridDialog(QDialog):
    """Scrollable thumbnail grid for all models currently visible in the browser."""

    def __init__(self, model_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Image Grid — {len(model_ids)} model{'s' if len(model_ids) != 1 else ''}")
        self.resize(920, 700)
        self._entries: list[tuple[QPixmap, str]] = []
        # (thumb_label, original_pixmap, title_label) for each cell
        self._cells: list[tuple[QLabel, QPixmap, QLabel]] = []
        self._thumb_size = THUMB_SIZE_DEFAULT
        self._build_ui(model_ids)

    def _build_ui(self, model_ids: list[int]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Slider toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Size:"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(THUMB_SIZE_MIN, THUMB_SIZE_MAX)
        self._slider.setValue(THUMB_SIZE_DEFAULT)
        self._slider.setFixedWidth(200)
        self._slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self._slider.valueChanged.connect(self._on_slider_changed)
        toolbar.addWidget(self._slider)
        self._size_lbl = QLabel(f"{THUMB_SIZE_DEFAULT} px")
        self._size_lbl.setFixedWidth(50)
        toolbar.addWidget(self._size_lbl)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        container = QWidget()
        self._grid = QGridLayout(container)
        self._grid.setSpacing(10)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        session = get_session()
        try:
            grid_col = 0
            grid_row = 0
            for model_id in model_ids:
                stl: STLModel = session.get(STLModel, model_id)
                if stl is None:
                    continue

                px = QPixmap()
                for img in stl.images:
                    candidate = pixmap_from_path(img.thumbnail_path)
                    if not candidate.isNull():
                        px = candidate
                        break

                title = stl.title or ""
                entry_index = len(self._entries)
                self._entries.append((px, title))

                cell, thumb_lbl, title_lbl = self._make_cell(px, title, entry_index)
                self._cells.append((thumb_lbl, px, title_lbl))
                self._grid.addWidget(cell, grid_row, grid_col)

                grid_col += 1
                if grid_col >= COLS:
                    grid_col = 0
                    grid_row += 1
        finally:
            session.close()

        scroll.setWidget(container)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_cell(self, px: QPixmap, title: str, entry_index: int) -> tuple[QWidget, QLabel, QLabel]:
        """Build a single thumbnail cell; returns (cell_widget, thumb_label, title_label)."""
        size = self._thumb_size
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(4, 4, 4, 4)
        cell_layout.setSpacing(4)

        thumb = QLabel()
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedSize(size, size)
        thumb.setFrameShape(QFrame.Shape.StyledPanel)

        if not px.isNull():
            scaled = px.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb.setPixmap(scaled)
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb.mousePressEvent = lambda _e, i=entry_index: self._view_image(i)
        else:
            thumb.setText("No image")

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setMaximumWidth(size + 8)

        cell_layout.addWidget(thumb)
        cell_layout.addWidget(title_lbl)

        return cell, thumb, title_lbl

    def _on_slider_changed(self, value: int) -> None:
        """Rescale all thumbnails to the new size."""
        self._thumb_size = value
        self._size_lbl.setText(f"{value} px")
        for thumb_lbl, orig_px, title_lbl in self._cells:
            thumb_lbl.setFixedSize(value, value)
            title_lbl.setMaximumWidth(value + 8)
            if not orig_px.isNull():
                scaled = orig_px.scaled(
                    value, value,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                thumb_lbl.setPixmap(scaled)

    def _view_image(self, index: int) -> None:
        """Open the full-size viewer starting at the given grid *index*."""
        viewable = [(px, t) for px, t in self._entries if not px.isNull()]
        if not viewable:
            return
        # Map index into the viewable-only list
        source_px = self._entries[index][0]
        try:
            view_index = next(i for i, (px, _) in enumerate(viewable) if px is source_px)
        except StopIteration:
            view_index = 0
        _GridImageViewer(viewable, view_index, self).exec()
