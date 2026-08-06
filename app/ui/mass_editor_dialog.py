"""
Mass editor dialog — edit multiple STLModel records at once.

Two modes can be combined:
  • Path pattern  — extract field values from each model's archive_path
  • Manual fields — apply the same value to all selected models
"""
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QDialogButtonBox, QPushButton, QCheckBox,
    QComboBox, QCompleter, QInputDialog, QMessageBox, QToolButton, QToolTip,
    QWidget,
)
from PyQt6.QtGui import QCursor

from app.database.db import session_scope, get_session
from app.database.models import STLModel, Tag, Release, Collection, Subcollection, Category, Scale, Sculptor, model_tags
from app.database.helpers import (
    LookupCache,
    get_or_create_category,
    get_or_create_collection,
    get_or_create_release,
    get_or_create_scale,
    get_or_create_sculptor,
    get_or_create_subcollection,
    get_or_create_tag,
)
import re

from app.utils.path_pattern import (
    parse_path, apply_extracted_to_model,
    ATTR_LABEL as _ATTR_LABEL, PATTERN_HELP as _PATTERN_HELP,
    parse_release_date, format_release_date,
)

_SAVED_PATTERNS_KEY = "mass_editor/saved_patterns"
_LAST_PATTERN_KEY   = "mass_editor/last_pattern"

# Separator used inside QSettings list entries: "My pattern:::/mnt/f/{source}/…"
_SEP = ":::"

_TOKEN_HELP = (
    "You can use tokens in any text field to copy values from each model:\n"
    "  {creator}  — the model's current creator\n"
    "  {source}   — the model's current source/type\n"
    "  {title}    — the model's current title\n"
    "  {date}     — the model's current release date (YYYY-MM)\n"
    "  {path}     — full archive path\n"
    "  {folder}   — parent folder name\n"
    "  {filename} — archive file name (without extension)\n\n"
    "Tokens from a parsed path pattern are also available."
)

_REGEX_HELP = (
    "Python regular expression syntax for Find & Replace.\n\n"
    "BASIC PATTERNS:\n"
    "  .        — any character\n"
    "  \\d       — digit (0-9)\n"
    "  \\w       — word character (letter, digit, underscore)\n"
    "  \\s       — whitespace (space, tab, newline)\n"
    "  [a-z]    — character range\n"
    "  [^a-z]   — negated character range\n\n"
    "QUANTIFIERS:\n"
    "  *        — zero or more (greedy)\n"
    "  +        — one or more\n"
    "  ?        — zero or one\n"
    "  {n}      — exactly n\n"
    "  {n,m}    — between n and m\n"
    "  *?, +?, ??  — non-greedy versions\n\n"
    "ANCHORS & GROUPS:\n"
    "  ^        — start of string\n"
    "  $        — end of string\n"
    "  (...)    — capture group\n"
    "  (?:...)  — non-capturing group\n"
    "  |        — alternation (or)\n\n"
    "REPLACEMENT:\n"
    "  \\0 or \\g<0> — entire match\n"
    "  \\1, \\2, …   — capture groups\n"
    "  \\g<name>    — named group\n\n"
    "EXAMPLES:\n"
    "  Find: (\\w+)-(\\w+)  Replace: \\2-\\1  (swap parts)\n"
    "  Find: ^\\s+|\\s+$    Replace: (empty)  (trim whitespace)\n"
    "  Find: (\\d{4})      Replace: [\\1]    (wrap numbers in brackets)\n"
    "  Find: (?i)^v        Replace: Version (case-insensitive v→Version)\n"
)


def _parse_path(pattern: str, path: str) -> dict[str, str]:
    return parse_path(pattern, path)


