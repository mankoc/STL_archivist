# Changelog

## Unreleased

- **The _Models with presupported stl_ warning now only marks archives that really contain presupported meshes.** Its name patterns (`[_ -]presupported`, `sup_*.stl`) were applied to every archive member regardless of type, so a preview image called `Dragon - Presupported.jpg`, a `readme_presupported.txt`, or even a *directory* entry such as `Biker Mice from Mars Presupported lys` (archives list their folders as members) flagged an archive holding no presupported mesh at all. The rule now requires an `.stl`, which qualifies in either of two ways:
  - its own filename is marked (`body_presupported.stl`, `sup_arm.stl`), or
  - it sits under a folder that marks its contents (`Presupported/`, `PS/`, `Supported STL/`, `Pre-Supported/`, `Presupports/`).

  Folder names match on whole words, so `PSD Sources/` and `Props/` are not mistaken for `PS/`. An “unsupported” marker always wins, wherever it appears — in any folder above the file or in the mesh's own filename. That covers the common raw-mesh folders (`No_Supported/`, `No Supports/`, `Unsupported/`, `Non-Supported/`, `Not Supported/`, `Without Supports/`, `No_PS/` and merged spellings), and crucially the layout where raw meshes are nested *inside* the supported tree — `supports/Unsupported/120MM_Unsup_Emerald.stl` and its `NSFW_supports/NSFW_Unsupported/` twin — which the outermost-marker-wins reading got wrong. Filenames carrying `_Unsup_`, `_UNSUPP`, `_unsupported` or `NoSupport` are excluded the same way, so a mesh can declare itself raw even inside a supported folder. The test is per-file: a clean sibling such as `supports/120MM_Emerald.stl` is still flagged. Only a whole word counts, so `Dino Supported/` and `Casino PS/` still do. Non-`.stl` files in a presupport folder — slicer files, renders, readmes — do not qualify, and the warning details window now names the mesh that matched rather than its folder. This also lines the warning up with the *Presupports* column, whose own detection has always required `.stl`. Existing records keep their stored warning until re-evaluated — use **Rescan Files** on the affected models to clear it.

- **Warning details now list the files responsible.** Clicking the ⚠ in the browser's *Warnings* column already opened a details window, but it matched the *presupported* warning against the configurable **supports** patterns rather than the rule that actually produced it — so the file list was routinely wrong or empty, which is no help when you are trying to work out why an archive is flagged. The detail view and the crawler now share one set of rule tests (`explain_warnings()` alongside `detect_warnings()`), so they cannot disagree. Also: the archive read moved off the GUI thread (it could freeze the window on a large `.7z`/`.rar`); *Archive without printable files*, *Archive with low compression rate*, and *Unreadable archive* now get explanations instead of a blank section; a missing or unopenable archive says so rather than silently showing nothing; and filenames are HTML-escaped.

- **Database Maintenance: fixed `QThread: Destroyed while thread is still running`.** All nine background workers in the dialog declared their own `finished` signal, which shadows `QThread.finished` — the signal Qt emits once a thread has actually stopped. Result handlers therefore ran from inside `run()`, and each one dropped the worker reference there, so an unparented thread could be destroyed while still running. Every worker now reports results through a distinctly named signal (`verify_finished`, `hash_finished`, `prune_finished`, and so on), and a single `_own_worker()` helper parents each thread to the dialog and releases it only when the real `QThread.finished` fires. Affects Verify, Search in Folder, Find Moved in DB, Find Duplicate Entries, Recompute Image Hashes, Compute Image Embeddings, Check Images Warning, Compact Thumbnail Store, and Prune Images per Model.

- **Database Maintenance: new _Check Presupport Warning_ action.** Re-reads every catalogued archive's file list and corrects the *Models with presupported stl* warning across the whole library — clearing it where no presupported `.stl` is present, adding it where one is. Only archive catalogs are read (nothing is decompressed), so it is far faster than rescanning, and it is the way to clear warnings raised by the older, looser presupport rule without re-crawling. Archives that are missing or cannot be opened are reported as skipped and left untouched, and no other warning on a record is modified. It shares a base worker with *Check Images Warning*, so both behave identically around batching, cancellation, thread lifetime, and canonical warning ordering.

- **Database Maintenance: new _Check Images Warning_ action.** Re-reads every catalogued archive's file list and corrects the *Archive without images* warning across the whole library — clearing it where the archive does contain images, adding it where it does not. Only archive catalogs are read (nothing is decompressed), so it is far faster than rescanning, and it is the quickest way to refresh a library after an update adds a new image format. Archives that are missing or cannot be opened are reported as skipped and left untouched, and no other warning on a record is modified.

