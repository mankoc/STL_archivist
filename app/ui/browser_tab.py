"""
Tab 2 – Model browser.
Left panel: filter tree (Author / Tags / Type).
Centre: sortable table with thumbnail, title, author, category, date, tags, path.
Right panel: image thumbnail sidebar (populated on single-click).
Right-click or double-click: Edit Model.
"""
from pathlib import Path as FSPath

_ICONS_DIR = FSPath(__file__).parent.parent.parent / "resources" / "icons"

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QSize, QStringListModel, QEvent, pyqtSignal, QItemSelection
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QPixmap, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTableView,
    QLineEdit, QLabel, QHeaderView, QAbstractItemView,
    QMenu, QScrollArea, QFrame, QInputDialog, QMessageBox,
    QStyledItemDelegate, QComboBox, QCompleter,
    QDialog, QSizePolicy, QApplication,
)
from PyQt6.QtCore import QSettings

from sqlalchemy.orm import selectinload

from app.database.db import get_session, session_scope
from app.database.models import STLModel, Tag, Sculptor, Category, Subcategory, Release
from app.utils.thumbnailer import pixmap_from_path
from app.utils.path_pattern import parse_release_date, format_release_date

# Column indices
COL_THUMB       = 0
COL_TITLE       = 1
COL_AUTHOR      = 2
COL_SOURCE      = 3
COL_DATE        = 4
COL_TAGS        = 5
COL_SCULPTOR    = 6
COL_CATEGORY    = 7
COL_SUBCATEGORY = 8
COL_SEQUENCE    = 9
COL_RELEASE     = 10
COL_PATH        = 11
_HEADERS = ["", "Title", "Author", "Source", "Date", "Tags",
            "Sculptor", "Category", "Subcategory", "Seq", "Release", "Path"]

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


