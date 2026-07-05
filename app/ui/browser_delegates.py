"""Item delegates for the model-browser table (inline cell editors).

Extracted from ``browser_tab.py``. Each delegate provides the editor widget for
one editable column — combo boxes backed by the shared ``lookup_cache`` for
foreign-key fields, and token-autocomplete line edits for the comma-separated
Tags and Scale columns.
"""
from PyQt6.QtCore import Qt, QStringListModel
from PyQt6.QtWidgets import QStyledItemDelegate, QComboBox, QLineEdit, QCompleter

from app.ui import lookup_cache as _lookup_cache
from app.database.db import session_scope
from app.database.models import Tag


class _ComboDelegate(QStyledItemDelegate):
    """Base delegate that shows an editable QComboBox populated by a query callable."""

    def _get_items(self) -> list[str]:
        raise NotImplementedError

    def createEditor(self, parent, option, index) -> QComboBox:
        combo = QComboBox(parent)
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(self._get_items())
        combo.setCurrentIndex(-1)
        combo.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        return combo

    def setEditorData(self, editor, index) -> None:
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText().strip())

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


class SourceDelegate(_ComboDelegate):
    """QComboBox of distinct source/distributor strings."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("source")


class SculptorDelegate(_ComboDelegate):
    """QComboBox of all sculptor names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("sculptor")


class CollectionDelegate(_ComboDelegate):
    """QComboBox of all Collection names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("collection")


class SubcollectionDelegate(_ComboDelegate):
    """QComboBox of all Subcollection names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("subcollection")


class CategoryDelegate(_ComboDelegate):
    """QComboBox of all Category names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("category")


class ReleaseDelegate(_ComboDelegate):
    """QComboBox of all Release names."""

    def _get_items(self) -> list[str]:
        return _lookup_cache.get("release")


class ScaleDelegate(QStyledItemDelegate):
    """QLineEdit that autocompletes each comma-separated token from the scale pool.

    Scale is many-to-many, so a cell holds a comma-separated list and is edited
    the same way as the Tags column.
    """

    def createEditor(self, parent, option, index) -> "_TagLineEdit":
        return _TagLineEdit(_lookup_cache.get("scale"), parent)

    def setEditorData(self, editor, index) -> None:
        editor.setText(index.data() or "")
        editor.setCursorPosition(len(editor.text()))

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.text().strip())

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


_PRESUPPORTS_OPTIONS = ["", "No", "Lychee", "Chitubox", "Presupported"]


class PresupportsDelegate(QStyledItemDelegate):
    """Fixed-option combo for the presupports column."""

    def createEditor(self, parent, option, index) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItems(_PRESUPPORTS_OPTIONS)
        return combo

    def setEditorData(self, editor, index) -> None:
        editor.setCurrentText(index.data() or "")

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.currentText())


class TagDelegate(QStyledItemDelegate):
    """QLineEdit that autocompletes each comma-separated token from the tag pool."""

    def createEditor(self, parent, option, index) -> "_TagLineEdit":
        with session_scope() as session:
            all_tags = [t.name for t in session.query(Tag).order_by(Tag.name).all()]
        edit = _TagLineEdit(all_tags, parent)
        return edit

    def setEditorData(self, editor, index) -> None:
        editor.setText(index.data() or "")
        editor.setCursorPosition(len(editor.text()))

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.text().strip())

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect)


class _TagLineEdit(QLineEdit):
    """QLineEdit with per-token autocomplete for comma-separated tag lists."""

    def __init__(self, all_tags: list[str], parent=None) -> None:
        super().__init__(parent)
        self._completer = QCompleter(self)
        self._completer.setModel(QStringListModel(all_tags, self._completer))
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert_completion)
        self.textEdited.connect(self._on_text_edited)

    def _current_token(self) -> str:
        return self.text()[:self.cursorPosition()].rsplit(",", 1)[-1].lstrip()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._show_completions()

    def _on_text_edited(self, _text: str) -> None:
        self._show_completions()

    def _show_completions(self) -> None:
        token = self._current_token()
        self._completer.setCompletionPrefix(token)
        self._completer.complete()

    def _insert_completion(self, chosen: str) -> None:
        text = self.text()
        cursor = self.cursorPosition()
        before = text[:cursor].rsplit(",", 1)
        prefix = (before[0] + ", ") if len(before) == 2 else ""
        after = text[cursor:]
        new_text = prefix + chosen + (", " if after.lstrip(",").strip() else ", ")
        self.setText(new_text)
        self.setCursorPosition(len(new_text))
