# stlLibrary — Developer Guide

## Overview

stlLibrary is a PyQt6 desktop application over a Rust core for cataloguing and browsing local collections
of 3D-printable STL model archives. It scans folders of `.zip`, `.7z`, and `.rar` archives, extracts
metadata and thumbnails, persists everything in SQLite + LMDB, and provides a full-featured browser UI.

## Running the Application

```bash
# GUI
python main.py

# Tests
python -m pytest tests/ -v

# Server (Phase 5; takes the library lock, so not while the app has the library open)
cd rust && cargo run -p stl-server -- --data-dir <dir> --bind 127.0.0.1:8770

# Web client (Phase 6; Node 24): dev server on :5173 proxying /api to :8770
cd web && npm install && npm run dev
npm run build        # web/dist, served by stl-server
npm test             # vitest
npm run gen:api      # after changing server routes or schemas
```

## Architecture

```
stl_database/
├── main.py                  # GUI entry point
├── create_icon.py           # Regenerate resources/icon.{ico,png,svg} (run manually)
├── app/
│   ├── config.py            # CrawlerConfig dataclass (passed to crawler, not imported by UI layer)
│   ├── core/                # Qt-free core (settings provider; see docs/server-migration-plan.md)
│   ├── services/            # Qt-free business logic (filter model, editing, lookups, maintenance)
│   ├── crawler/             # Archive processing pipeline
│   ├── database/            # init_db, native LibraryDb pool, filter cache
│   ├── ui/                  # PyQt6 widgets and dialogs
│   └── utils/               # Shared utilities (thumbnails, logging, string ops, path patterns)
├── resources/
│   ├── icon.ico             # Multi-size ICO (16–256 px) used as the window icon
│   ├── icon.png             # 256 × 256 PNG for packaging / taskbar
│   ├── icon.svg             # Scalable vector version
│   ├── icons/               # Toolbar / tree-view SVG icon set (light + dark variants)
│   └── material-icons/      # Cached Material Design Icons (pre-downloaded for offline use)
├── rust/                    # Rust workspace: stl-core (library), stl-py (PyO3 → `stl_native`),
│                            #   stl-server (axum HTTP API, Phase 5)
├── web/                     # React + TypeScript web client (Phase 6), served by stl-server
├── tools/build_native.py    # maturin develop into the running interpreter's environment
└── tests/                   # pytest; tests/factory.py builds data; tests/golden/ = Rust vs Python
```

## Rust migration (in progress)

The app is moving to Rust incrementally, towards a Jellyfin-style server + React web client.
**Read `docs/rust-migration-plan.md` before touching `rust/` or porting a function**, in
particular its compatibility rules (bit-identical `archive_hash`, one LMDB owner per process,
the native core owns the schema from Phase 4b).

- The native core is **required** (Phase 4h): `app/native.py`'s `module()` raises
  `RuntimeError` when `stl_native` is missing; there is no `STL_NATIVE` switch. Python
  implementations remain only where the port refuses input (archive readers, Pillow
  thumbnails, non-portable rule patterns, metadata edge cases): catch `TypeError` /
  `ValueError` / `OverflowError` / `UnicodeError` from the native call and fall back, or
  answer the crawl host's request.
- **Server (`rust/stl-server`):** handlers convert JSON and call `stl-core`; blocking work
  goes through `Library::run` (tokio blocking pool). Every route gets a `#[utoipa::path]`
  and an entry in `api::ApiDoc`, and a test in `stl-server/tests/api.rs` (the router on a
  temporary library). Request types use `deny_unknown_fields` and validate names (facets,
  groups, sort columns) before reaching the core.
- **Server accounts:** `stl-server/src/accounts.rs` over `<data_dir>/server.db` (never the
  library database). Every library route takes an `auth::CurrentUser` and calls
  `me.require(Role::…)` when it needs more than `viewer` (writes: `Editor`; users: `Admin`).
  Tests get a session with `TestLib::user(name, role)` (`tests/common`).
