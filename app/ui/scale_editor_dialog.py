"""
Scale editor dialog — list, add, rename and delete Scale entries.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QDialogButtonBox,
)

from app.database.db import session_scope, get_session
from app.database.models import Scale


class ScaleEditorDialog(QDialog):
    """Dialog for managing Scale lookup values (e.g. 28mm, 32mm, 1/35)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Scale Editor"))
        self.resize(320, 400)
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels([self.tr("Scale"), self.tr("Models")])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
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

        btn_row.addStretch()
        root.addLayout(btn_row)

        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.accept)
        root.addWidget(close_btn)

    def _load(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        session = get_session()
        try:
            for sc in session.query(Scale).order_by(Scale.name).all():
                row = self._table.rowCount()
                self._table.insertRow(row)
                name_item = QTableWidgetItem(sc.name)
                name_item.setData(Qt.ItemDataRole.UserRole, sc.id)
                count_item = QTableWidgetItem(str(len(sc.models)))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, 0, name_item)
                self._table.setItem(row, 1, count_item)
        finally:
            session.close()
        self._table.setSortingEnabled(True)

    def _selected_id(self) -> int | None:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        if len(rows) != 1:
            return None
        return self._table.item(list(rows)[0], 0).data(Qt.ItemDataRole.UserRole)

    def _add(self) -> None:
        """Prompt for a new scale name and add it to the database."""
        name, ok = QInputDialog.getText(self, self.tr("Add Scale"), self.tr("Scale name (e.g. 28mm):"))
        if not ok or not name.strip():
            return
        name = name.strip()
        with session_scope() as session:
            if session.query(Scale).filter_by(name=name).first():
                QMessageBox.warning(self, self.tr("Add Scale"), f"Scale '{name}' already exists.")
                return
            session.add(Scale(name=name))
        self._load()

    def _rename(self, *_) -> None:
        """Rename the selected scale, checking for duplicates first."""
        sc_id = self._selected_id()
        if sc_id is None:
            QMessageBox.information(self, self.tr("Rename"), self.tr("Select exactly one scale to rename."))
            return
        session = get_session()
        try:
            old_name = session.get(Scale, sc_id).name
        finally:
            session.close()
        new_name, ok = QInputDialog.getText(self, self.tr("Rename Scale"), self.tr("New name:"), text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()
        with session_scope() as session:
            if session.query(Scale).filter_by(name=new_name).first():
                QMessageBox.warning(self, self.tr("Rename Scale"), f"Scale '{new_name}' already exists.")
                return
            session.get(Scale, sc_id).name = new_name
        self._load()

    def _delete(self) -> None:
        """Delete the selected scale after user confirmation."""
        sc_id = self._selected_id()
        if sc_id is None:
            QMessageBox.information(self, self.tr("Delete"), self.tr("Select exactly one scale to delete."))
            return
        session = get_session()
        try:
            sc = session.get(Scale, sc_id)
            name, count = sc.name, len(sc.models)
        finally:
            session.close()
        msg = f"Delete scale '{name}'?"
        if count:
            msg += f"\n\nIt is currently used by {count} model(s)."
        reply = QMessageBox.question(
            self, self.tr("Delete Scale"), msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        with session_scope() as session:
            session.delete(session.get(Scale, sc_id))
        self._load()
