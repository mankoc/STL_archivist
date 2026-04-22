"""
Tab 2 – Model browser.
Left panel: filter tree (Author / Tags / Type).
Centre: sortable table with thumbnail, title, author, category, date, tags, path.
Right panel: image thumbnail sidebar (populated on single-click).
Right-click or double-click: Edit Model.
"""
from pathlib import Path as FSPath

_ICONS_DIR = FSPath(__file__).parent.parent.parent / "resources" / "icons"

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QSize, QStringListModel, QEvent, pyqtSignal, QItemSelection, QTimer, QThread
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QPixmap, QIcon, QKeySequence, QShortcut, QColor, QFont
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTableView,
    QLineEdit, QLabel, QHeaderView, QAbstractItemView,
    QMenu, QScrollArea, QFrame, QInputDialog, QMessageBox,
    QStyledItemDelegate, QComboBox, QCompleter,
    QDialog, QSizePolicy, QApplication, QPushButton,
    QProgressDialog,
)
from PyQt6.QtCore import QSettings

from sqlalchemy import or_, and_
from sqlalchemy.orm import selectinload

from app.database.db import get_session, session_scope
from app.database.models import STLModel, Tag, Sculptor, Category, Subcategory, Release, Scale
from app.utils.thumbnailer import pixmap_from_path
from app.utils.path_pattern import parse_release_date, format_release_date

# Column indices
COL_THUMB        = 0
COL_TITLE        = 1
COL_AUTHOR       = 2
COL_SOURCE       = 3
COL_DATE         = 4
COL_TAGS         = 5
COL_SCULPTOR     = 6
COL_CATEGORY     = 7
COL_SUBCATEGORY  = 8
COL_SEQUENCE     = 9
COL_RELEASE      = 10
COL_PATH         = 11
COL_SCALE        = 12
COL_PRESUPPORTED = 13
COL_ADDED        = 14
COL_WARNING      = 15
_HEADERS = ["", "Title", "Author", "Source", "Date", "Tags",
            "Sculptor", "Category", "Subcategory", "Seq", "Release", "Path",
            "Scale", "Presupports", "Added", "Warnings"]

MODEL_ID_ROLE = Qt.ItemDataRole.UserRole + 1


class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ImageViewerDialog(QDialog):
    def __init__(self, pixmap: QPixmap, parent=None):
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

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(self._get_items())
        combo.setCurrentIndex(-1)
        combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        return combo

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText().strip())

    def updateEditorGeometry(self, editor, option, index):
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


