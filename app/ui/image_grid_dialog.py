"""
Image grid dialog — shows a grid of thumbnails for a list of models.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QScrollArea, QSlider, QPushButton, QSplitter,
    QToolButton, QVBoxLayout, QWidget,
)

from app.database.db import get_session
from app.database.models import STLModel
from app.utils.thumbnailer import pixmap_from_path

THUMB_SIZE_DEFAULT = 150
THUMB_SIZE_MIN = 60
GRID_SPACING = 10
GRID_MARGIN = 8

_SORT_OPTIONS: list[tuple[str, str]] = [
    ("Original order",  "original"),
    ("Name",            "title"),
    ("Author",          "creator"),
    ("Archive",         "archive"),
    ("Archive size",    "size"),
    ("Date",            "date"),
]


def _selected_style() -> str:
    from app.ui.config_dialog import get_selection_border_color, get_selection_bg_color
    return (
        f"QLabel {{ border: 3px solid {get_selection_border_color()}; "
        f"background-color: {get_selection_bg_color()}; }}"
    )


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1_048_576:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1_073_741_824:
        return f"{size_bytes / 1_048_576:.1f} MB"
    else:
        return f"{size_bytes / 1_073_741_824:.2f} GB"


@dataclass
class _CellData:
    """All per-cell data, kept in one place so sorting never de-syncs lists."""
    original_index: int
    model_id: int
    cell_widget: QWidget
    thumb_lbl: QLabel
    orig_px: QPixmap          # first image (used for thumbnail in grid)
    all_images: list[QPixmap] # all images for the archive
    title_lbl: QLabel
    # Display fields (original case)
    title: str
    creator: str
    source: str
    date_str: str           # "YYYY-MM" / "YYYY" / ""
    archive_display: str    # basename of archive file
    tags: str               # comma-joined
    scale: str
    presupports: str
    warning: str            # newline-separated; "" if none
    description: str
    url: str
    # Sort keys
    sort_title: str
    sort_creator: str
    sort_archive: str       # archive filename, lowercase
    sort_size: int          # archive file size in bytes, 0 if unavailable
    sort_date: tuple[int, int]   # (year, month) for stable comparison


class _GridImageViewer(QDialog):
    """Full-size viewer for all images of a single archive.

    Left pane: scaled image (click to close); Left/Right arrows or buttons
    navigate between the archive's images.
    Right pane: scrollable metadata for the archive (static — same for all images).
    """

    def __init__(self, cd: _CellData, parent=None) -> None:
        super().__init__(parent)
        self._images = cd.all_images
        self._index  = 0
        self.setWindowTitle(cd.title or "Image Viewer")
        self.setModal(True)

        screen = self.screen().availableGeometry()
        self.resize(min(int(screen.width() * 0.92), 1100),
                    min(int(screen.height() * 0.92), 780))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Splitter: image left, metadata right ─────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        img_widget = QWidget()
        img_layout = QVBoxLayout(img_widget)
        img_layout.setContentsMargins(0, 0, 4, 0)
        img_layout.setSpacing(0)

        self._image_lbl = QLabel()
        self._image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._image_lbl.mousePressEvent = lambda _: self.accept()
        img_layout.addWidget(self._image_lbl, 1)

        self._splitter.addWidget(img_widget)

        meta_scroll = QScrollArea()
        meta_scroll.setWidgetResizable(True)
        meta_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        meta_scroll.setMinimumWidth(220)

        meta_container = QWidget()
        self._meta_form = QFormLayout(meta_container)
        self._meta_form.setContentsMargins(8, 8, 8, 8)
        self._meta_form.setSpacing(6)
        self._meta_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
        )
        self._meta_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        meta_scroll.setWidget(meta_container)

        self._splitter.addWidget(meta_scroll)
        outer.addWidget(self._splitter, 1)

        # ── Navigation row ────────────────────────────────────────────────────
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
        outer.addLayout(nav_row)

        QShortcut(QKeySequence(Qt.Key.Key_Left),   self, self._go_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right),  self, self._go_next)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.accept)

        # Metadata is the same for every image in this archive — populate once.
        self._populate_meta(cd)
        self._show_current()

    # ── Navigation ────────────────────────────────────────────────────────────

    def _show_current(self) -> None:
        self._rescale_image()
        total = len(self._images)
        self._counter_lbl.setText(f"Image {self._index + 1} / {total}")
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < total - 1)

    def _go_prev(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._show_current()

    def _go_next(self) -> None:
        if self._index < len(self._images) - 1:
            self._index += 1
            self._show_current()

    # ── Image rendering ───────────────────────────────────────────────────────

    def _rescale_image(self) -> None:
        if not self._images:
            return
        px = self._images[self._index]
        if px.isNull():
            return
        w = self._image_lbl.width()
        h = self._image_lbl.height()
        if w < 10 or h < 10:
            w = max(300, self.width() - 310)
            h = max(300, self.height() - 60)
        self._image_lbl.setPixmap(
            px.scaled(w, h,
                      Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        )

    # ── Metadata ──────────────────────────────────────────────────────────────

    def _populate_meta(self, cd: _CellData) -> None:
        def _row(label: str, value: str, *, color: str = "") -> None:
            if not value:
                return
            lbl = QLabel(f"<b>{label}</b>")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            val = QLabel(value)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if color:
                val.setStyleSheet(f"color: {color};")
            self._meta_form.addRow(lbl, val)

        _row("Title",       cd.title)
        _row("Creator",     cd.creator)
        _row("Source",      cd.source)
        _row("Date",        cd.date_str)
        _row("Tags",        cd.tags)
        _row("Scale",       cd.scale)
        _row("Presupports", cd.presupports)
        if cd.warning:
            _row("Warning", cd.warning.replace("\n", " · "), color="#d32f2f")
        _row("Archive",     cd.archive_display)
        if cd.sort_size:
            _row("Size",    _format_size(cd.sort_size))
        _row("Description", cd.description)
        if cd.url:
            lbl = QLabel("<b>URL</b>")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            url_lbl = QLabel(f'<a href="{cd.url}">{cd.url}</a>')
            url_lbl.setOpenExternalLinks(True)
            url_lbl.setWordWrap(True)
            self._meta_form.addRow(lbl, url_lbl)

    # ── Qt events ─────────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._splitter.setSizes([self.width() - 290, 280])
        self._rescale_image()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale_image()


class ImageGridDialog(QDialog):
    """Scrollable thumbnail grid for all models currently visible in the browser."""

    #: Emitted when the user clicks "Show selected in main window".
    #: Carries the list of selected model IDs.
    show_selection_requested = pyqtSignal(list)

    def __init__(self, model_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Image Grid — {len(model_ids)} model{'s' if len(model_ids) != 1 else ''}")
        self.resize(920, 700)
        self._cells_data: list[_CellData] = []
        self._selected_indices: set[int] = set()   # tracks original_index values
        self._thumb_size = THUMB_SIZE_DEFAULT
        self._current_cols = 0
        self._sort_key = "original"
        self._sort_desc = False
        self._scroll: QScrollArea | None = None
        self._grid: QGridLayout | None = None
        self._build_ui(model_ids)

    def _build_ui(self, model_ids: list[int]) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ── Toolbar ──────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()

        toolbar.addWidget(QLabel("Size:"))
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

        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("Sort:"))

        self._sort_combo = QComboBox()
        for label, _ in _SORT_OPTIONS:
            self._sort_combo.addItem(label)
        self._sort_combo.setFixedWidth(130)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        toolbar.addWidget(self._sort_combo)

        self._sort_dir_btn = QToolButton()
        self._sort_dir_btn.setText("▲")
        self._sort_dir_btn.setToolTip("Ascending")
        self._sort_dir_btn.setCheckable(True)
        self._sort_dir_btn.toggled.connect(self._on_sort_dir_toggled)
        toolbar.addWidget(self._sort_dir_btn)

        toolbar.addSpacing(16)

        sel_all_btn = QPushButton("Select All")
        sel_all_btn.setFixedWidth(80)
        sel_all_btn.clicked.connect(self._select_all)
        toolbar.addWidget(sel_all_btn)

        sel_none_btn = QPushButton("Deselect All")
        sel_none_btn.setFixedWidth(90)
        sel_none_btn.clicked.connect(self._deselect_all)
        toolbar.addWidget(sel_none_btn)

        self._sel_count_lbl = QLabel()
        self._sel_count_lbl.setFixedWidth(90)
        toolbar.addWidget(self._sel_count_lbl)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── Scroll area ───────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        layout.addWidget(self._scroll)

        container = QWidget()
        self._grid = QGridLayout(container)
        self._grid.setSpacing(GRID_SPACING)
        self._grid.setContentsMargins(GRID_MARGIN, GRID_MARGIN, GRID_MARGIN, GRID_MARGIN)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        session = get_session()
        try:
            for idx, model_id in enumerate(model_ids):
                stl: STLModel = session.get(STLModel, model_id)
                if stl is None:
                    continue

                all_images: list[QPixmap] = []
                for img in stl.images:
                    candidate = pixmap_from_path(img.thumbnail_path)
                    if not candidate.isNull():
                        all_images.append(candidate)
                px = all_images[0] if all_images else QPixmap()

                title = stl.title or ""
                creator = stl.creator or ""
                source = stl.source or ""
                year  = stl.release_year  or 0
                month = stl.release_month or 0
                date_str = (
                    f"{year}-{month:02d}" if year and month else
                    str(year) if year else ""
                )

                archive_path_str = stl.archive_path or ""
                archive_name_lc = Path(archive_path_str.replace("\\", "/")).name.lower()
                archive_display = Path(archive_path_str.replace("\\", "/")).name

                try:
                    archive_size = Path(archive_path_str).stat().st_size if archive_path_str else 0
                except OSError:
                    archive_size = 0

                tags_str    = ", ".join(t.name for t in stl.tags)
                scale_str   = stl.scale.name if stl.scale else ""
                presupports = stl.presupports or ""
                warning     = stl.warning or ""
                description = stl.description or ""
                url         = stl.url or ""

                cell, thumb_lbl, title_lbl = self._make_cell(px, title, idx)
                self._cells_data.append(_CellData(
                    original_index=idx,
                    model_id=model_id,
                    cell_widget=cell,
                    thumb_lbl=thumb_lbl,
                    orig_px=px,
                    all_images=all_images,
                    title_lbl=title_lbl,
                    title=title,
                    creator=creator,
                    source=source,
                    date_str=date_str,
                    archive_display=archive_display,
                    tags=tags_str,
                    scale=scale_str,
                    presupports=presupports,
                    warning=warning,
                    description=description,
                    url=url,
                    sort_title=title.lower(),
                    sort_creator=creator.lower(),
                    sort_archive=archive_name_lc,
                    sort_size=archive_size,
                    sort_date=(year, month),
                ))
        finally:
            session.close()

        self._scroll.setWidget(container)

        bottom = QHBoxLayout()
        self._show_btn = QPushButton("Show selected in main window")
        self._show_btn.setEnabled(False)
        self._show_btn.setToolTip("Filter the main browser to show only selected models")
        self._show_btn.clicked.connect(self._on_show_selected)
        bottom.addWidget(self._show_btn)
        bottom.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

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
            thumb.mousePressEvent = lambda e, i=entry_index: self._on_thumb_press(i, e)
        else:
            thumb.setText("No image")
            thumb.mousePressEvent = lambda e, i=entry_index: self._on_thumb_press(i, e)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setMaximumWidth(size + 8)

        cell_layout.addWidget(thumb)
        cell_layout.addWidget(title_lbl)
        return cell, thumb, title_lbl

    # ── Sort ─────────────────────────────────────────────────────────────────

    def _sorted_cells(self) -> list[_CellData]:
        """Return _cells_data sorted by the current sort key and direction."""
        key = self._sort_key
        if key == "original":
            ordered = sorted(self._cells_data, key=lambda c: c.original_index)
        elif key == "title":
            ordered = sorted(self._cells_data, key=lambda c: (c.sort_title, c.original_index))
        elif key == "creator":
            ordered = sorted(self._cells_data, key=lambda c: (c.sort_creator, c.sort_title))
        elif key == "archive":
            ordered = sorted(self._cells_data, key=lambda c: (c.sort_archive, c.original_index))
        elif key == "size":
            ordered = sorted(self._cells_data, key=lambda c: (c.sort_size, c.sort_title))
        elif key == "date":
            ordered = sorted(self._cells_data, key=lambda c: (c.sort_date, c.sort_title))
        else:
            ordered = list(self._cells_data)
        if self._sort_desc:
            ordered.reverse()
        return ordered

    def _on_sort_changed(self, index: int) -> None:
        self._sort_key = _SORT_OPTIONS[index][1]
        self._current_cols = 0   # force full relayout
        self._relayout()

    def _on_sort_dir_toggled(self, checked: bool) -> None:
        self._sort_desc = checked
        self._sort_dir_btn.setText("▼" if checked else "▲")
        self._sort_dir_btn.setToolTip("Descending" if checked else "Ascending")
        self._current_cols = 0
        self._relayout()

    # ── Selection ────────────────────────────────────────────────────────────

    def _on_thumb_press(self, original_index: int, event) -> None:
        """Ctrl+click toggles selection; plain click opens the viewer."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._toggle_select(original_index)
        else:
            self._view_image(original_index)

    def _toggle_select(self, original_index: int) -> None:
        if original_index in self._selected_indices:
            self._selected_indices.discard(original_index)
        else:
            self._selected_indices.add(original_index)
        for cd in self._cells_data:
            if cd.original_index == original_index:
                self._apply_cell_style(cd)
                break
        self._update_selection_ui()

    def _apply_cell_style(self, cd: _CellData) -> None:
        if cd.original_index in self._selected_indices:
            cd.thumb_lbl.setStyleSheet(_selected_style())
        else:
            cd.thumb_lbl.setStyleSheet("")

    def _update_selection_ui(self) -> None:
        n = len(self._selected_indices)
        self._sel_count_lbl.setText(f"{n} selected" if n else "")
        self._show_btn.setEnabled(n > 0)

    def _select_all(self) -> None:
        self._selected_indices = {cd.original_index for cd in self._cells_data}
        for cd in self._cells_data:
            self._apply_cell_style(cd)
        self._update_selection_ui()

    def _deselect_all(self) -> None:
        self._selected_indices.clear()
        for cd in self._cells_data:
            self._apply_cell_style(cd)
        self._update_selection_ui()

    def _on_show_selected(self) -> None:
        ids = [
            cd.model_id for cd in self._cells_data
            if cd.original_index in self._selected_indices
        ]
        if ids:
            self.show_selection_requested.emit(ids)
            self.accept()

    # ── Layout ───────────────────────────────────────────────────────────────

    def _compute_cols(self) -> int:
        if self._scroll is None:
            return max(1, (self.width() - 2 * GRID_MARGIN) // (self._thumb_size + GRID_SPACING))
        vp_width = self._scroll.viewport().width() - 2 * GRID_MARGIN
        return max(1, vp_width // (self._thumb_size + GRID_SPACING))

    def _relayout(self) -> None:
        cols = self._compute_cols()
        # Always relayout if sort changed (forced by _current_cols = 0) or cols changed.
        if cols == self._current_cols and cols != 0:
            return
        self._current_cols = cols

        # Clear the grid without destroying the widgets.
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)

        for i, cd in enumerate(self._sorted_cells()):
            self._grid.addWidget(cd.cell_widget, i // cols, i % cols)
            cd.cell_widget.setParent(self._grid.parentWidget())
            cd.cell_widget.show()

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
        for cd in self._cells_data:
            cd.thumb_lbl.setFixedSize(value, value)
            cd.title_lbl.setMaximumWidth(value + 8)
            if not cd.orig_px.isNull():
                scaled = cd.orig_px.scaled(
                    value, value,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                cd.thumb_lbl.setPixmap(scaled)
        self._relayout()

    def _view_image(self, original_index: int) -> None:
        """Open the full-size viewer showing all images for the clicked archive."""
        for cd in self._cells_data:
            if cd.original_index == original_index:
                if cd.all_images:
                    _GridImageViewer(cd, self).exec()
                return