- **Rescan Files** now recomputes warnings correctly. Rescanning an archive that does contain images did not clear an *Archive without images* warning whenever the archive could not actually be opened — most commonly a `.rar`, because the rescan built its configuration from dataclass defaults rather than from your settings, so the configured **unrar** path was ignored. A failed open returns an empty member list, which then looked exactly like an archive containing nothing, and the warning was re-applied while the rescan still reported success. Three related fixes:
  - The rescan now reads the same settings-backed configuration the crawler uses (unrar path, worker count, warning flags, STL-file-list toggle).
  - An archive that cannot be read is reported as an error and left untouched, instead of having its warnings, presupport status, scales, and file list rewritten from nothing.
  - Warning flags now match the crawler exactly: *Archive without printable files* honours your setting instead of being forced on, and *Archive with supports* keys off the **Support files detected** option and its patterns rather than reusing the presupported ones.

- Fixed **Generate Thumbnails for Selected…** crashing with `AttributeError: 'NoneType' object has no attribute 'finished'` as soon as the run completed. The completion handler clears the worker reference, but the main window was wired to the worker's `finished` signal *after* the modal progress dialog returned — which, for any run short enough to finish while the dialog was still up, meant wiring to a reference that had just been cleared. The completion signal is now emitted from the handler itself.

- **GIF images are now read from archives, and animated GIFs stay animated.** `.gif` joins the set of image types the crawler extracts. An animated GIF is stored as a resized GIF instead of a flattened JPEG, and plays in the image preview pane and the full-size viewer; everywhere a still is needed (table cells, image grid, dHash/CLIP matching, AI Tagging) the first frame is used, so nothing else changes. Safety caps keep one pathological source from bloating the store or pinning the GUI thread: over 60 frames or over 2 MB once resized falls back to a static first-frame JPEG. Already-crawled archives need a rescan (or **Tools → Generate Thumbnails for Selected…**) to pick up their GIFs.

- **AVIF images are now supported.** `.avif` files found inside archives are extracted and turned into thumbnails just like `.jpg`/`.png`/`.webp`, and `.avif` files can be dropped onto the image preview pane or picked in *Find by Image*. Decoding uses Pillow's built-in AVIF codec, so `requirements.txt` now asks for `Pillow>=11.3` (was `>=10.0`); no extra plugin is needed. Qt itself has no AVIF codec, so the *Find by Image* preview falls back to the Pillow-backed transcode when Qt cannot decode the picked file.

- Statistics: fixed **Show Statistics…** failing with `type object 'STLModel' has no attribute 'file_size'` and showing only an error message. *Total size (uncompressed)* is now summed from the catalogued archive contents (`STLFile.file_size`) instead of a column that does not exist on the model.
- Statistics / Column Distribution: fixed `QThread: Destroyed while thread is still running` warnings. Both dialogs' workers declared a `finished` signal that shadowed `QThread.finished`, and dropped the worker reference from the result handler while the thread was still winding down; the signals are renamed (`stats_ready` / `data_ready`), the workers are parented to their dialog, and a refresh waits for the previous run before starting a new one.

- Dependencies: `numpy` is now listed in `requirements.txt`. It was already a hard requirement — Find by Image and Find Content Duplicates import it at module level — but a fresh `pip install -r requirements.txt` did not pull it in.

- Code quality pass: hardened error handling and removed dead code with no change to normal behaviour.
  - AI server: a malformed, empty, or error response from `llama-server` now raises a clear, descriptive error instead of crashing with a low-level `KeyError`/`JSONDecodeError`; a startup that never becomes ready reports the last underlying error; and a force-killed server process is now waited on so it can't linger as a zombie.
  - AI setup: a failed or cancelled model/binary download no longer leaves a partial `.part` file behind.
  - Crawler: thumbnail/LMDB and `info.json` failures that were previously swallowed silently are now logged; a catalogued-but-unextracted image is skipped with a warning instead of risking a crash.
  - The AI tag-cleanup and prompt-building logic moved to a new `app/utils/ai_tagging.py` module with unit-test coverage; behaviour is unchanged.
  - Minor: removed unused imports and dead code, added missing docstrings, and made the crawler's warning scan and the `archive_folder` backfill slightly faster.

- Configuration: all AI settings now live in a dedicated **AI** section (previously split between *Browser* and *Debug*). It gathers **AI Tagging** (model temperature) and **Image similarity (embeddings)** — the CLIP enable/crawl/batch-size controls, the model download button, and the embedding *Search batch* that used to sit under *Debug*. A new **Enable AI tagging** checkbox at the top of the section switches the whole feature on or off; when disabled, the *AI Tagging* action is hidden from the toolbar, the Tools menu, and the table's right-click menu.

- Fixed & faster: applying filters (or refreshing after AI Tagging) with a **path filter** active no longer crashes with `OperationalError: too many SQL variables` on large libraries, and the filter pane rebuilds faster. The path filter's matching IDs are materialised once per rebuild into a connection-local TEMP table in the filter-tree worker; every per-section scoped count then references it as an indexed primary-key lookup, so the un-indexable `archive_path` LIKE scan runs a single time instead of ~once per section — and is never expanded into a giant `IN (?, …)` parameter list.

