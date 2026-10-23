# Libraries: relative paths and relocatable collections — plan

Status: **phases 1–3 done in 2026.43.7** (2026-10-23); sync (phases 4–5) follows. As built:
the relocation check and move are one job (`relocate_library`, `apply: false` to check);
absolute paths are computed in SQL by the `path_join` / `posix_join` functions; the AI
tagger is given `library name/relative path`; sidecar patterns belong to the library whose
root the sidecar's folder is.

## Goal

Today every model stores the absolute path of its archive (`models.archive_path`), and a
crawled folder is just a remembered path. Moving the archives to another disk, mounting them
elsewhere, or opening the library on another machine (Windows → Linux) breaks every path, and
the only repair is a full re-crawl.

A **library** is a named, relocatable collection: a root folder plus a permanent identity.
Models store their path **relative to their library** in a portable form, so:

- moving or re-mounting a library's folder is one change of its root — no re-crawl, no lost
  edits, tags, thumbnails or embeddings;
- a library database created on Windows opens on Linux (and back) after setting each root;
- later, two instances can recognise "the same library" by its identity and sync it.

## Decisions

| Question | Decision |
|---|---|
| Keep the old PyQt app able to read the database? | **No.** Paths become relative only; the migration is one-way. An automatic backup is taken first. |
| Libraries inside libraries | **Allowed physically, owned exclusively:** an archive belongs to the deepest library root above it; crawling the outer library skips inner roots. |
| Sync | **Later.** This round: relative paths, relocation, cross-OS moves (phases 1–3). |

Unchanged: `thumbnails.lmdb` (keys are image ids), `settings.json`, `METADATA.7z` sidecars
(they store archive *names*, already portable), `archive_hash`, perceptual hashes, embeddings.

## Data model

### `libraries` (replaces `crawled_folders`)

| Column | Meaning |
|---|---|
| `id` | Local id |
| `uuid` | Permanent identity, the same on every instance (sync, export) |
| `name` | Shown in the interface; defaults to the root's folder name; unique |
| `root` | Where the library is **on this instance** (absolute, native spelling) |
| `pattern` | Path pattern for crawls (as now) |
| `monitored` | Monitored-folder flag (as now) |
| `last_crawled_at`, `archive_count`, `new_count`, `updated_count` | As now |
| `created_at` | |

### `models`

- `archive_path` and `archive_folder` are **replaced** by:
  - `library_id INTEGER NOT NULL` → `libraries.id`
  - `rel_path TEXT NOT NULL` — the archive's portable relative path
  - `rel_folder TEXT NOT NULL` — `rel_path`'s folder (`""` at the root), indexed, for the
    folder tree, sidecars and "everything under this folder"
- `UNIQUE (library_id, rel_path)` replaces `UNIQUE (archive_path)`.

SQLite cannot drop a `UNIQUE` column in place: the migration rebuilds `models` (new table,
copy, drop, rename, indexes), inside one transaction.

### Portable relative paths (`stl_core::relpath::RelPath`)

A validated newtype used everywhere a relative path is stored or compared:

- `/` separators; no leading `/`, no drive, no empty, `.` or `..` segments;
- Unicode **NFC** (macOS stores some names decomposed; without this the same file would look
  different on two systems);
- case kept exactly as on disk; comparisons are exact, except where noted (relocation);
- `RelPath::from_absolute(root, path)` (refuses paths outside the root) and
  `RelPath::resolve(root) -> PathBuf` (native separators) are the only conversions.

Names Windows cannot hold (`\ : * ? " < > |`, trailing dot or space, reserved names such as
`CON`) are allowed on Linux but get a crawl warning, since such a library could not move to
Windows.

## Migration (one-way)

Runs in `storage::migrate` when it finds `crawled_folders` / `archive_path`:

1. **Backup first** — the server writes a normal backup
   (`backups/stlLibrary_backup_<time>_pre-libraries.7z`) before touching the schema, and
   refuses to migrate if it cannot.
2. Each crawled folder becomes a library (`uuid` generated, `name` from its last folder,
   made unique).
3. Each model goes to the **deepest** library root containing its path (Windows paths
   compared case-insensitively, as the crawler does today); `rel_path` from the remainder.
4. Models outside every library (crawled before folders were remembered, or added by the
   old app) are grouped by folder: each group's outermost folder becomes a library marked
   *created by the migration*, so nothing is lost and the user can rename or merge it.
5. `models` is rebuilt; `crawled_folders` is dropped.
6. A migration report (counts per library, orphan libraries created) goes to the log and to
   the first page load (Maintenance shows it once).

The compatibility rules in `CLAUDE.md` and the migration plan change accordingly: the
database is no longer readable by the PyQt app after this version.

## Path resolution in the code

All file access goes through a library's root:

- `stl_core::library::Roots` — `library_id → root` loaded per operation (a handful of rows),
  with `resolve(library_id, rel)`, `locate(abs_path) -> Option<(library_id, RelPath)>`
  (deepest root wins) and `reachable(library_id)`.
- SQL that filtered by an absolute prefix (`archive_path LIKE 'D:\STL\%'`) filters by
  `library_id` and `rel_folder` (`rel_folder = ?` or `rel_folder LIKE ? || '/%'`).