class MassEditorDialog(QDialog):
    """Bulk editor that applies changes to multiple STLModel records simultaneously.

    Two modes can be combined:
    - **Path pattern** — extract field values from each model's ``archive_path``
      and preview the results before applying.
    - **Manual fields** — fill in any field to overwrite it on all selected
      models; leave a field empty to leave it unchanged.
    """

    def __init__(self, model_ids: list[int], parent=None) -> None:
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
        path_group = QGroupBox(self.tr("Parse fields from archive path (optional)"))
        path_layout = QVBoxLayout(path_group)

        # Example path
        if self._example_path:
            ex_label = QLabel(f"<b>Example path:</b>&nbsp;<code>{self._example_path}</code>")
            ex_label.setTextFormat(Qt.TextFormat.RichText)
            ex_label.setWordWrap(True)
            path_layout.addWidget(ex_label)

        # Saved-patterns row
        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel(self.tr("Saved:")))
        self._saved_combo = QComboBox()
        self._saved_combo.setPlaceholderText(self.tr("— load a saved pattern —"))
        self._saved_combo.activated.connect(self._load_saved_pattern)
        saved_row.addWidget(self._saved_combo, stretch=1)
        save_btn = QPushButton(self.tr("Save…"))
        save_btn.clicked.connect(self._save_pattern)
        saved_row.addWidget(save_btn)
        delete_btn = QPushButton(self.tr("Delete"))
        delete_btn.clicked.connect(self._delete_pattern)
        saved_row.addWidget(delete_btn)
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip(_PATTERN_HELP)
        help_btn.setStyleSheet(
            "QToolButton { font-weight: bold; border: 1px solid gray;"
            " border-radius: 9px; min-width: 18px; min-height: 18px; }"
        )
        help_btn.enterEvent = lambda e: QToolTip.showText(QCursor.pos(), _PATTERN_HELP, help_btn)
        saved_row.addWidget(help_btn)
        path_layout.addLayout(saved_row)

        # Pattern input + Parse button
        pattern_row = QHBoxLayout()
        self._pattern_edit = QLineEdit()
        self._pattern_edit.setPlaceholderText(self.tr("/mnt/f/{source}/{creator}/{month}/{name}"))
        self._pattern_edit.textChanged.connect(self._update_preview)
        pattern_row.addWidget(self._pattern_edit)
        parse_btn = QPushButton(self.tr("Parse all paths"))
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

        self._overwrite_cb = QCheckBox(self.tr("Overwrite existing field values with parsed values"))
        self._overwrite_cb.setToolTip(
            self.tr("When checked, parsed values replace any existing field values.\n"
            "When unchecked, parsed values are only written to fields that are currently empty.")
        )
        path_layout.addWidget(self._overwrite_cb)

        root.addWidget(path_group)

        # ---- Manual override group ----
        manual_group = QGroupBox(
            self.tr("Manual overrides — fill a field to apply the same value to all selected models")
        )
        manual_vbox = QVBoxLayout(manual_group)

        token_row = QHBoxLayout()
        token_row.addStretch()
        token_help_btn = QToolButton()
        token_help_btn.setText("?")
        token_help_btn.setToolTip(_TOKEN_HELP)
        token_help_btn.setStyleSheet(
            "QToolButton { font-weight: bold; border: 1px solid gray;"
            " border-radius: 9px; min-width: 18px; min-height: 18px; }"
        )
        token_help_btn.enterEvent = lambda e: QToolTip.showText(
            QCursor.pos(), _TOKEN_HELP, token_help_btn
        )
        token_row.addWidget(token_help_btn)
        manual_vbox.addLayout(token_row)

        grid = QGridLayout()
        manual_vbox.addLayout(grid)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)

        session = get_session()
        try:
            _uniq = lambda col: sorted(filter(None, (v for (v,) in session.query(col).distinct())))

            # SQLite caps IN-clause variables at ~999; for large selections we
            # skip the scope filter and return all values from the full DB.
            _CHUNK = 999
            _ids = self._model_ids
            _large = len(_ids) > _CHUNK

            sources        = _uniq(STLModel.source)
            creators       = _uniq(STLModel.creator)
            titles         = _uniq(STLModel.title)

            if _large:
                dates = sorted({
                    format_release_date(y, mo)
                    for (y, mo) in session.query(STLModel.release_year, STLModel.release_month)
                                          .filter(STLModel.release_year.isnot(None)).distinct()
                })
                all_tags = sorted(t.name for t in session.query(Tag).order_by(Tag.name).all())
                def _fk_ids(fk_col):
                    return [
                        cid for (cid,) in session.query(fk_col)
                        .filter(fk_col.isnot(None)).distinct()
                    ]
            else:
                dates = sorted({
                    format_release_date(y, mo)
                    for (y, mo) in session.query(STLModel.release_year, STLModel.release_month)
                                          .filter(STLModel.id.in_(_ids))
                                          .filter(STLModel.release_year.isnot(None)).distinct()
                })
                all_tags = sorted(
                    t.name for t in session.query(Tag)
                    .join(model_tags, Tag.id == model_tags.c.tag_id)
                    .filter(model_tags.c.model_id.in_(_ids))
                    .distinct().order_by(Tag.name).all()
                )
                def _fk_ids(fk_col):
                    return [
                        cid for (cid,) in session.query(fk_col)
                        .filter(STLModel.id.in_(_ids))
                        .filter(fk_col.isnot(None)).distinct()
                    ]

            _rel_ids = _fk_ids(STLModel.release_id)
            releases       = sorted(filter(None, (
                r.name for r in session.query(Release)
                .filter(Release.id.in_(_rel_ids)).all()
            ))) if _rel_ids else sorted(r.name for r in session.query(Release).order_by(Release.name).all())
            _col_ids = _fk_ids(STLModel.collection_id)
            collections    = sorted(filter(None, (
                c.name for c in session.query(Collection)
                .filter(Collection.id.in_(_col_ids)).all()
            ))) if _col_ids else sorted(c.name for c in session.query(Collection).order_by(Collection.name).all())
            _sub_ids = _fk_ids(STLModel.subcollection_id)
            subcollections = sorted(filter(None, (
                s.name for s in session.query(Subcollection)
                .filter(Subcollection.id.in_(_sub_ids)).all()
            ))) if _sub_ids else sorted(s.name for s in session.query(Subcollection).order_by(Subcollection.name).all())
            categories     = sorted(c.name for c in session.query(Category).order_by(Category.name).all())
            scales         = sorted(s.name for s in session.query(Scale).order_by(Scale.name).all())
            sculptors      = sorted(s.name for s in session.query(Sculptor).order_by(Sculptor.name).all())
        finally:
            session.close()

        self._type_edit    = self._make_ac_edit(sources,  "e.g. patreon  or  {source}")
        self._creator_edit = self._make_ac_edit(creators, "e.g. SomeCreator  or  {creator}")
        self._date_edit    = self._make_ac_edit(dates,    "e.g. 2024-01  or  {date}")
        self._title_edit   = self._make_ac_edit(titles,   "e.g. {filename}  or  {creator} — {title}")
        self._url_edit     = self._make_ac_edit([],       "e.g. https://example.com or {url}")
        self._release_edit = self._make_ac_edit(releases, "Release name  or  {folder}")
        self._tags_edit    = self._make_ac_edit(all_tags, "tag1, tag2, …  (replaces existing tags)")

        self._scale_edit   = self._make_ac_edit(scales, "28mm, 32mm, …  (replaces existing scales)")
        self._presupports_combo = QComboBox()
        self._presupports_combo.addItems(["— no change —", "No", "Lychee", "Chitubox", "Presupported"])

        self._collection_edit     = self._make_combo(collections)
        self._collection_edit.currentTextChanged.connect(self._on_collection_changed)
        self._subcollection_edit  = self._make_combo(subcollections)
        self._category_edit       = self._make_combo(categories)
        self._sculptor_edit       = self._make_combo(sculptors)

        # Clear-mode checkboxes — when checked, the field is explicitly nulled on save.
        self._clear_cbs: dict[str, QCheckBox] = {}
        _clearable_fields = [
            ("source",         self._type_edit),
            ("creator",        self._creator_edit),
            ("release_date",   self._date_edit),
            ("title",          self._title_edit),
            ("url",            self._url_edit),
            ("release",        self._release_edit),
            ("tags",           self._tags_edit),
            ("scale",          self._scale_edit),
            ("presupports",    self._presupports_combo),
            ("collection",     self._collection_edit),
            ("subcollection",  self._subcollection_edit),
            ("category",       self._category_edit),
            ("sculptor",       self._sculptor_edit),
        ]
        for field_name, widget in _clearable_fields:
            cb = QCheckBox(self.tr("clear"))
            cb.setToolTip(f"When checked, {field_name} will be set to blank on all selected models")
            cb.setStyleSheet("QCheckBox { color: gray; font-size: 10px; }")
            cb.toggled.connect(widget.setDisabled)
            self._clear_cbs[field_name] = cb

        def _cell(r: int, c: int, label: str, field_name: str, widget) -> None:
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            container = QWidget()
            lay = QHBoxLayout(container)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            lay.addWidget(widget, stretch=1)
            lay.addWidget(self._clear_cbs[field_name])
            grid.addWidget(lbl,       r, c * 2)
            grid.addWidget(container, r, c * 2 + 1)

        _cell(0, 0, "Creator:",      "creator",       self._creator_edit)
        _cell(0, 1, "Source:",       "source",        self._type_edit)
        _cell(1, 0, "Date:",         "release_date",  self._date_edit)
        _cell(1, 1, "Title:",        "title",         self._title_edit)
        _cell(2, 0, "URL:",          "url",           self._url_edit)
        _cell(2, 1, "Release:",      "release",       self._release_edit)
        _cell(3, 0, "Sculptor:",     "sculptor",      self._sculptor_edit)
        _cell(3, 1, "Category:",     "category",      self._category_edit)
        _cell(4, 0, "Collection:",   "collection",    self._collection_edit)
        _cell(4, 1, "Subcollection:","subcollection", self._subcollection_edit)
        _cell(5, 0, "Scale:",        "scale",         self._scale_edit)
        _cell(5, 1, "Presupports:",  "presupports",   self._presupports_combo)

        # Tags spans the full width
        tags_lbl = QLabel(self.tr("Tags:"))
        tags_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tags_container = QWidget()
        tags_lay = QHBoxLayout(tags_container)
        tags_lay.setContentsMargins(0, 0, 0, 0)
        tags_lay.setSpacing(4)
        tags_lay.addWidget(self._tags_edit, stretch=1)
        tags_lay.addWidget(self._clear_cbs["tags"])
        grid.addWidget(tags_lbl,       6, 0)
        grid.addWidget(tags_container, 6, 1, 1, 3)

        root.addWidget(manual_group)

        # ---- Find & Replace group ----
        fnr_group = QGroupBox(self.tr("Find & Replace"))
        fnr_layout = QFormLayout(fnr_group)
        fnr_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText(self.tr("substring to find"))
        self._find_edit.setClearButtonEnabled(True)
        fnr_layout.addRow(self.tr("Find:"), self._find_edit)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText(self.tr("replacement (leave empty to delete)"))
        self._replace_edit.setClearButtonEnabled(True)
        fnr_layout.addRow(self.tr("Replace with:"), self._replace_edit)

        # Field selection — which string columns to apply F&R to
        self._fnr_fields: dict[str, QCheckBox] = {}
        fields_row = QWidget()
        fields_lay = QHBoxLayout(fields_row)
        fields_lay.setContentsMargins(0, 0, 0, 0)
        fields_lay.setSpacing(12)
        for field_name, label in (
            ("title",         "Title"),
            ("creator",       "Creator"),
            ("source",        "Source"),
            ("collection",    "Collection"),
            ("subcollection", "Subcollection"),
        ):
            cb = QCheckBox(label)
            cb.setChecked(field_name == "title")
            self._fnr_fields[field_name] = cb
            fields_lay.addWidget(cb)
        fields_lay.addStretch()
        fnr_layout.addRow(self.tr("Apply to:"), fields_row)

        options_row = QWidget()
        options_lay = QHBoxLayout(options_row)
        options_lay.setContentsMargins(0, 0, 0, 0)
        options_lay.setSpacing(16)
        self._fnr_case_check = QCheckBox(self.tr("Case-sensitive"))
        self._fnr_regex_check = QCheckBox(self.tr("Use regex"))
        self._fnr_regex_check.setToolTip(
            self.tr("When checked, the Find field is treated as a Python regular expression.\n"
            "Use capture groups in Replace with: \\1, \\2, …")
        )
        options_lay.addWidget(self._fnr_case_check)
        options_lay.addWidget(self._fnr_regex_check)

        regex_help_btn = QToolButton()
        regex_help_btn.setText("?")
        regex_help_btn.setToolTip(_REGEX_HELP)
        regex_help_btn.setStyleSheet(
            "QToolButton { font-weight: bold; border: 1px solid gray;"
            " border-radius: 9px; min-width: 18px; min-height: 18px; }"
        )
        regex_help_btn.enterEvent = lambda e: QToolTip.showText(
            QCursor.pos(), _REGEX_HELP, regex_help_btn
        )
        options_lay.addWidget(regex_help_btn)
        options_lay.addStretch()
        fnr_layout.addRow("", options_row)

        root.addWidget(fnr_group)

        # ---- Buttons ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._refresh_saved_combo()

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _make_ac_edit(values: list[str], placeholder: str = "") -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setClearButtonEnabled(True)
        if values:
            completer = QCompleter(values, edit)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            completer.setFilterMode(Qt.MatchFlag.MatchContains)
            edit.setCompleter(completer)
        return edit

    @staticmethod
    def _make_combo(values: list[str], *, placeholder: str = "") -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItem(placeholder)
        combo.addItems(values)
        combo.setCurrentIndex(0)
        combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.lineEdit().setClearButtonEnabled(True)
        return combo

    # ------------------------------------------------------------------
    # Token resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _token_context(model: "STLModel", path_extracted: dict[str, str]) -> dict[str, str]:
        from pathlib import Path as _P
        ap = model.archive_path or ""
        ctx: dict[str, str] = {
            "creator":  model.creator  or "",
            "source":   model.source   or "",
            "title":    model.title    or "",
            "date":     format_release_date(model.release_year, model.release_month),
            "path":     ap,
            "folder":   _P(ap).parent.name if ap else "",
            "filename": _P(ap).stem       if ap else "",
        }
        ctx.update(path_extracted)
        return ctx

    @staticmethod
    def _resolve_tokens(value: str, context: dict[str, str]) -> str:
        def _sub(m: re.Match) -> str:
            return context.get(m.group(1), m.group(0))
        return re.sub(r"\{(\w+)\}", _sub, value)

    def _on_collection_changed(self, collection_name: str) -> None:
        """Repopulate subcollection combo when the collection selection changes."""
        session = get_session()
        try:
            q = session.query(Subcollection).order_by(Subcollection.name)
            if collection_name.strip():
                col = session.query(Collection).filter_by(name=collection_name.strip()).first()
                if col:
                    q = q.filter(Subcollection.collection_id == col.id)
            subcollections = [s.name for s in q.all()]
        finally:
            session.close()
        current = self._subcollection_edit.currentText()
        self._subcollection_edit.blockSignals(True)
        self._subcollection_edit.clear()
        self._subcollection_edit.addItem("")
        self._subcollection_edit.addItems(subcollections)
        self._subcollection_edit.setCurrentText(current)
        self._subcollection_edit.blockSignals(False)

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
            QMessageBox.information(self, self.tr("Save Pattern"), self.tr("Enter a pattern first."))
            return
        name, ok = QInputDialog.getText(self, self.tr("Save Pattern"), self.tr("Pattern name:"))
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
            self._preview_label.setText(self.tr("<i>no match</i>"))
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
            self._parse_status.setText(self.tr("Enter a pattern first."))
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

        clearing = {k for k, cb in self._clear_cbs.items() if cb.isChecked()}

        manual: dict[str, str | None] = {}
        for field, edit in (
            ("source",       self._type_edit),
            ("creator",      self._creator_edit),
            ("release_date", self._date_edit),
            ("url",          self._url_edit),
            ("title",        self._title_edit),
        ):
            if field in clearing:
                manual[field] = None
            elif edit.text().strip():
                manual[field] = edit.text().strip()

        scale_tmpl   = self._scale_edit.text().strip()
        apply_scale  = "scale" in clearing or bool(scale_tmpl)

        presup_choice     = self._presupports_combo.currentText()
        apply_presupports = "presupports" in clearing or presup_choice != "— no change —"
        presupports_value = None if "presupports" in clearing else (presup_choice if apply_presupports else None)

        release_tmpl       = self._release_edit.text().strip()
        collection_name    = self._collection_edit.currentText().strip()
        subcollection_name = self._subcollection_edit.currentText().strip()
        category_name      = self._category_edit.currentText().strip()

        tags_tmpl  = self._tags_edit.text()
        apply_tags = "tags" in clearing or bool(tags_tmpl.strip())

        _cache: LookupCache = {}

        # Prepare Find & Replace pattern if needed
        find_str = self._find_edit.text()
        target_fields = [f for f, cb in self._fnr_fields.items() if cb.isChecked()]
        pattern = None
        if find_str and find_str.strip() and target_fields:
            replace_str = self._replace_edit.text()
            flags = 0 if self._fnr_case_check.isChecked() else re.IGNORECASE
            raw = find_str if self._fnr_regex_check.isChecked() else re.escape(find_str)
            try:
                pattern = re.compile(raw, flags)
            except re.error as exc:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, self.tr("Invalid Regex"), f"The pattern is not valid:\n{exc}")
                return

        # Single session for all operations (manual edits + Find & Replace)
        _fnr_cache: LookupCache = {}
        with session_scope() as session:
            for mid in self._model_ids:
                model: STLModel = session.get(STLModel, mid)
                if model is None:
                    continue

                path_extracted = self._path_results.get(mid, {})
                apply_extracted_to_model(
                    session, model, path_extracted,
                    overwrite=self._overwrite_cb.isChecked(),
                )

                ctx = self._token_context(model, path_extracted)
                for attr, value in manual.items():
                    if value is None:
                        if attr == "release_date":
                            model.release_year  = None
                            model.release_month = None
                        else:
                            setattr(model, attr, None)
                    else:
                        resolved = self._resolve_tokens(value, ctx)
                        if attr == "release_date":
                            _year, _month = parse_release_date(resolved or "")
                            model.release_year  = _year
                            model.release_month = _month
                        else:
                            setattr(model, attr, resolved or None)

                if "release" in clearing:
                    model.release = None
                elif release_tmpl:
                    release_name = self._resolve_tokens(release_tmpl, ctx)
                    if release_name:
                        model.release = get_or_create_release(session, release_name, cache=_cache)

                if "collection" in clearing:
                    model.collection    = None
                    model.subcollection = None
                elif collection_name:
                    col = get_or_create_collection(session, collection_name, cache=_cache)
                    model.collection = col

                    if "subcollection" in clearing:
                        model.subcollection = None
                    elif subcollection_name:
                        model.subcollection = get_or_create_subcollection(
                            session, subcollection_name, col.id, cache=_cache
                        )

                if "category" in clearing:
                    model.category = None
                elif category_name:
                    model.category = get_or_create_category(session, category_name, cache=_cache)

                sculptor_name = self._sculptor_edit.currentText().strip()
                if "sculptor" in clearing:
                    model.sculptor = None
                elif sculptor_name:
                    model.sculptor = get_or_create_sculptor(session, sculptor_name, cache=_cache)

                if apply_scale:
                    model.scales.clear()
                    if "scale" not in clearing or scale_tmpl:
                        _seen_scales: set[str] = set()
                        for name in [s.strip() for s in scale_tmpl.split(",") if s.strip()]:
                            if name.lower() in _seen_scales:
                                continue
                            model.scales.append(get_or_create_scale(session, name, cache=_cache))
                            _seen_scales.add(name.lower())

                if apply_presupports:
                    model.presupports = presupports_value or None

                if apply_tags:
                    if "tags" in clearing and not tags_tmpl.strip():
                        model.tags.clear()
                    else:
                        resolved_tags = self._resolve_tokens(tags_tmpl, ctx)
                        tag_names = [t.strip() for t in resolved_tags.split(",") if t.strip()]
                        model.tags.clear()
                        for name in tag_names:
                            model.tags.append(get_or_create_tag(session, name, cache=_cache))

                # Apply Find & Replace in the same session
                if pattern:
                    simple_fields = [f for f in target_fields if f not in ("collection", "subcollection")]
                    do_collection    = "collection"    in target_fields
                    do_subcollection = "subcollection" in target_fields

                    for field in simple_fields:
                        old_val = getattr(model, field, None)
                        if not old_val:
                            continue
                        new_val = pattern.sub(replace_str, old_val)
                        if new_val != old_val:
                            setattr(model, field, new_val or None)
                    if do_collection and model.collection:
                        new_name = pattern.sub(replace_str, model.collection.name)
                        if new_name and new_name != model.collection.name:
                            model.collection = get_or_create_collection(
                                session, new_name, cache=_fnr_cache
                            )
                    if do_subcollection and model.subcollection:
                        new_name = pattern.sub(replace_str, model.subcollection.name)
                        if new_name and new_name != model.subcollection.name:
                            parent_id = model.collection.id if model.collection else None
                            if parent_id is not None:
                                model.subcollection = get_or_create_subcollection(
                                    session, new_name, parent_id, cache=_fnr_cache
                                )

        from app.ui import lookup_cache as _lookup_cache
        _lookup_cache.invalidate()
        self.accept()
