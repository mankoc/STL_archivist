# UI Responsiveness & DB Access — Improvement Backlog

This document enumerates concrete, **backwards-compatible** changes to improve perceived UI
responsiveness in `stlLibrary`. Items are grouped by severity and end with a suggested
rollout order. No on-disk formats, public APIs, or signal payloads change; only emission
frequency, internal call sites, and new additive helpers.

Each item uses this template:

> **File(s):** path:lines
> **Symptom:** what the user perceives
> **Root cause:** what the code does today
> **Expected improvement:** measurable expectation
> **Implementation steps:** numbered
> **Backwards compatibility:** what is preserved
> **Verification:** how to confirm

---

## Section 1 — High Severity (UI-blocking DB / LMDB)

### 1. Delegate combos query the DB on every cell edit  [Severity: HIGH]

**File(s):** [app/ui/browser_tab.py:357-402](app/ui/browser_tab.py#L357-L402)
(`SourceDelegate`, `SculptorDelegate`, `CollectionDelegate`, `SubcollectionDelegate`, plus the
Release/Scale delegates nearby).

**Symptom:** clicking into a sculptor / source / release / collection cell freezes the table
for 100–500 ms while the combo populates. The freeze recurs on every edit because nothing is
cached.

**Root cause:** each `_get_items()` opens a session and runs a `SELECT DISTINCT … ORDER BY …`
on the UI thread. There is no memoization, so editing 5 cells in a row runs 5 full queries.

**Expected improvement:** first edit unchanged; subsequent edits drop from ~200 ms to <5 ms.

**Implementation steps:**
1. Add `app/ui/lookup_cache.py` exposing a process-wide singleton:
   ```
   class LookupCache:
       def get(self, kind: str) -> list[str]: ...
       def invalidate(self, kind: str | None = None) -> None: ...
   ```
   `kind` keys: `"source"`, `"sculptor"`, `"collection"`, `"subcollection"`, `"release"`,
   `"scale"`. Values are cached `list[str]` sorted ascending.
2. Rewrite each delegate's `_get_items()` to `return LOOKUP_CACHE.get(<kind>)`.
3. Invalidate the relevant kind(s) wherever those tables are written:
   - `mass_editor_dialog.py` after Apply.
   - `edit_dialog.py` after Save.
   - `main_window.py` / `browser_tab.py` upon `CrawlerWorker.counts_changed` (broad invalidate
     of all kinds).
   - `delete_dialog.py` and any place that prunes orphan rows.
4. Make `LookupCache.get()` thread-safe (a single `QMutex`) so future workers can also call it.

**Backwards compatibility:** delegate classes keep current signatures; cache is purely
additive. Cache cold-load behaves identically to today.

**Verification:** open the browser tab on a library with 1k+ sculptors. Tap-into 10 sculptor
cells consecutively; only the first should produce a measurable delay. Run the existing
pytest suite — no behavior change in tests.

---

### 2. Thumbnail pane loads QPixmaps on the UI thread  [Severity: HIGH]

**File(s):** [app/ui/browser_tab.py:3375-3410](app/ui/browser_tab.py#L3375-L3410)
(`_render_thumb_page`), [app/utils/thumbnailer.py:184-206](app/utils/thumbnailer.py#L184-L206)
(`pixmap_from_path`).

**Symptom:** selecting an image-heavy model (20+ thumbnails) stalls the UI for 50–200 ms;
quickly tabbing between models feels sticky.

**Root cause:** `_render_thumb_page` loops over images and calls `pixmap_from_path()`
synchronously. Each call does an LMDB read + `QPixmap.loadFromData()` on the GUI thread.
There is no pixmap cache, so page flips reload everything.

**Expected improvement:** model switches feel instantaneous (placeholder visible <16 ms);
already-seen thumbnails reappear with zero latency.

**Implementation steps:**
1. Add `get_thumbnail_bytes_many(ids: Iterable[int]) -> dict[int, bytes]` to
   `app/utils/thumbnailer.py`. One LMDB read txn + cursor; existing `get_thumbnail_bytes` keeps
   its signature.
2. Create `app/ui/thumb_loader.py`:
   - `ThumbnailLoaderWorker(QObject)` running on a dedicated `QThread`.
   - Slot `request(model_id: int, image_ids: list[int])` — cancels prior request, fetches
     bytes via the batch helper, decodes to `QPixmap`, emits `pixmap_ready(model_id,
     image_id, QPixmap)`.
3. In `BrowserTab.__init__`, instantiate the worker + thread once; tear down in `closeEvent`.
4. In `_render_thumb_page`:
   - For each image, look up `QPixmapCache` (key = `f"thumb:{image_id}"`); if hit, use it
     immediately; else insert a placeholder pixmap and enqueue a request.
   - Drop the synchronous `pixmap_from_path` call entirely.
5. Add a small bounded `QPixmapCache.setCacheLimit(64 * 1024)` (KB) on app start.
6. On `pixmap_ready`, find the matching slot (skip if the model selection changed) and call
   `QLabel.setPixmap`.

**Backwards compatibility:** `pixmap_from_path` and `get_thumbnail_bytes` unchanged. New
helper and worker are additive.

**Verification:** select a model with 50 thumbnails; the UI should not stutter. Switch
between two large models 10× — second switch is instant from cache. Confirm thumbnails for
non-current models do not leak into the current view (selection guard).

---

### 3. `_update_model_info_panel` issues 2 sequential queries per row click  [Severity: HIGH]

**File(s):** [app/ui/browser_tab.py:2658-2669](app/ui/browser_tab.py#L2658-L2669).

**Symptom:** arrow-keying through rows feels laggy; each selection round-trips to SQLite
twice (model + creator profile).

**Root cause:** the panel fetches `STLModel` and `CreatorModel` on demand, independently per
selection event.

**Expected improvement:** row-change becomes purely attribute reads (microseconds).

**Implementation steps:**
1. Extend the existing table-loader query (`TableLoaderWorker`) to also select
   `STLModel.url`, `STLModel.description`, and the creator's profile fields
   (`CreatorModel.url`, `CreatorModel.notes`, …) via an outer join. Store them in the row's
   `UserRole` payload (or a parallel `self._row_extra: dict[model_id, dict]`).
2. Rewrite `_update_model_info_panel` to read from that cache; remove both queries.
3. Invalidate the row payload when `edit_dialog` saves a single model (refresh that row only).

**Backwards compatibility:** the existing panel update signature is unchanged; the change is
internal to `BrowserTab`.

**Verification:** arrow-key through 100 rows quickly — panel must update at frame rate.
Profile with `cProfile` or `QElapsedTimer`: panel update <1 ms.

---

## Section 2 — Medium Severity (query batching & data shape)

### 4. N+1 on `stl.images` access for thumbnail pane  [Severity: MED]

**File(s):** [app/ui/browser_tab.py:3375](app/ui/browser_tab.py#L3375).

**Symptom:** selecting a model with many images fetches more data than needed.

**Root cause:** `list(stl.images)` hydrates full ORM `Image` rows even though only `id` and
`thumbnail_path` are used downstream.

**Expected improvement:** 30–60 % less data per selection; smaller working set in SQLite
page cache.

**Implementation steps:**
1. Replace `self._thumb_all_images = list(stl.images)` with
   ```python
   self._thumb_all_images = (
       session.query(Image.id, Image.thumbnail_path)
              .filter(Image.model_id == stl.id)
              .order_by(Image.id)
              .all()
   )
   ```
2. Update downstream consumers (`_render_thumb_page`, drag-drop, deletion) to unpack tuples
   `(image_id, thumbnail_path)` instead of attribute access. If the existing code touches
   other attributes (filename, etc.), add them to the projection.

**Backwards compatibility:** internal to `BrowserTab`; ORM model untouched.

**Verification:** SQL profiler (the existing `log_statement_timings` config flag) shows one
narrow `SELECT id, thumbnail_path …` per selection instead of `SELECT *`.

---

### 5. Filter-tree count queries are serial (8–10 separate GROUP BYs)  [Severity: MED]

**File(s):** [app/ui/browser_tab.py:1361-1439](app/ui/browser_tab.py#L1361-L1439)
(`_populate_filter_tree`, `_count_col`).

**Symptom:** changing a filter (or hitting Refresh) stalls the UI for 0.5–2 s on libraries
with 50 k+ models.

**Root cause:** the function issues ~10 sequential GROUP BY queries on the UI thread and
then rebuilds the tree synchronously.

**Expected improvement:** the UI stays responsive throughout the rebuild; total wall time
drops by ~30 % from query batching.

**Implementation steps:**
1. Extract the data-fetching part of `_populate_filter_tree` into
   `_collect_filter_tree_data(filters) -> dict[col_name, list[(value, count)]]`.
2. Wrap it in a `QRunnable` posted to `QThreadPool.globalInstance()`. Define a tiny
   `QObject` with a signal `filter_tree_ready(dict)` that the runnable emits.
3. Move the *tree-build* code (purely Qt widget mutation) into a slot on `filter_tree_ready`
   so it runs back on the GUI thread.
4. Show a subtle "Counting…" indicator (existing busy spinner) while the runnable executes.
5. (Optional second pass) Fold the per-column queries into a single `UNION ALL` with a
   discriminator column so SQLite scans the table once:
   ```sql
   SELECT 'creator' AS col, creator AS val, COUNT(*) FROM stl_model GROUP BY creator
   UNION ALL
   SELECT 'source',  source,  COUNT(*) FROM stl_model GROUP BY source
   UNION ALL ...
   ```
   then partition the rows in Python.

**Backwards compatibility:** filter semantics, tree layout, and persisted filter state
unchanged.

**Verification:** apply/clear a filter on a large library — interaction with other widgets
(scroll, type in search box) remains smooth. SQL log shows one query (post-step 5) instead
of 10.

---

### 6. Path parsing loop in Python over all rows  [Severity: MED]

**File(s):** [app/ui/browser_tab.py:1411-1417](app/ui/browser_tab.py#L1411-L1417).

**Symptom:** part of the filter-tree stall on large libraries is CPU spent in
`FSPath(ap).parent.as_posix()` once per model.

**Root cause:** folder grouping is computed from `archive_path` in Python at query time.

**Expected improvement:** 100–300 ms shaved from filter-tree rebuild on 50 k-model
libraries.

**Implementation steps:**
1. Add an optional column `STLModel.archive_folder: Mapped[Optional[str]]` (default `NULL`).
2. Register it in `_ensure_columns()` in `app/database/db.py` so existing DBs gain the
   column on next open.
3. Populate it: (a) on upsert in `crawler.py` (`model.archive_folder = str(archive_path.parent)`);
   (b) provide a one-shot backfill helper `backfill_archive_folder(session)` called once at
   app start when the column is detected as empty for >0 rows.
4. Add a non-unique index on `(archive_folder)`.
5. In `_populate_filter_tree`, replace the Python loop with a SQL `GROUP BY archive_folder`.
6. Keep the Python fallback for rows where `archive_folder IS NULL` (pre-backfill safety).

**Backwards compatibility:** column is additive, defaults `NULL`; older DBs still open. No
on-disk format change beyond the column itself, which `_ensure_columns` already supports.

**Verification:** open an old DB → column appears, backfill runs once; filter tree rebuild
no longer scales with model count for the folder slice.

---

## Section 3 — Medium Severity (crawler & thumbnail throughput)

### 7. `save_thumbnail()` followed by `get_thumbnail_bytes()` round-trip  [Severity: MED]

**File(s):** [app/crawler/crawler.py:1024-1031](app/crawler/crawler.py#L1024-L1031).

**Symptom:** crawl is slower than necessary; dHash computation re-reads bytes already
written 5 lines earlier.

**Root cause:** `save_thumbnail` returns only the relative path, forcing the caller to
re-fetch JPEG bytes from LMDB just to hash them.

**Expected improvement:** ~10–20 % faster `_update_thumbnails` on image-heavy archives; one
fewer LMDB read txn per image.

**Implementation steps:**
1. In [app/utils/thumbnailer.py](app/utils/thumbnailer.py), add:
   ```python
   def save_thumbnail_returning_bytes(
       image_bytes: bytes, image_id: int, max_size: int = THUMB_SIZE_DEFAULT,
   ) -> Optional[tuple[str, bytes]]:
       ...
   ```
   that returns `(rel_path, jpeg_bytes)`. Re-implement existing `save_thumbnail` as a
   wrapper that drops the bytes.
2. In `crawler.py`, replace the save+re-read with one call; pass `jpeg_bytes` straight to
   `compute_dhash_from_jpeg`.

**Backwards compatibility:** `save_thumbnail` public signature unchanged; new helper is
purely additive.

**Verification:** time `_update_thumbnails` before/after on the same large archive. SQL/LMDB
trace shows one read fewer per thumbnail.

---

### 8. One LMDB transaction per thumbnail write during crawl  [Severity: MED]

**File(s):** [app/utils/thumbnailer.py:121-171](app/utils/thumbnailer.py#L121-L171);
consumer [app/crawler/crawler.py:1015-1048](app/crawler/crawler.py#L1015-L1048).

**Symptom:** crawl throughput is limited by LMDB transaction commit overhead when archives
contain many images.

**Root cause:** each `save_thumbnail` opens a write txn, commits, and closes it.

**Expected improvement:** thumbnail writes ~2–4× faster per archive (LMDB commits dominate
small writes).

**Implementation steps:**
1. Add:
   ```python
   def save_thumbnails_batch(
       items: list[tuple[int, bytes, int]],  # (image_id, raw_bytes, max_size)
   ) -> list[tuple[int, Optional[str], Optional[bytes]]]:
       ...
   ```
   in `thumbnailer.py`. One `_lmdb_env.begin(write=True)` for the whole list; do PIL resizing
   per item, write all keys, commit once.
2. Refactor `_update_thumbnails` to build the items list first (after the
   limit / hash-changed checks), call the batch helper, then loop the result to assign
   `thumbnail_path` and `dhash`. Combine with #7 by returning the JPEG bytes from the batch
   helper.
3. Keep `save_thumbnail` / `write_raw_thumbnail` as-is for single-item callers (sidecar
   import, drag-drop in browser tab).

**Backwards compatibility:** purely additive.

**Verification:** crawl a folder of ~50 archives × 20 images. Wall-clock for
`_update_thumbnails` halves or better. Existing single-item paths (drag-drop into image
pane) still work.

---

### 9. `counts_changed` / `archive_result` signal flood  [Severity: MED]

**File(s):** [app/crawler/crawler.py:416](app/crawler/crawler.py#L416) and surrounding
`_phase_upsert`.

**Symptom:** during crawl the UI repaints per archive, sometimes 50× per batch, making the
progress dialog jittery and stealing CPU from real work.

**Root cause:** signals are emitted inside the inner upsert loop with no coalescing.

**Expected improvement:** smoother progress UI, ~5 % less CPU during crawl on the GUI
process.

**Implementation steps:**
1. In `_phase_upsert`, accumulate counts locally; emit `counts_changed` once at the end of
   the batch (preserving the payload shape).
2. For `archive_result`, keep per-archive emission but add a 50 ms coalescing layer on the
   *consumer* side (`crawler_tab.py`): buffer incoming results in a list and flush on a
   `QTimer.singleShot(50, …)` to update widgets in batches.
3. Optionally add a new `archive_results_batch(list[...])` signal on `CrawlerWorker` for
   future opt-in consumers, but do **not** remove the per-archive signal.

**Backwards compatibility:** existing signals keep names and payload shapes. Slots that
already exist receive fewer / the same events with identical structure.

**Verification:** crawl 200 archives; observe progress dialog updates as smooth scrolling
counts rather than rapid flickers. Profiler shows fewer `processEvents` calls.

---

## Section 4 — Low Severity (polish)

### 10. Logging is synchronous file I/O on hot paths  [Severity: LOW]

**File(s):** [app/utils/logger.py:58](app/utils/logger.py#L58).

**Symptom:** on slow disks, debug-level crawl logging adds latency to the worker thread.

**Root cause:** `RotatingFileHandler` writes (and may rotate) inline with the caller.

**Implementation steps:**
1. After the existing handler is constructed, wrap it with a `logging.handlers.QueueHandler`
   feeding a `logging.handlers.QueueListener(handler)` started during `init_logger()`.
2. Register an `atexit` (or `QApplication.aboutToQuit`) hook to call `listener.stop()`.
3. Keep `init_logger` signature unchanged.

**Backwards compatibility:** caller-facing API (`logging.getLogger("stl_database")`)
unchanged.

**Verification:** logging continues to appear in the rotating file; rotation still occurs at
the configured size; no log lines lost across shutdown.

---

### 11. `_BrowserProxyModel.lessThan` extracts UserRole on every compare  [Severity: LOW]

**File(s):** [app/ui/browser_tab.py:88-100](app/ui/browser_tab.py#L88-L100).

**Symptom:** sorting a large table by File Size / Sequence takes noticeably longer than
sorting a string column.

**Root cause:** `lessThan` calls `index.data(UserRole)` for both operands on every compare,
re-paying the Qt round-trip O(n log n) times.

**Implementation steps:**
1. Maintain `self._sort_keys: dict[(column, source_row), Any]` populated when rows are
   inserted by the loader and cleared on reset.
2. In `lessThan`, look up by `(column, sourceRow)` directly; fall back to current behavior
   only if the key is missing (safety net).

**Backwards compatibility:** sort order unchanged.

**Verification:** time sort-by-File-Size on a 10 k-row table — should drop from ~hundreds
of ms to tens of ms.

---

### 12. Manual `session.close()` in delegates without `session_scope`  [Severity: LOW]

**File(s):** [app/ui/browser_tab.py:357-402](app/ui/browser_tab.py#L357-L402).

**Symptom:** edge-case session leaks if a delegate query raises.

**Root cause:** `get_session()` + `try/finally session.close()` is used instead of the
`session_scope()` context manager that wraps commit/rollback semantics.

**Implementation steps:**
1. Replace each delegate's body with `with session_scope() as session: …`.
2. Combine with #1 — once the cache is in place these queries run rarely.

**Backwards compatibility:** behavioral parity in the happy path; better error path.

**Verification:** existing tests still pass; manual: throw an exception mid-query, confirm
no leaked connection (SQLite "database is locked" no longer appears).

---

## Section 5 — Cross-cutting items

### 13. SQLite pragmas review  [Severity: LOW]

**File(s):** [app/database/db.py](app/database/db.py).

Audit the current per-connection pragmas and ensure all of the following are set; document
the rationale next to each line:

- `PRAGMA journal_mode = WAL;` (already present per CLAUDE.md)
- `PRAGMA synchronous = NORMAL;` — safe with WAL; reduces fsyncs.
- `PRAGMA cache_size = -65536;` — 64 MB page cache (negative = KiB).
- `PRAGMA temp_store = MEMORY;` — keep temp B-trees in RAM.
- `PRAGMA mmap_size = 268435456;` — 256 MB mmap window for reads.
- `PRAGMA foreign_keys = ON;` (already required by some delete cascades — verify).

**Backwards compatibility:** pragmas are per-connection, no schema impact.

**Verification:** observable speedup on cold-cache queries; existing test suite unaffected.

---

### 14. Add a "UI work budget" rule to CLAUDE.md  [Severity: LOW — docs only]

**File(s):** [CLAUDE.md](CLAUDE.md).

Add a short subsection under *Module Responsibilities → `app/ui/`*:

> **UI work budget.** No method invoked on the GUI thread may perform DB, LMDB, archive, or
> file I/O beyond a single cheap row lookup. Anything heavier must be dispatched to a
> `QThread`, `QRunnable`, or coalesced via `QTimer`. New widgets that need data should
> request it from the existing loader/cache layers, not query directly.

**Backwards compatibility:** documentation only.

**Verification:** future code reviews can cite the rule.

---

## Section 6 — Suggested rollout order

Implement in the order below to maximize visible improvement per unit of risk:

**#1 → #2 → #5 → #4 → #3 → #8 → #7 → #9 → #6 → #11 → #10 → #12 → #13 → #14**

Each step is independently shippable; the README, in-app help, and `CHANGELOG.md` should be
updated alongside the relevant commit per the project's *Documentation* convention.
