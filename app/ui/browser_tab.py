"""
Tab 2 – Model browser.
Left panel: filter tree (Author / Tags / Type).
Centre: sortable table with thumbnail, title, author, category, date, tags, path.
Right panel: image thumbnail sidebar (populated on single-click).
Right-click or double-click: Edit Model.
"""
from pathlib import Path as FSPath, Path

_ICONS_DIR = FSPath(__file__).parent.parent.parent / "resources" / "icons"

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QSize, QStringListModel, QEvent, pyqtSignal, QItemSelection, QTimer, QThread, QRect, QPointF, QRectF
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QPixmap, QIcon, QKeySequence, QShortcut, QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTableView,
    QLineEdit, QLabel, QHeaderView, QAbstractItemView,
    QMenu, QScrollArea, QFrame, QInputDialog, QMessageBox,
    QStyledItemDelegate, QComboBox, QCompleter,
    QDialog, QSizePolicy, QApplication, QPushButton,
    QProgressDialog, QStyle, QStyleOptionViewItem,
)
from PyQt6.QtCore import QSettings

from sqlalchemy import func, or_, and_
from sqlalchemy.orm import selectinload

from app.database.db import get_session, session_scope
from app.database.models import STLModel, Tag, Sculptor, Collection, Subcollection, Category, Release, Scale
from app.utils.thumbnailer import pixmap_from_path
from app.utils.path_pattern import parse_release_date, format_release_date

# Column indices
COL_THUMB         = 0
COL_TITLE         = 1
COL_CREATOR       = 2
COL_SOURCE        = 3
COL_DATE          = 4
COL_TAGS          = 5
COL_SCULPTOR      = 6
COL_COLLECTION    = 7
COL_SUBCOLLECTION = 8
COL_SEQUENCE      = 9
COL_RELEASE       = 10
COL_PATH          = 11
COL_SCALE         = 12
COL_PRESUPPORTED  = 13
COL_ADDED         = 14
COL_WARNING       = 15
COL_FILESIZE      = 16
COL_CATEGORY      = 17
_HEADERS = ["", "Title", "Creator", "Source", "Date", "Tags",
            "Sculptor", "Collection", "Subcollection", "Sequence", "Release", "Path",
             "Scale", "Presupports", "Added", "Warnings", "File Size", "Category"]



def _fmt_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


class _BrowserProxyModel(QSortFilterProxyModel):
    """Proxy model that sorts the File Size column numerically via UserRole data."""

    def lessThan(self, left, right):
        # Numeric sort for File Size (stored under UserRole)
        if left.column() == COL_FILESIZE:
            return (left.data(Qt.ItemDataRole.UserRole) or -1) < (right.data(Qt.ItemDataRole.UserRole) or -1)
        # Numeric sort for Sequence column — use the numeric value stored under UserRole
        if left.column() == COL_SEQUENCE:
            def _to_int(v):
                try:
                    return int(v) if v is not None else -1
                except Exception:
                    return -1
            return _to_int(left.data(Qt.ItemDataRole.UserRole)) < _to_int(right.data(Qt.ItemDataRole.UserRole))
        return super().lessThan(left, right)

MODEL_ID_ROLE = Qt.ItemDataRole.UserRole + 1


class ClickableLabel(QLabel):
    """QLabel that emits a *clicked* signal on left mouse button press."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ImageViewerDialog(QDialog):
    """Full-screen image viewer — click anywhere to close."""

    def __init__(self, pixmap: QPixmap, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image")
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


class _ComboDelegate(QStyledItemDelegate):
    """Base delegate that shows an editable QComboBox populated by a query callable."""

    def _get_items(self) -> list[str]:
        raise NotImplementedError

    def createEditor(self, parent, option, index) -> QComboBox:
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(self._get_items())
        combo.setCurrentIndex(-1)
        combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        return combo

    def setEditorData(self, editor, index) -> None:
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText().strip())

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


class SourceDelegate(_ComboDelegate):
    """QComboBox of distinct source/distributor strings."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(filter(None, (
                v for (v,) in session.query(STLModel.source).distinct().all()
            )))
        finally:
            session.close()


class SculptorDelegate(_ComboDelegate):
    """QComboBox of all sculptor names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(s.name for s in session.query(Sculptor).order_by(Sculptor.name).all())
        finally:
            session.close()


class CollectionDelegate(_ComboDelegate):
    """QComboBox of all Collection names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(c.name for c in session.query(Collection).order_by(Collection.name).all())
        finally:
            session.close()


class SubcollectionDelegate(_ComboDelegate):
    """QComboBox of all Subcollection names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(
                s.name for s in session.query(Subcollection).order_by(Subcollection.name).all()
            )
        finally:
            session.close()


class CategoryDelegate(_ComboDelegate):
    """QComboBox of all Category names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(c.name for c in session.query(Category).order_by(Category.name).all())
        finally:
            session.close()


class ReleaseDelegate(_ComboDelegate):
    """QComboBox of all Release names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(r.name for r in session.query(Release).order_by(Release.name).all())
        finally:
            session.close()


class ScaleDelegate(_ComboDelegate):
    """QComboBox of all Scale names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(s.name for s in session.query(Scale).order_by(Scale.name).all())
        finally:
            session.close()


_PRESUPPORTS_OPTIONS = ["", "No", "Lychee", "Chitubox", "Presupported"]


class PresupportsDelegate(QStyledItemDelegate):
    """Fixed-option combo for the presupports column."""

    def createEditor(self, parent, option, index) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItems(_PRESUPPORTS_OPTIONS)
        return combo

    def setEditorData(self, editor, index) -> None:
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText())


class TagDelegate(QStyledItemDelegate):
    """QLineEdit that autocompletes each comma-separated token from the tag pool."""

    def createEditor(self, parent, option, index) -> "_TagLineEdit":
        session = get_session()
        try:
            all_tags = [t.name for t in session.query(Tag).order_by(Tag.name).all()]
        finally:
            session.close()

        edit = _TagLineEdit(all_tags, parent)
        return edit

    def setEditorData(self, editor, index) -> None:
        editor.setText(index.data() or "")
        editor.setCursorPosition(len(editor.text()))

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.text().strip())

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


class _TagLineEdit(QLineEdit):
    """QLineEdit with per-token autocomplete for comma-separated tag lists."""

    def __init__(self, all_tags: list[str], parent=None) -> None:
        super().__init__(parent)
        self._completer = QCompleter(self)
        self._completer.setModel(QStringListModel(all_tags, self._completer))
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)
        self.textEdited.connect(self._on_text_edited)

    def _current_token(self) -> str:
        return self.text()[:self.cursorPosition()].rsplit(",", 1)[-1].lstrip()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._show_completions()

    def _on_text_edited(self, _text: str) -> None:
        self._show_completions()

    def _show_completions(self) -> None:
        token = self._current_token()
        self._completer.setCompletionPrefix(token)
        self._completer.complete()

    def _insert_completion(self, chosen: str) -> None:
        text = self.text()
        cursor = self.cursorPosition()
        before = text[:cursor].rsplit(",", 1)
        prefix = (before[0] + ", ") if len(before) == 2 else ""
        after = text[cursor:]
        new_text = prefix + chosen + (", " if after.lstrip(",").strip() else ", ")
        self.setText(new_text)
        self.setCursorPosition(len(new_text))


