# Server + Web UI Migration Plan

**Status:** proposal, nothing implemented yet.

**Goal:** run stlLibrary as a backend server in Docker on a remote machine, with a React web UI. The PyQt6 desktop app is kept.

## Decisions taken

| Topic | Decision |
|---|---|
| Desktop UI | **Keep both**: add a web UI, and the PyQt app keeps working |
| Users | **Single user on LAN/VPN**, optional static API token |
| Database | Keep **SQLite + LMDB** |
| Web stack | **React + TypeScript** (Vite) |
| AI tagger / CLIP embeddings | **Deferred**: not in the server image for v1, desktop-only |

## Open decisions

1. Should the library mount be **read-only**, or should the server write sidecars and delete files (read-write)?
2. **Migrate the existing DB** with a path rewrite, or start fresh with a crawl on the server?
3. What counts as the **minimum usable version**? Is the browser view plus crawling enough?
4. Should Phase 0 happen on a **long-lived branch**, or land incrementally on `dev`?

---

## Current state (what exploration found)

### Already Qt-free (reusable as-is)
- `app/crawler/archive.py`, `metadata.py`, `rules.py`, `folder_metadata.py`
- `app/database/models.py`, `helpers.py`, `filter_cache.py`
- `app/services/file_deletion.py`
- `app/utils/thumbnailer.py` (bytes API), `path_pattern.py`, `string_utils.py`, `name_dedup.py`, `file_matching.py`

### Where Qt / UI leaks into the core
- **Crawler.** `app/crawler/crawler.py` defines `CrawlerWorker(QThread)` with 9 `pyqtSignal`s. It also imports `compute_dhash_from_jpeg` / `format_dhash` from `app/ui/find_content_duplicates_dialog.py`.
- **Settings.** All getters in `app/ui/config_dialog.py` read `QSettings()` plus `<data_dir>/settings.json`. They are imported lazily by:
  - `utils/logger.py`
  - `utils/image_embedding.py`
  - `utils/ai_runtime.py`
  - `utils/sound.py`
  - `ui/settings_loader.py`
- **SQL profiling flag.** `database/db.py` reads it from `QSettings`.
- **Qt by nature (UI-only, stay as is).** `utils/i18n.py`, `utils/material_icons.py`, and the `QPixmap` helper in `thumbnailer.py`.

### Business logic that lives inside widgets
- **Search and filtering.** About 200 lines of SQL building in `browser_tab.py` (`_apply_checked_filters`, `_run_id_subquery`, faceted scopes, the `_FILTER_PATH_IDS` temp table).
- **Paged row fetch.** `browser_table_model.py` (`_PageFetchRunnable`, `_pack_row`).
- **Filter tree data.** `browser_filter_tree._FilterTreeWorker`.
- **~35 `QThread` / `QRunnable` workers** whose `run()` bodies hold the real logic:
  - `db_maintenance_dialog.py`: Verify, DHash, Embedding, Dedup, Compact, PruneImages, FindDuplicateEntries, MovedInDb, WarningRecheck.
  - `browser_jobs.py`: Rescan, ThumbnailGenerator, ImageDrop, WarningDetail.
  - Also: `metadata_sidecar_dialog.py`, `update_crawled_folders_dialog.py`, `find_duplicates_dialog.py`, `find_content_duplicates_dialog.py`, `tag_editor_dialog.py`, `statistics_dialog.py`, `column_stats_dialog.py`, `backup_restore_dialog.py`.
- **Editor dialogs** (tag, sculptor, collection, category, scale, creator, mass editor) query the session inline.

### Host-bound data
- `STLModel.archive_path`, `STLModel.archive_folder` and `CrawledFolder.path` store absolute **Windows** paths (`D:\…`). In the container they become `/library/…`.
- SQLite (WAL) and LMDB **must live on the container host's local disk**, never on SMB/NFS.

---

## Phase 0: Extract a Qt-free core

**Prerequisite. No behaviour change for the desktop app.**

The same pattern applies to every item. Move the logic into `app/core/` or `app/services/` as plain functions or classes that take:
- a `progress(cur, total)` callback,
- a `log(str)` callback,
- a `threading.Event` for cancel.

The existing Qt class becomes a thin wrapper that calls the function and re-emits its signals. The desktop app keeps working throughout, and every step can be tested with pytest.

This phase is worth doing even if the server never ships. The logic becomes testable, the UI files shrink, and it follows the "UI work budget" rule in `CLAUDE.md`.

