# Changelog

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

### Build & CI

- PyInstaller spec for the GUI application (`STLDatabase.exe`)
- PyInstaller spec for the headless crawler (`crawl.exe`)
- GitHub Actions workflow: tests on Ubuntu (with offscreen Qt), Windows executable builds on tag push
- Artifacts and GitHub Release attachments for both executables on `v*` tags
