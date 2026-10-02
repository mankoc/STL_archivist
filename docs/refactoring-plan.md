# Refactoring Plan: modularity, speed, maintainability

**Status:** in progress.

| Item | State |
|---|---|
| P1 settings cache, P2 grouped counts, P3 background edits + sidecar coalescing, P4 bulk prune / verify | ✅ done |
| M1 single filter model (`services/library_query.py`) | ✅ done: characterised against the old table query (36 specs, identical rows) |
| M4 get-or-create via helpers (`services/editing.py`) | ✅ done: browser cells, model editor, AI tagger |
| M6 `utils/image_hash.py` | ✅ done |
| M5 workers | ◑ partial: all 13 `finished`-shadowing workers fixed, with an app-wide guard test; Verify / Find Duplicate Entries / Prune Images moved to `services/maintenance.py`. The other workers still hold their logic. |
| M2, M3, M7, M8, M9 | ☐ not started |

**Found while refactoring (behaviour kept, not yet fixed):** filter *exclusions* use SQL `NOT (col IN …)` / `NOT (col LIKE …)`, which is NULL for rows where the column is NULL. Excluding a creator, source, sculptor, collection, category, release, presupports value or warning therefore also hides every model with no value in that field. For example, excluding one warning hides all models with no warnings. The fix is a one-line `or_(col.is_(None), …)` per exclusion in `library_query.conditions`, pending a decision.

**Related:** [server-migration-plan.md](server-migration-plan.md). Most structural items below are the same work as that plan's Phase 0, so doing them serves both goals.

**Ground rules for every item**
- **No behaviour change**, unless the item says so explicitly.
- **One item per PR / commit**, with the test suite green after each.
- **Write tests before moving logic out of a widget.** Characterisation tests pin down today's results first.

---

## Summary

The core (crawler, archive I/O, rules, ORM, thumbnail store) is in good shape. The problems are concentrated in the UI layer:

| # | Finding | Kind | Impact | Effort |
|---|---|---|---|---|
| P1 | `settings.json` is read and parsed on **every** getter call, including inside paint code | Speed | High (UI smoothness) | S |
| P2 | Lookup editors count usage with `len(x.models)`: N+1 queries that load full model rows, on the GUI thread | Speed | High on large libraries | S |
| P3 | Inline cell edits write to the DB **and rebuild the folder's `METADATA.7z`** on the GUI thread, once per edited cell | Speed | Medium–high | M |
| M1 | Filter predicates are implemented **twice** (table query vs facet counts), and the two already differ | Correctness / modularity | High | M |
| M2 | `BrowserTab` is a ~2,750-line class with about eight responsibilities | Modularity | High | L |
| M3 | Six lookup editors plus five "merge similar" dialogs are near-duplicates (~1,900 lines) | Duplication | Medium | M |
| M4 | Get-or-create is re-implemented **29×** in the UI, although `database/helpers.py` already provides it | Duplication | Medium | S |
| M5 | ~35 hand-written `QThread` workers repeat the same progress, log, stop and finished boilerplate | Duplication | Medium | M |
| M6 | dHash helpers live in a dialog and the crawler imports them from the UI | Layering | Low | S |
| M7 | `config_dialog.py` (~1,900 lines) mixes setting getters, a theme engine and 12 dialog pages | Modularity | Medium | M |
| M8 | `find_content_duplicates_dialog.py` (~1,700 lines) puts its algorithms inside worker `run()` bodies | Modularity | Medium | M |
| M9 | The crawler is a `QThread`, and its phase methods take 10–12 parameters | Modularity | Low–medium | M |
| P4 | Small DB inefficiencies: prune loads rows only to delete them, and Verify opens a session per file | Speed | Low | S |

---

## Speed

### P1: Cache library settings (quick win)
**Evidence**
- `app/core/settings.py::_load_library_settings` does `Path.exists()`, `read_text()` and `json.loads()` on every call, and all ~70 getters call it.
- `browser_filter_tree.py:71-95` calls `get_status_*_color()` inside `_draw_indicator`, which runs for **every visible checkbox on every repaint** (scroll, hover, expand).
- `config_dialog._get_ui_color` can also **read the theme `.qss` file and run a regex** on each call.

