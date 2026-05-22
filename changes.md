# Database Performance — Tier 1 + Tier 2 changes (2026.21)

This document records the work done in response to the request "the database
operations are very slow, review all database operations to see what can be
improved." The review identified the dominant bottlenecks; this commit applies
the high-impact, low-risk improvements while keeping SQLite as the storage
engine.

All 238 existing tests pass after the changes (`pytest tests/ -v`).

---

## What's actually slow — concrete findings

The review profiled code paths and query counts on a representative library.
These are the bottlenecks that were found before any fix was applied, ordered
by severity.

### Finding 1 — SQLite left at factory defaults

`db.py` set only `journal_mode=WAL` and `foreign_keys=ON`. Everything else
stayed at SQLite's default settings, which are tuned for embedded use with
minimal resource consumption:

| Setting | Default | Cost |
|---|---|---|
| `synchronous` | `FULL` | fsync on every commit — 2–5× slower writes than `NORMAL` under WAL |
| `cache_size` | ~2 MB | Hot pages constantly evicted; every page re-read from disk |
| `temp_store` | file | Sort buffers and temp tables written to `/tmp` instead of RAM |
| `mmap_size` | 0 | No memory-mapped I/O; every large scan goes through the VFS layer |
| `busy_timeout` | 0 ms | First lock contention between UI thread and crawler raises `OperationalError` immediately |

Zero code changes to query logic — purely a configuration problem.
**Fix:** T1.1.

### Finding 2 — N+1 database queries in crawler Phase 1

`CrawlerWorker._phase_hash` issued **one `SELECT … WHERE archive_path = ?`
per archive** inside a Python loop, plus a synchronous `Path.stat()` per
archive before the parallel hash phase began:

```python
for archive_path in batch:
    stat = archive_path.stat()                          # sequential syscall
    row = session.query(STLModel).filter_by(            # one round-trip per archive
        archive_path=str(archive_path)
    ).first()
    if row and row.archive_hash == ...:
        skip.add(archive_path)
```

For a 50-archive batch that's **50 DB round-trips + 50 sequential stat
calls** before a single file is hashed. At 5 ms/query (WAL lock contention
at defaults) that's 250 ms of latency that scales linearly with batch size.
**Fix:** T1.2.

### Finding 3 — 50k sequential `Path.exists()` calls at every crawl start

`_collect_missing()` iterated every model row and called `Path(ap).exists()`
synchronously, one after another:

```python
for model in session.query(STLModel).all():
    if not Path(model.archive_path).exists():
        missing.append(model.id)
```

On a 50k-row library that's **50,000 sequential filesystem syscalls** before
the crawler even starts hashing. On a spinning disk or slow NAS these calls
serialise at the device level — wall-clock time scales with library size and
media latency.
**Fix:** T1.3.

### Finding 4 — Full-table scan + Python counting in the filter pane

`_populate_filter_tree` fetched all 13 relevant columns for every row in the
library and counted them in Python:

```python
_scan = session.execute(_select(
    STLModel.creator, STLModel.source, STLModel.release_year, ...  # 13 columns
)).all()

for (creator, source, yr, mo, sculptor_id, ...) in _scan:
    if creator is not None:
        _counts[("creator", creator)] = _counts.get(..., 0) + 1
    ...  # 12 more branches per row
```

For a 50k-row library: **50k rows × 13 columns = 650k Python branch
evaluations, 13 × 50k temporary value allocations** per filter pane rebuild.
Every filter click or Apply triggered this full pass. The payload crossing
the Python ↔ SQLite boundary was O(rows), not O(unique values).
**Fix:** T2.7 (GROUP BY per column) + T2.5 (composite indexes so each GROUP
BY is backed by an index scan rather than a full table scan).

### Finding 5 — Redundant `SELECT + flush` in every `get_or_create_*` call