- **Server edits:** after a write commits, call `lib.schedule_sidecars(archive_paths)`
  (debounced `METADATA.7z` refresh, `sidecars.rs`); delete thumbnails with one
  `thumbs.delete_many` after the commit. Library settings come from
  `library_settings::LibrarySettings` (the desktop's `settings.json`, read per use).
- **Server jobs:** `stl-server/src/jobs.rs`. A new kind is a `JobRequest` variant plus a
  `run` arm that reports through `job.ctl` (progress) and `lib.job_log` and checks
  `job.stopped()` between batches. Crawls run `stl_core::crawler::crawl` with `ServerHost`,
  which answers `Response::None` until 5d-2 adds native PDF and CLIP. Read-only jobs
  (the duplicate finders in `dupes.rs`) return a typed result in `JobDetail.result` and
  are listed in `JobRequest::read_only`; validate arguments in `JobRequest::validate`. Events go through
  `lib.jobs.events`; writes are announced by the `announce_changes` middleware.
- **Server PDF / CLIP:** `stl-server/src/tools.rs` loads pdfium and ONNX Runtime at
  runtime (never link them); a missing library disables the feature. CLIP preprocessing is
  `stl_core::clip` (golden-tested against Python); keep it in step with
  `image_embedding._preprocess`. Decode JPEGs in Rust with `jpeg-decoder`, not the `image`
  crate (zune-jpeg misreads the core encoder's output).
- **Server files:** browsing and crawling go through `api::files::check_access` (editors
  inside roots, admins anywhere; canonical paths). Never serve a file by a client-given
  path: downloads go by model id.
- **Web client (`web/`):** React 19 + Mantine 8 + TanStack Query/Table/Virtual. All requests
  go through `src/api/client.ts` (`api.GET/POST…` typed from `schema.d.ts`, `unwrap` for
  errors); never hand-write API types. After a server route or schema change run
  `npm run gen:api` (the cargo test `web_client_types_are_current` enforces it). Keep view
  logic testable as pure functions next to the component (`filters.ts`, `rowBlocks.ts`)
  with a vitest file. Query keys: `ids`, `rows`, `tree`, `model`, … (`api/events.ts`
  invalidates them on library changes).
- **Library lock (rule 7):** `stl_core::lock::LibraryLock` (`library.lock` + `library.owner`
  in the data directory). The server and `main.py` both hold it for their lifetime; never
  open a library's database or store from a second process without it.
- Logic lives in `rust/stl-core`; `rust/stl-py` only converts arguments and releases the GIL
  (`py.detach`). Long operations take `progress` / `log` / `should_stop` callbacks, as in
  `app/services`.
- A function that keeps a Python implementation gets a golden test in `tests/golden/`
  (to exercise the Python side, `monkeypatch.setattr(native, "module", lambda: None)` at the
  call site under test). Everything gets `cargo test` in the crate. Run pytest plus
  `cargo clippy --workspace --all-targets -- -D warnings`.
- After changing Rust code, rebuild with `python tools/build_native.py` before running pytest.
  It runs Cargo from `rust/` so `rust/.cargo/config.toml` (x86-64-v2, hardware popcount)
  applies; a plain `maturin develop` from the project root silently skips it.
- Native functions today: `archive_fingerprint`, `archive_members`, `archive_hash_file`,
  `read_archive_members`, `make_thumbnail`, `dhash`, `dhash_many`, `ThumbStore`,
  `detect_presupport`, `detect_warnings`, `explain_warnings`, `detect_scale`, `parse_path`,
  `render_path_pattern`, `soundex`, `hamming_pairs`, `cosine_pairs`, `levenshtein_pairs`,
  `clip_preprocess`, `model_pairs`. Long ones take `progress` / `should_stop` and raise
  `stl_native.Stopped`; the binding's `run_job` polls them from the calling thread, so
  Python callbacks never run on Rust threads. Arrays cross as NumPy buffers in and bytes out
  (`np.frombuffer`). The NumPy scans in `image_duplicates.py` still have Python versions;
  the `implementation` fixture in `tests/test_image_duplicates.py` runs both.
- Native archive reading (`rust/stl-core/src/archive/`: `.zip`, `.7z`, and `.rar` with
  `rar.rs` porting `rarfile`'s header parsing and reading data through UnRAR) must return
  exactly what the Python readers return, or raise. When in doubt it
  raises (`Unsupported`) and `app/crawler/archive.py` (`_native_catalog`, `_read_members`)
  falls back to the Python reader for that archive. `zip.rs` is a port of CPython's `zipfile`
  catalog and `ZipFile.open` checks. Do not use `sevenz-rust2`'s `BlockDecoder` on a block
  with an empty entry inside it: it silently drops that block's last files.
- **Schema changes:** the schema lives in `rust/stl-core/src/storage/schema_sql.rs` (the
  source of truth; `TABLES` plus `ADD_COLUMNS` for columns added to existing tables) and is
  migrated by `storage::migrate`, called from `db.init_db`. A data migration step goes into
  `storage/mod.rs`. `init_db` refuses `:memory:`.
- **All database access is native:** `app.database.native_db.library()` returns the
  `stl_native.LibraryDb` pool (any platform; raises before `init_db`). Services call its
  methods; semantics live in `rust/stl-core/src/storage/*.rs` (`library.rs` reads, `edit.rs`
  cell edits / moves / lookups, `model_edit.rs`, `admin.rs`, `browser.rs`, `stats.rs`,
  `views.rs`, `duplicates.rs`, `maintenance.rs`, `crawl.rs`, `jobs.rs`, `sidecar.rs`,
  `vectors.rs`). Plain `sqlite3` connections (tests) have no sqlite-vec and no vec0 delete
  trigger: write and read vectors through `LibraryDb`.
- **Dialogs do not query the database.** Each one uses a service:
  - lookup editors: `services/lookup_admin.py`;
  - model editors: `services/model_edit.py`;
  - browser panel, lookup lists and sort dialog: `services/browser_data.py`;
  - statistics: `services/library_stats.py`;
  - viewers: `services/model_views.py`;
  - content duplicates and folder lists: `services/content_duplicates.py`;
  - Database Maintenance: `services/maintenance.py`;
  - archive jobs (rescan, thumbnails, reconciliation): `services/archive_jobs.py`;
  - cell edits: `editing.apply_cell_edits`.

  New database work belongs in a service backed by a `LibraryDb` method, never in a dialog.
- Crawler rules (`rules.py`) use the native port with the built-in presupport patterns and
  with custom lists whose patterns are all portable (`rules.portable_pattern_error`,
  mirrored by `rules::portable_pattern`; change both together). Other lists run in Python,
  because fancy-regex and `re` read some syntax differently without raising. Keep `_native_patterns_ok` when touching them.
- Thumbnails: `thumbnailer.make_thumbnail` tries the native `make_thumbnail`
  (`rust/stl-core/src/thumbnail.rs`) and falls back to Pillow (`_make_thumbnail_py`) on
  `ValueError` (AVIF, CMYK JPEG, corrupt data). Output sizes follow `Image.thumbnail`.
- **Perceptual hashes are native-only (format 2).** Stored as `"2:" + 32 hex`
  (`format_dhash`); test a stored value with `is_current_dhash`, never with a length check on
  `DHASH_HEX_LEN`. Older Pillow hashes (bare hex) count as missing. Never add a Python
  fallback that writes hashes, because one library must not mix two algorithms. Batch work goes through `compute_dhashes`
  (one parallel native call); see `services.maintenance.recompute_dhashes`.

## Module Responsibilities

### `app/crawler/`

Handles all archive I/O and metadata extraction.

- **`archive.py`** — opens `.zip`/`.7z`/`.rar` archives, iterates members, extracts wanted files
  (images, `info.json`, `.url`), computes CRC-based fingerprints without full decompression.
- **`metadata.py`** — infers `ModelMetadata` from `info.json`, `.url` files, and path structure.
- **`rules.py`** — stateless rule functions: `detect_presupport()`, `detect_warnings()`,
  `detect_scale()` applied to archive member lists.
- **`folder_metadata.py`** — reads and writes `METADATA.7z` sidecars (per-folder snapshots that
  allow fast restore on a new machine without re-crawling every archive). Both directions
  run natively (`storage::sidecar`, through `thumbnailer.native_store()`).
- **`crawler.py`** — `CrawlerWorker(QThread)` orchestrates the four-phase pipeline:
  1. Hash (parallel) → 2. DB query (batch) → 3. Extract (parallel) → 4. Upsert (serial)
  `run()` hands the whole crawl to `stl_core::crawler` (`LibraryDb.crawl`), which asks
  Python only for what it cannot do exactly (`_NativeCrawlServices`: refused archives,
  Pillow-only images, PDF pages, CLIP, non-portable rules, …). `recrawl` uses the native
  database phases (`_native_diff`, `_native_upsert`).

### `app/database/`

- **`native_db.py`** — `library()`: the native `LibraryDb` connection pool for the open
  database (raises before `init_db`); `reset()` drops it.
- **`db.py`** — `init_db(path)` (migrates through the native core and opens the thumbnail
  store beside the database), `database_path()`, `clear_db()`, `backfill_archive_folder()`.
- **`filter_cache.py`** — the unscoped filter-tree payload, stored through `LibraryDb`.
- **`vectors.py`** — constants for the sqlite-vec `vec0` table `image_vec` (keyed by
  `Image.id`; `images` has no embedding column). The native core bundles sqlite-vec and owns
  the table (`storage/vectors.rs`): writes, KNN searches (in a `MATERIALIZED` CTE, `k` capped
  at 4096) and whole-table reads from the chunk shadow tables. Image deletes reach
  `image_vec` through a **TEMP** trigger created per native connection — never make it a
  persistent trigger, which breaks image deletes in any connection without the extension.
  Searches go through `app/services/image_similarity.py`.

### `app/ui/`

PyQt6 widgets and dialogs. The main window (`main_window.py`) hosts a `BrowserTab` and opens the
`CrawlerTab` on demand. Fourteen dialog files cover configuration, editing, bulk operations,
and metadata management.

**Image preview pane** (`browser_tab.py` — `_thumb_scroll` / `_thumb_vlayout`):
- Displays JPEG thumbnails stored in LMDB for the currently selected model.
- Accepts drag-and-drop of local image files (`.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.webp`,
  `.tiff`): each dropped file is stored as a new `Image` DB record and its thumbnail is written
  to LMDB via `save_thumbnail()`, then the pane refreshes automatically.
- Drop handling lives in `eventFilter` (already filtering the scroll area for resize events) and
  `_handle_image_drop`. The selected model is tracked in `self._current_model_id`.

**UI work budget.** No method invoked on the GUI thread may perform DB, LMDB, archive, or
file I/O beyond a single cheap row lookup. Anything heavier must be dispatched to a
`QThread`, `QRunnable`, or coalesced via `QTimer`. New widgets that need data should
request it from the existing loader/cache layers (`ThumbnailLoaderWorker`, `_FilterTreeWorker`,
`TableLoaderWorker`, `lookup_cache`), not query directly.

**Sidecar refresh after edits.** Never call `export_folder_metadata()` from a GUI-thread save.
Commit first, then call `app.ui.sidecar_exporter.schedule([folder, …])`: it debounces,
exports each folder once on a background thread, and is flushed on application close.

**Worker threads.** A `QThread` subclass must never declare a signal named `finished`, because
it hides Qt's real `QThread.finished`. Name result signals `<what>_finished`, and release or
`deleteLater()` a worker only from the real `finished` signal, or parent it to its owner.
`tests/test_worker_signal_shadowing.py` enforces the rule app-wide. New worker logic goes in
a plain function in `app/services/` that takes `progress` / `log` / `should_stop` callbacks;
the `QThread` only wraps it (see `VerifyWorker` → `services.maintenance.verify_archives`).

### `app/services/`

Qt-free business logic, testable without a `QApplication`:

- **`library_query.py`** — the browser's `FilterSpec` (facets, groups, path pruning);
  `table_ids` / `page_rows` are what the table model loads (ties ordered by id). Predicates
  live in `storage/library.rs`, shared by the table and the filter-pane counts. Add a new
  filter there once; never re-implement predicates in a widget.
- **`filter_tree.py`** — `filter_tree_data(spec, show, show_counts)`: every value list and
  count the filter pane shows; `_FilterTreeWorker` wraps it and caches the unfiltered result.
- **`editing.py`** — `apply_cell_edits([(model_id, field, text)])`: browser cell edits in
  one transaction (`CELL_FIELDS`); returns the touched folders for sidecar export.
- **`lookups.py`** — `usage_counts(kind)` / `usage_count(kind, id)`.
- **`image_similarity.py`** — CLIP searches: `find_similar_models` (KNN over the
  `MAX_RESULT_IMAGES` = 1024 nearest images, best image per model, returns `(results, capped)`)
  and `load_model_vectors` for the all-pairs duplicate scan.
- **`image_duplicates.py`** — Find Duplicate Archives → Images, both methods: collapse identical
  items, tiled all-pairs scan (`near_dhash_pairs` / `near_embedding_pairs`), skip items shared
  by more than N models (`shared_items`), aggregate to model pairs (`model_pairs`). Pigeonhole /
  multi-index Hamming search was tried and rejected: real dHashes cluster, and one segment
  bucket blew up to 30+ GB of candidates. `tests/test_image_duplicates.py` checks the
  aggregation against a slow reference.
- **`lookup_admin.py`** — the lookup editors' list / add / rename / delete / merge, author
  renames and creator profiles.
- **`model_edit.py`** — the editor dialog, AI tagger and mass editor: completion lists,
  model snapshots, saves (the mass save takes a plain plan dict).
- **`browser_data.py`** — browser info panel, thumbnail list, dropped images, image / model
  deletes, Add Tag, `lookup_cache` lists, the sort dialog's sources and path update
  (`set_archive_paths` also sets `archive_folder`).
- **`library_stats.py`** — Statistics and Column Distribution queries; ties ranked by value
  or id so both implementations agree.
- **`model_views.py`** — read-only viewer data: archive contents, archive browser folders
  and cells, image grid, Find Duplicates metadata rows, Find by Image dHash rows.
- **`content_duplicates.py`** — Find Duplicate Archives (content): STL file-list overlap
  scan, popup thumbnail ids, image-scan reads; crawled-folder list; sidecar folder list.
- **`model_summary.py`** — `model_summary(_session, model_id)`: one-statement display row
  (names, tags, image count) for detail panels; cheap enough for the GUI thread.
- **`maintenance.py`** — Database Maintenance operations (the dialog's reads and writes:
  embedding targets / store, warning re-check, moved-record merge, record removal; verify,
  duplicate entries, prune images,
  `compact_database` = `VACUUM` + `wal_checkpoint(TRUNCATE)`, run with `temp_store=FILE`).
- **`archive_jobs.py`** — Generate Thumbnails, Rescan, Update Crawled Folders' reconciliation
  writes (native in `storage::jobs`).
- **`file_deletion.py`** — archive deletion / trash (`delete_archives(ids, use_trash, trash)`
  manages its own transaction).
- **`moves.py`** — `archive_folder_for` (forward slashes; views compare the string) and
  `index_by_hash`. Relocation, merging into a missing record and deletion are `LibraryDb`
  methods (`relocate`, `merge_into_missing`, `delete_model`), which set `archive_folder`. The crawler matches moves by hash in Phase 2, before
  extraction (`CrawlerWorker._match_early_move`).

**Thumbnail deletes:** never call `delete_thumbnail()` in a loop. Each LMDB write transaction
ends with a disk flush (~160 ms on a hard disk). Collect the refs and call
`delete_thumbnails(refs)` once, after the database write commits.

### `app/utils/`

- **`thumbnailer.py`** — LMDB key-value store for JPEG thumbnails (keyed by `Image.id`).
  Key API: `save_thumbnail(bytes, image_id)` resizes and writes; `pixmap_from_path(str)` reads
  back a `QPixmap`; `delete_thumbnail(id)` removes an entry. The store is held by
  `_NativeBackend` (`stl_native.ThumbStore`, heed; key layout in `thumbstore.rs`). Never open
  it a second time in the same process (LMDB forbids it); call `close_thumbnail_store()`
  first. Go through the module functions or `_store` methods.
- **`material_icons.py`** — application icons. Maps icon roles (`ICON_MAP` keys) to Material
  Icon symbols (pre-cached via `setup_material_icons.py`, tinted for light/dark), or, when the
  user picks a colour set in Appearance (`icon_set` setting), to
  `resources/iconsets/<set>/<role>.svg` rendered untinted. Entry point:
  `lookup_icon(app_icon_name, is_dark_mode)`. The colour sets are vendored by `setup_iconsets.py`,
  whose `ROLES` table maps each role per set. **Adding a role** means: add it to `ICON_MAP`,
  add a row to `ROLES`, re-run `setup_iconsets.py`. `tests/test_icon_sets.py` fails until every
  set covers every role.
- **`path_pattern.py`** — parses structured folder paths using `{sculptor}/{release}/{title}`
  syntax and renders sort patterns; applying the values to a model happens natively (crawl
  upsert, mass save).
- **`string_utils.py`** — Levenshtein distance and Soundex (used for fuzzy name matching).
- **`image_hash.py`** — 128-bit dHash (format 2, computed natively; storage format and
  parsing here) and NumPy batch Hamming distance (crawler, maintenance, Find by Image,
  content duplicates).
- **`logger.py`** — rotating gzip-compressed file logger; must be initialised after
  `QApplication` / `QSettings` so the data-dir path is available.

## Crawler Pipeline (four phases per batch)

```
Phase 1  archive_hash()         — parallel  — read CRCs from archive catalog (no decompression)
Phase 2  crawl diff (native)    — serial    — check which archives are new or hash-changed
Phase 3  extract_archive_contents() — parallel — decompress only new/changed archives
Phase 4  upsert (native)        — serial    — write to DB (SQLite requires single writer)
Post     export_folder_metadata() — serial  — update METADATA.7z for modified folders
```

## Configuration

Two-tier config:

1. **`QSettings`** (OS-native store) — global, machine-wide:
   - `DATA_DIR_KEY` — directory for the `.db` file, LMDB store, and logs
   - `UNRAR_TOOL_KEY` — path to `unrar` binary

2. **Per-library JSON** (`<data_dir>/settings.json`) — persisted via `QSettings` but per
   data-directory, so different libraries can have different settings:
   - `max_thumbnails`, `max_thumb_size`, `max_workers`
   - `color_scheme`, `metadata_sidecar_enabled`, `metadata_thumbnails`
   - Crawler warning flags

`CrawlerConfig` dataclass is populated from these settings and passed into `CrawlerWorker` —
business-logic code has **no direct dependency on the UI layer**.

The non-visual getters and `build_crawler_config()` live in **`app/core/settings.py`** (Qt-free).
`get_data_dir()` / `get_unrar_path()` resolve in this order: env var (`STL_DATA_DIR` /
`STL_UNRAR_PATH`), then a provider registered with `register_providers()` (the desktop registers
`QSettings` readers when `app.ui.config_dialog` is imported), then the platform default.
`config_dialog` re-exports these getters. Business-logic code should import from
`app.core.settings`, not `app.ui.config_dialog`. Visual and widget-only settings (colors, fonts,
hotkeys, notifications, file manager) stay in `config_dialog`. Tests that monkeypatch
`_load_library_settings` for a core getter must patch `app.core.settings`.

## Circular-Import Pattern

Business-logic modules (`crawler.py`, `logger.py`) need values from `app.ui.config_dialog`
(e.g. feature flags, paths). Importing at module level would create a circular dependency
(`ui → crawler → ui`). The workaround is **deferred imports inside functions**:

```python
# Inside a method, not at the top of the file — intentional circular-import avoidance
from app.ui.config_dialog import get_warn_presupported_stl
```

Do not hoist these to module-level without first ensuring the import chain is safe.

## Documentation
Always update the readme, help and changelog with the changes that are made.

## Test Conventions

- Framework: `pytest`
- Database: the autouse `library_db` fixture (`tests/conftest.py`) gives every test its own
  database file and thumbnail store under `tmp_path`. Build and read data with
  `tests/factory.py` (`add_model`, `add_image`, `link_tags`, `row`, `rows`, `count`, …,
  plain `sqlite3`), then call the services.
- Archive tests use small synthetic archives built in `tempfile.TemporaryDirectory`.
- UI is not covered by automated tests.

## Storage

| Store | Path | Contents |
|---|---|---|
| SQLite | `<data_dir>/*.db` | All model metadata, relationships |
| sqlite-vec | `image_vec` table in the `.db` | CLIP image embeddings (512-d float32), keyed by `Image.id` |
| LMDB | `<data_dir>/thumbnails.lmdb/` | JPEG thumbnails, keyed by `Image.id` |
| Log | `<data_dir>/stlLibrary.log` | Rotating compressed log |
| Sidecar | `<archive_folder>/METADATA.7z` | Per-folder snapshot for fast restore |
