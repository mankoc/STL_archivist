"""Small standalone widgets/dialogs used by the model browser.

Extracted from ``browser_tab.py``. Qt-only, no app dependencies.
"""
from PyQt6.QtCore import Qt, pyqtSignal, QBuffer, QByteArray, QIODevice, QSize
from PyQt6.QtGui import QPixmap, QMovie
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
    """Full-screen image viewer — click anywhere to close.

    Pass *animated_data* (the stored GIF bytes) to play the animation instead of
    showing a still; *pixmap* is still required, since its dimensions decide how
    the dialog is sized.
    """

    def __init__(self, pixmap: QPixmap, parent=None, animated_data: bytes = None) -> None:
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
        if animated_data:
            self._movie = QMovie(self)
            buf = QBuffer(self._movie)
            buf.setData(QByteArray(animated_data))
            buf.open(QIODevice.OpenModeFlag.ReadOnly)
            self._movie.setDevice(buf)
            self._movie.setCacheMode(QMovie.CacheMode.CacheAll)
            if self._movie.isValid():
                self._movie.setScaledSize(scaled.size())
                label.setMovie(self._movie)
                self._movie.start()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        label.mousePressEvent = lambda _: self.accept()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)

        self.resize(scaled.width(), scaled.height())
