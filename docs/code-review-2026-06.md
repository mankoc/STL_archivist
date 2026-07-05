# Code Quality Review — June 2026

A focused improvement pass over the `AI-tagging` branch covering correctness,
dead code, documentation, performance, and code organization. The goal was to
improve the codebase **without destabilizing a working app**: low-risk changes
first, large structural refactors explicitly deferred.

Result: **265 tests passing, 0 failures** (was 235 passing / 3 failing before
the pass). Delivered as 6 commits, one per phase plus one follow-up bug fix.

## Commits

| Commit | Summary |
|--------|---------|
| `59554d4` | fix: harden error handling in AI server, runtime, and crawler |
| `de59f0e` | chore: remove dead code, unused imports, and scratch directories |
| `a1d3d71` | docs: add missing docstrings and clarifying comments |
| `fea95ca` | perf: trim hot-loop overhead in crawler, rules, and db backfill |
| `3b8bcc6` | refactor: extract pure AI tag/prompt logic and add unit tests |
| `b1347e5` | fix: METADATA.7z export found no records on Windows |

---

## Phase 1 — Bugs & correctness

- **`ai_server.py`** — `chat()` previously did `resp["choices"][0]["message"]["content"]`
  with no validation, so any malformed/error/empty response crashed the caller
  with a low-level `KeyError`/`IndexError`/`JSONDecodeError`. Extracted a tested
  `_parse_chat_response()` that validates the structure and raises a clear
  `RuntimeError`. `_wait_ready()` now records the last underlying error and
  includes it in the timeout message (it previously swallowed everything).
  `stop()` now `wait()`s after `kill()` so a force-killed server can't linger as
  a zombie.
- **`ai_runtime.py`** — `_download()` now removes its partial `.part` file on any
  failure or cancel instead of orphaning it.
- **`crawler.py`** — the thumbnail fallback path used an unguarded
  `contents[img.filename]`; now uses `.get()` and skips a catalogued-but-unextracted
  image with a warning.
- **Silent-failure logging** — added log lines to previously-silent `except`
  blocks: LMDB thumbnail batch writes (`thumbnailer.py`) and invalid `info.json`
  parsing (`metadata.py`). Control flow unchanged.

## Phase 2 — Dead code & cleanup

- Removed six confirmed-unused imports (`Qt`, `STLModel` ×2, `QGroupBox`,
  `QSplitter`, `Qt`) across UI dialogs.
- Removed the untracked-experiment directories `delme/` and `TMP/`.
- The unused `_SevenZReader._path` attribute was removed in the Phase 3 commit
  (its line was physically interleaved with the new class docstrings).

## Phase 3 — Documentation

- Module docstring for `database/models.py`.
- Class docstrings for the three archive readers (`_ZipReader`, `_SevenZReader`,
  `_RarReader`).
- Backend-cascade docstring for `sound._play_file`; docstring for
  `background_jobs.active_job`.

## Phase 4 — Performance & memory

- **`rules.detect_warnings`** — builds `Path` once per archive member instead of
  twice, and reuses the module-level compiled `SUPPORTED_STL_RE` instead of
  recompiling the default patterns on every call.
- **`crawler.py`** — dropped redundant per-iteration `image_embedding` imports
  (the module is already imported once where the embedding batch is built).
- **`db.backfill_archive_folder`** — groups ids by computed folder so each
  distinct folder needs one bulk `UPDATE` instead of one statement per row.

## Phase 5 — AI logic extraction + tests

- Moved the Qt/DB-free tag-cleanup and prompt-building helpers out of the
  1,300-line `ai_tagger_dialog.py` into a new **`app/utils/ai_tagging.py`**
  module (dialog shrank ~150 lines), and consolidated the tag-cleanup pipeline
  into `clean_tags()`.
- Added **`tests/test_ai_tagging.py`** (dedupe/atomize/cumulative-split, the full
  pipeline, prompt builders) and **`tests/test_ai_server.py`**
  (`_parse_chat_response` against valid, malformed, and empty payloads) — 27 new
  tests.

## Follow-up — METADATA.7z Windows bug

`export_folder_metadata` filtered `archive_path` with a hardcoded forward-slash
LIKE pattern (`str(folder) + "/%"`), but `archive_path` is stored with OS-native
separators. **On Windows the two never matched**, so the sidecar export silently
wrote nothing and the "fast restore without re-crawling" feature was broken.
Fixed to use `os.sep`; the exact `Path.parent` check remains authoritative. This
was the cause of the 3 pre-existing `test_folder_metadata` failures.

