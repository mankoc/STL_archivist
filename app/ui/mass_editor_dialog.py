"""
Mass editor dialog — edit multiple STLModel records at once.

Two modes can be combined:
  • Path pattern  — extract field values from each model's archive_path
  • Manual fields — apply the same value to all selected models
"""
import re

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QDialogButtonBox, QPushButton,
    QComboBox, QInputDialog, QMessageBox,
)

from app.database.db import session_scope, get_session
from app.database.models import STLModel, Tag

# Maps lowercase token name → STLModel attribute
_TOKEN_MAP = {
    "type":    "type",
    "author":  "author",
    "title":   "title",
    "name":    "title",
    "date":    "release_date",
    "month":   "release_date",
    "url":     "url",
}

# Human-readable label for each attribute (for the preview line)
_ATTR_LABEL = {
    "type":         "type",
    "author":       "author",
    "title":        "title",
    "release_date": "date",
    "url":          "url",
}

_PATTERN_HELP = (
    "<b>Pattern syntax:</b> use <code>{token}</code> for captured segments, "
    "<code>*</code> for ignored segments.<br>"
    "Supported tokens:&nbsp; "
    "<code>{type}</code>, <code>{author}</code>, "
    "<code>{title}</code>&nbsp;/&nbsp;<code>{name}</code>, "
    "<code>{date}</code>&nbsp;/&nbsp;<code>{month}</code>, "
    "<code>{url}</code><br>"
    "<b>Example:</b>&nbsp; <code>/mnt/f/{type}/{author}/*/{name}</code>"
)

_SAVED_PATTERNS_KEY = "mass_editor/saved_patterns"
_LAST_PATTERN_KEY   = "mass_editor/last_pattern"

# Separator used inside QSettings list entries: "My pattern:::/mnt/f/{type}/…"
_SEP = ":::"


def _pattern_to_regex(pattern: str) -> re.Pattern | None:
    """Compile *pattern* to a regex.

    ``{token}`` → named capture group ``(?P<token>[^/]+)``
    ``*``        → anonymous match  ``[^/]+``
    Duplicate token names get a numeric suffix so the regex stays valid.
    Returns None when the pattern contains no tokens or wildcards.
    """
    has_placeholder = bool(re.search(r"\{(\w+)\}|\*", pattern))
    if not has_placeholder:
        return None

    seen: dict[str, int] = {}
    # Escape the literal parts, then restore placeholder markers
    # Work token-by-token to preserve order
    parts = re.split(r"(\{\w+\}|\*)", pattern)
    regex_parts: list[str] = []
    for part in parts:
        if part == "*":
            regex_parts.append("[^/]+")
        elif part.startswith("{") and part.endswith("}"):
            token = part[1:-1]
            count = seen.get(token, 0)
            seen[token] = count + 1
            group = token if count == 0 else f"{token}_{count}"
            regex_parts.append(f"(?P<{group}>[^/]+)")
        else:
            regex_parts.append(re.escape(part))

    try:
        return re.compile("".join(regex_parts))
    except re.error:
        return None


def _parse_path(pattern: str, path: str) -> dict[str, str]:
    """Extract field values from *path* using *pattern*.

    Returns a dict mapping STLModel attribute names → extracted strings.
    Returns an empty dict if the pattern does not match.
    """
    rx = _pattern_to_regex(pattern)
    if rx is None:
        return {}

    m = rx.search(path)
    if not m:
        return {}

    result: dict[str, str] = {}
    for group, value in m.groupdict().items():
        base_token = re.sub(r"_\d+$", "", group)   # strip numeric suffix
        attr = _TOKEN_MAP.get(base_token.lower())
        if attr and value:
            result[attr] = value
    return result