**Change**
- Cache the parsed dict in `_load_library_settings`, keyed by `(path, st_mtime_ns)`. A cache hit costs one `stat()`, and `_save_library_settings` refreshes the cache.
- Return a copy, or document the dict as read-only. Some callers mutate it before saving.
- Memoise resolved UI colors in `config_dialog` and clear them in `apply_color_scheme()` and after Configuration saves.
- Optionally, have the filter-tree delegate fetch its three colors once per paint pass, or once per theme change.

**Verify:** add unit tests (cache hit, external edit invalidates, save refreshes), and check that a theme change in Configuration still recolors the indicators.

### P2: Usage counts via one aggregate query (quick win)
**Evidence:** `len(cat.models)` / `len(tag.models)` / `len(sc.models)` appears in `category_editor_dialog.py:79,147,239`, `collection_editor_dialog.py:79,144,234`, `subcollection_editor_dialog.py:86,199,297`, `scale_editor_dialog.py:69,127` and `tag_editor_dialog.py:103,196`. Each call lazy-loads **every `STLModel` row** of that lookup. On a large library, opening the Tag Editor runs one query per tag and materialises most of the models table.

**Change**
- Add `usage_counts(session, kind) -> dict[id, int]` to a new `app/services/lookups.py`. It is a single `GROUP BY` over the FK column, or over `model_tags` / `model_scales`.
- Single-row deletes use `select(func.count())`.

**Verify:** compare the counts with today's output on a test DB, and time the Tag Editor opening before and after.

### P3: Take inline cell edits off the GUI thread and coalesce sidecar export
**Evidence:** `browser_tab.py:1017-1136` (`_write_cell`) runs in the GUI thread. It does the get-or-create writes and then calls `export_folder_metadata()`, which rewrites the whole folder's `METADATA.7z`, optionally with thumbnails. It does this **for each edited cell**, so pasting into 10 cells of one folder rebuilds that sidecar 10 times.

**Change**
- Move the edit logic to `app/services/editing.py::apply_cell_edit(session, model_id, field, value)`. It uses the `helpers.get_or_create_*` functions (see M4).
- Run the debounced batch in a `QRunnable`.
- Collect the affected folders and export each sidecar **once** per flush, in the background.

**Verify:** add unit tests for `apply_cell_edit` per field (tags lowercased, scales split, subcollection scoped to its collection, empty value clears). Manual check: edit several cells and confirm the UI never stalls and the sidecar is updated.

### P4: Small DB inefficiencies
- **`helpers.prune_unused_lookups`** loads orphan rows and deletes them one by one. Replace with bulk `DELETE … WHERE id NOT IN (subquery)` and use the rowcounts.
- **`db_maintenance_dialog.py` Verify worker** opens a new `session_scope()` for each reappeared file. Collect the ids and clear the flag in one `UPDATE` at the end.
- **Case-insensitive creator/source filters:** `func.lower(creator).in_(…)` cannot use `ix_models_creator`. Only change this if profiling (`STL_SQL_PROFILING=1`) shows it matters; the fix would be a `COLLATE NOCASE` index.

---

## Modularity and duplication

### M1: One filter model, one predicate builder
**Evidence:** the include/exclude predicates are written twice:
- `browser_tab.py:800-996` (`_load_table`, against `models_view`, with a ~30-keyword signature);
- `browser_tab.py:1357-1436` (`_run_id_subquery`, against `STLModel`, for faceted counts).

They already diverge. For example, path filters use `LIKE` in one and a temp table in the other, and the `path` group key exists only in the table version. Any new filter must be added in both places, and the counts can silently disagree with the rows shown.

**Change** (this is server plan step 0.3):
- `app/services/library_query.py` with a `FilterSpec` dataclass (include/exclude per facet, has/no-value groups, id set, duplicate pairs).
- `predicates(spec, cols, skip=frozenset())` builds the conditions once, for either `models_view` or `STLModel`.
- `build_table_select(spec)` and `facet_scopes(spec)` are built on top.
- `_apply_checked_filters` only *collects* a `FilterSpec` from the tree, and `_load_table(spec)` takes the dataclass.

