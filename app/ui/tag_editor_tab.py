"""
Tab 4 – Tag editor.
Shows all tags with usage count; supports rename, merge, and delete.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QLabel, QHeaderView,
    QAbstractItemView,
)

from app.database.db import session_scope, get_session
from app.database.models import Tag, STLModel, model_tags


class TagEditorTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        root.addWidget(QLabel(
            "Select one tag to rename/delete. "
            "Select two or more tags to merge them into one."
        ))

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Tag", "Models"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename_tag)
        btn_row.addWidget(rename_btn)

        merge_btn = QPushButton("Merge selected → first")
        merge_btn.clicked.connect(self._merge_tags)
        btn_row.addWidget(merge_btn)

        delete_btn = QPushButton("Delete unused")
        delete_btn.clicked.connect(self._delete_unused)
        btn_row.addWidget(delete_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    def refresh(self):
        self._table.setRowCount(0)
        session = get_session()
        try:
            tags = session.query(Tag).order_by(Tag.name).all()
            for tag in tags:
                count = len(tag.models)
                row = self._table.rowCount()
                self._table.insertRow(row)

                name_item = QTableWidgetItem(tag.name)
                name_item.setData(Qt.ItemDataRole.UserRole, tag.id)
                count_item = QTableWidgetItem(str(count))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                self._table.setItem(row, 0, name_item)
                self._table.setItem(row, 1, count_item)
        finally:
            session.close()

    # ------------------------------------------------------------------
    def _selected_tag_ids(self) -> list[int]:
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        return [
            self._table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            for r in sorted(rows)
        ]

    # ------------------------------------------------------------------
    def _rename_tag(self):
        ids = self._selected_tag_ids()
        if len(ids) != 1:
            QMessageBox.information(self, "Rename", "Select exactly one tag to rename.")
            return

        tag_id = ids[0]
        session = get_session()
        tag = session.get(Tag, tag_id)
        old_name = tag.name
        session.close()

        new_name, ok = QInputDialog.getText(self, "Rename Tag", "New name:", text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip().lower()

        with session_scope() as session:
            # Check for existing tag with same name
            existing = session.query(Tag).filter_by(name=new_name).first()
            tag = session.get(Tag, tag_id)
            if existing and existing.id != tag_id:
                QMessageBox.warning(self, "Rename", f"Tag '{new_name}' already exists. Use Merge instead.")
                return
            tag.name = new_name

        self.refresh()

    def _merge_tags(self):
        ids = self._selected_tag_ids()
        if len(ids) < 2:
            QMessageBox.information(self, "Merge", "Select at least two tags to merge.")
            return

        session = get_session()
        tags = [session.get(Tag, tid) for tid in ids]
        names = [t.name for t in tags]
        session.close()

        target_name, ok = QInputDialog.getItem(
            self, "Merge Tags",
            "Merge all selected tags into:",
            names, editable=False
        )
        if not ok:
            return

        with session_scope() as session:
            target = session.query(Tag).filter_by(name=target_name).first()
            others = [session.get(Tag, tid) for tid in ids if session.get(Tag, tid).name != target_name]

            for src_tag in others:
                for model in list(src_tag.models):
                    if target not in model.tags:
                        model.tags.append(target)
                src_tag.models.clear()
                session.delete(src_tag)

        self.refresh()

    def _delete_unused(self):
        with session_scope() as session:
            unused = [t for t in session.query(Tag).all() if not t.models]
            count = len(unused)
            for t in unused:
                session.delete(t)

        QMessageBox.information(self, "Delete Unused", f"Deleted {count} unused tag(s).")
        self.refresh()