### 0.1 Settings provider: `app/core/settings.py` ✅ done
- **Machine-wide values** are `get_data_dir()` and `get_unrar_path()`. They resolve in this order:
  1. Environment variables `STL_DATA_DIR` / `STL_UNRAR_PATH`.
  2. A provider callable registered by the desktop app, which reads `QSettings`.
  3. The platform default.
- **Non-visual getters move here:** thumbs, workers, warning flags, sidecar, trash, file delete, presupport patterns, logging, recurse, embeddings.
- **What stays in `config_dialog.py`:** visual getters (colors, fonts, hotkeys, file manager, notifications). It re-exports the moved getters so existing imports keep working.
- **`build_crawler_config()`** moves here, and `ui/settings_loader.py` becomes a re-export.
- **Lazy imports to repoint** to `app.core.settings`: `logger.py`, `image_embedding.py`, `ai_runtime.py`, `db.py`.

### 0.2 Crawler: `app/crawler/pipeline.py`
- **New class** `Crawler(config, sink, cancel_event)`.
- **`sink`** is a Protocol with the same events as today: `progress`, `log`, `warning`, `phase`, `counts_changed`, `archive_result`, `file_moved`, `file_deleted`, `finished`.
- **`CrawlerWorker(QThread)`** stays, adapting the sink to its signals.
- **dHash helpers** move to `app/utils/image_hash.py`.

### 0.3 Query service: `app/services/library_query.py`
- **`FilterSpec` dataclass** holding the include/exclude lists that `_apply_checked_filters` builds today.
- **Functions:**
  - `build_id_select(spec, skip=…)`
  - `facet_scopes(spec)`
  - `fetch_rows(ids)`
  - `filter_tree_payload(scope)`
  - a sort spec
- **Callers:** `browser_tab.py` and `browser_table_model.py` call this service instead of building SQL.

### 0.4 Job services
Lift worker `run()` bodies into:
- `maintenance.py`
- `duplicates.py`
- `sidecars.py`
- `editing.py` (lookup CRUD, merge and rename, mass edit)
- `stats.py`
- `backup.py`

The order follows web-UI need: rescan, thumbnails and editing first, then maintenance, then duplicates.

### 0.5 Requirements split
| File | Contents |
|---|---|
| `requirements-core.txt` | SQLAlchemy, py7zr, rarfile, Pillow, pymupdf, xxhash, lmdb, rapidfuzz, numpy |
| `requirements-server.txt` | core + fastapi, uvicorn[standard], python-multipart |
| `requirements-desktop.txt` | core + PyQt6, qt-material |
| `requirements.txt` | desktop + dev tools |

### 0.6 Import guard test
A test that imports `app.core`, `app.crawler.*`, `app.database.*`, `app.services.*` and the non-Qt `app.utils` modules with `PyQt6` blocked in `sys.modules`. It keeps the core Qt-free.

---

## Phase 1: Backend server (`server/`, FastAPI)

### App
- **`server/main.py`** is the app factory.
- **On startup** it reads the environment:
  - `STL_DATA_DIR=/data`
  - `STL_LIBRARY_ROOTS=/library`
  - `STL_API_TOKEN` (optional)
  - `STL_UNRAR_PATH`
- **Then** it calls `init_db()` and starts the logger.
- **Routes:** the API is mounted under `/api`, and the built web UI is served from `/`.

### Job runner (`server/jobs.py`)
- **Registry:** in-process, backed by a `ThreadPoolExecutor`. Each job has an id, kind, status, progress, a ring-buffer log and cancel.
- **`JobSink`** implements the crawler sink and the progress callbacks. This replaces Qt signals.
- **Write lock:** a global lock enforces one writer job at a time, because SQLite allows a single writer.
- **Progress** streams over SSE: `GET /api/jobs/{id}/events`.