class ThumbnailGeneratorWorker(QThread):
    """Background worker that (re-)generates thumbnails for a list of model IDs."""

    progress = pyqtSignal(int, int)   # (current, total)
    log      = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, model_ids: list[int], max_thumbnails: int, parent=None) -> None:
        super().__init__(parent)
        self._model_ids = model_ids
        self._max_thumbnails = max_thumbnails
        self._stop = False

    def stop(self) -> None:
        """Request the worker to stop as soon as possible."""
        self._stop = True

    def run(self) -> None:
        """Re-extract and save thumbnails for every model in *model_ids*, then update sidecars."""
        from pathlib import Path
        from app.database.db import session_scope
        from app.database.models import STLModel, Image as DBImage
        from app.crawler.archive import extract_archive_contents, IMAGE_SUFFIXES
        from app.utils.thumbnailer import save_thumbnail, delete_thumbnail, get_thumbnail_base_dir
        from app.ui.config_dialog import get_max_thumb_size, get_unrar_path

        max_size  = get_max_thumb_size()
        unrar     = get_unrar_path()
        total     = len(self._model_ids)
        modified_folders: set[Path] = set()

        for idx, model_id in enumerate(self._model_ids, start=1):
            if self._stop:
                self.log.emit("Thumbnail generation cancelled")
                break
            self.progress.emit(idx, total)

            with session_scope() as session:
                stl: STLModel = session.get(STLModel, model_id)
                if stl is None:
                    continue

                archive_path = Path(stl.archive_path)
                self.log.emit(f"[{idx}/{total}] {archive_path.name}")

                if not archive_path.exists():
                    self.log.emit(f"  WARNING: archive not found, skipping")
                    continue

                # Delete existing thumbnails
                for old_img in list(stl.images):
                    if old_img.thumbnail_path:
                        delete_thumbnail(old_img.thumbnail_path)
                    session.delete(old_img)
                session.flush()

                # Extract and generate new thumbnails
                from app.crawler.rules import detect_warnings
                from app.ui.config_dialog import get_warn_presupported_stl, get_warn_no_images, get_warn_nested_archive

                all_names: list[str] = []
                contents = extract_archive_contents(
                    archive_path, unrar_path=unrar, all_names_out=all_names,
                )
                image_names = sorted(
                    name for name in contents
                    if Path(name.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES
                )

                count = 0
                for filename in image_names:
                    if self._stop:
                        break
                    if count >= self._max_thumbnails:
                        break
                    raw_bytes = contents[filename]
                    img = DBImage(model=stl, filename=filename)
                    session.add(img)
                    session.flush()
                    rel_path = save_thumbnail(raw_bytes, img.id, max_size=max_size)
                    if rel_path:
                        img.thumbnail_path = rel_path
                        count += 1
                    else:
                        session.delete(img)
                        self.log.emit(f"  WARNING: thumbnail conversion failed for {filename}")

                self.log.emit(f"  {count} thumbnail(s) stored")

                warnings = detect_warnings(
                    all_names,
                    check_presupported=get_warn_presupported_stl(),
                    check_no_images=get_warn_no_images(),
                    check_nested_archive=get_warn_nested_archive(),
                )
                stl.warning = warnings or None
                if warnings:
                    self.log.emit(f"  WARNING: {warnings.replace(chr(10), '; ')}")

                modified_folders.add(archive_path.parent)

        # Update METADATA.7z for all affected folders (if option is enabled)
        thumb_base = get_thumbnail_base_dir()
        if modified_folders and thumb_base is not None:
            from app.ui.config_dialog import get_metadata_thumbnails_enabled, get_metadata_sidecar_enabled
            from app.crawler.folder_metadata import export_folder_metadata
            include_thumbs = get_metadata_thumbnails_enabled()
            if get_metadata_sidecar_enabled():
                self.log.emit(f"Updating METADATA.7z in {len(modified_folders)} folder(s)…")
                with session_scope() as session:
                    for folder in sorted(modified_folders):
                        export_folder_metadata(folder, session, thumb_base,
                                               include_thumbnails=True)
                        self.log.emit(f"  Updated METADATA.7z in {folder.name}")

        self.finished.emit()


class FilterItemDelegate(QStyledItemDelegate):
    """Replaces Qt's default tristate checkbox with colored circle indicators:
        Unchecked        — gray outline circle  (not selected)
        Checked          — green filled circle with white +  (include)
        PartiallyChecked — red filled circle with white −  (exclude)
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        is_checkable = bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        check_state  = index.data(Qt.ItemDataRole.CheckStateRole)

        if not is_checkable or check_state is None:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        widget = option.widget
        style  = widget.style() if widget else QApplication.style()

        check_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, widget
        )

        # Let the style draw the full item (icon + text + selection background).
        # Then overdraw the default checkbox indicator with our custom circle.
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        if check_rect.isValid():
            self._draw_indicator(painter, check_rect, Qt.CheckState(check_state))

    @staticmethod
    def _draw_indicator(painter: QPainter, rect: QRect, state: Qt.CheckState) -> None:
        size = (min(rect.width(), rect.height()) - 4) * 1.5
        if size < 4:
            return
        cx   = rect.center().x()
        cy   = rect.center().y()
        half = size / 2.0
        rf   = QRectF(cx - half, cy - half, size, size)
        pw   = max(1.5, size * 0.14)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if state == Qt.CheckState.Unchecked:
            # Neutral gray circle — "parent / not selected"
            painter.setPen(QPen(QColor("#888888"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rf)

        elif state == Qt.CheckState.Checked:
            # Green filled circle with white "+"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#4CAF50"))
            painter.drawEllipse(rf)
            pen = QPen(QColor("white"), pw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pad = 0.25
            painter.drawLine(QPointF(cx, cy - half * (1 - pad * 2)),
                             QPointF(cx, cy + half * (1 - pad * 2)))
            painter.drawLine(QPointF(cx - half * (1 - pad * 2), cy),
                             QPointF(cx + half * (1 - pad * 2), cy))

        elif state == Qt.CheckState.PartiallyChecked:
            # Red filled circle with white "−"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F44336"))
            painter.drawEllipse(rf)
            pen = QPen(QColor("white"), pw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pad = 0.25
            painter.drawLine(QPointF(cx - half * (1 - pad * 2), cy),
                             QPointF(cx + half * (1 - pad * 2), cy))

        painter.restore()


class FilterTreeWidget(QTreeWidget):
    """QTreeWidget whose checkable items cycle through three states on every click
    (Unchecked → Checked → PartiallyChecked → Unchecked), whether the click lands
    on the checkbox indicator or the item text.

    State semantics:
        Unchecked       — no filter applied for this value
        Checked         — include items that match this value
        PartiallyChecked — exclude items that match this value
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setItemDelegate(FilterItemDelegate(self))
        # Maps id(item) → state before the last checkbox click so _on_filter_item_changed
        # can decide which state comes next in the cycle.
        self._pre_click_states: dict[int, Qt.CheckState] = {}

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        item = self.itemAt(event.pos())
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            super().mousePressEvent(event)
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            super().mousePressEvent(event)
            return

        # Determine whether the click landed on the checkbox widget.
        vis_rect = self.visualItemRect(item)
        indicator_w = self.style().pixelMetric(
            QStyle.PixelMetric.PM_IndicatorWidth, None, self
        )
        is_checkbox_click = event.pos().x() <= vis_rect.left() + indicator_w + 4

        if is_checkbox_click:
            # Store pre-click state; let Qt perform its normal Unchecked↔Checked toggle,
            # then _on_filter_item_changed will complete the 3-step cycle.
            self._pre_click_states[id(item)] = item.checkState(0)
            super().mousePressEvent(event)
        else:
            data_tuple = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data_tuple, tuple) and data_tuple[0] == "group":
                # Group header text click → expand/collapse only; state changes via checkbox.
                super().mousePressEvent(event)
            else:
                # Leaf / path item text click → advance through the full 3-state cycle.
                new_state = self._next_check_state(item.checkState(0))
                self.blockSignals(True)
                item.setCheckState(0, new_state)
                self.blockSignals(False)
                self.itemChanged.emit(item, 0)
                event.accept()

    @staticmethod
    def _next_check_state(state: Qt.CheckState) -> Qt.CheckState:
        if state == Qt.CheckState.Unchecked:
            return Qt.CheckState.Checked
        if state == Qt.CheckState.Checked:
            return Qt.CheckState.PartiallyChecked
        return Qt.CheckState.Unchecked

    def _clear_all(self, root: QTreeWidgetItem) -> None:
        d = root.data(0, Qt.ItemDataRole.UserRole)
        if d is not None and (root.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            root.setCheckState(0, Qt.CheckState.Unchecked)
        for i in range(root.childCount()):
            self._clear_all(root.child(i))


class BrowserTab(QWidget):
    """Main model-browser widget.

    Composed of three panels in a horizontal splitter:
    - Left: collapsible filter tree (Author / Tags / Type / Date / Path).
    - Centre: search bar + sortable, editable QTableView of all models.
    - Right: thumbnail sidebar populated on single-click.

    Signals:
        model_count_changed(int): Emitted after each table reload with the new row count.
    """

    model_count_changed = pyqtSignal(int)
    # Emitted when a background thumbnail generation reports progress
    thumbnail_progress_updated = pyqtSignal(int, int)
    thumbnail_finished = pyqtSignal()
    thumbnail_started = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._thumb_pixmaps: list[QPixmap] = []  # original (unscaled) pixmaps
        self._cached_path_dirs: frozenset[str] = frozenset()
        self._cached_sorted_dirs: list[str] = []
        # Debounce rapid cell edits: keyed by (row, col) → QStandardItem
        self._pending_edits: dict[tuple[int, int], "QStandardItem"] = {}
        self._edit_timer = QTimer(self)
        self._edit_timer.setSingleShot(True)
        self._edit_timer.setInterval(400)  # ms
        self._edit_timer.timeout.connect(self._flush_pending_edits)
        # Debounce search-box keystrokes to avoid scanning on every character
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)  # ms
        self._search_timer.timeout.connect(
            lambda: self._on_search(self._search_edit.text())
        )
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # ---- Left: filter tree ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        filter_header = QHBoxLayout()
        filter_header.addWidget(QLabel("Filter"))
        filter_header.addStretch()
        self._clear_filters_btn = QPushButton("Clear")
        self._clear_filters_btn.setFlat(True)
        self._clear_filters_btn.setToolTip("Clear all active filters")
        self._clear_filters_btn.clicked.connect(self._clear_filters)
        filter_header.addWidget(self._clear_filters_btn)
        left_layout.addLayout(filter_header)

        self._filter_tree = FilterTreeWidget()
        self._filter_tree.setHeaderHidden(True)
        self._filter_tree.itemChanged.connect(self._on_filter_item_changed)
        left_layout.addWidget(self._filter_tree)

        # ---- Info panel: author + tags of selected model ----
        self._info_author_lbl = QLabel()
        self._info_author_lbl.setWordWrap(True)
        self._info_author_lbl.setVisible(False)
        left_layout.addWidget(self._info_author_lbl)

        self._info_tags_lbl = QLabel()
        self._info_tags_lbl.setWordWrap(True)
        self._info_tags_lbl.setVisible(False)
        left_layout.addWidget(self._info_tags_lbl)

        splitter.addWidget(left)

        # ---- Centre: search bar + table ----
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Title, creator or tag…")
        self._search_edit.textChanged.connect(lambda _: self._search_timer.start())
        search_row.addWidget(self._search_edit)
        centre_layout.addLayout(search_row)

        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(_HEADERS)
        self._model.itemChanged.connect(self._on_item_changed)

        self._proxy = _BrowserProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(-1)  # search all columns

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.horizontalHeader().setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(COL_PATH,  QHeaderView.ResizeMode.Interactive)
        self._table.setColumnHidden(COL_THUMB, True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setItemDelegateForColumn(COL_SOURCE,        SourceDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TAGS,          TagDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SCULPTOR,      SculptorDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_COLLECTION,    CollectionDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SUBCOLLECTION, SubcollectionDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_CATEGORY,      CategoryDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_RELEASE,       ReleaseDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SCALE,         ScaleDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_PRESUPPORTED,  PresupportsDelegate(self._table))
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        header = self._table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_context_menu)
        header.setSectionsMovable(True)
        header.sectionMoved.connect(self._save_header_state)
        header.sectionResized.connect(self._save_header_state)
        self._restore_header_state()
        self._table.selectionModel().currentChanged.connect(self._on_current_changed)
        self._table.doubleClicked.connect(self._on_double_click)
        QShortcut(QKeySequence.StandardKey.SelectAll, self._table, self._table.selectAll)
        QShortcut(QKeySequence("E"), self._table, self._edit_selected)
        centre_layout.addWidget(self._table)

        splitter.addWidget(centre)

        # ---- Right: thumbnail sidebar + creator info panel ----
        right = QWidget()
        self._right_pane = right
        right.setMinimumWidth(160)
        right_outer = QVBoxLayout(right)
        right_outer.setContentsMargins(4, 0, 0, 0)

        right_vsplit = QSplitter(Qt.Orientation.Vertical)
        right_vsplit.setHandleWidth(6)
        self._right_vsplit = right_vsplit

        # Top: images
        img_widget = QWidget()
        img_layout = QVBoxLayout(img_widget)
        img_layout.setContentsMargins(0, 0, 0, 0)
        img_layout.addWidget(QLabel("Images"))

        self._thumb_content = QWidget()
        self._thumb_vlayout = QVBoxLayout(self._thumb_content)
        self._thumb_vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._thumb_vlayout.setSpacing(6)

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setWidget(self._thumb_content)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_scroll.installEventFilter(self)
        img_layout.addWidget(self._thumb_scroll)
        right_vsplit.addWidget(img_widget)

        # Bottom: creator info panel (hidden until a model with creator info is selected)
        self._creator_panel = QFrame()
        self._creator_panel.setFrameShape(QFrame.Shape.StyledPanel)
        creator_layout = QVBoxLayout(self._creator_panel)
        creator_layout.setContentsMargins(6, 6, 6, 6)
        creator_layout.setSpacing(4)
        self._creator_name_lbl = QLabel()
        self._creator_name_lbl.setWordWrap(True)
        _cfont = self._creator_name_lbl.font()
        _cfont.setBold(True)
        self._creator_name_lbl.setFont(_cfont)
        creator_layout.addWidget(self._creator_name_lbl)
        self._creator_details_lbl = QLabel()
        self._creator_details_lbl.setWordWrap(True)
        self._creator_details_lbl.setOpenExternalLinks(True)
        self._creator_details_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._creator_details_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self._creator_details_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        creator_layout.addWidget(self._creator_details_lbl)
        creator_layout.addStretch()
        self._creator_panel.setVisible(False)
        right_vsplit.addWidget(self._creator_panel)

        right_vsplit.setStretchFactor(0, 3)
        right_vsplit.setStretchFactor(1, 1)
        right_outer.addWidget(right_vsplit)

        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setHandleWidth(6)
        self._thumb_splitter = splitter

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Reload all data from the database."""
        self._populate_filter_tree()
        self._apply_checked_filters()
        self._clear_thumbnails()

    def image_pane_visible(self) -> bool:
        return self._right_pane.isVisible()

    def toggle_image_pane(self) -> bool:
        """Show or hide the right thumbnail pane. Returns the new visibility state."""
        visible = not self._right_pane.isVisible()
        if visible:
            sizes = self._thumb_splitter.sizes()
            saved = getattr(self, "_saved_right_width", 220)
            total = sum(sizes)
            self._thumb_splitter.setSizes([sizes[0], total - sizes[0] - saved, saved])
        else:
            sizes = self._thumb_splitter.sizes()
            self._saved_right_width = sizes[2] or 220
            self._thumb_splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
        self._right_pane.setVisible(visible)
        return visible

    # ------------------------------------------------------------------
    def _populate_filter_tree(self) -> None:
        """Rebuild the left-panel filter tree from the current DB contents."""
        # Save checked items and expanded top-level sections so they survive the rebuild.
        # expanded_sections is None on the very first call (tree is empty) → default collapsed.
        checked_data:  set = set()
        excluded_data: set = set()
        expanded_sections: set[str] | None = None

        root_item = self._filter_tree.invisibleRootItem()
        if root_item.childCount() > 0:
            expanded_sections = set()
            def _collect_state(item: QTreeWidgetItem):
                d = item.data(0, Qt.ItemDataRole.UserRole)
                if d:
                    state = item.checkState(0)
                    if state == Qt.CheckState.Checked:
                        checked_data.add(d)
                    elif state == Qt.CheckState.PartiallyChecked:
                        excluded_data.add(d)
                if item.childCount() and item.isExpanded():
                    expanded_sections.add(item.text(0))
                for i in range(item.childCount()):
                    _collect_state(item.child(i))
            for i in range(root_item.childCount()):
                _collect_state(root_item.child(i))

        self._filter_tree.blockSignals(True)
        self._filter_tree.clear()

        from app.ui.config_dialog import get_color_scheme as _get_cs
        _v = "light" if "light" in _get_cs().lower() else "dark"

        def _icon(name: str):
            p = _ICONS_DIR / f"{name}_{_v}.svg"
            return QIcon(str(p)) if p.exists() else QIcon()

        _icon_author      = _icon("icon_author")
        _icon_tag         = _icon("icon_tag")
        _icon_type        = _icon("icon_type")
        _icon_path        = _icon("icon_path")
        _icon_date        = _icon("icon_date")
        _icon_sculptor    = _icon("icon_sculptor")
        _icon_category    = _icon("icon_category")
        _icon_subcategory = _icon("icon_subcategory")
        _icon_release     = _icon("icon_release")
        _icon_scale       = _icon("icon_scale")
        _icon_presupports = _icon("icon_presupports")
        _icon_warnings    = _icon("icon_warnings")

        _CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable

        def _make_leaf(label: str, data) -> QTreeWidgetItem:
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            item.setFlags(_CHECKABLE)
            if data in checked_data:
                state = Qt.CheckState.Checked
            elif data in excluded_data:
                state = Qt.CheckState.PartiallyChecked
            else:
                state = Qt.CheckState.Unchecked
            item.setCheckState(0, state)
            return item

        header = self._table.horizontalHeader()
        show_author       = not header.isSectionHidden(COL_CREATOR)
        show_tags         = not header.isSectionHidden(COL_TAGS)
        show_source       = not header.isSectionHidden(COL_SOURCE)
        show_date         = not header.isSectionHidden(COL_DATE)
        show_sculptor     = not header.isSectionHidden(COL_SCULPTOR)
        show_collection   = not header.isSectionHidden(COL_COLLECTION)
        show_subcollection = not header.isSectionHidden(COL_SUBCOLLECTION)
        show_category     = not header.isSectionHidden(COL_CATEGORY)
        show_release      = not header.isSectionHidden(COL_RELEASE)
        show_scale        = not header.isSectionHidden(COL_SCALE)
        show_presupports  = not header.isSectionHidden(COL_PRESUPPORTED)
        show_warnings     = not header.isSectionHidden(COL_WARNING)

        session = get_session()
        try:
            if show_author:
                authors_item = _make_leaf("Creators", ("group", "creator"))
                authors_item.setIcon(0, _icon_author)
                self._filter_tree.addTopLevelItem(authors_item)
                for (creator,) in session.query(STLModel.creator).distinct().order_by(STLModel.creator).all():
                    if creator:
                        authors_item.addChild(_make_leaf(creator, ("creator", creator)))

            if show_tags:
                tags_item = _make_leaf("Tags", ("group", "tag"))
                tags_item.setIcon(0, _icon_tag)
                self._filter_tree.addTopLevelItem(tags_item)
                for tag in session.query(Tag).order_by(Tag.name).all():
                    tags_item.addChild(_make_leaf(tag.name, ("tag", tag.name)))

            if show_source:
                types_item = _make_leaf("Type", ("group", "source"))
                types_item.setIcon(0, _icon_type)
                self._filter_tree.addTopLevelItem(types_item)
                for (t,) in session.query(STLModel.source).distinct().order_by(STLModel.source).all():
                    if t:
                        types_item.addChild(_make_leaf(t, ("source", t)))

            if show_date:
                _MONTH_NAMES = [
                    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                ]
                dates_item = _make_leaf("Date", ("group", "date"))
                dates_item.setIcon(0, _icon_date)
                self._filter_tree.addTopLevelItem(dates_item)
                rows = (
                    session.query(STLModel.release_year, STLModel.release_month)
                    .filter(STLModel.release_year.isnot(None))
                    .distinct()
                    .order_by(STLModel.release_year.desc(), STLModel.release_month)
                    .all()
                )
                year_items: dict[int, QTreeWidgetItem] = {}
                for year, month in rows:
                    if year not in year_items:
                        yi = _make_leaf(str(year), ("date_year", year))
                        dates_item.addChild(yi)
                        year_items[year] = yi
                    if month is not None:
                        label = _MONTH_NAMES[month] if 1 <= month <= 12 else str(month)
                        year_items[year].addChild(
                            _make_leaf(label, ("date_month", (year, month)))
                        )

            if show_sculptor:
                sculptor_item = _make_leaf("Sculptor", ("group", "sculptor"))
                sculptor_item.setIcon(0, _icon_sculptor)
                self._filter_tree.addTopLevelItem(sculptor_item)
                for sc in session.query(Sculptor).order_by(Sculptor.name).all():
                    sculptor_item.addChild(_make_leaf(sc.name, ("sculptor_id", sc.id)))

            if show_collection:
                collection_item = _make_leaf("Collection", ("group", "collection"))
                collection_item.setIcon(0, _icon_category)
                self._filter_tree.addTopLevelItem(collection_item)
                for col in session.query(Collection).order_by(Collection.name).all():
                    collection_item.addChild(_make_leaf(col.name, ("collection_id", col.id)))

            if show_subcollection:
                subcol_section = _make_leaf("Subcollection", ("group", "subcollection"))
                subcol_section.setIcon(0, _icon_subcategory)
                self._filter_tree.addTopLevelItem(subcol_section)
                for col in session.query(Collection).order_by(Collection.name).all():
                    subs = (session.query(Subcollection)
                            .filter_by(collection_id=col.id)
                            .order_by(Subcollection.name).all())
                    if not subs:
                        continue
                    col_node = QTreeWidgetItem([col.name])
                    col_node.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    subcol_section.addChild(col_node)
                    for sub in subs:
                        col_node.addChild(_make_leaf(sub.name, ("subcollection_id", sub.id)))

            if show_category:
                category_item = _make_leaf("Category", ("group", "category"))
                category_item.setIcon(0, _icon_category)
                self._filter_tree.addTopLevelItem(category_item)
                for cat in session.query(Category).order_by(Category.name).all():
                    category_item.addChild(_make_leaf(cat.name, ("category_id", cat.id)))

            if show_release:
                release_item = _make_leaf("Release", ("group", "release"))
                release_item.setIcon(0, _icon_release)
                self._filter_tree.addTopLevelItem(release_item)
                for rel in session.query(Release).order_by(Release.name).all():
                    release_item.addChild(_make_leaf(rel.name, ("release_id", rel.id)))

            if show_scale:
                scale_item = _make_leaf("Scale", ("group", "scale"))
                scale_item.setIcon(0, _icon_scale)
                self._filter_tree.addTopLevelItem(scale_item)
                for sc in session.query(Scale).order_by(Scale.name).all():
                    scale_item.addChild(_make_leaf(sc.name, ("scale_id", sc.id)))

            if show_presupports:
                presup_item = _make_leaf("Presupports", ("group", "presupports"))
                presup_item.setIcon(0, _icon_presupports)
                self._filter_tree.addTopLevelItem(presup_item)
                for (val,) in (session.query(STLModel.presupports)
                               .filter(STLModel.presupports.isnot(None))
                               .distinct().order_by(STLModel.presupports).all()):
                    if val:
                        presup_item.addChild(_make_leaf(val, ("presupports", val)))

            if show_warnings:
                _WARNING_TYPES = [
                    "presupported stl",
                    "Archive without images",
                    "Archive inside archive",
                    "Archive with supports",
                ]
                warn_item = _make_leaf("Warnings", ("group", "warning"))
                warn_item.setIcon(0, _icon_warnings)
                self._filter_tree.addTopLevelItem(warn_item)
                for wtype in _WARNING_TYPES:
                    warn_item.addChild(_make_leaf(wtype, ("warning", wtype)))

            paths_item = _make_leaf("Path", ("group", "path"))
            paths_item.setIcon(0, _icon_path)
            self._filter_tree.addTopLevelItem(paths_item)
            dirs: set[str] = set()
            for (ap,) in session.query(STLModel.archive_path).distinct().all():
                if ap:
                    # Use as_posix() so the filter tree always uses forward slashes,
                    # regardless of OS — the LIKE query normalises the DB column too.
                    dirs.add(FSPath(ap).parent.as_posix())
            new_dirs = frozenset(dirs)
            if new_dirs != self._cached_path_dirs:
                self._cached_path_dirs = new_dirs
                self._cached_sorted_dirs = sorted(dirs)
            self._build_path_tree(paths_item, self._cached_sorted_dirs, checked_data, excluded_data)
        finally:
            session.close()

        self._filter_tree.blockSignals(False)

        # Restore expansion: first run → all collapsed; subsequent runs → restore state.
        if expanded_sections is not None:
            root_item = self._filter_tree.invisibleRootItem()
            for i in range(root_item.childCount()):
                section = root_item.child(i)
                section.setExpanded(section.text(0) in expanded_sections)

    @staticmethod
    def _build_path_tree(root_item: QTreeWidgetItem, dirs: list[str],
                         checked_data: set | None = None,
                         excluded_data: set | None = None) -> None:
        """Build a nested tree of directory segments under *root_item*."""
        _CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        node_map: dict[str, QTreeWidgetItem] = {}

        for d in dirs:
            parts = FSPath(d).parts
            for depth in range(1, len(parts) + 1):
                full = FSPath(*parts[:depth]).as_posix()
                if full in node_map:
                    continue
                label = parts[depth - 1]
                parent = node_map.get(FSPath(*parts[:depth - 1]).as_posix()) if depth > 1 else root_item
                if parent is None:
                    parent = root_item
                data = ("path", full)
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, data)
                item.setFlags(_CHECKABLE)
                if checked_data and data in checked_data:
                    state = Qt.CheckState.Checked
                elif excluded_data and data in excluded_data:
                    state = Qt.CheckState.PartiallyChecked
                else:
                    state = Qt.CheckState.Unchecked
                item.setCheckState(0, state)
                parent.addChild(item)
                node_map[full] = item

    def show_model_ids(self, ids: list[int]) -> None:
        """Show only the models with the given IDs, ignoring all sidebar filters."""
        self._load_table(id_filters=ids)
        if self._proxy.rowCount() > 0:
            self._table.setCurrentIndex(self._proxy.index(0, 0))

    def show_duplicate_pairs(self, pairs: list) -> None:
        """Show duplicate pairs sorted by pair and color-coded with alternating colors."""
        all_ids = list({pid for pair in pairs for pid in pair})
        self._load_table(id_filters=all_ids, duplicate_pairs=pairs)
        if self._proxy.rowCount() > 0:
            self._table.setCurrentIndex(self._proxy.index(0, 0))

    def _load_table(
        self,
        author_filters:        list[str] | None = None,
        author_exc_filters:    list[str] | None = None,
        tag_filters:           list[str] | None = None,
        tag_exc_filters:       list[str] | None = None,
        type_filters:          list[str] | None = None,
        type_exc_filters:      list[str] | None = None,
        path_filters:          list[str] | None = None,
        path_exc_filters:      list[str] | None = None,
        date_filters:          list[tuple[int, int | None]] | None = None,
        date_exc_filters:      list[tuple[int, int | None]] | None = None,
        sculptor_filters:      list[str] | None = None,
        sculptor_exc_filters:  list[str] | None = None,
        collection_filters:    list[str] | None = None,
        collection_exc_filters: list[str] | None = None,
        subcollection_filters: list[str] | None = None,
        subcollection_exc_filters: list[str] | None = None,
        category_filters:      list[str] | None = None,
        category_exc_filters:  list[str] | None = None,
        release_filters:       list[str] | None = None,
        release_exc_filters:   list[str] | None = None,
        scale_filters:         list[str] | None = None,
        scale_exc_filters:     list[str] | None = None,
        presupports_filters:   list[str] | None = None,
        presupports_exc_filters: list[str] | None = None,
        warning_filters:       list[str] | None = None,
        warning_exc_filters:   list[str] | None = None,
        has_value_groups:      list[str] | None = None,
        no_value_groups:       list[str] | None = None,
        id_filters:            list[int] | None = None,
        duplicate_pairs:       list[list[int]] | None = None,
    ) -> None:
        """Reload the table with optional include/exclude filter lists.

        Within each category the values are OR-ed; across categories they are AND-ed.
        *_exc_filters* variants exclude rows that match those values.
        *date_filters* / *date_exc_filters* are lists of (year, month_or_None) tuples.
        """
        self._loading = True
        self._model.removeRows(0, self._model.rowCount())
        session = get_session()

        def _date_clauses(pairs):
            conds = []
            for year, month in pairs:
                if month is None:
                    conds.append(STLModel.release_year == year)
                else:
                    conds.append(and_(STLModel.release_year == year,
                                      STLModel.release_month == month))
            return conds

        try:
            q = session.query(STLModel).options(
                selectinload(STLModel.tags),
                selectinload(STLModel.sculptor),
                selectinload(STLModel.collection),
                selectinload(STLModel.subcollection),
                selectinload(STLModel.category),
                selectinload(STLModel.release),
                selectinload(STLModel.scale),
            )
            # --- explicit ID set (from duplicate finder etc.) ---
            if id_filters:
                q = q.filter(STLModel.id.in_(id_filters))
            # --- include filters (OR within category, AND across) ---
            if author_filters:
                q = q.filter(STLModel.creator.in_(author_filters))
            if type_filters:
                q = q.filter(STLModel.source.in_(type_filters))
            if tag_filters:
                q = q.filter(STLModel.tags.any(Tag.name.in_(tag_filters)))
            if path_filters:
                # Normalise the DB column to forward slashes so the filter works
                # on Windows where archive_path is stored with backslashes.
                _norm = func.replace(STLModel.archive_path, "\\", "/")
                q = q.filter(or_(*(
                    _norm.like(p.rstrip("/") + "/%")
                    for p in path_filters
                )))
            if date_filters:
                q = q.filter(or_(*_date_clauses(date_filters)))
            if sculptor_filters:
                q = q.filter(STLModel.sculptor_id.in_(sculptor_filters))
            if collection_filters:
                q = q.filter(STLModel.collection_id.in_(collection_filters))
            if subcollection_filters:
                q = q.filter(STLModel.subcollection_id.in_(subcollection_filters))
            if category_filters:
                q = q.filter(STLModel.category_id.in_(category_filters))
            if release_filters:
                q = q.filter(STLModel.release_id.in_(release_filters))
            if scale_filters:
                q = q.filter(STLModel.scale_id.in_(scale_filters))
            if presupports_filters:
                q = q.filter(STLModel.presupports.in_(presupports_filters))
            if warning_filters:
                q = q.filter(or_(*(STLModel.warning.contains(w) for w in warning_filters)))
            # --- exclude filters (NOT, AND across) ---
            if author_exc_filters:
                q = q.filter(~STLModel.creator.in_(author_exc_filters))
            if type_exc_filters:
                q = q.filter(~STLModel.source.in_(type_exc_filters))
            if tag_exc_filters:
                q = q.filter(~STLModel.tags.any(Tag.name.in_(tag_exc_filters)))
            if path_exc_filters:
                _norm = func.replace(STLModel.archive_path, "\\", "/")
                for p in path_exc_filters:
                    q = q.filter(~_norm.like(p.rstrip("/") + "/%"))
            if date_exc_filters:
                q = q.filter(~or_(*_date_clauses(date_exc_filters)))
            if sculptor_exc_filters:
                q = q.filter(~STLModel.sculptor_id.in_(sculptor_exc_filters))
            if collection_exc_filters:
                q = q.filter(~STLModel.collection_id.in_(collection_exc_filters))
            if subcollection_exc_filters:
                q = q.filter(~STLModel.subcollection_id.in_(subcollection_exc_filters))
            if category_exc_filters:
                q = q.filter(~STLModel.category_id.in_(category_exc_filters))
            if release_exc_filters:
                q = q.filter(~STLModel.release_id.in_(release_exc_filters))
            if scale_exc_filters:
                q = q.filter(~STLModel.scale_id.in_(scale_exc_filters))
            if presupports_exc_filters:
                q = q.filter(~STLModel.presupports.in_(presupports_exc_filters))
            if warning_exc_filters:
                q = q.filter(and_(*(~STLModel.warning.contains(w) for w in warning_exc_filters)))

            # Group-level "has/no value" filters — applied when the group header is checked.
            _group_has_value = {
                "creator":       lambda: and_(STLModel.creator.isnot(None),        STLModel.creator != ""),
                "tag":           lambda: STLModel.tags.any(),
                "source":        lambda: and_(STLModel.source.isnot(None),         STLModel.source != ""),
                "date":          lambda: STLModel.release_year.isnot(None),
                "sculptor":      lambda: STLModel.sculptor_id.isnot(None),
                "collection":    lambda: STLModel.collection_id.isnot(None),
                "subcollection": lambda: STLModel.subcollection_id.isnot(None),
                "category":      lambda: STLModel.category_id.isnot(None),
                "release":       lambda: STLModel.release_id.isnot(None),
                "scale":         lambda: STLModel.scale_id.isnot(None),
                "presupports":   lambda: and_(STLModel.presupports.isnot(None),    STLModel.presupports != ""),
                "warning":       lambda: and_(STLModel.warning.isnot(None),        STLModel.warning != ""),
                "path":          lambda: and_(STLModel.archive_path.isnot(None),   STLModel.archive_path != ""),
            }
            if has_value_groups:
                for _key in has_value_groups:
                    _cond = _group_has_value.get(_key)
                    if _cond:
                        q = q.filter(_cond())
            if no_value_groups:
                for _key in no_value_groups:
                    _cond = _group_has_value.get(_key)
                    if _cond:
                        q = q.filter(~_cond())

            if duplicate_pairs is not None:
                from app.ui.config_dialog import get_pair_colors
                _ca, _cb = get_pair_colors()
                _pair_colors = [QColor(_ca), QColor(_cb)]
                _seen: set[int] = set()
                _pair_for_id: dict[int, int] = {}
                _ordered_pos: dict[int, int] = {}
                _pos = 0
                for _pi, _pair in enumerate(duplicate_pairs):
                    for _pid in _pair:
                        if _pid not in _seen:
                            _seen.add(_pid)
                            _ordered_pos[_pid] = _pos
                            _pos += 1
                            _pair_for_id[_pid] = _pi
                _stls = q.all()
                _stls.sort(key=lambda m: _ordered_pos.get(m.id, len(_ordered_pos)))
            else:
                _stls = q.order_by(STLModel.title).all()
                _pair_for_id = {}

            for stl in _stls:
                thumb_item = QStandardItem()
                thumb_item.setData(stl.id, MODEL_ID_ROLE)
                thumb_item.setEditable(False)

                tags_str = ", ".join(t.name for t in stl.tags)
                path_item = QStandardItem(stl.archive_path or "")
                path_item.setEditable(False)
                seq_str = str(stl.sequence) if stl.sequence is not None else ""
                added_str = stl.created_at.strftime("%Y-%m-%d %H:%M") if stl.created_at else ""
                added_item = QStandardItem(added_str)
                added_item.setEditable(False)
                warning_item = QStandardItem("⚠" if stl.warning else "")
                warning_item.setEditable(False)
                warning_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if stl.warning:
                    warning_item.setData(stl.warning, Qt.ItemDataRole.ToolTipRole)
                    warning_item.setBackground(QColor("#4a4a4a"))
                    warning_item.setForeground(QColor("#FFD700"))
                    _warn_font = QFont()
                    _warn_font.setPointSize(16)
                    _warn_font.setBold(True)
                    warning_item.setFont(_warn_font)
                try:
                    raw_size = FSPath(stl.archive_path).stat().st_size
                    size_display = _fmt_size(raw_size)
                except OSError:
                    raw_size = -1
                    size_display = ""
                size_item = QStandardItem(size_display)
                size_item.setData(raw_size, Qt.ItemDataRole.UserRole)
                size_item.setEditable(False)

                # Build row items. For numeric sorting, store numeric values under UserRole
                seq_item = QStandardItem(seq_str)
                seq_item.setEditable(True)
                # store numeric sequence under UserRole so proxy can sort numerically
                seq_item.setData(stl.sequence if stl.sequence is not None else None, Qt.ItemDataRole.UserRole)

                row = [
                    thumb_item,
                    QStandardItem(stl.title or ""),
                    QStandardItem(stl.creator or ""),
                    QStandardItem(stl.source or ""),
                    QStandardItem(format_release_date(stl.release_year, stl.release_month)),
                    QStandardItem(tags_str),
                    QStandardItem(stl.sculptor.name    if stl.sculptor    else ""),
                    QStandardItem(stl.collection.name  if stl.collection  else ""),
                    QStandardItem(stl.subcollection.name if stl.subcollection else ""),
                    seq_item,
                    QStandardItem(stl.release.name if stl.release else ""),
                    path_item,
                    QStandardItem(stl.scale.name if stl.scale else ""),
                    QStandardItem(stl.presupports or ""),
                    added_item,
                    warning_item,
                    size_item,
                    QStandardItem(stl.category.name if stl.category else ""),
                ]
                if _pair_for_id:
                    _pi = _pair_for_id.get(stl.id)
                    if _pi is not None:
                        _color = _pair_colors[_pi % 2]
                        for _it in row:
                            _it.setBackground(_color)
                self._model.appendRow(row)
        finally:
            session.close()
            self._loading = False
        self.model_count_changed.emit(self._model.rowCount())

    # ------------------------------------------------------------------
    def _on_item_changed(self, item: QStandardItem) -> None:
        """Debounce cell edits; actual DB write is deferred to *_flush_pending_edits*."""
        if self._loading:
            return
        col = item.column()
        if col in (COL_THUMB, COL_ADDED, COL_WARNING, COL_FILESIZE):
            return
        # Debounce: store the latest item for this cell and (re)start the timer.
        self._pending_edits[(item.row(), col)] = item
        self._edit_timer.start()

    def _flush_pending_edits(self) -> None:
        """Write all debounced cell edits to the DB in a single pass."""
        items = list(self._pending_edits.values())
        self._pending_edits.clear()
        for item in items:
            self._write_item(item)

    def _write_item(self, item: QStandardItem) -> None:
        """Persist a single edited table cell to the database."""
        col = item.column()
        model_id = self._model.item(item.row(), COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is None:
            return
        value = item.text().strip() or None
        with session_scope() as session:
            stl: STLModel = session.get(STLModel, model_id)
            if stl is None:
                return
            if col == COL_TITLE:
                stl.title = value
            elif col == COL_CREATOR:
                stl.creator = value
            elif col == COL_SOURCE:
                stl.source = value
            elif col == COL_DATE:
                _year, _month = parse_release_date(value or "")
                stl.release_year  = _year
                stl.release_month = _month
            elif col == COL_TAGS:
                stl.tags.clear()
                for name in [t.strip().lower() for t in (value or "").split(",") if t.strip()]:
                    tag = session.query(Tag).filter_by(name=name).first()
                    if tag is None:
                        tag = Tag(name=name)
                        session.add(tag)
                        session.flush()
                    stl.tags.append(tag)
            elif col == COL_SCULPTOR:
                if value:
                    sc = session.query(Sculptor).filter_by(name=value).first()
                    if sc is None:
                        sc = Sculptor(name=value)
                        session.add(sc)
                        session.flush()
                    stl.sculptor = sc
                else:
                    stl.sculptor = None
            elif col == COL_COLLECTION:
                if value:
                    col_obj = session.query(Collection).filter_by(name=value).first()
                    if col_obj is None:
                        col_obj = Collection(name=value)
                        session.add(col_obj)
                        session.flush()
                    stl.collection = col_obj
                else:
                    stl.collection = None
                    stl.subcollection = None
            elif col == COL_SUBCOLLECTION:
                if value:
                    q = session.query(Subcollection).filter_by(name=value)
                    if stl.collection_id:
                        q = q.filter_by(collection_id=stl.collection_id)
                    sc = q.first()
                    if sc is None and stl.collection_id:
                        sc = Subcollection(name=value, collection_id=stl.collection_id)
                        session.add(sc)
                        session.flush()
                    if sc:
                        stl.subcollection = sc
                else:
                    stl.subcollection = None
            elif col == COL_CATEGORY:
                if value:
                    cat = session.query(Category).filter_by(name=value).first()
                    if cat is None:
                        cat = Category(name=value)
                        session.add(cat)
                        session.flush()
                    stl.category = cat
                else:
                    stl.category = None
            elif col == COL_SEQUENCE:
                try:
                    stl.sequence = int(value) if value else None
                except ValueError:
                    pass
            elif col == COL_RELEASE:
                if value:
                    rel = session.query(Release).filter_by(name=value).first()
                    if rel is None:
                        rel = Release(name=value)
                        session.add(rel)
                        session.flush()
                    stl.release = rel
                else:
                    stl.release = None
            elif col == COL_SCALE:
                if value:
                    sc = session.query(Scale).filter_by(name=value).first()
                    if sc is None:
                        sc = Scale(name=value)
                        session.add(sc)
                        session.flush()
                    stl.scale = sc
                else:
                    stl.scale = None
            elif col == COL_PRESUPPORTED:
                stl.presupports = value.strip() or None

            # Update per-folder METADATA.7z sidecar with the saved change
            if stl.archive_path:
                from pathlib import Path as _Path
                from app.crawler.folder_metadata import export_folder_metadata
                from app.utils.thumbnailer import get_thumbnail_base_dir
                from app.ui.config_dialog import get_metadata_thumbnails_enabled, get_metadata_sidecar_enabled
                thumb_base = get_thumbnail_base_dir()
                if thumb_base is not None and get_metadata_sidecar_enabled():
                    try:
                        export_folder_metadata(
                            _Path(stl.archive_path).parent,
                            session, thumb_base,
                            include_thumbnails=get_metadata_thumbnails_enabled(),
                        )
                    except Exception:
                        pass  # best-effort; never block the edit

    # ------------------------------------------------------------------
    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)

    def _on_filter_item_changed(self, item: QTreeWidgetItem, _col: int) -> None:
        """Called when a checkbox in the filter tree changes state."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return

        # Complete the tristate cycle for checkbox-click events.
        # mousePressEvent stores the pre-click state; here we map it to the next state.
        prev = self._filter_tree._pre_click_states.pop(id(item), None)
        if prev is not None:
            current = item.checkState(0)
            if prev == Qt.CheckState.Unchecked:
                next_state = Qt.CheckState.Checked
            elif prev == Qt.CheckState.Checked:
                next_state = Qt.CheckState.PartiallyChecked
            else:  # PartiallyChecked → reset
                next_state = Qt.CheckState.Unchecked
            if next_state != current:
                self._filter_tree.blockSignals(True)
                item.setCheckState(0, next_state)
                self._filter_tree.blockSignals(False)

        # Propagate the final state down to all children (path, date, etc.).
        # Group headers are excluded because their children have different semantics.
        kind = data[0] if isinstance(data, tuple) else None
        if kind != "group" and item.childCount() > 0:
            self._filter_tree.blockSignals(True)
            self._propagate_state_to_children(item, item.checkState(0))
            self._filter_tree.blockSignals(False)

        self._apply_checked_filters()

    @staticmethod
    def _propagate_state_to_children(item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        """Recursively set all checkable descendants of *item* to *state*."""
        for i in range(item.childCount()):
            child = item.child(i)
            if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                child.setCheckState(0, state)
            BrowserTab._propagate_state_to_children(child, state)

    def _apply_checked_filters(self) -> None:
        """Collect include (Checked) and exclude (PartiallyChecked) filter items and reload."""
        author_inc:          list[str]   = []; author_exc:          list[str]   = []
        tag_inc:             list[str]   = []; tag_exc:             list[str]   = []
        type_inc:            list[str]   = []; type_exc:            list[str]   = []
        path_inc:            list[str]   = []; path_exc:            list[str]   = []
        date_inc:            list[tuple] = []; date_exc:            list[tuple] = []
        sculptor_inc:        list[int]   = []; sculptor_exc:        list[int]   = []
        collection_inc:      list[int]   = []; collection_exc:      list[int]   = []
        subcollection_inc:   list[int]   = []; subcollection_exc:   list[int]   = []
        category_inc:        list[int]   = []; category_exc:        list[int]   = []
        release_inc:         list[int]   = []; release_exc:         list[int]   = []
        scale_inc:           list[int]   = []; scale_exc:           list[int]   = []
        presupports_inc:     list[str]   = []; presupports_exc:     list[str]   = []
        warning_inc:         list[str]   = []; warning_exc:         list[str]   = []
        has_value_groups:    set[str]    = set()
        no_value_groups:     set[str]    = set()

        _buckets = {
            "creator":           (author_inc,       author_exc),
            "tag":               (tag_inc,          tag_exc),
            "source":            (type_inc,         type_exc),
            "path":              (path_inc,         path_exc),
            "sculptor_id":       (sculptor_inc,     sculptor_exc),
            "collection_id":     (collection_inc,   collection_exc),
            "subcollection_id":  (subcollection_inc, subcollection_exc),
            "category_id":       (category_inc,     category_exc),
            "release_id":        (release_inc,      release_exc),
            "scale_id":          (scale_inc,        scale_exc),
            "presupports":       (presupports_inc,  presupports_exc),
            "warning":           (warning_inc,      warning_exc),
        }

        def _collect(item: QTreeWidgetItem):
            data  = item.data(0, Qt.ItemDataRole.UserRole)
            state = item.checkState(0)
            if data and state in (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked):
                is_inc = state == Qt.CheckState.Checked
                kind, value = data
                if kind == "group":
                    (has_value_groups if is_inc else no_value_groups).add(value)
                elif kind in ("date_year", "date_month"):
                    target = (date_inc if is_inc else date_exc)
                    target.append((value, None) if kind == "date_year" else value)
                elif kind in _buckets:
                    _buckets[kind][0 if is_inc else 1].append(value)
            for i in range(item.childCount()):
                _collect(item.child(i))

        root = self._filter_tree.invisibleRootItem()
        for i in range(root.childCount()):
            _collect(root.child(i))

        self._load_table(
            author_filters=author_inc or None,               author_exc_filters=author_exc or None,
            tag_filters=tag_inc or None,                     tag_exc_filters=tag_exc or None,
            type_filters=type_inc or None,                   type_exc_filters=type_exc or None,
            path_filters=path_inc or None,                   path_exc_filters=path_exc or None,
            date_filters=date_inc or None,                   date_exc_filters=date_exc or None,
            sculptor_filters=sculptor_inc or None,           sculptor_exc_filters=sculptor_exc or None,
            collection_filters=collection_inc or None,       collection_exc_filters=collection_exc or None,
            subcollection_filters=subcollection_inc or None, subcollection_exc_filters=subcollection_exc or None,
            category_filters=category_inc or None,           category_exc_filters=category_exc or None,
            release_filters=release_inc or None,             release_exc_filters=release_exc or None,
            scale_filters=scale_inc or None,                 scale_exc_filters=scale_exc or None,
            presupports_filters=presupports_inc or None,     presupports_exc_filters=presupports_exc or None,
            warning_filters=warning_inc or None,             warning_exc_filters=warning_exc or None,
            has_value_groups=has_value_groups or None,
            no_value_groups=no_value_groups or None,
        )

    def _clear_filters(self) -> None:
        """Uncheck all filter items and reload the full table."""
        self._filter_tree.blockSignals(True)
        def _uncheck(item: QTreeWidgetItem):
            if item.data(0, Qt.ItemDataRole.UserRole) is not None:
                item.setCheckState(0, Qt.CheckState.Unchecked)
            for i in range(item.childCount()):
                _uncheck(item.child(i))
        root = self._filter_tree.invisibleRootItem()
        for i in range(root.childCount()):
            _uncheck(root.child(i))
        self._filter_tree.blockSignals(False)
        self._load_table()

    def _on_current_changed(self, current, previous) -> None:
        if not current.isValid():
            self._clear_thumbnails()
            self._info_author_lbl.setVisible(False)
            self._info_tags_lbl.setVisible(False)
            self._creator_panel.setVisible(False)
            return
        src_index = self._proxy.mapToSource(current)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is not None:
            self._show_thumbnails(model_id)
            self._update_info_panel(model_id)
            self._update_creator_panel(model_id)

    def _update_info_panel(self, model_id: int) -> None:
        """Refresh the creator/tag info labels below the filter tree."""
        session = get_session()
        try:
            stl: STLModel = session.get(STLModel, model_id)
            if stl is None:
                return
            if stl.creator:
                self._info_author_lbl.setText(f"<b>Creator:</b> {stl.creator}")
                self._info_author_lbl.setVisible(True)
            else:
                self._info_author_lbl.setVisible(False)
            if stl.tags:
                self._info_tags_lbl.setText("<b>Tags:</b> " + ", ".join(t.name for t in stl.tags))
                self._info_tags_lbl.setVisible(True)
            else:
                self._info_tags_lbl.setVisible(False)
        finally:
            session.close()

    def _update_creator_panel(self, model_id: int) -> None:
        """Load creator profile from DB and show it in the right-pane creator panel."""
        from app.database.models import Creator as CreatorModel
        session = get_session()
        try:
            stl: STLModel = session.get(STLModel, model_id)
            if stl is None or not stl.creator:
                self._creator_panel.setVisible(False)
                return
            creator_name = stl.creator
            c = session.query(CreatorModel).filter_by(name=creator_name).first()
            self._creator_name_lbl.setText(creator_name)
            if c:
                lines: list[str] = []
                if c.full_name:
                    lines.append(f"<b>Full name:</b> {c.full_name}")
                if c.patreon_url:
                    lines.append(f'<b>Patreon:</b> <a href="{c.patreon_url}">{c.patreon_url}</a>')
                if c.artstation_url:
                    lines.append(f'<b>ArtStation:</b> <a href="{c.artstation_url}">{c.artstation_url}</a>')
                if c.gumroad_url:
                    lines.append(f'<b>Gumroad:</b> <a href="{c.gumroad_url}">{c.gumroad_url}</a>')
                if c.myminifactory_url:
                    lines.append(f'<b>MyMiniFactory:</b> <a href="{c.myminifactory_url}">{c.myminifactory_url}</a>')
                self._creator_details_lbl.setText("<br>".join(lines))
            else:
                self._creator_details_lbl.setText("")
            self._creator_panel.setVisible(True)
        finally:
            session.close()

    def _open_creator_editor(self, creator_name: str) -> None:
        from app.ui.creator_editor_dialog import CreatorEditorDialog
        dlg = CreatorEditorDialog(creator_name, parent=self)
        if dlg.exec():
            ids = self._selected_model_ids()
            if len(ids) == 1:
                self._update_creator_panel(ids[0])

    def _on_double_click(self, index) -> None:
        if index.column() != COL_PATH:
            return  # let the cell editor handle it
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is not None:
            self._open_editor(model_id)

    def _edit_selected(self) -> None:
        """Open the single-model editor or mass editor depending on selection count."""
        ids = self._selected_model_ids()
        if len(ids) == 1:
            self._open_editor(ids[0])
        elif len(ids) > 1:
            self._open_mass_editor(ids)

    def _selected_model_ids(self) -> list[int]:
        rows = {self._proxy.mapToSource(idx).row()
                for idx in self._table.selectedIndexes()}
        return [
            self._model.item(r, COL_THUMB).data(MODEL_ID_ROLE)
            for r in sorted(rows)
            if self._model.item(r, COL_THUMB) is not None
        ]

    def _save_header_state(self) -> None:
        """Persist column widths, order, and visibility to QSettings."""
        settings = QSettings()
        settings.setValue("browser/header_state", self._table.horizontalHeader().saveState())
        settings.setValue("browser/header_column_count", self._model.columnCount())

    def _restore_header_state(self) -> None:
        """Restore previously saved column widths, order, and visibility."""
        settings = QSettings()
        state        = settings.value("browser/header_state")
        saved_count  = settings.value("browser/header_column_count", type=int)
        current_count = self._model.columnCount()

        if state and saved_count == current_count:
            self._table.horizontalHeader().restoreState(state)
            # Always keep COL_THUMB hidden after restoring
            self._table.setColumnHidden(COL_THUMB, True)
            # restoreState() preserves saved resize modes — re-assert Interactive so
            # columns that were previously Stretch remain manually resizable.
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(COL_PATH,  QHeaderView.ResizeMode.Interactive)
        else:
            # Column layout changed — discard stale state and use defaults
            settings.remove("browser/header_state")
            settings.remove("browser/header_column_count")
            self._table.setColumnWidth(COL_TITLE, 260)
            self._table.setColumnWidth(COL_PATH,  320)

    def _header_context_menu(self, pos) -> None:
        """Show a column-visibility toggle menu when the table header is right-clicked."""
        menu = QMenu(self)
        header = self._table.horizontalHeader()
        for col in range(1, self._model.columnCount()):  # col 0 (thumb) is always hidden
            label = _HEADERS[col]
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(col))
            action.setData(col)
        chosen = menu.exec(header.mapToGlobal(pos))
        if chosen is not None:
            col = chosen.data()
            header.setSectionHidden(col, not chosen.isChecked())
            self._save_header_state()
            if col in (COL_CREATOR, COL_TAGS, COL_SOURCE, COL_PATH, COL_DATE):
                self._populate_filter_tree()

    def _context_menu(self, pos) -> None:
        """Show the row-level context menu (edit, delete, thumbnails, sidecar, etc.)."""
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)

        selected_ids = self._selected_model_ids()

        style = QApplication.style()
        SP = style.StandardPixmap
        menu = QMenu(self)
        
        show_files_act = menu.addAction(style.standardIcon(SP.SP_FileDialogContentsView), "Show Files…")
        show_files_act.setVisible(len(selected_ids) == 1)
        menu.addSeparator()

        edit_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Model…")
        edit_act.setVisible(len(selected_ids) == 1)
        mass_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), f"Mass Edit {len(selected_ids)} model(s)…")
        mass_act.setVisible(len(selected_ids) > 1)
        menu.addSeparator()
        delete_act = menu.addAction(style.standardIcon(SP.SP_TrashIcon), f"Delete {len(selected_ids)} model(s) from DB…")
        
        from app.ui.config_dialog import get_allow_file_delete
        delete_file_act = None
        if get_allow_file_delete():
            delete_file_act = menu.addAction(style.standardIcon(SP.SP_DialogDiscardButton), f"Delete {len(selected_ids)} archive file(s) from disk…")

        add_tag_act = None
        edit_tags_act = None
        edit_collections_act = None
        edit_subcollections_act = None
        edit_categories_act = None
        edit_scales_act = None
        edit_creator_act = None
        src_col = self._proxy.mapToSource(index).column()
        if src_col == COL_CREATOR:
            menu.addSeparator()
            edit_creator_act = menu.addAction(
                style.standardIcon(SP.SP_FileDialogDetailedView),
                "Edit Creator Information…",
            )
        elif src_col == COL_TAGS:
            menu.addSeparator()
            add_tag_act = menu.addAction(style.standardIcon(SP.SP_FileDialogNewFolder), "Add Tag…")
            edit_tags_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Tags…")
        elif src_col == COL_COLLECTION:
            menu.addSeparator()
            edit_collections_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Collections…")
        elif src_col == COL_SUBCOLLECTION:
            menu.addSeparator()
            edit_subcollections_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Subcollections…")
        elif src_col == COL_CATEGORY:
            menu.addSeparator()
            edit_categories_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Categories…")
        elif src_col == COL_SCALE:
            menu.addSeparator()
            edit_scales_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Scales…")

        from app.ui.config_dialog import get_file_sorting_enabled
        sort_act = None
        if get_file_sorting_enabled():
            menu.addSeparator()
            sort_act = menu.addAction(
                style.standardIcon(SP.SP_DirOpenIcon),
                f"Sort {len(selected_ids)} file(s) into directory…",
            )

        menu.addSeparator()
        rescan_act = menu.addAction(style.standardIcon(SP.SP_BrowserReload), f"Rescan {len(selected_ids)} file(s)")
        thumb_act = menu.addAction(
            style.standardIcon(SP.SP_BrowserReload),
            f"Generate Thumbnails for {len(selected_ids)} model(s)…",
        )

        # Recrawl action: only enabled in debug mode
        recrawl_act = None
        from app.ui.config_dialog import get_debug_menu_enabled
        if get_debug_menu_enabled():
            recrawl_act = menu.addAction(style.standardIcon(SP.SP_BrowserReload), f"Recrawl {len(selected_ids)} model(s)…")

        from app.ui.config_dialog import get_metadata_sidecar_enabled
        sidecar_act = None
        if get_metadata_sidecar_enabled():
            sidecar_act = menu.addAction(
                style.standardIcon(SP.SP_DriveHDIcon),
                f"Create / Update Sidecar for {len(selected_ids)} model(s)…",
            )

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == show_files_act:
            self._show_files(model_id)
        elif action == edit_act:
            self._open_editor(model_id)
        elif action == mass_act:
            self._open_mass_editor(selected_ids)
        elif action == delete_act:
            self._delete_models(selected_ids)
        elif delete_file_act and action == delete_file_act:
            self._delete_files_from_disk(selected_ids)
        elif sort_act and action == sort_act:
            self._sort_files(selected_ids)
        elif action == thumb_act:
            self._generate_thumbnails(selected_ids)
        elif action == rescan_act:
            self._rescan_files(selected_ids)
        elif recrawl_act and action == recrawl_act:
            self._recrawl_selection(selected_ids)
        elif sidecar_act and action == sidecar_act:
            self._create_sidecar_for_selection(selected_ids)
        elif add_tag_act and action == add_tag_act:
            self.add_tag()
        elif edit_tags_act and action == edit_tags_act:
            self._open_tag_editor()
        elif edit_collections_act and action == edit_collections_act:
            self._open_category_editor()
        elif edit_subcollections_act and action == edit_subcollections_act:
            self._open_subcategory_editor()
        elif edit_categories_act and action == edit_categories_act:
            self._open_category_editor(new_category=True)
        elif edit_scales_act and action == edit_scales_act:
            self._open_scale_editor()
        elif edit_creator_act and action == edit_creator_act:
            creator_name = self._model.item(src_index.row(), COL_CREATOR).text()
            if creator_name:
                self._open_creator_editor(creator_name)

    def _show_files(self, model_id: int) -> None:
        from app.ui.file_list_dialog import FileListDialog
        FileListDialog(model_id, parent=self).exec()

    def _recrawl_selection(self, model_ids: list[int]) -> None:
        """Run the crawler extraction for the selected model(s) and show a preview
        of the changes before applying them to the DB.

        This is synchronous and may be slow for many models – callers already
        invoke it from the UI thread via the context menu.
        """
        if not model_ids:
            return

        from app.ui.recrawl_dialog import RecrawlPreviewDialog
        from app.crawler.archive import extract_archive_contents, list_contents, archive_hash
        from app.crawler.metadata import extract_metadata
        from app.database.db import session_scope
        from app.config import CrawlerConfig

        changes_all: dict[int, list[tuple[str, str, str]]] = {}

        # For each selected model, open the archive, extract relevant files and
        # compute the metadata. Build a list of (field, current, new) tuples.
        with session_scope() as session:
            models = session.query(STLModel).filter(STLModel.id.in_(model_ids)).all()
            for m in models:
                p = m.archive_path
                try:
                    archive_path = Path(p)
                except Exception:
                    continue
                # Extract only the relevant files (no thumbnail generation here)
                contents = extract_archive_contents(archive_path, unrar_path=CrawlerConfig().unrar_path)
                meta = extract_metadata(archive_path, contents=contents, source_dir=archive_path.parent)

                # Compare fields and collect changes
                changes: list[tuple[str, str, str]] = []
                def _add_if_diff(field: str, current: str, new: str) -> None:
                    cur = "" if current is None else str(current)
                    nw = "" if new is None else str(new)
                    if cur != nw:
                        changes.append((field, cur, nw))

                _add_if_diff("Title", m.title, meta.title)
                _add_if_diff("Creator", m.creator, meta.creator)
                # ModelMetadata uses 'distributor' as the field name for the source/platform
                _add_if_diff("Source", m.source, meta.distributor)
                # release_date -> year/month
                if meta.release_date:
                    ry, rm = parse_release_date(meta.release_date)
                    new_date = format_release_date(ry, rm)
                else:
                    new_date = ""
                _add_if_diff("Release date", format_release_date(m.release_year, m.release_month), new_date)
                _add_if_diff("URL", m.url, meta.url)

                # Tags comparison – join into a comma-separated string for preview
                cur_tags = ", ".join(t.name for t in m.tags)
                new_tags = ", ".join(meta.tags)
                _add_if_diff("Tags", cur_tags, new_tags)

                # STL file list preview – show count change only
                cur_files = str(len(m.stl_files))
                new_files = str(len([n for n in (list_contents(archive_path) or []) if Path(n.replace("\\", "/")).suffix.lower() in {".stl", ".obj", ".lys", ".chitubox"}]))
                _add_if_diff("File list count", cur_files, new_files)

                changes_all[m.id] = changes

        # If no changes for any selected model, inform the user
        all_changes = [(f"ID {mid}",) + ch for mid, chlist in changes_all.items() for ch in chlist]
        # Flatten into (field, current, new) with model prefix in the field name
        flat: list[tuple[str, str, str]] = []
        for mid, chlist in changes_all.items():
            for field, cur, nw in chlist:
                flat.append((f"[{mid}] {field}", cur, nw))

        dlg = RecrawlPreviewDialog(f"Recrawl preview for {len(model_ids)} model(s)", flat, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # User accepted — apply changes by re-running the upsert logic for each archive
        # We'll reuse the crawler's _upsert_archive via a temporary CrawlerWorker instance
        from app.crawler.crawler import CrawlerWorker
        config = CrawlerConfig()
        worker = CrawlerWorker(str(Path(models[0].archive_path).parent), pattern="", config=config)

        # Run per-model upsert synchronously using session_scope and the crawler helper
        with session_scope() as session:
            for mid in model_ids:
                m = session.query(STLModel).get(mid)
                if not m:
                    continue
                archive_path = Path(m.archive_path)
                # compute hash and extract contents
                cur_hash = archive_hash(archive_path, unrar_path=config.unrar_path)
                data = extract_archive_contents(archive_path, unrar_path=config.unrar_path, all_names_out=None, stl_info_out=None)
                # call the private upsert method on the worker instance
                try:
                    worker._upsert_archive(archive_path, session, config.max_thumbnails, config.max_thumb_size, current_hash=cur_hash, existing=m, contents=data, all_names=None, stl_info=None)
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    QMessageBox.warning(self, "Recrawl failed", f"Failed to recrawl {archive_path}: {exc}")
                    continue

        QMessageBox.information(self, "Recrawl complete", f"Recrawl applied for {len(model_ids)} model(s).")

    def _rescan_files(self, model_ids: list[int]) -> None:
        """Run validations and re-extract metadata for the selected files.

        This is a faster, focused check compared to a full recrawl: it will
        compute the archive hash if the filesystem indicates modification,
        re-run rule detection (warnings), refresh STL file list (if enabled),
        and update metadata fields from info.json/.url.
        """
        if not model_ids:
            return

        from app.crawler.archive import archive_hash, extract_archive_contents
        from app.crawler.metadata import extract_metadata
        from app.config import CrawlerConfig
        from app.database.db import session_scope
        from app.ui.config_dialog import (
            get_warn_presupported_stl, get_warn_no_images, get_warn_nested_archive,
            get_store_stl_file_list, get_warn_supports, get_supports_patterns,
        )

        config = CrawlerConfig()
        updated = 0
        for mid in model_ids:
            # Use a separate transactional scope per model so failures don't
            # roll back other models' updates.
            with session_scope() as session:
                m = session.get(STLModel, mid)
                if not m or not m.archive_path:
                    continue
                p = Path(m.archive_path)
                try:
                    if not p.exists():
                        QMessageBox.warning(self, "Rescan", f"Archive not found: {m.archive_path}")
                        continue

                    # Quick check: if size/mtime matches DB, skip heavy work
                    try:
                        st = p.stat()
                        fs_size = int(st.st_size)
                        fs_mtime = int(st.st_mtime_ns)
                    except Exception:
                        fs_size = None
                        fs_mtime = None

                    need_hash = True
                    if fs_size is not None and fs_mtime is not None and m.archive_size is not None and m.archive_mtime_ns is not None:
                        if fs_size == m.archive_size and fs_mtime == m.archive_mtime_ns:
                            need_hash = False

                    if need_hash:
                        new_hash = archive_hash(p, unrar_path=config.unrar_path)
                    else:
                        new_hash = m.archive_hash

                    # Extract files we need and capture the full member list so we can
                    # run the validation rules without opening the archive twice.
                    all_names: list[str] = []
                    contents = extract_archive_contents(
                        p,
                        unrar_path=config.unrar_path,
                        all_names_out=all_names,
                        stl_info_out=None,
                    )
                    meta = extract_metadata(p, contents=contents, source_dir=p.parent)

                    # Update model in-place
                    m.archive_hash = new_hash
                    try:
                        m.archive_size = fs_size
                        m.archive_mtime_ns = fs_mtime
                    except Exception:
                        pass
                    # assign metadata fields
                    m.title = meta.title or m.title
                    m.creator = meta.creator or m.creator
                    m.source = meta.distributor or m.source
                    if meta.release_date:
                        ry, rm = parse_release_date(meta.release_date)
                        if ry is not None:
                            m.release_year = ry
                            m.release_month = rm
                    m.url = meta.url or m.url
                    m.description = meta.description or m.description

                    # Recompute presupports/warnings/scale using the full member list
                    from app.crawler.rules import detect_warnings, detect_presupport, detect_scale
                    m.presupports = detect_presupport(all_names)
                    warnings = detect_warnings(
                        all_names,
                        check_presupported=get_warn_presupported_stl(),
                        check_no_images=get_warn_no_images(),
                        check_nested_archive=get_warn_nested_archive(),
                        check_supports=get_warn_supports(),
                        supports_patterns=get_supports_patterns(),
                    )
                    m.warning = warnings or None

                    # Re-detect scale and update model.scale if found
                    scales = detect_scale([p.name] + all_names)
                    if scales:
                        from app.database.helpers import get_or_create_scale
                        m.scale = get_or_create_scale(session, scales[0])

                    if get_store_stl_file_list():
                        # Refresh stl_files entries
                        for old in list(m.stl_files):
                            session.delete(old)
                        session.flush()
                        from app.database.models import STLFile
                        for fname in [n for n in (all_names or []) if Path(n.replace('\\', '/')).suffix.lower() in {'.stl', '.obj', '.lys', '.chitubox'}]:
                            session.add(STLFile(model=m, filename=fname, file_size=None, archive_hash=new_hash))

                    updated += 1
                except Exception as exc:
                    # Protect the UI from uncaught exceptions during rescan of
                    # an individual archive. Inform the user and continue.
                    logger = __import__("logging").getLogger(__name__)
                    logger.exception("Rescan failed for %s: %s", p, exc)
                    QMessageBox.warning(self, "Rescan failed", f"Failed to rescan {p}: {exc}")
                    continue

        QMessageBox.information(self, "Rescan complete", f"Rescan applied for {updated} model(s).")
        # Refresh the browser so any updated warnings / presupports / scale are shown
        self.refresh()

    def _delete_files_from_disk(self, model_ids: list[int]) -> None:
        """Confirm with the user, then delegate to the file-deletion service.

        The slot itself stays UI-only: gather config, show the confirmation
        dialog, call :func:`app.services.file_deletion.delete_archives`, and
        report the result.  All file I/O and DB cascade handling lives in
        the service so it can be unit-tested without a ``QApplication``.
        """
        if not model_ids:
            return

        from app.ui.config_dialog import get_trash_folder_path, get_use_trash_folder
        from app.services.file_deletion import delete_archives

        use_trash  = get_use_trash_folder()
        trash_path = get_trash_folder_path()
        verb       = "Move to trash" if use_trash else "Permanently delete"

        reply = QMessageBox.warning(
            self,
            f"{verb} {len(model_ids)} archive(s)",
            f"Are you sure you want to <b>{verb.lower()}</b> the selected "
            f"archive file(s) from your disk?<br><br>"
            f"This will also remove them from the database.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            report = delete_archives(session, model_ids, use_trash, trash_path)

        self.refresh()
        if report.failed:
            QMessageBox.warning(
                self,
                "Delete Files",
                f"Successfully processed {report.deleted} file(s). "
                f"Failed to process {len(report.failed)} file(s).",
            )
        
    def _delete_models(self, model_ids: list[int]) -> None:
        """Confirm then delete models and their LMDB thumbnails from the database."""
        count = len(model_ids)
        reply = QMessageBox.warning(
            self,
            "Delete Model",
            f"Permanently delete <b>{count} model{'s' if count != 1 else ''}</b> "
            f"and their thumbnail files from the database?<br><br>This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from app.utils.thumbnailer import delete_thumbnail
        with session_scope() as session:
            for model_id in model_ids:
                stl: STLModel = session.get(STLModel, model_id)
                if stl is None:
                    continue
                for img in list(stl.images):
                    if img.thumbnail_path:
                        delete_thumbnail(img.thumbnail_path)
                session.delete(stl)

        self.refresh()

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:
        # Listen to the scroll area resize, not just the viewport, as viewport resize
        # can be triggered by the scrollbar appearing/disappearing.
        if obj is self._thumb_scroll and event.type() == QEvent.Type.Resize:
            self._rescale_thumbnails()
        return super().eventFilter(obj, event)

    def _clear_thumbnails(self) -> None:
        """Remove all thumbnail widgets from the sidebar and drop the pixmap cache."""
        self._thumb_pixmaps.clear()
        while self._thumb_vlayout.count():
            item = self._thumb_vlayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._creator_panel.setVisible(False)

    def _show_thumbnails(self, model_id: int) -> None:
        """Load and display thumbnails for the model with *model_id* in the sidebar."""
        self._clear_thumbnails()
        session = get_session()
        try:
            stl: STLModel = session.get(STLModel, model_id)
            if stl is None:
                return

            for img in stl.images:
                px = pixmap_from_path(img.thumbnail_path)
                self._thumb_pixmaps.append(px)
                lbl = ClickableLabel()
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setFrameShape(QFrame.Shape.StyledPanel)
                lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                lbl.clicked.connect(lambda _px=px: ImageViewerDialog(_px, self).exec())
                self._thumb_vlayout.addWidget(lbl)
        finally:
            session.close()
        self._rescale_thumbnails(force=True)

    def _rescale_thumbnails(self, force: bool = False) -> None:
        """Scale all sidebar thumbnails to fit the current scroll-area width."""
        # Use the total scroll area width instead of viewport width to avoid
        # infinite resize loops (which happen when the scrollbar appearing/disappearing
        # changes the viewport width, which in turn changes the image height, etc.)
        w = max(self._thumb_scroll.width() - 36, 60)
        
        if not force and getattr(self, '_last_thumb_w', -1) == w:
            return
        self._last_thumb_w = w

        for i, px in enumerate(self._thumb_pixmaps):
            item = self._thumb_vlayout.itemAt(i)
            if item and item.widget():
                scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
                item.widget().setPixmap(scaled)

    def add_tag(self) -> None:
        """Prompt for a new tag name, create it in the DB, and rebuild the filter tree."""
        name, ok = QInputDialog.getText(self, "Add Tag", "Tag name:")
        if not ok or not name.strip():
            return
        name = name.strip().lower()
        with session_scope() as session:
            if session.query(Tag).filter_by(name=name).first():
                QMessageBox.information(self, "Add Tag", f"Tag '{name}' already exists.")
                return
            session.add(Tag(name=name))
        self._populate_filter_tree()

    def _open_tag_editor(self) -> None:
        from app.ui.tag_editor_dialog import TagEditorDialog
        TagEditorDialog(parent=self).exec()
        self.refresh()

    def _open_category_editor(self, new_category: bool = False) -> None:
        if new_category:
            from app.ui.category_editor_dialog import CategoryEditorDialog
            CategoryEditorDialog(parent=self).exec()
        else:
            from app.ui.collection_editor_dialog import CollectionEditorDialog
            CollectionEditorDialog(parent=self).exec()
        self.refresh()

    def _open_subcategory_editor(self) -> None:
        from app.ui.subcollection_editor_dialog import SubcollectionEditorDialog
        SubcollectionEditorDialog(parent=self).exec()
        self.refresh()

    def _open_scale_editor(self) -> None:
        from app.ui.scale_editor_dialog import ScaleEditorDialog
        ScaleEditorDialog(parent=self).exec()
        self.refresh()

    def _open_editor(self, model_id: int) -> None:
        from app.ui.editor_dialog import EditorDialog
        dlg = EditorDialog(model_id, parent=self)
        if dlg.exec():
            self.refresh()

    def _open_mass_editor(self, model_ids: list[int]) -> None:
        from app.ui.mass_editor_dialog import MassEditorDialog
        dlg = MassEditorDialog(model_ids, parent=self)
        if dlg.exec():
            self.refresh()

    def _sort_files(self, model_ids: list[int]) -> None:
        from app.ui.sort_dialog import SortFilesDialog
        dlg = SortFilesDialog(model_ids, parent=self)
        if dlg.exec():
            self.refresh()

    def _generate_thumbnails(self, model_ids: list[int]) -> None:
        """Prompt for a thumbnail count then run ThumbnailGeneratorWorker on *model_ids*."""
        from app.ui.config_dialog import get_max_thumbnails

        count, ok = QInputDialog.getInt(
            self,
            "Generate Thumbnails",
            "Number of thumbnails to generate per model:",
            get_max_thumbnails(),
            1,
            100,
            1,
        )
        if not ok:
            return

        # Custom progress dialog with a "Send to background" button and Cancel
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar

        dlg = QDialog(self)
        dlg.setWindowTitle("Generate Thumbnails")
        dlg.setModal(True)
        vlay = QVBoxLayout(dlg)
        label = QLabel("Generating thumbnails…")
        vlay.addWidget(label)
        progress_bar = QProgressBar()
        progress_bar.setRange(0, len(model_ids))
        progress_bar.setValue(0)
        vlay.addWidget(progress_bar)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        send_bg_btn = QPushButton("Send to background")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(send_bg_btn)
        btn_row.addWidget(cancel_btn)
        vlay.addLayout(btn_row)
        dlg.setMinimumWidth(420)

        self._thumb_worker = ThumbnailGeneratorWorker(model_ids, count, parent=self)
        # Prevent starting if another background job is active
        from app.utils.background_jobs import start_job, active_job
        if not start_job("thumbnails"):
            QMessageBox.warning(self, "Generate Thumbnails", "Another background job is already running. Try again later.")
            return

        def _on_progress(current, total):
            # Update our custom dialog progress
            progress_bar.setValue(current)
            label.setText(f"Processing model {current} of {total}…")
            # Forward progress for optional background display in main window
            self.thumbnail_progress_updated.emit(current, total)

        def _on_finished():
            progress_bar.setValue(len(model_ids))
            # Close the dialog if it's still open
            if dlg.isVisible():
                dlg.accept()
            self.refresh()
            self._thumb_worker = None
            from app.utils.background_jobs import end_job
            end_job("thumbnails")

        self._thumb_worker.progress.connect(_on_progress)
        self._thumb_worker.finished.connect(_on_finished)
        # Wire up buttons: Send to background hides the dialog; Cancel stops the worker
        def _send_background():
            dlg.hide()
            # show current progress in status bar via signal
            self.thumbnail_progress_updated.emit(progress_bar.value(), progress_bar.maximum())

        def _on_cancel():
            # Request worker to stop and close dialog
            if self._thumb_worker is not None:
                self._thumb_worker.stop()
            dlg.reject()
            from app.utils.background_jobs import end_job
            end_job("thumbnails")

        send_bg_btn.clicked.connect(_send_background)
        cancel_btn.clicked.connect(_on_cancel)

        self._thumb_worker.start()
        # Show modal dialog (blocks until accept/reject) — if Send to background was clicked
        # dlg.exec() will return but the worker continues running; if Cancel was clicked,
        # the worker is requested to stop.
        dlg.exec()

        # Ensure main window knows when the worker finishes
        self._thumb_worker.finished.connect(lambda: self.thumbnail_finished.emit())

    def generate_thumbnails_for_selected(self) -> None:
        """Generate thumbnails for the rows currently selected in the table.

        Called from the main-window Tools menu; mirrors the right-click action.
        """
        ids = self._selected_model_ids()
        if not ids:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Generate Thumbnails",
                "Select one or more models in the table first.",
            )
            return
        self._generate_thumbnails(ids)

    def _create_sidecar_for_selection(self, model_ids: list[int]) -> None:
        """Open a sidecar dialog scoped to the folders of the selected models."""
        from pathlib import Path
        from app.ui.metadata_sidecar_dialog import MetadataSidecarDialog

        with session_scope() as session:
            rows = (
                session.query(STLModel.archive_path)
                .filter(STLModel.id.in_(model_ids))
                .all()
            )

        folders = {Path(r.archive_path).parent for r in rows if r.archive_path}
        if not folders:
            QMessageBox.warning(self, "Create Sidecar", "No valid file paths found for the selected models.")
            return

        dlg = MetadataSidecarDialog(folders=folders, parent=self)
        dlg.exec()

    def show_image_grid(self) -> None:
        """Open an image grid for all models currently visible in the table."""
        model_ids = []
        for proxy_row in range(self._proxy.rowCount()):
            src_row = self._proxy.mapToSource(self._proxy.index(proxy_row, 0)).row()
            item = self._model.item(src_row, COL_THUMB)
            if item is not None:
                mid = item.data(MODEL_ID_ROLE)
                if mid is not None:
                    model_ids.append(mid)
        from app.ui.image_grid_dialog import ImageGridDialog
        dlg = ImageGridDialog(model_ids, parent=self)
        dlg.show_selection_requested.connect(self.show_model_ids)
        dlg.exec()