Every call to `get_or_create_sculptor`, `get_or_create_tag`,
`get_or_create_collection`, etc. issued a `SELECT … WHERE name = ?` followed
immediately by `session.flush()` — even when the same sculptor or tag had
already been looked up earlier in the same batch:

```python
# called once per archive, with shared sculptors across the batch
sculptor = get_or_create_sculptor(session, name)  # SELECT + maybe INSERT + flush
```

A 50-archive batch sharing 5 distinct sculptors and 20 tags generates
**~125 redundant SELECTs** just for those two entity types, before accounting
for release, scale, collection, and category lookups.
**Fix:** T2.8 (per-batch `LookupCache`).

### Finding 6 — Only single-column indexes; composite filter queries do a partial scan

The browser filter pane builds queries that combine two or more columns
(e.g. `WHERE creator = ? AND source = ?`, `WHERE sculptor_id = ? AND
collection_id = ?`). With only single-column indexes, SQLite used one index
for the first predicate and fell back to a table scan for the second.

For a 10k-sculptor library filtered by sculptor + collection this is an
O(matching-sculptor-rows) scan instead of an O(log n) index lookup.
**Fix:** T2.5 (composite indexes + `ANALYZE`).

### Finding 7 — No visibility into which queries are slow

There was no instrumentation. The only signal that something was slow was
user perception. Without per-statement timing it was impossible to confirm
whether a fix helped or rank remaining bottlenecks.
**Fix:** T1.4 (SQL profiler hooks, now toggled from **Settings → Debug**).

---

## Tier 1 — small, broadly-applicable speedups

### T1.1 — PRAGMA tuning (db.py)

**Before:** only `journal_mode=WAL` and `foreign_keys=ON` were set.

**After:** added five additional pragmas applied on every connect:

| Pragma | Value | Effect |
|---|---|---|
| `synchronous` | `NORMAL` | With WAL, fsyncs only at checkpoint instead of every commit. Safe on power loss with WAL; 2-5x faster writes. |
| `cache_size` | `-65536` (64 MB) | Page cache, was ~2 MB default. Frequently-touched pages stay in RAM. |
| `temp_store` | `MEMORY` | Sort buffers and temp tables held in RAM, not /tmp. |
| `mmap_size` | `268435456` (256 MB) | Memory-mapped I/O window — large GROUP BY scans benefit considerably. |
| `busy_timeout` | `5000` ms | Concurrent UI + crawler no longer hits "database is locked" — waits up to 5 s for the lock. |
| `wal_autocheckpoint` | `1000` pages | Caps WAL growth at ~4 MB so the log doesn't balloon during heavy crawls. |

