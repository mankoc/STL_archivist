# STL Database — Processed Implementation Plan

## Overview

A desktop PyQt application for browsing a local database of 3D printable STL model archives. The app crawls a folder of archives (7z/zip/rar), extracts metadata and thumbnails, stores them in SQLite, and provides a searchable, editable browser UI.

---

## Technology Stack

| Concern | Choice | Notes |
|---|---|---|
| GUI | PyQt6 | Modern skin via QSS or qt-material |
| Database | SQLite via SQLAlchemy ORM | ORM layer enables future DB migration |
| Archive handling | `py7zr`, `rarfile`, `zipfile` | Standard + 3rd party libs |
| Image thumbnails | Pillow | Resize images extracted from archives |
| Hashing | `hashlib` (xxHash or SHA256) | Detect updated archives |
| Packaging | `pyinstaller` | Single-binary deploy for Linux & Windows |

---

## Project Structure

```
stl_database/
├── main.py                  # App entry point
├── requirements.txt
├── .gitignore
├── app/
│   ├── __init__.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py        # SQLAlchemy ORM models
│   │   └── db.py            # Session factory, migration helpers
│   ├── crawler/
│   │   ├── __init__.py
│   │   ├── crawler.py       # Main crawl logic (threaded)
│   │   ├── archive.py       # Archive open/read helpers
│   │   └── metadata.py      # Infer metadata from paths/info.json
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── main_window.py   # QMainWindow with tab widget
│   │   ├── crawler_tab.py   # Tab 1: Crawler controls + log
│   │   ├── browser_tab.py   # Tab 2: Model browser/search
│   │   ├── editor_dialog.py # Tab/Dialog 3: Single model editor
│   │   └── tag_editor_tab.py# Tab 4: Tag mass editor
│   └── utils/
│       ├── __init__.py
│       └── thumbnailer.py   # Extract & resize thumbnail images
├── resources/
│   └── style.qss            # Qt stylesheet (dark modern theme)
└── tests/
    └── ...
```

---

## Database Schema

```sql
-- models table
CREATE TABLE models (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT,
    author      TEXT,
    type        TEXT,          -- patreon / kickstarter / myminifactory / unknown
    release_date TEXT,         -- ISO date inferred from path/filename
    url         TEXT,
    description TEXT,
    archive_path TEXT UNIQUE NOT NULL,
    archive_hash TEXT NOT NULL, -- SHA256 of archive for change detection
    created_at  TEXT,
    updated_at  TEXT
);

-- tags table (shared tag list)
CREATE TABLE tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

-- model_tags join table
CREATE TABLE model_tags (
    model_id INTEGER REFERENCES models(id) ON DELETE CASCADE,
    tag_id   INTEGER REFERENCES tags(id)   ON DELETE CASCADE,
    PRIMARY KEY (model_id, tag_id)
);

-- images table (thumbnails stored as file paths or BLOBs)
CREATE TABLE images (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id  INTEGER REFERENCES models(id) ON DELETE CASCADE,
    filename  TEXT,
    thumbnail BLOB    -- small resized JPEG stored in DB
);
```

SQLAlchemy ORM classes map 1:1 to these tables. Abstract base session factory allows swapping SQLite for PostgreSQL/MySQL later with a config change.

---

## Implementation Phases

### Phase 1 — Project Scaffold
- [ ] Initialize repo, `requirements.txt`, `.gitignore`
- [ ] Create folder structure above
- [ ] Set up SQLAlchemy with SQLite, run `create_all()`
- [ ] Stub `main.py` that opens a bare QMainWindow

### Phase 2 — Crawler Engine
- [ ] `archive.py`: open 7z/zip/rar, list contents, stream individual files without full extraction
- [ ] `metadata.py`:
  - Parse `info.json` if present
  - Infer `author`, `title`, `date`, `type` from folder path segments using regex patterns (e.g. `[Patreon]`, `2024-01`, author name as parent folder)
- [ ] `thumbnailer.py`: extract first image file from archive, resize to 256×256 with Pillow, return JPEG bytes
- [ ] `crawler.py`:
  - Walk source directory recursively
  - For each archive: compute SHA256 hash
  - Compare hash against DB; skip if unchanged
  - Collect metadata, thumbnail, write to DB via SQLAlchemy session
  - Run in `QThread` to keep UI responsive
  - Emit signals: `progress(int, int)`, `log(str)`, `finished()`

### Phase 3 — Crawler Tab UI
- [ ] Folder picker (source path input + browse button)
- [ ] Start/Stop buttons
- [ ] Progress bar bound to crawler signals
- [ ] Scrollable log output (QPlainTextEdit)

### Phase 4 — Browser Tab
Modeled after Calibre's book browser:
- [ ] Left panel: filter tree by Author / Tags / Type
- [ ] Center: `QTableView` backed by a `QSqlTableModel` or custom model
  - Columns: Thumbnail, Title, Author, Type, Date, Tags
  - Sortable columns, column resizing
- [ ] Top search bar: text search across title, author, tags
- [ ] Right-click context menu → "Edit Model" → opens `EditorDialog`
- [ ] Inline editing of fields directly in the table

### Phase 5 — Model Editor Dialog
- [ ] Form layout with all editable fields: Title, Author, Type, Date, URL, Description, Tags
- [ ] Tag input with autocomplete from existing tags table
- [ ] Thumbnail gallery: scrollable image strip with ability to add/remove images
- [ ] "Re-infer from path" button re-runs metadata extraction for that archive
- [ ] Save / Cancel buttons

### Phase 6 — Tag Editor Tab
- [ ] Table of all tags with usage count
- [ ] Merge: select multiple tags → merge into one (updates `model_tags`)
- [ ] Rename tag in-place
- [ ] Delete unused tags
- [ ] Batch add tag to a set of selected models

### Phase 7 — Polish & Packaging
- [ ] Apply `qt-material` dark theme or custom `style.qss`
- [ ] Keyboard shortcuts (Ctrl+F for search, Delete for remove, etc.)
- [ ] Settings dialog: default source folder, thumbnail cache size, DB path
- [ ] `pyinstaller` spec file for single-binary builds (Linux + Windows)
- [ ] Basic error handling: corrupt archives, missing files, DB lock

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| SQLAlchemy ORM (not raw SQLite) | Enables future DB migration; cleaner code |
| Stream archive contents, don't fully extract | Memory efficiency for large archives |
| Store thumbnails as BLOBs in DB | Keeps everything in one portable file; switch to filesystem cache if DB grows too large |
| `QThread` for crawler | Prevents UI freeze during long crawls |
| SHA256 hash for change detection | Detects any byte-level change in the archive, not just mtime |
| Tags normalized in separate table | Enables the tag editor and prevents tag name duplication |

---

## `requirements.txt` (initial)

```
PyQt6>=6.6
SQLAlchemy>=2.0
py7zr>=0.21
rarfile>=4.1
Pillow>=10.0
qt-material>=2.14
xxhash>=3.4          # optional, faster than sha256
```

---

## `.gitignore` additions

```
__pycache__/
*.pyc
*.pyo
*.db
*.sqlite
thumbnails/
dist/
build/
*.spec
.venv/
```

---

## Open Questions / Future Work

- Should thumbnails eventually be stored on disk (filesystem cache) instead of DB BLOBs to keep DB size manageable?
- Support for reading nested archives (archive inside archive)?
- Multi-language support?
- Cloud sync of the SQLite DB (e.g. to a NAS)?
- Plugin system for custom metadata extractors?
