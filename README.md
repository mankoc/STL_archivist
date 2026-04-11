# STL Database

A desktop application for cataloguing and browsing your local collection of 3D printable STL model archives. Built with PyQt6 and SQLAlchemy.

## Features

- **Crawler** — scans a folder of archives (`.zip`, `.7z`, `.rar`) and automatically imports model metadata into a local SQLite database
- **Path pattern parsing** — extract sculptor, release name, model title, and more directly from your folder/file naming conventions
- **Thumbnail generation** — extracts preview images from archives and stores them locally
- **Tagging** — add and manage tags per model for easy filtering
- **Browser** — search, filter, and browse your collection with inline editing support
- **Editor** — edit model metadata (title, author, sculptor, type, subtype, release, tags, source, URL, date)
- **Mass editor** — apply bulk changes across multiple models at once

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt`:
  - PyQt6
  - SQLAlchemy
  - Pillow
  - py7zr
  - rarfile
  - qt-material
  - xxhash

## Installation

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

## Usage

### 1. Configure the database path

On first launch, go to **Settings → Configure Database Path** and choose where the SQLite database file should be stored.

### 2. Crawl your archive folder

1. Click **Open Crawler** (toolbar icon or `Ctrl+O`).
2. Select the root folder that contains your STL archives.
3. Optionally set a **path pattern** to auto-populate metadata fields from your folder/file naming convention (see [Path Patterns](#path-patterns) below).
4. Click **Start**. The crawler will walk the directory recursively, hash each archive, extract preview images, and upsert records into the database.

### 3. Browse and edit

- Use the search bar and filter dropdowns to narrow down the collection.
- Double-click a row (or press `Enter`) to open the **Editor** and update model details.
- Right-click for context-menu options including opening the archive folder, previewing a random file, and deleting a record.
- Use **Mass Edit** to apply changes to multiple selected rows at once.

### 4. Tags

- **Add Tag** — creates a new global tag (toolbar button).
- **Edit Tags** — opens the tag manager to rename or delete existing tags.
- Tags can be assigned per model through the editor or the inline tag autocomplete in the browser table.

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
├── main.py                  # Entry point
├── requirements.txt
├── resources/
│   └── style.qss            # Application stylesheet
├── app/
│   ├── crawler/
│   │   ├── crawler.py       # QThread-based directory crawler
│   │   ├── archive.py       # Archive extraction helpers
│   │   └── metadata.py      # Metadata extraction logic
│   ├── database/
│   │   ├── db.py            # SQLAlchemy engine / session helpers
│   │   └── models.py        # ORM models (STLModel, Tag, Image, …)
│   ├── ui/
│   │   ├── main_window.py   # Main application window
│   │   ├── browser_tab.py   # Collection browser
│   │   ├── crawler_dialog.py
│   │   ├── editor_dialog.py
│   │   ├── mass_editor_dialog.py
│   │   ├── tag_editor_tab.py
│   │   ├── tag_editor_dialog.py
│   │   ├── config_dialog.py
│   │   └── help_dialog.py
│   └── utils/
│       ├── thumbnailer.py   # Thumbnail save/delete helpers
│       └── path_pattern.py  # Path pattern parser
└── tests/
```

## License

Personal / private use. No license currently specified.
