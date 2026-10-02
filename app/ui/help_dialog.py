"""
Multi-page help reference for stlLibrary.

Navigation is handled internally: pages are stored as HTML-returning callables
keyed by a page-ID string.  Hyperlinks use the custom scheme  help:<page_id>
and are intercepted by _on_anchor_clicked so QTextBrowser never tries to open
them externally.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox,
    QTextBrowser, QListWidget, QListWidgetItem, QSplitter,
    QPushButton, QWidget,
)

_SCREENSHOTS_DIR = Path(__file__).parent.parent.parent / "resources" / "screenshots"


def _screenshot(name: str) -> str:
    """Return an <img> tag for a screenshot, or empty string if the file is missing."""
    path = _SCREENSHOTS_DIR / name
    if not path.exists():
        return ""
    return (
        f'<p><img src="file://{path}" '
        f'style="max-width:100%;border:1px solid #888;border-radius:4px;" '
        f'alt="{path.stem}"></p>'
    )

# ── Page IDs ──────────────────────────────────────────────────────────────────
PAGE_OVERVIEW    = "overview"
PAGE_BROWSER     = "browser"
PAGE_FILTER      = "filter"
PAGE_IMAGEGRID   = "imagegrid"
PAGE_CRAWLER     = "crawler"
PAGE_SIDECARS    = "sidecars"
PAGE_TAGS        = "tags"
PAGE_EDITOR      = "editor"
PAGE_MASS_EDITOR = "mass_editor"
PAGE_CONFIG      = "config"
PAGE_STATS_TOOLS = "stats_tools"

_NAV_ITEMS = [
    (PAGE_OVERVIEW,    "Overview"),
    (PAGE_BROWSER,     "Browser"),
    (PAGE_FILTER,      "Filter Panel"),
    (PAGE_IMAGEGRID,   "Image Grid"),
    (PAGE_CRAWLER,     "Crawler"),
    (PAGE_SIDECARS,    "Metadata Sidecars"),
    (PAGE_TAGS,        "Tags"),
    (PAGE_EDITOR,      "Model Editor"),
    (PAGE_MASS_EDITOR, "Mass Editor"),
    (PAGE_CONFIG,      "Configuration"),
    (PAGE_STATS_TOOLS, "Statistics & Tools"),
]
_PAGE_IDS = [pid for pid, _ in _NAV_ITEMS]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _link(page_id: str, label: str) -> str:
    return f'<a href="help:{page_id}">{label}</a>'


def _nav_bar(current: str) -> str:
    parts = []
    for pid, label in _NAV_ITEMS:
        parts.append(f"<b>{label}</b>" if pid == current else _link(pid, label))
    inner = " &nbsp;|&nbsp; ".join(parts)
    return f'<p style="font-size:10px;color:#888;">{inner}</p><hr style="margin:4px 0 10px 0">'


def _prev_next(current: str) -> str:
    idx = _PAGE_IDS.index(current)
    left  = _link(_PAGE_IDS[idx - 1], f"&larr; {_NAV_ITEMS[idx-1][1]}") if idx > 0 else ""
    right = _link(_PAGE_IDS[idx + 1], f"{_NAV_ITEMS[idx+1][1]} &rarr;") if idx < len(_PAGE_IDS) - 1 else ""
    return (
        '<div style="margin-top:16px;overflow:hidden;">'
        f'<span style="float:left">{left}</span>'
        f'<span style="float:right">{right}</span>'
        '</div>'
    )


def _is_dark() -> bool:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QPalette
    return QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def _css() -> str:
    if _is_dark():
        return """<style>
