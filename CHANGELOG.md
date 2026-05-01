# Changelog

## v0.2.0 — 2026-05-01

### Storage

- Added **`stl_files` table** — stores the name and uncompressed size of every `.stl` file
  inside each archive, keyed by `(model_id, filename)`; populated during crawling when
  *Store STL file list* is enabled in Configuration → Metadata Sidecar
- STL file list is written to and restored from `METADATA.7z` sidecar files

### Configuration

- Added **Store STL file list** checkbox in the Metadata Sidecar settings section

### UI — Tools

- Added **Find Archives by Content** dialog (Tools menu, non-modal)

  **STL Files tab**
  - Inverted-index overlap search: finds archive pairs sharing a configurable fraction of
    identical `.stl` files (matched by filename and uncompressed size)
  - Configurable *Similarity threshold* (1–100 %, default 80 %) relative to the larger archive
  - Results table: Title A/B, file counts, matching file count, similarity %; sortable
  - *Show Pair / Show All in Main Table* buttons filter the browser to the selected archives

  **Images tab**
  - Perceptual duplicate detection via 64-bit difference hash (dHash); pure-Pillow, no
    extra dependencies
  - Pigeonhole-principle segment index for O(N) candidate selection (no false negatives)
  - Configurable *Max hash distance* (0–15, default 5); 0 = pixel-identical after resize,
    ≤5 catches same render at different resolutions
  - Results sorted by best Hamming distance, then most matching pairs
  - Hover any result row to see a side-by-side thumbnail popup for both archives
  - Background workers with progress bar and Stop button for both tabs

### Documentation

- Updated README: added STL file list and Find Archives by Content to the feature list
  and project structure
- Updated Help dialog: Configuration page documents the new checkbox; Statistics & Tools
  page documents both tabs of Find Archives by Content; Sidecars page notes that the STL
  file list is included in sidecar snapshots

---

## v0.1.1 — 2026-04-29

### UI — Browser

- Added **File Size** column to the browser table showing each archive's size on disk
  (formatted as B / KB / MB / GB); sorts numerically

### UI — Tools

- Added **Find Metadata Duplicates** dialog (Tools menu) for locating models with identical
  or phonetically similar field values across any combination of columns
  - Column selection: Title, Author, Source, Sculptor, Category, Subcategory, Release, Scale
  - Three match methods: **Exact match**, **Levenshtein** (configurable max edit distance),
    and **Soundex** (phonetic)
  - Results table with Group, Column, ID, Title, Value, and Match columns; sortable
  - **Show Group in Main Table** — filters the browser to the selected duplicate group
  - **Show All Groups in Main Table** — filters the browser to every model in any duplicate group
  - Non-modal dialog; browser table updates live while the dialog stays open
  - Background worker with Stop button to cancel long-running similarity searches

---

## v0.1-alpha — 2026-04-26

Initial alpha release of STL Database, a desktop application for cataloguing local collections of
3D-printable STL model archives.

### Crawler

- Four-phase parallel pipeline: hash → DB diff → extract → upsert
- Supports `.zip`, `.7z`, and `.rar` archives
- CRC-based fingerprinting (xxHash-64) without full decompression
- Parallel worker pool with configurable thread count
- Structured path-pattern parsing (`{sculptor}/{release}/{title}`) to infer metadata from folder layout
- `info.json` and `.url` file parsing for additional metadata
- Presupport detection, warning detection, and scale detection from archive member names
- Custom warning patterns configurable from the UI
- Command-line entry point (`crawl.exe` / `crawl.py`) for headless operation

### Metadata Sidecars

- Per-folder `METADATA.7z` snapshots for fast restore without re-crawling archives
- Optional thumbnail embedding in sidecars
- Scoped sidecar generation (only updated folders are rewritten)
- Import/export functionality for moving libraries between machines

### Storage

- SQLite database with WAL mode and automatic schema migration
- LMDB key-value store for JPEG thumbnails (keyed by image ID)
- Rotating gzip-compressed log file

### Database Model

- `STLModel` with sculptor, category, subcategory, release, scale, and tag relationships
- Many-to-many tag join table
- Release date stored as year + month
- Atomic upsert logic: existing records updated, unchanged records skipped

### UI — Browser

- Filterable, sortable model table with inline cell editing and tag autocomplete
- Multi-value filter support and clear-filters button
- Image pane toggle showing stored thumbnails for the selected model
- Image grid dialog with dynamic thumbnail sizing
- Mass editor dialog for bulk editing sculptor, category, subcategory, and tags across selected models
- File sorting dialog with configurable path patterns and atomic move with rollback on failure

### UI — Dialogs

- Configuration dialog with tree-structured sections (database, thumbnails, warnings, sidecars)
- Column statistics and aggregated statistics dialogs
- Database maintenance dialog (vacuum, integrity check)
- Scale editor dialog
- Help dialog with usage guide, screenshots, and sidecar documentation
- About dialog with license information

### Configuration

- Two-tier config: OS-native `QSettings` for machine-wide paths, per-library JSON for crawler settings
- Configurable: `max_thumbnails`, `max_thumb_size`, `max_workers`, `color_scheme`,
  `metadata_sidecar_enabled`, `metadata_thumbnails`, custom warning patterns
- Platform-specific defaults (Windows / Linux)
- Windows 11 Dark and Light themes
- Nine Material Design themes via qt-material (dark/light variants of Blue, Teal, Purple, Amber, Red, Orange)

### Build & CI

- PyInstaller spec for the GUI application (`STLDatabase.exe`)
- PyInstaller spec for the headless crawler (`crawl.exe`)
- GitHub Actions workflow: tests on Ubuntu (with offscreen Qt), Windows executable builds on tag push
- Artifacts and GitHub Release attachments for both executables on `v*` tags
