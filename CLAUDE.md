# StlArchivist — Developer Guide

## Overview

StlArchivist (formerly stlLibrary) catalogues local collections of 3D-printable model archives (`.zip`, `.7z`,
`.rar`): metadata, thumbnails, tags, duplicates, AI tagging. It is a Rust server
(`stl-server`, over the `stl-core` library) with a React web client served by it, and a Tauri
desktop app (`stl-desktop`) that shows the client in a window and can run a local server.
The PyQt6 application it replaced is in the git history (Phase 8 of
`docs/rust-migration-plan.md` removed it); the data directory format is unchanged.

## Building and running

```bash
.\build.ps1                 # Windows: web/dist + rust/target/release/{stl-server,stl-desktop}
.\build.ps1 -Installer      # + NSIS installer (needs: cargo install tauri-cli --version 2.12.3)
./build.sh [--server]       # Linux / macOS

# Server (takes the library lock)
cd rust && cargo run -p stl-server -- --data-dir <dir> --bind 127.0.0.1:8770
# Desktop app (starts the stl-server beside it for a local library)
cd rust && cargo build -p stl-server -p stl-desktop && cargo run -p stl-desktop

# Web client (Node 24): dev server on :5173 proxying /api to :8770
cd web && npm install && npm run dev
npm run build        # web/dist, served by stl-server
npm test             # vitest
npm run gen:api      # after changing server routes or schemas, then (from the repository root)
python tools/api_doc.py  # docs/api.md, the API reference, from web/openapi.json

# Checks (all must pass)
cd rust && cargo test --workspace && cargo clippy --workspace --all-targets -- -D warnings
cd web && npm test && npx tsc --noEmit -p .
```

## Architecture

```text
stl_database/
├── rust/
│   ├── stl-core/      # Library: archive readers, fingerprints, metadata, rules, thumbnails,
│   │                  #   dHash, CLIP preprocessing, thumbnail store (LMDB), storage (SQLite +
│   │                  #   sqlite-vec), crawler, backups, rescan, sort files, AI tagger text
│   ├── stl-server/    # axum HTTP + WebSocket API, accounts, jobs, sidecars, tools (pdfium,
│   │                  #   ONNX Runtime), AI (llama-server), backups
│   ├── stl-desktop/   # Tauri shell: launcher page (ui/), window on a server, tray, menus
│   └── stl-proc/      # end_with_parent: child processes die with their parent (Windows job)
├── web/               # React 19 + Mantine 8 + TanStack client
├── resources/         # Application icon (copies in rust/stl-desktop/icons)
└── docs/              # rust-migration-plan.md (history and per-step notes)
```

## Rules

- **Compatibility:** the data directory (`stlLibrary.db`, `thumbnails.lmdb/`, `settings.json`,
  `METADATA.7z` sidecars, backups) keeps its formats: bit-identical `archive_hash`, perceptual
  hashes as `"2:" + 32 hex`, the schema in `rust/stl-core/src/storage/schema_sql.rs` (`TABLES`
  plus `ADD_COLUMNS`, migrated by `storage::migrate`; data steps in `storage/mod.rs`). Since
  2026.43.7 the database is no longer readable by the PyQt app: `storage::libraries::migrate`
  converted absolute paths to libraries once (the server backs up first). Libraries, sidecars,
  thumbnails and settings must still open older data directories. See
  `docs/libraries-plan.md` and the migration plan's compatibility rules before changing formats.
- **Paths:** a model's archive is `library_id` + `rel_path` (a `relpath::RelPath`: `/`
  separators, NFC, no `..`) below the root of its library *folder* (a row of `libraries`,
  `storage::libraries::Folder`; the library users see is its `library_groups` row, a
  `Group`, through `libraries.group_id`); never store an absolute path. In Rust go
  between absolute and relative paths with `libraries::Roots` (`locate` = deepest library;
  `locate_or_create` where a move may leave every library), in SQL read
  `schema_sql::archive_path_sql` / `archive_folder_sql` or `models_view.archive_path`, and look
  batches up with `crawl::located_json` + `crawl::LOCATED` (the unique index).
- **Library lock:** `stl_core::lock::LibraryLock` (`library.lock` + `library.owner`). The
  server holds it for its lifetime; never open a library's database or store from a second
  process without it. One `ThumbStore` per process (LMDB).
- **Logic lives in `stl-core`;** the server converts JSON and calls it. Long operations take a
  `Control` (progress, stop).
