# STL Database

A desktop application for cataloguing and browsing your local collection of 3D printable STL model archives. Built with PyQt6 and SQLAlchemy.

## Features

- **Crawler** — scans a folder of archives (`.zip`, `.7z`, `.rar`) and automatically imports model metadata into a local SQLite database; runs with parallel workers for fast processing
- **Path pattern parsing** — extract sculptor, release name, model title, and more directly from your folder/file naming conventions
- **Thumbnail generation** — extracts preview images from archives and stores them locally; configurable count and resolution
- **Scale detection** — automatically detects miniature scale (e.g. `28mm`, `32mm`, `1/35`) from archive and file names
- **Presupport detection** — identifies whether an archive contains Lychee, Chitubox, or presupported STL files
- **Crawler warnings** — flags archives that contain presupported STLs, no images, or nested archives
- **Tagging** — add and manage tags per model for easy filtering
- **Browser** — search, filter, and browse your collection with inline editing, tri-state filters (include / exclude / no filter), and a clear-filters button
- **Filter panel** — each filter group header shows an icon and acts as a "has value / no value" toggle; individual values cycle through include (green **+**), exclude (red **−**), and off states
- **Image grid** — display a scrollable grid of thumbnails for the currently filtered set of models; resize thumbnails live with a slider (60–300 px)
- **Editor** — edit model metadata (title, author, sculptor, category, subcategory, release, scale, presupports, source, URL, date, sequence, tags)
- **Mass editor** — apply bulk changes across multiple models at once, including category and subcategory
- **Metadata sidecars** — export a `METADATA.7z` snapshot into every archive folder so the library can be restored on a new machine without re-crawling; optionally includes thumbnails
- **Image pane toggle** — show or hide the right-hand thumbnail sidebar via a toolbar button or the View menu
- **File sorting** — optionally move and rename archive files into a structured directory layout using a path pattern (enable in Configuration)
- **File Size column** — browser table shows each archive's size on disk (B / KB / MB / GB), sortable numerically
- **STL file list** — optionally records the name and uncompressed size of every `.stl` file inside each archive in a dedicated `stl_files` table (enable *Store STL file list* in Configuration); populated automatically during crawling and included in METADATA.7z sidecars
- **Find Metadata Duplicates** — Tools menu dialog that finds models with identical or phonetically similar field values; supports exact match, Levenshtein (configurable distance), and Soundex; results can be sent directly to the browser table
- **Find Archives by Content** — two-tab tool under the Tools menu: the *STL Files* tab flags archive pairs that share a configurable fraction of identical files (by name + size); the *Images* tab finds archives with perceptually similar thumbnails using a 64-bit dHash and a configurable Hamming distance threshold; hover any image-match row to see both thumbnails in a popup; right-click any result row to open a side-by-side **Archive Contents** comparison
- **Archive content viewer** — right-click a single model and choose *Show Files…* to list every `.stl` file recorded in the `stl_files` table (filename, size, hash); or choose *Compare Archive Contents…* from the Find Archives by Content dialog to compare two archives side-by-side with configurable matching by filename, size, or hash
- **Physical file deletion** — when *Allow file delete* is enabled in Configuration → File writing, a new right-click item *Delete archive file(s) from disk…* appears; optionally moves files to a configurable trash folder (with recovery info) instead of permanently deleting them
- **Background crawl** — start a crawl then click *Run in Background* to dismiss the Crawler dialog without stopping the worker; a status indicator in the main-window status bar shows live progress and brings the dialog back when clicked
- **Statistics** — column statistics and aggregated statistics dialogs for a quick numeric overview of any field; database maintenance dialog (vacuum, integrity check)
- **Themes** — built-in color schemes: System Default, VS Code Dark+, Windows 11 Dark, Windows 11 Light, and nine Material Design themes (dark/light variants of Blue, Teal, Purple, Amber, Red, Orange) via qt-material

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt`:
  - PyQt6
  - SQLAlchemy
  - Pillow
  - py7zr
  - rarfile
  - xxhash
  - lmdb
  - qt-material

## Installation

### Pre-built Windows executables

Grab the latest release from the [Releases](../../releases) page:

| Executable | Description |
|---|---|
| `STLDatabase-<version>.exe` | Full GUI application |
| `crawl-<version>.exe` | Headless command-line crawler |

No Python installation required — both are self-contained single-file executables.

### From source

```bash
# 1. Clone the repository
git clone <repo-url>
cd stl_database

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the application
python main.py
```

> **Note for RAR support:** `rarfile` requires the `unrar` binary to be installed on your system.
> - Linux: `sudo apt install unrar`
> - Windows: install [WinRAR](https://www.rarlab.com/) or the standalone `UnRAR` tool and make sure it is on your `PATH`.
> You can also set a custom path to the executable in **Configuration → Crawler → RAR tool**.

## Usage

### 1. Configure the application

On first launch, go to **File → Configuration…** and set up:

- **Database** — choose where the SQLite database and thumbnails are stored (restart required after changing)
- **Crawler** — max thumbnails per model, max image size in pixels, path to `unrar` executable, and which crawler warnings to enable
- **File writing** — enable *Sort files into directory* (adds a right-click menu entry); optionally enable *Allow file delete* (with a confirmation guard) to physically delete or trash archive files from the right-click menu; configure a *Trash folder* to move deleted files there instead of permanently removing them
- **File writing → Sidecar** — enable *Metadata sidecar* to write `METADATA.7z` per folder during crawl; optionally include thumbnails
- **Appearance** — select a color scheme (preview is applied immediately; Cancel reverts)

### 2. Crawl your archive folder

#### GUI

1. Click **Open Crawler** (toolbar icon or `Ctrl+O`).
2. Select the root folder that contains your STL archives.
3. Optionally set a **path pattern** to auto-populate metadata fields from your folder/file naming convention (see [Path Patterns](#path-patterns) below).
4. Click **Start**. The crawler will walk the directory recursively, hash each archive, detect scale and presupport information, extract preview images, and upsert records into the database.

#### Command line

`crawl.py` runs the same crawler without opening the GUI — useful for scripting or scheduled imports.

```bash
python crawl.py /path/to/stl/folder
```

By default it uses the same database configured in the GUI. Override any setting with options:

| Option | Default | Description |
|---|---|---|
| `--db PATH` | GUI-configured path | SQLite database file to write to |
| `--pattern PATTERN` | *(none)* | Path pattern for metadata extraction (see [Path Patterns](#path-patterns)) |
| `--workers N` | CPUs / 2, clamped to 2–8 | Parallel worker threads |
| `--max-thumbnails N` | 5 | Max preview images stored per archive |
| `--max-thumb-size PX` | 512 | Max thumbnail dimension in pixels |
| `--unrar PATH` | GUI-configured path | Path to `unrar` executable for RAR archives |

**Examples:**

```bash
# Use a custom database and extract metadata from the path structure
python crawl.py ~/stl_collection \
  --db ~/stl_collection/library.db \
  --pattern "{sculptor}/{release}/{title}"

