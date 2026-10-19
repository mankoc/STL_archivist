# stlLibrary — Developer Guide

## Overview

stlLibrary catalogues local collections of 3D-printable model archives (`.zip`, `.7z`,
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
npm run gen:api      # after changing server routes or schemas

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
  `METADATA.7z` sidecars, backups) stays readable by the PyQt app's last version: bit-identical
  `archive_hash`, perceptual hashes as `"2:" + 32 hex`, the schema in
  `rust/stl-core/src/storage/schema_sql.rs` (`TABLES` plus `ADD_COLUMNS`, migrated by
  `storage::migrate`; data steps in `storage/mod.rs`). Read the migration plan's compatibility
  rules before changing formats.
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
  `/api/images`, `/api/lookups`, `/api/maintenance` and `/api/ai/commit` are announced by
  the `announce_changes` middleware (POSTs that only read are listed there). Library settings
  come from `library_settings::LibrarySettings` (`settings.json`, read per use).
- **Jobs:** `jobs.rs`. A new kind is a `JobRequest` variant, a `kind()` name, a `run` arm,
  checks in `JobRequest::validate` and (for read-only jobs) `read_only`. Report through
  `job.ctl` and `lib.job_log`, check `job.stopped()` between batches, queue sidecars with
  `job.touch_sidecars`. Results go in `JobResult` (`dupes.rs`), returned in
  `JobDetail.result`. Crawls run `stl_core::crawler::crawl` with `ServerHost`.
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
server origins get only `allow-open-folder` (runtime capability in `grant`). Installer
settings are in `tauri.bundle.json`, merged only by `cargo tauri build --config …`, so plain
builds need neither the web client nor the server built.

## Documentation and versions

Always update the README, the changelog and the migration plan with the changes that are
made. Commit messages carry no co-author lines.

**Every commit raises the version**, by at least the minor part (`2026.25.0` → `2026.26.0`;
the major part for sweeping changes), chosen by the scope of the change; never a patch-only
bump. The version is the same in four places: `rust/Cargo.toml` (`[workspace.package]
version`, which the server reports), `rust/stl-desktop/tauri.bundle.json` (the installer),
`web/package.json`, and the `Version:` line of `README.md`. Each version gets a
`## <version> — <date>` section in `CHANGELOG.md` with that commit's changes.