class BrowserTab(QWidget):
    model_count_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._thumb_pixmaps: list[QPixmap] = []  # original (unscaled) pixmaps
        self._cached_path_dirs: frozenset[str] = frozenset()
        self._cached_sorted_dirs: list[str] = []
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
        left_layout.addWidget(QLabel("Filter"))

        self._filter_tree = QTreeWidget()
        self._filter_tree.setHeaderHidden(True)
        self._filter_tree.itemClicked.connect(self._on_filter_clicked)
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
        self._search_edit.textChanged.connect(self._on_search)
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
        self._table.setItemDelegateForColumn(COL_SOURCE,      SourceDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TAGS,        TagDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SCULPTOR,    SculptorDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_CATEGORY,    CategoryDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_SUBCATEGORY, SubcategoryDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_RELEASE,     ReleaseDelegate(self._table))
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        header = self._table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_context_menu)
        header.setSectionsMovable(True)
        header.sectionMoved.connect(self._save_header_state)
        header.sectionResized.connect(lambda *_: self._save_header_state())
        self._restore_header_state()
        self._table.clicked.connect(self._on_row_clicked)
        self._table.doubleClicked.connect(self._on_double_click)
        QShortcut(QKeySequence.StandardKey.SelectAll, self._table, self._table.selectAll)
        QShortcut(QKeySequence("E"), self._table, self._edit_selected)
        centre_layout.addWidget(self._table)

        splitter.addWidget(centre)

        # ---- Right: thumbnail sidebar ----
        right = QWidget()
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
        self._load_table()
        self._clear_thumbnails()

    # ------------------------------------------------------------------
    def _populate_filter_tree(self):
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

        header = self._table.horizontalHeader()
        show_author = not header.isSectionHidden(COL_AUTHOR)
        show_tags   = not header.isSectionHidden(COL_TAGS)
        show_source = not header.isSectionHidden(COL_SOURCE)
        show_path   = not header.isSectionHidden(COL_PATH)

        session = get_session()
        try:
            if show_author:
                authors_item = QTreeWidgetItem(["Authors"])
                authors_item.setIcon(0, _icon_author)
                self._filter_tree.addTopLevelItem(authors_item)
                for (author,) in session.query(STLModel.author).distinct().order_by(STLModel.author).all():
                    if author:
                        child = QTreeWidgetItem([author])
                        child.setData(0, Qt.ItemDataRole.UserRole, ("author", author))
                        authors_item.addChild(child)

            if show_tags:
                tags_item = QTreeWidgetItem(["Tags"])
                tags_item.setIcon(0, _icon_tag)
                self._filter_tree.addTopLevelItem(tags_item)
                for tag in session.query(Tag).order_by(Tag.name).all():
                    child = QTreeWidgetItem([tag.name])
                    child.setData(0, Qt.ItemDataRole.UserRole, ("tag", tag.name))
                    tags_item.addChild(child)

            if show_source:
                types_item = QTreeWidgetItem(["Type"])
                types_item.setIcon(0, _icon_type)
                self._filter_tree.addTopLevelItem(types_item)
                for (t,) in session.query(STLModel.source).distinct().order_by(STLModel.source).all():
                    if t:
                        child = QTreeWidgetItem([t])
                        child.setData(0, Qt.ItemDataRole.UserRole, ("source", t))
                        types_item.addChild(child)

            if show_path:
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
                self._build_path_tree(paths_item, self._cached_sorted_dirs)
        finally:
            session.close()

        self._filter_tree.expandAll()

    @staticmethod
    def _build_path_tree(root_item: QTreeWidgetItem, dirs: list[str]) -> None:
        """Build a nested tree of directory segments under *root_item*."""
        # node_map: full path string → QTreeWidgetItem
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
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, ("path", full))
                parent.addChild(item)
                node_map[full] = item

    def _load_table(self, author_filter=None, tag_filter=None, type_filter=None, path_filter=None):
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
            )
            if author_filter:
                q = q.filter(STLModel.author == author_filter)
            if type_filter:
                q = q.filter(STLModel.source == type_filter)
            if tag_filter:
                q = q.filter(STLModel.tags.any(Tag.name == tag_filter))
            if path_filter:
                prefix = path_filter.rstrip("/") + "/"
                q = q.filter(STLModel.archive_path.like(prefix + "%"))

            for stl in q.order_by(STLModel.title).all():
                thumb_item = QStandardItem()
                thumb_item.setData(stl.id, MODEL_ID_ROLE)
                thumb_item.setEditable(False)

                tags_str = ", ".join(t.name for t in stl.tags)
                path_item = QStandardItem(stl.archive_path or "")
                path_item.setEditable(False)
                seq_str = str(stl.sequence) if stl.sequence is not None else ""
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
        if col == COL_THUMB:
            return
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

    # ------------------------------------------------------------------
    def _on_search(self, text: str):
        self._proxy.setFilterFixedString(text)

    def _on_filter_clicked(self, item: QTreeWidgetItem, _col: int):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            self._load_table()
            return
        kind, value = data
        if kind == "author":
            self._load_table(author_filter=value)
        elif kind == "tag":
            self._load_table(tag_filter=value)
        elif kind == "source":
            self._load_table(type_filter=value)
        elif kind == "path":
            self._load_table(path_filter=value)

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

    def _restore_header_state(self):
        settings = QSettings()
        state = settings.value("browser/header_state")
        if state:
            self._table.horizontalHeader().restoreState(state)
            # Always keep COL_THUMB hidden after restoring
            self._table.setColumnHidden(COL_THUMB, True)
            # restoreState() preserves saved resize modes — re-assert Interactive so
            # columns that were previously Stretch remain manually resizable.
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(COL_PATH,  QHeaderView.ResizeMode.Interactive)
        else:
            # Set reasonable defaults when no saved state exists
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
            if col in (COL_AUTHOR, COL_TAGS, COL_SOURCE, COL_PATH):
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

        from app.ui.config_dialog import get_file_sorting_enabled
        sort_act = None
        if get_file_sorting_enabled():
            menu.addSeparator()
            sort_act = menu.addAction(
                style.standardIcon(SP.SP_DirOpenIcon),
                f"Sort {len(selected_ids)} file(s) into directory…",
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
        elif add_tag_act and action == add_tag_act:
            self.add_tag()
        elif edit_tags_act and action == edit_tags_act:
            self._open_tag_editor()
        elif edit_categories_act and action == edit_categories_act:
            self._open_category_editor()
        elif edit_subcategories_act and action == edit_subcategories_act:
            self._open_subcategory_editor()

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
