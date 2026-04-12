"""
Image grid dialog — shows a grid of thumbnails for a list of models.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QScrollArea, QWidget, QGridLayout,
    QLabel, QFrame, QDialogButtonBox,
)

from app.database.db import get_session
from app.database.models import STLModel
from app.utils.thumbnailer import pixmap_from_path

THUMB_SIZE = 150
COLS = 5


class ImageGridDialog(QDialog):
    def __init__(self, model_ids: list[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Image Grid — {len(model_ids)} model{'s' if len(model_ids) != 1 else ''}")
        self.resize(920, 700)
        self._build_ui(model_ids)

    def _build_ui(self, model_ids: list[int]):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        container = QWidget()
        grid = QGridLayout(container)
        grid.setSpacing(10)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        session = get_session()
        try:
            grid_col = 0
            grid_row = 0
            for model_id in model_ids:
                stl: STLModel = session.get(STLModel, model_id)
                if stl is None:
                    continue

                # Use first available thumbnail
                px = QPixmap()
                for img in stl.images:
                    candidate = pixmap_from_path(img.thumbnail_path)
                    if not candidate.isNull():
                        px = candidate
                        break

                cell = self._make_cell(px, stl.title or "")
                grid.addWidget(cell, grid_row, grid_col)

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

    def _make_cell(self, px: QPixmap, title: str) -> QWidget:
        cell = QWidget()
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(4, 4, 4, 4)
        cell_layout.setSpacing(4)

        thumb = QLabel()
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        thumb.setFrameShape(QFrame.Shape.StyledPanel)

        if not px.isNull():
            scaled = px.scaled(
                THUMB_SIZE, THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb.setPixmap(scaled)
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)
            _px = px
            thumb.mousePressEvent = lambda _e, p=_px: self._view_image(p)
        else:
            thumb.setText("No image")

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setMaximumWidth(THUMB_SIZE + 8)

        cell_layout.addWidget(thumb)
        cell_layout.addWidget(title_lbl)

        return cell

    def _view_image(self, px: QPixmap):
        from app.ui.browser_tab import ImageViewerDialog
        ImageViewerDialog(px, self).exec()
