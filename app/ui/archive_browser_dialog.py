"""
Archive image browser — browse and view images organized by crawled directories.

Left pane: tree of all crawled directories.
Right pane: grid of one representative image per archive in the selected directory.
Click an image to view all images for that archive in a modal.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QThread, QSettings
from PyQt6.QtGui import QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QScrollArea, QSlider, QPushButton, QSplitter,
    QToolButton, QVBoxLayout, QWidget, QTreeWidget, QTreeWidgetItem,
)

from app.database.db import get_session
from app.database.models import STLModel, Image
from app.utils.thumbnailer import pixmap_from_path
from sqlalchemy import func, distinct, or_

THUMB_SIZE_DEFAULT = 150
THUMB_SIZE_MIN = 60
GRID_SPACING = 10
GRID_MARGIN = 8


def _selected_style() -> str:
    from app.ui.config_dialog import get_selection_border_color, get_selection_bg_color
    return (
        f"QLabel {{ border: 3px solid {get_selection_border_color()}; "
        f"background-color: {get_selection_bg_color()}; }}"
    )


@dataclass
class _ArchiveCell:
    """Data for a single archive cell in the grid."""
    model_id: int
    archive_name: str        # just the filename, no path
    archive_path: str        # full path
    cell_widget: QWidget
    thumb_lbl: QLabel
    orig_px: QPixmap         # representative thumbnail
    all_images: list[QPixmap]  # all images for this archive
    title_lbl: QLabel
    # Display fields
    title: str
    creator: str


class _FullImageViewer(QDialog):
    """Full-size (100%) image viewer for a single image."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Viewer")
        self.setModal(True)

        screen = self.screen().availableGeometry()
        self.resize(min(int(screen.width() * 0.9), 1200),
                    min(int(screen.height() * 0.9), 900))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        img_lbl = QLabel()
        img_lbl.setPixmap(pixmap)
        img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        img_lbl.setToolTip(self.tr("Click to close"))
        img_lbl.mousePressEvent = lambda _: self.accept()
        scroll.setWidget(img_lbl)

        # Close button
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        close_layout.addWidget(close_btn)
        layout.addLayout(close_layout)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.accept)