---

## Findings corrected from the initial sweep

The exploration sweep that seeded the plan produced a few inaccurate claims that
were verified and **rejected** before any edit:

- **"Redundant `shutil.rmtree` in `thumbnailer.compact_store`"** — the two calls
  serve different purposes (one clears a pre-existing temp dir before the copy;
  the other cleans up the leftover dir after the data file is swapped out). Not a
  bug; left untouched.
- **"`DEFAULT_SUPPORTS_PATTERNS` is dead/single-use"** — it is imported by
  `test_rules.py` and used as the default in `detect_warnings`. Kept.

## Follow-up pass — deferred items

A later pass tackled two of the three deferred items (the BrowserTab split is
still held back). Commits `a5de980` and `20a0aa8`.

### GUI-thread I/O (done)

- **`browser_tab._handle_image_drop`** — file reads and thumbnail encoding now
  run in a background `_ImageDropRunnable`; the DB inserts and LMDB writes
  marshal back to the GUI thread (`_on_image_drop_ready`). This keeps writes on
  the single existing writer — no new SQLite contention — while the UI no longer
  freezes when many/large files are dropped.
- **`editor_dialog._populate_thumbnails`** — reads a model's thumbnails in a
  single LMDB transaction (`get_thumbnail_bytes_many`) instead of one round-trip
  per image. The model-field load stays synchronous: it is a bounded, single-row
  read on an explicit user action, so full async population of a short-lived
  modal dialog would be over-engineering.

### Editor-dialog dedup (done, scoped)

The duplicated Levenshtein/Soundex merge-scan logic across the
Category/Collection/Subcollection editors moved into a pure, unit-tested
**`app/utils/name_dedup.py`** (`find_similar_pairs` / `find_similar_pairs_grouped`).

A full widget base class was **deliberately not built**: the per-dialog strings
are fully translated in `resources/i18n/es.ts`, and collapsing them behind
dynamic labels (`f"{label} Editor"`) would stop `pylupdate6` extracting them and
regress those translations to English. Sharing only the string-free algorithm
captures the real duplication with zero i18n impact and adds test coverage.

While migrating, **`app/ui/subcategory_editor_dialog.py` was found to be dead,
broken code** — it imported a `Subcategory` model that no longer exists (replaced
by `Subcollection`), so it could not even be imported, and nothing referenced it.
Deleted.

### BrowserTab file split (done)

`app/ui/browser_tab.py` was reduced from **~4,361 to ~2,540 lines** by moving its
~17 independent helper classes into six focused sibling modules — **leaf classes
only**; the `BrowserTab` class itself was not touched (no methods moved, no
shared-state plumbing), so the change is pure cut-paste + imports:

- `browser_common.py` — shared column constants, `_HEADERS`, `_pack_row`,
  `_FILTER_PATH_IDS`, `_fmt_size`, `_prune_ancestor_paths`.
- `browser_dialogs.py` — `ClickableLabel`, `ImageViewerDialog`.
- `browser_delegates.py` — the cell-editor delegates + `_TagLineEdit`.
- `browser_table_model.py` — `_IdLoaderThread`, `_PageFetchRunnable`, `_SqlTableModel`.
- `browser_filter_tree.py` — `FilterItemDelegate`, `FilterTreeWidget`, the
  ~600-line `_FilterTreeWorker`.
- `browser_jobs.py` — `RescanWorker`, `RescanProgressDialog`,
  `ThumbnailGeneratorWorker`, `_ImageDropRunnable`.

Done one module per commit, each verified (import graph + 273-test suite) before
the next. None of the new modules import `browser_tab` (no circular-import risk).
A final pass removed ~33 now-unused imports from `browser_tab.py` (proven unused
by AST + grep). Added `tests/test_ui_module_imports.py` as a cheap import guard,
and a headless (offscreen) smoke test confirmed `BrowserTab` still constructs and
`refresh()` runs.

### Still deferred

- **Extracting `BrowserTab`'s own method clusters** (thumbnail pane, filter-tree
  management, editing) into controller objects — touches shared state
  (`_current_model_id`, `_model`, `_table`); the higher-risk follow-up.

## Verification

- `python -m pytest tests/ -v` → 282 passed, 0 failed.
- Import smoke check across `main_window`, `crawler`, `db`, and the AI/util
  modules.
