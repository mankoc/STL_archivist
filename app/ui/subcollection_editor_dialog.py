"""
Subcollection editor dialog — list, add, rename, delete and merge Subcollection entries.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QSpinBox, QComboBox,
)

from app.database.db import session_scope, get_session
from app.database.models import Collection, Subcollection
from app.utils.name_dedup import find_similar_pairs_grouped


class SubcollectionEditorDialog(QDialog):
    """Dialog for managing Subcollection lookup values grouped by their parent Collection."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Subcollection Editor"))
        self.resize(520, 520)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([self.tr("Subcollection"), self.tr("Collection"), self.tr("Models")])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._rename)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()

        add_btn = QPushButton(self.tr("Add…"))
        add_btn.clicked.connect(self._add)
        btn_row.addWidget(add_btn)

        rename_btn = QPushButton(self.tr("Rename…"))
        rename_btn.clicked.connect(self._rename)
        btn_row.addWidget(rename_btn)

        delete_btn = QPushButton(self.tr("Delete"))
        delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(delete_btn)

        merge_btn = QPushButton(self.tr("Merge Duplicates…"))
        merge_btn.clicked.connect(self._merge_duplicates)
        btn_row.addWidget(merge_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.accept)
        root.addWidget(close_btn)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        session = get_session()
        try:
            for sc in (session.query(Subcollection)
                       .join(Subcollection.collection)
                       .order_by(Collection.name, Subcollection.name)
                       .all()):
                row = self._table.rowCount()
                self._table.insertRow(row)

                name_item = QTableWidgetItem(sc.name)
                name_item.setData(Qt.ItemDataRole.UserRole, sc.id)

                col_item = QTableWidgetItem(sc.collection.name)
                col_item.setData(Qt.ItemDataRole.UserRole, sc.collection_id)

                count_item = QTableWidgetItem(str(len(sc.models)))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self._table.setItem(row, 0, name_item)
                self._table.setItem(row, 1, col_item)
                self._table.setItem(row, 2, count_item)
        finally:
            session.close()
        self._table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    def _selected_id(self) -> int | None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        return self._table.item(list(rows)[0], 0).data(Qt.ItemDataRole.UserRole)

    def _collection_names(self) -> list[str]:
        session = get_session()
        try:
            return [col.name for col in
                    session.query(Collection).order_by(Collection.name).all()]
        finally:
            session.close()

    # ------------------------------------------------------------------
    def _add(self) -> None:
        """Prompt for a parent collection and new subcollection name, then insert into DB."""
        collection_names = self._collection_names()
        if not collection_names:
            QMessageBox.warning(self, self.tr("Add Subcollection"),
                                self.tr("No collections exist. Add a collection first."))
            return

        # Pre-select the collection of the currently selected row
        preselect = 0
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) == 1:
            row = list(rows)[0]
            col_name = self._table.item(row, 1).text()
            if col_name in collection_names:
                preselect = collection_names.index(col_name)

        parent_name, ok = QInputDialog.getItem(
            self, self.tr("Add Subcollection"), self.tr("Parent collection:"), collection_names, preselect, False)
        if not ok:
            return

        name, ok = QInputDialog.getText(
            self, self.tr("Add Subcollection"), f"Subcollection name under '{parent_name}':")
        if not ok or not name.strip():
            return
        name = name.strip()

        with session_scope() as session:
            col = session.query(Collection).filter_by(name=parent_name).first()
            if col is None:
                return
            if session.query(Subcollection).filter_by(
                    name=name, collection_id=col.id).first():
                QMessageBox.warning(
                    self, self.tr("Add Subcollection"),
                    f"Subcollection '{name}' already exists under '{parent_name}'.")
                return
            session.add(Subcollection(name=name, collection_id=col.id))
        self._load()

    def _rename(self, *_) -> None:
        """Rename the selected subcollection, checking for duplicates within its parent."""
        sc_id = self._selected_id()
        if sc_id is None:
            QMessageBox.information(self, self.tr("Rename"),
                                    self.tr("Select exactly one subcollection to rename."))
            return

        session = get_session()
        try:
            sc = session.get(Subcollection, sc_id)
            old_name = sc.name
            collection_id = sc.collection_id
        finally:
            session.close()

        new_name, ok = QInputDialog.getText(
            self, self.tr("Rename Subcollection"), self.tr("New name:"), text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == old_name:
            return

        with session_scope() as session:
            if session.query(Subcollection).filter_by(
                    name=new_name, collection_id=collection_id).first():
                QMessageBox.warning(
                    self, self.tr("Rename Subcollection"),
                    f"Subcollection '{new_name}' already exists under this collection.")
                return
            session.get(Subcollection, sc_id).name = new_name
        self._load()

    def _delete(self) -> None:
        """Delete the selected subcollection after user confirmation."""
        sc_id = self._selected_id()
        if sc_id is None:
            QMessageBox.information(self, self.tr("Delete"),
                                    self.tr("Select exactly one subcollection to delete."))
            return

        session = get_session()
        try:
            sc = session.get(Subcollection, sc_id)
            name = sc.name
            count = len(sc.models)
        finally:
            session.close()

        msg = f"Delete subcollection '{name}'?"
        if count:
            msg += f"\n\nIt is currently used by {count} model(s)."
        reply = QMessageBox.question(
            self, self.tr("Delete Subcollection"), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            session.delete(session.get(Subcollection, sc_id))
        self._load()

    def _merge_duplicates(self) -> None:
        """Open the merge-duplicate-subcollections sub-dialog and reload on close."""
        dlg = _MergeSubcollectionsDialog(parent=self)
        dlg.exec()
        self._load()


class _MergeSubcollectionsDialog(QDialog):
    """Sub-dialog for merging similar-sounding subcollections within the same parent collection."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Merge Duplicate Subcollections"))
        self.resize(580, 420)
        self._build_ui()
        self._scan()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel(self.tr("Method:")))
        self._method_combo = QComboBox()
        self._method_combo.addItems(["Levenshtein", "Soundex"])
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        thresh_row.addWidget(self._method_combo)
        self._thresh_label = QLabel(self.tr("Max distance:"))
        thresh_row.addWidget(self._thresh_label)
        self._thresh_spin = QSpinBox()
        self._thresh_spin.setRange(1, 5)
        self._thresh_spin.setValue(2)
        thresh_row.addWidget(self._thresh_spin)
        scan_btn = QPushButton(self.tr("Scan"))
        scan_btn.clicked.connect(self._scan)
        thresh_row.addWidget(scan_btn)
        thresh_row.addStretch()
        root.addLayout(thresh_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            [self.tr("Subcollection A"), self.tr("Subcollection B"), self.tr("Collection"), self.tr("Distance")])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()
        merge_btn = QPushButton(self.tr("Merge Selected…"))
        merge_btn.clicked.connect(self._merge_selected)
        btn_row.addWidget(merge_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.accept)
        root.addWidget(close_btn)

    def _on_method_changed(self):
        is_lev = self._method_combo.currentText() == "Levenshtein"
        self._thresh_label.setVisible(is_lev)
        self._thresh_spin.setVisible(is_lev)

    def _scan(self):
        method = self._method_combo.currentText()
        session = get_session()
        try:
            # Group subcollections by parent collection
            col_groups: dict[int, tuple[str, list]] = {}
            for sc in (session.query(Subcollection)
                       .join(Subcollection.collection)
                       .order_by(Collection.name, Subcollection.name)
                       .all()):
                if sc.collection_id not in col_groups:
                    col_groups[sc.collection_id] = (sc.collection.name, [])
                col_groups[sc.collection_id][1].append(
                    (sc.id, sc.name, len(sc.models)))
        finally:
            session.close()

        pairs = find_similar_pairs_grouped(col_groups, method, self._thresh_spin.value())
        col_header = "Distance" if method == "Levenshtein" else "Soundex"

        self._table.setHorizontalHeaderItem(3, QTableWidgetItem(col_header))
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for (id_a, name_a, count_a), (id_b, name_b, count_b), col_name, score in pairs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item_a = QTableWidgetItem(f"{name_a} ({count_a})")
            item_a.setData(Qt.ItemDataRole.UserRole, id_a)
            item_b = QTableWidgetItem(f"{name_b} ({count_b})")
            item_b.setData(Qt.ItemDataRole.UserRole, id_b)
            col_item = QTableWidgetItem(col_name)
            col_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item = QTableWidgetItem(score)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, item_a)
            self._table.setItem(row, 1, item_b)
            self._table.setItem(row, 2, col_item)
            self._table.setItem(row, 3, score_item)
        self._table.setSortingEnabled(True)

    def _merge_selected(self):
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            QMessageBox.information(self, self.tr("Merge"),
                                    self.tr("Select exactly one pair to merge."))
            return
        row = list(rows)[0]
        item_a = self._table.item(row, 0)
        item_b = self._table.item(row, 1)
        name_a = item_a.text().rsplit(" (", 1)[0]
        name_b = item_b.text().rsplit(" (", 1)[0]
        id_a = item_a.data(Qt.ItemDataRole.UserRole)
        id_b = item_b.data(Qt.ItemDataRole.UserRole)

        chosen, ok = QInputDialog.getItem(
            self, self.tr("Merge Subcollections"),
            f"Merging '{name_a}' and '{name_b}'.\nWhich name to keep?",
            [name_a, name_b], 0, False,
        )
        if not ok:
            return

        keep_id, delete_id = (id_a, id_b) if chosen == name_a else (id_b, id_a)

        with session_scope() as session:
            keep_sc = session.get(Subcollection, keep_id)
            delete_sc = session.get(Subcollection, delete_id)
            for model in list(delete_sc.models):
                model.subcollection = keep_sc
            session.delete(delete_sc)

        self._scan()
