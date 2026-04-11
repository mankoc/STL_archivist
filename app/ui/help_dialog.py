"""
Help dialog — quick-reference for STL Database.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QDialogButtonBox, QTextBrowser,
)


_HELP_HTML = """
<h2>STL Database — Quick Reference</h2>

<h3>Browser</h3>
<table>
  <tr><td><b>Double-click row</b></td><td>Edit model (path column opens editor; other columns allow inline editing)</td></tr>
  <tr><td><b>E</b></td><td>Edit selected model(s) — single selection opens the model editor, multiple opens the mass editor</td></tr>
  <tr><td><b>Ctrl+A</b></td><td>Select all rows</td></tr>
  <tr><td><b>Right-click</b></td><td>Context menu — Edit Model (single) or Mass Edit (multiple)</td></tr>
  <tr><td><b>Click thumbnail</b></td><td>Open full-size image viewer; click image to close</td></tr>
  <tr><td><b>F5</b></td><td>Refresh the table from the database</td></tr>
</table>

<h3>Crawler</h3>
<p>Open via <b>File → Open Crawler…</b> or the toolbar button.</p>
<ul>
  <li>Select a root directory; the crawler recursively finds all archive files
      (<code>.zip</code>, <code>.7z</code>, <code>.rar</code>, …).</li>
  <li>Each archive is hashed — unchanged files are skipped on subsequent runs.</li>
  <li>Archives that no longer exist on disk are <b>automatically removed</b> from the database.</li>
  <li>Metadata is inferred from path structure:
    <ul>
      <li>A folder named <b>Patreon(s)</b> sets <em>type = Patreon</em>; the next folder becomes the <em>author</em>.</li>
      <li>A folder named <b>Kickstarter(s)</b> sets <em>type = Kickstarter</em>; the next folder becomes the <em>author</em>.</li>
      <li>Dates in folder names (<code>2024-01</code>, <code>2024</code>) are used as <em>release date</em>.</li>
    </ul>
  </li>
  <li>You can define a <b>parse pattern</b> (e.g. <code>/mnt/f/{source}/{author}/*/{name}</code>) to extract
      fields from paths more precisely. Patterns can be saved and reused.</li>
  <li>Configure the maximum number of thumbnails and their size in <b>File → Configuration…</b></li>
</ul>

<h3>Tags</h3>
<ul>
  <li><b>Tags → Add Tag…</b> — create a new global tag.</li>
  <li><b>Tags → Edit Tags…</b> — rename or delete tags; merge similar tags by Levenshtein distance.</li>
  <li>Tags can be edited inline in the browser table or via the model/mass editors.</li>
</ul>

<h3>Mass Editor</h3>
<p>Select multiple rows and press <b>E</b> (or right-click → Mass Edit) to open the mass editor.</p>
<ul>
  <li>Fill in any field to overwrite that field on all selected models.</li>
  <li>Leave a field empty to leave it unchanged.</li>
  <li>The <b>Parse fields from archive path</b> section lets you extract metadata from the
      archive path using a pattern, then preview and apply the results.</li>
</ul>

<h3>Configuration</h3>
<p>Open via <b>File → Configuration…</b></p>
<ul>
  <li><b>Data directory</b> — all database files are stored here (default: <code>~/.stl_database</code>).
      Requires a restart to take effect.</li>
  <li><b>Max thumbnails per model</b> — how many preview images are extracted per archive.</li>
  <li><b>Max image size</b> — longest side in pixels when storing images.</li>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STL Database — Help")
        self.resize(720, 560)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(_HELP_HTML)
        root.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
