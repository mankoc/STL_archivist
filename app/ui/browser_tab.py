"""
Tab 2 – Model browser.
Left panel: filter tree (Author / Tags / Type).
Centre: sortable table with thumbnail, title, author, type, date, tags, path.
Right panel: image thumbnail sidebar (populated on single-click).
Right-click or double-click: Edit Model.
"""
from pathlib import Path as FSPath

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QSize, QStringListModel, QEvent, pyqtSignal
from PyQt6.QtGui import QStandardItemModel, QStandardItem, QPixmap, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTableView,
    QLineEdit, QLabel, QHeaderView, QAbstractItemView,
    QMenu, QScrollArea, QFrame, QInputDialog, QMessageBox,
    QStyledItemDelegate, QComboBox, QCompleter,
    QDialog, QSizePolicy,
)

from app.database.db import get_session, session_scope
from app.database.models import STLModel, Tag
from app.utils.thumbnailer import pixmap_from_bytes

# Column indices
COL_THUMB  = 0
COL_TITLE  = 1
COL_AUTHOR = 2
COL_TYPE   = 3
COL_DATE   = 4
COL_TAGS   = 5
COL_PATH   = 6
_HEADERS = ["", "Title", "Author", "Type", "Date", "Tags", "Path"]

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


class TypeDelegate(QStyledItemDelegate):
    """Editable QComboBox populated with all existing type values."""

    def createEditor(self, parent, option, index):
        session = get_session()
        try:
            types = sorted(filter(None, (
                v for (v,) in session.query(STLModel.type).distinct().all()
            )))
        finally:
            session.close()

        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(types)
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._thumb_pixmaps: list[QPixmap] = []  # original (unscaled) pixmaps
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
        self._table.horizontalHeader().setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(COL_PATH,  QHeaderView.ResizeMode.Stretch)
        self._table.setColumnHidden(COL_THUMB, True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setItemDelegateForColumn(COL_TYPE, TypeDelegate(self._table))
        self._table.setItemDelegateForColumn(COL_TAGS, TagDelegate(self._table))
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.horizontalHeader().customContextMenuRequested.connect(self._header_context_menu)
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

    # ------------------------------------------------------------------
    def refresh(self):
        """Reload all data from the database."""
        self._populate_filter_tree()
        self._load_table()
        self._clear_thumbnails()

    # ------------------------------------------------------------------
    def _populate_filter_tree(self):
        self._filter_tree.clear()

        from PyQt6.QtWidgets import QApplication as _App
        _style = _App.style()
        _SP = _style.StandardPixmap
        _icon_author = _style.standardIcon(_SP.SP_FileDialogDetailedView)
        _icon_tag    = _style.standardIcon(_SP.SP_FileDialogListView)
        _icon_type   = _style.standardIcon(_SP.SP_DirHomeIcon)
        _icon_path   = _style.standardIcon(_SP.SP_DirOpenIcon)

        session = get_session()
        try:
            authors_item = QTreeWidgetItem(["Authors"])
            authors_item.setIcon(0, _icon_author)
            self._filter_tree.addTopLevelItem(authors_item)
            for (author,) in session.query(STLModel.author).distinct().order_by(STLModel.author).all():
                if author:
                    child = QTreeWidgetItem([author])
                    child.setData(0, Qt.ItemDataRole.UserRole, ("author", author))
                    authors_item.addChild(child)

            tags_item = QTreeWidgetItem(["Tags"])
            tags_item.setIcon(0, _icon_tag)
            self._filter_tree.addTopLevelItem(tags_item)
            for tag in session.query(Tag).order_by(Tag.name).all():
                child = QTreeWidgetItem([tag.name])
                child.setData(0, Qt.ItemDataRole.UserRole, ("tag", tag.name))
                tags_item.addChild(child)

            types_item = QTreeWidgetItem(["Type"])
            types_item.setIcon(0, _icon_type)
            self._filter_tree.addTopLevelItem(types_item)
            for (t,) in session.query(STLModel.type).distinct().order_by(STLModel.type).all():
                if t:
                    child = QTreeWidgetItem([t])
                    child.setData(0, Qt.ItemDataRole.UserRole, ("type", t))
                    types_item.addChild(child)

            paths_item = QTreeWidgetItem(["Path"])
            paths_item.setIcon(0, _icon_path)
            self._filter_tree.addTopLevelItem(paths_item)
            dirs: set[str] = set()
            for (ap,) in session.query(STLModel.archive_path).all():
                if ap:
                    dirs.add(str(FSPath(ap).parent))
            self._build_path_tree(paths_item, sorted(dirs))
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
            q = session.query(STLModel)
            if author_filter:
                q = q.filter(STLModel.author == author_filter)
            if type_filter:
                q = q.filter(STLModel.type == type_filter)
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
                row = [
                    thumb_item,
                    QStandardItem(stl.title or ""),
                    QStandardItem(stl.author or ""),
                    QStandardItem(stl.type or ""),
                    QStandardItem(stl.release_date or ""),
                    QStandardItem(tags_str),
                    path_item,
                ]
                self._model.appendRow(row)
        finally:
            session.close()
            self._loading = False

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
            elif col == COL_TYPE:
                stl.type = value
            elif col == COL_DATE:
                stl.release_date = value
            elif col == COL_TAGS:
                stl.tags.clear()
                for name in [t.strip().lower() for t in (value or "").split(",") if t.strip()]:
                    tag = session.query(Tag).filter_by(name=name).first()
                    if tag is None:
                        tag = Tag(name=name)
                        session.add(tag)
                        session.flush()
                    stl.tags.append(tag)

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
        elif kind == "type":
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

    def _context_menu(self, pos):
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        src_index = self._proxy.mapToSource(index)
        model_id = self._model.item(src_index.row(), COL_THUMB).data(MODEL_ID_ROLE)

        selected_ids = self._selected_model_ids()

        menu = QMenu(self)
        edit_act = menu.addAction("Edit Model…")
        edit_act.setVisible(len(selected_ids) == 1)
        mass_act = menu.addAction(f"Mass Edit {len(selected_ids)} model(s)…")
        mass_act.setVisible(len(selected_ids) > 1)
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == edit_act:
            self._open_editor(model_id)
        elif action == mass_act:
            self._open_mass_editor(selected_ids)

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
                px = pixmap_from_bytes(img.thumbnail)
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
                scaled = px.scaled(w, w,
                                   Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
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