# Faster crawl with more workers, no thumbnails
python crawl.py ~/stl_collection --workers 8 --max-thumbnails 0
```

Progress and log output are written to stdout.

### 3. Browse and edit

- Use the search bar and filter tree (Authors / Tags / Type / Date / Sculptor / Category / Subcategory / Release / Scale / Presupports / Warnings / Path) to narrow down the collection.
- **Filter indicators** use colored circles:
  - Gray circle — no filter active for this value
  - Green **+** — include rows matching this value
  - Red **−** — exclude rows matching this value
- **Click a leaf value** to cycle through include → exclude → off.
- **Click the checkbox icon on a group header** (e.g. Authors, Tags) to filter by whether models have *any* value for that field:
  - Green **+** — show only models that have a value for this field
  - Red **−** — show only models that have *no* value for this field
- Use the **Clear** button to reset all active filters.
- Double-click a row (or press `E`) to open the **Editor** and update model details.
- Click **Image Grid** in the toolbar to view thumbnails for all currently displayed models. Use the **Size** slider at the top of the grid to resize thumbnails from 60 to 300 px.
- Click the **image pane** toolbar button (or use the View menu) to toggle the thumbnail sidebar.
- Right-click for context-menu options: *Show Files…* (single selection, lists archive STL file contents), edit, mass-edit, *Delete from DB*, *Delete archive file(s) from disk…* (shown only when enabled in Configuration), generate thumbnails, create/update sidecar, and (if enabled) sort files into a directory.
- Start a crawl and click **Run in Background** to dismiss the Crawler dialog without stopping the worker; a status badge in the status bar shows progress and brings the dialog back when clicked.
- Use **Mass Edit** to apply changes to multiple selected rows at once.

### 4. Tags

- **Add Tag** — creates a new global tag (toolbar button).
- **Edit Tags** — opens the tag manager to rename or delete existing tags.
- Tags can be assigned per model through the editor or the inline tag autocomplete in the browser table.

### 5. Categories, Subcategories, and Sculptors

Managed through dedicated editor dialogs accessible from the browser's right-click menu or the editor. Each category can have multiple subcategories (e.g. Category: *Comics*, Subcategory: *Marvel*). Deleting a category cascades to its subcategories.

### 6. Scales

Scale values (e.g. `28mm`, `32mm`, `1/35`) are detected automatically during crawl and can also be set manually per model through the editor or the **Scale** inline cell in the browser.

## Path Patterns

The crawler can parse structured folder/file paths to extract metadata automatically. Define a pattern using placeholders that match segments of the path relative to the crawl root.

| Placeholder | Maps to |
|---|---|
| `{sculptor}` | Sculptor name |
| `{release}` | Release name |
| `{title}` | Model title |
| `{source}` | Source/distributor (e.g. Patreon, MyMiniFactory) |
| `{subtype}` | Model subtype (e.g. Marvel, DC) |
| `{sequence}` | Position in a series |
| `*` | Ignored segment |

**Example** — if your archives are organised as:

```
/ArtistName/2024-01 Some Release/model_name.zip
```

use the pattern:

```
{sculptor}/{release}/{title}
```

## Project Structure

```
stl_database/
├── main.py                  # GUI entry point
├── crawl.py                 # Command-line crawler (headless)
├── requirements.txt
├── resources/
│   ├── style.qss            # Application stylesheet
│   ├── icons/               # SVG icons (light/dark variants)
│   └── themes/              # QSS color schemes (VS Code, Win11); Material themes loaded via qt-material
├── app/
│   ├── config.py            # CrawlerConfig dataclass
│   ├── crawler/
│   │   ├── crawler.py       # QThread-based directory crawler (parallel workers)
│   │   ├── archive.py       # Archive extraction helpers (.zip/.7z/.rar)
│   │   ├── metadata.py      # Metadata extraction logic (info.json, .url, path)
│   │   ├── rules.py         # Scale / presupport / warning detection rules
│   │   └── folder_metadata.py  # METADATA.7z sidecar read/write
│   ├── database/
│   │   ├── db.py            # SQLAlchemy engine / session helpers
│   │   └── models.py        # ORM models (STLModel, Tag, Image, Sculptor, Category, Subcategory, Release, Scale)
│   ├── ui/
│   │   ├── main_window.py   # Main application window
│   │   ├── browser_tab.py   # Collection browser with inline editing and multi-value filters
│   │   ├── crawler_dialog.py
│   │   ├── crawler_tab.py
│   │   ├── editor_dialog.py
│   │   ├── mass_editor_dialog.py
│   │   ├── image_grid_dialog.py
│   │   ├── metadata_sidecar_dialog.py  # Sidecar generation dialog and worker
│   │   ├── scale_editor_dialog.py
│   │   ├── category_editor_dialog.py
│   │   ├── subcategory_editor_dialog.py
│   │   ├── tag_editor_dialog.py
│   │   ├── sort_dialog.py
│   │   ├── config_dialog.py
│   │   ├── find_duplicates_dialog.py
│   │   ├── find_content_duplicates_dialog.py
│   │   ├── file_list_dialog.py
│   │   ├── about_dialog.py
│   │   └── help_dialog.py
│   └── utils/
│       ├── thumbnailer.py   # LMDB thumbnail store (save/load/delete)
│       ├── path_pattern.py  # Path pattern parser and renderer
│       ├── string_utils.py  # Levenshtein distance, Soundex
│       └── logger.py        # Rotating gzip-compressed file logger
└── tests/
```

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-nc-sa/4.0/) (CC BY-NC-SA 4.0).

You are free to share and adapt this work for non-commercial purposes, provided you give appropriate credit and distribute any derivative works under the same license.
