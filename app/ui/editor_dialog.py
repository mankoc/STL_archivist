"""
Model editor dialog — all editable fields for a single STLModel.
Layout: form fields on the left, thumbnail sidebar on the right.
Opened from the browser via double-click or right-click → Edit Model.
"""
from PyQt6.QtCore import Qt, QSettings, QStringListModel
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QDialogButtonBox,
    QPushButton, QScrollArea, QWidget, QCompleter, QComboBox,
    QFrame, QGroupBox, QInputDialog, QMessageBox, QSplitter, QSpinBox, QToolButton, QToolTip,
)
from PyQt6.QtGui import QCursor

from app.database.db import session_scope, get_session
from app.database.models import STLModel, Tag, Release, Sculptor, Category, Subcategory, Scale
from app.utils.thumbnailer import pixmap_from_path
from app.utils.path_pattern import parse_path, ATTR_LABEL, PATTERN_HELP, parse_release_date, format_release_date


class TagEdit(QLineEdit):
    """QLineEdit that autocompletes the current comma-separated token."""

    def __init__(self, all_tags: list[str], parent=None):
        super().__init__(parent)
        self.setPlaceholderText("tag1, tag2, tag3…")
        self.setClearButtonEnabled(True)
        self._all_tags = all_tags

        self._completer = QCompleter(self)
        self._completer.setModel(QStringListModel(all_tags, self._completer))
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)

        self.textEdited.connect(self._on_text_edited)

    # ------------------------------------------------------------------
    def _current_token(self) -> str:
        """Return the tag fragment to the left of the cursor."""
        text = self.text()
        cursor = self.cursorPosition()
        segment = text[:cursor].rsplit(",", 1)[-1]
        return segment.lstrip()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._show_completions()

    def _on_text_edited(self, _text: str):
        self._show_completions()

    def _show_completions(self):
        token = self._current_token()
        self._completer.setCompletionPrefix(token)
        self._completer.complete()

    def _insert_completion(self, chosen: str):
        text = self.text()
        cursor = self.cursorPosition()
        # Replace the current token with the chosen completion
        before = text[:cursor].rsplit(",", 1)
        prefix = (before[0] + ", ") if len(before) == 2 else ""
        after = text[cursor:]
        new_text = prefix + chosen + (", " if after.lstrip(",").strip() else ", ")
        self.setText(new_text)
        self.setCursorPosition(len(new_text))

    # ------------------------------------------------------------------
    def get_tags(self) -> list[str]:
        return [t.strip() for t in self.text().split(",") if t.strip()]