class _ArchiveImageViewer(QDialog):
    """Grid viewer for all images of a single archive."""

    def __init__(self, ac: _ArchiveCell, parent=None) -> None:
        super().__init__(parent)
        self._images = ac.all_images
        self.setWindowTitle(ac.archive_name or "Images")
        self.setModal(True)

        screen = self.screen().availableGeometry()
        self.resize(min(int(screen.width() * 0.92), 1200),
                    min(int(screen.height() * 0.92), 800))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Metadata header ───────────────────────────────────────────────────
        meta_layout = QHBoxLayout()
        if ac.title:
            meta_layout.addWidget(QLabel(f"<b>{ac.title}</b>"))
        if ac.creator:
            meta_layout.addWidget(QLabel(f"by {ac.creator}"))
        meta_layout.addWidget(QLabel(f"({ac.archive_name})"))
        meta_layout.addStretch()
        layout.addLayout(meta_layout)

        # ── Size slider ───────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(self.tr("Size:")))

        from app.ui.config_dialog import get_max_thumb_size
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(THUMB_SIZE_MIN, get_max_thumb_size())
        self._thumb_size = THUMB_SIZE_DEFAULT
        self._slider.setValue(min(THUMB_SIZE_DEFAULT, get_max_thumb_size()))
        self._slider.setFixedWidth(200)
        self._slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self._slider.valueChanged.connect(lambda v: self._size_lbl.setText(f"{v} px"))
        self._slider.sliderReleased.connect(self._on_slider_released)
        toolbar.addWidget(self._slider)

        self._size_lbl = QLabel(f"{THUMB_SIZE_DEFAULT} px")
        self._size_lbl.setFixedWidth(50)
        toolbar.addWidget(self._size_lbl)
        toolbar.addStretch()

        layout.addLayout(toolbar)

        # ── Scroll area with grid ─────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        layout.addWidget(self._scroll, 1)

        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setSpacing(8)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        # Store original pixmaps and labels for resizing
        self._thumb_labels: list[QLabel] = []
        self._original_pixmaps: list[QPixmap] = []

        # Add images to grid (4 columns, adjustable)
        cols = 4

        for idx, px in enumerate(self._images):
            if px.isNull():
                continue

            # Create thumbnail
            thumb = QLabel()
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setFixedSize(self._thumb_size, self._thumb_size)
            thumb.setFrameShape(QFrame.Shape.StyledPanel)
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)

            scaled = px.scaled(
                self._thumb_size, self._thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb.setPixmap(scaled)
            thumb.mousePressEvent = lambda e, px=px: self._view_full_size(px)

            self._thumb_labels.append(thumb)
            self._original_pixmaps.append(px)

            self._grid.addWidget(thumb, idx // cols, idx % cols)

        self._scroll.setWidget(self._container)

        # ── Close button ──────────────────────────────────────────────────────
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        close_layout.addWidget(close_btn)
        layout.addLayout(close_layout)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.accept)

    def _view_full_size(self, pixmap: QPixmap) -> None:
        """Open full-size viewer for the clicked image."""
        viewer = _FullImageViewer(pixmap, self)
        viewer.exec()

    def _on_slider_released(self) -> None:
        """Update thumbnail size when slider is released."""
        value = self._slider.value()
        self._thumb_size = value

        # Update all thumbnails
        for thumb_lbl, original_px in zip(self._thumb_labels, self._original_pixmaps):
            thumb_lbl.setFixedSize(value, value)
            scaled = original_px.scaled(
                value, value,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            thumb_lbl.setPixmap(scaled)


class ArchiveBrowserDialog(QDialog):
    """Browse images organized by crawled directory.

    Left: tree of all unique archive folders.
    Right: grid of archives in the selected folder, showing one image per archive.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image Browsing")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.resize(1200, 700)

        self._selected_folder: str | None = None
        self._include_subfolders: bool = False
        self._cells_data: list[_ArchiveCell] = []
        self._thumb_size = THUMB_SIZE_DEFAULT
        self._current_cols = 0
        self._scroll: QScrollArea | None = None
        self._grid: QGridLayout | None = None
        self._folder_tree: QTreeWidget | None = None
        self._include_subfolders_btn: QPushButton | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # ── Left: folder tree ────────────────────────────────────────────────
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel(self.tr("Directories")))

        self._folder_tree = QTreeWidget()
        self._folder_tree.setHeaderHidden(True)
        self._folder_tree.itemClicked.connect(self._on_folder_selected)
        left_layout.addWidget(self._folder_tree)

        splitter.addWidget(left_widget)
        splitter.setSizes([250, 950])

        # ── Right: grid + toolbar ────────────────────────────────────────────
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        # Toolbar
        toolbar = QHBoxLayout()

        self._include_subfolders_btn = QPushButton(self.tr("Include Subfolders"))
        self._include_subfolders_btn.setCheckable(True)
        self._include_subfolders_btn.setToolTip(
            self.tr("Show all archives in subfolders for the current folder")
        )
        self._include_subfolders_btn.toggled.connect(self._on_subfolder_toggle)
        self._include_subfolders_btn.setEnabled(False)
        toolbar.addWidget(self._include_subfolders_btn)

        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel(self.tr("Size:")))

        from app.ui.config_dialog import get_max_thumb_size
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(THUMB_SIZE_MIN, get_max_thumb_size())
        self._slider.setValue(min(THUMB_SIZE_DEFAULT, get_max_thumb_size()))
        self._slider.setFixedWidth(200)
        self._slider.setTickPosition(QSlider.TickPosition.NoTicks)
        self._slider.valueChanged.connect(lambda v: self._size_lbl.setText(f"{v} px"))
        self._slider.sliderReleased.connect(self._on_slider_released)
        toolbar.addWidget(self._slider)

        self._size_lbl = QLabel(f"{THUMB_SIZE_DEFAULT} px")
        self._size_lbl.setFixedWidth(50)
        toolbar.addWidget(self._size_lbl)
        toolbar.addStretch()

        right_layout.addLayout(toolbar)

        # Scroll area with grid
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        right_layout.addWidget(self._scroll)

        container = QWidget()
        self._grid = QGridLayout(container)
        self._grid.setSpacing(GRID_SPACING)
        self._grid.setContentsMargins(GRID_MARGIN, GRID_MARGIN, GRID_MARGIN, GRID_MARGIN)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self._scroll.setWidget(container)

        splitter.addWidget(right_widget)

        # ── Load directory tree ──────────────────────────────────────────────
        self._populate_folder_tree()

    def _populate_folder_tree(self) -> None:
        """Load folder tree from cache, or query and build if not cached."""
        from app.database.filter_cache import get_folder_tree

        # Try to load from cache first
        cached_tree = get_folder_tree()
        if cached_tree is not None:
            self._build_tree_from_dict(cached_tree)
            return

        # Cache miss or empty — query database and build from scratch
        session = get_session()
        try:
            folders = session.query(
                distinct(STLModel.archive_folder)
            ).filter(STLModel.archive_folder.isnot(None)).order_by(STLModel.archive_folder).all()

            folder_list = [f[0] for f in folders if f[0]]
            # Build tree structure
            folder_tree = self._build_folder_tree(folder_list)
            self._build_tree_from_dict(folder_tree)
        finally:
            session.close()

    def _build_folder_tree(self, folders: list[str]) -> list[dict]:
        """Build a hierarchical tree structure from a flat list of folder paths."""
        root_nodes = {}
        all_nodes = {}

        for folder in folders:
            if not folder:
                continue

            folder = folder.replace("\\", "/")
            parts = folder.split("/")

            current_path = ""
            parent_node = None

            for part in parts:
                if not part:
                    continue

                current_path = current_path + "/" + part if current_path else part
                key = current_path.lower()

                if key not in all_nodes:
                    node = {
                        "name": part,
                        "path": current_path,
                        "children": [],
                    }
                    all_nodes[key] = node

                    if parent_node is None:
                        root_nodes[key] = node
                    else:
                        parent_node["children"].append(node)
                else:
                    node = all_nodes[key]

                parent_node = node

        return list(root_nodes.values())

    def _build_tree_from_dict(self, tree_nodes: list[dict]) -> None:
        """Populate the QTreeWidget from a hierarchical dict structure."""
        def add_node(parent_item: QTreeWidgetItem | None, node: dict) -> None:
            item = QTreeWidgetItem([node["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, node["path"])

            if parent_item is None:
                self._folder_tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)

            for child in node.get("children", []):
                add_node(item, child)

        for root_node in tree_nodes:
            add_node(None, root_node)

    def _on_folder_selected(self, item: QTreeWidgetItem, column: int) -> None:
        """Load archives for the selected folder."""
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if folder == self._selected_folder:
            return

        self._selected_folder = folder
        self._include_subfolders_btn.setEnabled(True)
        self._include_subfolders_btn.setChecked(False)
        self._cells_data.clear()
        self._load_archives_for_folder(folder, include_subfolders=False)
        self._current_cols = 0
        self._relayout()

        # Save the last selected folder
        settings = QSettings()
        settings.setValue("archive_browser/last_folder", folder)

    def _on_subfolder_toggle(self, checked: bool) -> None:
        """Toggle inclusion of subfolders."""
        if self._selected_folder is None:
            return

        self._include_subfolders = checked
        self._cells_data.clear()
        self._load_archives_for_folder(self._selected_folder, include_subfolders=checked)
        self._current_cols = 0
        self._relayout()

    def _load_archives_for_folder(self, folder: str, include_subfolders: bool = False) -> None:
        """Query all models in the given folder and build cell data.

        If include_subfolders is True, also include archives from all subfolders.
        """
        session = get_session()
        try:
            if include_subfolders:
                # Include archives from this folder and all subfolders
                # Use LIKE to match the folder and any subfolder starting with it
                folder_pattern = folder.replace("\\", "/")
                if not folder_pattern.endswith("/"):
                    folder_pattern += "/"

                models = session.query(STLModel).filter(
                    or_(
                        STLModel.archive_folder == folder,
                        STLModel.archive_folder.like(folder_pattern + "%")
                    )
                ).order_by(STLModel.archive_path).all()
            else:
                # Only this folder
                models = session.query(STLModel).filter(
                    STLModel.archive_folder == folder
                ).order_by(STLModel.archive_path).all()

            for idx, stl in enumerate(models):
                # Get all images for this model
                all_images: list[QPixmap] = []
                for img in stl.images:
                    candidate = pixmap_from_path(img.thumbnail_path)
                    if not candidate.isNull():
                        all_images.append(candidate)

                px = all_images[0] if all_images else QPixmap()

                title = stl.title or ""
                creator = stl.creator or ""
                archive_path_str = stl.archive_path or ""
                archive_name = Path(archive_path_str.replace("\\", "/")).name

                cell, thumb_lbl, title_lbl = self._make_cell(px, archive_name, idx)
                self._cells_data.append(_ArchiveCell(
                    model_id=stl.id,
                    archive_name=archive_name,
                    archive_path=archive_path_str,
                    cell_widget=cell,
                    thumb_lbl=thumb_lbl,
                    orig_px=px,
                    all_images=all_images,
                    title_lbl=title_lbl,
                    title=title,
                    creator=creator,
                ))
        finally:
            session.close()

    def _make_cell(self, px: QPixmap, archive_name: str, entry_index: int) -> tuple[QWidget, QLabel, QLabel]:
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
            thumb.mousePressEvent = lambda e, i=entry_index: self._on_thumb_press(i, e)
        else:
            thumb.setText(self.tr("No image"))
            thumb.mousePressEvent = lambda e, i=entry_index: self._on_thumb_press(i, e)

        title_lbl = QLabel(archive_name)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setMaximumWidth(size + 8)

        cell_layout.addWidget(thumb)
        cell_layout.addWidget(title_lbl)
        return cell, thumb, title_lbl

    def _on_thumb_press(self, cell_index: int, event) -> None:
        """Click opens the image viewer for that archive."""
        if cell_index < len(self._cells_data):
            self._view_archive(cell_index)

    def _view_archive(self, cell_index: int) -> None:
        """Open full-size viewer for the clicked archive."""
        if cell_index < len(self._cells_data):
            ac = self._cells_data[cell_index]
            if ac.all_images:
                _ArchiveImageViewer(ac, self).exec()

    def _compute_cols(self) -> int:
        if self._scroll is None:
            return max(1, (self.width() - 2 * GRID_MARGIN) // (self._thumb_size + GRID_SPACING))
        vp_width = self._scroll.viewport().width() - 2 * GRID_MARGIN
        return max(1, vp_width // (self._thumb_size + GRID_SPACING))

    def _relayout(self) -> None:
        if self._grid is None or self._scroll is None:
            return
        cols = self._compute_cols()
        if cols == self._current_cols and cols != 0:
            return
        self._current_cols = cols

        while self._grid.count():
            item = self._grid.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)

        for i, ac in enumerate(self._cells_data):
            self._grid.addWidget(ac.cell_widget, i // cols, i % cols)
            ac.cell_widget.setParent(self._grid.parentWidget())
            ac.cell_widget.show()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _on_slider_released(self) -> None:
        value = self._slider.value()
        self._thumb_size = value
        self._current_cols = 0
        for ac in self._cells_data:
            ac.thumb_lbl.setFixedSize(value, value)
            ac.title_lbl.setMaximumWidth(value + 8)
            if not ac.orig_px.isNull():
                scaled = ac.orig_px.scaled(
                    value, value,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                ac.thumb_lbl.setPixmap(scaled)
        self._relayout()

    def showEvent(self, event) -> None:
        """Restore the last selected folder when dialog is shown."""
        if event is not None:
            super().showEvent(event)

        # Restore last folder on first show
        if self._selected_folder is None and self._folder_tree.topLevelItemCount() > 0:
            settings = QSettings()
            last_folder = settings.value("archive_browser/last_folder", "", str)

            if last_folder:
                # Find and select the item in the tree
                self._select_folder_in_tree(last_folder)

    def _select_folder_in_tree(self, folder_path: str) -> None:
        """Find and select a folder item in the tree by its path."""
        def find_item(item: QTreeWidgetItem, target_path: str) -> QTreeWidgetItem | None:
            item_path = item.data(0, Qt.ItemDataRole.UserRole)
            if item_path == target_path:
                return item

            for i in range(item.childCount()):
                result = find_item(item.child(i), target_path)
                if result is not None:
                    return result

            return None

        # Search from all root items
        for i in range(self._folder_tree.topLevelItemCount()):
            root_item = self._folder_tree.topLevelItem(i)
            found_item = find_item(root_item, folder_path)
            if found_item is not None:
                self._folder_tree.setCurrentItem(found_item)
                self._on_folder_selected(found_item, 0)
                return
