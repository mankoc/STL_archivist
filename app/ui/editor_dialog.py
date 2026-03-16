"""
Model editor dialog — all editable fields for a single STLModel.
Layout: form fields on the left, thumbnail sidebar on the right.
Opened from the browser via double-click or right-click → Edit Model.
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QTextEdit, QDialogButtonBox,
    QPushButton, QScrollArea, QWidget, QCompleter, QComboBox,
    QFrame, QSplitter,
)

from app.crawler.metadata import extract_metadata
from app.database.db import session_scope, get_session
from app.database.models import STLModel, Tag
from app.utils.thumbnailer import pixmap_from_bytes


class TagEdit(QLineEdit):
    """QLineEdit with autocomplete from existing tags."""

    def __init__(self, all_tags: list[str], parent=None):
        super().__init__(parent)
        self.setPlaceholderText("tag1, tag2, tag3…")
        completer = QCompleter(all_tags, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompleter(completer)

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
            titles  = _uniq(STLModel.title)
            authors = _uniq(STLModel.author)
            types   = _uniq(STLModel.type)
            dates   = _uniq(STLModel.release_date)
            all_tags = [t.name for t in session.query(Tag).order_by(Tag.name).all()]
        finally:
            session.close()

        self._title_edit  = self._autocomplete_edit(titles)
        self._author_edit = self._autocomplete_edit(authors)
        self._type_edit   = QComboBox()
        self._type_edit.setEditable(True)
        self._type_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._type_edit.addItems(types)
        self._type_edit.setCurrentIndex(-1)
        self._type_edit.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._type_edit.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self._date_edit   = self._autocomplete_edit(dates)
        self._url_edit    = QLineEdit()
        self._desc_edit   = QTextEdit()
        self._desc_edit.setMaximumHeight(90)
        self._tags_edit   = TagEdit(all_tags)

        form.addRow("Archive Path:", self._path_edit)
        form.addRow("Title:",        self._title_edit)
        form.addRow("Author:",       self._author_edit)
        form.addRow("Type:",         self._type_edit)
        form.addRow("Date:",         self._date_edit)
        form.addRow("URL:",          self._url_edit)
        form.addRow("Description:",  self._desc_edit)
        form.addRow("Tags:",         self._tags_edit)
        left_layout.addLayout(form)

        reinfer_btn = QPushButton("Re-infer from archive…")
        reinfer_btn.clicked.connect(self._reinfer)
        left_layout.addWidget(reinfer_btn)
        left_layout.addStretch()

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
        return edit

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
            self._type_edit.setCurrentText(model.type or "")
            self._date_edit.setText(model.release_date or "")
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
            px = pixmap_from_bytes(img.thumbnail)
            px = px.scaled(sidebar_width, sidebar_width,
                           Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(px)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFrameShape(QFrame.Shape.StyledPanel)
            self._thumb_vlayout.addWidget(lbl)

    # ------------------------------------------------------------------
    def _reinfer(self):
        if not hasattr(self, "_archive_path"):
            return
        meta = extract_metadata(Path(self._archive_path))
        if meta.title:        self._title_edit.setText(meta.title)
        if meta.author:       self._author_edit.setText(meta.author)
        if meta.distributor:  self._type_edit.setCurrentText(meta.distributor)
        if meta.release_date: self._date_edit.setText(meta.release_date)
        if meta.url:          self._url_edit.setText(meta.url)
        if meta.description:  self._desc_edit.setPlainText(meta.description)
        if meta.tags:         self._tags_edit.setText(", ".join(meta.tags))

    # ------------------------------------------------------------------
    def _save(self):
        with session_scope() as session:
            model: STLModel = session.get(STLModel, self.model_id)
            if model is None:
                self.accept()
                return

            model.title        = self._title_edit.text().strip() or None
            model.author       = self._author_edit.text().strip() or None
            model.type         = self._type_edit.currentText().strip() or None
            model.release_date = self._date_edit.text().strip() or None
            model.url          = self._url_edit.text().strip() or None
            model.description  = self._desc_edit.toPlainText().strip() or None

            model.tags.clear()
            for name in self._tags_edit.get_tags():
                tag = session.query(Tag).filter_by(name=name.lower()).first()
                if tag is None:
                    tag = Tag(name=name.lower())
                    session.add(tag)
                    session.flush()
                model.tags.append(tag)

        self.accept()
