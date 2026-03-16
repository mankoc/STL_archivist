# STL Database — Application Plan

## Overview

A cross-platform desktop application that builds and maintains a browsable database of 3D-print model archives stored on a local hard drive. The user points the application at a root folder (e.g. `/mnt/f/STL`), the crawler indexes every archive it finds, and the browser lets them search, filter, and edit the collected metadata.

---

## Definitions

A **model** is a collection of STL files plus supported 3D-model files (`.lys`, `.chitu`, etc.) that together form a single printable figure. Models are distributed as archive files (primarily `.7z`, occasionally `.zip` or `.rar`) organised in a folder hierarchy that typically encodes the distributor type, author, release date, and model name.

Inside each archive there may be:
- STL / slicer files (`.stl`, `.lys`, `.chitu`)
- Image files used as previews
- An `.url` file pointing to the original source page
- An `info.json` file with structured metadata, for example:

```json
{
    "name": "Cyborg Hulk",
    "author": "Caleb Nefzen",
    "distributor": "Artstation",
    "url": "https://calebnefzen.artstation.com/...",
    "description": "...",
    "tags": ["hulk", "cyborg", "statue", "collectible", "marvel hulk"]
}
```

---

## Technologies

| Concern | Choice |
|---|---|
| GUI | PyQt6 with a custom QSS stylesheet |
| Database | SQLite via SQLAlchemy ORM (WAL mode, foreign keys on) |
| Archive reading | `py7zr`, `rarfile`, `zipfile` |
| Thumbnailing | Pillow |
| Settings | `QSettings` (native per-platform storage) |
| Deployment | Pure Python; runs on Linux and Windows |

The SQLAlchemy layer is abstracted behind `db.py` so the backend can be swapped for PostgreSQL or another engine in the future without touching application code.

---

## Database Schema

All data is stored in a single SQLite file whose path is configurable via the application settings.

### `models` table
| Field | Type | Notes |
|---|---|---|
| `id` | Integer PK | auto-increment |
| `title` | String | name of the model |
| `author` | String | creator / sculptor |
| `type` | String | distributor: patreon / kickstarter / myminifactory / unknown |
| `release_date` | String | ISO date string inferred from path or metadata |
| `url` | Text | link to original source |
| `description` | Text | freeform description |
| `archive_path` | Text (unique) | absolute path to the archive file |
| `archive_hash` | String | SHA-256 of the archive, used to detect changes |
| `created_at` / `updated_at` | DateTime | audit timestamps |

### `tags` table
A shared tag pool — tag names are unique and lowercased. Models reference tags via a `model_tags` join table (many-to-many, cascade delete).

### `images` table
Up to five JPEG thumbnails extracted per model, stored as `LargeBinary` blobs and linked to their model with cascade delete.

---

## Application Structure

```
main.py                     — entry point; reads db path from QSettings, inits DB, launches window
app/
  database/
    models.py               — SQLAlchemy ORM models (STLModel, Tag, Image)
    db.py                   — engine factory, session helpers, init_db(), clear_db()
  crawler/
    crawler.py              — QThread worker: crawls a directory, upserts records
    archive.py              — archive inspection helpers (list contents, iter images)
    metadata.py             — metadata extraction from paths and info.json
  utils/
    thumbnailer.py          — Pillow thumbnail generation and QPixmap helpers
  ui/
    main_window.py          — QMainWindow: menu bar, toolbar, tab widget
    browser_tab.py          — Tab 1: filter tree + model table + thumbnail sidebar
    editor_dialog.py        — Single-model editor dialog
    mass_editor_dialog.py   — Multi-model editor dialog
    crawler_dialog.py       — Crawler launcher dialog
    config_dialog.py        — Application configuration dialog
resources/
  style.qss                 — application stylesheet
```

---

## Features

### 1. Crawler

Accessed via **File → Open Crawler…** or the toolbar. Runs in a background `QThread` so the UI stays responsive.