**Verify:** first write **characterisation tests** that build a small DB, run representative filter combinations through today's code paths and record the id sets. Then swap the implementation and check the results are identical. Add a parity test that the row set equals the facet scope with nothing skipped.

### M2: Split `BrowserTab`
**Responsibilities currently in one class:** layout, the filter tree (populate, check propagation, apply), table loading, inline editing, search history, the info panel, the thumbnail pane (paging, GIF movies, drag-drop, context menu), a ~150-line context menu, header state, and launching about 12 dialogs and jobs.

**Target**
- `FilterPane` (filter tree widget, `_on_filter_data_ready`, check-state logic). It emits `filters_changed(FilterSpec)`.
- `ThumbnailPane` (paging, movies, drop handling, image context menu). It takes `show(model_id)`.
- `ModelInfoPanel` (HTML rendering plus its small cache).
- `BrowserActions` (context-menu construction and the dialog and job launchers).
- What remains in `BrowserTab` is composition and the table.

**Order:** do this after M1, P3 and M4, because those remove most of the logic that would otherwise move twice. Move one pane at a time, with a manual smoke test after each.

### M3: One generic lookup editor
**Evidence:** `category_editor_dialog.py`, `collection_editor_dialog.py`, `subcollection_editor_dialog.py`, `scale_editor_dialog.py`, `tag_editor_dialog.py` and `author_editor_dialog.py` share the same `_load / _selected_id / _add / _rename / _delete / _merge_*` structure. Five of them carry their own `_Merge…Dialog` with the same scan and merge table.

**Change**
- `app/services/lookups.py`: `list_with_counts`, `add`, `rename` (with a duplicate check), `delete`, `merge(src_ids, dst_id)` and `find_similar(kind, method, threshold)`. These reuse `utils/name_dedup.py` / `string_utils.py`, and the scan runs in a worker.
- `app/ui/lookup_editor_dialog.py`: a `LookupEditorDialog(spec)`, where `LookupSpec` declares the kind, labels and capabilities. Subcollection gets a parent-collection column, and Author uses a string key instead of an id.
- One shared `MergeSimilarDialog`.
- The Tag editor keeps its extra methods (token fuzzy, rapidfuzz) through the spec.
- Invalidate `lookup_cache` in one place after each write (today it depends on each dialog remembering to).

**Verify:** add service unit tests (rename collision, merge moves the FK/m2m rows, delete nulls the FKs), then run a manual pass through each editor.

### M4: Use the existing get-or-create helpers
**Evidence:** 29 inline `session.query(X).filter_by(name=…).first()` + `add` + `flush` blocks across 9 UI files, such as `browser_tab.py` (8), `editor_dialog.py` (5) and `author_editor_dialog.py` (5). `database/helpers.py` already has `get_or_create_*`, with tag normalisation and an optional batch cache.

**Note:** the inline copies drift. In `_write_cell` the subcollection lookup falls back to *any* subcollection with that name when the model has no collection, whereas the helper always scopes by `collection_id`. Decide the intended behaviour when consolidating, and add a test for it.

### M5: Generic background-job wrapper
**Evidence:** about 35 `QThread` / `QRunnable` classes repeat the same pieces: `progress`, `log`, a `*_finished` signal, `stop()` with `_stop_requested`, and `session_scope` handling. The recent fix for `finished` shadowing `QThread.finished` had to be applied to nine workers one at a time.

**Change**
- `app/ui/job_thread.py`: a `JobThread(fn, *args)` with fixed signals `progress(int, int)`, `log(str)`, `result(object)` and `failed(str)`. It calls `fn(*args, progress=…, log=…, should_stop=…)`.
- It optionally takes a `background_jobs` name, so exclusive jobs are registered and released automatically.
- The worker bodies move to plain functions in `app/services/*` (server plan step 0.4), and each dialog does `JobThread(maintenance.verify_archives)`.
- Migrate worker by worker, starting with `db_maintenance_dialog.py`, the largest cluster.

**Verify:** each service function gets a plain pytest test, with no Qt event loop needed. The existing `test_maintenance_worker_signals.py` should keep passing against the wrappers.

