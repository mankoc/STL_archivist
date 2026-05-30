"""
Tag editor dialog — list, add, rename, and delete tags.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QSpinBox, QComboBox,
)


from app.database.db import session_scope, get_session
from app.database.models import Tag
from app.utils.string_utils import levenshtein as _levenshtein, soundex as _soundex


class TagEditorDialog(QDialog):
    """Dialog for managing global Tag values: list, add, rename, delete, and merge similar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Tag Editor"))
        self.resize(420, 520)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels([self.tr("Tag"), self.tr("Models")])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._rename_tag)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()

        add_btn = QPushButton(self.tr("Add…"))
        add_btn.clicked.connect(self._add_tag)
        btn_row.addWidget(add_btn)

        rename_btn = QPushButton(self.tr("Rename…"))
        rename_btn.clicked.connect(self._rename_tag)
        btn_row.addWidget(rename_btn)

        delete_btn = QPushButton(self.tr("Delete"))
        delete_btn.clicked.connect(self._delete_tag)
        btn_row.addWidget(delete_btn)

        merge_btn = QPushButton(self.tr("Merge Similar…"))
        merge_btn.clicked.connect(self._merge_similar)
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
            for tag in session.query(Tag).order_by(Tag.name).all():
                row = self._table.rowCount()
                self._table.insertRow(row)

                name_item = QTableWidgetItem(tag.name)
                name_item.setData(Qt.ItemDataRole.UserRole, tag.id)

                count_item = QTableWidgetItem(str(len(tag.models)))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self._table.setItem(row, 0, name_item)
                self._table.setItem(row, 1, count_item)
        finally:
            session.close()
        self._table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    def _selected_tag_id(self) -> int | None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        return self._table.item(list(rows)[0], 0).data(Qt.ItemDataRole.UserRole)

    # ------------------------------------------------------------------
    def _add_tag(self) -> None:
        """Prompt for a new tag name (lower-cased) and insert it into the DB."""
        name, ok = QInputDialog.getText(self, self.tr("Add Tag"), self.tr("Tag name:"))
        if not ok or not name.strip():
            return
        name = name.strip().lower()
        with session_scope() as session:
            if session.query(Tag).filter_by(name=name).first():
                QMessageBox.warning(self, self.tr("Add Tag"), f"Tag '{name}' already exists.")
                return
            session.add(Tag(name=name))
        self._load()

    def _rename_tag(self) -> None:
        """Rename the selected tag, rejecting duplicates."""
        tag_id = self._selected_tag_id()
        if tag_id is None:
            QMessageBox.information(self, self.tr("Rename"), self.tr("Select exactly one tag to rename."))
            return

        session = get_session()
        try:
            tag = session.get(Tag, tag_id)
            old_name = tag.name
        finally:
            session.close()

        new_name, ok = QInputDialog.getText(self, self.tr("Rename Tag"), self.tr("New name:"), text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip().lower()
        if new_name == old_name:
            return

        with session_scope() as session:
            if session.query(Tag).filter_by(name=new_name).first():
                QMessageBox.warning(self, self.tr("Rename Tag"), f"Tag '{new_name}' already exists.")
                return
            session.get(Tag, tag_id).name = new_name
        self._load()

    def _delete_tag(self) -> None:
        """Delete the selected tag after user confirmation."""
        tag_id = self._selected_tag_id()
        if tag_id is None:
            QMessageBox.information(self, self.tr("Delete"), self.tr("Select exactly one tag to delete."))
            return

        session = get_session()
        try:
            tag = session.get(Tag, tag_id)
            name = tag.name
            count = len(tag.models)
        finally:
            session.close()

        msg = f"Delete tag '{name}'?"
        if count:
            msg += f"\n\nIt is currently used by {count} model(s)."
        reply = QMessageBox.question(
            self, self.tr("Delete Tag"), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            tag = session.get(Tag, tag_id)
            session.delete(tag)
        self._load()

    def _merge_similar(self) -> None:
        """Open the merge-similar-tags sub-dialog and reload the table on close."""
        dlg = _MergeSimilarDialog(parent=self)
        dlg.exec()
        self._load()


class _MergeSimilarDialog(QDialog):
    """Sub-dialog for finding and merging similar-sounding tags.

    Uses either Levenshtein distance (threshold configurable) or Soundex
    grouping to identify candidates, then lets the user merge a selected pair.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Merge Similar Tags"))
        self.resize(520, 420)
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

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([self.tr("Tag A"), self.tr("Tag B"), self.tr("Distance")])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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

    def _on_method_changed(self) -> None:
        """Show or hide the threshold spinner depending on the selected method."""
        is_lev = self._method_combo.currentText() == "Levenshtein"
        self._thresh_label.setVisible(is_lev)
        self._thresh_spin.setVisible(is_lev)

    def _scan(self) -> None:
        """Scan all tags and populate the table with similar pairs."""
        method = self._method_combo.currentText()
        session = get_session()
        try:
            tags = [
                (t.id, t.name, len(t.models))
                for t in session.query(Tag).order_by(Tag.name).all()
            ]
        finally:
            session.close()

        pairs: list[tuple] = []
        if method == "Levenshtein":
            threshold = self._thresh_spin.value()
            for i in range(len(tags)):
                for j in range(i + 1, len(tags)):
                    d = _levenshtein(tags[i][1], tags[j][1])
                    if d <= threshold:
                        pairs.append((tags[i], tags[j], str(d)))
            pairs.sort(key=lambda x: int(x[2]))
            col_header = "Distance"
        else:  # Soundex
            codes = {tid: _soundex(name) for tid, name, _ in tags}
            groups: dict[str, list] = {}
            for tid, name, count in tags:
                code = codes[tid]
                groups.setdefault(code, []).append((tid, name, count))
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

    def _merge_selected(self) -> None:
        """Merge the two tags in the selected row, re-assigning models to the kept tag."""
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            QMessageBox.information(self, self.tr("Merge"), self.tr("Select exactly one pair to merge."))
            return
        row = list(rows)[0]
        item_a = self._table.item(row, 0)
        item_b = self._table.item(row, 1)
        name_a = item_a.text().rsplit(" (", 1)[0]
        name_b = item_b.text().rsplit(" (", 1)[0]
        id_a = item_a.data(Qt.ItemDataRole.UserRole)
        id_b = item_b.data(Qt.ItemDataRole.UserRole)

        chosen, ok = QInputDialog.getItem(
            self, self.tr("Merge Tags"),
            f"Merging '{name_a}' and '{name_b}'.\nWhich name to keep?",
            [name_a, name_b], 0, False,
        )
        if not ok:
            return

        keep_id, delete_id = (id_a, id_b) if chosen == name_a else (id_b, id_a)

        with session_scope() as session:
            keep_tag = session.get(Tag, keep_id)
            delete_tag = session.get(Tag, delete_id)
            for model in list(delete_tag.models):
                if keep_tag not in model.tags:
                    model.tags.append(keep_tag)
            session.delete(delete_tag)

        self._scan()