class EditorDialog(QDialog):
    def __init__(self, model_id: int, parent=None):
        super().__init__(parent)
        self.model_id = model_id
        self.setWindowTitle("Edit Model")
        self.resize(860, 560)
        self._build_ui()
        self._load_model()

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        # ---- Left: form ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setStyleSheet("color: gray;")

        session = get_session()
        try:
            _uniq = lambda col: sorted(
                filter(None, (v for (v,) in session.query(col).distinct().all()))
            )
            titles     = _uniq(STLModel.title)
            authors    = _uniq(STLModel.author)
            sources    = _uniq(STLModel.source)
            dates      = sorted({
                format_release_date(y, mo)
                for (y, mo) in session.query(STLModel.release_year, STLModel.release_month)
                                      .filter(STLModel.release_year.isnot(None))
                                      .distinct()
                                      .all()
            })
            all_tags   = [t.name for t in session.query(Tag).order_by(Tag.name).all()]
            releases   = sorted(r.name for r in session.query(Release).order_by(Release.name).all())
            sculptors  = sorted(s.name for s in session.query(Sculptor).order_by(Sculptor.name).all())
            categories   = sorted(c.name for c in session.query(Category).order_by(Category.name).all())
            subcategories = sorted(s.name for s in session.query(Subcategory).order_by(Subcategory.name).all())
            scales     = sorted(s.name for s in session.query(Scale).order_by(Scale.name).all())
        finally:
            session.close()

        self._title_edit  = self._autocomplete_edit(titles)
        self._author_edit = self._autocomplete_edit(authors)
        self._source_edit = self._make_combo(sources)
        self._date_edit   = self._autocomplete_edit(dates)
        self._url_edit    = QLineEdit()
        self._url_edit.setClearButtonEnabled(True)
        self._desc_edit   = QTextEdit()
        self._desc_edit.setMaximumHeight(90)
        self._tags_edit   = TagEdit(all_tags)
        self._release_edit = self._make_combo(releases, blank_first=True)

        self._sculptor_edit      = self._make_combo(sculptors, blank_first=True)
        self._category_edit      = self._make_combo(categories, blank_first=True)
        self._category_edit.currentTextChanged.connect(self._on_category_changed)
        self._subcategory_edit   = self._make_combo(subcategories, blank_first=True)

        self._sequence_spin = QSpinBox()
        self._sequence_spin.setRange(0, 9999)
        self._sequence_spin.setSpecialValueText("")   # 0 shown as blank
        self._sequence_spin.setValue(0)

        self._scale_edit = self._make_combo(scales, blank_first=True)
        self._presupports_edit = self._make_combo(["No", "Lychee", "Chitubox", "Presupported"], blank_first=True)

        form.addRow("Archive Path:", self._path_edit)
        form.addRow("Title:",        self._title_edit)
        form.addRow("Author:",       self._author_edit)
        form.addRow("Source:",       self._source_edit)
        form.addRow("Date:",         self._date_edit)
        form.addRow("Release:",      self._release_edit)
        form.addRow("Sculptor:",     self._sculptor_edit)
        form.addRow("Category:",     self._category_edit)
        form.addRow("Subcategory:",  self._subcategory_edit)
        form.addRow("Sequence:",     self._with_clear_btn(
            self._sequence_spin, lambda: self._sequence_spin.setValue(0)
        ))
        form.addRow("Scale:",        self._scale_edit)
        form.addRow("Presupports:",  self._presupports_edit)
        form.addRow("URL:",          self._url_edit)
        form.addRow("Description:",  self._with_clear_btn(
            self._desc_edit, self._desc_edit.clear
        ))
        form.addRow("Tags:",         self._tags_edit)
        left_layout.addLayout(form)

        # ---- Pattern inference group ----
        pat_group = QGroupBox("Infer from path pattern")
        pat_layout = QVBoxLayout(pat_group)

        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel("Saved:"))
        self._pat_saved_combo = QComboBox()
        self._pat_saved_combo.setPlaceholderText("— load a saved pattern —")
        self._pat_saved_combo.activated.connect(self._pat_load_saved)
        saved_row.addWidget(self._pat_saved_combo, stretch=1)
        pat_save_btn = QPushButton("Save…")
        pat_save_btn.clicked.connect(self._pat_save)
        saved_row.addWidget(pat_save_btn)
        pat_del_btn = QPushButton("Delete")
        pat_del_btn.clicked.connect(self._pat_delete)
        saved_row.addWidget(pat_del_btn)
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip(PATTERN_HELP)
        help_btn.setStyleSheet(
            "QToolButton { font-weight: bold; border: 1px solid gray;"
            " border-radius: 9px; min-width: 18px; min-height: 18px; }"
        )
        help_btn.enterEvent = lambda e: QToolTip.showText(QCursor.pos(), PATTERN_HELP, help_btn)
        saved_row.addWidget(help_btn)
        pat_layout.addLayout(saved_row)

        pat_input_row = QHBoxLayout()
        self._pattern_edit = QLineEdit()
        self._pattern_edit.setPlaceholderText("/mnt/f/{source}/{author}/*/{name}")
        self._pattern_edit.textChanged.connect(self._pat_update_preview)
        pat_input_row.addWidget(self._pattern_edit)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._pat_apply)
        pat_input_row.addWidget(apply_btn)
        pat_layout.addLayout(pat_input_row)

        self._pat_preview = QLabel("")
        self._pat_preview.setTextFormat(Qt.TextFormat.RichText)
        self._pat_preview.setWordWrap(True)
        self._pat_preview.setStyleSheet("color: #555; font-style: italic;")
        pat_layout.addWidget(self._pat_preview)

        left_layout.addWidget(pat_group)
        left_layout.addStretch()

        self._pat_refresh_saved_combo()

        splitter.addWidget(left)

        # ---- Right: thumbnail sidebar ----
        right = QWidget()
        right.setMinimumWidth(180)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Images"))

        self._thumb_content = QWidget()
        self._thumb_vlayout = QVBoxLayout(self._thumb_content)
        self._thumb_vlayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._thumb_vlayout.setSpacing(6)

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setWidget(self._thumb_content)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right_layout.addWidget(self._thumb_scroll)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # ---- Bottom: OK / Cancel ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    @staticmethod
    def _autocomplete_edit(values: list[str]) -> QLineEdit:
        edit = QLineEdit()
        completer = QCompleter(values, edit)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        edit.setCompleter(completer)
        edit.setClearButtonEnabled(True)
        return edit

    @staticmethod
    def _make_combo(values: list[str], *, blank_first: bool = False) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if blank_first:
            combo.addItem("")
        combo.addItems(values)
        combo.setCurrentIndex(0 if blank_first else -1)
        combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        combo.lineEdit().setClearButtonEnabled(True)
        return combo

    @staticmethod
    def _with_clear_btn(widget: QWidget, clear_fn) -> QWidget:
        """Wrap *widget* in a container with a small × button on the right."""
        container = QWidget()
        lay = QHBoxLayout(container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(widget, stretch=1)
        btn = QToolButton()
        btn.setText("×")
        btn.setFixedWidth(20)
        btn.setStyleSheet(
            "QToolButton { color: gray; border: none; font-size: 14px; padding: 0; }"
            "QToolButton:hover { color: #cc3333; }"
        )
        btn.clicked.connect(clear_fn)
        lay.addWidget(btn, alignment=Qt.AlignmentFlag.AlignTop)
        return container

    def _on_category_changed(self, category_name: str):
        """Repopulate subcategory combo when the category changes."""
        session = get_session()
        try:
            q = session.query(Subcategory).order_by(Subcategory.name)
            if category_name.strip():
                cat = session.query(Category).filter_by(name=category_name.strip()).first()
                if cat:
                    q = q.filter(Subcategory.category_id == cat.id)
            subcategories = [s.name for s in q.all()]
        finally:
            session.close()
        current = self._subcategory_edit.currentText()
        self._subcategory_edit.blockSignals(True)
        self._subcategory_edit.clear()
        self._subcategory_edit.addItem("")
        self._subcategory_edit.addItems(subcategories)
        self._subcategory_edit.setCurrentText(current)
        self._subcategory_edit.blockSignals(False)

    # ------------------------------------------------------------------
    def _load_model(self):
        session = get_session()
        try:
            model: STLModel = session.get(STLModel, self.model_id)
            if model is None:
                return
            self._archive_path = model.archive_path
            self._path_edit.setText(model.archive_path or "")
            self._title_edit.setText(model.title or "")
            self._author_edit.setText(model.author or "")
            self._source_edit.setCurrentText(model.source or "")
            self._date_edit.setText(format_release_date(model.release_year, model.release_month))
            self._release_edit.setCurrentText(model.release.name if model.release else "")
            self._sculptor_edit.setCurrentText(model.sculptor.name if model.sculptor else "")
            category_name = model.category.name if model.category else ""
            self._category_edit.setCurrentText(category_name)
            self._on_category_changed(category_name)   # repopulate subcategories for this category
            self._subcategory_edit.setCurrentText(model.subcategory.name if model.subcategory else "")
            self._sequence_spin.setValue(model.sequence if model.sequence is not None else 0)
            self._scale_edit.setCurrentText(model.scale.name if model.scale else "")
            self._presupports_edit.setCurrentText(model.presupports or "")
            self._url_edit.setText(model.url or "")
            self._desc_edit.setPlainText(model.description or "")
            self._tags_edit.setText(", ".join(t.name for t in model.tags))
            self._populate_thumbnails(model)
        finally:
            session.close()

    def _populate_thumbnails(self, model: STLModel):
        while self._thumb_vlayout.count():
            item = self._thumb_vlayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        sidebar_width = 200
        for img in model.images:
            lbl = QLabel()
            px = pixmap_from_path(img.thumbnail_path)
            px = px.scaled(sidebar_width, sidebar_width,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(px)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFrameShape(QFrame.Shape.StyledPanel)
            self._thumb_vlayout.addWidget(lbl)

    # ------------------------------------------------------------------
    # Pattern inference helpers (shared saved-patterns store with mass editor)
    # ------------------------------------------------------------------
    _SAVED_PATTERNS_KEY = "mass_editor/saved_patterns"
    _SEP = ":::"

    def _pat_saved_patterns(self) -> list[tuple[str, str]]:
        raw = QSettings().value(self._SAVED_PATTERNS_KEY, [])
        if isinstance(raw, str):
            raw = [raw]
        result = []
        for entry in raw:
            if self._SEP in entry:
                name, _, pattern = entry.partition(self._SEP)
                result.append((name.strip(), pattern.strip()))
        return result

    def _pat_write_saved(self, patterns: list[tuple[str, str]]) -> None:
        entries = [f"{name}{self._SEP}{pat}" for name, pat in patterns]
        QSettings().setValue(self._SAVED_PATTERNS_KEY, entries)

    def _pat_refresh_saved_combo(self) -> None:
        self._pat_saved_combo.clear()
        for name, _ in self._pat_saved_patterns():
            self._pat_saved_combo.addItem(name)

    def _pat_load_saved(self, index: int) -> None:
        patterns = self._pat_saved_patterns()
        if 0 <= index < len(patterns):
            self._pattern_edit.setText(patterns[index][1])

    def _pat_save(self) -> None:
        pattern = self._pattern_edit.text().strip()
        if not pattern:
            QMessageBox.information(self, "Save Pattern", "Enter a pattern first.")
            return
        name, ok = QInputDialog.getText(self, "Save Pattern", "Pattern name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        patterns = [(n, p) for n, p in self._pat_saved_patterns() if n != name]
        patterns.append((name, pattern))
        self._pat_write_saved(patterns)
        self._pat_refresh_saved_combo()

    def _pat_delete(self) -> None:
        idx = self._pat_saved_combo.currentIndex()
        if idx < 0:
            return
        patterns = self._pat_saved_patterns()
        if 0 <= idx < len(patterns):
            patterns.pop(idx)
            self._pat_write_saved(patterns)
            self._pat_refresh_saved_combo()

    def _pat_update_preview(self, pattern: str) -> None:
        archive_path = getattr(self, "_archive_path", "") or ""
        if not archive_path or not pattern.strip():
            self._pat_preview.clear()
            return
        extracted = parse_path(pattern.strip(), archive_path)
        if not extracted:
            self._pat_preview.setText("<i>no match</i>")
            return
        parts = [
            f"<b>{ATTR_LABEL.get(attr, attr)}:</b>&nbsp;{val}"
            for attr, val in extracted.items()
        ]
        self._pat_preview.setText("&nbsp;&nbsp;→&nbsp;&nbsp;".join(parts))

    def _pat_apply(self) -> None:
        archive_path = getattr(self, "_archive_path", "") or ""
        pattern = self._pattern_edit.text().strip()
        if not archive_path or not pattern:
            return
        extracted = parse_path(pattern, archive_path)
        if not extracted:
            return
        _field_map = {
            "title":           lambda v: self._title_edit.setText(v),
            "author":          lambda v: self._author_edit.setText(v),
            "source":          lambda v: self._source_edit.setCurrentText(v),
            "release_date":    lambda v: self._date_edit.setText(v),
            "url":             lambda v: self._url_edit.setText(v),
            "sculptor_name":   lambda v: self._sculptor_edit.setCurrentText(v),
            "category_name":    lambda v: self._category_edit.setCurrentText(v),
            "subcategory_name": lambda v: self._subcategory_edit.setCurrentText(v),
            "release_name":    lambda v: self._release_edit.setCurrentText(v),
            "sequence":        lambda v: self._sequence_spin.setValue(int(v)) if v.isdigit() else None,
        }
        for attr, value in extracted.items():
            if value and attr in _field_map:
                _field_map[attr](value)

    # ------------------------------------------------------------------
    def _save(self):
        with session_scope() as session:
            model: STLModel = session.get(STLModel, self.model_id)
            if model is None:
                self.accept()
                return

            model.title        = self._title_edit.text().strip() or None
            model.author       = self._author_edit.text().strip() or None
            model.source       = self._source_edit.currentText().strip() or None
            _year, _month = parse_release_date(self._date_edit.text().strip())
            model.release_year  = _year
            model.release_month = _month
            scale_name = self._scale_edit.currentText().strip()
            if scale_name:
                sc = session.query(Scale).filter_by(name=scale_name).first()
                if sc is None:
                    sc = Scale(name=scale_name)
                    session.add(sc)
                    session.flush()
                model.scale = sc
            else:
                model.scale = None
            model.presupports  = self._presupports_edit.currentText().strip() or None
            model.url          = self._url_edit.text().strip() or None
            model.description  = self._desc_edit.toPlainText().strip() or None
            seq = self._sequence_spin.value()
            model.sequence     = seq if seq > 0 else None

            release_name = self._release_edit.currentText().strip()
            if release_name:
                rel = session.query(Release).filter_by(name=release_name).first()
                if rel is None:
                    rel = Release(name=release_name)
                    session.add(rel)
                    session.flush()
                model.release = rel
            else:
                model.release = None

            sculptor_name = self._sculptor_edit.currentText().strip()
            if sculptor_name:
                sc = session.query(Sculptor).filter_by(name=sculptor_name).first()
                if sc is None:
                    sc = Sculptor(name=sculptor_name)
                    session.add(sc)
                    session.flush()
                model.sculptor = sc
            else:
                model.sculptor = None

            category_name = self._category_edit.currentText().strip()
            if category_name:
                cat = session.query(Category).filter_by(name=category_name).first()
                if cat is None:
                    cat = Category(name=category_name)
                    session.add(cat)
                    session.flush()
                model.category = cat
            else:
                model.category = None
                model.subcategory = None

            if model.category:
                subcategory_name = self._subcategory_edit.currentText().strip()
                if subcategory_name:
                    sc = session.query(Subcategory).filter_by(
                        name=subcategory_name, category_id=model.category.id
                    ).first()
                    if sc is None:
                        sc = Subcategory(name=subcategory_name, category_id=model.category.id)
                        session.add(sc)
                        session.flush()
                    model.subcategory = sc
                else:
                    model.subcategory = None
            else:
                model.subcategory = None

            model.tags.clear()
            for name in self._tags_edit.get_tags():
                tag = session.query(Tag).filter_by(name=name.lower()).first()
                if tag is None:
                    tag = Tag(name=name.lower())
                    session.add(tag)
                    session.flush()
                model.tags.append(tag)

        self.accept()
