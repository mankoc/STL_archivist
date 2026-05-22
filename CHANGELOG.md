# Changelog

## 2026.21 — 2026-05-22

### Filter pane — path filter performance (~24× faster scoped counts)

When a folder node is selected in the filter pane the browser rebuilds scoped
counts for all 13 filter sections.  Each scoped-count query previously embedded
`replace(archive_path, '\\', '/') LIKE ?` inline; SQLite cannot index a function
call on the column, so every query did a full table scan (~37 ms each, ×26
queries ≈ ~960 ms total per rebuild).

The fix materialises the matching model IDs in a single LIKE pass, then all
subsequent queries use `WHERE id IN (…)` — a primary-key lookup.  Scoped
filter-pane refresh with an active folder selection now costs ~40 ms instead
of ~960 ms.

**File:** [app/ui/browser_tab.py:2485-2525](app/ui/browser_tab.py#L2485-L2525)

---

### Path Patterns — prefix modifier

- **`{token:n}` modifier** — any path-pattern token can now be followed by `:<n>` to emit
  only the first *n* characters of the field's value when rendering a destination path.
  This is useful for grouping files into initial-letter buckets without a separate tool:
  ```
  {creator:1}/{creator}/{title}
  ```
  With creator `"Sanix"` the above renders as `S/Sanix/<title>`.
- The modifier applies to file-move output (`render_path_pattern`); when parsing an existing
  path for metadata extraction the full segment is captured as usual.
- `PATTERN_HELP` (the inline tooltip shown in the Sort Files and Crawler dialogs) updated
  with an example.
- README **Path Patterns** section updated with the full token table and modifier docs.
- Help dialog token table updated with all current aliases; new *Prefix modifier* section added.

---

## 2026.21 — 2026-05-20

### Crawler — Move tracking improvements

- **Deferred deletion of missing records** — archives that go missing during a crawl are no
  longer immediately deleted from the database; they are flagged `is_missing = True` so a
  future crawl of any folder can still attempt to reconcile the move
- **Cross-session reconciliation** — `is_missing` records are included in every subsequent
  crawl's missing candidate pool, meaning a file moved to a folder not covered by the current
  crawl session can still be matched in a later run
- **Collision guards in move matching** — ambiguous matches are now skipped rather than
  resolved arbitrarily:
  - If two new archives share the same content hash (rare fallback-fingerprint collision),
    neither is used for hash-based matching
  - If two new archives share the same filename + file size, the name+size fallback is
    suppressed for that key
  - If two missing records share the same content hash (content duplicates), hash matching
    is skipped for both

### Tools — Database Maintenance

- **Flagged Missing status** — Verify now distinguishes records flagged `is_missing` by the
  crawler (shown in purple as *Flagged Missing*) from records that are newly found absent
  (*Missing*); if a flagged file reappears on disk the flag is cleared automatically
- **Remove Flagged Missing button** — new button removes only crawler-flagged records that
  are still absent, separate from *Remove All Missing* which targets newly-detected absences
- **Search / Find Moved include flagged records** — *Search in Folder…* and *Find Moved in
  DB…* now include *Flagged Missing* rows as candidates alongside *Missing* ones
- **Compute dHashes removed from UI** — the *Compute dHashes* button has been removed from
  the toolbar; the worker remains available programmatically

### Tools — Backup / Restore

- **New Backup / Restore Library… dialog** — accessible from **Tools → Backup / Restore
  Library…**; packs `stlLibrary.db`, `thumbnails.lmdb/`, and `settings.json` into a
  single `.7z` archive; restore extracts back into the data directory after a confirmation
  prompt; both operations run in a background thread with a progress bar and log panel

### Logging

- **urllib3 connection-pool messages suppressed** — `urllib3.connectionpool` is now set to
  `WARNING` level at startup, removing verbose HTTP debug lines forwarded by `dku.utils`

### Crawler — Results log

- **Unchanged archives hidden by default** — the crawler log no longer prints a line for
  each unchanged archive (`log_unchanged` defaults to `False`); only new, updated, and
  errored archives appear in the results

---

## 2026.20 — 2026-05-17

### UI — Find Duplicate Archives by Content

- **Open folder in file manager** — right-clicking any row in the STL Files or Images results
  tables now shows **Open Folder of Archive A in Explorer** and **Open Folder of Archive B in
  Explorer**; both actions open the parent directory of the respective archive in the configured
  file manager
- **Delete archive from disk** — two additional right-click actions, **Delete Archive A from
  Disk…** and **Delete Archive B from Disk…**, appear when *Allow file delete* is enabled in
  Configuration; they follow the same trash / permanent-delete logic as the main browser's delete
  action (DB record removed, LMDB thumbnails purged, file moved to trash or permanently deleted);
  the row is removed from the results table on success

### Configuration — Browser

- **File Manager** — new *File Manager* group in **Configuration → Browser** lets you choose
  which program is used to open archive folders from right-click menus:
  - **System Default** — delegates to `QDesktopServices` (OS-native file manager)
  - **Double Commander** — launches Double Commander with the target folder; the executable
    path is pre-filled with the platform default
    (`C:\Program Files\Double Commander\doublecmd.exe` on Windows, `doublecmd` on Linux)
  - **Custom…** — specify any executable; path field and Browse button become visible when
    a non-system option is selected

---

## 2026.19 — 2026-05-10

### UI — Crawler

- **Path pattern toggle** — a new *Apply path pattern to metadata extraction* checkbox
  sits below the pattern input; it is enabled only when the pattern field is non-empty.
  Unticking it runs the crawl without path-pattern extraction while keeping the saved
  pattern text for future use.
- **Override metadata** — a new *Override metadata (leave blank to auto-detect)* group box
  exposes two force fields: **Creator** and **Type / Source**. Any non-blank value is
  applied to every archive in the crawl, overriding whatever was auto-detected from the
  path or `info.json`.

### UI — Update Crawled Folders dialog

- **Folder selection list** — the dialog now shows a checkbox list of every stored
  `CrawledFolder` record. All folders are pre-selected; individual folders can be
  unchecked to skip them. **Select All** / **Deselect All** buttons are provided.
  The list is disabled while a crawl is in progress and re-enabled on completion.

### UI — Filter Panel

- **Faceted filtering extended to all sections** — all 13 filter sections (Authors, Tags,
  Type, Date, Sculptor, Category, Subcategory, Release, Scale, Presupports, Warnings,
  Path, and the group-header has/no-value filters) now update dynamically whenever any
  other filter changes. Previously only five sections were cascading.
- **Expansion state preserved** — expanding or collapsing a group with sub-items (e.g.
  Path tree nodes) no longer collapses unexpanded siblings when a filter is applied or
  the tree is rebuilt.

### Crawler — Warnings

- **Archive without printable files** — new warning flag raised when an archive contains
  no file with a printable extension (`.stl`, `.obj`, `.lys`, `.chitubox`). Configurable
  via **Configuration → Crawler → Warnings → Archive without printable files**.

### Crawler — URL discovery

- **Restricted URL search scope** — `find_url_file()` now limits its search to three
  locations: (1) a `.url` file inside the archive itself, (2) a `.url` file in the same
  directory as the archive, and (3) a `.url` file inside any `*-links` or `*_links`
  sibling folder. Grandparent directory traversal has been removed to avoid picking up
  unrelated `.url` files from parent artist folders.

---

## 2026.19 — 2026-05-08

### UI — Browser

- **Edit Authors dialog** — new right-click context menu item *Edit Authors…* (available when
  clicking the Author column); lists every distinct author with model counts and supports:
  - **Rename** — updates all models and the underlying creator profile
  - **Delete** — sets `creator = null` on all affected models and removes the profile
  - **Merge** — Ctrl+select two authors and merge them into one; prompts to choose the kept name
  - **Merge Similar** — Levenshtein-distance scan with a configurable threshold; same UX as the
    Tag editor's similarity merge

### UI — Filter Panel

- **Solo on double-click** — double-clicking the *text label* of any leaf filter item clears
  all other active filters and checks only the clicked item, making it easy to isolate a single
  value without manually unchecking everything else
- **Model counts** — each filter item now shows the number of matching models in parentheses
  (e.g. `Jane Doe (42)`); counts for path-tree nodes accumulate all descendant models;
  configurable via **Configuration → Browser → Show model counts in filter pane**

### UI — Crawler

- **Moved-file detection** — when the crawler would normally delete a missing archive record,
  it first searches among newly discovered archives for a match by hash (definitive) or by
  filename + size (fallback); on a match the old record's path is updated to the new location
  and the duplicate new record is removed, preserving all metadata, images, and thumbnails
- **Auto-restore on finish** — when the crawler dialog is hidden (running in background) and
  the crawl completes, the dialog is automatically brought back to the foreground

### UI — Database Maintenance

- **Find Moved in DB** — new *Find Moved in DB…* button scans all database records to find
  archives flagged as missing that appear to have been moved elsewhere in the database; matching
  is done by hash first, then by filename + size; matches are highlighted as
  *Moved (in DB)* rows in a new *Match Path* column
- **Merge Moved** — new *Merge Moved* button updates the matched old record to the new path
  and removes the duplicate, consolidating both records into one

### UI — Mass Editor

- **Find & Replace — field selection** — the Find & Replace operation now applies to any
  combination of user-selected fields (Title, Creator, Source, URL); previously it was limited
  to Title only; each field has an individual checkbox under *Apply to:*
- **Filtered autocomplete** — the autocomplete/dropdown suggestions for Collection,
  Subcollection, Release date, and Tags are now scoped to only the values present among the
  currently selected models, reducing noise when editing a focused subset

### Configuration

- Added **Show model counts in filter pane** checkbox in **Configuration → Browser**;
  when unchecked the parenthesised counts are hidden from all filter panel items

---

## 2026.18 — 2026-05-03

### Misc

- Bumped version to v0.5.0. Help and About dialogs now display the correct application version.

---

## 2026.18 — 2026-05-02

### UI — Browser

- **Background crawl** — the Crawler dialog is now persistent; a new **Run in Background**
  button dismisses the dialog without stopping the worker; a status indicator in the
  main-window status bar shows `Crawler: N / total` while a crawl runs in the background;
  clicking it brings the dialog back
- **Row selection** — switched thumbnail/info-panel updates from `clicked` to
  `selectionModel().currentChanged`; deselecting all rows now properly clears the thumbnail
  pane and info labels
- **Show Files** — new right-click context menu item (single selection); opens an **Archive
  Contents** dialog listing every `.stl` file in the `stl_files` table (filename, size, hash)
- **Delete archive file from disk** — new right-click context menu item (visible only when
  *Allow file delete* is enabled in Configuration); physically removes or moves to trash the
  selected archive file(s) and deletes the corresponding database records
- Renamed the existing *Delete N model(s)…* context item to *Delete N model(s) from DB…*
  to distinguish database-only deletion from the new physical-delete action
- `show_model_ids()` and `show_duplicate_pairs()` now auto-select the first row after
  filtering so the thumbnail pane is immediately populated

### UI — Configuration

- Renamed the **Browser** settings section to **File writing**
- Moved the **Sidecar** settings as a child item under **File writing**
- Added file-deletion settings under **File writing**:
  - *Allow file delete* — destructive option highlighted in red; guarded by a confirmation
    dialog that requires ticking a checkbox before the OK button is enabled
  - *Use trash folder* — when enabled, files are moved to a configured folder instead of
    being permanently deleted; a JSON recovery info sidecar is written alongside
  - *Trash folder* path picker with a Browse… button; only editable when *Allow file delete*
    is active

### UI — Dialogs

- **Archive Contents (`FileListDialog`)** — lists every STL file stored for a given model:
  filename, formatted size, and archive hash; opened via the right-click *Show Files…* item
- **Compare Archive Contents (`FileComparisonDialog`)** — side-by-side comparison of two
  archives' STL file lists; configurable match key (Filename / Size / Hash); greedy matching
  highlights paired files with a green `==` marker; unmatched files shown in trailing rows

### UI — Tools

- Added right-click context menus to both the **STL Files** and **Images** results tables in
  the **Find Archives by Content** dialog
- **Compare Archive Contents…** opens `FileComparisonDialog` for the selected duplicate pair

### Bug fixes

- Thumbnail sidebar infinite resize loop: `eventFilter` now watches the `QScrollArea` widget
  instead of its viewport, and a width-cache guard skips redundant rescale passes

---

## 2026.18 — 2026-05-02

### UI — Tools

- **Find Archives by Content**: added **Path A** and **Path B** columns to both the
  STL Files and Images results tables, showing the full archive path for each member
  of a duplicate pair

---

## 2026.18 — 2026-05-01

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

## 2026.18 — 2026-04-29

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

## 2026.17 — 2026-04-26

Initial alpha release of stlLibrary, a desktop application for cataloguing local collections of
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

- PyInstaller spec for the GUI application (`stlLibrary.exe`)
- PyInstaller spec for the headless crawler (`crawl.exe`)
- GitHub Actions workflow: tests on Ubuntu (with offscreen Qt), Windows executable builds on tag push
- Artifacts and GitHub Release attachments for both executables on `v*` tags
