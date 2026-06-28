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

## Deferred (intentionally not done)

These were flagged but left for a dedicated refactor pass; doing them in a
low-risk sweep would have added churn and regression risk to untested code:

- **GUI-thread I/O** — `browser_tab._handle_image_drop` and
  `editor_dialog._load_model` do file/LMDB/DB work on the GUI thread. These are
  user-initiated, bounded-size operations, and moving DB writes off the main
  thread collides with the app's single-writer SQLite model (the crawler uses a
  dedicated writer thread). Needs a proper threading design, not a quick move.
- **Structural refactors** — splitting the ~4,300-line `BrowserTab` god class,
  and a shared base class for the seven near-duplicate editor dialogs
  (~1,700 lines of boilerplate).

## Verification

- `python -m pytest tests/ -v` → 265 passed, 0 failed.
- Import smoke check across `main_window`, `crawler`, `db`, and the AI/util
  modules.
