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
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTextEdit, QToolButton, QVBoxLayout,
)

from app.database.db import session_scope, get_session
from app.database.models import STLModel
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


def _compute_target(base_dir: Path, pattern: str, model: STLModel) -> Path:
    """Compute the target path for *model* given *base_dir* and *pattern*."""
    src = Path(model.archive_path)
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
# Dialog
# ---------------------------------------------------------------------------

class SortFilesDialog(QDialog):
    """
    Dialog for sorting selected STL archive files into a structured directory.

    Parameters
    ----------
    model_ids : list[int]
        Primary-key IDs of the STLModel rows whose archive files to move.
    parent : QWidget, optional
    """

    def __init__(self, model_ids: list[int], parent=None):
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
    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ---- Options form ----
        form_box = QGroupBox("Move options")
        form = QFormLayout(form_box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        # Target directory row
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit()
        self._dir_edit.setText(_load_last_directory())
        self._dir_edit.setPlaceholderText("Select or type the base destination directory…")
        self._dir_edit.textChanged.connect(self._refresh_preview)
        dir_row.addWidget(self._dir_edit)
        browse_btn = QPushButton("Browse…")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_dir)
        dir_row.addWidget(browse_btn)
        form.addRow("Target directory:", dir_row)

        # Pattern row
        pat_row = QHBoxLayout()
        self._pat_edit = QLineEdit()
        self._pat_edit.setPlaceholderText("{category}/{author}/{title}")
        self._pat_edit.textChanged.connect(self._refresh_preview)
        pat_row.addWidget(self._pat_edit)

        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip("Hover for pattern syntax help")
        help_btn.setMouseTracking(True)
        help_btn.installEventFilter(self)
        self._help_btn_ref = help_btn
        pat_row.addWidget(help_btn)

        form.addRow("Path pattern:", pat_row)

        # Saved patterns row
        saved_row = QHBoxLayout()
        self._saved_combo = QComboBox()
        self._saved_combo.setPlaceholderText("— select a saved pattern —")
        self._saved_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._saved_combo.setMinimumWidth(200)
        self._saved_combo.activated.connect(self._load_selected_pattern)
        saved_row.addWidget(self._saved_combo, 1)

        save_btn = QPushButton("Save")
        save_btn.setFixedWidth(60)
        save_btn.setToolTip("Save current pattern for later use")
        save_btn.clicked.connect(self._save_current_pattern)
        saved_row.addWidget(save_btn)

        del_btn = QPushButton("Delete")
        del_btn.setFixedWidth(60)
        del_btn.setToolTip("Remove the selected saved pattern")
        del_btn.clicked.connect(self._delete_selected_pattern)
        saved_row.addWidget(del_btn)

        form.addRow("Saved patterns:", saved_row)

        layout.addWidget(form_box)

        # ---- Preview ----
        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_box)
        self._preview_edit = QTextEdit()
        self._preview_edit.setReadOnly(True)
        self._preview_edit.setFontFamily("monospace")
        self._preview_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        preview_layout.addWidget(self._preview_edit)
        layout.addWidget(preview_box)

        # ---- Buttons ----
        self._btn_box = QDialogButtonBox()
        self._move_btn = self._btn_box.addButton(
            "Move Files", QDialogButtonBox.ButtonRole.AcceptRole
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

    def _show_help_popup(self):
        if self._help_btn_ref is not None:
            self._help_popup.show_near(self._help_btn_ref)

    def hideEvent(self, event):
        self._help_popup.hide()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._help_popup.hide()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Saved patterns
    # ------------------------------------------------------------------

    def _populate_saved_combo(self):
        self._saved_combo.clear()
        for pat in _load_saved_patterns():
            self._saved_combo.addItem(pat)

    def _load_selected_pattern(self, index: int):
        if 0 <= index < self._saved_combo.count():
            self._pat_edit.setText(self._saved_combo.itemText(index))

    def _save_current_pattern(self):
        pattern = self._pat_edit.text().strip()
        if not pattern:
            QMessageBox.warning(self, "Save pattern", "Enter a pattern before saving.")
            return
        patterns = _load_saved_patterns()
        if pattern in patterns:
            QMessageBox.information(self, "Save pattern", "Pattern is already saved.")
            return
        patterns.insert(0, pattern)
        _save_patterns(patterns)
        self._populate_saved_combo()

    def _delete_selected_pattern(self):
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
    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select target directory", self._dir_edit.text() or ""
        )
        if d:
            self._dir_edit.setText(d)

    # ------------------------------------------------------------------
    def _refresh_preview(self):
        """Re-compute target paths and update the preview text."""
        self._preview_data.clear()
        self._move_btn.setEnabled(False)

        base_text = self._dir_edit.text().strip()
        pattern = self._pat_edit.text().strip()

        if not base_text:
            self._preview_edit.setPlainText("← Set a target directory to see preview.")
            return
        if not pattern:
            self._preview_edit.setPlainText("← Set a path pattern to see preview.")
            return

        base_dir = Path(base_text)

        lines: list[str] = []
        has_errors = False

        session = get_session()
        try:
            for mid in self._model_ids:
                model: STLModel = session.get(STLModel, mid)
                if model is None:
                    lines.append(f"[ERROR] model id={mid} not found in database")
                    has_errors = True
                    continue

                src = model.archive_path
                if not src:
                    lines.append(f"[SKIP ] {model.title or mid!r} — no archive_path set")
                    has_errors = True
                    continue

                src_path = Path(src)
                if not src_path.exists():
                    lines.append(
                        f"[MISS ] {src}\n"
                        f"        ↳ source file not found on disk"
                    )
                    has_errors = True
                    continue

                target = _compute_target(base_dir, pattern, model)
                collision = target.exists() and target != src_path
                collision_note = "  [COLLISION — will be renamed]" if collision else ""

                lines.append(
                    f"FROM  {src}\n"
                    f"  TO  {target}{collision_note}\n"
                )
                self._preview_data.append((str(src_path), str(target), mid))
        finally:
            session.close()

        self._preview_edit.setPlainText("\n".join(lines))
        if self._preview_data and not has_errors:
            self._move_btn.setEnabled(True)
        elif self._preview_data:
            self._move_btn.setEnabled(True)
            self._move_btn.setText(
                f"Move {len(self._preview_data)} of {len(self._model_ids)} Files"
            )
        else:
            self._move_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _do_move(self):
        """Perform the file moves and update the database."""
        if not self._preview_data:
            return

        moved: list[tuple[str, str, int]] = []
        errors: list[str] = []

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for src_str, dst_str, mid in self._preview_data:
                src = Path(src_str)
                dst = Path(dst_str)

                if not src.exists():
                    errors.append(f"Source gone: {src}")
                    continue

                dst = _unique_target(dst)

                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    errors.append(f"Cannot create directory {dst.parent}: {exc}")
                    continue

                try:
                    shutil.move(str(src), str(dst))
                except (OSError, shutil.Error) as exc:
                    errors.append(f"Move failed {src} → {dst}: {exc}")
                    continue

                moved.append((src_str, str(dst), mid))
        finally:
            QApplication.restoreOverrideCursor()

        if moved:
            with session_scope() as session:
                for _src, final_dst, mid in moved:
                    model: STLModel = session.get(STLModel, mid)
                    if model is not None:
                        model.archive_path = final_dst

        if moved:
            _save_last_directory(self._dir_edit.text().strip())

        if errors:
            QMessageBox.warning(
                self,
                "Sort — partial success",
                f"Moved {len(moved)} of {len(self._preview_data)} file(s).\n\n"
                "Errors:\n" + "\n".join(errors),
            )
        else:
            QMessageBox.information(
                self,
                "Sort complete",
                f"Successfully moved {len(moved)} file(s).",
            )

        self.accept()
