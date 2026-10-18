"""
Sort / organise files dialog.

Allows the user to move selected archive files into a new directory tree whose
structure is defined by a path pattern (e.g. ``{category}/{author}/{title}``).

The file move is performed securely:
  • Target directories are created with exist_ok=True.
  • The source file is verified to exist before moving.
  • Name collisions are resolved by appending ``_1``, ``_2``, … suffixes.
  • The DB ``archive_path`` is updated only after the move succeeds.
  • Any failure aborts the whole batch (already-moved files remain moved, but
    the loop stops and the user is informed of the partial result).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from PyQt6.QtCore import Qt, QEvent, QPoint, QSettings, QTimer
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout,
)

from app.services import browser_data
from app.utils.path_pattern import render_path_pattern, PATTERN_HELP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SETTINGS_KEY = "sort_dialog/saved_patterns"
_LAST_DIR_KEY = "sort_dialog/last_directory"
_MAX_SAVED = 20


def _load_saved_patterns() -> list[str]:
    s = QSettings()
    raw = s.value(_SETTINGS_KEY, "[]")
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [str(p) for p in result]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _save_patterns(patterns: list[str]) -> None:
    s = QSettings()
    s.setValue(_SETTINGS_KEY, json.dumps(patterns[:_MAX_SAVED]))


def _load_last_directory() -> str:
    return QSettings().value(_LAST_DIR_KEY, "")


def _save_last_directory(path: str) -> None:
    QSettings().setValue(_LAST_DIR_KEY, path)


def _sanitise_segment(segment: str) -> str:
    """Replace filesystem-unsafe characters in a single path segment."""
    import re
    s = segment.strip()
    s = re.sub(r'[\\/:*?"<>|]', "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "_unknown"


def _unique_target(path: Path) -> Path:
    """Return *path* unchanged if it does not exist, otherwise append _1, _2 …"""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _compute_target(base_dir: Path, pattern: str, model: dict) -> Path:
    """Compute the target path for *model* (``browser_data.sort_sources`` fields)."""
    src = Path(model["archive_path"])
    ext = src.suffix

    rendered = render_path_pattern(pattern.strip("/\\"), model)

    parts = [p for p in rendered.replace("\\", "/").split("/") if p]
    if parts:
        filename_stem = _sanitise_segment(parts[-1])
        dir_parts = [_sanitise_segment(p) for p in parts[:-1]]
    else:
        filename_stem = _sanitise_segment(src.stem)
        dir_parts = []

    target_dir = base_dir.joinpath(*dir_parts) if dir_parts else base_dir
    target = target_dir / (filename_stem + ext)
    return target


# ---------------------------------------------------------------------------
# Hover help popup
# ---------------------------------------------------------------------------

class _HelpPopup(QFrame):
    """Frameless rich-text panel shown when the user hovers over the ? button."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setObjectName("HelpPopup")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        lbl = QLabel()
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setText(PATTERN_HELP)
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)

    def show_near(self, widget) -> None:
        """Position the popup just below *widget* and show it."""
        pos: QPoint = widget.mapToGlobal(QPoint(0, widget.height() + 4))
        self.adjustSize()
        # Keep within screen bounds
        screen = QApplication.primaryScreen().availableGeometry()
        x = min(pos.x(), screen.right() - self.width() - 4)
        y = min(pos.y(), screen.bottom() - self.height() - 4)
        self.move(x, y)
        self.show()


# ---------------------------------------------------------------------------
# Confirmation dialog
# ---------------------------------------------------------------------------

