"""
Column Distribution dialog — lets the user pick any catalogue column and view
a bar chart of how models are distributed across its distinct values.
"""
import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui._charts import BarChartWidget

logger = logging.getLogger(__name__)

_MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# (display label, internal key)
COLUMN_CHOICES: list[tuple[str, str]] = [
    ("Category",       "category"),
    ("Collection",     "collection"),
    ("Creator",        "creator"),
    ("Presupports",    "presupports"),
    ("Release",        "release"),
    ("Release Month",  "release_month"),
    ("Release Year",   "release_year"),
    ("Scale",          "scale"),
    ("Sculptor",       "sculptor"),
    ("Source",         "source"),
    ("Subcollection",  "subcollection"),
    ("Tag",            "tag"),
]


def _query_column(session, key: str, top_n: int | None) -> tuple[list[tuple[str, int]], int]:
    """Return (data, total_models) for the chosen column.

    *data* is a list of (label, count) pairs sorted by count descending,
    capped at *top_n* entries.  *total_models* is the overall model count
    used to compute percentages.
    """
    from sqlalchemy import func
    from app.database.models import (
        STLModel, Sculptor, Collection, Subcollection, Category,
        Release, Scale, Tag, model_tags,
    )

    total: int = session.query(func.count(STLModel.id)).scalar() or 0

    def _cap(q):
        return q.limit(top_n) if top_n is not None else q

    def _direct(col, null_label="(none)", filter_null=False):
        q = session.query(col, func.count(STLModel.id).label("n"))
        if filter_null:
            q = q.filter(col.isnot(None))
        q = _cap(q.group_by(col).order_by(func.count(STLModel.id).desc()))
        return [(str(v) if v is not None else null_label, cnt) for v, cnt in q.all()]

    if key == "source":
        data = _direct(STLModel.source, null_label="(unknown)")

    elif key == "release_year":
        data = _direct(STLModel.release_year, filter_null=True)

    elif key == "release_month":
        raw = _direct(STLModel.release_month, filter_null=True)
        data = [
            (_MONTH_NAMES.get(int(v), v) if v.isdigit() else v, cnt)
            for v, cnt in raw
        ]

    elif key == "presupports":
        data = _direct(STLModel.presupports, null_label="(not set)")

    elif key == "creator":
        data = _direct(STLModel.creator, null_label="(unknown)")

    elif key == "sculptor":
        q = (session.query(Sculptor.name, func.count(STLModel.id).label("n"))
             .join(STLModel, STLModel.sculptor_id == Sculptor.id)
             .group_by(Sculptor.id)
             .order_by(func.count(STLModel.id).desc()))
        data = list(_cap(q).all())

    elif key == "collection":
        q = (session.query(Collection.name, func.count(STLModel.id).label("n"))
             .join(STLModel, STLModel.collection_id == Collection.id)
             .group_by(Collection.id)
             .order_by(func.count(STLModel.id).desc()))
        data = list(_cap(q).all())

    elif key == "subcollection":
        q = (session.query(Subcollection.name, func.count(STLModel.id).label("n"))
             .join(STLModel, STLModel.subcollection_id == Subcollection.id)
             .group_by(Subcollection.id)
             .order_by(func.count(STLModel.id).desc()))
        data = list(_cap(q).all())

    elif key == "category":
        q = (session.query(Category.name, func.count(STLModel.id).label("n"))
             .join(STLModel, STLModel.category_id == Category.id)
             .group_by(Category.id)
             .order_by(func.count(STLModel.id).desc()))
        data = list(_cap(q).all())

    elif key == "release":
        q = (session.query(Release.name, func.count(STLModel.id).label("n"))
             .join(STLModel, STLModel.release_id == Release.id)
             .group_by(Release.id)
             .order_by(func.count(STLModel.id).desc()))
        data = list(_cap(q).all())

    elif key == "scale":
        q = (session.query(Scale.name, func.count(STLModel.id).label("n"))
             .join(STLModel, STLModel.scale_id == Scale.id)
             .group_by(Scale.id)
             .order_by(func.count(STLModel.id).desc()))
        data = list(_cap(q).all())

    elif key == "tag":
        q = (session.query(Tag.name, func.count(model_tags.c.model_id).label("n"))
             .join(model_tags, model_tags.c.tag_id == Tag.id)
             .group_by(Tag.id)
             .order_by(func.count(model_tags.c.model_id).desc()))
        data = list(_cap(q).all())

    else:
        data = []

    return data, total


