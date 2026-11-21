# StlArchivist code review — 2026-11-21

A review of the whole repository at `development` (`76efbf6`), done as six parallel reviews:

- `stl-core` storage
- the rest of `stl-core`
- the server's API layer
- the rest of the server
- the desktop shell, the small crates, `tools/`, `docker/` and CI
- the web client

Findings that two reviews reported separately appear once. Findings marked ✔ were checked against
the code a second time.

| Critical | Required | Optional | Nits |
|---:|---:|---:|---:|
| 1 | 14 | 26 | 13 |

## Critical

### 1. A stopped crawl deletes the records of moved archives ✔ (fix in progress)

`rust/stl-core/src/crawler.rs:1204-1206`

When a crawl is stopped, or aborts after 50 upserts fail in a row, it still reconciles missing
records, and manual crawls from the web client never defer deletion. After a folder reorganisation,
a crawl stopped halfway deletes every moved archive it had not reached yet, with its tags, edits,
image order and thumbnails. This breaks the rule that a crawl never treats what it could not see as
gone.

**Fix:** judge missing records only after a complete pass, with a regression test that stops a
crawl after a move.

## Required

### 2. Job requests silently drop misspelled options ✔ (fix in progress)

`rust/stl-server/src/jobs/request.rs:8`

`JobRequest` lacks `deny_unknown_fields`, which CLAUDE.md requires on request types. A typo such as
`keep_mising` is ignored, so a crawl meant to flag missing records deletes them instead.

**Fix:** add the attribute, plus a test that a misspelled option is refused.

### 3. One small GIF can crash the server ✔

`rust/stl-core/src/thumbnail.rs:204`

The animated-GIF path decodes with no size limits. A GIF of a few hundred bytes that declares a
65535×65535 screen asks for about 17 GB, and the process aborts. It crashes again on every crawl
and monitored pass.

**Fix:** call `set_limits(Limits::default())`, cap the dimensions, and fall back to a still
thumbnail.

### 4. Prune Images can delete the chosen cover ✔

`rust/stl-core/src/storage/maintenance.rs:202`

Pruning keeps the lowest image ids and ignores `images.position`, so the cover a user picked can be
pruned.

**Fix:** order by `model_id, IMAGE_ORDER`, and add a test.

### 5. Generate Thumbnails resets the image order ✔

`rust/stl-core/src/storage/jobs.rs:39`, `rust/stl-core/src/storage/crawl.rs:555`

Generate Thumbnails, and a crawl that finds an archive changed, re-create every image row, so the
chosen order and cover go back to the first extracted image.

**Fix:** carry `position` over to the new row with the same filename.

### 6. Thumbnails change before the database commits

`rust/stl-core/src/storage/crawl.rs:560`, `rust/stl-core/src/storage/jobs.rs:44`,
`rust/stl-server/src/archive_tools.rs:140`

Old thumbnails are deleted, and new ones written, inside the open transaction. If a savepoint or
the commit rolls back, the image rows come back without their thumbnails.

**Fix:** return the old references and call `delete_many` after the commit, as `delete_model`
already does.

### 7. Prune Images leaves sidecars stale ✔

`rust/stl-server/src/jobs/upkeep.rs:84`

The prune job never queues sidecars, so `METADATA.7z` keeps listing the deleted images.

**Fix:** return the pruned models' archive paths and call `job.touch_sidecars`.

### 8. Password changes are not throttled ✔

`rust/stl-server/src/api/auth.rs:156-165`

`PUT /api/auth/password` skips `password_checks` and the login throttle. Any signed-in viewer can
flood the server with Argon2 work (about 19 MB each), and a stolen session can guess the current
password without limit.

**Fix:** take the semaphore, and record failures in `lib.logins`.

### 9. Revoked users keep their WebSocket access

`rust/stl-server/src/api/workers.rs:46`, `rust/stl-server/src/workers.rs:434`,
`rust/stl-server/src/api/jobs.rs:160`

The worker and event sockets check the session only when they open. A disabled or demoted user's
compute worker keeps writing tags and vectors into the library, and `/api/events` keeps streaming
to them.

**Fix:** re-check the session and role on the ping timer, or close the sockets when an account
changes.

### 10. A new inner folder loses its exclusions and patterns

`rust/stl-core/src/storage/libraries.rs:374`, `:403`, `:487`

When a library folder is added inside another, its records move to it, but the outer folder's
`excluded_folders` and `folder_patterns` rows below that point do not. Excluded folders get crawled
again, and patterns are lost. `remove` has the same gap the other way round.

**Fix:** move those rows in `adopt` and back in `remove`, with tests for both.

### 11. `.url` members are read with no size limit

`rust/stl-core/src/metadata.rs:311-327`

`read_url_file` decompresses link files whole, whatever their size, which leaves room for a
compression bomb. It also lists the archive again for every model without a URL.

**Fix:** apply `SIZE_LIMIT_META` (1 MB), and pass in the catalog already read.

### 12. A paused AI job can hang the AI status

`rust/stl-server/src/ai.rs:324-366`

`AiRuntime::ensure` holds the `running` lock while `llama-server` starts (up to 240 s) and calls
`is_stopped()`, which blocks while the job is paused. `/api/ai/status` then waits without limit.

**Fix:** use `stop_requested()`, and start the child outside the lock.

### 13. The desktop window freezes while a library starts ✔

