"""Small standalone widgets/dialogs used by the model browser.

Extracted from ``browser_tab.py``. Qt-only, no app dependencies.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout


class ClickableLabel(QLabel):
    """QLabel that emits a *clicked* signal on left mouse button press and *context_menu_requested* on right-click."""

    clicked = pyqtSignal()
    context_menu_requested = pyqtSignal(int)  # image_id

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            image_id = self.property("image_id")
            if image_id is not None:
                self.context_menu_requested.emit(image_id)
        super().mousePressEvent(event)


class ImageViewerDialog(QDialog):
    """Full-screen image viewer — click anywhere to close."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Image"))
        self.setModal(True)
        self._pixmap = pixmap

        screen = self.screen().availableGeometry()
        max_w = int(screen.width() * 0.9)
        max_h = int(screen.height() * 0.9)

        scaled = pixmap.scaled(max_w, max_h,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)

        label = QLabel()
        label.setPixmap(scaled)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.mousePressEvent = lambda _: self.accept()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

        self.resize(scaled.width(), scaled.height())