- Places that show a path show `library name / rel_path`; *Copy path* and *Open folder* use
  the resolved absolute path (with `path_map` as now).

Files to change (by path uses): `storage/library.rs`, `views.rs`, `maintenance.rs`,
`crawl.rs`, `edit.rs`, `browser.rs`, `sidecar.rs`, `model_edit.rs`, `duplicates.rs`,
`jobs.rs`, `vectors.rs`, `schema_sql.rs`, `metadata.rs`, `ai_tagging.rs`, `sort_files.rs`,
`monitor.rs`, `crawler.rs`; in the server `api/edit.rs`, `api/models.rs`, `api/views.rs`,
`api/files.rs`, `api/maintenance.rs`, `ai.rs`, `sidecars.rs`, `jobs.rs`, `maint.rs`,
`monitor.rs`; in the web client 12 uses plus the crawled-folders UI.

Behaviour to keep, per area:

- **Crawler** — crawls a *library*: lists archives under its root (skipping inner library
  roots), stores `RelPath`s, matches moved archives by hash **across libraries** (a move
  from one library to another keeps the record, changing its `library_id`).
- **Metadata from paths** — path patterns already match folders below the crawled folder;
  they now read `rel_path` directly.
- **Sidecars** — a folder is `(library, rel_folder)`; the `METADATA.7z` format is unchanged.
- **Sort files** — the target is a library (+ optional sub-folder); moves update
  `library_id` / `rel_path`.
- **AI tagger** — prompts get `library name / rel_path` instead of the absolute path (the
  model sees the meaningful part only).
- **Monitored folders** — per library, as now.
- **Access control** — `roots` in `server.json` still limits where editors may *create* a
  library; relocating needs the same rights.

## Features

### Libraries (replaces Crawled folders)

The Jobs page's *Crawled folders* card becomes **Libraries**: name (editable), root, path
pattern, monitored, archive count, last crawl, and **status**: *ok* or *root not found*.
Actions: crawl, monitor, rename, **Relocate…**, remove (records stay unless chosen
otherwise). The Library browser gets a **Library** filter section and column.

### Relocate

`POST /api/libraries/{id}/relocate {root, dry_run}` (editors; the new root must be allowed
for them) and `stl-server libraries relocate <name|uuid> <new root>` for headless servers:

1. The new root must exist and must not be inside another library (other than an inner
   library being moved along).
2. **Check**: every `rel_path` is looked up under the new root (in batches); the result —
   found / missing / found with another letter case — is shown before anything changes.
3. **Apply**: one transaction sets `root` (and the roots of inner libraries that were under
   the old root). With *fix letter case* chosen, `rel_path`s found only case-insensitively
   (common after moving from Windows to Linux) are corrected to the spelling on disk.
4. Records found missing stay; they are marked missing at the next crawl, as today.

Nothing is re-read: hashes, thumbnails, tags and edits stay.

### Moving to another machine or operating system

Back up on the old machine, restore on the new one (`stl-server restore`), open it: every
library shows *root not found*; relocate each. Tested with a library created with Windows
roots and relocated to Linux paths.

## Phases (this round)

1. **Core** — `RelPath` with tests (Windows/Linux paths, NFC, case, refusal of `..`),
   `Roots`, the new schema and the migration (with the automatic backup) over a fixture
   database made by the current version, including nested folders and orphan models.
2. **Rewrite of path uses** — core storage, crawler, sidecars, sort files, monitor, metadata,
   AI inputs; then the server (API types: models expose `library` and `rel_path` plus the
   resolved `path`; jobs take a library id; routes `GET/POST /api/libraries`,
   `PATCH /api/libraries/{id}`, `POST .../relocate`, `.../monitor`, `.../remove`; the CLI);
   `npm run gen:api`. Every existing test passes with relative paths.
3. **Interface and docs** — the Libraries card, the relocate dialog (check, then apply),
   the Library filter/column, path display, the migration report, the help topics
   (*Libraries*, *Moving a library*), README, user guide, deployment guide, changelog,
   migration plan and `CLAUDE.md` rules.

## Later: sharing and sync

4. **Export / import a library** — a bundle of one library's records, lookups, thumbnails
   and embeddings keyed by `uuid` + `rel_path`; importing merges into the library with the
   same `uuid` (or creates it), matching moved archives by hash.
5. **Two-way sync between instances** — per-model change tracking (`updated_at` plus
   deletion tombstones), a sync endpoint exchanging changes since the last sync per library,
   thumbnails by digest, and a conflict rule (last change wins per field). Identity across
   instances: library `uuid` + `rel_path`, with `archive_hash` for moves.

## Risks

- **One-way migration** — mitigated by the automatic backup and a migration test over real
  database fixtures; the release notes say the old app cannot open the result.
- **Size of the rewrite** — about 170 path uses; the existing test suites (core, server,
  web) are the safety net, plus new tests for nested libraries, cross-library moves and
  relocation.
- **Case-sensitivity** — Windows ignores case, Linux does not; relocation reports and can fix
  case differences, and crawls on Linux compare exactly.