`rust/stl-desktop/src/main.rs:634-647`

The app holds the `shell.local` lock for the whole local-server start, up to 30 minutes on a first
conversion. Reloading, switching server or quitting meanwhile freezes the window.

**Fix:** release the lock while `spawn_local` runs, and make `local_state` async.

### 14. Model views stay stale after a rescan

`web/src/api/events.ts:12`

`LIBRARY_KEYS` leaves out `model-files`, `model-viewable`, `model-warnings` and `stats`, so the
Files tab keeps its old list after a Rescan.

**Fix:** add the keys, or nest them under `["model", id]`.

### 15. Upload errors show as JSON parse errors

`web/src/browser/DetailPane.tsx:57-64`, `web/src/tools/FindByImagePage.tsx:12-20`

The hand-written uploads call `resp.json()` before checking `resp.ok`. A plain-text or HTML 413
from axum or a proxy shows as "Unexpected token '<'".

**Fix:** add one `postForm` helper to `src/api/client.ts`, used by both.

## Optional

### Desktop and CI security

- Server pages can pass UNC paths to `open_folder` and the MeshPeek and packer commands, which makes
  Windows send NTLM credentials to any host (`rust/stl-desktop/src/main.rs:722`, `:826`).
- `open_folder` launches `.app` bundles on macOS (`main.rs:727`).
- The worker-components commands don't check the calling page's origin (`main.rs:530-623`).
- The MeshPeek release tag is pasted into `run:` scripts in a job with `contents: write`
  (`.github/workflows/meshpeek.yml:83`, `:117`).
- The pdfium and ONNX Runtime downloads in the Docker build have no checksum
  (`docker/Dockerfile:36-41`).
- A failed components install leaves the worker stopped (`main.rs:560`).
- Child processes don't end with the desktop app on Linux (`rust/stl-proc/src/lib.rs:85`).

### Server

- Writes with no body rely on `SameSite=Strict` alone against CSRF; reject writes whose `Origin`
  doesn't match `Host` (`rust/stl-server/src/auth.rs:39`).
- A worker token is a full 30-day user session; scope it to the worker connection
  (`api/workers.rs:35`).
- Stopped jobs end as Failed instead of Cancelled (`jobs/mod.rs:500`).
- Sort Files can move files and then fail the database update (`jobs/archives.rs:107`).
- The UnRAR download can't be stopped and has no size cap (`components.rs:595`).
- Blocking SQLite and file calls run outside `Library::run` (`api/ai.rs`, `api/settings.rs:143`,
  `api/backups.rs`).
- Move the admin-only job kinds into an exhaustive `JobRequest::required_role` (`api/jobs.rs:42`).

### stl-core

- Rescan keeps `fallback:` fingerprints that the crawler would read again (`rescan.rs:44`).
- One odd ZIP member fails the whole archive (`archive/zip.rs:228`, `:245`).
- Trailing dots and spaces in sorted names aren't trimmed, and Windows strips them
  (`sort_files.rs:70`).
- Images beside an archive are read with no size cap (`crawler.rs:516`).
- Rescan lists each archive two or three times (`rescan.rs:42`, `:50`).
- A reused image id could lose a new thumbnail (medium confidence;
  `storage/schema_sql.rs:265`).
- The core puts filter keys into SQL as given; only the server validates them
  (`storage/queries.rs:357`).

### Web client

- Two quick image drags can undo each other; update the cache optimistically
  (`browser/DetailPane.tsx:143`).
- An error from the ids query wipes the selection (`browser/BrowserPage.tsx:174`).
- Split `BrowserPage.tsx` (about 425 lines) and `SettingsPage.tsx` (682 lines).
- Add `eslint-plugin-react-hooks` to the checks.

## Nits

- `LIKE` filters don't escape `_` and `%` (`storage/queries.rs:347`).
- `include_folder` matches the exact spelling, while `exclude_folder` ignores case
  (`storage/libraries.rs:131`).
- Several query-parameter structs lack `deny_unknown_fields`.
- Creating two users with the same name at once gives a 500 instead of a 409
  (`api/users.rs:38`).
- Delete Files takes no stop control (`archive_tools.rs:195`).
- `sort_files`: `dedup` only removes neighbouring duplicates, and a failed remove leaves a copy
  behind (`sort_files.rs:316`, `:255`).
- A name requested twice in one `view` batch comes back Missing (`view.rs:96`).
- The update dialog shows the version string the page sent, not the normalised one
  (`stl-desktop/src/main.rs:893`).
- A misplaced doc comment on `LOCAL_START_TIMEOUT` (`main.rs:56`).
- Two different web-link helpers: `webLink` and `webHref` (`browser/detail.ts:55`, `cells.ts`).

## Done well

- Every API handler checks the right role (all 24 modules).
- Argon2id passwords, unknown names handled in constant time, and session tokens stored only as
  SHA-256.
- Downloads are pinned by SHA-256 or signer, and zip/tar unpacking refuses path traversal.
- Sync deltas are checked against the sender's fingerprints, and tokens never reach clients.
- No SQL injection or XSS paths found.

## Not covered in depth

- `ai_tagging`, `clip`, `dhash`, `avif` and RAR header parsing.
- The facet half of `queries.rs`, `dupes.rs` and `library_settings.rs`.
- The Lookups and Duplicates pages and the settings cards in the web client.
- How Tauri checks IPC calls made from iframes.
