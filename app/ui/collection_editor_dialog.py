"""
Collection editor dialog — list, add, rename, delete and merge Collection entries.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QSpinBox, QComboBox,
)

from app.database.db import session_scope, get_session
from app.database.models import Collection, STLModel
from app.utils.string_utils import levenshtein as _levenshtein, soundex as _soundex


class CollectionEditorDialog(QDialog):
    """Dialog for managing Collection lookup values: list, add, rename, delete, and merge."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Collection Editor")
        self.resize(420, 520)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Collection", "Models"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._rename)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()

        add_btn = QPushButton("Add…")
        add_btn.clicked.connect(self._add)
        btn_row.addWidget(add_btn)

        rename_btn = QPushButton("Rename…")
        rename_btn.clicked.connect(self._rename)
        btn_row.addWidget(rename_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(delete_btn)

        merge_btn = QPushButton("Merge Duplicates…")
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
            for col in session.query(Collection).order_by(Collection.name).all():
                row = self._table.rowCount()
                self._table.insertRow(row)

                name_item = QTableWidgetItem(col.name)
                name_item.setData(Qt.ItemDataRole.UserRole, col.id)

                count_item = QTableWidgetItem(str(len(col.models)))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self._table.setItem(row, 0, name_item)
                self._table.setItem(row, 1, count_item)
        finally:
            session.close()
        self._table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    def _selected_id(self) -> int | None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        return self._table.item(list(rows)[0], 0).data(Qt.ItemDataRole.UserRole)

    # ------------------------------------------------------------------
    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Collection", "Collection name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        with session_scope() as session:
            if session.query(Collection).filter_by(name=name).first():
                QMessageBox.warning(self, "Add Collection", f"Collection '{name}' already exists.")
                return
            session.add(Collection(name=name))
        self._load()

    def _rename(self, *_) -> None:
        col_id = self._selected_id()
        if col_id is None:
            QMessageBox.information(self, "Rename", "Select exactly one collection to rename.")
            return

        session = get_session()
        try:
            old_name = session.get(Collection, col_id).name
        finally:
            session.close()

        new_name, ok = QInputDialog.getText(self, "Rename Collection", "New name:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == old_name:
            return

        with session_scope() as session:
            if session.query(Collection).filter_by(name=new_name).first():
                QMessageBox.warning(self, "Rename Collection", f"Collection '{new_name}' already exists.")
                return
            session.get(Collection, col_id).name = new_name
        self._load()

    def _delete(self) -> None:
        col_id = self._selected_id()
        if col_id is None:
            QMessageBox.information(self, "Delete", "Select exactly one collection to delete.")
            return

        session = get_session()
        try:
            col = session.get(Collection, col_id)
            name = col.name
            count = len(col.models)
            sub_count = len(col.subcollections)
        finally:
            session.close()

        msg = f"Delete collection '{name}'?"
        if count:
            msg += f"\n\nIt is currently used by {count} model(s)."
        if sub_count:
            msg += f"\nDeleting it will also remove its {sub_count} subcollection(s)."
        reply = QMessageBox.question(
            self, "Delete Collection", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            session.delete(session.get(Collection, col_id))
        self._load()

    def _merge_duplicates(self) -> None:
        dlg = _MergeCollectionsDialog(parent=self)
        dlg.exec()
        self._load()


class _MergeCollectionsDialog(QDialog):
    """Sub-dialog for finding and merging similar-sounding collections."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Merge Duplicate Collections")
        self.resize(520, 420)
        self._build_ui()
        self._scan()

    def _build_ui(self):
        root = QVBoxLayout(self)

        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel("Method:"))
        self._method_combo = QComboBox()
        self._method_combo.addItems(["Levenshtein", "Soundex"])
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        thresh_row.addWidget(self._method_combo)
        self._thresh_label = QLabel("Max distance:")
        thresh_row.addWidget(self._thresh_label)
        self._thresh_spin = QSpinBox()
        self._thresh_spin.setRange(1, 5)
        self._thresh_spin.setValue(2)
        thresh_row.addWidget(self._thresh_spin)
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self._scan)
        thresh_row.addWidget(scan_btn)
        thresh_row.addStretch()
        root.addLayout(thresh_row)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Collection A", "Collection B", "Distance"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()
        merge_btn = QPushButton("Merge Selected…")
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
            collections = [
                (col.id, col.name, len(col.models))
                for col in session.query(Collection).order_by(Collection.name).all()
            ]
        finally:
            session.close()

        pairs: list[tuple] = []
        if method == "Levenshtein":
            threshold = self._thresh_spin.value()
            for i in range(len(collections)):
                for j in range(i + 1, len(collections)):
                    d = _levenshtein(collections[i][1], collections[j][1])
                    if d <= threshold:
                        pairs.append((collections[i], collections[j], str(d)))
            pairs.sort(key=lambda x: int(x[2]))
            col_header = "Distance"
        else:
            codes = {cid: _soundex(name) for cid, name, _ in collections}
            groups: dict[str, list] = {}
            for cid, name, count in collections:
                groups.setdefault(codes[cid], []).append((cid, name, count))
            for code, members in groups.items():
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        pairs.append((members[i], members[j], code))
            pairs.sort(key=lambda x: x[2])
            col_header = "Soundex"

        self._table.setHorizontalHeaderItem(2, QTableWidgetItem(col_header))
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for (id_a, name_a, count_a), (id_b, name_b, count_b), score in pairs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item_a = QTableWidgetItem(f"{name_a} ({count_a})")
            item_a.setData(Qt.ItemDataRole.UserRole, id_a)
            item_b = QTableWidgetItem(f"{name_b} ({count_b})")
            item_b.setData(Qt.ItemDataRole.UserRole, id_b)
            score_item = QTableWidgetItem(score)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, item_a)
            self._table.setItem(row, 1, item_b)
            self._table.setItem(row, 2, score_item)
        self._table.setSortingEnabled(True)

    def _merge_selected(self):
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            QMessageBox.information(self, "Merge", "Select exactly one pair to merge.")
            return
        row = list(rows)[0]
        item_a = self._table.item(row, 0)
        item_b = self._table.item(row, 1)
        name_a = item_a.text().rsplit(" (", 1)[0]
        name_b = item_b.text().rsplit(" (", 1)[0]
        id_a = item_a.data(Qt.ItemDataRole.UserRole)
        id_b = item_b.data(Qt.ItemDataRole.UserRole)

        chosen, ok = QInputDialog.getItem(
            self, "Merge Collections",
            f"Merging '{name_a}' and '{name_b}'.\nWhich name to keep?",
            [name_a, name_b], 0, False,
        )
        if not ok:
            return

        keep_id, delete_id = (id_a, id_b) if chosen == name_a else (id_b, id_a)

        with session_scope() as session:
            keep_col = session.get(Collection, keep_id)
            delete_col = session.get(Collection, delete_id)
            for model in list(delete_col.models):
                model.collection = keep_col
                model.subcollection = None
            session.delete(delete_col)

        self._scan()