**File:** [app/database/db.py:28-49](app/database/db.py#L28-L49)

### T1.2 — Batch Phase 1 DB lookup in the crawler

**Before:** the hash phase issued one `SELECT … WHERE archive_path = ?` per
archive in a Python loop, *and* a synchronous `Path.stat()` per archive. For a
50-archive batch that's 50 round-trips before parallel hashing even began.

**After:**
1. `Path.stat()` runs in parallel via the existing `ThreadPoolExecutor`.
2. A single `SELECT … WHERE archive_path IN (…)` fetches all existing rows in
   one round-trip.
3. The same in-memory matching against stat results decides which files need
   re-hashing.

**File:** [app/crawler/crawler.py:172-220](app/crawler/crawler.py#L172-L220)

### T1.3 — Parallelise `_collect_missing()` existence checks

**Before:** at the start of every crawl, the code iterated every model in the
DB and called `Path(ap).exists()` synchronously. For a 50k-row library that's
50k sequential filesystem syscalls just to start a crawl.

**After:**
1. Rows already flagged `is_missing=True` skip the syscall entirely (they are
   re-included automatically by the existing logic).
2. The remaining existence checks run in a `ThreadPoolExecutor` sized to the
   crawler's worker pool.

**File:** [app/crawler/crawler.py:454-498](app/crawler/crawler.py#L454-L498)

### T1.4 — Toggleable per-statement SQL profiler

**New:** SQLAlchemy `before_cursor_execute` / `after_cursor_execute` hooks
time every statement. When the `QSettings` key `debug/sql_profiling` is
truthy, the log gets one line per statement at INFO; statements ≥ 100 ms are
also escalated to WARNING for easy `grep` against the rotating log file.

The toggle is checked per-statement so it takes effect immediately without
restarting the engine.

**UI toggle:** the profiler can now be enabled from **Settings → Debug →
"Log SQL statement timings"** without touching a debug shell.

**Files:**
- [app/database/db.py:170-206](app/database/db.py#L170-L206) — profiler hooks
- [app/ui/config_dialog.py](app/ui/config_dialog.py) — checkbox in `_build_debug_page`, load/save via `QSettings`

---

## Tier 2 — targeted improvements requiring schema / query refactor

### T2.5 — Composite indexes for hot filter combinations

**Before:** only single-column indexes existed. Browser queries combining
`creator + source`, `sculptor_id + collection_id`, etc. fell back to one
index + table scan for the second predicate.

**After:** added five new indexes via `CREATE INDEX IF NOT EXISTS` (so they
apply to existing databases on next startup, no migration needed):

| Index | Columns | Used by |
|---|---|---|
| `ix_models_creator_source` | `(creator, source)` | Browser filters Creator+Source; also accelerates creator-only filters via prefix-matching. |
| `ix_models_sculptor_collection` | `(sculptor_id, collection_id)` | Browser cascading filters Sculptor+Collection. |
| `ix_models_collection_subcollection` | `(collection_id, subcollection_id)` | Filter pane Subcollection section. |
| `ix_models_is_missing` (partial) | `(is_missing) WHERE is_missing=1` | `_collect_missing` and DB-maintenance flagged-missing scan; only the tiny minority of rows is indexed. |
| `ix_images_dhash` (partial) | `(dhash) WHERE dhash IS NOT NULL` | `find_content_duplicates_dialog` perceptual-image search. |

An `ANALYZE` is also issued once after creation so the SQLite query planner
starts using the new indexes on the very first query.

**File:** [app/database/db.py:155-211](app/database/db.py#L155-L211)

### T2.6 — Move filter-pane rebuild to a worker thread *(deferred)*

**Status:** **not implemented** in this commit.

**Why:** `_populate_filter_tree` is a 580-line method with QTreeWidget
construction deeply interleaved with DB queries (lines 1223-1806 of
[browser_tab.py](app/ui/browser_tab.py#L1223)). A correct worker-thread
refactor requires:
1. Extracting a `_collect_filter_pane_data(session, scope, …) -> dict`
   helper that returns a plain data structure.
2. Adapting all 13 per-section tree builders to read from that dict instead
   of issuing their own queries inline.
3. Adding a `FilterPaneWorker(QThread)` with a generation counter to discard
   stale results when the user toggles filters rapidly.
4. Manual UI testing of all 13 filter sections with both unscoped and
   cascading-scope behaviour, including the path-tree subset.

Given the SQL improvements in T2.5 + T2.7 alone should reduce filter rebuild
time substantially (the unscoped count-gather was O(N) Python; now it's
indexed GROUP BY in SQLite C code), the worker-thread refactor is best done
as a follow-up after measuring the real-world impact of this commit.

**Recommended approach when revisited:**
- Add `FilterPaneWorker(QThread)` that opens its own session and calls
  `_collect_filter_pane_data`.
- Emit a `results_ready(dict)` signal whose slot runs the existing tree
  construction on the GUI thread.
- Track a monotonic generation counter on `BrowserTab` so a second click
  starting a new worker invalidates any in-flight results.

### T2.7 — Replace full-table scan + Python counting with GROUP BY queries

**Before** ([browser_tab.py:1355-1419](app/ui/browser_tab.py)):
```python
_scan = session.execute(_select(
    STLModel.creator, STLModel.source, STLModel.release_year, ...
    # 13 columns
)).all()

for (creator, source, yr, mo, sculptor_id, collection_id, ...) in _scan:
    if creator is not None:
        _counts[("creator", creator)] = _counts.get(...) + 1
    ...  # 13 more if-blocks per row
```
On a 50k-row library that's 650k Python operations and 13 × 50k value
allocations per filter rebuild.

**After:** one `GROUP BY` query per column, executed in SQLite's C code,
each backed by either a single-column or composite index. The total payload
crossing the Python ↔ SQLite boundary is now O(unique-values), not O(rows).

```python
def _count_col(col, kind, *, none_filter=True):
    q = session.query(col, func.count(STLModel.id))
    if none_filter:
        q = q.filter(col.isnot(None))
    q = q.group_by(col)
    for v, c in q.all():
        _counts[(kind, v)] = c

_count_col(STLModel.creator,          "creator")
_count_col(STLModel.source,           "source")
_count_col(STLModel.sculptor_id,      "sculptor_id")
# … 6 more
```

The few values that need Python work — date (year + month composite tally),
warnings (substring matching against a newline-separated text column), and
paths (folder splitting from `archive_path`) — were left as targeted queries
that only pull their relevant column.

**Expected impact:** for a 50k-row library, the unscoped pass should drop
from ~600 ms (full scan + Python loop) to ~50 ms (indexed GROUP BYs). Larger
libraries scale even better because the GROUP BY work happens in SQLite's
B-tree traversal, not Python.

**File:** [app/ui/browser_tab.py:1350-1438](app/ui/browser_tab.py#L1350-L1438)

### T2.10 — Skip empty subquery in `_run_id_subquery`; add `presupports` + `source` indexes

**Before:** `_run_id_subquery()` always returned a `.subquery()` object, even when no
filter conditions applied to the inner query (e.g. when only one filter is active and
the skipped section has no other active filters).  SQLite saw:

```sql
WHERE models.id IN (SELECT anon_1.id FROM (SELECT models.id AS id FROM models) AS anon_1)
```

which forced SQLite to materialise all model IDs before the outer scan — needlessly
turning a free query into a full-scan + join.  The `source` DISTINCT query at 23:41:57
showed this clearly at 25–26 ms.

Additionally, `presupports` queries (GROUP BY / DISTINCT) consistently took 15–31 ms
because there was no index — every query was a full table scan.  Similarly, the
source-only scoped-count queries took 10–14 ms because `source` is the second column
in the `(creator, source)` composite index and SQLite won't use that prefix for a
source-only predicate.

**After:**
1. `_run_id_subquery` tracks whether any WHERE condition was added via a `_has_condition`
   flag (using a local `_w()` helper).  When no conditions apply it returns `None`
   instead of `.subquery()`.  Callers in `_populate_filter_tree` already guard with
   `if scope is not None:`, so the change is transparent.
2. New partial index `ix_models_presupports ON models (presupports) WHERE presupports IS NOT NULL`
   allows index-only GROUP BY / DISTINCT for the presupports filter section.
3. New index `ix_models_source ON models (source)` covers source-only predicates that
   could not use the composite `(creator, source)` index.

**Files:** [app/ui/browser_tab.py:2507-2577](app/ui/browser_tab.py#L2507),
[app/database/db.py:188-220](app/database/db.py#L188)

---

### T2.9 — Materialise path-filter IDs once in the filter pane

**Before:** whenever a folder path filter was active, every scoped-count query
(13 count queries + 13 list-value queries = 26 total) embedded the condition
`replace(archive_path, '\\', '/') LIKE ?` inline.  Because `replace()` is a
function call on the column, SQLite cannot use any index — each query did a
full table scan.  From profiler output: each such query took ~37 ms, total
≈ 37 ms × 26 = **~960 ms** per filter pane refresh when a folder is selected.

**After:** one LIKE scan materialises the matching model IDs into a Python
list (`_path_ids_mat`).  All 26 subsequent queries use
`WHERE models.id IN (1, 2, …)` — a primary-key lookup that takes < 1 ms each.
Total cost is now one ~37 ms scan + 26 × < 1 ms = **~40 ms** — roughly a 24×
speedup for any filter combination that includes a folder selection.

The `else` branch in `_run_id_subquery` preserves the LIKE path for the
case where no path filter is active (the materialized list is `None`, and
`path_inc` / `path_exc` are both empty so the branch is a no-op).

**File:** [app/ui/browser_tab.py:2485-2525](app/ui/browser_tab.py#L2485-L2525)

### T2.8 — Per-batch lookup cache for `get_or_create_*` helpers

**Before:** every call to `get_or_create_sculptor`, `_tag`, `_collection`,
`_release`, `_scale`, etc. issued a `SELECT … WHERE name = ?` followed by a
`session.flush()`. Inside a 50-archive crawl batch that shares ~5 distinct
sculptors and ~20 tags, that's hundreds of redundant SELECTs.

**After:**
1. Added an optional `cache: LookupCache | None` keyword to every helper in
   [app/database/helpers.py](app/database/helpers.py). The cache is a plain
   `dict[tuple[str, frozenset], Any]` keyed by `(model_cls.__name__,
   frozenset(filters.items()))`.
2. When provided, the helper returns the cached instance on a hit and stores
   the result on a miss — eliminating the SELECT for repeated lookups within
   the cache's lifetime.
3. Callers without a cache see no behaviour change (the default is `None`).

**Crawler wiring:** [`CrawlerWorker.run`](app/crawler/crawler.py#L120-L133)
creates a fresh `self._lookup_cache = {}` immediately before
`_phase_upsert(...)` and resets it after, so the cache scope matches the
batch's session. All `get_or_create_*` call sites in the crawler now thread
the cache through:
- `_detect_rules` — scale lookup
- `_assign_metadata` — release lookup
- `_upsert_archive` — six "force override" lookups (sculptor, category,
  collection, release, scale, source) and per-tag lookups in the tags loop

**Trade-off:** the cache stores live ORM instances. They are only valid
within the originating session, which is why the cache is recreated per
batch (matching the `session_scope()` lifetime) and not retained globally.

**File:** [app/database/helpers.py:36-122](app/database/helpers.py#L36-L122),
[app/crawler/crawler.py:117-133](app/crawler/crawler.py#L117-L133)

---

## What was NOT changed / deferred work

To keep this commit focused and reviewable, the following were considered
and deferred. They are tracked as open todos.

### Deferred: T2.6 — Worker-thread refactor of `_populate_filter_tree`

`_populate_filter_tree` is a 580-line method
([browser_tab.py:1223-1806](app/ui/browser_tab.py#L1223)) with QTreeWidget
construction deeply interleaved with DB queries across 13 filter sections.
A correct refactor requires:

1. Extracting `_collect_filter_pane_data(session, scope, …) -> dict` — a
   pure-data helper with no Qt calls.
2. Adapting all 13 per-section tree builders to read from that dict instead
   of issuing their own queries inline.
3. Adding `FilterPaneWorker(QThread)` that opens its own session, calls
   `_collect_filter_pane_data`, and emits a `results_ready(dict)` signal
   whose slot drives the existing tree construction on the GUI thread.
4. A monotonic generation counter on `BrowserTab` so a second filter click
   invalidates any in-flight worker result.
5. Manual UI testing of all 13 sections with both unscoped and
   cascading-scope behaviour, including the path-tree subset.

The SQL improvements in T2.5 + T2.7 already move the bottleneck out of
Python; measure real-world impact first before committing to this refactor.

### T2.11 — Mass-editor `LookupCache` batching

**Before:** `_save()` in `mass_editor_dialog.py` issued one `filter_by(name=…).first()`
per FK field per model row in its loop — up to 7 round-trips per model (release,
collection, subcollection, category, sculptor, scale, and one per tag name). When
editing 500 models with 3 shared tags, that was ~3,500 redundant SELECTs.

**After:** A `LookupCache` dict is created before the loop and passed to
`get_or_create_*` helpers (the same pattern as T2.8). The first model in the batch
populates the cache; every subsequent model hits the cache for the same names.
The Find & Replace loop gets its own `_fnr_cache` for the same reason.

**Trade-off:** cache lifetime is the `session_scope()` block, same as T2.8.

**Files:** [app/ui/mass_editor_dialog.py:17-25](app/ui/mass_editor_dialog.py#L17),
[app/ui/mass_editor_dialog.py:632-740](app/ui/mass_editor_dialog.py#L632)

---

### T2.12 — `models_view` SQLite VIEW + `_load_table` refactor

**Motivation:** The browser SELECT joined six FK lookup tables in every
`_load_table()` call. While the `TableLoaderWorker` background thread kept the UI
responsive, the explicit multi-table join was constructed in Python on every reload,
and the join plan had to be re-evaluated by SQLite on every query.

**Implementation:**
1. `_ensure_views()` in `db.py` creates a `models_view` SQL VIEW that pre-defines
   the six LEFT JOINs (sculptors, collections, subcollections, categories, releases,
   scales). The view is reflected into a SQLAlchemy `Table` object and cached at
   `_models_view` for the lifetime of the process.
2. `get_models_view()` exposes the reflected Table for type-safe column access.
3. `_load_table()` in `browser_tab.py` now `SELECT FROM models_view` via `mv =
   get_models_view()`.  All WHERE clauses use `mv.c.X` (view columns) instead of
   `STLModel.X` — the FK ID columns are included in the view, so all existing integer
   filter predicates continue to work unchanged.

**Correctness:** SQLite materialises views inline at query time, so the WHERE predicates
push straight through to the underlying `models` table. No trigger maintenance is
required. 238 tests pass.

**Files:** [app/database/db.py:219-295](app/database/db.py#L219),
[app/ui/browser_tab.py:1916-2075](app/ui/browser_tab.py#L1916)

### Deferred: Tier 3 — Bulk-insert / deferred flush patterns

Two related improvements that reduce `session.flush()` overhead during large
initial crawls:

- **`session.bulk_save_objects` / `INSERT OR IGNORE`:** avoids round-tripping
  to SQLite after every `get_or_create` INSERT. Worth doing if T2.8 alone
  doesn't close the gap on first-time crawls of very large folders.

- **Deferred flush across the batch:** collect all new entities (tags,
  sculptors, releases, etc.) throughout the batch and issue a single
  bulk INSERT at end-of-batch rather than calling `session.flush()` after
  each `get_or_create`. Requires the caller to hold pending PKs in memory
  until the flush resolves them — more invasive, highest potential gain for
  import-heavy workloads.

### Ruled out: Engine swap

DuckDB, embedded Postgres, and `apsw` were evaluated. SQLite tuned with the
new pragmas (T1.1) is the right engine for this single-writer desktop
workload. The concurrency story (`busy_timeout` + WAL) now matches the
actual access pattern (one UI thread + one crawler thread). No engine swap
is warranted.

---

## Verification

```bash
.venv/bin/pytest tests/ -v
# 238 passed in 2.83s
```

## How to measure the impact

1. Enable the SQL profiler: **Settings → Debug → "Log SQL statement timings"**
   (or programmatically: `QSettings().setValue("debug/sql_profiling", True)`).
   The toggle is read per-statement — no restart needed.
2. Trigger the slow operation (e.g. click Apply on the filter pane on a
   large library, or start a crawl).
3. `grep "SQL " <data_dir>/stlLibrary.log` to see per-statement timings.
4. Lines tagged `WARNING SQL` are statements that ran ≥ 100 ms — those are
   the remaining targets for the deferred Tier 3 work above.
