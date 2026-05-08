"""
Author editor dialog — list, rename, delete, and merge author/creator names.

The ``creator`` field on STLModel is a plain string; this dialog manipulates it
in bulk (updating every model that shares the same creator name) and keeps the
auxiliary Creator profile table in sync.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QSpinBox, QComboBox,
)

from app.database.db import session_scope, get_session
from app.database.models import STLModel, Creator
from app.utils.string_utils import levenshtein as _levenshtein, soundex as _soundex


class AuthorEditorDialog(QDialog):
    """Dialog for managing author/creator names: list, rename, delete, and merge."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Author Editor")
        self.resize(480, 540)
        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Author", "Models"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.doubleClicked.connect(self._rename_author)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()

        rename_btn = QPushButton("Rename…")
        rename_btn.clicked.connect(self._rename_author)
        btn_row.addWidget(rename_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_author)
        btn_row.addWidget(delete_btn)

        merge_btn = QPushButton("Merge…")
        merge_btn.clicked.connect(self._merge_selected_pair)
        btn_row.addWidget(merge_btn)

        merge_similar_btn = QPushButton("Merge Similar…")
        merge_similar_btn.clicked.connect(self._merge_similar)
        btn_row.addWidget(merge_similar_btn)

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
            rows = (
                session.query(STLModel.creator, STLModel.creator)
                .filter(STLModel.creator.isnot(None))
                .group_by(STLModel.creator)
                .order_by(STLModel.creator)
                .all()
            )
            from sqlalchemy import func
            counts = dict(
                session.query(STLModel.creator, func.count(STLModel.id))
                .filter(STLModel.creator.isnot(None))
                .group_by(STLModel.creator)
                .all()
            )
        finally:
            session.close()

        for (name, _) in rows:
            row = self._table.rowCount()
            self._table.insertRow(row)
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, name)
            count_item = QTableWidgetItem(str(counts.get(name, 0)))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, count_item)

        self._table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    def _selected_name(self) -> str | None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        return self._table.item(list(rows)[0], 0).data(Qt.ItemDataRole.UserRole)

    def _selected_names(self) -> list[str]:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in rows]

    # ------------------------------------------------------------------
    def _rename_author(self) -> None:
        old_name = self._selected_name()
        if old_name is None:
            QMessageBox.information(self, "Rename", "Select exactly one author to rename.")
            return

        new_name, ok = QInputDialog.getText(self, "Rename Author", "New name:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name == old_name:
            return

        with session_scope() as session:
            existing = (
                session.query(STLModel)
                .filter_by(creator=new_name)
                .first()
            )
            if existing:
                reply = QMessageBox.question(
                    self, "Name Conflict",
                    f"'{new_name}' already exists. Merge both authors under that name?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            (
                session.query(STLModel)
                .filter_by(creator=old_name)
                .update({"creator": new_name})
            )
            # Rename or merge the Creator profile record
            old_profile = session.query(Creator).filter_by(name=old_name).first()
            if old_profile:
                new_profile = session.query(Creator).filter_by(name=new_name).first()
                if new_profile is None:
                    old_profile.name = new_name
                else:
                    session.delete(old_profile)

        self._load()

    # ------------------------------------------------------------------
    def _delete_author(self) -> None:
        name = self._selected_name()
        if name is None:
            QMessageBox.information(self, "Delete", "Select exactly one author to delete.")
            return

        session = get_session()
        try:
            count = (
                session.query(STLModel)
                .filter_by(creator=name)
                .count()
            )
        finally:
            session.close()

        reply = QMessageBox.question(
            self, "Delete Author",
            f"Clear the creator field on {count} model(s) currently attributed to '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with session_scope() as session:
            session.query(STLModel).filter_by(creator=name).update({"creator": None})
            profile = session.query(Creator).filter_by(name=name).first()
            if profile:
                session.delete(profile)

        self._load()

    # ------------------------------------------------------------------
    def _merge_selected_pair(self) -> None:
        """Merge two authors selected in the table (no fuzzy search required)."""
        names = self._selected_names()
        if len(names) != 2:
            QMessageBox.information(
                self, "Merge",
                "Select exactly two authors to merge (hold Ctrl to multi-select).",
            )
            return
        self._do_merge(names[0], names[1])

    def _merge_similar(self) -> None:
        dlg = _MergeSimilarAuthorsDialog(parent=self)
        dlg.exec()
        self._load()

    # ------------------------------------------------------------------
    def _do_merge(self, name_a: str, name_b: str) -> None:
        chosen, ok = QInputDialog.getItem(
            self, "Merge Authors",
            f"Merging '{name_a}' and '{name_b}'.\nWhich name to keep?",
            [name_a, name_b], 0, False,
        )
        if not ok:
            return

        keep_name, delete_name = (name_a, name_b) if chosen == name_a else (name_b, name_a)

        with session_scope() as session:
            session.query(STLModel).filter_by(creator=delete_name).update({"creator": keep_name})
            old_profile = session.query(Creator).filter_by(name=delete_name).first()
            if old_profile:
                session.delete(old_profile)

        self._load()


class _MergeSimilarAuthorsDialog(QDialog):
    """Sub-dialog for finding and merging similar-sounding author names."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Merge Similar Authors")
        self.resize(560, 440)
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

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Author A", "Author B", "Distance"])
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

    def _on_method_changed(self) -> None:
        is_lev = self._method_combo.currentText() == "Levenshtein"
        self._thresh_label.setVisible(is_lev)
        self._thresh_spin.setVisible(is_lev)

    def _scan(self) -> None:
        from sqlalchemy import func
        session = get_session()
        try:
            authors = [
                (name, count)
                for name, count in (
                    session.query(STLModel.creator, func.count(STLModel.id))
                    .filter(STLModel.creator.isnot(None))
                    .group_by(STLModel.creator)
                    .order_by(STLModel.creator)
                    .all()
                )
            ]
        finally:
            session.close()

        method = self._method_combo.currentText()
        pairs: list[tuple] = []

        if method == "Levenshtein":
            threshold = self._thresh_spin.value()
            for i in range(len(authors)):
                for j in range(i + 1, len(authors)):
                    d = _levenshtein(authors[i][0], authors[j][0])
                    if d <= threshold:
                        pairs.append((authors[i], authors[j], str(d)))
            pairs.sort(key=lambda x: int(x[2]))
            col_header = "Distance"
        else:  # Soundex
            codes = {name: _soundex(name) for name, _ in authors}
            groups: dict[str, list] = {}
            for name, count in authors:
                groups.setdefault(codes[name], []).append((name, count))
            for code, members in groups.items():
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        pairs.append((members[i], members[j], code))
            pairs.sort(key=lambda x: x[2])
            col_header = "Soundex"

        self._table.setHorizontalHeaderItem(2, QTableWidgetItem(col_header))
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for (name_a, count_a), (name_b, count_b), score in pairs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item_a = QTableWidgetItem(f"{name_a} ({count_a})")
            item_a.setData(Qt.ItemDataRole.UserRole, name_a)
            item_b = QTableWidgetItem(f"{name_b} ({count_b})")
            item_b.setData(Qt.ItemDataRole.UserRole, name_b)
            score_item = QTableWidgetItem(score)
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, item_a)
            self._table.setItem(row, 1, item_b)
            self._table.setItem(row, 2, score_item)
        self._table.setSortingEnabled(True)

    def _merge_selected(self) -> None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            QMessageBox.information(self, "Merge", "Select exactly one pair to merge.")
            return
        row = list(rows)[0]
        name_a = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        name_b = self._table.item(row, 1).data(Qt.ItemDataRole.UserRole)

        chosen, ok = QInputDialog.getItem(
            self, "Merge Authors",
            f"Merging '{name_a}' and '{name_b}'.\nWhich name to keep?",
            [name_a, name_b], 0, False,
        )
        if not ok:
            return

        keep_name, delete_name = (name_a, name_b) if chosen == name_a else (name_b, name_a)

        with session_scope() as session:
            session.query(STLModel).filter_by(creator=delete_name).update({"creator": keep_name})
            old_profile = session.query(Creator).filter_by(name=delete_name).first()
            if old_profile:
                session.delete(old_profile)

        self._scan()
