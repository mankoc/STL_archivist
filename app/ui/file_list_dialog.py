from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QHBoxLayout, QLabel, QComboBox
)

from app.ui.config_dialog import get_match_highlight_color
from app.services.model_views import model_label, stl_files
from app.utils.file_matching import match_files

def format_size(size_bytes: int) -> str:
    """Format an integer byte count into a human-readable string (e.g. 1.2 MB)."""
    if size_bytes is None:
        return "Unknown"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

class FileListDialog(QDialog):
    def __init__(self, model_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Archive Contents"))
        self.resize(800, 600)
        self.model_id = model_id
        
        layout = QVBoxLayout(self)
        
        title = model_label(model_id)
        if title is not None:
            layout.addWidget(QLabel(f"<b>Archive:</b> {title}"))
        
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([self.tr("Filename"), self.tr("Size"), self.tr("Hash")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        
        self.load_files()
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def load_files(self) -> None:
        files = stl_files(self.model_id)
        self.table.setRowCount(len(files))
        for i, f in enumerate(files):
            self.table.setItem(i, 0, QTableWidgetItem(f.filename))

            size_str = format_size(f.file_size) if f.file_size is not None else "Unknown"
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 1, size_item)

            self.table.setItem(i, 2, QTableWidgetItem(f.archive_hash or ""))

class FileComparisonDialog(QDialog):
    def __init__(self, model_id_a: int, model_id_b: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Compare Archive Contents"))
        self.resize(1200, 700)
        self.model_id_a = model_id_a
        self.model_id_b = model_id_b
        
        self.files_a = []
        self.files_b = []
        
        layout = QVBoxLayout(self)
        
        header_layout = QHBoxLayout()
        self.lbl_a = QLabel()
        self.lbl_b = QLabel()
        header_layout.addWidget(self.lbl_a)
        header_layout.addStretch()
        header_layout.addWidget(self.lbl_b)
        layout.addLayout(header_layout)
        
        ctrl_layout = QHBoxLayout()
        ctrl_layout.addWidget(QLabel(self.tr("Match by:")))
        self.match_combo = QComboBox()
        self.match_combo.addItems(["Filename", "Size", "Hash"])
        self.match_combo.currentIndexChanged.connect(self.rebuild_table)
        ctrl_layout.addWidget(self.match_combo)
        ctrl_layout.addStretch()
        layout.addLayout(ctrl_layout)
        
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([self.tr("Filename A"), self.tr("Size A"), self.tr("Match"), self.tr("Filename B"), self.tr("Size B")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        
        self.load_data()
        self.rebuild_table()
        
    def load_data(self) -> None:
        la, lb = model_label(self.model_id_a), model_label(self.model_id_b)
        if la is not None:
            self.lbl_a.setText(f"<b>A:</b> {la}")
        if lb is not None:
            self.lbl_b.setText(f"<b>B:</b> {lb}")
        self.files_a = stl_files(self.model_id_a)
        self.files_b = stl_files(self.model_id_b)

    def rebuild_table(self) -> None:
        mode = self.match_combo.currentText()
        rows = match_files(self.files_a, self.files_b, key=mode.lower())
            
        self.table.setRowCount(len(rows))
        for i, (fa, fb) in enumerate(rows):
            if fa:
                self.table.setItem(i, 0, QTableWidgetItem(fa.filename))
                item_size = QTableWidgetItem(format_size(fa.file_size) if fa.file_size is not None else "")
                item_size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, 1, item_size)
            else:
                self.table.setItem(i, 0, QTableWidgetItem(""))
                self.table.setItem(i, 1, QTableWidgetItem(""))
                
            if fa and fb:
                item_match = QTableWidgetItem("==")
                item_match.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                c = QColor(get_match_highlight_color()); c.setAlpha(50)
                item_match.setBackground(c)
                self.table.setItem(i, 2, item_match)
            else:
                self.table.setItem(i, 2, QTableWidgetItem(""))
                
            if fb:
                self.table.setItem(i, 3, QTableWidgetItem(fb.filename))
                item_size = QTableWidgetItem(format_size(fb.file_size) if fb.file_size is not None else "")
                item_size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, 4, item_size)
            else:
                self.table.setItem(i, 3, QTableWidgetItem(""))
                self.table.setItem(i, 4, QTableWidgetItem(""))