### Endpoints
| Area | Endpoints |
|---|---|
| Browse | `POST /api/models/search` takes a `FilterSpec` and a sort, and returns `{token, total}`. The server caches the sorted id list per token in an LRU, which avoids slow deep `OFFSET`. `GET /api/models/search/{token}/rows?offset&limit` returns the rows. |
| Facets | `POST /api/filters/tree` returns faceted counts for a `FilterSpec` scope |
| Model | `GET/PATCH /api/models/{id}`, `POST /api/models/bulk` (mass edit), `DELETE /api/models/{id}` (honours allow-delete and the trash setting) |
| Images | `GET /api/images/{id}/thumb` returns JPEG bytes from LMDB with ETag and long cache. `POST /api/models/{id}/images` is a multipart upload. `DELETE /api/images/{id}`. |
| Archive | `GET /api/models/{id}/archive` (download), `GET /api/models/{id}/contents` (list contents) |
| Lookups | CRUD + merge for `tags`, `sculptors`, `collections`, `subcollections`, `categories`, `releases`, `scales`, `creators` |
| Crawl | `POST /api/crawl` returns a job. `GET /api/crawled-folders`, `POST /api/crawled-folders/update-all`. |
| Filesystem | `GET /api/fs/browse?path=` is a directory picker **restricted to `STL_LIBRARY_ROOTS`** that rejects path traversal |
| Jobs | `POST /api/maintenance/{verify,dhash,dedup,compact,prune-images,moved,recheck-warnings,remap-paths}`, `POST /api/duplicates/{name,content,image}`, `POST /api/sidecars/{export,remove}`, `POST /api/backup` |
| Info | `GET /api/stats`, `GET/PUT /api/settings` (non-visual keys only), `GET /api/health` |

### Auth
- **Token:** if `STL_API_TOKEN` is set, every `/api` call needs `Authorization: Bearer <token>`.
- **Web UI:** it asks for the token once and stores it in localStorage.
- **CORS:** not needed, because the UI and API share one origin.

### Desktop features mapped to the server
| Desktop | Server version |
|---|---|
| Open folder in file manager | Download the archive |
| Drag-and-drop images | Multipart upload |
| Sounds and notifications | Browser notifications on "job finished" SSE events |
| Pick a folder to crawl | Browse inside the mounted library roots only |
| AI tagger / embeddings | Disabled in v1 |

