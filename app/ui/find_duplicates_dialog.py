"""
Find Metadata Duplicates — search for models with identical or similar field values.
"""
import logging
from collections import defaultdict

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.utils.string_utils import levenshtein as _lev, soundex as _sdx

logger = logging.getLogger(__name__)

# (display_name, dotted_attr_path) — one level of relationship supported
_COLUMNS = [
    ("Title",          "title"),
    ("Creator",        "creator"),
    ("Source",         "source"),
    ("Sculptor",       "sculptor.name"),
    ("Collection",     "collection.name"),
    ("Subcollection",  "subcollection.name"),
    ("Category",       "category.name"),
    ("Release",        "release.name"),
    ("Scale",          "scales.name"),
]

_COL_GROUP  = 0
_COL_COLUMN = 1
_COL_ID     = 2
_COL_TITLE  = 3
_COL_VALUE  = 4
_COL_SCORE  = 5


def _resolve(obj, chain: str) -> str:
    """Resolve a dotted attribute path such as 'sculptor.name' on *obj*."""
    parts = chain.split(".", 1)
    val = getattr(obj, parts[0], None)
    if val is None:
        return ""
    if isinstance(val, (list, set)):
        # Relationship collection (e.g. scales) — join the sub-attribute values.
        sub = parts[1] if len(parts) == 2 else "name"
        return ", ".join(sorted(str(getattr(x, sub, x) or "") for x in val))
    if len(parts) == 2:
        return str(getattr(val, parts[1], "") or "")
    return str(val)


class _SearchWorker(QThread):
    """Background thread that compares model values and emits duplicate groups."""

    progress = pyqtSignal(int, int)   # (current, total) — steps are columns
    search_finished = pyqtSignal(list)       # list of group dicts

    def __init__(self, columns, method: str, threshold: int, parent=None) -> None:
        super().__init__(parent)
        self._columns   = columns    # list of (display_name, attr_chain)
        self._method    = method     # "exact" | "levenshtein" | "soundex"
        self._threshold = threshold  # Levenshtein max distance
        self._stop      = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from sqlalchemy.orm import joinedload, selectinload

        from app.database.db import get_session
        from app.database.models import STLModel

        session = get_session()
        try:
            models = (
                session.query(STLModel)
                .options(
                    joinedload(STLModel.sculptor),
                    joinedload(STLModel.collection),
                    joinedload(STLModel.subcollection),
                    joinedload(STLModel.category),
                    joinedload(STLModel.release),
                    selectinload(STLModel.scales),
                )
                .all()
            )
            # Materialise all needed values while the session is open
            rows: list[dict] = []
            for m in models:
                row: dict = {"id": m.id, "title": m.title or ""}
                for disp, chain in self._columns:
                    row[disp] = _resolve(m, chain)
                rows.append(row)
        finally:
            session.close()

        groups: list[dict] = []
        total = len(self._columns)
        for idx, (disp, _) in enumerate(self._columns):
            if self._stop:
                break
            self.progress.emit(idx, total)
            if self._method == "exact":
                groups.extend(self._find_exact(rows, disp))
            elif self._method == "levenshtein":
                groups.extend(self._find_levenshtein(rows, disp))
            else:
                groups.extend(self._find_soundex(rows, disp))

        self.progress.emit(total, total)
        self.search_finished.emit(groups)

    # ------------------------------------------------------------------
    def _find_exact(self, rows: list[dict], col: str) -> list[dict]:
        buckets: dict[str, list] = defaultdict(list)
        for r in rows:
            v = r[col].strip()
            if v:
                buckets[v].append(r)
        return [
            {"column": col, "similarity": "Exact", "models": list(b)}
            for b in buckets.values()
            if len(b) > 1
        ]

    def _find_levenshtein(self, rows: list[dict], col: str) -> list[dict]:
        pairs: list[dict] = []
        # Normalise once to avoid repeated work
        vals = [(r, r[col].strip().lower()) for r in rows if r[col].strip()]
        n = len(vals)
        for i in range(n):
            if self._stop:
                break
            ri, vi = vals[i]
            for j in range(i + 1, n):
                rj, vj = vals[j]
                # Skip early if length difference already exceeds threshold
                if abs(len(vi) - len(vj)) > self._threshold:
                    continue
                dist = _lev(vi, vj)
                if 0 < dist <= self._threshold:
                    pairs.append({
                        "column": col,
                        "similarity": f"Levenshtein: {dist}",
                        "models": [ri, rj],
                    })
        return pairs

    def _find_soundex(self, rows: list[dict], col: str) -> list[dict]:
        buckets: dict[str, list[tuple]] = defaultdict(list)
        for r in rows:
            v = r[col].strip()
            if v:
                code = _sdx(v)
                if code != "0000":
                    buckets[code].append((r, v))
        groups: list[dict] = []
        for code, items in buckets.items():
            if len(items) < 2:
                continue
            # Skip groups where every entry is identical (already shown by exact match)
            if len({v for _, v in items}) == 1:
                continue
            groups.append({
                "column": col,
                "similarity": f"Soundex: {code}",
                "models": [r for r, _ in items],
            })
        return groups


