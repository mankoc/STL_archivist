"""
Tag editor dialog — list, add, rename, and delete tags.
"""
import fnmatch
import re

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QPushButton,
    QInputDialog, QMessageBox, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QSpinBox, QComboBox,
    QListWidget, QListWidgetItem, QLineEdit,
)

# Item-data roles for the merge-similar lists.
_ID_ROLE = Qt.ItemDataRole.UserRole
_NAME_ROLE = Qt.ItemDataRole.UserRole + 1


from app.services import lookup_admin
from app.utils.string_utils import (
    levenshtein as _levenshtein,
    levenshtein_within as _within,
    soundex as _soundex,
)


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

        self._search_edit = QLineEdit()
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setPlaceholderText(self.tr("Filter tags…  (use * and ? as wildcards)"))
        self._search_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self._search_edit)

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

        clear_unused_btn = QPushButton(self.tr("Clear Unused…"))
        clear_unused_btn.setToolTip(self.tr("Delete all tags with 0 models"))
        clear_unused_btn.clicked.connect(self._clear_unused_tags)
        btn_row.addWidget(clear_unused_btn)

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
        for tag_id, tag_name, tag_count in lookup_admin.list_lookups("tag"):
            row = self._table.rowCount()
            self._table.insertRow(row)

            name_item = QTableWidgetItem(tag_name)
            name_item.setData(Qt.ItemDataRole.UserRole, tag_id)

            count_item = QTableWidgetItem(str(tag_count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, count_item)
        self._table.setSortingEnabled(True)
        self._apply_filter()

    # ------------------------------------------------------------------
    def _apply_filter(self) -> None:
        """Show only rows whose tag name matches the wildcard query.

        ``*`` matches any run of characters and ``?`` a single one (case-
        insensitive). A query with no wildcard is treated as a substring match.
        """
        query = self._search_edit.text().strip().lower()
        if not query:
            for row in range(self._table.rowCount()):
                self._table.setRowHidden(row, False)
            return
        if "*" not in query and "?" not in query:
            query = f"*{query}*"
        try:
            rx = re.compile(fnmatch.translate(query))
        except re.error:
            return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            hidden = item is None or rx.match(item.text().lower()) is None
            self._table.setRowHidden(row, hidden)

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
        if not lookup_admin.add("tag", name):
            QMessageBox.warning(self, self.tr("Add Tag"), f"Tag '{name}' already exists.")
            return
        self._load()

    def _rename_tag(self) -> None:
        """Rename the selected tag, rejecting duplicates."""
        tag_id = self._selected_tag_id()
        if tag_id is None:
            QMessageBox.information(self, self.tr("Rename"), self.tr("Select exactly one tag to rename."))
            return

        old_name = lookup_admin.name_of("tag", tag_id)[0]

        new_name, ok = QInputDialog.getText(self, self.tr("Rename Tag"), self.tr("New name:"), text=old_name)
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip().lower()
        if new_name == old_name:
            return

        if not lookup_admin.rename("tag", tag_id, new_name):
            QMessageBox.warning(self, self.tr("Rename Tag"), f"Tag '{new_name}' already exists.")
            return
        self._load()

    def _delete_tag(self) -> None:
        """Delete the selected tag after user confirmation."""
        tag_id = self._selected_tag_id()
        if tag_id is None:
            QMessageBox.information(self, self.tr("Delete"), self.tr("Select exactly one tag to delete."))
            return

        name, count, _subs = lookup_admin.delete_info("tag", tag_id)

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

        lookup_admin.delete("tag", tag_id)
        self._load()

    def _clear_unused_tags(self) -> None:
        """Delete all tags with 0 models after user confirmation."""
        unused_count = lookup_admin.unused_tag_count()

        if unused_count == 0:
            QMessageBox.information(
                self, self.tr("Clear Unused Tags"),
                self.tr("No unused tags found.")
            )
            return

        reply = QMessageBox.question(
            self, self.tr("Clear Unused Tags"),
            self.tr(f"Delete {unused_count} tag(s) with 0 models?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        lookup_admin.delete_unused_tags()
        self._load()

    def _merge_similar(self) -> None:
        """Open the merge-similar-tags sub-dialog and reload the table on close."""
        dlg = _MergeSimilarDialog(parent=self)
        dlg.exec()
        self._load()


def _levenshtein_pairs(tags: list[tuple], threshold: int) -> list[tuple]:
    """Return all ``(tag_a, tag_b, distance)`` pairs within *threshold* edits.

    Prefers rapidfuzz (C++/SIMD, computes the whole distance matrix at once);
    falls back to a pure-Python bounded scan if rapidfuzz / numpy is unavailable.
    """
    try:
        return _levenshtein_pairs_rapidfuzz(tags, threshold)
    except Exception:
        return _levenshtein_pairs_python(tags, threshold)


def _levenshtein_pairs_rapidfuzz(tags: list[tuple], threshold: int) -> list[tuple]:
    import numpy as np
    from rapidfuzz import process
    from rapidfuzz.distance import Levenshtein

    names = [t[1] for t in tags]
    # Distances above the cutoff are stored as threshold+1, so `<= threshold`
    # selects the matches. uint8 keeps the matrix small (n*n bytes).
    mat = process.cdist(
        names, names, scorer=Levenshtein.distance,
        score_cutoff=threshold, dtype=np.uint8, workers=-1,
    )
    ii, jj = np.where(mat <= threshold)
    pairs = [
        (tags[int(i)], tags[int(j)], str(int(mat[i, j])))
        for i, j in zip(ii.tolist(), jj.tolist())
        if i < j
    ]
    pairs.sort(key=lambda x: int(x[2]))
    return pairs


def _levenshtein_pairs_python(tags: list[tuple], threshold: int) -> list[tuple]:
    tags = sorted(tags, key=lambda t: len(t[1]))
    n = len(tags)
    pairs: list[tuple] = []
    for i in range(n):
        name_i = tags[i][1]
        li = len(name_i)
        for j in range(i + 1, n):
            name_j = tags[j][1]
            if len(name_j) - li > threshold:
                break  # sorted by length — no later j can be close enough
            if _within(name_i, name_j, threshold):
                pairs.append((tags[i], tags[j], str(_levenshtein(name_i, name_j))))
    pairs.sort(key=lambda x: int(x[2]))
    return pairs


def _token_fuzzy_pairs(tags: list[tuple], min_score: int) -> list[tuple]:
    """Return ``(tag_a, tag_b, "NN%")`` pairs whose token-set similarity >= *min_score*.

    Catches word reorderings and overlaps (e.g. "spectral warrior" ↔ "warrior
    spectral", "goblin" ↔ "goblin warrior") rather than character edits. Prefers
    rapidfuzz; falls back to a Jaccard token overlap if rapidfuzz/numpy is absent.
    """
    try:
        import numpy as np
        from rapidfuzz import fuzz, process
        names = [t[1] for t in tags]
        mat = process.cdist(
            names, names, scorer=fuzz.token_set_ratio,
            score_cutoff=min_score, dtype=np.uint8, workers=-1,
        )
        ii, jj = np.where(mat >= min_score)
        pairs = [
            (tags[int(i)], tags[int(j)], f"{int(mat[i, j])}%")
            for i, j in zip(ii.tolist(), jj.tolist())
            if i < j
        ]
    except Exception:
        cutoff = min_score / 100.0
        toks = [(t, set(t[1].lower().split())) for t in tags]
        pairs = []
        n = len(toks)
        for i in range(n):
            ti, si = toks[i]
            if not si:
                continue
            for j in range(i + 1, n):
                tj, sj = toks[j]
                if not sj:
                    continue
                jac = len(si & sj) / len(si | sj)
                if jac >= cutoff:
                    pairs.append((ti, tj, f"{int(jac * 100)}%"))

    pairs.sort(key=lambda x: -int(x[2].rstrip("%")))   # most similar first
    return pairs


class _ScanWorker(QThread):
    """Background scan for similar tag pairs (Levenshtein or Soundex).

    Runs off the UI thread because a large library (thousands of tags) makes the
    pairwise comparison expensive. Tag model-counts are fetched in a single
    GROUP BY (not one lazy load per tag), and the Levenshtein scan sorts by name
    length so it can stop comparing once the length gap exceeds the threshold
    (distance is always >= the length difference).
    """

    scan_finished = pyqtSignal(list, str)   # (pairs, column_header)

    def __init__(self, method: str, threshold: int, parent=None) -> None:
        super().__init__(parent)
        self._method = method
        self._threshold = threshold

    def run(self) -> None:
        tags = lookup_admin.tags_for_merge_scan()

        pairs: list[tuple] = []
        if self._method == "Levenshtein":
            pairs = _levenshtein_pairs(tags, self._threshold)
            header = "Distance"
        elif self._method == "Fuzzy (tokens)":
            pairs = _token_fuzzy_pairs(tags, self._threshold)
            header = "Similarity"
        else:  # Soundex
            codes = {tid: _soundex(name) for tid, name, _ in tags}
            groups: dict[str, list] = {}
            for tid, name, count in tags:
                groups.setdefault(codes[tid], []).append((tid, name, count))
            for code, members in groups.items():
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        pairs.append((members[i], members[j], code))
            pairs.sort(key=lambda x: x[2])
            header = "Soundex"

        self.scan_finished.emit(pairs, header)


class _MergeSimilarDialog(QDialog):
    """Sub-dialog for finding and merging similar-sounding tags.

    Uses either Levenshtein distance (threshold configurable) or Soundex
    grouping to identify candidates, then lets the user merge a selected pair.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Merge Similar Tags"))
        self.resize(640, 460)
        self._worker: _ScanWorker | None = None
        self._adj: dict = {}   # tag_id -> [(other_id, name, count, score), …]
        self._build_ui()
        self._scan()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        thresh_row = QHBoxLayout()
        thresh_row.addWidget(QLabel(self.tr("Method:")))
        self._method_combo = QComboBox()
        self._method_combo.addItems(["Levenshtein", "Fuzzy (tokens)", "Soundex"])
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        thresh_row.addWidget(self._method_combo)
        self._thresh_label = QLabel(self.tr("Max distance:"))
        thresh_row.addWidget(self._thresh_label)
        self._thresh_spin = QSpinBox()
        self._thresh_spin.setRange(1, 5)
        self._thresh_spin.setValue(2)
        thresh_row.addWidget(self._thresh_spin)
        self._scan_btn = QPushButton(self.tr("Scan"))
        self._scan_btn.clicked.connect(self._scan)
        thresh_row.addWidget(self._scan_btn)
        self._status_lbl = QLabel("")
        thresh_row.addWidget(self._status_lbl)
        thresh_row.addStretch()
        root.addLayout(thresh_row)

        # Master-detail: pick a value on the left, review/merge its similar tags
        # (checkable) on the right.
        lists_row = QHBoxLayout()

        left_col = QVBoxLayout()
        left_col.addWidget(QLabel(self.tr("Tags with similar matches:")))
        self._value_list = QListWidget()
        self._value_list.currentItemChanged.connect(self._on_value_selected)
        left_col.addWidget(self._value_list)
        lists_row.addLayout(left_col, 1)

        right_col = QVBoxLayout()
        self._similar_header = QLabel(self.tr("Similar tags:"))
        right_col.addWidget(self._similar_header)
        self._similar_list = QListWidget()
        right_col.addWidget(self._similar_list)
        sel_row = QHBoxLayout()
        all_btn = QPushButton(self.tr("Check all"))
        all_btn.clicked.connect(lambda: self._set_all_checked(True))
        none_btn = QPushButton(self.tr("Uncheck all"))
        none_btn.clicked.connect(lambda: self._set_all_checked(False))
        sel_row.addWidget(all_btn)
        sel_row.addWidget(none_btn)
        sel_row.addStretch()
        right_col.addLayout(sel_row)
        lists_row.addLayout(right_col, 1)

        root.addLayout(lists_row, 1)

        btn_row = QHBoxLayout()
        btn_row.addWidget(QLabel(self.tr("Replace with:")))
        self._replace_combo = QComboBox()
        self._replace_combo.setEditable(True)
        self._replace_combo.setMinimumWidth(220)
        self._replace_combo.setToolTip(self.tr(
            "The name the merged tags become. Pick one of the involved tag names "
            "or type a new one."
        ))
        btn_row.addWidget(self._replace_combo)
        self._merge_btn = QPushButton(self.tr("Merge checked into this"))
        self._merge_btn.clicked.connect(self._merge_into_value)
        btn_row.addWidget(self._merge_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        close_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btn.rejected.connect(self.accept)
        root.addWidget(close_btn)

    def _on_method_changed(self) -> None:
        """Reconfigure the threshold control for the selected method."""
        method = self._method_combo.currentText()
        show = method != "Soundex"
        self._thresh_label.setVisible(show)
        self._thresh_spin.setVisible(show)
        if method == "Levenshtein":
            self._thresh_label.setText(self.tr("Max distance:"))
            self._thresh_spin.setSuffix("")
            self._thresh_spin.setRange(1, 5)
            self._thresh_spin.setValue(2)
        elif method == "Fuzzy (tokens)":
            self._thresh_label.setText(self.tr("Min similarity:"))
            self._thresh_spin.setSuffix(" %")
            self._thresh_spin.setRange(50, 100)
            self._thresh_spin.setValue(85)

    def _scan(self) -> None:
        """Start a background scan for similar tag pairs."""
        if self._worker is not None and self._worker.isRunning():
            return
        self._scan_btn.setEnabled(False)
        self._status_lbl.setText(self.tr("Scanning…"))
        self._worker = _ScanWorker(
            self._method_combo.currentText(), self._thresh_spin.value(), self
        )
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.start()

    def _on_scan_finished(self, pairs: list, col_header: str) -> None:
        """Build the similarity adjacency and populate the value list."""
        self._worker = None
        self._scan_btn.setEnabled(True)

        info: dict = {}                 # id -> (name, count)
        adj: dict[object, list] = {}    # id -> [(other_id, name, count, score), …]
        for (id_a, name_a, count_a), (id_b, name_b, count_b), score in pairs:
            info[id_a] = (name_a, count_a)
            info[id_b] = (name_b, count_b)
            adj.setdefault(id_a, []).append((id_b, name_b, count_b, score))
            adj.setdefault(id_b, []).append((id_a, name_a, count_a, score))
        self._adj = adj

        self._status_lbl.setText(self.tr("%d tag(s) with similar matches") % len(adj))
        self._value_list.clear()
        self._similar_list.clear()
        self._similar_header.setText(self.tr("Similar tags:"))
        for tid in sorted(adj, key=lambda t: info[t][0].lower()):
            name, count = info[tid]
            item = QListWidgetItem(f"{name} ({count})")
            item.setData(_ID_ROLE, tid)
            item.setData(_NAME_ROLE, name)
            self._value_list.addItem(item)

    def _on_value_selected(self, current, _previous) -> None:
        """Show the tags similar to the value selected on the left."""
        self._similar_list.clear()
        self._replace_combo.clear()
        if current is None:
            self._similar_header.setText(self.tr("Similar tags:"))
            return
        tid = current.data(_ID_ROLE)
        name = current.data(_NAME_ROLE)
        self._similar_header.setText(
            self.tr("Similar to '%s' — check the ones to merge in:") % name
        )
        similar = sorted(self._adj.get(tid, []), key=lambda x: x[1].lower())
        for oid, oname, ocount, score in similar:
            item = QListWidgetItem(f"{oname} ({ocount})   ·   {score}")
            item.setData(_ID_ROLE, oid)
            item.setData(_NAME_ROLE, oname)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._similar_list.addItem(item)

        # Editable combo of every candidate name to keep (the value + its
        # similar tags), so the user can pick the canonical spelling or type a
        # brand-new one.
        seen: set[str] = set()
        for cand in [name] + [oname for _, oname, _, _ in similar]:
            if cand.lower() not in seen:
                seen.add(cand.lower())
                self._replace_combo.addItem(cand)
        self._replace_combo.setCurrentText(name)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self._similar_list.count()):
            self._similar_list.item(i).setCheckState(state)

    def _merge_into_value(self) -> None:
        """Merge the selected value and every checked similar tag into one name.

        The target name comes from the editable *Replace with* combo, so it may
        be the value's own name, one of the similar tags, or a brand-new name.
        """
        current = self._value_list.currentItem()
        if current is None:
            QMessageBox.information(
                self, self.tr("Merge"), self.tr("Select a value on the left first.")
            )
            return

        target = self._replace_combo.currentText().strip()
        if not target:
            QMessageBox.information(
                self, self.tr("Merge"), self.tr("Enter or pick a name to replace with.")
            )
            return

        # Tags involved: the selected value plus every checked similar tag.
        group: list[tuple] = [(current.data(_ID_ROLE), current.data(_NAME_ROLE))]
        for i in range(self._similar_list.count()):
            item = self._similar_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                group.append((item.data(_ID_ROLE), item.data(_NAME_ROLE)))

        # Tags that will actually be removed (everything whose name differs from
        # the chosen target name).
        doomed = [(tid, name) for tid, name in group if name.lower() != target.lower()]
        if not doomed:
            QMessageBox.information(
                self, self.tr("Merge"),
                self.tr("Nothing to merge — check at least one tag with a different "
                        "name, or change the replacement name."),
            )
            return

        names = ", ".join(n for _, n in doomed)
        reply = QMessageBox.question(
            self, self.tr("Merge Tags"),
            self.tr("Merge %d tag(s) into '%s'?\n\n%s\n\n"
                    "Their models are re-tagged with '%s' and the old tags deleted.")
            % (len(doomed), target, names, target),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        lookup_admin.merge_tags([did for did, _ in doomed], target)

        self._scan()

    def closeEvent(self, event) -> None:
        # Don't tear down while the scan thread is still running.
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(5000)
        super().closeEvent(event)
