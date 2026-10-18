"""Dialog for viewing and editing a creator's extended profile."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit, QDialogButtonBox,
)


class CreatorEditorDialog(QDialog):
    """View / edit the auxiliary Creator record for a given creator name.

    If no Creator row exists for the name yet, one is created on Save.
    """

    def __init__(self, creator_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Creator — {creator_name}")
        self.resize(500, 240)
        self._creator_name = creator_name
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        header = QLabel(f"<b>{self._creator_name}</b>")
        root.addWidget(header)

        form = QFormLayout()
        self._full_name_edit   = QLineEdit()
        self._patreon_edit     = QLineEdit()
        self._artstation_edit  = QLineEdit()
        self._gumroad_edit     = QLineEdit()
        self._mmf_edit         = QLineEdit()
        for edit in (self._patreon_edit, self._artstation_edit,
                     self._gumroad_edit, self._mmf_edit):
            edit.setPlaceholderText(self.tr("https://…"))
        form.addRow(self.tr("Full name:"),         self._full_name_edit)
        form.addRow(self.tr("Patreon URL:"),       self._patreon_edit)
        form.addRow(self.tr("ArtStation URL:"),    self._artstation_edit)
        form.addRow(self.tr("Gumroad URL:"),       self._gumroad_edit)
        form.addRow(self.tr("MyMiniFactory URL:"), self._mmf_edit)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _load(self) -> None:
        from app.services import lookup_admin
        c = lookup_admin.creator_profile(self._creator_name)
        if c:
            self._full_name_edit.setText(c["full_name"] or "")
            self._patreon_edit.setText(c["patreon_url"] or "")
            self._artstation_edit.setText(c["artstation_url"] or "")
            self._gumroad_edit.setText(c["gumroad_url"] or "")
            self._mmf_edit.setText(c["myminifactory_url"] or "")

    def _save(self) -> None:
        from app.services import lookup_admin
        lookup_admin.save_creator_profile(self._creator_name, {
            "full_name":         self._full_name_edit.text(),
            "patreon_url":       self._patreon_edit.text(),
            "artstation_url":    self._artstation_edit.text(),
            "gumroad_url":       self._gumroad_edit.text(),
            "myminifactory_url": self._mmf_edit.text(),
        })
        self.accept()