### M6: Move the image-hash helpers out of the UI
Move `_dhash`, `compute_dhash_from_jpeg`, `format_dhash`, `parse_dhash`, `_hamming` and `_hamming128_batch` from `find_content_duplicates_dialog.py` to `app/utils/image_hash.py`. Keep re-exports in the dialog. `crawler.py:44` then no longer imports the UI. (Server plan 0.2.)

### M7: Split `config_dialog.py`
- **`app/ui/theme.py`:** `THEMES`, QSS token parsing, `_get_ui_color` with the P1 memoisation, `apply_color_scheme`, the user-override CSS and `get_pair_colors`.
- **`app/ui/ui_settings.py`:** the remaining visual and widget-only getters (fonts, hotkeys, filter-pane options, notifications, file manager, `open_folder_with_file_manager`).
- **`app/ui/config_pages/`:** one module per page (database, crawler, warnings, sidecar, debug, hotkeys, browser, AI, file writing, language, appearance, notifications). Each page gets a small `load(settings)` / `save(settings)` interface, so `ConfigDialog` just iterates over pages.
- **`config_dialog.py`** keeps re-exports for compatibility, which can be removed gradually.

### M8: Separate algorithms from the duplicates dialog
- Move the `_ContentDupWorker`, `_ImageDupWorker` (~270-line `run`) and `_EmbeddingDupWorker` logic into `app/services/duplicates.py` as pure functions that return pairs and groups. Run them through `JobThread` (M5).
- Move `_ThumbStrip`, `_ThumbnailPopup` and `_ImgHoverFilter` to `app/ui/widgets/thumb_strip.py`, so the image grid and archive browser can reuse them.

### M9: Decouple the crawler from Qt and slim its signatures
- **Server plan 0.2:** a `Crawler(config, sink, cancel_event)` in `app/crawler/pipeline.py`, with `CrawlerWorker(QThread)` as a thin adapter.
- **Signatures:** replace the 10–12 parameter phase methods (`_phase_upsert`, `_upsert_archive`) with a `BatchResult` dataclass that carries `hashes`, `existing`, `contents`, `all_names`, `stl_info`, `uncompressed` and `precomp` for one batch.

---

## Maintainability (cross-cutting)

- **Deferred imports.** There are dozens of `from app.ui.config_dialog import …` imports inside functions. These were a workaround for `ui → crawler → ui` cycles. Now that the settings are in `app.core.settings`, business-logic modules can import it at module level, and the circular-import section of `CLAUDE.md` can be narrowed accordingly.
- **Layer rule, enforced by a test.** `app.core`, `app.crawler`, `app.database`, `app.services` and the non-Qt `app.utils` must import without PyQt6. A guard test (a subprocess with `PyQt6` blocked, like `test_core_settings.py`) keeps M5, M6, M8 and M9 honest.
- **Test coverage follows extraction.** Logic that lives in widgets is untested today (per CLAUDE.md, "UI is not covered"). Each extraction above adds plain pytest coverage for code that has none now.
- **Hard-coded colors.** `db_maintenance_dialog.py` mixes theme colors with literal `QColor(…)` values. Route them through `theme.py` during M7.

---

## Recommended order

| Step | Items | Why this order |
|---|---|---|
| 1. Quick wins | **P1, P2, M4, P4** | Small, low risk, immediately noticeable on large libraries |
| 2. Query core | **M1** (with characterisation tests) | Fixes the dual-implementation risk. Prerequisite for M2 and for the server |
| 3. Edit path | **P3** | Builds on M4. Removes the worst GUI-thread I/O |
| 4. Jobs | **M6, M5** (maintenance first, then the rest), **M8** | Turns about 35 workers into testable services and a single wrapper |
| 5. Lookups | **M3** | Uses the services from step 4 plus the P2 counts |
| 6. Big splits | **M7**, then **M2** | Easiest once the logic has moved out |
| 7. Crawler | **M9** | Self-contained, and also completes server plan 0.2 |

Steps 2, 4 and 7 complete most of the server plan's Phase 0.

## Measuring speed

- Turn on `STL_SQL_PROFILING=1` (or Configuration → Debug) and record the slowest statements before and after steps 1–3.
- Time three user-visible operations before and after each speed item, on the real library: opening the Tag Editor, scrolling the filter pane, and applying a multi-facet filter.
