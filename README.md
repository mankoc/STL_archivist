# stlLibrary

**stlLibrary** is a desktop application for managing local collections of 3D-printable model archives. It scans folders of `.zip`, `.7z`, and `.rar` files, extracts metadata and thumbnails, and stores everything in a local database so you can browse and search your collection without unpacking anything manually.

Built with PyQt6 and a Rust core (`stl_native`).

Version: 2026.24

## Features

- **Archive scanning** — crawls directories of compressed archives in parallel using CRC-based fingerprinting (no full decompression required)
- **Metadata extraction** — pulls model info from embedded `info.json`, `.url` files, and folder path structure
- **Path pattern parsing** — extract sculptor, release name, model title, and more from your folder/file naming conventions; an optional toggle lets you disable pattern extraction without clearing the saved pattern
- **Metadata overrides** — force a specific Creator or Type/Source value for every archive in a crawl, overriding whatever is auto-detected
- **Thumbnail browser** — stores JPEG previews in LMDB and displays them in a scrollable image pane; supports drag-and-drop to add new images
- **Image formats** — images are read from archives in `.jpg`, `.jpeg`, `.png`, `.webp`, `.avif`, `.gif`, and `.bmp`; drag-and-drop and *Find by Image* additionally accept `.tif` and `.tiff`. All are transcoded to JPEG for the LMDB thumbnail store
- **Animated GIFs** — an animated GIF keeps its animation: the thumbnail is stored as a resized GIF and plays in the image preview pane and the full-size viewer. Everywhere a still is needed (table cells, image grid, similarity hashing, AI tagging) the first frame is used. Sources over 60 frames or 2 MB fall back to a static first-frame JPEG
- **Image grid** — scrollable grid of thumbnails for the currently filtered set of models, with a resizable thumbnail slider (60–300 px)
- **Scale detection** — automatically detects miniature scale (e.g. `28mm`, `32mm`, `1/35`) from archive and file names; a model can hold **multiple scales** (e.g. a 180 mm bust and a 32 mm version in one archive)
- **Presupport detection** — identifies whether an archive contains Lychee, Chitubox, or presupported STL files
- **Crawler warnings** — flags archives that contain presupported STLs, no images, no printable files (`.stl`/`.obj`/`.lys`/`.chitubox`), or nested archives. The presupported check only ever matches `.stl` files — named as presupported (`body_presupported.stl`, `sup_arm.stl`) or sitting in a presupport folder (`Presupported/`, `PS/`, `Supported STL/`) — so images, readmes, slicer files, and folder entries never trigger it. An “unsupported” marker wins wherever it appears — in any folder above the file or in the mesh's own name — so `No_Supported/`, `Unsupported/`, `Without Supports/`, `No_PS/` and nested layouts like `supports/Unsupported/120MM_Unsup_Emerald.stl` are read as raw meshes, while a clean sibling such as `supports/120MM_Emerald.stl` is still flagged. The filename patterns are editable in *Configuration → Crawler warnings*
- **Rich model catalog** — tracks sculptors, categories, subcategories, releases, scales, tags, and warnings per model
- **Tagging** — add and manage tags per model for easy filtering
- **Browser** — search, filter, and browse your collection with inline editing, tri-state filters (include / exclude / no filter), and a clear-filters button
- **Filter panel** — faceted / cascading multi-value filters; group headers toggle "has value / no value"; individual values cycle through include (green **+**), exclude (red **−**), and off states
- **Editor** — edit model metadata (title, author, sculptor, category, subcategory, release, scale, presupports, source, URL, date, sequence, tags)
- **Mass editor** — apply bulk changes across multiple models at once, including category and subcategory; preview what a path pattern extracts for every selected model with **Show parsed values**, and tidy stray spacing with **Trim leading/trailing whitespace**
- **Metadata sidecars** — export a `METADATA.7z` snapshot into every archive folder so the library can be restored on a new machine without re-crawling; optionally includes thumbnails
- **Image pane toggle** — show or hide the right-hand thumbnail sidebar via a toolbar button or the View menu
- **File sorting** — optionally move and rename archive files into a structured directory layout using a path pattern (enable in Configuration)
- **File Size column** — browser table shows each archive's size on disk (B / KB / MB / GB), sortable numerically
- **STL file list** — optionally records the name and uncompressed size of every `.stl` file inside each archive (enable *Store STL file list* in Configuration); included in METADATA.7z sidecars
- **Find Metadata Duplicates** — finds models with identical or phonetically similar field values; supports exact match, Levenshtein (configurable distance), and Soundex; results can be sent directly to the browser table
- **Find Archives by Content** — two-tab tool: the *STL Files* tab flags archive pairs that share a configurable fraction of identical files; the *Images* tab finds archives with similar thumbnails (selecting a result previews both archives' images), comparing every pair of distinct images (on a 363k-image library about 4 s by perceptual hash and 35 s by AI embedding) and skipping banners and logos shared by more than a chosen number of archives (default 10); hover any row to see thumbnails in a popup; right-click any result row to open the archive's folder in the configured file manager, compare archive contents, or delete the archive from disk
- **Image similarity matching** — two methods, selectable in the *Images* tab and *Find by Image*: a built-in **128-bit perceptual hash (dHash)** for near-duplicates (computed by the native core), and an optional **AI embedding (CLIP)** for semantic matching of the same model across different renders, angles, and colours. Embeddings are an optional, dependency-light add-on (`onnxruntime` + a CLIP ONNX model); compute them via **Database Maintenance → Compute Image Embeddings**
- **Find by Image** — pick an image file and find catalogued models whose thumbnails look like it, by perceptual hash or AI embedding. Results show the best-matching image per model, from at most the 1024 closest images (the status line says when more match, so you can tighten the threshold). Click a result to see its full thumbnail with the model's title, creator, sculptor, collection, release, tags, image count, archive path and size; **Go to Model** (or a double-click on the result) shows the model in the main table
- **AI Tagging** — right-click any row and choose *AI Tagging…* to let Qwen2.5-VL analyse the model's thumbnails — along with the archive path, filenames, and any metadata already catalogued — and suggest tags, title, creator, sculptor, collection, subcollection, scale, source, year, and description (it picks the most specific collection — e.g. Batman → *comics* / *DC* — rather than defaulting to a generic genre). Each suggestion is editable before saving; tags are merged with (not replaced by) existing ones. Select several rows for one-by-one review, or click **Process All** to run every selected model unattended and save results automatically. By default existing values are kept (only empty fields filled, new tags added); tick **Overwrite existing values** to let the suggestions replace current field values instead. Tick **Ignore existing metadata** to withhold the catalogued metadata and file list from the prompt, using only the images, archive path, and image filenames (re-tag from scratch). The whole feature can be switched off with **Enable AI tagging** in *Configuration → AI* (which hides the action from the toolbar, Tools menu, and right-click menu). The model's **temperature** is configurable in *Configuration → AI → AI Tagging* (lower = more literal and grounded, higher = more creative). Runs locally via the [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` binary — **no torch, no Python AI packages**. On first use the small runtime and the ~3.3 GB GGUF model are downloaded once into your data folder; pick a **CPU** or **NVIDIA GPU (CUDA)** build in the setup dialog (CPU works on any machine)
- **Archive content viewer** — list every `.stl` file in an archive, or compare two archives side-by-side with configurable matching by filename, size, or hash
- **Physical file deletion** — optionally delete or trash archive files from disk via the right-click menu in the browser table or the duplicate-finder results (enable in Configuration → File writing)
- **Configurable file manager** — choose which program opens archive folders from right-click menus: System Default, Double Commander, or a custom executable (Configuration → Browser)
- **Background crawl** — dismiss the Crawler dialog without stopping the worker; a status badge in the status bar shows live progress and brings the dialog back when clicked
- **Move tracking** — missing archives are flagged rather than deleted so cross-session and cross-folder move detection works across multiple crawl runs; collision guards prevent ambiguous name+size or hash matches from mis-attributing metadata. A moved archive whose content is unchanged is recognised by its hash before extraction, so it keeps its record, metadata and thumbnails without being decompressed again
- **Warning details** — click the ⚠ in the browser's Warnings column to see exactly which archive members triggered each warning; absence-based warnings explain themselves, and unreadable or missing archives are reported rather than shown as empty
- **Check Presupport Warning** — a Database Maintenance action that re-reads every archive's file list and fixes the *Models with presupported stl* warning library-wide, clearing it where no presupported `.stl` is present and adding it where one is. Reads archive catalogs only, so it is much faster than rescanning; unreadable or missing archives are skipped rather than rewritten
- **Check Images Warning** — a Database Maintenance action that re-reads every archive's file list and fixes the *Archive without images* warning library-wide, clearing it where images are present and adding it where they are not. Reads archive catalogs only, so it is much faster than rescanning; unreadable or missing archives are skipped rather than flagged
- **Thumbnail deduplication** — identical thumbnails are stored once and shared between models, so a library that ships the same renders across many archives stops paying for every copy. Run **Database Maintenance → Deduplicate Thumbnails** to collapse an existing store (and sweep thumbnails no model references); new thumbnails are deduplicated as they are written
- **Backup / Restore** — pack the database, thumbnails, and settings into a single `.7z` archive via **Tools → Backup / Restore Library…**; restore extracts back into the data directory after confirmation
- **Statistics** — column and aggregated statistics dialogs for a quick numeric overview (counts, compressed/uncompressed library size, storage used); database maintenance (vacuum, integrity check)
- **Database maintenance tools** — verify/relocate missing archives, find duplicate entries, recompute image hashes, compute CLIP embeddings, compact the thumbnail store, **compact the database** (SQLite `VACUUM`, reclaiming the space left by deleted records), and **prune the number of images per model** down to a chosen maximum (keeps the oldest, deletes the extras and their thumbnails)
- **Themes** — built-in color schemes: System Default, VS Code Dark+, Windows 11 Dark, Windows 11 Light, and nine Material Design themes via qt-material
- **Localization** — translatable user interface; **English** and **Spanish** ship built-in, and additional language definitions can be added as Qt Linguist `.ts` files in `resources/i18n/`. Pick the language in **Configuration → Appearance**

## Requirements

- Python 3.10+
- Dependencies listed in `requirements.txt`:
  - PyQt6
  - Pillow
  - py7zr
  - rarfile
  - xxhash
  - qt-material
  - onnxruntime
  - numpy

## Installation

### Pre-built Windows executables

Grab the latest release from the [Releases](../../releases) page:

| Executable | Description |
|---|---|
| `stlLibrary-<version>.exe` | Full GUI application |

No Python installation required — self-contained single-file executable.

### From source

```bash
# 1. Clone the repository
git clone <repo-url>
cd stl_database

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Build the native (Rust) core
python tools/build_native.py

# 5. Run the application
python main.py
```

The native core (`rust/`, installed as the `stl_native` module) is required: it owns the
database (schema, queries and writes, with sqlite-vec built in), the thumbnail store and the
crawl. Python handles what the core hands back: archives it cannot read exactly, images only
Pillow decodes (AVIF, CMYK JPEG), PDF pages and CLIP embeddings. Building it needs
[Rust](https://rustup.rs) and, on Windows, the Visual Studio C++ Build Tools. The codebase is moving to Rust step by step; see
[docs/rust-migration-plan.md](docs/rust-migration-plan.md).

> **Note for RAR support:** `rarfile` requires the `unrar` binary to be installed on your system.
> - Linux: `sudo apt install unrar`
> - Windows: install [WinRAR](https://www.rarlab.com/) or the standalone `UnRAR` tool and make sure it is on your `PATH`.
> You can also set a custom path to the executable in **Configuration → Crawler → RAR tool**.

### Server (preview)

The Rust server that will replace the desktop app (see
[docs/rust-migration-plan.md](docs/rust-migration-plan.md), Phase 5) can already serve a
library read-only over HTTP:

```bash
cd rust
cargo run --release -p stl-server -- --data-dir <data dir> --bind 127.0.0.1:8770
```

It reads `<data dir>/server.json` (`{"bind": "127.0.0.1:8770"}`) and the `STL_DATA_DIR` /
`STL_BIND` environment variables; the API is described at `/api/openapi.json`.

The server has user accounts with three roles: *viewer* (read only), *editor* and *admin*
(also manages users). On first start, create the administrator **on the server's own
machine** (setup is refused from other machines and through a proxy):

```bash
curl -X POST http://127.0.0.1:8770/api/auth/setup -H "content-type: application/json" \
     -d '{"name": "admin", "password": "at least 8 characters"}'
```

Viewers can browse; editors can also edit models (cells, the editor, the mass editor),
add and remove images, and manage tags, categories, collections, scales and creators. Edits
refresh the folder's `METADATA.7z` as the desktop app does.

Editors run crawls and maintenance as jobs (`POST /api/jobs`, for example
`{"kind": "crawl", "folder": "D:/STL/Patreon"}`), one at a time, with progress and logs
streamed over the `/api/events` WebSocket. Crawls use the library's crawler settings.

PDF previews and AI embeddings need two runtime libraries, which the server loads when it
finds them (beside the server, in the data directory, or at a configured path):

| Feature | Library | Configure with |
|---|---|---|
| PDF previews | pdfium ([prebuilt binaries](https://github.com/bblanchon/pdfium-binaries), build 7881) | `"pdfium"` in `server.json` or `STL_PDFIUM_PATH` |
| AI embeddings | ONNX Runtime 1.17 or newer (the `onnxruntime` Python package ships one) | `"onnxruntime"` in `server.json` or `ORT_DYLIB_PATH` |

The CLIP model is found as in the desktop app (`<data dir>/models/clip_image_encoder.onnx`
or `STLLIBRARY_CLIP_MODEL`). An editor can then run `{"kind": "compute_embeddings"}`, and
crawls embed new thumbnails when *compute embeddings during crawl* is on. The server
still cannot read the few images only Pillow decodes (AVIF, CMYK JPEG).
`.rar` archives need UnRAR on `PATH`, or `"unrar"` in `server.json` / `STL_UNRAR_PATH`.

The AI tagger uses the desktop app's AI install (*AI Setup*: llama.cpp's `llama-server` and
the Qwen2.5-VL model under `<data dir>/ai`). Elsewhere, point `server.json` at them, or at
an OpenAI-compatible server that is already running:

```json
{"ai": {"llama_server": "/opt/llama/llama-server", "model": "/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",
        "mmproj": "/models/mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf", "gpu_layers": 99}}
```

(`{"ai": {"url": "http://127.0.0.1:8080"}}` or `STL_AI_URL` for a running one). The server
starts `llama-server` on the first request and stops it after ten idle minutes or when it
shuts down.

Accounts are kept in `<data dir>/server.db`, separate from the library database. Put the
server behind an HTTPS reverse proxy before exposing it beyond `127.0.0.1`.

Editors pick folders to crawl by browsing the server's disks (`/api/files/browse`) within
the library's roots: the crawled folders and any `"roots"` listed in `server.json`.
Administrators can browse anywhere. Anyone signed in can download an archive
(`/api/models/{id}/archive`). To let remote clients open an archive's folder, map the
server's paths to theirs in `server.json`:

```json
{"bind": "0.0.0.0:8770", "roots": ["D:/STL"], "path_map": [{"server": "D:/STL", "client": "\\\\nas\\stl"}]}
```

On a headless machine or in a container, create the first administrator with
`stl-server create-admin admin` (the password comes from `STL_ADMIN_PASSWORD` or is asked
for). A `Dockerfile` builds the server with pdfium and ONNX Runtime included; see the
comments at its top. It has not been test-built yet.

Administrators back up the library from the web client (Maintenance → Backups); backups
are kept in `<data dir>/backups` and use the desktop app's Backup / Restore format, so
either side restores the other's. To restore one, stop the server and run
`stl-server restore FILE.7z --data-dir DIR`; the files it replaces are moved to
`pre-restore-<time>` in the data directory.

A library can be open in only one process at a time: the desktop app refuses a library the
server has open (and a second app instance on the same library), and the server refuses one
the app has open.

### Web client (preview)

The browser front end for the server lives in `web/` (React + TypeScript, Mantine). It needs
[Node.js](https://nodejs.org) 24 to build:

```bash
cd web
npm install
npm run build      # writes web/dist; stl-server serves it at http://127.0.0.1:8770/
npm run dev        # development: http://localhost:5173, forwarding /api to a server on :8770
```

So far it signs in and browses the library: the filter pane (include / exclude values,
has-value groups, counts), search, the sortable table, and a detail pane with thumbnails and
the archive download. Editors can edit cells in place (double-click), open the model editor,
add images by dropping them on the detail pane, remove images, and remove selected models
from the library. The **Jobs** page tracks crawled folders (add them by browsing the
server's disks, crawl them with a path pattern) and runs maintenance, with live progress and
logs. **Mass edit** (on a selection) and the **Lookups** page (tags, categories,
collections, subcollections, scales, creators) work as in the desktop app. **Statistics** and **Find by Image** (perceptual hash, or AI embedding when the server has
ONNX Runtime and the CLIP model) work as their desktop dialogs. **Duplicates** finds archives
sharing STL files, archives with matching images and models with similar metadata, and
compares two archives side by side. **Maintenance** verifies archives, finds duplicate
entries and moved records (by content, in the database or under a folder), merges and
removes records, re-checks warnings, generates or removes the metadata sidecars, runs the
image and storage jobs and (administrators) makes backups. The **AI tagger** (on a
selection) suggests tags and metadata per model for review, or tags them all in one job.
*Archives* on a selection rescans archives, regenerates thumbnails or deletes the files (to
the trash folder); the detail pane lists a model's files and explains its warnings. The
browser shows its models as a table or as an image grid, and **Folders** browses the
archives folder by folder. *Sort files…* moves selected archives into a folder tree built
from a path pattern, after a preview. **Settings** holds your colour scheme, the library's crawler and sidecar settings
(administrators change them; they are the same `settings.json` the desktop app uses), and
user administration.

### Desktop shell (preview)

`rust/stl-desktop` is a small native app (Tauri 2, using the system's WebView2 on Windows)
that shows the web client in its own window:

```bash
cd rust
cargo build -p stl-server -p stl-desktop   # the shell starts the stl-server beside it
cargo run -p stl-desktop
```

Its start page opens a library on this computer (it starts `stl-server` on a free local
port for that window, and stops it when the window closes) or connects to a server,
remembering the servers you add. *File → Switch server…* (Ctrl+L) returns to it; the tray
icon offers the same. Inside the shell, the detail pane can also show an archive's folder
in the file manager when the server's paths are visible from this computer. The shell
holds no library logic: everything else is the web client talking to the server.

### Environment overrides

These optional environment variables take precedence over the values set in Configuration. They are useful for portable or headless setups.

| Variable | Overrides |
|---|---|
| `STL_DATA_DIR` | Data directory (database, thumbnails, logs, `settings.json`) |
| `STL_UNRAR_PATH` | Path to the `unrar` executable |

## Usage

### 1. Configure the application

On first launch, go to **File → Configuration…** and set up:

- **Database** — choose where the SQLite database and thumbnails are stored (restart required after changing)
- **Crawler** — max thumbnails per model, max image size in pixels, path to `unrar` executable, and which crawler warnings to enable (pre-supported STL, no images, no printable files, nested archives, support files)
- **File writing** — enable *Sort files into directory* (adds a right-click menu entry); optionally enable *Allow file delete* (with a confirmation guard) to physically delete or trash archive files from the right-click menu; configure a *Trash folder* to move deleted files there instead of permanently removing them
- **File writing → Sidecar** — enable *Metadata sidecar* to write `METADATA.7z` per folder during crawl; optionally include thumbnails
- **Browser** — configure the filter pane (model counts, alphabetical letter bins) and the **File Manager** used when opening archive folders: System Default, Double Commander, or a custom executable path
- **Appearance** — select the interface **Language**, a color scheme (preview is applied immediately; Cancel reverts), an **Icon set** (Material monochrome, Fluent Color, Fluent Emoji or Flat Color Icons; applies after restart), **Toolbar buttons** (icon and text, or icon only), highlight colors, and fonts

### 2. Crawl your archive folder

#### GUI

1. Click **Open Crawler** (toolbar icon or `Ctrl+O`).
2. Select the root folder that contains your STL archives.
3. Optionally set a **path pattern** to auto-populate metadata fields from your folder/file naming convention (see [Path Patterns](#path-patterns) below). The **Apply path pattern to metadata extraction** checkbox sits above the *Parse metadata from path* section and both shows/hides the pattern editor and toggles pattern extraction without losing the saved pattern text.
4. Optionally click the collapsible **Override metadata** header to expand it and force a specific Creator or Type/Source value for every archive in this crawl.
5. Click **Start**. The crawler will walk the directory recursively, hash each archive, detect scale and presupport information, extract preview images, and upsert records into the database.

### 3. Browse and edit

- Use the search bar and filter tree (Authors / Tags / Type / Date / Sculptor / Category / Subcategory / Release / Scale / Presupports / Warnings / Path) to narrow down the collection.
- **Filter indicators** use colored circles:
  - Gray circle — no filter active for this value
  - Green **+** — include rows matching this value
  - Red **−** — exclude rows matching this value
- **Click a leaf value** to cycle through include → exclude → off.
- **Click the checkbox icon on a group header** (e.g. Authors, Tags) to filter by whether models have *any* value for that field:
  - Green **+** — show only models that have a value for this field
  - Red **−** — show only models that have *no* value for this field
- Use the **Clear** button to reset all active filters.
- Double-click a row (or press `E`) to open the **Editor** and update model details.
- Click **Image Grid** in the toolbar to view thumbnails for all currently displayed models. Use the **Size** slider at the top of the grid to resize thumbnails from 60 to 300 px.
- Click the **image pane** toolbar button (or use the View menu) to toggle the thumbnail sidebar.
- Right-click for context-menu options: *Show Files…* (single selection, lists archive STL file contents), edit, mass-edit, *Delete from DB*, *Delete archive file(s) from disk…* (shown only when enabled in Configuration), generate thumbnails, create/update sidecar, and (if enabled) sort files into a directory.
- Start a crawl and click **Run in Background** to dismiss the Crawler dialog without stopping the worker; a status badge in the status bar shows progress and brings the dialog back when clicked.
- Use **Mass Edit** to apply changes to multiple selected rows at once.

### 4. Tags

- **Add Tag** — creates a new global tag (toolbar button).
- **Edit Tags** — opens the tag manager to rename or delete existing tags.
- Tags can be assigned per model through the editor or the inline tag autocomplete in the browser table.

### 5. Categories, Subcategories, and Sculptors

Managed through dedicated editor dialogs accessible from the browser's right-click menu or the editor. Each category can have multiple subcategories (e.g. Category: *Comics*, Subcategory: *Marvel*). Deleting a category cascades to its subcategories.

### 6. Scales

Scale values (e.g. `28mm`, `32mm`, `1/35`) are detected automatically during crawl and can also be set manually per model through the editor or the **Scale** inline cell in the browser. A model can carry **several scales at once** — enter them as a comma-separated list (with autocomplete); each is filterable individually in the filter pane, and a model appears under every scale it has.

## Path Patterns

The crawler and the file-sort feature can parse and render structured folder/file paths using a pattern syntax. Define a pattern with `{token}` placeholders that match folder segments.

| Placeholder | Aliases | Maps to |
|---|---|---|
| `{source}` | | Distribution / shop name |
| `{creator}` | `{author}` | Creator / designer name |
| `{title}` | `{name}` | Model title |
| `{date}` | `{month}` | Release date (`YYYY-MM` or `YYYY`) |
| `{sculptor}` | | Sculptor (looked up or created) |
| `{collection}` | `{category}` | Collection |
| `{subcollection}` | `{subcategory}` | Subcollection / variant |
| `{release}` | | Release / bundle name |
| `{sequence}` | | Position in a series |
| `{url}` | | Source URL |
| `*` | | Ignored segment (any value) |

### Prefix modifier `{token:n}`

Append `:<n>` to any token to use only the first *n* characters of its value. This is useful when sorting files into initial-letter buckets:

```
{creator:1}/{creator}/{title}
```

With creator `"Sanix"` this renders as `S/Sanix/<title>`.

**Example** — if your archives are organised as:

```
/ArtistName/2024-01 Some Release/model_name.zip
```

use the pattern:

```
{sculptor}/{release}/{title}
```

## Project Structure

```
stl_database/
├── main.py                  # GUI entry point
├── requirements.txt
├── resources/
│   ├── style.qss            # Application stylesheet
│   ├── icons/               # SVG icons (light/dark variants)
│   ├── i18n/                # Qt Linguist .ts language definitions (en.ts ships built-in)
│   └── themes/              # QSS color schemes (VS Code, Win11); Material themes loaded via qt-material
├── app/
│   ├── config.py            # CrawlerConfig dataclass
│   ├── crawler/
│   │   ├── crawler.py       # QThread-based directory crawler (parallel workers)
│   │   ├── archive.py       # Archive extraction helpers (.zip/.7z/.rar)
│   │   ├── metadata.py      # Metadata extraction logic (info.json, .url, path)
│   │   ├── rules.py         # Scale / presupport / warning detection rules
│   │   └── folder_metadata.py  # METADATA.7z sidecar read/write
│   ├── database/
│   │   ├── db.py            # init_db (schema migration in the native core), clear / backfill
│   │   └── native_db.py     # library(): the native LibraryDb connection pool
│   ├── ui/
│   │   ├── main_window.py   # Main application window
│   │   ├── browser_tab.py   # Collection browser with inline editing and multi-value filters
│   │   ├── crawler_dialog.py
│   │   ├── crawler_tab.py
│   │   ├── editor_dialog.py
│   │   ├── mass_editor_dialog.py
│   │   ├── image_grid_dialog.py
│   │   ├── metadata_sidecar_dialog.py  # Sidecar generation dialog and worker
│   │   ├── scale_editor_dialog.py
│   │   ├── category_editor_dialog.py
│   │   ├── subcategory_editor_dialog.py
│   │   ├── tag_editor_dialog.py
│   │   ├── sort_dialog.py
│   │   ├── config_dialog.py
│   │   ├── find_duplicates_dialog.py
│   │   ├── find_content_duplicates_dialog.py
│   │   ├── file_list_dialog.py
│   │   ├── about_dialog.py
│   │   └── help_dialog.py
│   └── utils/
│       ├── thumbnailer.py   # LMDB thumbnail store (save/load/delete)
│       ├── path_pattern.py  # Path pattern parser and renderer
│       ├── string_utils.py  # Levenshtein distance, Soundex
│       ├── i18n.py          # Translation loader (.ts parser + QTranslator)
│       └── logger.py        # Rotating gzip-compressed file logger
├── rust/                    # Rust workspace (migration in progress)
│   ├── stl-core/            # Core library: no UI, no Python
│   └── stl-py/              # PyO3 bindings, installed as the `stl_native` module
├── tools/build_native.py    # Builds rust/ and installs `stl_native` into the active environment
└── tests/                   # pytest suite; tests/golden/ compares Rust and Python outputs
```

## Localization

The interface is translatable via Qt's standard mechanism. User-facing strings
are wrapped in `self.tr(...)` (inside widgets/dialogs) or
`QCoreApplication.translate(...)` (in free functions), and translations live as
Qt Linguist `.ts` files in `resources/i18n/`. English (`en.ts`) ships built-in;
untranslated strings fall back to the English source.

**Add a new language** (e.g. German):

```bash
# 1. Regenerate the English template from the current sources
pylupdate6 main.py app/ui/*.py -ts resources/i18n/en.ts

# 2. Copy it and translate the <translation> values
cp resources/i18n/en.ts resources/i18n/de.ts   # then edit de.ts (Qt Linguist works too)
```

`de.ts` then appears automatically in **Configuration → Appearance → Language**.
At runtime [app/utils/i18n.py](app/utils/i18n.py) parses the chosen `.ts` file
directly through a custom `QTranslator`, so no `lrelease`/`.qm` compilation step
is needed.

## AI Image Embeddings

The image-similarity tools can use a learned **CLIP** embedding for semantic
matching (the same model across different renders, angles, and colours) in
addition to the built-in perceptual hash. `onnxruntime` and `numpy` are
included in `requirements.txt` and bundled in the Windows executable.

The only additional step is providing the model file:

```
<data_dir>/models/clip_image_encoder.onnx
```

Use **Configuration → AI → Image similarity → Download CLIP model** to
fetch it automatically, or export it manually:

```bash
pip install optimum[exporters]
optimum-cli export onnx --model openai/clip-vit-base-patch32 \
    --task zero-shot-image-classification clip_onnx/
# then copy clip_onnx/vision_model.onnx to <data_dir>/models/clip_image_encoder.onnx
```

Once the model is in place, open **Database Maintenance → Compute Image
Embeddings** to populate embeddings for existing thumbnails. The **AI embedding
(CLIP)** method becomes selectable in *Find by Image* and *Find Duplicate
Archives → Images*.

**Storage** — embeddings are stored with the
[sqlite-vec](https://github.com/asg017/sqlite-vec) extension (built into the native
core) in a `vec0` table, `image_vec`, inside the main database.
*Find by Image* searches it with a nearest-neighbour query inside SQLite
(about 0.2 s on 334k embeddings), over the 1024 nearest images.

Databases from earlier versions kept embeddings on the `images` table. The first
start with this version moves them into `image_vec` and compacts the file
(about a minute for 334k embeddings, shown on the splash screen). Back up the
database first if you want to be able to go back to an older version. Because
`image_vec` is a virtual table, tools without sqlite-vec (DB Browser for SQLite,
for example) can read every other table but not that one.

**GPU acceleration** — the Windows executable uses the **DirectML** backend
(built into Windows, works on NVIDIA / AMD / Intel GPUs with no extra driver
install). When running from source on Linux/macOS with an NVIDIA GPU, install
`onnxruntime-gpu` instead of `onnxruntime` for CUDA acceleration.

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-nc-sa/4.0/) (CC BY-NC-SA 4.0).

You are free to share and adapt this work for non-commercial purposes, provided you give appropriate credit and distribute any derivative works under the same license.