class CategoryDelegate(_ComboDelegate):
    """QComboBox of all Category names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(c.name for c in session.query(Category).order_by(Category.name).all())
        finally:
            session.close()


class SubcategoryDelegate(_ComboDelegate):
    """QComboBox of all Subcategory names."""

    def _get_items(self) -> list[str]:
        session = get_session()
        try:
            return sorted(
                s.name for s in session.query(Subcategory).order_by(Subcategory.name).all()
            )
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

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(_PRESUPPORTS_OPTIONS)
        return combo

    def setEditorData(self, editor, index):
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


class TagDelegate(QStyledItemDelegate):
    """QLineEdit that autocompletes each comma-separated token from the tag pool."""

    def createEditor(self, parent, option, index):
        session = get_session()
        try:
            all_tags = [t.name for t in session.query(Tag).order_by(Tag.name).all()]
        finally:
            session.close()

        edit = _TagLineEdit(all_tags, parent)
        return edit

    def setEditorData(self, editor, index):
        editor.setText(index.data() or "")
        editor.setCursorPosition(len(editor.text()))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text().strip())

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class _TagLineEdit(QLineEdit):
    """QLineEdit with per-token autocomplete for comma-separated tag lists."""

    def __init__(self, all_tags: list[str], parent=None):
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

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._show_completions()

    def _on_text_edited(self, _text: str):
        self._show_completions()

    def _show_completions(self):
        token = self._current_token()
        self._completer.setCompletionPrefix(token)
        self._completer.complete()

    def _insert_completion(self, chosen: str):
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

    def __init__(self, model_ids: list[int], max_thumbnails: int, parent=None):
        super().__init__(parent)
        self._model_ids = model_ids
        self._max_thumbnails = max_thumbnails

    def run(self):
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


class BrowserTab(QWidget):
    model_count_changed = pyqtSignal(int)

    def __init__(self, parent=None):
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
    def _build_ui(self):
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

        self._filter_tree = QTreeWidget()
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
        self._search_edit.setPlaceholderText("Title, author or tag…")
        self._search_edit.textChanged.connect(lambda _: self._search_timer.start())
        search_row.addWidget(self._search_edit)
        centre_layout.addLayout(search_row)

        self._model = QStandardItemModel()
        self._model.setHorizontalHeaderLabels(_HEADERS)
        self._model.itemChanged.connect(self._on_item_changed)

        self._proxy = QSortFilterProxyModel()
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
        self._table.setItemDelegateForColumn(COL_SOURCE,       SourceDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TAGS,         TagDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SCULPTOR,     SculptorDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_CATEGORY,     CategoryDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SUBCATEGORY,  SubcategoryDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_RELEASE,      ReleaseDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SCALE,        ScaleDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_PRESUPPORTED, PresupportsDelegate(self._table))
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        header = self._table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_context_menu)
        header.setSectionsMovable(True)
        header.sectionMoved.connect(self._save_header_state)
        header.sectionResized.connect(self._save_header_state)
        self._restore_header_state()
        self._table.clicked.connect(self._on_row_clicked)
        self._table.doubleClicked.connect(self._on_double_click)
        QShortcut(QKeySequence.StandardKey.SelectAll, self._table, self._table.selectAll)
        QShortcut(QKeySequence("E"), self._table, self._edit_selected)
        centre_layout.addWidget(self._table)

        splitter.addWidget(centre)

        # ---- Right: thumbnail sidebar ----
        right = QWidget()
        self._right_pane = right
        right.setMinimumWidth(160)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.addWidget(QLabel("Images"))

        self._thumb_content = QWidget()
        self._thumb_vlayout = QVBoxLayout(self._thumb_content)
        self._thumb_vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._thumb_vlayout.setSpacing(6)

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setWidget(self._thumb_content)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_scroll.viewport().installEventFilter(self)
        right_layout.addWidget(self._thumb_scroll)

        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setHandleWidth(6)
        self._thumb_splitter = splitter

    # ------------------------------------------------------------------
    def refresh(self):
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
    def _populate_filter_tree(self):
        # Save checked items and expanded top-level sections so they survive the rebuild.
        # expanded_sections is None on the very first call (tree is empty) → default collapsed.
        checked_data: set = set()
        expanded_sections: set[str] | None = None

        root_item = self._filter_tree.invisibleRootItem()
        if root_item.childCount() > 0:
            expanded_sections = set()
            def _collect_state(item: QTreeWidgetItem):
                d = item.data(0, Qt.ItemDataRole.UserRole)
                if d and item.checkState(0) == Qt.CheckState.Checked:
                    checked_data.add(d)
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

        _icon_author = _icon("icon_author")
        _icon_tag    = _icon("icon_tag")
        _icon_type   = _icon("icon_type")
        _icon_path   = _icon("icon_path")
        _icon_date   = (_icon("icon_date") if (_ICONS_DIR / f"icon_date_{_v}.svg").exists()
                        else QApplication.style().standardIcon(
                            QApplication.style().StandardPixmap.SP_FileDialogContentsView))

        _CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable

        def _make_leaf(label: str, data) -> QTreeWidgetItem:
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            item.setFlags(_CHECKABLE)
            state = Qt.CheckState.Checked if data in checked_data else Qt.CheckState.Unchecked
            item.setCheckState(0, state)
            return item

        header = self._table.horizontalHeader()
        show_author = not header.isSectionHidden(COL_AUTHOR)
        show_tags   = not header.isSectionHidden(COL_TAGS)
        show_source = not header.isSectionHidden(COL_SOURCE)
        show_date   = not header.isSectionHidden(COL_DATE)

        session = get_session()
        try:
            if show_author:
                authors_item = QTreeWidgetItem(["Authors"])
                authors_item.setIcon(0, _icon_author)
                self._filter_tree.addTopLevelItem(authors_item)
                for (author,) in session.query(STLModel.author).distinct().order_by(STLModel.author).all():
                    if author:
                        authors_item.addChild(_make_leaf(author, ("author", author)))

            if show_tags:
                tags_item = QTreeWidgetItem(["Tags"])
                tags_item.setIcon(0, _icon_tag)
                self._filter_tree.addTopLevelItem(tags_item)
                for tag in session.query(Tag).order_by(Tag.name).all():
                    tags_item.addChild(_make_leaf(tag.name, ("tag", tag.name)))

            if show_source:
                types_item = QTreeWidgetItem(["Type"])
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
                dates_item = QTreeWidgetItem(["Date"])
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

            paths_item = QTreeWidgetItem(["Path"])
            paths_item.setIcon(0, _icon_path)
            self._filter_tree.addTopLevelItem(paths_item)
            dirs: set[str] = set()
            for (ap,) in session.query(STLModel.archive_path).distinct().all():
                if ap:
                    dirs.add(str(FSPath(ap).parent))
            new_dirs = frozenset(dirs)
            if new_dirs != self._cached_path_dirs:
                self._cached_path_dirs = new_dirs
                self._cached_sorted_dirs = sorted(dirs)
            self._build_path_tree(paths_item, self._cached_sorted_dirs, checked_data)
            self._init_path_parent_states(paths_item)
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
                         checked_data: set | None = None) -> None:
        """Build a nested tree of directory segments under *root_item*."""
        _CHECKABLE = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        node_map: dict[str, QTreeWidgetItem] = {}

        for d in dirs:
            parts = FSPath(d).parts
            for depth in range(1, len(parts) + 1):
                full = str(FSPath(*parts[:depth]))
                if full in node_map:
                    continue
                label = parts[depth - 1]
                parent = node_map.get(str(FSPath(*parts[:depth - 1]))) if depth > 1 else root_item
                if parent is None:
                    parent = root_item
                data = ("path", full)
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, data)
                item.setFlags(_CHECKABLE)
                state = (Qt.CheckState.Checked
                         if checked_data and data in checked_data
                         else Qt.CheckState.Unchecked)
                item.setCheckState(0, state)
                parent.addChild(item)
                node_map[full] = item

    def _load_table(self, author_filters=None, tag_filters=None, type_filters=None,
                    path_filters=None, date_filters=None):
        """Reload the table, optionally with multi-value filter lists.

        Within each category the values are OR-ed; across categories they are AND-ed.
        *date_filters* is a list of (year, month_or_None) tuples.
        """
        self._loading = True
        self._model.removeRows(0, self._model.rowCount())
        session = get_session()
        try:
            q = session.query(STLModel).options(
                selectinload(STLModel.tags),
                selectinload(STLModel.sculptor),
                selectinload(STLModel.category),
                selectinload(STLModel.subcategory),
                selectinload(STLModel.release),
                selectinload(STLModel.scale),
            )
            if author_filters:
                q = q.filter(STLModel.author.in_(author_filters))
            if type_filters:
                q = q.filter(STLModel.source.in_(type_filters))
            if tag_filters:
                q = q.filter(STLModel.tags.any(Tag.name.in_(tag_filters)))
            if path_filters:
                q = q.filter(or_(*(
                    STLModel.archive_path.like(p.rstrip("/") + "/%")
                    for p in path_filters
                )))
            if date_filters:
                date_conditions = []
                for year, month in date_filters:
                    if month is None:
                        date_conditions.append(STLModel.release_year == year)
                    else:
                        date_conditions.append(
                            and_(STLModel.release_year == year,
                                 STLModel.release_month == month)
                        )
                q = q.filter(or_(*date_conditions))

            for stl in q.order_by(STLModel.title).all():
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
                row = [
                    thumb_item,
                    QStandardItem(stl.title or ""),
                    QStandardItem(stl.author or ""),
                    QStandardItem(stl.source or ""),
                    QStandardItem(format_release_date(stl.release_year, stl.release_month)),
                    QStandardItem(tags_str),
                    QStandardItem(stl.sculptor.name if stl.sculptor else ""),
                    QStandardItem(stl.category.name if stl.category else ""),
                    QStandardItem(stl.subcategory.name if stl.subcategory else ""),
                    QStandardItem(seq_str),
                    QStandardItem(stl.release.name if stl.release else ""),
                    path_item,
                    QStandardItem(stl.scale.name if stl.scale else ""),
                    QStandardItem(stl.presupports or ""),
                    added_item,
                    warning_item,
                ]
                self._model.appendRow(row)
        finally:
            session.close()
            self._loading = False
        self.model_count_changed.emit(self._model.rowCount())

    # ------------------------------------------------------------------
    def _on_item_changed(self, item: QStandardItem):
        if self._loading:
            return
        col = item.column()
        if col in (COL_THUMB, COL_ADDED, COL_WARNING):
            return
        # Debounce: store the latest item for this cell and (re)start the timer.
        self._pending_edits[(item.row(), col)] = item
        self._edit_timer.start()

    def _flush_pending_edits(self):
        """Write all debounced cell edits to the DB in a single pass."""
        items = list(self._pending_edits.values())
        self._pending_edits.clear()
        for item in items:
            self._write_item(item)

    def _write_item(self, item: QStandardItem):
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
            elif col == COL_AUTHOR:
                stl.author = value
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
                    stl.subcategory = None  # subcategory depends on category
            elif col == COL_SUBCATEGORY:
                if value:
                    q = session.query(Subcategory).filter_by(name=value)
                    if stl.category_id:
                        q = q.filter_by(category_id=stl.category_id)
                    sc = q.first()
                    if sc is None and stl.category_id:
                        sc = Subcategory(name=value, category_id=stl.category_id)
                        session.add(sc)
                        session.flush()
                    if sc:
                        stl.subcategory = sc
                else:
                    stl.subcategory = None
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
    def _on_search(self, text: str):
        self._proxy.setFilterFixedString(text)

    def _init_path_parent_states(self, item: QTreeWidgetItem) -> Qt.CheckState:
        """Recursively compute and set PartiallyChecked on path ancestor items.
        Called after building the path tree to initialise tristate visuals."""
        if item.childCount() == 0:
            return item.checkState(0)
        child_states = [self._init_path_parent_states(item.child(i))
                        for i in range(item.childCount())]
        if item.data(0, Qt.ItemDataRole.UserRole) is None:
            return Qt.CheckState.Unchecked
        checked = child_states.count(Qt.CheckState.Checked)
        partial = child_states.count(Qt.CheckState.PartiallyChecked)
        n = len(child_states)
        if checked == n:
            new_state = Qt.CheckState.Checked
        elif checked > 0 or partial > 0:
            new_state = Qt.CheckState.PartiallyChecked
        else:
            new_state = Qt.CheckState.Unchecked
        item.setCheckState(0, new_state)
        return new_state

    def _update_path_parent_states(self, item: QTreeWidgetItem) -> None:
        """Walk up the path tree from *item* and set Partial/Checked/Unchecked
        on each ancestor based on its children's states."""
        self._filter_tree.blockSignals(True)
        try:
            parent = item.parent()
            while parent is not None and parent.data(0, Qt.ItemDataRole.UserRole) is not None:
                n = parent.childCount()
                checked = sum(1 for i in range(n)
                              if parent.child(i).checkState(0) == Qt.CheckState.Checked)
                partial = sum(1 for i in range(n)
                              if parent.child(i).checkState(0) == Qt.CheckState.PartiallyChecked)
                if checked == n:
                    parent.setCheckState(0, Qt.CheckState.Checked)
                elif checked > 0 or partial > 0:
                    parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
                else:
                    parent.setCheckState(0, Qt.CheckState.Unchecked)
                parent = parent.parent()
        finally:
            self._filter_tree.blockSignals(False)

    def _on_filter_item_changed(self, item: QTreeWidgetItem, _col: int):
        """Called when a checkbox in the filter tree is toggled."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is not None:
            if isinstance(data, tuple) and data[0] == "path":
                self._update_path_parent_states(item)
            self._apply_checked_filters()

    def _apply_checked_filters(self):
        """Collect all checked filter items and reload the table."""
        author_filters: list[str] = []
        tag_filters:    list[str] = []
        type_filters:   list[str] = []
        path_filters:   list[str] = []
        date_filters:   list[tuple] = []

        def _collect(item: QTreeWidgetItem):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and item.checkState(0) == Qt.CheckState.Checked:
                kind, value = data
                if kind == "author":
                    author_filters.append(value)
                elif kind == "tag":
                    tag_filters.append(value)
                elif kind == "source":
                    type_filters.append(value)
                elif kind == "path":
                    path_filters.append(value)
                elif kind == "date_year":
                    date_filters.append((value, None))
                elif kind == "date_month":
                    date_filters.append(value)  # (year, month)
            for i in range(item.childCount()):
                _collect(item.child(i))

        root = self._filter_tree.invisibleRootItem()
        for i in range(root.childCount()):
            _collect(root.child(i))

        self._load_table(
            author_filters=author_filters or None,
            tag_filters=tag_filters or None,
            type_filters=type_filters or None,
            path_filters=path_filters or None,
            date_filters=date_filters or None,
        )

    def _clear_filters(self):
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

    def _on_row_clicked(self, index):
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is not None:
            self._show_thumbnails(model_id)
            self._update_info_panel(model_id)

    def _update_info_panel(self, model_id: int):
        session = get_session()
        try:
            stl: STLModel = session.get(STLModel, model_id)
            if stl is None:
                return
            if stl.author:
                self._info_author_lbl.setText(f"<b>Author:</b> {stl.author}")
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

    def _on_double_click(self, index):
        if index.column() != COL_PATH:
            return  # let the cell editor handle it
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)
        if model_id is not None:
            self._open_editor(model_id)

    def _edit_selected(self):
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

    def _save_header_state(self):
        settings = QSettings()
        settings.setValue("browser/header_state", self._table.horizontalHeader().saveState())
        settings.setValue("browser/header_column_count", self._model.columnCount())

    def _restore_header_state(self):
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

    def _header_context_menu(self, pos):
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
            if col in (COL_AUTHOR, COL_TAGS, COL_SOURCE, COL_PATH, COL_DATE):
                self._populate_filter_tree()

    def _context_menu(self, pos):
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)

        selected_ids = self._selected_model_ids()

        style = QApplication.style()
        SP = style.StandardPixmap
        menu = QMenu(self)
        edit_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Model…")
        edit_act.setVisible(len(selected_ids) == 1)
        mass_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), f"Mass Edit {len(selected_ids)} model(s)…")
        mass_act.setVisible(len(selected_ids) > 1)
        menu.addSeparator()
        delete_act = menu.addAction(style.standardIcon(SP.SP_TrashIcon), f"Delete {len(selected_ids)} model(s)…")

        add_tag_act = None
        edit_tags_act = None
        edit_categories_act = None
        edit_subcategories_act = None
        edit_scales_act = None
        src_col = self._proxy.mapToSource(index).column()
        if src_col == COL_TAGS:
            menu.addSeparator()
            add_tag_act = menu.addAction(style.standardIcon(SP.SP_FileDialogNewFolder), "Add Tag…")
            edit_tags_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Tags…")
        elif src_col == COL_CATEGORY:
            menu.addSeparator()
            edit_categories_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Categories…")
        elif src_col == COL_SUBCATEGORY:
            menu.addSeparator()
            edit_subcategories_act = menu.addAction(style.standardIcon(SP.SP_FileDialogDetailedView), "Edit Subcategories…")
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
        thumb_act = menu.addAction(
            style.standardIcon(SP.SP_BrowserReload),
            f"Generate Thumbnails for {len(selected_ids)} model(s)…",
        )

        from app.ui.config_dialog import get_metadata_sidecar_enabled
        sidecar_act = None
        if get_metadata_sidecar_enabled():
            sidecar_act = menu.addAction(
                style.standardIcon(SP.SP_DriveHDIcon),
                f"Create / Update Sidecar for {len(selected_ids)} model(s)…",
            )

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == edit_act:
            self._open_editor(model_id)
        elif action == mass_act:
            self._open_mass_editor(selected_ids)
        elif action == delete_act:
            self._delete_models(selected_ids)
        elif sort_act and action == sort_act:
            self._sort_files(selected_ids)
        elif action == thumb_act:
            self._generate_thumbnails(selected_ids)
        elif sidecar_act and action == sidecar_act:
            self._create_sidecar_for_selection(selected_ids)
        elif add_tag_act and action == add_tag_act:
            self.add_tag()
        elif edit_tags_act and action == edit_tags_act:
            self._open_tag_editor()
        elif edit_categories_act and action == edit_categories_act:
            self._open_category_editor()
        elif edit_subcategories_act and action == edit_subcategories_act:
            self._open_subcategory_editor()
        elif edit_scales_act and action == edit_scales_act:
            self._open_scale_editor()

    def _delete_models(self, model_ids: list[int]):
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
    def eventFilter(self, obj, event):
        if obj is self._thumb_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._rescale_thumbnails()
        return super().eventFilter(obj, event)

    def _clear_thumbnails(self):
        self._thumb_pixmaps.clear()
        while self._thumb_vlayout.count():
            item = self._thumb_vlayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_thumbnails(self, model_id: int):
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
        self._rescale_thumbnails()

    def _rescale_thumbnails(self):
        w = max(self._thumb_scroll.viewport().width() - 12, 60)
        for i, px in enumerate(self._thumb_pixmaps):
            item = self._thumb_vlayout.itemAt(i)
            if item and item.widget():
                scaled = px.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
                item.widget().setPixmap(scaled)

    def add_tag(self):
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

    def _open_tag_editor(self):
        from app.ui.tag_editor_dialog import TagEditorDialog
        TagEditorDialog(parent=self).exec()
        self.refresh()

    def _open_category_editor(self):
        from app.ui.category_editor_dialog import CategoryEditorDialog
        CategoryEditorDialog(parent=self).exec()
        self.refresh()

    def _open_subcategory_editor(self):
        from app.ui.subcategory_editor_dialog import SubcategoryEditorDialog
        SubcategoryEditorDialog(parent=self).exec()
        self.refresh()

    def _open_scale_editor(self):
        from app.ui.scale_editor_dialog import ScaleEditorDialog
        ScaleEditorDialog(parent=self).exec()
        self.refresh()

    def _open_editor(self, model_id: int):
        from app.ui.editor_dialog import EditorDialog
        dlg = EditorDialog(model_id, parent=self)
        if dlg.exec():
            self.refresh()

    def _open_mass_editor(self, model_ids: list[int]):
        from app.ui.mass_editor_dialog import MassEditorDialog
        dlg = MassEditorDialog(model_ids, parent=self)
        if dlg.exec():
            self.refresh()

    def _sort_files(self, model_ids: list[int]):
        from app.ui.sort_dialog import SortFilesDialog
        dlg = SortFilesDialog(model_ids, parent=self)
        if dlg.exec():
            self.refresh()

    def _generate_thumbnails(self, model_ids: list[int]):
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

        progress = QProgressDialog(
            "Generating thumbnails…", "Cancel", 0, len(model_ids), self
        )
        progress.setWindowTitle("Generate Thumbnails")
        progress.setMinimumDuration(0)
        progress.setModal(True)
        progress.setValue(0)

        self._thumb_worker = ThumbnailGeneratorWorker(model_ids, count, parent=self)

        def _on_progress(current, total):
            progress.setValue(current)
            progress.setLabelText(f"Processing model {current} of {total}…")

        def _on_finished():
            progress.setValue(len(model_ids))
            self.refresh()
            self._thumb_worker = None

        self._thumb_worker.progress.connect(_on_progress)
        self._thumb_worker.finished.connect(_on_finished)
        progress.canceled.connect(lambda: None)  # worker runs to completion; cancel just closes dialog
        self._thumb_worker.start()

    def generate_thumbnails_for_selected(self):
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

    def _create_sidecar_for_selection(self, model_ids: list[int]):
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

    def show_image_grid(self):
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
        ImageGridDialog(model_ids, parent=self).exec()
