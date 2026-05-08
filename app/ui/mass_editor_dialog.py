"""
Mass editor dialog — edit multiple STLModel records at once.

Two modes can be combined:
  • Path pattern  — extract field values from each model's archive_path
  • Manual fields — apply the same value to all selected models
"""
from PyQt6.QtCore import Qt, QSettings, QStringListModel
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QDialogButtonBox, QPushButton, QCheckBox,
    QComboBox, QCompleter, QInputDialog, QMessageBox, QToolButton, QToolTip,
    QWidget,
)
from PyQt6.QtGui import QCursor

from app.database.db import session_scope, get_session
from app.database.models import STLModel, Tag, Release, Collection, Subcollection, Category, Scale, model_tags
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
        path_group = QGroupBox("Parse fields from archive path (optional)")
        path_layout = QVBoxLayout(path_group)

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
        self._pattern_edit.setPlaceholderText("/mnt/f/{source}/{creator}/{month}/{name}")
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

        form = QFormLayout()
        manual_vbox.addLayout(form)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        session = get_session()
        try:
            _uniq = lambda col: sorted(filter(None, (v for (v,) in session.query(col).distinct())))
            _uniq_sel = lambda col: sorted(filter(None, (
                v for (v,) in session.query(col)
                .filter(STLModel.id.in_(self._model_ids)).distinct()
            )))
            sources        = _uniq(STLModel.source)
            creators       = _uniq(STLModel.creator)
            dates          = sorted({
                format_release_date(y, mo)
                for (y, mo) in session.query(STLModel.release_year, STLModel.release_month)
                                      .filter(STLModel.id.in_(self._model_ids))
                                      .filter(STLModel.release_year.isnot(None)).distinct()
            })
            titles         = _uniq(STLModel.title)
            releases       = sorted(r.name for r in session.query(Release).order_by(Release.name).all())
            all_tags       = sorted(
                t.name for t in session.query(Tag)
                .join(model_tags, Tag.id == model_tags.c.tag_id)
                .filter(model_tags.c.model_id.in_(self._model_ids))
                .distinct().order_by(Tag.name).all()
            )
            def _fk_ids(fk_col):
                return [
                    cid for (cid,) in session.query(fk_col)
                    .filter(STLModel.id.in_(self._model_ids))
                    .filter(fk_col.isnot(None)).distinct()
                ]
            collections    = sorted(filter(None, (
                c.name for c in session.query(Collection)
                .filter(Collection.id.in_(_fk_ids(STLModel.collection_id))).all()
            )))
            subcollections = sorted(filter(None, (
                s.name for s in session.query(Subcollection)
                .filter(Subcollection.id.in_(_fk_ids(STLModel.subcollection_id))).all()
            )))
            categories     = sorted(c.name for c in session.query(Category).order_by(Category.name).all())
            scales         = sorted(s.name for s in session.query(Scale).order_by(Scale.name).all())
        finally:
            session.close()

        self._type_edit    = self._make_ac_edit(sources,  "e.g. patreon  or  {source}")
        self._creator_edit = self._make_ac_edit(creators, "e.g. SomeCreator  or  {creator}")
        self._date_edit    = self._make_ac_edit(dates,    "e.g. 2024-01  or  {date}")
        self._title_edit   = self._make_ac_edit(titles,   "e.g. {filename}  or  {creator} — {title}")
        self._url_edit     = self._make_ac_edit([],       "e.g. https://example.com or {url}")
        self._release_edit = self._make_ac_edit(releases, "Release name  or  {folder}")
        self._tags_edit    = self._make_ac_edit(all_tags, "tag1, tag2, …  (replaces existing tags)")

        self._scale_combo  = self._make_combo(scales, placeholder="— no change —")
        self._presupports_combo = QComboBox()
        self._presupports_combo.addItems(["— no change —", "No", "Lychee", "Chitubox", "Presupported"])

        self._collection_edit     = self._make_combo(collections)
        self._collection_edit.currentTextChanged.connect(self._on_collection_changed)
        self._subcollection_edit  = self._make_combo(subcollections)
        self._category_edit       = self._make_combo(categories)

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
            ("scale",          self._scale_combo),
            ("presupports",    self._presupports_combo),
            ("collection",     self._collection_edit),
            ("subcollection",  self._subcollection_edit),
            ("category",       self._category_edit),
        ]
        for field_name, widget in _clearable_fields:
            cb = QCheckBox("clear")
            cb.setToolTip(f"When checked, {field_name} will be set to blank on all selected models")
            cb.setStyleSheet("QCheckBox { color: gray; font-size: 10px; }")
            cb.toggled.connect(widget.setDisabled)
            self._clear_cbs[field_name] = cb

        def _row(label, field_name, widget):
            container = QWidget()
            lay = QHBoxLayout(container)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            lay.addWidget(widget, stretch=1)
            lay.addWidget(self._clear_cbs[field_name])
            form.addRow(label, container)

        _row("Source:",         "source",        self._type_edit)
        _row("Creator:",        "creator",       self._creator_edit)
        _row("Date:",           "release_date",  self._date_edit)
        _row("Title:",          "title",         self._title_edit)
        _row("URL:",            "url",           self._url_edit)
        _row("Scale:",          "scale",         self._scale_combo)
        _row("Presupports:",    "presupports",   self._presupports_combo)
        _row("Release:",        "release",       self._release_edit)
        _row("Collection:",     "collection",    self._collection_edit)
        _row("Subcollection:",  "subcollection", self._subcollection_edit)
        _row("Category:",       "category",      self._category_edit)
        _row("Tags:",           "tags",          self._tags_edit)

        root.addWidget(manual_group)

        # ---- Find & Replace group ----
        fnr_group = QGroupBox("Find & Replace")
        fnr_layout = QFormLayout(fnr_group)
        fnr_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("substring to find")
        self._find_edit.setClearButtonEnabled(True)
        fnr_layout.addRow("Find:", self._find_edit)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("replacement (leave empty to delete)")
        self._replace_edit.setClearButtonEnabled(True)
        fnr_layout.addRow("Replace with:", self._replace_edit)

        # Field selection — which string columns to apply F&R to
        self._fnr_fields: dict[str, QCheckBox] = {}
        fields_row = QWidget()
        fields_lay = QHBoxLayout(fields_row)
        fields_lay.setContentsMargins(0, 0, 0, 0)
        fields_lay.setSpacing(12)
        for field_name, label in (
            ("title",   "Title"),
            ("creator", "Creator"),
            ("source",  "Source"),
            ("url",     "URL"),
        ):
            cb = QCheckBox(label)
            cb.setChecked(field_name == "title")
            self._fnr_fields[field_name] = cb
            fields_lay.addWidget(cb)
        fields_lay.addStretch()
        fnr_layout.addRow("Apply to:", fields_row)

        options_row = QWidget()
        options_lay = QHBoxLayout(options_row)
        options_lay.setContentsMargins(0, 0, 0, 0)
        options_lay.setSpacing(16)
        self._fnr_case_check = QCheckBox("Case-sensitive")
        self._fnr_regex_check = QCheckBox("Use regex")
        self._fnr_regex_check.setToolTip(
            "When checked, the Find field is treated as a Python regular expression.\n"
            "Use capture groups in Replace with: \\1, \\2, …"
        )
        options_lay.addWidget(self._fnr_case_check)
        options_lay.addWidget(self._fnr_regex_check)
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

        scale_name   = self._scale_combo.currentText().strip()
        apply_scale  = "scale" in clearing or (bool(scale_name) and scale_name != "— no change —")
        scale_value  = None if "scale" in clearing else scale_name

        presup_choice     = self._presupports_combo.currentText()
        apply_presupports = "presupports" in clearing or presup_choice != "— no change —"
        presupports_value = None if "presupports" in clearing else (presup_choice if apply_presupports else None)

        release_tmpl       = self._release_edit.text().strip()
        collection_name    = self._collection_edit.currentText().strip()
        subcollection_name = self._subcollection_edit.currentText().strip()
        category_name      = self._category_edit.currentText().strip()

        tags_tmpl  = self._tags_edit.text()
        apply_tags = "tags" in clearing or bool(tags_tmpl.strip())

        with session_scope() as session:
            for mid in self._model_ids:
                model: STLModel = session.get(STLModel, mid)
                if model is None:
                    continue

                path_extracted = self._path_results.get(mid, {})
                apply_extracted_to_model(session, model, path_extracted)

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
                        release_obj = session.query(Release).filter_by(name=release_name).first()
                        if release_obj is None:
                            release_obj = Release(name=release_name)
                            session.add(release_obj)
                            session.flush()
                        model.release = release_obj

                if "collection" in clearing:
                    model.collection    = None
                    model.subcollection = None
                elif collection_name:
                    col = session.query(Collection).filter_by(name=collection_name).first()
                    if col is None:
                        col = Collection(name=collection_name)
                        session.add(col)
                        session.flush()
                    model.collection = col

                    if "subcollection" in clearing:
                        model.subcollection = None
                    elif subcollection_name:
                        sub = session.query(Subcollection).filter_by(
                            name=subcollection_name, collection_id=col.id
                        ).first()
                        if sub is None:
                            sub = Subcollection(name=subcollection_name, collection_id=col.id)
                            session.add(sub)
                            session.flush()
                        model.subcollection = sub

                if "category" in clearing:
                    model.category = None
                elif category_name:
                    cat = session.query(Category).filter_by(name=category_name).first()
                    if cat is None:
                        cat = Category(name=category_name)
                        session.add(cat)
                        session.flush()
                    model.category = cat

                if apply_scale:
                    if scale_value:
                        sc = session.query(Scale).filter_by(name=scale_value).first()
                        if sc is None:
                            sc = Scale(name=scale_value)
                            session.add(sc)
                            session.flush()
                        model.scale = sc
                    else:
                        model.scale = None

                if apply_presupports:
                    model.presupports = presupports_value or None

                if apply_tags:
                    if "tags" in clearing and not tags_tmpl.strip():
                        model.tags.clear()
                    else:
                        resolved_tags = self._resolve_tokens(tags_tmpl, ctx)
                        tag_names = [t.strip().lower() for t in resolved_tags.split(",") if t.strip()]
                        model.tags.clear()
                        for name in tag_names:
                            tag = session.query(Tag).filter_by(name=name).first()
                            if tag is None:
                                tag = Tag(name=name)
                                session.add(tag)
                                session.flush()
                            model.tags.append(tag)

        find_str = self._find_edit.text()
        target_fields = [f for f, cb in self._fnr_fields.items() if cb.isChecked()]
        if find_str and find_str.strip() and target_fields:
            replace_str = self._replace_edit.text()
            flags = 0 if self._fnr_case_check.isChecked() else re.IGNORECASE
            raw = find_str if self._fnr_regex_check.isChecked() else re.escape(find_str)
            try:
                pattern = re.compile(raw, flags)
            except re.error as exc:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Invalid Regex", f"The pattern is not valid:\n{exc}")
                return
            with session_scope() as session:
                for mid in self._model_ids:
                    model = session.get(STLModel, mid)
                    if model is None:
                        continue
                    for field in target_fields:
                        old_val = getattr(model, field, None)
                        if not old_val:
                            continue
                        new_val = pattern.sub(replace_str, old_val)
                        if new_val != old_val:
                            setattr(model, field, new_val or None)

        self.accept()