- **Archive readers** (`stl-core/src/archive/`) return exactly what the original Python
  readers returned, or raise `Unsupported`; callers then fall back (a size/mtime fingerprint
  in the crawler, "cannot read" in rescans). `zip.rs` ports CPython's `zipfile`; never use
  `sevenz-rust2`'s `BlockDecoder` on a block with an empty entry (it drops that block's last
  files). Decode JPEGs with `jpeg-decoder`, not the `image` crate (zune-jpeg misreads the
  core encoder's output).
- **Rules:** custom presupport patterns run only when `rules::portable_pattern` accepts them.

## Server (`rust/stl-server`)

- Handlers convert JSON and call `stl-core`; blocking work goes through `Library::run`.
  Every route gets a `#[utoipa::path]`, an entry in `api::ApiDoc`, and a test in
  `stl-server/tests/` (the router on a temporary library, `tests/common`). Request types use
  `deny_unknown_fields` and validate names before reaching the core.
- **Accounts:** `accounts.rs` over `<data_dir>/server.db` (never the library database). Every
  library route takes an `auth::CurrentUser` and calls `me.require(Role::…)` when it needs
  more than viewer (writes: `Editor`; users, backups, compacting, clearing: `Admin`). Tests get
  a session with `TestLib::user(name, role)`; `library_with` adjusts the config.
- **Edits:** after a write commits, call `lib.schedule_sidecars(archive_paths)`; delete
  thumbnails with one `thumbs.delete_many` after the commit. Writes under `/api/models`,
  `/api/images`, `/api/lookups`, `/api/maintenance`, `/api/libraries` and `/api/ai/commit` are announced by
  the `announce_changes` middleware (POSTs that only read are listed there). Library settings
  come from `library_settings::LibrarySettings` (`settings.json`, read per use).
- **Jobs:** `jobs.rs`. A new kind is a `JobRequest` variant, a `kind()` name, a `run` arm,
  checks in `JobRequest::validate` and (for read-only jobs) `read_only`. Report through
  `job.ctl` and `lib.job_log`, check `job.stopped()` between batches, queue sidecars with
  `job.touch_sidecars`. Results go in `JobResult` (`dupes.rs`), returned in
  `JobDetail.result`. Crawls run `stl_core::crawler::crawl` with `ServerHost`. Monitored
  folders (`monitor.rs`) queue ordinary `crawl` jobs (`keep_missing`) from a background pass.
- **Files:** browsing, crawling and any job given a folder (search folder, sort files,
  update folders) go through `api::files::check_access` (editors inside roots, admins
  anywhere; canonical paths). A crawl of a known folder takes its stored spelling
  (`files::known_spelling`). Never serve a file by a client-given path: downloads go by model
  id, backups by validated name.
- **PDF / CLIP:** `tools.rs` loads pdfium and ONNX Runtime at runtime (never link them); a
  missing library disables the feature. CLIP preprocessing is `stl_core::clip`.
- **AI tagger:** `ai.rs` runs `llama-server` as a child (never link llama.cpp) or uses
  `ai.url`; text handling is `stl_core::ai_tagging` (a port of the old Python
  `ai_tagging.py`, kept faithful: Python `str.split`/`splitlines` semantics).
- **Compute workers:** `workers.rs` (the hub: `/api/workers/connect`, a WebSocket of
  `ToServer`/`ToWorker` JSON) and `worker.rs` (`stl-server worker`, the client; opens no
  library). Embeddings go through `Library::embed` / `can_embed` / `wait_for_embedder`, the
  tagger's model through `workers.chat` then `ai.ask`; never call `tools.embedder` directly
  for library work. `compute: workers` (`Config::compute`) means jobs wait, crawls skip.
- **Backups:** `stl_core::backup` (the old desktop layout); restoring is the offline
  `stl-server restore` command (it takes the library lock). Never replace the database of an
  open library.
- Spawned helpers (`llama-server`, the desktop's local server) call
  `stl_proc::end_with_parent`.

## Web client (`web/`)

React 19 + Mantine 8 + TanStack Query/Table/Virtual. All requests go through
`src/api/client.ts` (`api.GET/POST…` typed from `schema.d.ts`, `unwrap` for errors); never
hand-write API types. After a server route or schema change run `npm run gen:api` (the cargo
test `web_client_types_are_current` enforces it). Keep view logic testable as pure functions
next to the component (`filters.ts`, `rowBlocks.ts`, `columns.ts`, `grid.ts`, …) with a
vitest file. Query keys a library change invalidates are listed in `api/events.ts`
(`LIBRARY_KEYS`). Per-user preferences go through `/api/me/settings`; `localStorage` only for
conveniences (wrap it in try/catch). Pages are hash routes (`shell/route.ts`).

## Desktop shell (`rust/stl-desktop`)

No library logic and no data commands. New commands go into `build.rs`'s app manifest; the
launcher capability (`capabilities/launcher.json`) lists what the bundled page may call, and
server origins get only `SERVER_PERMISSIONS` (`open_folder` and the compute worker's
commands: start/stop/state, its folder's components through `stl-server components`, and
picking its folder; runtime capability in `grant`). The worker commands act for the calling
page's origin only; component changes run the server binary, never code in the shell. Installer
settings are in `tauri.bundle.json`, merged only by `cargo tauri build --config …`, so plain
builds need neither the web client nor the server built.

## Documentation and versions

Always update the README, the changelog and the migration plan with the changes that are
made. Commit messages carry no co-author lines.

**Every commit raises the version**, which is `<year>.<ISO week>.<N>`: the year and ISO week
of the commit date, and `N` counting the commits of that week from 0 (`2026.43.0`,
`2026.43.1`, … then `2026.44.0` in the next week; a new year restarts at its own week). Take
the ISO year and week of the commit date (`date +%G.%V`), and the last part one above the
previous version's when the year and week are unchanged, else 0. The version is the same in four places: `rust/Cargo.toml` (`[workspace.package]
version`, which the server reports), `rust/stl-desktop/tauri.bundle.json` (the installer),
`web/package.json`, and the `Version:` line of `README.md`. Each version gets a
`## <version> — <date>` section in `CHANGELOG.md` with that commit's changes.