- Tag Editor: added a filter box that supports wildcards — `*` matches any run of characters and `?` a single one (case-insensitive); a plain query is treated as a substring match (e.g. `drag` finds "dragon", `gob*war*` finds "goblin warrior").
- Tag Editor → Merge Similar: new **Fuzzy (tokens)** method (rapidfuzz token-set ratio) finds tags that share words regardless of order or extra words — e.g. "spectral warrior" ↔ "warrior spectral", "goblin" ↔ "goblin warrior" — with a configurable minimum-similarity %. Complements the existing Levenshtein and Soundex methods.
- Tag Editor → Merge Similar: the kept name is now chosen from an editable **Replace with** combo (pre-filled with the selected value and all its similar tags), so you can keep any of the involved names or type a brand-new one; the value and every checked similar tag are merged into it.
- Tag Editor → Merge Similar: redesigned as pick-a-value / merge-many. Select a tag on the left to see every tag similar to it on the right (checkable, all checked by default), then **Merge checked into selected value** folds them all into that tag in one action — instead of merging one pair at a time.
- Tag Editor → Merge Similar: scan is now near-instant and no longer freezes the UI on large libraries (~5 min → ~0.2 s on 5,000 tags). It uses [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz) for the Levenshtein matrix (falling back to a bounded pure-Python scan if rapidfuzz/numpy are unavailable), fetches tag model-counts in a single `GROUP BY` instead of one query per tag, and runs on a background thread with a result count.
- Browser: the search box and the Creator/Type filters are now explicitly case-insensitive (search uses `ILIKE`; free-text filters match regardless of case), so "patreon" and "Patreon" are treated the same.
- **Scale is now multi-valued.** A model can have several scales at once (e.g. a 180 mm bust and a 32 mm tabletop version in the same archive). Enter scales as a comma-separated list (with autocomplete) in the editor, mass editor, and the table's Scale cell; the filter pane lists each scale and a model appears under every scale it has. The crawler now stores *all* detected scales, AI Tagging can suggest several, and METADATA.7z sidecars round-trip the full list (old single-scale sidecars still import). Existing single scales are migrated automatically on first launch.
- Scale detection: now recognizes scales bounded by underscores (e.g. `Hero_32mm_Base`, `Cyborg_1-35_kit`), which the previous word-boundary regex missed. Fixes both crawl-time detection and AI Tagging.
- AI Tagging: scales are now detected deterministically from the archive path, image filenames, **and the archive's file list** (using the same detector as the crawler), so multi-scale archives are fully captured (e.g. an archive with `_32mm` and `_75mm` files now yields both) instead of relying on the model, which under-reported them.
- AI Tagging: new **Ignore existing metadata** checkbox. When checked, the model's catalogued metadata and file list are withheld from the prompt, so only the images, archive path, and image filenames are used — handy for re-tagging from scratch without prior bias. Applies to both single-model and Process All.
- AI Tagging: the archive's printable file list (`.stl`/`.obj`/… names, when stored) is now passed to the model as context, improving subject and scale recognition (filenames often encode the scale). Used as a hint and as grounding for scale detection.
- AI Tagging: new **Max tags** control in the dialog caps how many AI-generated tags are kept (the first N after cleanup; 0 = no limit). Applies to single-model and Process All, is remembered between sessions, prunes the already-shown tags immediately when you lower it, and doesn't stop you adding tags manually.
- AI Tagging: now suggests **Collection** and **Subcollection** instead of the generic genre Category. The model picks the most specific top-level collection from a fixed list (fantasy, sci-fi, comics, tv, movies, games, books, anime, real life, historic, cartoons, music and musicians, modelling, props, pin-ups) — e.g. Batman → Collection "comics", Subcollection "DC" — instead of defaulting everything to "fantasy". The separate genre Category field is left untouched.
- AI Tagging: cumulative chains of near-identical tags are now broken into their increments. The model often emits a growing ladder ("spectral", "spectral warrior", "spectral warrior gothic metal", …); each tag is reduced to the words it adds over the longest shorter tag that prefixes it, so the chain becomes "spectral", "warrior", "gothic", "metal", … as separate tags. Tags that nothing prefixes (e.g. "dark fantasy") are kept whole.
- AI Tagging: cumulative/run-on tags are now split into atomic ones. The model often emitted overlapping phrases ("x", "x with y", "x with y and z"); tags are now split on connectives (with / and / in / …) and punctuation — e.g. "figure in suit with bow tie and gloves" becomes "figure", "suit", "bow tie", "gloves" — and the prompt asks for single-concept tags. Short noun phrases like "bow tie" are kept whole.
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