class MassEditorDialog(QDialog):
    def __init__(self, model_ids: list[int], parent=None):
        super().__init__(parent)
        self._model_ids = model_ids
        self._path_results: dict[int, dict[str, str]] = {}
        self._example_path = self._fetch_example_path(model_ids)

        self.setWindowTitle(f"Mass Edit — {len(model_ids)} model(s)")
        self.resize(660, 540)
        self._build_ui()
        self._load_last_pattern()

    # ------------------------------------------------------------------
    @staticmethod
    def _fetch_example_path(model_ids: list[int]) -> str:
        if not model_ids:
            return ""
        session = get_session()
        try:
            model = session.get(STLModel, model_ids[0])
            return (model.archive_path or "") if model else ""
        finally:
            session.close()

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        # ---- Path pattern group ----
        path_group = QGroupBox("Parse fields from archive path (optional)")
        path_layout = QVBoxLayout(path_group)

        help_label = QLabel(_PATTERN_HELP)
        help_label.setTextFormat(Qt.TextFormat.RichText)
        help_label.setWordWrap(True)
        path_layout.addWidget(help_label)

        # Example path
        if self._example_path:
            ex_label = QLabel(f"<b>Example path:</b>&nbsp;<code>{self._example_path}</code>")
            ex_label.setTextFormat(Qt.TextFormat.RichText)
            ex_label.setWordWrap(True)
            path_layout.addWidget(ex_label)

        # Saved-patterns row
        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel("Saved:"))
        self._saved_combo = QComboBox()
        self._saved_combo.setPlaceholderText("— load a saved pattern —")
        self._saved_combo.activated.connect(self._load_saved_pattern)
        saved_row.addWidget(self._saved_combo, stretch=1)
        save_btn = QPushButton("Save…")
        save_btn.clicked.connect(self._save_pattern)
        saved_row.addWidget(save_btn)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_pattern)
        saved_row.addWidget(delete_btn)
        path_layout.addLayout(saved_row)

        # Pattern input + Parse button
        pattern_row = QHBoxLayout()
        self._pattern_edit = QLineEdit()
        self._pattern_edit.setPlaceholderText("/mnt/f/{type}/{author}/{month}/{name}")
        self._pattern_edit.textChanged.connect(self._update_preview)
        pattern_row.addWidget(self._pattern_edit)
        parse_btn = QPushButton("Parse all paths")
        parse_btn.clicked.connect(self._parse_paths)
        pattern_row.addWidget(parse_btn)
        path_layout.addLayout(pattern_row)

        # Live preview of extracted values for the example path
        self._preview_label = QLabel("")
        self._preview_label.setTextFormat(Qt.TextFormat.RichText)
        self._preview_label.setWordWrap(True)
        self._preview_label.setStyleSheet("color: #555; font-style: italic;")
        path_layout.addWidget(self._preview_label)

        # Parse-all status
        self._parse_status = QLabel("")
        self._parse_status.setStyleSheet("color: gray; font-style: italic;")
        path_layout.addWidget(self._parse_status)

        root.addWidget(path_group)

        # ---- Manual override group ----
        manual_group = QGroupBox(
            "Manual overrides — fill a field to apply the same value to all selected models"
        )
        form = QFormLayout(manual_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._type_edit  = QLineEdit(); self._type_edit.setPlaceholderText("e.g. patreon")
        self._author_edit = QLineEdit(); self._author_edit.setPlaceholderText("e.g. SomeAuthor")
        self._date_edit  = QLineEdit(); self._date_edit.setPlaceholderText("e.g. 2024-01")
        self._title_edit = QLineEdit()
        self._tags_edit  = QLineEdit(); self._tags_edit.setPlaceholderText("tag1, tag2, … (replaces existing tags)")

        form.addRow("Type:",   self._type_edit)
        form.addRow("Author:", self._author_edit)
        form.addRow("Date:",   self._date_edit)
        form.addRow("Title:",  self._title_edit)
        form.addRow("Tags:",   self._tags_edit)

        root.addWidget(manual_group)

        # ---- Buttons ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_saved_combo()

    # ------------------------------------------------------------------
    # Settings helpers
    # ------------------------------------------------------------------
    def _saved_patterns(self) -> list[tuple[str, str]]:
        """Return list of (name, pattern) from QSettings."""
        raw = QSettings().value(_SAVED_PATTERNS_KEY, [])
        if isinstance(raw, str):
            raw = [raw]
        result = []
        for entry in raw:
            if _SEP in entry:
                name, _, pattern = entry.partition(_SEP)
                result.append((name.strip(), pattern.strip()))
        return result

    def _write_saved_patterns(self, patterns: list[tuple[str, str]]) -> None:
        entries = [f"{name}{_SEP}{pat}" for name, pat in patterns]
        QSettings().setValue(_SAVED_PATTERNS_KEY, entries)

    def _refresh_saved_combo(self) -> None:
        self._saved_combo.clear()
        for name, _ in self._saved_patterns():
            self._saved_combo.addItem(name)

    def _load_last_pattern(self) -> None:
        pattern = QSettings().value(_LAST_PATTERN_KEY, "")
        self._pattern_edit.setText(pattern)

    def _save_last_pattern(self) -> None:
        QSettings().setValue(_LAST_PATTERN_KEY, self._pattern_edit.text().strip())

    # ------------------------------------------------------------------
    # Saved-pattern actions
    # ------------------------------------------------------------------
    def _load_saved_pattern(self, index: int) -> None:
        patterns = self._saved_patterns()
        if 0 <= index < len(patterns):
            self._pattern_edit.setText(patterns[index][1])

    def _save_pattern(self) -> None:
        pattern = self._pattern_edit.text().strip()
        if not pattern:
            QMessageBox.information(self, "Save Pattern", "Enter a pattern first.")
            return
        name, ok = QInputDialog.getText(self, "Save Pattern", "Pattern name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        patterns = self._saved_patterns()
        # Overwrite if name already exists
        patterns = [(n, p) for n, p in patterns if n != name]
        patterns.append((name, pattern))
        self._write_saved_patterns(patterns)
        self._refresh_saved_combo()

    def _delete_pattern(self) -> None:
        idx = self._saved_combo.currentIndex()
        if idx < 0:
            return
        patterns = self._saved_patterns()
        if 0 <= idx < len(patterns):
            patterns.pop(idx)
            self._write_saved_patterns(patterns)
            self._refresh_saved_combo()

    # ------------------------------------------------------------------
    # Live preview
    # ------------------------------------------------------------------
    def _update_preview(self, pattern: str) -> None:
        self._parse_status.clear()
        if not self._example_path or not pattern.strip():
            self._preview_label.clear()
            return

        extracted = _parse_path(pattern.strip(), self._example_path)
        if not extracted:
            self._preview_label.setText("<i>no match</i>")
            return

        parts = [
            f"<b>{_ATTR_LABEL.get(attr, attr)}:</b>&nbsp;{val}"
            for attr, val in extracted.items()
        ]
        self._preview_label.setText("&nbsp;&nbsp;→&nbsp;&nbsp;".join(parts))

    # ------------------------------------------------------------------
    # Parse all selected paths
    # ------------------------------------------------------------------
    def _parse_paths(self) -> None:
        pattern = self._pattern_edit.text().strip()
        if not pattern:
            self._parse_status.setText("Enter a pattern first.")
            return

        self._path_results.clear()
        matched = 0
        session = get_session()
        try:
            for mid in self._model_ids:
                model = session.get(STLModel, mid)
                if model is None:
                    continue
                extracted = _parse_path(pattern, model.archive_path or "")
                if extracted:
                    self._path_results[mid] = extracted
                    matched += 1
        finally:
            session.close()

        total = len(self._model_ids)
        if matched:
            self._parse_status.setText(
                f"Matched {matched}/{total} path(s) — values will be applied per-model on save."
            )
        else:
            self._parse_status.setText(
                f"No paths matched the pattern ({total} model(s) checked)."
            )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def _save(self) -> None:
        self._save_last_pattern()

        manual: dict[str, str | None] = {}
        if self._type_edit.text().strip():
            manual["type"] = self._type_edit.text().strip()
        if self._author_edit.text().strip():
            manual["author"] = self._author_edit.text().strip()
        if self._date_edit.text().strip():
            manual["release_date"] = self._date_edit.text().strip()
        if self._title_edit.text().strip():
            manual["title"] = self._title_edit.text().strip()

        tag_names = [
            t.strip().lower()
            for t in self._tags_edit.text().split(",")
            if t.strip()
        ]
        apply_tags = bool(tag_names)

        with session_scope() as session:
            for mid in self._model_ids:
                model: STLModel = session.get(STLModel, mid)
                if model is None:
                    continue

                for attr, value in self._path_results.get(mid, {}).items():
                    setattr(model, attr, value)

                for attr, value in manual.items():
                    setattr(model, attr, value)

                if apply_tags:
                    model.tags.clear()
                    for name in tag_names:
                        tag = session.query(Tag).filter_by(name=name).first()
                        if tag is None:
                            tag = Tag(name=name)
                            session.add(tag)
                            session.flush()
                        model.tags.append(tag)

        self.accept()
