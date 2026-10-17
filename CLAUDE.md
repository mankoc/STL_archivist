# stlLibrary — Developer Guide

## Overview

stlLibrary is a PyQt6/SQLAlchemy desktop application for cataloguing and browsing local collections
of 3D-printable STL model archives. It scans folders of `.zip`, `.7z`, and `.rar` archives, extracts
metadata and thumbnails, persists everything in SQLite + LMDB, and provides a full-featured browser UI.

## Running the Application

```bash
# GUI
python main.py

# Tests
python -m pytest tests/ -v
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
│   ├── database/            # SQLAlchemy ORM + session helpers
│   ├── ui/                  # PyQt6 widgets and dialogs
│   └── utils/               # Shared utilities (thumbnails, logging, string ops, path patterns)
├── resources/
│   ├── icon.ico             # Multi-size ICO (16–256 px) used as the window icon
│   ├── icon.png             # 256 × 256 PNG for packaging / taskbar
│   ├── icon.svg             # Scalable vector version
│   ├── icons/               # Toolbar / tree-view SVG icon set (light + dark variants)
│   └── material-icons/      # Cached Material Design Icons (pre-downloaded for offline use)
├── rust/                    # Rust workspace: stl-core (library), stl-py (PyO3 → `stl_native`)
├── tools/build_native.py    # maturin develop into the running interpreter's environment
└── tests/                   # pytest test suite; tests/golden/ = Rust vs Python equality
```

## Rust migration (in progress)

The app is moving to Rust incrementally, towards a Jellyfin-style server + React web client.
**Read `docs/rust-migration-plan.md` before touching `rust/` or porting a function**, in
particular its compatibility rules (bit-identical `archive_hash`, one LMDB owner per process,
Python owns the schema until Phase 4).

- Ported functions are called through `app/native.py` and always keep a Python fallback
  (`native.module()` is `None` when the module is missing or `STL_NATIVE=0`). Catch
  `TypeError` / `ValueError` / `OverflowError` / `UnicodeError` from the native call and fall
  back; never let the app require `stl_native`.
- Logic lives in `rust/stl-core`; `rust/stl-py` only converts arguments and releases the GIL
  (`py.detach`). Long operations take `progress` / `log` / `should_stop` callbacks, as in
  `app/services`.
- Every port gets a golden test in `tests/golden/` (skipped when `stl_native` is not built)
  and `cargo test` in the crate. Run pytest with and without `STL_NATIVE=0`, plus
  `cargo clippy --workspace --all-targets -- -D warnings`.
- After changing Rust code, rebuild with `python tools/build_native.py` before running pytest.
  It runs Cargo from `rust/` so `rust/.cargo/config.toml` (x86-64-v2, hardware popcount)
  applies; a plain `maturin develop` from the project root silently skips it.
- Native functions today: `archive_fingerprint`, `hamming_pairs`, `cosine_pairs`,
  `levenshtein_pairs`. Long ones take `progress` / `should_stop` and raise
  `stl_native.Stopped`; the binding's `run_job` polls them from the calling thread, so
  Python callbacks never run on Rust threads. Arrays cross as NumPy buffers in and bytes out
  (`np.frombuffer`). Tests for code with a native path should run in both modes (see the
  `implementation` fixture in `tests/test_image_duplicates.py`).

## Module Responsibilities

### `app/crawler/`

Handles all archive I/O and metadata extraction.

- **`archive.py`** — opens `.zip`/`.7z`/`.rar` archives, iterates members, extracts wanted files
  (images, `info.json`, `.url`), computes CRC-based fingerprints without full decompression.
- **`metadata.py`** — infers `ModelMetadata` from `info.json`, `.url` files, and path structure.
- **`rules.py`** — stateless rule functions: `detect_presupport()`, `detect_warnings()`,
  `detect_scale()` applied to archive member lists.
- **`folder_metadata.py`** — reads and writes `METADATA.7z` sidecars (per-folder snapshots that
  allow fast restore on a new machine without re-crawling every archive).
- **`crawler.py`** — `CrawlerWorker(QThread)` orchestrates the four-phase pipeline:
  1. Hash (parallel) → 2. DB query (batch) → 3. Extract (parallel) → 4. Upsert (serial)

### `app/database/`

- **`models.py`** — SQLAlchemy ORM classes: `STLModel` (central), `Tag`, `Image`, `Sculptor`,
  `Category`, `Subcategory`, `Release`, `Scale`. Tags use a many-to-many join table.
- **`db.py`** — engine/session factory (`init_db`, `session_scope`), WAL-mode pragmas,
  schema migration helper (`_ensure_columns`).