class FindDuplicatesDialog(QDialog):
    """Dialog for finding models that share identical or phonetically similar field values."""

    show_models_requested = pyqtSignal(list)  # emits list[int] of model IDs

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Find Metadata Duplicates"))
        self.resize(1000, 680)
        self._worker: _SearchWorker | None = None
        self._groups: list[dict] = []
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ---- Column selection ----
        col_box = QGroupBox(self.tr("Columns to check"))
        col_row = QHBoxLayout(col_box)
        self._col_checks: dict[str, QCheckBox] = {}
        for disp, _ in _COLUMNS:
            cb = QCheckBox(disp)
            cb.setChecked(disp == "Title")
            col_row.addWidget(cb)
            self._col_checks[disp] = cb
        col_row.addStretch()
        root.addWidget(col_box)

        # ---- Match method ----
        method_box = QGroupBox(self.tr("Match method"))
        method_row = QHBoxLayout(method_box)
        self._rb_exact = QRadioButton(self.tr("Exact match"))
        self._rb_lev   = QRadioButton(self.tr("Levenshtein"))
        self._rb_sdx   = QRadioButton(self.tr("Soundex (phonetic)"))
        self._rb_exact.setChecked(True)
        self._lev_label = QLabel(self.tr("Max distance:"))
        self._lev_spin  = QSpinBox()
        self._lev_spin.setRange(1, 20)
        self._lev_spin.setValue(3)
        self._lev_label.setEnabled(False)
        self._lev_spin.setEnabled(False)
        for w in (self._rb_exact, self._rb_lev, self._rb_sdx,
                  self._lev_label, self._lev_spin):
            method_row.addWidget(w)
        method_row.addStretch()
        root.addWidget(method_box)

        self._rb_lev.toggled.connect(self._sync_threshold_widgets)

        # ---- Controls row ----
        ctrl = QHBoxLayout()
        self._find_btn = QPushButton(self.tr("Find Duplicates"))
        self._find_btn.clicked.connect(self._run_search)
        ctrl.addWidget(self._find_btn)
        self._stop_btn = QPushButton(self.tr("Stop"))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_search)
        ctrl.addWidget(self._stop_btn)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        ctrl.addWidget(self._progress, 1)
        root.addLayout(ctrl)

        # ---- Status label ----
        self._status = QLabel("")
        root.addWidget(self._status)

        # ---- Results table ----
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            [self.tr("Group"), self.tr("Column"), self.tr("ID"), self.tr("Title"), self.tr("Value"), self.tr("Match")]
        )
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._table)

        # ---- Bottom row: show buttons + close ----
        bottom = QHBoxLayout()
        self._show_btn = QPushButton(self.tr("Show Group in Main Table"))
        self._show_btn.setEnabled(False)
        self._show_btn.setToolTip(
            self.tr("Select one or more rows from the same group, then click to view those models in the main table")
        )
        self._show_btn.clicked.connect(self._show_in_table)
        bottom.addWidget(self._show_btn)
        self._show_all_btn = QPushButton(self.tr("Show All Groups in Main Table"))
        self._show_all_btn.setEnabled(False)
        self._show_all_btn.setToolTip(self.tr("Show all models that appear in any duplicate group"))
        self._show_all_btn.clicked.connect(self._show_all_in_table)
        bottom.addWidget(self._show_all_btn)
        bottom.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    def _sync_threshold_widgets(self) -> None:
        on = self._rb_lev.isChecked()
        self._lev_label.setEnabled(on)
        self._lev_spin.setEnabled(on)

    def _on_selection_changed(self) -> None:
        self._show_btn.setEnabled(bool(self._table.selectedItems()))

    def _run_search(self) -> None:
        selected = [
            (disp, chain)
            for disp, chain in _COLUMNS
            if self._col_checks[disp].isChecked()
        ]
        if not selected:
            QMessageBox.information(self, self.tr("No columns"), self.tr("Please select at least one column."))
            return

        if self._rb_exact.isChecked():
            method = "exact"
        elif self._rb_lev.isChecked():
            method = "levenshtein"
        else:
            method = "soundex"

        self._find_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._show_all_btn.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, len(selected))
        self._progress.setValue(0)
        self._status.setText(self.tr("Searching…"))
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._groups = []

        self._worker = _SearchWorker(selected, method, self._lev_spin.value(), self)
        self._worker.progress.connect(self._on_progress)
        self._worker.search_finished.connect(self._on_finished)
        self._worker.start()

    def _stop_search(self) -> None:
        if self._worker:
            self._worker.stop()
        self._stop_btn.setEnabled(False)
        self._status.setText(self.tr("Stopping…"))

    # ------------------------------------------------------------------
    def _on_progress(self, current: int, total: int) -> None:
        self._progress.setValue(current)

    def _on_finished(self, groups: list) -> None:
        self._progress.setVisible(False)
        self._find_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._groups = groups
        from app.utils.sound import play_notification
        play_notification("duplicates")

        if not groups:
            self._show_all_btn.setEnabled(False)
            self._status.setText(self.tr("No duplicates found."))
            return

        self._show_all_btn.setEnabled(True)

        self._table.setSortingEnabled(False)
        for group_idx, group in enumerate(groups, 1):
            col_name   = group["column"]
            similarity = group["similarity"]
            for model_dict in group["models"]:
                row = self._table.rowCount()
                self._table.insertRow(row)

                grp_item = QTableWidgetItem()
                grp_item.setData(Qt.ItemDataRole.DisplayRole, group_idx)
                self._table.setItem(row, _COL_GROUP, grp_item)

                self._table.setItem(row, _COL_COLUMN, QTableWidgetItem(col_name))

                id_item = QTableWidgetItem()
                id_item.setData(Qt.ItemDataRole.DisplayRole, model_dict["id"])
                self._table.setItem(row, _COL_ID, id_item)

                self._table.setItem(row, _COL_TITLE, QTableWidgetItem(model_dict["title"]))
                self._table.setItem(row, _COL_VALUE, QTableWidgetItem(model_dict[col_name]))
                self._table.setItem(row, _COL_SCORE, QTableWidgetItem(similarity))

        self._table.setSortingEnabled(True)
        n_groups = len(groups)
        n_models = sum(len(g["models"]) for g in groups)
        self._status.setText(
            f"{n_groups} duplicate group{'s' if n_groups != 1 else ''} found "
            f"({n_models} model{'s' if n_models != 1 else ''} affected)"
        )

    # ------------------------------------------------------------------
    def _show_in_table(self) -> None:
        selected_rows = self._table.selectionModel().selectedRows()
        if not selected_rows:
            return

        # Use the group number of the first selected row to gather all IDs in that group
        first_row = selected_rows[0].row()
        grp_item  = self._table.item(first_row, _COL_GROUP)
        if grp_item is None:
            return
        target_group = grp_item.data(Qt.ItemDataRole.DisplayRole)

        # Collect model IDs for every row belonging to that group
        ids: list[int] = []
        for r in range(self._table.rowCount()):
            gi = self._table.item(r, _COL_GROUP)
            if gi and gi.data(Qt.ItemDataRole.DisplayRole) == target_group:
                id_item = self._table.item(r, _COL_ID)
                if id_item is not None:
                    ids.append(id_item.data(Qt.ItemDataRole.DisplayRole))

        if ids:
            self.show_models_requested.emit(ids)

    def _show_all_in_table(self) -> None:
        ids: list[int] = list({
            m["id"]
            for group in self._groups
            for m in group["models"]
        })
        if ids:
            self.show_models_requested.emit(ids)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait()
        super().closeEvent(event)