**On each run:**
1. **Cleanup** — queries all `archive_path` values in the database and deletes any record whose file no longer exists on disk (images cascade-deleted automatically).
2. **Discovery** — recursively scans the source directory for archive files.
3. **Hashing** — computes a SHA-256 hash of each archive. Records with a matching hash are skipped.
4. **Metadata extraction** — reads `info.json` if present; infers title, author, type, date, and tags from the archive path and filename when structured fields are absent.
5. **Thumbnailing** — extracts up to five image files from the archive and stores resized JPEG thumbnails in the database.
6. **Upsert** — inserts new records or updates changed ones; progress and a per-file log are shown in the UI.

### 2. Model Browser

The main view. Split into three resizable panels:

**Left — Filter tree (resizable)**
Organised into four collapsible sections:
- **Authors** — distinct author values from the database
- **Tags** — all tags in the tag pool
- **Type** — distinct distributor type values
- **Path** — directory hierarchy of all archive paths; clicking a folder filters to all models underneath it

An **Add Tag…** button at the bottom of the panel creates a new tag directly.

Clicking any leaf node in the tree filters the centre table. Clicking a section header clears the filter.

**Centre — Model table**
Columns: thumbnail, title, author, type, date, tags, path. Sortable by any column. A search bar above the table performs a live case-insensitive full-text filter across all columns simultaneously. Supports multi-row selection (Ctrl+click, Shift+click).

Right-click context menu:
- **Edit Model…** — opens the single-model editor for the clicked row.
- **Mass Edit N model(s)…** — opens the mass editor for all selected rows (enabled only when ≥ 2 rows are selected).

**Right — Thumbnail sidebar**
Displays the stored preview images for the model selected by single-click.

### 3. Model Editor

A dialog opened from the browser (double-click or right-click → Edit Model…). Shows all editable fields for a single model:

- Archive path (read-only)
- Title, Author, Type, Date, URL, Description, Tags
- All text fields have autocomplete from existing values in the database. The **Type** field is an editable dropdown showing all known types with contains-matching autocomplete.
- A **Re-infer from archive…** button re-runs metadata extraction from the archive file and pre-fills the form.
- A thumbnail sidebar shows all stored images for the model.

### 4. Mass Editor

A dialog opened from the browser via right-click on a multi-row selection. Allows editing metadata for many models at once using two complementary mechanisms that can be combined:

**Path pattern parsing**
- The user enters a pattern such as `/mnt/f/{type}/{author}/*/{name}` that describes the structure of the archive paths.
- `{token}` placeholders capture the corresponding path segment into a named field; `*` matches a segment without capturing it.
- Supported tokens: `{type}`, `{author}`, `{title}` / `{name}`, `{date}` / `{month}`, `{url}`.
- The archive path of the first selected model is shown as a live example. As the pattern is typed, the extracted values for that example path are shown in real time.
- Named patterns can be saved, loaded from a dropdown, and deleted. The last-used pattern is restored automatically on the next open.
- **Parse all paths** applies the pattern to all selected models and reports how many matched.

**Manual overrides**
- Each field (Type, Author, Date, Title, Tags) has a checkbox. Only ticked fields are written.
- A ticked field applies the same value to every selected model.
- Tags replace the existing tag list for each affected model.

On save, path-parsed values are applied per-model first; manual overrides take precedence.

### 5. Configuration

Accessed via **File → Configuration…**. Settings are stored in `QSettings` (platform-native).

| Setting | Description |
|---|---|
| Database file path | Absolute or relative path to the SQLite `.db` file. Defaults to `stl_database.db` in the working directory. A restart is required for path changes to take effect. |

---

## Project Files

- `requirements.txt` — pinned Python dependencies
- `.gitignore` — excludes `__pycache__`, `*.pyc`, `*.db`, `*.db-shm`, `*.db-wal`, `.venv/`, `dist/`, `build/`, etc.
- `tests/` — unit tests for metadata extraction and database helpers
