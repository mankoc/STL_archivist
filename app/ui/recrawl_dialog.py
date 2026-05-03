from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHBoxLayout,
    QPushButton, QLabel, QHeaderView,
)
from PyQt6.QtCore import Qt


class RecrawlPreviewDialog(QDialog):
    """Simple dialog that shows a field-by-field preview of changes.

    Accepting the dialog means the caller should apply the new data.
    """

    def __init__(self, title: str, changes: list[tuple[str, str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 400)

        root = QVBoxLayout(self)

        if not changes:
            root.addWidget(QLabel("No changes detected."))
        else:
            t = QTableWidget(0, 3)
            t.setHorizontalHeaderLabels(["Field", "Current", "New"])
            t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            t.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
            for row, (field, old, new) in enumerate(changes):
                t.insertRow(row)
                t.setItem(row, 0, QTableWidgetItem(field))
                t.setItem(row, 1, QTableWidgetItem(old or ""))
                t.setItem(row, 2, QTableWidgetItem(new or ""))
            root.addWidget(t)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._accept_btn = QPushButton("Accept")
        self._cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(self._accept_btn)
        btn_row.addWidget(self._cancel_btn)
        root.addLayout(btn_row)

        self._accept_btn.clicked.connect(self.accept)
        self._cancel_btn.clicked.connect(self.reject)