body  { font-family: system-ui, sans-serif; font-size: 13px; margin: 8px 14px; }
h1    { font-size: 17px; margin: 0 0 4px 0; }
h2    { font-size: 14px; border-bottom: 1px solid #555; padding-bottom: 2px; margin-top: 16px; }
h3    { font-size: 13px; margin-top: 10px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; }
td, th{ padding: 3px 8px; border: 1px solid #555; vertical-align: top; }
th    { background: #3a3a3a; font-weight: bold; }
code  { background: #3a3a3a; color: #e0e0e0; padding: 1px 4px; border-radius: 2px; font-size: 12px; }
pre   { background: #2a2a2a; border: 1px solid #555; border-radius: 4px; color: #e0e0e0;
        padding: 8px 10px; font-size: 11px; white-space: pre; overflow-x: auto; }
.note { background: #3d3400; border-left: 3px solid #f9a825; padding: 5px 9px; margin: 6px 0; color: #ffe082; }
.tip  { background: #1a3a1e; border-left: 3px solid #43a047; padding: 5px 9px; margin: 6px 0; color: #a5d6a7; }
.warn { background: #3d1518; border-left: 3px solid #e53935; padding: 5px 9px; margin: 6px 0; color: #ef9a9a; }
.kb   { display: inline-block; padding: 1px 5px; border: 1px solid #666;
        border-radius: 3px; background: #3a3a3a; color: #e0e0e0; font-family: monospace; font-size: 11px; }
a     { color: #64b5f6; text-decoration: none; }
a:hover { text-decoration: underline; }
ul, ol { margin: 4px 0; padding-left: 20px; }
li    { margin: 2px 0; }
</style>"""
    return """<style>
body  { font-family: system-ui, sans-serif; font-size: 13px; margin: 8px 14px; }
h1    { font-size: 17px; margin: 0 0 4px 0; }
h2    { font-size: 14px; border-bottom: 1px solid #aaa; padding-bottom: 2px; margin-top: 16px; }
h3    { font-size: 13px; margin-top: 10px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; }
td, th{ padding: 3px 8px; border: 1px solid #ccc; vertical-align: top; }
th    { background: #e8e8e8; font-weight: bold; }
code  { background: #f0f0f0; color: #222; padding: 1px 4px; border-radius: 2px; font-size: 12px; }
pre   { background: #f5f5f5; border: 1px solid #ddd; border-radius: 4px; color: #222;
        padding: 8px 10px; font-size: 11px; white-space: pre; overflow-x: auto; }
.note { background: #fff8e1; border-left: 3px solid #f9a825; padding: 5px 9px; margin: 6px 0; color: #5d4037; }
.tip  { background: #e8f5e9; border-left: 3px solid #43a047; padding: 5px 9px; margin: 6px 0; color: #1b5e20; }
.warn { background: #fce4ec; border-left: 3px solid #e53935; padding: 5px 9px; margin: 6px 0; color: #b71c1c; }
.kb   { display: inline-block; padding: 1px 5px; border: 1px solid #aaa;
        border-radius: 3px; background: #f0f0f0; color: #222; font-family: monospace; font-size: 11px; }
a     { color: #1565c0; text-decoration: none; }
a:hover { text-decoration: underline; }
ul, ol { margin: 4px 0; padding-left: 20px; }
li    { margin: 2px 0; }
</style>"""


# ── Page content ──────────────────────────────────────────────────────────────

def _page_overview() -> str:
    # Import version here to avoid module-level circular imports
    from app import __version__
    return _css() + _nav_bar(PAGE_OVERVIEW) + f"""
<h1>stlLibrary v{__version__} &mdash; Help</h1>
<p>stlLibrary is a desktop application for cataloguing and browsing local collections
of 3D-printable model archives (<code>.zip</code>, <code>.7z</code>, <code>.rar</code>).
It extracts thumbnails, infers metadata from folder structure, and stores everything in
a local SQLite + LMDB database so you can search, filter, and tag your collection.</p>

<h2>Quick Start</h2>
<ol>
  <li>Launch the application: <code>python main.py</code></li>
  <li>Open <b>File &rarr; Configuration&hellip;</b> and set the <em>Data Directory</em>
      (defaults to <code>~/.local/share/stlLibrary</code>).</li>
  <li>Click <b>Open Crawler&hellip;</b> (toolbar or <span class="kb">Ctrl+O</span>)
      and select the root folder that contains your archive files.</li>
  <li>Optionally enter a <em>path pattern</em> such as
      <code>/mnt/f/&#123;source&#125;/&#123;author&#125;/*/&#123;name&#125;</code>
      to extract metadata from the folder structure, then press <b>Start Crawl</b>.</li>
  <li>When the crawl finishes, the <b>Browser</b> table fills with your models.
      Use the <b>Filter Panel</b> on the left to narrow results.</li>
</ol>

<h2>All Topics</h2>
<table>
  <tr><th>Topic</th><th>What you will learn</th></tr>
  <tr><td>{_link(PAGE_BROWSER,     "Browser")}</td>
      <td>Table columns, keyboard shortcuts, inline editing, image preview pane</td></tr>
  <tr><td>{_link(PAGE_FILTER,      "Filter Panel")}</td>
      <td>Include / exclude filters, group-header has-value / no-value filters, Clear button</td></tr>
  <tr><td>{_link(PAGE_IMAGEGRID,   "Image Grid")}</td>
      <td>Scrollable thumbnail grid, size slider, full-size viewer, keyboard navigation</td></tr>
  <tr><td>{_link(PAGE_CRAWLER,     "Crawler")}</td>
      <td>Crawling archives, path patterns, metadata inference, sidecar files</td></tr>
  <tr><td>{_link(PAGE_TAGS,        "Tags")}</td>
      <td>Creating, editing, merging, and applying tags</td></tr>
  <tr><td>{_link(PAGE_EDITOR,      "Model Editor")}</td>
      <td>Editing a single model&rsquo;s metadata fields</td></tr>
  <tr><td>{_link(PAGE_MASS_EDITOR, "Mass Editor")}</td>
      <td>Bulk-editing multiple models at once, path-pattern field extraction</td></tr>
  <tr><td>{_link(PAGE_CONFIG,      "Configuration")}</td>
      <td>Data directory, thumbnails, appearance, warnings, metadata sidecars</td></tr>
  <tr><td>{_link(PAGE_STATS_TOOLS, "Statistics &amp; Tools")}</td>
      <td>Statistics, column distribution, database maintenance, thumbnail generation</td></tr>
</table>

<h2>Keyboard Shortcuts &mdash; Summary</h2>
<table>
  <tr><th>Shortcut</th><th>Action</th></tr>
  <tr><td><span class="kb">Ctrl+O</span></td><td>Open Crawler</td></tr>
  <tr><td><span class="kb">F5</span></td><td>Refresh table from the database</td></tr>
  <tr><td><span class="kb">F1</span></td><td>Open this Help dialog</td></tr>
  <tr><td><span class="kb">E</span></td>
      <td>Edit selected model(s) &mdash; one row &rarr; Model Editor; multiple &rarr; Mass Editor</td></tr>
  <tr><td><span class="kb">Ctrl+A</span></td><td>Select all rows in the browser table</td></tr>
  <tr><td><span class="kb">&larr; / &rarr;</span></td><td>Navigate images in the full-size viewer</td></tr>
  <tr><td><span class="kb">Escape</span></td><td>Close the full-size image viewer</td></tr>
</table>
{_prev_next(PAGE_OVERVIEW)}"""


def _page_browser() -> str:
    return _css() + _nav_bar(PAGE_BROWSER) + f"""
<h1>Browser</h1>
<p>The browser is the main view: a sortable, filterable table of all models in the database,
with a filter panel on the left and an image preview pane on the right.</p>

<h2>Layout</h2>
{_screenshot("main_window.jpg")}
<h2>Table Columns</h2>
<table>
  <tr><th>Column</th><th>Description</th></tr>
  <tr><td><em>(thumb)</em></td><td>Thumbnail — click to open the full-size image viewer</td></tr>
  <tr><td>Title</td><td>Model name; editable inline by double-clicking</td></tr>
  <tr><td>Author</td><td>Author / publisher of the model</td></tr>
  <tr><td>Source</td><td>Origin platform (e.g. <code>Patreon</code>, <code>Kickstarter</code>),
      inferred from folder names during crawl</td></tr>
  <tr><td>Date</td><td>Release date, extracted from <code>info.json</code> or date-shaped folder names
      such as <code>2024-01</code></td></tr>
  <tr><td>Tags</td><td>Comma-separated list of tags; editable inline</td></tr>
  <tr><td>Sculptor</td><td>3-D sculptor (may differ from the publisher)</td></tr>
  <tr><td>Category</td><td>Top-level category, e.g. Fantasy, Sci-Fi</td></tr>
  <tr><td>Subcategory</td><td>Sub-category within the parent category</td></tr>
  <tr><td>Sequence</td><td>Sequence number within a release</td></tr>
  <tr><td>Release</td><td>Release name or collection title</td></tr>
  <tr><td>Path</td><td>Absolute path to the archive file — double-click to open the Model Editor</td></tr>
  <tr><td>Scale</td><td>Print scale(s) — one or more, comma-separated (e.g. <code>28mm</code>, <code>32mm</code>, <code>180mm</code>)</td></tr>
  <tr><td>Presupports</td><td>Support status: No / Lychee / Chitubox / Presupported</td></tr>
  <tr><td>Added</td><td>Date the record was added to the database</td></tr>
  <tr><td>Warnings</td><td>Flags raised during crawl (nested archive, no images, &hellip;)</td></tr>
  <tr><td>File Size</td><td>Archive size on disk (B / KB / MB / GB); read from the file system at load time; sorts numerically</td></tr>
</table>

<h2>Keyboard Shortcuts</h2>
<table>
  <tr><th>Key</th><th>Action</th></tr>
  <tr><td><span class="kb">E</span></td>
      <td>Edit — one row opens the Model Editor; multiple rows open the Mass Editor</td></tr>
  <tr><td><span class="kb">Ctrl+A</span></td><td>Select all rows</td></tr>
  <tr><td><span class="kb">F5</span></td><td>Refresh table from the database</td></tr>
  <tr><td><span class="kb">Double-click (Path column)</span></td>
      <td>Open Model Editor for that row</td></tr>
  <tr><td><span class="kb">Double-click (other columns)</span></td>
      <td>Begin inline editing of that cell</td></tr>
</table>

<h2>Context Menu</h2>
<p>Right-click any selected row(s) to open the context menu:</p>
<ul>
  <li><b>Show Files&hellip;</b> &mdash; lists every <code>.stl</code> file stored for this
      archive (filename, size, hash); single-selection only; requires <em>Store STL file
      list</em> to be enabled</li>
  <li><b>Edit Model</b> &mdash; opens the full Model Editor (single selection)</li>
  <li><b>Mass Edit</b> &mdash; opens the Mass Editor (multiple selections)</li>
  <li><b>Edit Authors&hellip;</b> &mdash; opens the Author Editor when right-clicking the
      Author column; lets you rename, delete, and merge author records across the whole
      database (see below)</li>
  <li><b>Delete N model(s) from DB&hellip;</b> &mdash; removes the selected records from the
      database without touching the archive files on disk</li>
  <li><b>Delete N archive file(s) from disk&hellip;</b> &mdash; physically deletes or moves to
      trash the archive files and also removes the database records; only visible when
      <em>Allow file delete</em> is enabled in
      <b>Configuration &rarr; File writing</b></li>
</ul>

<h2>Author Editor</h2>
<p>Right-click the <b>Author</b> column and choose <b>Edit Authors&hellip;</b> to open the
Author Editor, which lists every distinct author value in the database together with its
model count.</p>
<table>
  <tr><th>Action</th><th>Effect</th></tr>
  <tr><td><b>Rename</b></td>
      <td>Give the selected author a new name; all models and the creator profile are updated.
          If the target name already exists you are offered a merge instead.</td></tr>
  <tr><td><b>Delete</b></td>
      <td>Set <code>creator = null</code> on all affected models and remove the creator profile.</td></tr>
  <tr><td><b>Merge</b></td>
      <td>Ctrl+select exactly two authors and click Merge; choose which name to keep.
          All models from the discarded author are reassigned to the kept one.</td></tr>
  <tr><td><b>Merge Similar&hellip;</b></td>
      <td>Scans all author names for pairs within a configurable Levenshtein distance;
          works identically to the Tag editor&rsquo;s similarity merge.</td></tr>
</table>

<h2>Inline Editing</h2>
<p>Double-click a cell (except the Path column) to edit it in place.
Tags are entered as a comma-separated list.
The Presupports cell shows a dropdown.
Press <span class="kb">Enter</span> to confirm or <span class="kb">Escape</span> to cancel.</p>

<h2>Image Preview Pane</h2>
<p>Click any row to populate the right-hand pane with that model&rsquo;s thumbnails.
Click a thumbnail to open it in the full-size viewer.
Right-click a thumbnail to remove it from the database.
Toggle the pane with <b>View &rarr; Images</b> or the <b>Images</b> toolbar button.</p>

<p>Drop image files onto the pane to add them to the selected model.  Accepted
formats are <code>.jpg</code>, <code>.jpeg</code>, <code>.png</code>,
<code>.webp</code>, <code>.avif</code>, <code>.bmp</code>, <code>.gif</code>,
<code>.tif</code>, and <code>.tiff</code>.  Thumbnails are stored as resized
JPEGs, with one exception: an <b>animated GIF</b> is stored as a resized GIF and
plays in the pane (click it to watch the animation full size).  Anywhere a still
image is required &mdash; table cells, the image grid, similarity matching, AI
Tagging &mdash; the first frame is used.  Animations longer than 60 frames, or
that would exceed 2&nbsp;MB once resized, are stored as a still JPEG instead.</p>

<div class="tip">Tip: Click any column header to sort by that column; click again to reverse the order.</div>
{_prev_next(PAGE_BROWSER)}"""


def _page_filter() -> str:
    return _css() + _nav_bar(PAGE_FILTER) + f"""
<h1>Filter Panel</h1>
<p>The collapsible tree on the left side of the browser lets you narrow the table
without writing any search queries. Filters combine with AND logic &mdash; every
active filter must match for a model to appear.</p>

<h2>Filter Groups</h2>
<p>The panel contains one group for each filterable field:</p>
<table>
  <tr><th>Group</th><th>Field filtered</th></tr>
  <tr><td>Authors</td><td>Author column</td></tr>
  <tr><td>Tags</td><td>Tags column (any matching tag)</td></tr>
  <tr><td>Type</td><td>Presupports column</td></tr>
  <tr><td>Date</td><td>Release date (year or month)</td></tr>
  <tr><td>Sculptor</td><td>Sculptor column</td></tr>
  <tr><td>Category</td><td>Category column</td></tr>
  <tr><td>Subcategory</td><td>Subcategory column</td></tr>
  <tr><td>Release</td><td>Release column</td></tr>
  <tr><td>Scale</td><td>Scale column</td></tr>
  <tr><td>Presupports</td><td>Presupports status</td></tr>
  <tr><td>Warnings</td><td>Warning flags</td></tr>
  <tr><td>Path</td><td>Archive path prefix</td></tr>
</table>

<h2>Filter States</h2>
<p>Every leaf item (a specific value, e.g. &ldquo;Jane Doe&rdquo;) shows a small colored indicator
on its left. Clicking the item cycles through three states:</p>

<pre>
  &#9711; Gray   &mdash; <b>off</b>     (no filter, this value is ignored)
  &#8853; Green  &mdash; <b>include</b> (show only models matching this value)
  &#8854; Red    &mdash; <b>exclude</b> (hide all models matching this value)

  Example: including &quot;Jane Doe&quot; and excluding &quot;WIP&quot; tag would show
  only Jane Doe&rsquo;s models that are NOT tagged WIP.</pre>

<div class="note">You can combine multiple includes (OR within the same group)
and multiple excludes freely.</div>

<h2>Solo Mode (Double-Click)</h2>
<p><b>Double-clicking</b> the text label of any leaf item clears all other active filters
and checks <em>only</em> the clicked item. This is a quick way to isolate a single author,
tag, or category without having to manually uncheck everything else.</p>

<h2>Faceted (Cascading) Filtering</h2>
<p>All filter sections update dynamically whenever any other filter changes:</p>
<ul>
  <li>Each section shows only the values that exist among the models currently
      passing all <em>other</em> active filters.</li>
  <li>A section&rsquo;s own filter is <b>not</b> included in its own scope &mdash; so selecting
      one release does not hide the other releases in that group. You can always pick
      additional values from the same section.</li>
  <li>When no other filters are active, the section reverts to showing all values in the
      database.</li>
</ul>
<div class="tip">Example: filter by Creator &ldquo;Jane Doe&rdquo; and the Release group instantly
narrows to only Jane Doe&rsquo;s releases. You can then include several of her releases
without the list collapsing further.</div>

<h2>Model Counts</h2>
<p>When <em>Show model counts in filter pane</em> is enabled in
<b>Configuration &rarr; File writing</b>, each filter item shows the number of matching models
in parentheses — for example <code>Jane Doe (42)</code>. Path tree nodes accumulate the
counts of all archives in their subtrees. Counts in each section reflect
the current scope, not the full database.</p>

<h2>Group Header Filters</h2>
<p>The <em>circle indicator</em> next to a group header (e.g. the circle beside &ldquo;Authors&rdquo;)
controls a <b>has-value / no-value</b> filter for the entire column:</p>
<table>
  <tr><th>Indicator</th><th>Effect</th></tr>
  <tr><td><span style="color:#4CAF50"><b>&#8853; Green</b></span> on group header</td>
      <td>Show only models that <b>have</b> a value in that field (field is not empty)</td></tr>
  <tr><td><span style="color:#F44336"><b>&#8854; Red</b></span> on group header</td>
      <td>Show only models that have <b>no</b> value in that field (field is empty)</td></tr>
</table>

<h2>Expand / Collapse</h2>
<p>Clicking the <b>text</b> of a group header expands or collapses its children
without changing the filter state. Only clicking the <b>indicator circle</b> changes
the filter.</p>

<h2>Clear All Filters</h2>
<p>Click the <b>Clear</b> button at the top of the filter panel to reset all active
filters at once and show the full database.</p>

<div class="tip">Tip: Use &ldquo;exclude&rdquo; to hide noise quickly &mdash; for example, exclude the
<em>WIP</em> tag to focus on finished models without losing your other filters.</div>
{_prev_next(PAGE_FILTER)}"""


def _page_imagegrid() -> str:
    return _css() + _nav_bar(PAGE_IMAGEGRID) + f"""
<h1>Image Grid</h1>
<p>The Image Grid shows a scrollable thumbnail overview of every model currently
visible in the browser table &mdash; useful for a visual scan when you are looking
for a specific model by appearance rather than name.</p>

<h2>Opening the Grid</h2>
<p>Click the <b>Image Grid</b> button in the toolbar (or use <b>View &rarr; Image Grid</b>).
The grid reflects the current filter state: only models shown in the table appear in the grid.</p>

<h2>Layout</h2>
{_screenshot("image_grid.jpg")}
<h2>Size Slider</h2>
<p>Drag the <b>Size</b> slider at the top of the grid to resize all thumbnails between
<b>60 px</b> and <b>300 px</b> on the longest side. The grid reflows automatically.</p>

<h2>Opening the Full-Size Viewer</h2>
<p>Click any thumbnail to open the full-size image viewer. The viewer fills most of
the screen, keeping the image&rsquo;s aspect ratio. Use the controls at the bottom
or the keyboard to navigate:</p>

<table>
  <tr><th>Control</th><th>Action</th></tr>
  <tr><td><b>&larr; Prev</b> button or <span class="kb">&larr;</span></td>
      <td>Show the previous image in the current model&rsquo;s set</td></tr>
  <tr><td><b>Next &rarr;</b> button or <span class="kb">&rarr;</span></td>
      <td>Show the next image</td></tr>
  <tr><td>Click the image or <span class="kb">Escape</span></td>
      <td>Close the viewer and return to the grid</td></tr>
</table>

<div class="tip">Tip: Models without any stored image show a placeholder tile.
Re-run the crawler or use <b>Tools &rarr; Generate Thumbnails for Selected&hellip;</b>
to fill in missing thumbnails.</div>
{_prev_next(PAGE_IMAGEGRID)}"""


def _page_crawler() -> str:
    return _css() + _nav_bar(PAGE_CRAWLER) + f"""
<h1>Crawler</h1>
<p>The crawler scans a folder tree for archive files, extracts metadata and thumbnails,
and stores everything in the database.  Unchanged archives (identified by CRC hash)
are skipped on subsequent runs, making incremental updates fast.</p>

<h2>Opening the Crawler</h2>
<p>Click <b>Open Crawler&hellip;</b> in the toolbar, or use <b>File &rarr; Open Crawler&hellip;</b>
(<span class="kb">Ctrl+O</span>).  The crawler dialog is persistent &mdash; closing it while a
crawl is running only hides it; the worker continues in the background.</p>

<h2>Running a Crawl in the Background</h2>
<p>Once a crawl has started the <b>Run in Background</b> button becomes available.
Clicking it dismisses the Crawler dialog without stopping the worker.  A
<b>status indicator</b> appears in the bottom-right corner of the main window showing
<code>Crawler: N&nbsp;/&nbsp;total</code>.  Click the indicator at any time to bring the
Crawler dialog back.</p>

<h2>Crawler Tab Layout</h2>
{_screenshot("crawler.jpg")}
<h2>Steps</h2>
<ol>
  <li>Click <b>&#9556;</b> (Browse) next to <em>Root folder</em> and select the top-level directory
      containing your archives.</li>
  <li>Optionally enter a <b>path pattern</b> (see below).</li>
  <li>Press <b>Start Crawl</b>.  A progress bar and live log appear.</li>
  <li>When finished, the browser table refreshes automatically.</li>
</ol>

<h2>Path Patterns</h2>
<p>A path pattern maps folder segments onto model fields. Tokens are wrapped in
<code>&#123;&nbsp;&hellip;&nbsp;&#125;</code>; a bare <code>*</code> matches any segment without extracting a value.</p>

<table>
  <tr><th>Token</th><th>Extracted into</th></tr>
  <tr><td><code>&#123;source&#125;</code></td><td>Source platform</td></tr>
  <tr><td><code>&#123;creator&#125;</code> / <code>&#123;author&#125;</code></td><td>Creator / designer name</td></tr>
  <tr><td><code>&#123;title&#125;</code> / <code>&#123;name&#125;</code></td><td>Model title</td></tr>
  <tr><td><code>&#123;date&#125;</code> / <code>&#123;month&#125;</code></td><td>Release date (<code>YYYY-MM</code> or <code>YYYY</code>)</td></tr>
  <tr><td><code>&#123;sculptor&#125;</code></td><td>Sculptor (looked up or created)</td></tr>
  <tr><td><code>&#123;collection&#125;</code> / <code>&#123;category&#125;</code></td><td>Collection</td></tr>
  <tr><td><code>&#123;subcollection&#125;</code> / <code>&#123;subcategory&#125;</code></td><td>Subcollection / variant</td></tr>
  <tr><td><code>&#123;release&#125;</code></td><td>Release / bundle name</td></tr>
  <tr><td><code>&#123;sequence&#125;</code></td><td>Position in a series</td></tr>
  <tr><td><code>&#123;url&#125;</code></td><td>Source URL</td></tr>
  <tr><td><code>*</code></td><td>(ignored segment)</td></tr>
</table>

<h3>Prefix modifier <code>{{token:n}}</code></h3>
<p>Append <code>:n</code> to any token to use only the first <em>n</em> characters of its
value when rendering a destination path (e.g. in the file-sort dialog).
This is useful for creating initial-letter sub-folders:</p>
<pre>
&#123;creator:1&#125;/&#123;creator&#125;/&#123;title&#125;

With creator &quot;Sanix&quot; → S/Sanix/&lt;title&gt;</pre>
<div class="tip">The <code>:n</code> modifier applies to file-move output only.
When parsing an existing path for metadata extraction the full segment is captured regardless.</div>

<h3>Pattern Examples</h3>
<pre>
Folder layout:
  /mnt/f/Patreons/Jane Doe/2024-01 - Dragon Set/dragon_warrior.zip

Pattern:
  /mnt/f/&#123;source&#125;/&#123;author&#125;/&#123;release&#125;/&#123;name&#125;

Extracted:
  source  = &quot;Patreons&quot;
  author  = &quot;Jane Doe&quot;
  release = &quot;2024-01 - Dragon Set&quot;
  name    = &quot;dragon_warrior&quot;</pre>

<pre>
Folder layout:
  /mnt/f/Kickstarters/AwesomeMinis/Wave3/orc_berseker.zip

Pattern:
  /mnt/f/&#123;source&#125;/&#123;author&#125;/*/&#123;name&#125;

Extracted:
  source = &quot;Kickstarters&quot;
  author = &quot;AwesomeMinis&quot;
  name   = &quot;orc_berseker&quot;</pre>

<h2>Saving and Loading Patterns</h2>
<p>Enter a pattern and click <b>Save</b> to give it a name and add it to the
<em>Saved</em> dropdown. Select a saved pattern and click <b>Delete</b> to remove it.
The <b>Preview</b> line updates in real time as you type, showing what a randomly
chosen file path would extract.</p>

<h2>Apply Path Pattern Toggle</h2>
<p>The <b>Apply path pattern to metadata extraction</b> checkbox sits <em>above</em> the
<em>Parse metadata from path</em> section and acts as both the on/off switch and the
expander for the whole pattern editor. The checkbox is enabled only when the
pattern field contains text.</p>
<ul>
  <li><b>Checked</b> — the pattern editor is shown and pattern extraction is active;
      field values are inferred from the folder structure.</li>
  <li><b>Unchecked</b> — the pattern editor is hidden and the pattern is <em>not</em>
      applied; only automatic inference and <code>info.json</code> are used. The pattern
      text itself is preserved.</li>
</ul>
<div class="tip">Tip: untick the checkbox when you want to try a crawl without pattern
extraction but don&rsquo;t want to lose the pattern text you typed.</div>

<h2>Override Metadata</h2>
<p>Click the <b>Override metadata (leave blank to auto-detect)</b> header to expand the
collapsible section (collapsed by default) and force specific field values across every
archive in the current crawl:</p>
<table>
  <tr><th>Field</th><th>Effect</th></tr>
  <tr><td><b>Creator</b></td>
      <td>Overrides the author/creator field on every imported archive.
          Useful when crawling a folder that belongs entirely to one artist.</td></tr>
  <tr><td><b>Type / Source</b></td>
      <td>Overrides the source platform field (e.g. <code>Patreon</code>,
          <code>Kickstarter</code>) on every imported archive.
          Useful when the folder name does not match a known platform keyword.</td></tr>
</table>
<p>Leave either field blank to use the auto-detected value as normal. Override values are
applied after all automatic inference, so they always take precedence over path patterns
and <code>info.json</code>.</p>

<h2>Update Crawled Folders</h2>
<p>Open <b>Tools &rarr; Update Crawled Folders&hellip;</b> to re-crawl all folders that were
previously imported. The dialog shows a checkbox list of every stored folder:</p>
<ul>
  <li>All folders are pre-selected; untick any you want to skip.</li>
  <li>Use <b>Select All</b> / <b>Deselect All</b> to quickly toggle the whole list.</li>
  <li>Click <b>Start</b> to process only the checked folders sequentially;
      the progress bar and log update for each folder in turn.</li>
</ul>

<h2>Automatic Metadata Inference (without a pattern)</h2>
<p>Even without a pattern, the crawler infers some metadata from the folder structure:</p>
<ul>
  <li>A folder named <b>Patreon</b> or <b>Patreons</b> sets <em>source = Patreon</em>;
      the next folder becomes the <em>author</em>.</li>
  <li>A folder named <b>Kickstarter</b> or <b>Kickstarters</b> sets
      <em>source = Kickstarter</em>; the next folder becomes the <em>author</em>.</li>
  <li>Folder names matching <code>YYYY-MM</code> or <code>YYYY</code> are used as
      the <em>release date</em>.</li>
  <li>An <code>info.json</code> file inside the archive overrides all path-inferred values.</li>
</ul>

<h2>Moved Archive Detection</h2>
<p>When the crawler would normally delete a record for a missing archive, it first searches
the set of newly-discovered archives for a match:</p>
<ol>
  <li><b>Hash match</b> (definitive) &mdash; if a new archive has the same CRC fingerprint as
      the missing record, the old record&rsquo;s path is updated to the new location.</li>
  <li><b>Filename + size match</b> (fallback) &mdash; if no hash match is found, a match on
      both filename and file size is used instead.</li>
</ol>
<p>On a match all existing metadata, images, and thumbnails are preserved under the new path.
Archives that match neither criterion are removed as before.</p>

<h2>Running in the Background</h2>
<p>When the Crawler dialog is hidden and the crawl finishes, the dialog is automatically
brought back to the foreground so you do not miss the completion notification.</p>

<h2>Deleted Archives</h2>
<p>If a missing archive cannot be matched to any newly-discovered file during a crawl, its
database record is <b>automatically removed</b>.</p>

<h2>Four-Phase Pipeline</h2>
<table>
  <tr><th>Phase</th><th>What happens</th><th>Parallelism</th></tr>
  <tr><td>1. Hash</td><td>Read CRC checksums from archive catalog (no decompression)</td><td>Parallel</td></tr>
  <tr><td>2. DB query</td><td>Check which archives are new or changed</td><td>Serial</td></tr>
  <tr><td>3. Extract</td><td>Decompress only new / changed archives; extract images and metadata</td><td>Parallel</td></tr>
  <tr><td>4. Upsert</td><td>Write records to SQLite</td><td>Serial</td></tr>
</table>

<h2>Metadata Sidecars</h2>
<p>When enabled (see {_link(PAGE_CONFIG, "Configuration")}), the crawler writes a
<code>METADATA.7z</code> sidecar file into each crawled folder after processing it.
This sidecar contains a snapshot of all metadata for that folder, enabling a fast
<em>restore</em> on a new machine without re-crawling every archive from scratch.</p>
{_prev_next(PAGE_CRAWLER)}"""


def _page_tags() -> str:
    return _css() + _nav_bar(PAGE_TAGS) + f"""
<h1>Tags</h1>
<p>Tags are free-form labels that can be attached to any number of models.
They appear in the browser table and in the filter panel, making them powerful
for informal categorization that cuts across the formal Category / Subcategory hierarchy.</p>

<h2>Creating Tags</h2>
<p>Open <b>Tags &rarr; Add Tag&hellip;</b> in the menu bar, enter a name, and press OK.
The new tag is immediately available for use in all editors.</p>

<h2>Editing Tags (Tag Editor)</h2>
<p>Open <b>Tags &rarr; Edit Tags&hellip;</b> to manage the global tag list:</p>
<ul>
  <li><b>Rename</b> a tag by double-clicking its name and typing the new label.
      All models using the tag are updated automatically.</li>
  <li><b>Delete</b> a tag to remove it from the tag list and from every model that used it.</li>
  <li><b>Merge by similarity</b> &mdash; set a <em>Levenshtein distance</em> threshold and
      click <b>Find Similar</b> to see pairs of tags that differ by at most that many
      character edits. Merging two similar tags consolidates them into one.</li>
</ul>

<h2>Applying Tags to Models</h2>
<p>Tags can be set in three ways:</p>
<table>
  <tr><th>Method</th><th>How</th></tr>
  <tr><td>Inline editing</td>
      <td>Double-click the <em>Tags</em> cell in the browser table and type a comma-separated list</td></tr>
  <tr><td>Model Editor</td>
      <td>Use the tag picker in the single-model editor ({_link(PAGE_EDITOR, "Model Editor")})</td></tr>
  <tr><td>Mass Editor</td>
      <td>Apply the same tags to all selected models at once ({_link(PAGE_MASS_EDITOR, "Mass Editor")})</td></tr>
</table>

<div class="tip">Tip: Tags are case-sensitive. &ldquo;WIP&rdquo; and &ldquo;wip&rdquo; are different tags.
Use the editor&rsquo;s similarity merge to consolidate accidental duplicates.</div>
{_prev_next(PAGE_TAGS)}"""


def _page_editor() -> str:
    return _css() + _nav_bar(PAGE_EDITOR) + f"""
<h1>Model Editor</h1>
<p>The Model Editor lets you view and update all metadata for a <b>single model</b>.</p>

<h2>Opening the Editor</h2>
<ul>
  <li>Double-click the <b>Path</b> column of any row in the browser table.</li>
  <li>Select a single row and press <span class="kb">E</span>.</li>
  <li>Right-click a single row and choose <b>Edit Model</b>.</li>
</ul>

<h2>Layout</h2>
{_screenshot("model_editor.jpg")}
<h2>Fields</h2>
<table>
  <tr><th>Field</th><th>Notes</th></tr>
  <tr><td>Title</td><td>Free text</td></tr>
  <tr><td>Author</td><td>Free text; auto-completes from existing authors</td></tr>
  <tr><td>Source</td><td>Free text (e.g. <code>Patreon</code>, <code>Kickstarter</code>)</td></tr>
  <tr><td>Date</td><td>Free text; formats <code>YYYY-MM</code> or <code>YYYY</code> accepted</td></tr>
  <tr><td>Sculptor</td><td>Auto-completes from existing sculptors</td></tr>
  <tr><td>Category</td><td>Dropdown populated from the existing category list</td></tr>
  <tr><td>Subcategory</td><td>Dropdown filtered to the selected category</td></tr>
  <tr><td>Release</td><td>Dropdown populated from the existing release list</td></tr>
  <tr><td>Scale</td><td>One or more scales, comma-separated with autocomplete (e.g. 28mm, 32mm, 180mm)</td></tr>
  <tr><td>Presupports</td><td>Dropdown: No / Lychee / Chitubox / Presupported</td></tr>
  <tr><td>Tags</td><td>Comma-separated tag names; auto-complete on each token</td></tr>
  <tr><td>Notes</td><td>Free-form notes; not displayed in the browser table</td></tr>
</table>

<p>Click <b>Save</b> to write changes to the database or <b>Cancel</b> to discard them.</p>
{_prev_next(PAGE_EDITOR)}"""


def _page_mass_editor() -> str:
    return _css() + _nav_bar(PAGE_MASS_EDITOR) + f"""
<h1>Mass Editor</h1>
<p>The Mass Editor lets you update one or more fields across <b>all currently selected
models</b> in a single operation.  Leave a field empty to leave it unchanged.</p>

<h2>Opening the Mass Editor</h2>
<ul>
  <li>Select multiple rows in the browser table and press <span class="kb">E</span>.</li>
  <li>Right-click the selection and choose <b>Mass Edit</b>.</li>
</ul>

<h2>Layout</h2>
{_screenshot("mass_editor.jpg")}
<h2>Manual Field Overwrite</h2>
<p>Fill in any field in the lower section to overwrite that field on all selected models.
Leaving a field empty means &ldquo;do not change&rdquo;.</p>
<p>Each field also has a <b>clear</b> checkbox: when checked, that field is explicitly set to
blank on all selected models, regardless of its current value.</p>

<div class="tip">Tip: The autocomplete suggestions for <em>Collection</em>, <em>Subcollection</em>,
<em>Release date</em>, and <em>Tags</em> are scoped to the values that already exist among
the selected models, keeping the list short and relevant.</div>

<h2>Path Pattern Extraction</h2>
<p>The upper section offers an optional <em>path pattern</em> that can extract field
values individually for each selected model based on its own archive path.
This is useful when you have selected a batch of models whose paths all follow the
same folder structure but have different values:</p>

<ol>
  <li>Enter a pattern using the same <code>&#123;token&#125;</code> syntax as the Crawler
      (see {_link(PAGE_CRAWLER, "Crawler &rarr; Path Patterns")}). The line beneath shows what
      it extracts from the example path.</li>
  <li>Optionally click <b>Parse all paths</b> to check how many of the selected paths match,
      or <b>Show parsed values</b> to see exactly what each model's path yields in a table
      &mdash; one row per selected model, one column per extracted field, with unmatched paths
      marked <em>no match</em>.</li>
  <li>Click <b>OK</b> to apply. The paths are parsed at this point whether or not you pressed
      the button, so the pattern in the box is always the one applied.</li>
</ol>

<div class="note">Path-extracted values are applied <em>per model</em>; manual field values are applied
<em>uniformly</em> to all selected models. Both happen in the same Apply operation.</div>

<div class="warn">Parsed values only fill fields that are currently <em>empty</em> unless
<b>Overwrite existing field values with parsed values</b> is ticked. Models catalogued by the
crawler already have a title, so re-titling them from a pattern needs that box checked.</div>

<div class="tip">If the pattern matches none of the selected paths, Mass Edit tells you and asks
whether to apply the rest of the changes &mdash; so a pattern that silently extracts nothing
does not look like it worked.</div>

<h2>Trimming Whitespace</h2>
<p><b>Trim leading/trailing whitespace from text fields</b> strips spaces and tabs from the start
and end of <em>Title</em>, <em>Creator</em>, <em>Source</em>, <em>URL</em> and
<em>Description</em> on every selected model. It runs last, so values parsed from the path,
typed in the manual overrides, or produced by Find &amp; Replace are trimmed too &mdash; useful
when a pattern such as <code>&#123;release&#125; -&#123;title&#125;</code> leaves a stray space.
A field containing nothing but whitespace is emptied.</p>

<div class="note">Sculptor, Collection, Subcollection, Category and Release names are not
trimmed: they are shared lookup values, so renaming one would affect every other model using
it.</div>

<h2>Saving and Loading Patterns</h2>
<p>Just like the Crawler tab, the Mass Editor lets you save patterns to the
<em>Saved</em> dropdown and recall them for future use.</p>

<h2>Find &amp; Replace</h2>
<p>The <b>Find &amp; Replace</b> section at the bottom applies a substring substitution to one
or more text fields on all selected models.</p>
<table>
  <tr><th>Control</th><th>Description</th></tr>
  <tr><td><b>Find</b></td>
      <td>The substring to search for (treated as a literal string, not a regex).</td></tr>
  <tr><td><b>Replace with</b></td>
      <td>The replacement string; leave empty to delete the matched substring.</td></tr>
  <tr><td><b>Apply to</b></td>
      <td>Checkboxes for <em>Title</em>, <em>Creator</em>, <em>Source</em>, and <em>URL</em>;
          tick any combination to apply the replacement to those fields.</td></tr>
  <tr><td><b>Case-sensitive</b></td>
      <td>When checked, the search is case-sensitive; otherwise it matches regardless of case.</td></tr>
</table>
<div class="note">Find &amp; Replace runs after all manual field overwrites and path-pattern
extractions within the same OK operation.</div>
{_prev_next(PAGE_MASS_EDITOR)}"""


def _page_config() -> str:
    return _css() + _nav_bar(PAGE_CONFIG) + f"""
<h1>Configuration</h1>
<p>Open <b>File &rarr; Configuration&hellip;</b> to access application settings.
The dialog is divided into sections shown in a tree on the left.</p>

<h2>Database</h2>
<table>
  <tr><th>Setting</th><th>Description</th></tr>
  <tr><td>Data Directory</td>
      <td>Where the <code>.db</code> file, LMDB thumbnail store, and log files are kept.
          Default: <code>~/.local/share/stlLibrary</code>.
          <b>A restart is required after changing this.</b></td></tr>
  <tr><td>unrar path</td>
      <td>Full path to the <code>unrar</code> binary, required to open
          <code>.rar</code> archives.  Leave blank if you have no RAR files.</td></tr>
</table>
<div class="note">The environment variables <code>STL_DATA_DIR</code> and
<code>STL_UNRAR_PATH</code>, when set, take precedence over these two settings.</div>

<h2>Language</h2>
<table>
  <tr><th>Setting</th><th>Description</th></tr>
  <tr><td>Language</td>
      <td>Interface language. <b>English</b> and <b>Spanish</b> ship built-in;
          any additional <code>&lt;code&gt;.ts</code> language definition placed in
          <code>resources/i18n/</code> appears here automatically. Strings with no
          translation fall back to English.
          <b>Restart the application for the change to fully take effect.</b></td></tr>
</table>

<h2>Crawler</h2>
<table>
  <tr><th>Setting</th><th>Description</th></tr>
  <tr><td>Max thumbnails per model</td>
      <td>How many preview images are extracted from each archive.
          Higher values give more choices in the grid but use more disk space.</td></tr>
  <tr><td>Max image size (px)</td>
      <td>Longest side in pixels when storing thumbnails in LMDB.
          Larger values look better in the Image Grid but increase storage.</td></tr>
  <tr><td>Max worker threads</td>
      <td>Degree of parallelism for hashing and extraction phases.
          Defaults to the number of CPU cores.</td></tr>
  <tr><td>Sort files within archive</td>
      <td>When enabled, image files inside an archive are sorted before
          extraction, giving a deterministic thumbnail order.</td></tr>
</table>

<h2>Warnings</h2>
<p>Control which conditions the crawler records in the <em>Warnings</em> column:</p>
<table>
  <tr><th>Warning</th><th>Meaning</th></tr>
  <tr><td>Pre-supported STL detected</td>
      <td>The archive contains a pre-supported <code>.stl</code> &mdash; either one named as
          such (<code>body_presupported.stl</code>, <code>sup_arm.stl</code>) or one sitting in
          a folder that marks its contents as pre-supported (<code>Presupported/</code>,
          <code>PS/</code>, <code>Supported STL/</code>, <code>Pre-Supported/</code>).
          Folder names are matched as whole words, so <code>PSD Sources/</code> and
          <code>Props/</code> do not count. An &ldquo;unsupported&rdquo; marker always wins,
          wherever it appears &mdash; in any folder above the file or in the mesh&rsquo;s own
          name. So <code>No_Supported/</code>, <code>Unsupported/</code>,
          <code>Non-Supported/</code>, <code>Without Supports/</code> and <code>No_PS/</code>
          are never flagged, and neither is
          <code>supports/Unsupported/120MM_Unsup_Emerald.stl</code>, where the raw meshes are
          nested inside the supported tree. A clean sibling in the same folder
          (<code>supports/120MM_Emerald.stl</code>) is still flagged &mdash; the test is
          per-file. Only <code>.stl</code> members qualify: images, readmes, slicer files,
          and the folder entries themselves never raise it. The filename patterns are editable
          in <b>Configuration &rarr; Crawler warnings</b>; the folder and negation rules are
          built in</td></tr>
  <tr><td>No images found</td>
      <td>The archive contains no image files (<code>.jpg</code>, <code>.jpeg</code>,
          <code>.png</code>, <code>.webp</code>, <code>.avif</code>, <code>.gif</code>,
          <code>.bmp</code>) — no thumbnail can be shown</td></tr>
  <tr><td>Nested archive detected</td>
      <td>A <code>.zip</code> / <code>.7z</code> / <code>.rar</code> is found inside the archive</td></tr>
  <tr><td>Archive without printable files</td>
      <td>The archive contains no file with a printable extension
          (<code>.stl</code>, <code>.obj</code>, <code>.lys</code>, <code>.chitubox</code>)
          &mdash; may indicate a non-model archive or a content issue</td></tr>
</table>
<p>The <em>Support file patterns</em> list (editable in the Warnings section) defines
name substrings that trigger the &ldquo;support files detected&rdquo; warning.
Add, edit, or remove patterns as needed for your naming conventions.</p>

<h2>File writing</h2>
<table>
  <tr><th>Setting</th><th>Description</th></tr>
  <tr><td>Show model counts in filter pane</td>
      <td>When enabled, each filter panel item shows the number of matching models in
          parentheses (e.g. <code>Jane Doe (42)</code>). Path-tree nodes show cumulative
          counts. Disable for a less cluttered panel or on very large collections.</td></tr>
  <tr><td>Enable file sorting</td>
      <td>Adds a <em>Sort Files into Directory&hellip;</em> right-click menu entry that
          moves and renames archive files into a structured layout using a path pattern.</td></tr>
  <tr><td>Allow file delete</td>
      <td>Enables the <em>Delete archive file(s) from disk&hellip;</em> right-click menu
          item.  <span style="color:#d32f2f;font-weight:bold;">Destructive &mdash;</span>
          guarded by a confirmation dialog that requires ticking a checkbox before the OK
          button is enabled.</td></tr>
  <tr><td>Use trash folder</td>
      <td>When <em>Allow file delete</em> is on, move files to the configured trash folder
          instead of permanently deleting them.  A JSON recovery info file is written
          alongside the moved archive.  Only editable when <em>Allow file delete</em>
          is checked.</td></tr>
  <tr><td>Trash folder</td>
      <td>Destination folder for moved archives.  Use the <b>Browse&hellip;</b> button to
          pick a path.</td></tr>
  <tr><td>Enable Debug menu</td>
      <td>Shows the <b>Debug</b> menu in the menu bar, which provides
          <em>Delete Database</em> and <em>Delete Logs</em> actions.
          Keep this off in everyday use.</td></tr>
</table>

<h2>File writing &rarr; Sidecar</h2>
<table>
  <tr><th>Setting</th><th>Description</th></tr>
  <tr><td>Enable metadata sidecars</td>
      <td>Write <code>METADATA.7z</code> sidecar files into each crawled folder.
          When enabled, <b>Tools &rarr; Generate / Update Metadata Sidecars&hellip;</b>
          also becomes available.</td></tr>
  <tr><td>Include thumbnails</td>
      <td>Store JPEG thumbnails inside the sidecar as well as metadata.
          Makes the sidecar larger but allows a full restore without re-downloading images.</td></tr>
  <tr><td>Store STL file list</td>
      <td>Record the name and uncompressed size of every <code>.stl</code> file found inside
          each archive in a dedicated <code>stl_files</code> database table.  Required for
          <b>Tools &rarr; Find Duplicate Archives by Content &rarr; STL Files</b> tab.
          Re-crawl existing archives after enabling to populate historical entries.
          The list is also written into and restored from METADATA.7z sidecars.</td></tr>
</table>


<h2>Browser</h2>
<table>
  <tr><th>Setting</th><th>Description</th></tr>
  <tr><td>Show model counts in filter pane</td>
      <td>When enabled, each filter panel item shows the number of matching models in
          parentheses (e.g. <code>Jane Doe (42)</code>). Path-tree nodes show cumulative
          counts. Disable for a less cluttered panel or on very large collections.</td></tr>
  <tr><td>Group large sections into letter bins</td>
      <td>Splits filter sections that exceed the <em>Split above</em> threshold into
          alphabetical sub-groups (A, B, C&hellip;) to reduce scrolling.</td></tr>
  <tr><td>Split above (items)</td>
      <td>Minimum number of items in a filter section before alphabetical grouping activates.
          Only editable when letter bins are enabled.</td></tr>
  <tr><td>Open folder with</td>
      <td>Program used when <b>Open Folder in Explorer</b> is chosen from a right-click menu.
          <ul>
            <li><b>System Default</b> &mdash; delegates to the OS (Explorer on Windows,
                Nautilus / Dolphin on Linux, Finder on macOS).</li>
            <li><b>Double Commander</b> &mdash; launches Double Commander with the target
                folder; the executable path pre-fills with the platform default
                (<code>doublecmd.exe</code> on Windows, <code>doublecmd</code> on Linux).</li>
            <li><b>Custom&hellip;</b> &mdash; enter any executable path; use
                <b>Browse&hellip;</b> to pick the file.</li>
          </ul>
          The <b>Executable</b> path field and Browse button are hidden when <em>System
          Default</em> is selected.</td></tr>
</table>

<h2>Appearance</h2>
<table>
  <tr><th>Setting</th><th>Description</th></tr>
  <tr><td>Color scheme</td>
      <td>Choose a built-in theme (System, Win11 Light/Dark, VS Code Dark, or a
          Material variant). A live preview is shown as you select each option.</td></tr>
  <tr><td>Custom Colors</td>
      <td>Collapsible section (3-column grid) of per-element colour overrides:
          duplicate-highlight, status indicators, warning rows, maintenance view,
          highlights, and window background/text. Includes <em>Reset all to theme
          defaults</em> and <em>Save as color scheme…</em>.</td></tr>
  <tr><td>Typography</td>
      <td>Override the application font family and size (0 = theme default).</td></tr>
</table>

<div class="note">Changes to the Data Directory take effect only after restarting the application.</div>
{_prev_next(PAGE_CONFIG)}"""


def _page_stats_tools() -> str:
    return _css() + _nav_bar(PAGE_STATS_TOOLS) + f"""
<h1>Statistics &amp; Tools</h1>

<h2>Statistics &mdash; Show Statistics&hellip;</h2>
<p>Open via <b>Statistics &rarr; Show Statistics&hellip;</b></p>
<p>Displays aggregate counts and breakdowns about the entire database:</p>
<ul>
  <li>Total models, total images, total tags, total archives</li>
  <li>Total size &mdash; compressed (sum of archive sizes) and uncompressed
      (sum of the catalogued file sizes inside the archives)</li>
  <li>Storage used by the database file and the thumbnail store</li>
  <li>Models by source, by presupport status, by scale</li>
  <li>Breakdown by top authors, categories, sculptors, and release names</li>
  <li>Bar charts show the distribution of each field visually</li>
</ul>
<p>Click <b>Refresh</b> to reload the statistics after a crawl.</p>

<h2>Statistics &mdash; Column Distribution&hellip;</h2>
<p>Open via <b>Statistics &rarr; Column Distribution&hellip;</b></p>
<p>Pick any column from a dropdown to see a bar chart showing how many models fall
into each distinct value for that column. Useful for spotting naming inconsistencies
(e.g. &ldquo;Patreon&rdquo; vs &ldquo;Patreons&rdquo;) or for understanding the spread of a dimension
like Scale or Category.</p>

<h2>Tools &mdash; Generate Thumbnails for Selected&hellip;</h2>
<p>Open via <b>Tools &rarr; Generate Thumbnails for Selected&hellip;</b></p>
<p>Select one or more rows in the browser table and use this action to re-extract
thumbnails from their archives. Handy when the original crawl ran with a lower
<em>Max thumbnails</em> setting or when thumbnails are missing.</p>

<h2>Tools &mdash; Generate / Update Metadata Sidecars&hellip;</h2>
<p>Open via <b>Tools &rarr; Generate / Update Metadata Sidecars&hellip;</b>
(only available when sidecars are enabled in {_link(PAGE_CONFIG, "Configuration")})</p>
<p>Writes or refreshes <code>METADATA.7z</code> sidecar files for <em>all</em> folders
currently represented in the database. Progress is shown in a dialog with a live log.</p>
<p>Use this after a crawl has finished importing metadata but before moving the archive
collection to a new machine, so the sidecar snapshots are up-to-date.</p>

<h2>Tools &mdash; Find Metadata Duplicates&hellip;</h2>
<p>Open via <b>Tools &rarr; Find Metadata Duplicates&hellip;</b></p>
<p>Searches the database for models that share identical or phonetically similar values
in one or more metadata fields. The dialog is <em>non-modal</em> — results are visible
alongside the browser table and clicking a button filters the browser directly.</p>

<h3>Columns to check</h3>
<p>Check any combination of: Title, Author, Source, Sculptor, Category, Subcategory, Release, Scale.</p>

<h3>Match methods</h3>
<table>
  <tr><th>Method</th><th>How it works</th></tr>
  <tr><td><b>Exact match</b></td>
      <td>Groups all models whose field value is character-for-character identical
          (case-sensitive, whitespace-trimmed).</td></tr>
  <tr><td><b>Levenshtein</b></td>
      <td>Reports pairs of models whose field values are within a configurable
          <em>max edit distance</em> (1&ndash;20, default 3). Useful for catching typos
          and minor spelling variations. A length pre-filter skips obviously
          different strings for performance.</td></tr>
  <tr><td><b>Soundex (phonetic)</b></td>
      <td>Groups models whose field value encodes to the same 4-character Soundex
          code, catching names that sound the same but are spelled differently
          (e.g. &ldquo;Smith&rdquo; / &ldquo;Smyth&rdquo; / &ldquo;Smythe&rdquo;).
          Groups where every entry is identical are excluded (use Exact match instead).</td></tr>
</table>

<h3>Results table</h3>
<p>Each row shows: <b>Group</b> number, <b>Column</b> checked, model <b>ID</b>,
<b>Title</b>, the matched <b>Value</b>, and the <b>Match</b> score or method.
The table is sortable by any column.</p>

<h3>Sending results to the browser</h3>
<ul>
  <li><b>Show Group in Main Table</b> &mdash; select any row, then click to filter the browser
      to all models in that duplicate group.</li>
  <li><b>Show All Groups in Main Table</b> &mdash; filter the browser to every model that
      appears in any duplicate group found by the last search.</li>
</ul>

<div class="tip">Tip: Levenshtein search on very large collections (thousands of models)
is O(n&sup2;) — use the Stop button to cancel if it takes too long, or reduce the
max distance to 1 or 2 to narrow the scope.</div>

<h2>Tools &mdash; Find Duplicate Archives by Content&hellip;</h2>
<p>Open via <b>Tools &rarr; Find Duplicate Archives by Content&hellip;</b></p>
<p>A <em>non-modal</em> dialog with two independent tabs that identify pairs of archives
sharing content &mdash; either the same STL files or visually similar thumbnail images.</p>

<h3>STL Files tab</h3>
<p>Compares every pair of archives by the fraction of <code>.stl</code> files they share
(matched by filename and uncompressed size).  A pair is flagged when at least
<em>Similarity threshold %</em> of the <b>larger</b> archive&rsquo;s files appear in the other.</p>
<p>Selecting a result row shows a preview pane with the thumbnails of <b>both</b> archives
side by side, so you can confirm visually that the two are the same model before acting on them.</p>
<table>
  <tr><th>Parameter</th><th>Description</th></tr>
  <tr><td>Similarity threshold</td>
      <td>1&ndash;100 % (default 80 %).  Higher values = fewer, more certain matches.</td></tr>
</table>
<div class="note">Requires <b>Store STL file list</b> to be enabled in
{_link(PAGE_CONFIG, "Configuration")} and archives to have been crawled with that option on.</div>

<h3>Images tab</h3>
<p>Computes a 128-bit perceptual difference hash (dHash &mdash; horizontal and vertical
gradients) for every stored thumbnail and reports archive pairs that contain at least one
pair of visually similar images.  The comparison is resolution-independent, so the same
render at different sizes is caught.</p>
<table>
  <tr><th>Parameter</th><th>Description</th></tr>
  <tr><td>Max hash distance</td>
      <td>0&ndash;32 (default 10).  The maximum number of bits allowed to differ between
          two thumbnail hashes.  0 = bit-for-bit identical pixels after downscaling;
          &le;6 = very similar (different resolution or minor JPEG artefacts);
          &le;16 = noticeably similar; higher values increase recall but also false positives.</td></tr>
</table>
<div class="note">After upgrading, run <b>Database Maintenance &rarr; Recompute Image Hashes</b>
once so existing thumbnails are re-hashed with the current 128-bit algorithm; until then
older 64-bit hashes are recomputed on the fly during a search.</div>

<h4>Matching method &mdash; dHash vs AI embedding</h4>
<p>The <b>Method</b> dropdown chooses how images are compared:</p>
<ul>
  <li><b>Perceptual hash (dHash)</b> &mdash; the built-in, dependency-free method above;
      great for near-duplicates (re-encodes, resizes, minor edits).</li>
  <li><b>AI embedding (CLIP)</b> &mdash; an optional learned embedding that matches the
      <em>same model</em> across different renders, angles, and colours far more accurately.
      Pairs are scored by <em>cosine similarity</em> (0&ndash;1); set a <b>Min similarity</b>
      threshold (e.g. 0.90). This option is greyed out until the optional dependencies
      (<code>onnxruntime</code> + a CLIP ONNX model) are installed and embeddings have been
      computed via <b>Database Maintenance &rarr; Compute Image Embeddings</b>. See the README
      for setup. The same method selector is available in <b>Find by Image</b>.</li>
</ul>

<div class="tip">Tip: hover any row in the Images tab to see a side-by-side popup of both
archives&rsquo; first thumbnails, making it easy to judge whether the match is genuine.</div>

<h3>Sending results to the browser</h3>
<ul>
  <li><b>Show Pair in Main Table</b> &mdash; select one row, then click to filter the browser
      to the two archives in that pair.</li>
  <li><b>Show All in Main Table</b> &mdash; filter the browser to every archive appearing in
      any result row.</li>
</ul>

<h3>Right-click context menu</h3>
<p>Right-clicking any row in either results table shows the following actions:</p>
<table>
  <tr><th>Action</th><th>Description</th></tr>
  <tr><td><b>Compare Archive Contents&hellip;</b></td>
      <td>Opens a side-by-side comparison of the two archives&rsquo; STL file lists.
          Use the <b>Match by</b> dropdown to pair files by <em>Filename</em>,
          <em>Size</em>, or <em>Hash</em>.  Matched pairs are highlighted with a green
          <code>==</code> marker; unmatched files appear in the remaining rows.</td></tr>
  <tr><td><b>Open Folder of Archive A/B in Explorer</b></td>
      <td>Opens the parent folder of the respective archive in the file manager configured
          in {_link(PAGE_CONFIG, "Configuration &rarr; Browser")}.  Disabled when the
          archive path is empty.</td></tr>
  <tr><td><b>Delete Archive A/B from Disk&hellip;</b></td>
      <td>Visible only when <em>Allow file delete</em> is enabled in
          {_link(PAGE_CONFIG, "Configuration &rarr; File writing")}.
          Asks for confirmation, then physically removes the archive (or moves it to the
          trash folder) and deletes its database record &mdash; the same logic used by the
          main browser&rsquo;s delete action.  The row is removed from the results table
          on success.</td></tr>
</table>

<h2>Tools &mdash; Database Maintenance&hellip;</h2>
<p>Open via <b>Tools &rarr; Database Maintenance&hellip;</b></p>
<p>The maintenance dialog helps you keep the database consistent with the files on disk:</p>

{_screenshot("db_mant.jpg")}
<h3>Status colours</h3>
<table>
  <tr><th>Status</th><th>Meaning</th></tr>
  <tr><td><b>OK</b></td><td>Archive exists on disk and size matches.</td></tr>
  <tr><td><b style="color:#c62828">Missing</b></td>
      <td>Archive path does not exist on disk (newly detected this Verify run).</td></tr>
  <tr><td><b style="color:#8c3cb4">Flagged Missing</b></td>
      <td>Flagged as missing by the crawler in a previous crawl session and still absent.
          These records are kept in the database to allow future move-detection across
          different crawl runs.</td></tr>
  <tr><td><b style="color:#b46400">Modified</b></td>
      <td>File exists but its size on disk differs from the stored value — the archive may
          have been replaced or partially written.</td></tr>
  <tr><td><b style="color:#1e64b4">Moved (in DB)</b></td>
      <td>A matching archive was found elsewhere in the database (after running
          <b>Find Moved in DB…</b>); the match path is shown in the <em>Match Path</em>
          column.</td></tr>
  <tr><td><b style="color:#2e7d32">Relocated</b></td>
      <td>Archive was located in a different folder by <b>Search in Folder…</b> and the
          path has been updated.</td></tr>
</table>

<h3>Buttons</h3>
<table>
  <tr><th>Button</th><th>Action</th></tr>
  <tr><td><b>Verify</b></td>
      <td>Checks every archive path in the database against the filesystem.
          Also clears the <em>is_missing</em> flag for any record whose file has
          reappeared on disk.</td></tr>
  <tr><td><b>Search in Folder&hellip;</b></td>
      <td>Recursively searches a chosen folder for archives matching any <em>Missing</em>
          or <em>Flagged Missing</em> record by content hash (or filename + size as
          fallback). Updates the path on a match.</td></tr>
  <tr><td><b>Find Moved in DB&hellip;</b></td>
      <td>Scans all database records to detect <em>Missing</em> and <em>Flagged Missing</em>
          archives that appear to have been re-crawled at a different path. Matches by hash
          first, then by filename + size. Matches are highlighted as <em>Moved (in DB)</em>.</td></tr>
  <tr><td><b>Merge Moved</b></td>
      <td>For every <em>Moved (in DB)</em> row, updates the original record to the new path
          and removes the duplicate, preserving all metadata and thumbnails.</td></tr>
  <tr><td><b>Remove Selected</b></td>
      <td>Permanently deletes the selected rows from the database.</td></tr>
  <tr><td><b>Remove All Missing</b></td>
      <td>Permanently deletes every record with status <em>Missing</em> (newly detected
          absences). Does not affect <em>Flagged Missing</em> records.</td></tr>
  <tr><td><b>Remove Flagged Missing</b></td>
      <td>Permanently deletes every record with status <em>Flagged Missing</em> — archives
          that the crawler could not locate over multiple sessions. Use this to clean up
          records for files you know have been permanently removed.</td></tr>
  <tr><td><b>Check Images Warning</b></td>
      <td>Re-reads every catalogued archive&rsquo;s file list and corrects the
          <em>Archive without images</em> warning: it is cleared where the archive does
          contain images and added where it does not. Only the archive catalog is read
          (nothing is decompressed), so this runs far faster than a full rescan and is the
          quickest way to refresh the whole library after an update adds support for a new
          image format. Archives that are missing or cannot be opened are counted as
          <em>skipped</em> and left untouched. Other warnings on a record are never
          changed &mdash; use <b>Rescan Files</b> to recompute all of them. Adding the
          warning requires <em>Archive contains images</em> to be enabled in
          <b>Configuration &rarr; Crawler warnings</b>; a stale warning is cleared either
          way.</td></tr>
  <tr><td><b>Check Presupport Warning</b></td>
      <td>Re-reads every catalogued archive&rsquo;s file list and corrects the
          <em>Models with presupported stl</em> warning: it is cleared where the archive holds
          no presupported <code>.stl</code> and added where it does. Only the archive catalog
          is read, so this runs far faster than a full rescan &mdash; run it after the
          presupport rules change to clear warnings raised by an older, looser rule (an image
          or a folder entry whose name merely contained &ldquo;presupported&rdquo;, or a
          <code>No_Supported/</code> folder of raw meshes). Archives that are missing or cannot
          be opened are counted as <em>skipped</em> and left untouched, and no other warning on
          a record is changed. Adding the warning requires <em>Pre-supported STL</em> to be
          enabled in <b>Configuration &rarr; Crawler warnings</b>; a stale warning is cleared
          either way.</td></tr>
  <tr><td><b>Deduplicate Thumbnails</b></td>
      <td>Stores each distinct thumbnail once instead of once per model. Libraries that
          share renders across archives often carry a large proportion of byte-identical
          thumbnails &mdash; collapsing them can halve the store. Also removes thumbnails no
          model references any more, then compacts. Every thumbnail stays viewable; only
          redundant copies go. Safe to stop and re-run &mdash; progress is committed as it
          goes, and a partly converted store reads correctly. New thumbnails are stored this
          way from now on, so duplicates do not build up again.</td></tr>
  <tr><td><b>Compact Thumbnail Store</b></td>
      <td>Rewrites the LMDB thumbnail store to reclaim disk space left behind by deleted
          thumbnails. LMDB reuses freed space but never shrinks the file on its own, so run
          this after removing many models or images. The store is briefly closed and rebuilt.</td></tr>
  <tr><td><b>Prune Images per Model&hellip;</b></td>
      <td>Reduces the number of stored images per model to a maximum you choose (e.g. keep
          3 of 10). The oldest images are kept; the extras and their thumbnails are deleted
          from the database. Run <b>Compact Thumbnail Store</b> afterwards to reclaim the
          freed disk space.</td></tr>
  <tr><td><b>Show only problems</b> checkbox</td>
      <td>Hides <em>OK</em> and <em>Relocated</em> rows so you can focus on records that
          need attention.</td></tr>
</table>

<div class="note">Click the <b>&#9888;</b> in the browser&rsquo;s <b>Warnings</b> column to open
<b>Warning Details</b>, which lists the exact archive members that triggered each warning.
This is the quickest way to check a warning you doubt. The presupported rule only ever
matches <code>.stl</code> members &mdash; by their own name or by a pre-supported folder above
them &mdash; so an image, a readme, or a folder entry whose name contains
<code>presupported</code> does not raise it, and the file listed is always the mesh that
qualified rather than its folder.
Warnings that fire on an absence (no images, no printable files) say so instead of listing
files, and an archive that is missing or cannot be opened is reported as such.
Each listed file shows its size, with a total beside the heading &mdash; so a
<em>Nested archives</em> or <em>Models with presupported stl</em> warning tells you at a glance
how much of the archive it accounts for. Sizes come from the archive catalog, so they are the
<em>uncompressed</em> sizes of those members.</div>

<div class="warn">Removing records is permanent and cannot be undone. Run <b>Verify</b> first
and review the list carefully before using any Remove action.</div>

<h2>Tools &mdash; Backup / Restore Library&hellip;</h2>
<p>Open via <b>Tools &rarr; Backup / Restore Library&hellip;</b></p>
<p>Creates or restores a complete snapshot of the library data:</p>
<table>
  <tr><th>Button</th><th>Action</th></tr>
  <tr><td><b>Backup&hellip;</b></td>
      <td>Prompts for a destination file (default name includes a timestamp), then packs
          <code>stlLibrary.db</code>, <code>thumbnails.lmdb/</code>, and
          <code>settings.json</code> from the data directory into a single
          <code>.7z</code> archive. A progress bar and log show the file count.</td></tr>
  <tr><td><b>Restore&hellip;</b></td>
      <td>Prompts for a previously created <code>.7z</code> backup file, asks for
          confirmation, then extracts its contents into the current data directory,
          overwriting existing files. <b>The application must be restarted after a
          restore.</b></td></tr>
</table>
<div class="note">Both operations run in a background thread — the UI stays responsive
and the Close button is disabled until the operation completes.</div>

{_prev_next(PAGE_STATS_TOOLS)}"""


# ── Page registry ─────────────────────────────────────────────────────────────

def _page_sidecars() -> str:
    return _css() + _nav_bar(PAGE_SIDECARS) + f"""
<h1>Metadata Sidecars</h1>
<p>A <b>metadata sidecar</b> is a file named <code>METADATA.7z</code> that the crawler
writes into each archive folder after processing it.  It stores a snapshot of every
model&rsquo;s database record for that folder, and optionally its JPEG thumbnails.
The main purpose is a <b>fast restore</b>: when you move your collection to a new
machine you can rebuild the entire database by importing the sidecars rather than
re-crawling and re-extracting every archive from scratch.</p>

<h2>What Is Inside METADATA.7z</h2>
<table>
  <tr><th>Member</th><th>Contents</th></tr>
  <tr><td><code>metadata.json</code></td>
      <td>A JSON snapshot of all DB fields for every direct-child archive in the folder
          (title, author, tags, category, presupports, date, &hellip;) plus the per-archive
          STL file list when <em>Store STL file list</em> is enabled, a schema version
          number, and a write timestamp.</td></tr>
  <tr><td><code>thumbnails/</code></td>
      <td>JPEG thumbnails for the models, included only when
          <em>Include thumbnails in sidecar</em> is enabled in
          {_link(PAGE_CONFIG, "Configuration")}.</td></tr>
</table>

<h2>Enabling Sidecars</h2>
<p>Sidecars are <b>opt-in</b>.  Enable them in
<b>File &rarr; Configuration&hellip; &rarr; File writing &rarr; Sidecar</b>:</p>
<ul>
  <li><b>Enable metadata sidecars</b> &mdash; must be on for any sidecar activity to occur.</li>
  <li><b>Include thumbnails</b> &mdash; embeds JPEG thumbnails inside the sidecar.
      Makes the file larger but lets a restore work without any internet access or
      re-extraction of original archives.</li>
</ul>
<div class="note">When sidecars are disabled the crawler never writes or reads
<code>METADATA.7z</code> and the <b>Tools &rarr; Generate / Update Metadata Sidecars</b>
menu item is greyed out.</div>

<h2>How Sidecars Are Written</h2>
<p>There are two ways a sidecar gets created or updated:</p>
<ol>
  <li><b>Automatically after each crawl</b> &mdash; once the crawler finishes processing
      a folder it writes (or refreshes) the <code>METADATA.7z</code> for that folder.
      Only folders that had at least one archive added or updated are touched.</li>
  <li><b>Manually via Tools</b> &mdash; open
      <b>Tools &rarr; Generate / Update Metadata Sidecars&hellip;</b> to refresh the
      sidecar for <em>every</em> folder in the database in one operation.
      Use this after a bulk edit or before moving the collection.</li>
</ol>

<h2>Restore Workflow (New Machine)</h2>
<ol>
  <li>Copy or mount your archive collection on the new machine.</li>
  <li>Install the application and set the <em>Data Directory</em> in Configuration.</li>
  <li>Enable <em>Metadata sidecars</em> in Configuration.</li>
  <li>Open the Crawler, point it at the root folder, and start a crawl.
      The crawler detects the <code>METADATA.7z</code> sidecars and imports their
      contents into the database before extracting any archive &mdash; only archives
      whose CRC has changed since the snapshot was written are fully re-extracted.</li>
</ol>
<div class="tip">Tip: if you also enabled <em>Include thumbnails</em> before the move,
the restore will populate all thumbnails from the sidecar without touching the
original archive files at all.</div>

<h2>Sidecar vs. Full Crawl</h2>
<table>
  <tr><th></th><th>Full crawl (no sidecar)</th><th>Crawl with sidecar import</th></tr>
  <tr><td>Reads metadata from</td><td>Inside each archive</td><td>METADATA.7z first, then archive for changed files</td></tr>
  <tr><td>Reads thumbnails from</td><td>Decompresses each archive</td><td>METADATA.7z (if thumbnails included)</td></tr>
  <tr><td>Speed on unchanged collection</td><td>Fast (CRC skip)</td><td>Faster (no archive I/O at all)</td></tr>
  <tr><td>Requires original archives</td><td>Yes</td><td>Only for files changed since last snapshot</td></tr>
</table>
{_prev_next(PAGE_SIDECARS)}"""


_PAGES: dict[str, object] = {
    PAGE_OVERVIEW:    _page_overview,
    PAGE_BROWSER:     _page_browser,
    PAGE_FILTER:      _page_filter,
    PAGE_IMAGEGRID:   _page_imagegrid,
    PAGE_CRAWLER:     _page_crawler,
    PAGE_SIDECARS:    _page_sidecars,
    PAGE_TAGS:        _page_tags,
    PAGE_EDITOR:      _page_editor,
    PAGE_MASS_EDITOR: _page_mass_editor,
    PAGE_CONFIG:      _page_config,
    PAGE_STATS_TOOLS: _page_stats_tools,
}


# ── Dialog ────────────────────────────────────────────────────────────────────

class HelpDialog(QDialog):
    """
    Multi-page help reference.

    Left panel: QListWidget navigation.
    Right panel: QTextBrowser with styled HTML content.
    Internal links use the custom  help:<page_id>  scheme.
    """

    def __init__(self, parent=None, start_page: str = PAGE_OVERVIEW) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("stlLibrary — Help"))
        self.resize(960, 720)

        self._history: list[str] = []
        self._history_pos: int = -1

        self._build_ui()
        self._navigate_to(start_page)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left nav ----
        self._nav = QListWidget()
        self._nav.setMaximumWidth(155)
        self._nav.setMinimumWidth(120)
        for pid, label in _NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            self._nav.addItem(item)
        self._nav.currentRowChanged.connect(self._on_nav_row_changed)
        splitter.addWidget(self._nav)

        # ---- Right content area ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(2)

        btn_bar = QHBoxLayout()
        self._back_btn = QPushButton(self.tr("← Back"))
        self._back_btn.setFixedWidth(80)
        self._back_btn.setEnabled(False)
        self._back_btn.clicked.connect(self._go_back)
        self._fwd_btn = QPushButton(self.tr("Forward →"))
        self._fwd_btn.setFixedWidth(90)
        self._fwd_btn.setEnabled(False)
        self._fwd_btn.clicked.connect(self._go_forward)
        btn_bar.addWidget(self._back_btn)
        btn_bar.addWidget(self._fwd_btn)
        btn_bar.addStretch()
        right_layout.addLayout(btn_bar)

        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        self._browser.setStyleSheet(
            "QTextBrowser { selection-background-color: palette(highlight);"
            "                selection-color: palette(highlighted-text); }"
        )
        self._browser.anchorClicked.connect(self._on_anchor_clicked)
        right_layout.addWidget(self._browser)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ------------------------------------------------------------------
    def _navigate_to(self, page_id: str, add_to_history: bool = True) -> None:
        if page_id not in _PAGES:
            return

        if add_to_history:
            self._history = self._history[: self._history_pos + 1]
            self._history.append(page_id)
            self._history_pos = len(self._history) - 1

        self._browser.setHtml(_PAGES[page_id]())
        self._browser.verticalScrollBar().setValue(0)

        idx = _PAGE_IDS.index(page_id) if page_id in _PAGE_IDS else -1
        if idx >= 0:
            self._nav.blockSignals(True)
            self._nav.setCurrentRow(idx)
            self._nav.blockSignals(False)

        self._back_btn.setEnabled(self._history_pos > 0)
        self._fwd_btn.setEnabled(self._history_pos < len(self._history) - 1)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        raw = url.toString()
        if raw.startswith("help:"):
            self._navigate_to(raw[5:])

    def _on_nav_row_changed(self, row: int) -> None:
        if 0 <= row < len(_PAGE_IDS):
            self._navigate_to(_PAGE_IDS[row])

    def _go_back(self) -> None:
        if self._history_pos > 0:
            self._history_pos -= 1
            self._navigate_to(self._history[self._history_pos], add_to_history=False)

    def _go_forward(self) -> None:
        if self._history_pos < len(self._history) - 1:
            self._history_pos += 1
            self._navigate_to(self._history[self._history_pos], add_to_history=False)
