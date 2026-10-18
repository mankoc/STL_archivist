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
from PyQt6.QtGui import QCursor, QPixmap



from app.services import model_edit
from app.utils.path_pattern import parse_path, ATTR_LABEL, PATTERN_HELP, format_release_date


class TagEdit(QLineEdit):
    """QLineEdit with per-token autocomplete for comma-separated tag lists.

    As the user types, the substring to the left of the cursor (up to the
    last comma) is used as the completion prefix.  Selecting a completion
    replaces only that token, leaving the rest of the field unchanged.
    """

    def __init__(self, all_tags: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(self.tr("tag1, tag2, tag3…"))
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

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._show_completions()

    def _on_text_edited(self, _text: str) -> None:
        self._show_completions()

    def _show_completions(self) -> None:
        """Update the completer prefix to the current token and show the popup."""
        token = self._current_token()
        self._completer.setCompletionPrefix(token)
        self._completer.complete()

    def _insert_completion(self, chosen: str) -> None:
        """Replace the current incomplete token with the chosen completion."""
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
        """Return the current tag list as stripped, non-empty strings."""
        return [t.strip() for t in self.text().split(",") if t.strip()]


class EditorDialog(QDialog):
    """Full metadata editor for a single STL model.

    Displays all editable fields (title, creator, sculptor, collection,
    subcollection, category, release, scale, presupports, source, URL, date,
    sequence, tags, description) in a form layout with a thumbnail sidebar.

    On accept the changes are saved through services.model_edit (one transaction).
    """

    def __init__(self, model_id: int, parent=None) -> None:
        super().__init__(parent)
        self.model_id = model_id
        self.setWindowTitle(self.tr("Edit Model"))
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

        choices = model_edit.editor_choices()
        titles         = choices["titles"]
        creators       = choices["creators"]
        sources        = choices["sources"]
        dates          = choices["dates"]
        all_tags       = choices["tags"]
        releases       = choices["releases"]
        sculptors      = choices["sculptors"]
        collections    = choices["collections"]
        subcollections = choices["subcollections"]
        categories     = choices["categories"]
        scales         = choices["scales"]

        self._title_edit  = self._autocomplete_edit(titles)
        self._creator_edit = self._autocomplete_edit(creators)
        self._source_edit = self._make_combo(sources, blank_first=True)
        self._date_edit   = self._autocomplete_edit(dates)
        self._url_edit    = QLineEdit()
        self._url_edit.setClearButtonEnabled(True)
        self._desc_edit   = QTextEdit()
        self._desc_edit.setMaximumHeight(90)
        self._tags_edit   = TagEdit(all_tags)
        self._release_edit = self._make_combo(releases, blank_first=True)

        self._sculptor_edit       = self._make_combo(sculptors, blank_first=True)
        self._collection_edit     = self._make_combo(collections, blank_first=True)
        self._collection_edit.currentTextChanged.connect(self._on_collection_changed)
        self._subcollection_edit  = self._make_combo(subcollections, blank_first=True)
        self._category_edit       = self._make_combo(categories, blank_first=True)

        self._sequence_spin = QSpinBox()
        self._sequence_spin.setRange(0, 9999)
        self._sequence_spin.setSpecialValueText("")   # 0 shown as blank
        self._sequence_spin.setValue(0)

        self._scale_edit = TagEdit(scales)
        self._scale_edit.setPlaceholderText(self.tr("28mm, 32mm, 180mm…"))
        self._presupports_edit = self._make_combo(["No", "Lychee", "Chitubox", "Presupported"], blank_first=True)

        _c0 = lambda combo: (lambda: combo.setCurrentIndex(0))

        form.addRow(self.tr("Archive Path:"),  self._path_edit)
        form.addRow(self.tr("Title:"),         self._title_edit)
        form.addRow(self.tr("Creator:"),       self._creator_edit)
        form.addRow(self.tr("Source:"),        self._with_clear_btn(self._source_edit,       _c0(self._source_edit)))
        form.addRow(self.tr("Date:"),          self._date_edit)
        form.addRow(self.tr("Release:"),       self._with_clear_btn(self._release_edit,      _c0(self._release_edit)))
        form.addRow(self.tr("Sculptor:"),      self._with_clear_btn(self._sculptor_edit,     _c0(self._sculptor_edit)))
        form.addRow(self.tr("Collection:"),    self._with_clear_btn(self._collection_edit,   _c0(self._collection_edit)))
        form.addRow(self.tr("Subcollection:"), self._with_clear_btn(self._subcollection_edit,_c0(self._subcollection_edit)))
        form.addRow(self.tr("Category:"),      self._with_clear_btn(self._category_edit,     _c0(self._category_edit)))
        form.addRow(self.tr("Sequence:"),      self._with_clear_btn(
            self._sequence_spin, lambda: self._sequence_spin.setValue(0)
        ))
        form.addRow(self.tr("Scale:"),         self._with_clear_btn(self._scale_edit,        self._scale_edit.clear))
        form.addRow(self.tr("Presupports:"),   self._with_clear_btn(self._presupports_edit,  _c0(self._presupports_edit)))
        form.addRow(self.tr("URL:"),           self._url_edit)
        form.addRow(self.tr("Description:"),   self._with_clear_btn(
            self._desc_edit, self._desc_edit.clear
        ))
        form.addRow(self.tr("Tags:"),          self._tags_edit)
        left_layout.addLayout(form)

        # ---- Pattern inference group ----
        pat_group = QGroupBox(self.tr("Infer from path pattern"))
        pat_layout = QVBoxLayout(pat_group)

        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel(self.tr("Saved:")))
        self._pat_saved_combo = QComboBox()
        self._pat_saved_combo.setPlaceholderText(self.tr("— load a saved pattern —"))
        self._pat_saved_combo.activated.connect(self._pat_load_saved)
        saved_row.addWidget(self._pat_saved_combo, stretch=1)
        pat_save_btn = QPushButton(self.tr("Save…"))
        pat_save_btn.clicked.connect(self._pat_save)
        saved_row.addWidget(pat_save_btn)
        pat_del_btn = QPushButton(self.tr("Delete"))
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
        self._pattern_edit.setPlaceholderText(self.tr("/mnt/f/{source}/{creator}/*/{name}"))
        self._pattern_edit.textChanged.connect(self._pat_update_preview)
        pat_input_row.addWidget(self._pattern_edit)
        apply_btn = QPushButton(self.tr("Apply"))
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
        right_layout.addWidget(QLabel(self.tr("Images")))

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

    def _on_collection_changed(self, collection_name: str):
        """Repopulate subcollection combo when the collection changes."""
        subcollections = model_edit.subcollection_names(collection_name)
        current = self._subcollection_edit.currentText()
        self._subcollection_edit.blockSignals(True)
        self._subcollection_edit.clear()
        self._subcollection_edit.addItem("")
        self._subcollection_edit.addItems(subcollections)
        self._subcollection_edit.setCurrentText(current)
        self._subcollection_edit.blockSignals(False)

    # ------------------------------------------------------------------
    def _load_model(self):
        model = model_edit.model_for_editor(self.model_id)
        if model is None:
            return
        self._archive_path = model["archive_path"]
        self._path_edit.setText(model["archive_path"] or "")
        self._title_edit.setText(model["title"] or "")
        self._creator_edit.setText(model["creator"] or "")
        self._source_edit.setCurrentText(model["source"] or "")
        self._date_edit.setText(format_release_date(model["release_year"], model["release_month"]))
        self._release_edit.setCurrentText(model["release"] or "")
        self._sculptor_edit.setCurrentText(model["sculptor"] or "")
        collection_name = model["collection"] or ""
        self._collection_edit.setCurrentText(collection_name)
        self._on_collection_changed(collection_name)
        self._subcollection_edit.setCurrentText(model["subcollection"] or "")
        self._category_edit.setCurrentText(model["category"] or "")
        self._sequence_spin.setValue(model["sequence"] if model["sequence"] is not None else 0)
        self._scale_edit.setText(", ".join(model["scales"]))
        self._presupports_edit.setCurrentText(model["presupports"] or "")
        self._url_edit.setText(model["url"] or "")
        self._desc_edit.setPlainText(model["description"] or "")
        self._tags_edit.setText(", ".join(model["tags"]))
        self._populate_thumbnails(model["images"])

    def _populate_thumbnails(self, images: "list[tuple[int, bool]]"):
        while self._thumb_vlayout.count():
            item = self._thumb_vlayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Read all of this model's thumbnails in a single LMDB transaction rather
        # than one round-trip per image. JPEG→QPixmap decoding stays on the GUI
        # thread (QPixmap requires it) and is cheap for a single model's images.
        from app.utils.thumbnailer import get_thumbnail_bytes_many
        ids = [image_id for image_id, has_thumb in images if has_thumb]
        blobs = get_thumbnail_bytes_many(ids) if ids else {}

        sidebar_width = 200
        for image_id, has_thumb in images:
            lbl = QLabel()
            px = QPixmap()
            data = blobs.get(image_id) if has_thumb else None
            if data:
                px.loadFromData(data)
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
            QMessageBox.information(self, self.tr("Save Pattern"), self.tr("Enter a pattern first."))
            return
        name, ok = QInputDialog.getText(self, self.tr("Save Pattern"), self.tr("Pattern name:"))
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
            self._pat_preview.setText(self.tr("<i>no match</i>"))
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
            "title":              lambda v: self._title_edit.setText(v),
            "creator":            lambda v: self._creator_edit.setText(v),
            "source":             lambda v: self._source_edit.setCurrentText(v),
            "release_date":       lambda v: self._date_edit.setText(v),
            "url":                lambda v: self._url_edit.setText(v),
            "sculptor_name":      lambda v: self._sculptor_edit.setCurrentText(v),
            "collection_name":    lambda v: self._collection_edit.setCurrentText(v),
            "subcollection_name": lambda v: self._subcollection_edit.setCurrentText(v),
            "release_name":       lambda v: self._release_edit.setCurrentText(v),
            "sequence":           lambda v: self._sequence_spin.setValue(int(v)) if v.isdigit() else None,
        }
        for attr, value in extracted.items():
            if value and attr in _field_map:
                _field_map[attr](value)

    # ------------------------------------------------------------------
    def _save(self):
        folder = model_edit.save_model_editor(self.model_id, {
            "title":         self._title_edit.text(),
            "creator":       self._creator_edit.text(),
            "source":        self._source_edit.currentText(),
            "date":          self._date_edit.text(),
            "scales":        self._scale_edit.get_tags(),
            "presupports":   self._presupports_edit.currentText(),
            "url":           self._url_edit.text(),
            "description":   self._desc_edit.toPlainText(),
            "sequence":      self._sequence_spin.value(),
            "release":       self._release_edit.currentText(),
            "sculptor":      self._sculptor_edit.currentText(),
            "collection":    self._collection_edit.currentText(),
            "subcollection": self._subcollection_edit.currentText(),
            "category":      self._category_edit.currentText(),
            "tags":          self._tags_edit.get_tags(),
        })

        from app.ui import lookup_cache as _lookup_cache
        from app.ui import sidecar_exporter
        _lookup_cache.invalidate()
        # Refresh the per-folder METADATA.7z in the background, after commit.
        if folder:
            sidecar_exporter.schedule([folder])
        self.accept()