### Path migration for an existing database
- **New code:** `app/services/path_remap.py`, plus the `remap-paths` job and a CLI script.
- **What it does:** takes `{from_prefix, to_prefix}` and rewrites `models.archive_path`, `models.archive_folder` and `crawled_folders.path`, turning `\` into `/`.
- **Result:** the existing Windows DB and LMDB can be copied into `/data` and reused without a re-crawl.
- **Fallback:** the existing `MovedInDbWorker` logic.

---

## Phase 2: Docker

### `Dockerfile` (multi-stage)
1. **`node:22-alpine`** builds `web/` into `dist/`.
2. **`python:3.12-slim`**:
   - Installs `requirements-server.txt` and a RAR extractor. Debian's `unrar` is non-free, so either enable the non-free repo or use `unar` / `7zip` through `rarfile`'s tool selection.
   - Copies `app/`, `server/` and `dist/`.
   - Runs as a non-root `PUID`/`PGID` user.
   - Starts `uvicorn server.main:app --host 0.0.0.0 --port 8000 --workers 1`. **It must be a single worker**, because there is one SQLite writer and the job registry and id cache live in memory.

### `docker-compose.yml`
| Item | Value |
|---|---|
| `/data` | Named volume or **local** bind: DB, LMDB, logs, `settings.json` |
| `/library` | Bind mount of the archive tree. Read-write if sidecars, deletion or trash are wanted, otherwise `:ro` with sidecar export disabled. |
| Port | `8000` |
| Healthcheck | `GET /api/health` |
| Env | `STL_API_TOKEN`, `STL_LIBRARY_ROOTS`, `PUID`/`PGID` |

### `.dockerignore`
Excludes `.venv`, `tests`, `resources/material-icons` and local data directories.

### Operational issues
- **Paths:** existing DB paths are Windows paths, so remap them (Phase 1) or re-crawl.
- **Storage:** SQLite WAL and LMDB on a network filesystem risk corruption. The archives can be on a network share, but `/data` cannot.
- **RAR licensing:** affects which base packages the image can use.

---

## Phase 3: Web frontend (`web/`, React + TS + Vite)

### Stack
| Concern | Choice |
|---|---|
| Data fetching | TanStack Query |
| Large table | TanStack Table + TanStack Virtual (rows paged from the search token) |
| API client | Generated from FastAPI OpenAPI (`openapi-typescript` + `openapi-fetch`) |
| Components | Light component library (e.g. Mantine), light/dark mode |
| Dev | Vite proxies `/api` to `localhost:8000` |

### Delivery order
1. **Browser view.** Filter tree with include/exclude tri-state and counts, search box, virtualized table, thumbnail pane with upload, detail/edit drawer. *This covers most daily use and is the hardest part.*
2. **Crawler page.** Folder picker, pattern, overrides, live SSE log and progress, crawled-folders list with "update all".
3. **Settings.**
4. **Lookup editors** (tags, sculptors, collections, categories, scales, creators) and the **mass editor**.
5. **Maintenance, duplicates, statistics and sidecars**, all on a generic job progress view.

The PyQt UI is about 25k lines, so full parity is a large effort. The browser view alone is a useful first version.

---

## Phase 4: PyQt alongside the server

| Mode | Description | Cost |
|---|---|---|
| **Local (v1 default)** | PyQt runs in-process on the Phase 0 core and services. Nothing regresses. | Free after Phase 0 |
| **Remote (later, optional)** | A `LibraryBackend` protocol with `LocalBackend` (calls services) and `HttpBackend` (calls the API and SSE). Widgets are migrated screen by screen. | About as large as building the web UI again |

**Caveat:** the desktop app and the server must **not** open the same SQLite/LMDB at the same time, especially over a network share.

**Recommendation:** decide on remote mode only after the web UI reaches parity. The web UI alone may be enough.

---

## Effort and risk overview

| Phase | Relative effort | Risk |
|---|---|---|
| 0. Extract logic from the UI | Large | Medium (desktop regressions in `browser_tab.py` and others) |
| 1. Server + job runner | Medium | Low |
| 2. Docker + path remap | Small | Low–medium (data migration) |
| 3. Web UI: browser view | Medium–large | Medium |
| 3. Web UI: full parity | Large | Low |
| 4. PyQt remote mode | Large | Medium |

---

## Files

**Modified**
- `app/crawler/crawler.py`
- `app/ui/config_dialog.py`, `settings_loader.py`
- `app/ui/browser_tab.py`, `browser_table_model.py`, `browser_filter_tree.py`, `browser_jobs.py`
- `app/ui/db_maintenance_dialog.py`
- `app/utils/logger.py`, `image_embedding.py`, `ai_runtime.py`
- `app/database/db.py`
- `requirements.txt`, `main.py`

**New**
- `app/core/settings.py`
- `app/crawler/pipeline.py`
- `app/utils/image_hash.py`
- `app/services/library_query.py`, `editing.py`, `maintenance.py`, `duplicates.py`, `sidecars.py`, `stats.py`, `backup.py`, `path_remap.py`
- `server/main.py`, `jobs.py`, `auth.py`, `routes/*`
- `web/`
- `Dockerfile`, `docker-compose.yml`, `.dockerignore`
- `requirements-core.txt`, `requirements-server.txt`, `requirements-desktop.txt`

---

## Docs and tests

### Docs
Per `CLAUDE.md`, update:
- `README.md`: Docker quick start, env vars, volumes, path remap.
- `app/ui/help_dialog.py`: server and web mode.
- `CHANGELOG.md`.
- The architecture section of `CLAUDE.md`: `app/core`, `server/`, `web/`, the services layer.

### Tests (pytest)
- **`library_query`:** filter, sort and facets against in-memory SQLite, compared with current behaviour.
- **`path_remap`.**
- **Crawler `pipeline`:** run with a list-collecting sink.
- **API (`tests/server/`, FastAPI `TestClient`):**
  - search → rows
  - thumbnail bytes
  - crawl job lifecycle over a synthetic archive dir
  - token auth
  - `fs/browse` traversal rejection
- **Import guard:** no PyQt6 in the core.

---

## Verification

1. **After each Phase 0 step:** `python -m pytest tests/ -v` passes, and `python main.py` still browses, filters, crawls and edits as before (manual smoke test).
2. **Local server:** run `uvicorn server.main:app` with `STL_DATA_DIR` pointing at a copy of the data dir. Then:
   - run `remap-paths`;
   - open `/docs`;
   - `curl` search → rows → thumbnail;
   - start a crawl on a small folder and watch the SSE stream.
3. **Remote host:** run `docker compose up --build`. Then check that:
   - the web UI loads at `http://host:8000` and the token prompt works;
   - browsing and filtering work on the real library;
   - a crawl of a mounted folder updates the table;
   - thumbnail upload and archive download work.
4. **Persistence:** restart the container. The data persists in `/data` and the filter cache rebuilds.