class ColumnStatsWorker(QThread):
    finished = pyqtSignal(list, int)   # data, total

    def __init__(self, column_key: str, top_n: int | None, parent=None) -> None:
        super().__init__(parent)
        self._key   = column_key
        self._top_n = top_n

    def run(self) -> None:
        from app.database.db import session_scope
        try:
            with session_scope() as s:
                data, total = _query_column(s, self._key, self._top_n)
            self.finished.emit(data, total)
        except Exception as exc:
            logger.error("ColumnStatsWorker error: %s", exc, exc_info=True)
            self.finished.emit([], 0)


class ColumnStatsDialog(QDialog):
    """Dialog that shows a bar-chart distribution for any model column."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Column Distribution"))
        self.resize(700, 560)
        self.setModal(True)
        self._worker: ColumnStatsWorker | None = None
        self._chart_box: QGroupBox | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Options row ──────────────────────────────────────────────
        opt_row = QHBoxLayout()

        opt_row.addWidget(QLabel(self.tr("Column:")))
        self._col_combo = QComboBox()
        for label, _ in COLUMN_CHOICES:
            self._col_combo.addItem(label)
        opt_row.addWidget(self._col_combo)

        opt_row.addSpacing(16)
        opt_row.addWidget(QLabel(self.tr("Top N:")))
        self._top_n_spin = QSpinBox()
        self._top_n_spin.setRange(1, 99999)
        self._top_n_spin.setValue(20)
        self._top_n_spin.setSuffix(self.tr(" entries"))
        self._top_n_spin.setKeyboardTracking(False)
        opt_row.addWidget(self._top_n_spin)

        self._show_all_cb = QCheckBox(self.tr("Show all"))
        self._show_all_cb.toggled.connect(self._top_n_spin.setDisabled)
        opt_row.addWidget(self._show_all_cb)

        opt_row.addSpacing(16)
        style = QApplication.style()
        SP = style.StandardPixmap
        self._show_btn = QPushButton(style.standardIcon(SP.SP_BrowserReload), self.tr("Show"))
        self._show_btn.clicked.connect(self._run)
        opt_row.addWidget(self._show_btn)

        self._col_combo.currentIndexChanged.connect(self._run)

        opt_row.addStretch()
        root.addLayout(opt_row)

        # ── Chart scroll area ─────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(self._scroll.Shape.NoFrame)
        self._scroll_content = QWidget()
        self._scroll_layout  = QVBoxLayout(self._scroll_content)
        self._scroll_layout.setSpacing(0)
        self._scroll_layout.addStretch()
        self._scroll.setWidget(self._scroll_content)
        root.addWidget(self._scroll, stretch=1)

        # ── Status + close ───────────────────────────────────────────
        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(style.standardIcon(SP.SP_DialogCloseButton), self.tr("Close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._run()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        col_idx = self._col_combo.currentIndex()
        _, key  = COLUMN_CHOICES[col_idx]
        top_n   = None if self._show_all_cb.isChecked() else self._top_n_spin.value()
        label   = self._col_combo.currentText()

        self._show_btn.setEnabled(False)
        self._status_lbl.setText(f"Loading distribution for '{label}'…")
        self._clear_chart()

        self._worker = ColumnStatsWorker(key, top_n)
        self._worker.finished.connect(lambda data, total: self._on_finished(data, total, label))
        self._worker.start()

    def _on_finished(self, data: list, total: int, label: str) -> None:
        self._worker = None
        self._show_btn.setEnabled(True)

        if not data:
            self._status_lbl.setText(self.tr("No data found for this column."))
            return

        self._status_lbl.setText("")
        self._show_chart(data, total, label)

    # ------------------------------------------------------------------
    def _clear_chart(self) -> None:
        if self._chart_box is not None:
            self._scroll_layout.removeWidget(self._chart_box)
            self._chart_box.deleteLater()
            self._chart_box = None

    def _show_chart(self, data: list, total: int, label: str) -> None:
        top_n = None if self._show_all_cb.isChecked() else self._top_n_spin.value()
        title = f"Distribution by {label}" + (f"  (top {top_n})" if top_n and len(data) == top_n else "")

        box = QGroupBox(title)
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(8, 6, 8, 8)
        vbox.addWidget(BarChartWidget(data, total))
        self._chart_box = box

        # Insert before the trailing stretch
        self._scroll_layout.insertWidget(0, box)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.wait(2000)
        super().closeEvent(event)
