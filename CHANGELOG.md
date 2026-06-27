# Changelog

## Unreleased

- **Scale is now multi-valued.** A model can have several scales at once (e.g. a 180 mm bust and a 32 mm tabletop version in the same archive). Enter scales as a comma-separated list (with autocomplete) in the editor, mass editor, and the table's Scale cell; the filter pane lists each scale and a model appears under every scale it has. The crawler now stores *all* detected scales, AI Tagging can suggest several, and METADATA.7z sidecars round-trip the full list (old single-scale sidecars still import). Existing single scales are migrated automatically on first launch.
- AI Tagging: scale is no longer hallucinated. Scale can't be seen in a photo, so the model is now told never to guess it, and any suggested scale that doesn't actually appear in the archive path, filenames, or existing metadata is discarded (space/dash-insensitive, so `1/35` still matches `1-35`).
- AI Tagging: the local model server (`llama-server`) is now also shut down when the main window closes, not just when the AI Tagging dialog closes, so the background process never lingers after the app exits.
- AI Tagging: repeated words within a single tag are now collapsed (e.g. "gothic gothic gothic horror" → "gothic horror"), cleaning up the model's occasional stutter.
- Database Maintenance: new **Prune Images per Model…** button reduces the number of stored images per model to a chosen maximum (e.g. keep 3 of 10). The oldest images are kept; the extras and their thumbnails are deleted from the database. Run **Compact Thumbnail Store** afterwards to reclaim the freed disk space.
- Database Maintenance: action buttons are now arranged in a 3-column grid instead of a single row.
- Configuration → Browser: new **AI Tagging → Model temperature** setting (default 0.2) controls the sampling temperature passed to the AI tagging model. Lower values keep tags/metadata literal and grounded in the images; higher values are more creative but more prone to hallucinated results.
- AI Tagging: the tag prompt now tells the AI to tag only what is actually shown and not to add related characters, vehicles, or items from the same franchise that don't appear (e.g. tagging C-3PO on an X-Wing), cutting down hallucinated by-association tags.
- AI Tagging: the prompt no longer assumes every image in an archive shows the same model. The AI is told some images may be a different model, separate parts, packaging, or promo art, and to focus on the main miniature — reducing tag/metadata pollution from mixed-content archives.
- AI Tagging: the model's existing catalogued metadata (title, creator, sculptor, category, scale, source, year, description, and tags) is now included in the prompt as a hint, so the AI can build on and correct what the archive already had instead of guessing from scratch.
- AI Tagging: new **Overwrite existing values** checkbox. Unchecked (default), AI suggestions only fill empty fields; checked, they replace the current values. Applies to both single-model Save and **Process All**. Tags are always merged (never removed).
- AI Tagging: new **Process All** button runs AI analysis on every selected model unattended and saves the results automatically — no per-model review. The dialog shows each model live as it is processed (thumbnails, then tags and metadata fill in) so you can watch progress. Existing values are preserved (only empty fields are filled and new tags merged in); the run can be stopped mid-way.
- Fixed: AI Tagging could crash with `QThread: Destroyed while thread is still running` when navigating between models (Save & Next), finishing a Process All run, or closing the dialog while inference was still in progress. The dialog now waits for the model's `generate()` to finish before releasing the worker thread.
- Fixed: crawling a large library could abort upserts with `MDB_MAP_FULL: Environment mapsize limit reached` once the thumbnail store grew past its fixed 10 GB cap. The LMDB map now grows automatically when full and reopens sized to the existing data, so the store is no longer capped.
- Database Maintenance: new **Compact Thumbnail Store** button reclaims disk space from deleted thumbnails by rewriting the LMDB store (which never shrinks on its own). Run it after removing many models or images.
- **AI tagging no longer uses PyTorch.** It now runs **Qwen2.5-VL-3B** through the bundled-on-demand [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` binary instead of torch + transformers. Benefits:
  - **No Python AI packages** — this removes the torch download (1.5–3 GB), the "Setup incomplete — packages not found" failure, and the requirement to build the exe with a specific Python version (the old approach broke on Python 3.14, which torch has no wheels for).
  - On first use the app downloads the small llama.cpp runtime (~17 MB CPU / ~650 MB CUDA) and the Qwen2.5-VL GGUF model (~3.3 GB) into `<data_dir>/ai/`. A setup dialog offers a **CPU** or **NVIDIA GPU (CUDA)** build and shows download progress.
  - Works on any machine for CPU; GPU acceleration is automatic when the CUDA variant is installed.
  - The model runs as a local background process (`llama-server`) that the app starts on demand and shuts down when the AI Tagging dialog closes.

## 2026.24 — 2026-06-15

- **AI Tagging**: new right-click "AI Tagging…" option (any column, single row) opens a dialog that uses Qwen2-VL-2B-Instruct to suggest tags and fill all editable metadata fields (title, creator, sculptor, category, scale, source, year, description). Each suggestion is editable inline; tags appear as removable pill buttons. OK merges suggestions into the database without overwriting existing tags.
- AI packages and the Qwen2-VL model weights (~4.8 GB total) are downloaded on first use into `<data_dir>/ai_packages/` and `<data_dir>/ai_cache/` — no system install or admin rights required. A first-run setup dialog shows download progress.
- Works on Windows without a GPU (CPU inference via PyTorch CPU wheel); if an NVIDIA GPU is present it is used automatically.
- Packaged `.exe` bundles `pip` so the download-and-install flow works inside the frozen app with no Python on the host system.

## 2026.22 — 2026-05-30

- AI image similarity (optional): images can now carry a learned **CLIP embedding** (ONNX, CPU) for semantic matching of the same model across different renders/angles/colours. New **Database Maintenance → Compute Image Embeddings** button, plus an **AI embedding (CLIP)** method selector in *Find by Image* and *Find Duplicate Archives → Images*. Optional and dependency-light: disabled gracefully until `onnxruntime` + a CLIP ONNX model are present.
- Fixed: the browser's active filters were silently dropped after a mass edit (and any other refresh); the table now stays filtered and the filter-pane selections are preserved.
- Image similarity: perceptual hash upgraded from 64-bit to **128-bit** (horizontal + vertical dHash) for fewer false matches; old hashes are recomputed on the fly, and **Database Maintenance → Recompute Image Hashes** re-hashes the whole library. Still dependency-free (pure Pillow).
- Find Duplicate Archives by Content (STL Files tab): selecting a result now previews the thumbnails of both archives side by side.
- Localization: translatable UI via Qt Linguist `.ts` files in `resources/i18n/` (English and Spanish built-in, more drop-in), with a language picker in Configuration; untranslated strings fall back to English.
- Configuration: Language moved to its own sidebar section; Appearance colour pickers grouped into a collapsible, width-scaling 3-column "Custom Colors" section; sidebar labels now translatable.
- Crawler tab: resizeable folder list (splitter), the path-pattern checkbox doubles as the editor show/hide toggle, and Override metadata is now collapsible.

## 2026.21 — 2026-05-20 … 05-24

- Crawler merged with Update Crawled Folders into a single multi-folder dialog with a colour-coded results table.
- Path patterns: `{token:n}` prefix modifier to emit only the first *n* characters of a value.
- Crawler move tracking: deferred deletion of missing records, cross-session reconciliation, and collision guards for ambiguous matches.
- Database Maintenance: distinct "Flagged Missing" status with dedicated removal; search / find-moved now include flagged rows.
- Tools: Backup / Restore Library dialog (packs db, thumbnails, and settings into one `.7z`).
- Performance: `LookupCache` in the mass editor, a `models_view` SQL view for the browser, and filter-pane query optimisations (~24× faster scoped counts; new presupports/source indexes).
- Crawler log hides unchanged archives by default; urllib3 connection-pool noise suppressed.

## 2026.20 — 2026-05-17

- Find Duplicate Archives by Content: right-click to open an archive's folder or delete the archive from disk.
- Configuration: choose the file manager used to open folders (System default / Double Commander / custom).

## 2026.19 — 2026-05-08 … 05-10

- Crawler: path-pattern apply toggle; Override metadata (Creator, Type/Source); moved-file detection (hash, then name+size); auto-restore dialog on finish.
- Filter pane: faceted filtering across all sections, preserved expansion state, solo-on-double-click, and per-item model counts.
- Browser: Edit Authors dialog (rename / delete / merge / merge-similar).
- Mass editor: Find & Replace across selected fields; autocomplete scoped to selected models.
- Database Maintenance: Find Moved in DB + Merge Moved.
- Update Crawled Folders: per-folder checkbox selection.
- New crawler warning: archive without printable files; `.url` search scope restricted.

## 2026.18 — 2026-05-01 … 05-03

- Browser: background crawl with a status indicator; Show Files; delete archive file from disk; File Size column.
- Tools: Find Archives by Content (STL-file overlap and perceptual-image dHash); Find Metadata Duplicates (exact / Levenshtein / Soundex); Archive Contents and Compare Archive Contents dialogs.
- Storage: `stl_files` table recording each `.stl` name and size, included in sidecars.
- Configuration: Browser section renamed File writing; added file-deletion / trash-folder settings and Store STL file list.
- Fix: thumbnail sidebar resize loop.

## 2026.17 — 2026-04-26

Initial alpha release — a desktop app for cataloguing 3D-printable STL archives.

- Crawler: four-phase parallel pipeline (hash → diff → extract → upsert) for `.zip` / `.7z` / `.rar`, CRC fingerprinting, path-pattern + `info.json` / `.url` metadata, presupport/scale/warning detection, and a CLI entry point.
- Storage: SQLite (WAL + migrations), LMDB thumbnails, rotating logs, and per-folder `METADATA.7z` sidecars.
- Browser: filterable/sortable table with inline editing, tag autocomplete, image pane, image grid, mass editor, and file sorting.
- Dialogs: configuration, statistics, column distribution, database maintenance, scale editor, help, and about.
- Configuration: two-tier (QSettings + per-library JSON); Windows 11 and nine Material themes.
- Build/CI: PyInstaller specs (GUI + headless crawler) and GitHub Actions tests / Windows release builds.
