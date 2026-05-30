# Changelog

## 2026.22 — 2026-05-30

- Find Duplicate Archives by Content (STL Files tab): selecting a result now previews the thumbnails of both archives side by side.
- Localization: translatable UI via Qt Linguist `.ts` files in `resources/i18n/` (English and Spanish built-in, more drop-in), with a language picker in Configuration; untranslated strings fall back to English.
- Configuration: Language moved to its own sidebar section; Appearance colour pickers grouped into a collapsible, width-scaling 3-column "Custom Colors" section; sidebar labels now translatable.
- Crawler tab: resizeable folder list (splitter), the path-pattern checkbox doubles as the editor show/hide toggle, and Override metadata is now collapsible.

## 2026.21 — 2026-05-20 … 05-24

- Crawler merged with Update Crawled Folders into a single multi-folder dialog with a colour-coded results table.
- Path patterns: `{token:n}` prefix modifier to emit only the first *n* characters of a value.
- Crawler move tracking: deferred deletion of missing records, cross-session reconciliation, and collision guards for ambiguous matches.
- Database Maintenance: distinct "Flagged Missing" status with dedicated removal; search / find-moved now include flagged rows.
- Tools: Backup / Restore Library dialog (packs db, thumbnails, and settings into one `.7z`).
- Performance: `LookupCache` in the mass editor, a `models_view` SQL view for the browser, and filter-pane query optimisations (~24× faster scoped counts; new presupports/source indexes).
- Crawler log hides unchanged archives by default; urllib3 connection-pool noise suppressed.

## 2026.20 — 2026-05-17

- Find Duplicate Archives by Content: right-click to open an archive's folder or delete the archive from disk.
- Configuration: choose the file manager used to open folders (System default / Double Commander / custom).

## 2026.19 — 2026-05-08 … 05-10

- Crawler: path-pattern apply toggle; Override metadata (Creator, Type/Source); moved-file detection (hash, then name+size); auto-restore dialog on finish.
- Filter pane: faceted filtering across all sections, preserved expansion state, solo-on-double-click, and per-item model counts.
- Browser: Edit Authors dialog (rename / delete / merge / merge-similar).
- Mass editor: Find & Replace across selected fields; autocomplete scoped to selected models.
- Database Maintenance: Find Moved in DB + Merge Moved.
- Update Crawled Folders: per-folder checkbox selection.
- New crawler warning: archive without printable files; `.url` search scope restricted.

## 2026.18 — 2026-05-01 … 05-03

- Browser: background crawl with a status indicator; Show Files; delete archive file from disk; File Size column.
- Tools: Find Archives by Content (STL-file overlap and perceptual-image dHash); Find Metadata Duplicates (exact / Levenshtein / Soundex); Archive Contents and Compare Archive Contents dialogs.
- Storage: `stl_files` table recording each `.stl` name and size, included in sidecars.
- Configuration: Browser section renamed File writing; added file-deletion / trash-folder settings and Store STL file list.
- Fix: thumbnail sidebar resize loop.

## 2026.17 — 2026-04-26

Initial alpha release — a desktop app for cataloguing 3D-printable STL archives.

- Crawler: four-phase parallel pipeline (hash → diff → extract → upsert) for `.zip` / `.7z` / `.rar`, CRC fingerprinting, path-pattern + `info.json` / `.url` metadata, presupport/scale/warning detection, and a CLI entry point.
- Storage: SQLite (WAL + migrations), LMDB thumbnails, rotating logs, and per-folder `METADATA.7z` sidecars.
- Browser: filterable/sortable table with inline editing, tag autocomplete, image pane, image grid, mass editor, and file sorting.
- Dialogs: configuration, statistics, column distribution, database maintenance, scale editor, help, and about.
- Configuration: two-tier (QSettings + per-library JSON); Windows 11 and nine Material themes.
- Build/CI: PyInstaller specs (GUI + headless crawler) and GitHub Actions tests / Windows release builds.