- **`vectors.py`** — CLIP embeddings in the sqlite-vec `vec0` table `image_vec` (keyed by
  `Image.id`; `images` has no embedding column). sqlite-vec is loaded per connection in the
  engine's `connect` event; if it fails, `vectors.available()` is False and embedding features
  disable themselves. Image deletes reach `image_vec` through a **TEMP** trigger created per
  connection — never make it a persistent trigger, which breaks image deletes in any connection
  without the extension. vec0 has no `INSERT OR REPLACE` (use `vectors.store`), caps KNN `k`
  at 4096, and must not be joined inside a KNN query: put the `MATCH` in a `MATERIALIZED` CTE.
  Searches go through `app/services/image_similarity.py`. For whole-table reads use
  `vectors.read_all()`, which decodes sqlite-vec's chunk shadow tables (4.7x faster than a
  row scan). It is gated on `_CHUNK_LAYOUT_VERSIONS`; after upgrading sqlite-vec, run
  `tests/test_vectors.py` and add the new version only if the layout test passes.

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

- **`library_query.py`** — the browser's `FilterSpec` and the single predicate builder used by
  both the table (`table_where`) and the faceted filter-pane counts (`facet_scopes`), plus the
  search condition. Add a new filter here once; never re-implement predicates in a widget.
- **`editing.py`** — field-level model edits (`apply_cell_edit`, `assign_tags`, `set_collection`,
  …) built on the `database/helpers.py` get-or-create functions. Use these instead of inline
  `query(...).filter_by(name=…).first()` + `add` + `flush`.
- **`lookups.py`** — `usage_counts(session, kind)`: one `GROUP BY` instead of `len(x.models)`,
  which lazy-loads every referencing model row.
- **`image_similarity.py`** — CLIP searches: `find_similar_models` (KNN over the
  `MAX_RESULT_IMAGES` = 1024 nearest images, best image per model, returns `(results, capped)`)
  and `load_model_vectors` for the all-pairs duplicate scan.
- **`image_duplicates.py`** — Find Duplicate Archives → Images, both methods: collapse identical
  items, tiled all-pairs scan (`near_dhash_pairs` / `near_embedding_pairs`), skip items shared
  by more than N models (`shared_items`), aggregate to model pairs (`model_pairs`). Pigeonhole /
  multi-index Hamming search was tried and rejected: real dHashes cluster, and one segment
  bucket blew up to 30+ GB of candidates. `tests/test_image_duplicates.py` checks the
  aggregation against a slow reference.
- **`model_summary.py`** — `model_summary(session, model_id)`: one-statement display row
  (names, tags, image count) for detail panels; cheap enough for the GUI thread.
- **`maintenance.py`** — Database Maintenance operations (verify, duplicate entries, prune images,
  `compact_database` = `VACUUM` + `wal_checkpoint(TRUNCATE)`, run with `temp_store=FILE`).
- **`file_deletion.py`** — archive deletion / trash.
- **`moves.py`** — moved archives: `relocate`, `merge_into_missing`, `delete_model`,
  `index_by_hash`, `archive_folder_for`. Always set `archive_folder` through it (forward
  slashes; views compare the string). The crawler matches moves by hash in Phase 2, before
  extraction (`CrawlerWorker._match_early_move`).

**Thumbnail deletes:** never call `delete_thumbnail()` in a loop. Each LMDB write transaction
ends with a disk flush (~160 ms on a hard disk). Collect the refs and call
`delete_thumbnails(refs)` once, after the session commits.

### `app/utils/`

- **`thumbnailer.py`** — LMDB key-value store for JPEG thumbnails (keyed by `Image.id`).
  Key API: `save_thumbnail(bytes, image_id)` resizes and writes; `pixmap_from_path(str)` reads
  back a `QPixmap`; `delete_thumbnail(id)` removes an entry.
- **`material_icons.py`** — application icons. Maps icon roles (`ICON_MAP` keys) to Material
  Icon symbols (pre-cached via `setup_material_icons.py`, tinted for light/dark), or, when the
  user picks a colour set in Appearance (`icon_set` setting), to
  `resources/iconsets/<set>/<role>.svg` rendered untinted. Entry point:
  `lookup_icon(app_icon_name, is_dark_mode)`. The colour sets are vendored by `setup_iconsets.py`,
  whose `ROLES` table maps each role per set. **Adding a role** means: add it to `ICON_MAP`,
  add a row to `ROLES`, re-run `setup_iconsets.py`. `tests/test_icon_sets.py` fails until every
  set covers every role.
- **`path_pattern.py`** — parses structured folder paths using `{sculptor}/{release}/{title}`
  syntax and maps extracted values onto ORM model fields.
- **`string_utils.py`** — Levenshtein distance and Soundex (used for fuzzy name matching).
- **`image_hash.py`** — 128-bit dHash and NumPy batch Hamming distance (crawler, maintenance,
  Find by Image, content duplicates).
- **`logger.py`** — rotating gzip-compressed file logger; must be initialised after
  `QApplication` / `QSettings` so the data-dir path is available.

## Crawler Pipeline (four phases per batch)

```
Phase 1  archive_hash()         — parallel  — read CRCs from archive catalog (no decompression)
Phase 2  session.query(batch)   — serial    — check which archives are new or hash-changed
Phase 3  extract_archive_contents() — parallel — decompress only new/changed archives
Phase 4  _upsert_archive()      — serial    — write to DB (SQLite requires single writer)
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
- Database: in-memory SQLite via `init_db(":memory:")` — no test file left on disk.
- Fixtures: `@pytest.fixture(autouse=True)` resets global engine state between tests.
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
