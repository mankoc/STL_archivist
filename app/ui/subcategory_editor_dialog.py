"""
Subcategory editor dialog — list, add, rename, delete and merge Subcategory entries.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QSpinBox, QComboBox,
)

from app.database.db import session_scope, get_session
from app.database.models import Category, Subcategory
from app.utils.string_utils import levenshtein as _levenshtein, soundex as _soundex


class SubcategoryEditorDialog(QDialog):
    """Dialog for managing Subcategory lookup values grouped by their parent Category."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Subcategory Editor")
        self.resize(520, 520)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Subcategory", "Category", "Models"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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
            for sc in (session.query(Subcategory)
                       .join(Subcategory.category)
                       .order_by(Category.name, Subcategory.name)
                       .all()):
                row = self._table.rowCount()
                self._table.insertRow(row)

                name_item = QTableWidgetItem(sc.name)
                name_item.setData(Qt.ItemDataRole.UserRole, sc.id)

                cat_item = QTableWidgetItem(sc.category.name)
                cat_item.setData(Qt.ItemDataRole.UserRole, sc.category_id)

                count_item = QTableWidgetItem(str(len(sc.models)))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self._table.setItem(row, 0, name_item)
                self._table.setItem(row, 1, cat_item)
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

    def _category_names(self) -> list[str]:
        session = get_session()
        try:
            return [cat.name for cat in
                    session.query(Category).order_by(Category.name).all()]
        finally:
            session.close()

    # ------------------------------------------------------------------
    def _add(self) -> None:
        """Prompt for a parent category and new subcategory name, then insert into DB."""
        category_names = self._category_names()
        if not category_names:
            QMessageBox.warning(self, "Add Subcategory",
                                "No categories exist. Add a category first.")
            return

        # Pre-select the category of the currently selected row
        preselect = 0
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) == 1:
            row = list(rows)[0]
            cat_name = self._table.item(row, 1).text()
            if cat_name in category_names:
                preselect = category_names.index(cat_name)

        parent_name, ok = QInputDialog.getItem(
            self, "Add Subcategory", "Parent category:", category_names, preselect, False)
        if not ok:
            return

        name, ok = QInputDialog.getText(
            self, "Add Subcategory", f"Subcategory name under '{parent_name}':")
        if not ok or not name.strip():
            return
        name = name.strip()

        with session_scope() as session:
            cat = session.query(Category).filter_by(name=parent_name).first()
            if cat is None:
                return
            if session.query(Subcategory).filter_by(
                    name=name, category_id=cat.id).first():
                QMessageBox.warning(
                    self, "Add Subcategory",
                    f"Subcategory '{name}' already exists under '{parent_name}'.")
                return
            session.add(Subcategory(name=name, category_id=cat.id))
        self._load()

    def _rename(self, *_) -> None:
        """Rename the selected subcategory, checking for duplicates within its parent."""
        sc_id = self._selected_id()
        if sc_id is None:
            QMessageBox.information(self, "Rename",
                                    "Select exactly one subcategory to rename.")
            return

        session = get_session()
        try:
            sc = session.get(Subcategory, sc_id)
            old_name = sc.name
            category_id = sc.category_id
        finally:
            session.close()

        new_name, ok = QInputDialog.getText(
            self, "Rename Subcategory", "New name:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == old_name:
            return

        with session_scope() as session:
            if session.query(Subcategory).filter_by(
                    name=new_name, category_id=category_id).first():
                QMessageBox.warning(
                    self, "Rename Subcategory",
                    f"Subcategory '{new_name}' already exists under this category.")
                return
            session.get(Subcategory, sc_id).name = new_name
        self._load()

    def _delete(self) -> None:
        """Delete the selected subcategory after user confirmation."""
        sc_id = self._selected_id()
        if sc_id is None:
            QMessageBox.information(self, "Delete",
                                    "Select exactly one subcategory to delete.")
            return

        session = get_session()
        try:
            sc = session.get(Subcategory, sc_id)
            name = sc.name
            count = len(sc.models)
        finally:
            session.close()

        msg = f"Delete subcategory '{name}'?"
        if count:
            msg += f"\n\nIt is currently used by {count} model(s)."
        reply = QMessageBox.question(
            self, "Delete Subcategory", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            session.delete(session.get(Subcategory, sc_id))
        self._load()

    def _merge_duplicates(self) -> None:
        """Open the merge-duplicate-subcategories sub-dialog and reload on close."""
        dlg = _MergeSubcategoriesDialog(parent=self)
        dlg.exec()
        self._load()


class _MergeSubcategoriesDialog(QDialog):
    """Sub-dialog for merging similar-sounding subcategories within the same parent category."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Merge Duplicate Subcategories")
        self.resize(580, 420)
        self._build_ui()
        self._scan()

    def _build_ui(self) -> None:
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

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            ["Subcategory A", "Subcategory B", "Category", "Distance"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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
            # Group subcategories by parent category
            cat_groups: dict[int, tuple[str, list]] = {}
            for sc in (session.query(Subcategory)
                       .join(Subcategory.category)
                       .order_by(Category.name, Subcategory.name)
                       .all()):
                if sc.category_id not in cat_groups:
                    cat_groups[sc.category_id] = (sc.category.name, [])
                cat_groups[sc.category_id][1].append(
                    (sc.id, sc.name, len(sc.models)))
        finally:
            session.close()

        pairs: list[tuple] = []
        if method == "Levenshtein":
            threshold = self._thresh_spin.value()
            for cat_name, subcategories in cat_groups.values():
                for i in range(len(subcategories)):
                    for j in range(i + 1, len(subcategories)):
                        d = _levenshtein(subcategories[i][1], subcategories[j][1])
                        if d <= threshold:
                            pairs.append((subcategories[i], subcategories[j],
                                          cat_name, str(d)))
            pairs.sort(key=lambda x: int(x[3]))
            col_header = "Distance"
        else:  # Soundex
            for cat_name, subcategories in cat_groups.values():
                groups: dict[str, list] = {}
                for sc in subcategories:
                    groups.setdefault(_soundex(sc[1]), []).append(sc)
                for members in groups.values():
                    for i in range(len(members)):
                        for j in range(i + 1, len(members)):
                            code = _soundex(members[i][1])
                            pairs.append((members[i], members[j],
                                          cat_name, code))
            pairs.sort(key=lambda x: x[3])
            col_header = "Soundex"

        self._table.setHorizontalHeaderItem(3, QTableWidgetItem(col_header))
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for (id_a, name_a, count_a), (id_b, name_b, count_b), cat_name, score in pairs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item_a = QTableWidgetItem(f"{name_a} ({count_a})")
            item_a.setData(Qt.ItemDataRole.UserRole, id_a)
            item_b = QTableWidgetItem(f"{name_b} ({count_b})")
            item_b.setData(Qt.ItemDataRole.UserRole, id_b)
            cat_item = QTableWidgetItem(cat_name)
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            score_item = QTableWidgetItem(score)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, item_a)
            self._table.setItem(row, 1, item_b)
            self._table.setItem(row, 2, cat_item)
            self._table.setItem(row, 3, score_item)
        self._table.setSortingEnabled(True)

    def _merge_selected(self):
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            QMessageBox.information(self, "Merge",
                                    "Select exactly one pair to merge.")
            return
        row = list(rows)[0]
        item_a = self._table.item(row, 0)
        item_b = self._table.item(row, 1)
        name_a = item_a.text().rsplit(" (", 1)[0]
        name_b = item_b.text().rsplit(" (", 1)[0]
        id_a = item_a.data(Qt.ItemDataRole.UserRole)
        id_b = item_b.data(Qt.ItemDataRole.UserRole)

        chosen, ok = QInputDialog.getItem(
            self, "Merge Subcategories",
            f"Merging '{name_a}' and '{name_b}'.\nWhich name to keep?",
            [name_a, name_b], 0, False,
        )
        if not ok:
            return

        keep_id, delete_id = (id_a, id_b) if chosen == name_a else (id_b, id_a)

        with session_scope() as session:
            keep_sc = session.get(Subcategory, keep_id)
            delete_sc = session.get(Subcategory, delete_id)
            for model in list(delete_sc.models):
                model.subcategory = keep_sc
            session.delete(delete_sc)

        self._scan()