class _ConfirmMoveDialog(QDialog):
    """Shows the planned moves and asks the user to confirm before proceeding."""

    def __init__(self, preview_data: list[tuple[str, str, int]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Confirm file move"))
        self.setMinimumSize(720, 400)

        layout = QVBoxLayout(self)

        lbl = QLabel(f"The following {len(preview_data)} file(s) will be moved:")
        layout.addWidget(lbl)

        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels([self.tr("From"), self.tr("To")])
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)

        for src, dst, _mid in preview_data:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(src))
            table.setItem(row, 1, QTableWidgetItem(dst))

        table.resizeRowsToContents()
        layout.addWidget(table)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Move Files"))
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class SortFilesDialog(QDialog):
    """Dialog for sorting selected STL archive files into a structured directory.

    Args:
        model_ids: Primary-key IDs of the STLModel rows whose archive files to move.
        parent: Optional parent widget.
    """

    def __init__(self, model_ids: list[int], parent=None) -> None:
        super().__init__(parent)
        self._model_ids = model_ids
        self._preview_data: list[tuple[str, str, int]] = []

        self.setWindowTitle(f"Sort Files — {len(model_ids)} selected")
        self.setMinimumSize(720, 520)

        self._help_popup = _HelpPopup()
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(200)
        self._hover_timer.timeout.connect(self._show_help_popup)
        self._help_btn_ref = None  # set in _build_ui

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- Options form ----
        form_box = QGroupBox(self.tr("Move options"))
        form = QFormLayout(form_box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # Target directory row
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setText(_load_last_directory())
        self._dir_edit.setPlaceholderText(self.tr("Select or type the base destination directory…"))
        self._dir_edit.textChanged.connect(self._refresh_preview)
        dir_row.addWidget(self._dir_edit)
        browse_btn = QPushButton(self.tr("Browse…"))
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        form.addRow(self.tr("Target directory:"), dir_row)

        # Pattern row
        pat_row = QHBoxLayout()
        self._pat_edit = QLineEdit()
        self._pat_edit.setPlaceholderText(self.tr("{category}/{author}/{title}"))
        self._pat_edit.textChanged.connect(self._refresh_preview)
        pat_row.addWidget(self._pat_edit)

        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip(self.tr("Hover for pattern syntax help"))
        help_btn.setMouseTracking(True)
        help_btn.installEventFilter(self)
        self._help_btn_ref = help_btn
        pat_row.addWidget(help_btn)

        form.addRow(self.tr("Path pattern:"), pat_row)

        # Saved patterns row
        saved_row = QHBoxLayout()
        self._saved_combo = QComboBox()
        self._saved_combo.setPlaceholderText(self.tr("— select a saved pattern —"))
        self._saved_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._saved_combo.setMinimumWidth(200)
        self._saved_combo.activated.connect(self._load_selected_pattern)
        saved_row.addWidget(self._saved_combo, 1)

        save_btn = QPushButton(self.tr("Save"))
        save_btn.setFixedWidth(60)
        save_btn.setToolTip(self.tr("Save current pattern for later use"))
        save_btn.clicked.connect(self._save_current_pattern)
        saved_row.addWidget(save_btn)

        del_btn = QPushButton(self.tr("Delete"))
        del_btn.setFixedWidth(60)
        del_btn.setToolTip(self.tr("Remove the selected saved pattern"))
        del_btn.clicked.connect(self._delete_selected_pattern)
        saved_row.addWidget(del_btn)

        form.addRow(self.tr("Saved patterns:"), saved_row)

        layout.addWidget(form_box)

        # ---- Preview ----
        preview_box = QGroupBox(self.tr("Preview"))
        preview_layout = QVBoxLayout(preview_box)

        self._preview_status = QLabel("")
        self._preview_status.setVisible(False)
        preview_layout.addWidget(self._preview_status)

        self._preview_table = QTableWidget(0, 3)
        self._preview_table.setHorizontalHeaderLabels([self.tr("Status"), self.tr("From"), self.tr("To")])
        hdr = self._preview_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._preview_table.setWordWrap(True)
        preview_layout.addWidget(self._preview_table)
        layout.addWidget(preview_box)

        # ---- Buttons ----
        self._btn_box = QDialogButtonBox()
        self._dry_run_btn = self._btn_box.addButton(
            self.tr("Dry Run"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self._dry_run_btn.setToolTip(self.tr("Preview where files would be moved without moving them"))
        self._dry_run_btn.clicked.connect(self._refresh_preview)
        self._move_btn = self._btn_box.addButton(
            self.tr("Move Files"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._move_btn.setEnabled(False)
        self._btn_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._btn_box.accepted.connect(self._do_move)
        self._btn_box.rejected.connect(self.reject)
        layout.addWidget(self._btn_box)

        self._populate_saved_combo()

    # ------------------------------------------------------------------
    # Hover help
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event: QEvent) -> bool:
        if obj is self._help_btn_ref:
            if event.type() == QEvent.Type.Enter:
                self._hover_timer.start()
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
                self._hover_timer.stop()
                self._help_popup.hide()
        return super().eventFilter(obj, event)

    def _show_help_popup(self) -> None:
        if self._help_btn_ref is not None:
            self._help_popup.show_near(self._help_btn_ref)

    def hideEvent(self, event) -> None:
        self._help_popup.hide()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._help_popup.hide()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Saved patterns
    # ------------------------------------------------------------------

    def _populate_saved_combo(self) -> None:
        self._saved_combo.clear()
        for pat in _load_saved_patterns():
            self._saved_combo.addItem(pat)

    def _load_selected_pattern(self, index: int) -> None:
        if 0 <= index < self._saved_combo.count():
            self._pat_edit.setText(self._saved_combo.itemText(index))

    def _save_current_pattern(self) -> None:
        pattern = self._pat_edit.text().strip()
        if not pattern:
            QMessageBox.warning(self, self.tr("Save pattern"), self.tr("Enter a pattern before saving."))
            return
        patterns = _load_saved_patterns()
        if pattern in patterns:
            QMessageBox.information(self, self.tr("Save pattern"), self.tr("Pattern is already saved."))
            return
        patterns.insert(0, pattern)
        _save_patterns(patterns)
        self._populate_saved_combo()

    def _delete_selected_pattern(self) -> None:
        index = self._saved_combo.currentIndex()
        if index < 0:
            return
        pattern = self._saved_combo.itemText(index)
        patterns = _load_saved_patterns()
        if pattern in patterns:
            patterns.remove(pattern)
            _save_patterns(patterns)
        self._populate_saved_combo()

    # ------------------------------------------------------------------
    def _browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, self.tr("Select target directory"), self._dir_edit.text() or ""
        )
        if d:
            self._dir_edit.setText(d)

    # ------------------------------------------------------------------
    def _refresh_preview(self) -> None:
        """Re-compute target paths and update the preview table."""
        self._preview_data.clear()
        self._move_btn.setEnabled(False)
        self._preview_table.setRowCount(0)

        base_text = self._dir_edit.text().strip()
        pattern = self._pat_edit.text().strip()

        if not base_text:
            self._preview_status.setText(self.tr("← Set a target directory to see preview."))
            self._preview_status.setVisible(True)
            return
        if not pattern:
            self._preview_status.setText(self.tr("← Set a path pattern to see preview."))
            self._preview_status.setVisible(True)
            return

        self._preview_status.setVisible(False)
        base_dir = Path(base_text)
        has_errors = False

        _RED    = QColor(255, 180, 180)
        _YELLOW = QColor(255, 220, 150)

        def _add_row(status: str, from_text: str, to_text: str, color: QColor | None = None):
            row = self._preview_table.rowCount()
            self._preview_table.insertRow(row)
            for col, text in enumerate([status, from_text, to_text]):
                item = QTableWidgetItem(text)
                if color is not None:
                    item.setBackground(QBrush(color))
                self._preview_table.setItem(row, col, item)

        sources = browser_data.sort_sources(self._model_ids)
        for mid in self._model_ids:
            model = sources.get(mid)
            if model is None:
                _add_row("ERROR", f"model id={mid}", "not found in database", _RED)
                has_errors = True
                continue

            src = model["archive_path"]
            if not src:
                _add_row("SKIP", model["title"] or str(mid), "no archive_path set", _YELLOW)
                has_errors = True
                continue

            src_path = Path(src)
            if not src_path.exists():
                _add_row("MISS", src, "source file not found on disk", _RED)
                has_errors = True
                continue

            target = _compute_target(base_dir, pattern, model)
            collision = target.exists() and target != src_path
            if collision:
                _add_row("COLLISION", src, str(target), _YELLOW)
            else:
                _add_row("OK", src, str(target))
            self._preview_data.append((str(src_path), str(target), mid))

        self._preview_table.resizeRowsToContents()

        self._move_btn.setEnabled(bool(self._preview_data))

    # ------------------------------------------------------------------
    def _do_move(self) -> None:
        """Perform the file moves and update the database.

        Shows a confirmation dialog with the full move plan before touching
        any files.  The operation is atomic with respect to the database:
          • All file moves are attempted first.
          • If any move fails, every already-moved file is rolled back and the
            DB is left completely unchanged.
          • Only after every move succeeds is the DB updated in a single
            transaction, so archive_path values are always consistent with the
            filesystem.
        """
        self._refresh_preview()
        if not self._preview_data:
            return

        confirmed = _ConfirmMoveDialog(self._preview_data, self).exec()
        if confirmed != QDialog.DialogCode.Accepted:
            return

        # Phase 1: resolve unique destination paths up-front so the whole plan
        # is known before any files are touched.
        plan: list[tuple[Path, Path, int]] = []
        for src_str, dst_str, mid in self._preview_data:
            src = Path(src_str)
            if not src.exists():
                QMessageBox.warning(
                    self,
                    self.tr("Sort — aborted"),
                    f"Source file no longer exists:\n{src}\n\n"
                    "No files were moved.",
                )
                return
            plan.append((src, _unique_target(Path(dst_str)), mid))

        # Phase 2: move every file, stopping at the first failure.
        moved_so_far: list[tuple[Path, Path]] = []
        move_error: str | None = None

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for src, dst, _mid in plan:
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    move_error = f"Cannot create directory {dst.parent}: {exc}"
                    break

                try:
                    shutil.move(str(src), str(dst))
                    moved_so_far.append((src, dst))
                except (OSError, shutil.Error) as exc:
                    move_error = f"Move failed {src} → {dst}: {exc}"
                    break
        finally:
            QApplication.restoreOverrideCursor()

        if move_error:
            # Roll back every file that was successfully moved so the
            # filesystem is restored to its original state.
            rollback_errors: list[str] = []
            for orig_src, moved_dst in reversed(moved_so_far):
                try:
                    shutil.move(str(moved_dst), str(orig_src))
                except (OSError, shutil.Error) as exc:
                    rollback_errors.append(f"{moved_dst} → {orig_src}: {exc}")

            if rollback_errors:
                QMessageBox.critical(
                    self,
                    self.tr("Sort — failed (partial rollback)"),
                    f"An error occurred:\n{move_error}\n\n"
                    "Some files could not be rolled back:\n"
                    + "\n".join(rollback_errors)
                    + "\n\nThe database has NOT been updated. "
                    "Please reconcile the affected paths manually.",
                )
            else:
                QMessageBox.warning(
                    self,
                    self.tr("Sort — aborted"),
                    f"Move failed:\n{move_error}\n\n"
                    "All moves have been rolled back. "
                    "No files were moved and the database was not changed.",
                )
            return

        # Phase 3: every move succeeded — write all DB updates in one
        # transaction so archive_path values are never partially updated.
        browser_data.set_archive_paths([(mid, str(dst)) for _src, dst, mid in plan])

        # Phase 3b: remove any source directories that are now empty.
        # Done after the DB commit so the DB is never inconsistent with the
        # filesystem if this cleanup step is skipped.
        source_dirs: set[Path] = {src.parent for src, _dst, _mid in plan}
        for directory in sorted(source_dirs, key=lambda p: len(p.parts), reverse=True):
            try:
                if directory.is_dir() and not any(directory.iterdir()):
                    directory.rmdir()
            except OSError:
                pass

        _save_last_directory(self._dir_edit.text().strip())
        QMessageBox.information(
            self,
            self.tr("Sort complete"),
            f"Successfully moved {len(plan)} file(s).",
        )
        self.accept()
