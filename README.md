# stlLibrary

**stlLibrary** manages local collections of 3D-printable model archives. It scans folders of
`.zip`, `.7z` and `.rar` files, extracts metadata and thumbnails, and stores everything in a
library database so you can browse, search, tag and de-duplicate your collection without
unpacking anything.

It is a server (`stl-server`, Rust) with a web client (React) that runs in any browser, and
a small desktop app (`stl-desktop`, Tauri) that shows the same client in its own window and
can open a library on your computer without setting up a server.

**Guides:** [using stlLibrary](docs/user-guide.md) · [deploying a server](docs/deployment.md)

Version: 2026.25.0

> The earlier PyQt6 desktop application lives on in the repository's history (and on its
> own branch). Libraries are compatible: this version opens the same data directory
> (`stlLibrary.db`, `thumbnails.lmdb/`, `settings.json`).

## Features

- **Archive scanning** — crawls folders of archives in parallel using CRC-based
  fingerprints (no decompression needed to notice what changed); moved archives keep their
  record, metadata and thumbnails, even when moved between crawled folders
- **Metadata extraction** — from embedded `info.json`, `.url` files and the folder structure
  through [path patterns](#path-patterns)
- **Thumbnails** — up to N images per archive (JPEG; animated GIFs stay animated), stored
  once even when many archives share them; PDF previews with pdfium
- **Scale, presupport and warning detection** — scales such as `28mm`, `32mm`, `1/35` (a model
  can have several); Lychee / Chitubox / presupported STL detection; warnings for archives
  with presupported STLs, no images, no printable files or nested archives, with *Warning
  details* showing which files trigger each
- **Browser** — faceted filters (include / exclude per value, has-value groups, counts),
  search, a sortable virtualised table with in-place editing and selectable columns, an
  image grid, a detail pane with thumbnails, downloads and image drag-and-drop, and a
  folder-by-folder archive browser
- **Editing** — model editor, mass editor (path patterns with a preview, find & replace,
  trimming), lookup editors for tags, categories, collections, subcollections, scales and
  creators
- **Duplicates** — archives sharing STL files, archives with similar images (perceptual hash
  or AI embedding, skipping banners shared by many archives), models with equal or similar
  metadata (exact, Levenshtein, Soundex), with side-by-side comparison
- **Find by Image** — drop a picture to find models whose thumbnails look like it
- **AI tagger** — Qwen2.5-VL through llama.cpp suggests tags, title, creator, sculptor,
  collection, scale, source, year and description; review one by one or tag a selection in
  one job
- **Maintenance** — verify archives, find moved and duplicate records, search a folder for
  missing archives, re-check warnings, recompute image hashes and embeddings, prune images,
  compact the database, rescan archives, regenerate thumbnails, delete files to a trash
  folder, sort files into a folder tree by pattern
- **Metadata sidecars** — a `METADATA.7z` per archive folder lets a new library restore its
  records without re-crawling
- **Backups** — database, thumbnails and settings in one `.7z`; restore with
  `stl-server restore`
- **Users** — viewer, editor and administrator roles; per-user preferences
- **Statistics** — totals, rankings and the distribution of any column

## Installation

### Windows installer

The [Releases](../../releases) page has `stlLibrary_<version>_x64-setup.exe`: the desktop
app with the server and the web client. It installs for the current user and needs no
administrator rights (Windows 10 and 11 ship the WebView2 runtime it uses).

### From source

You need [Rust](https://rustup.rs) (on Windows with the Visual Studio C++ Build Tools) and
[Node.js](https://nodejs.org) 24.

```powershell
# Windows: the web client, the server and the desktop app (release build)
.\build.ps1
# ... and the installer (rust\target\release\bundle\nsis\)
cargo install tauri-cli --version 2.12.3 --locked
.\build.ps1 -Installer
```

```bash
# Linux / macOS
./build.sh             # everything (the desktop app needs WebKitGTK on Linux)
./build.sh --server    # only the web client and the server, for a headless host
```

The binaries end up in `rust/target/release`. Keep `stl-server` beside `stl-desktop`; the
server finds the web client in `web/` beside it, or in `web/dist` of the checkout.

### Docker

The `Dockerfile` builds the server and the web client with pdfium and ONNX Runtime; see the
comments at its top.

```bash
docker build -t stllibrary-server .
docker run -d --name stl -p 8770:8770 -v /srv/stl-data:/data -v /srv/STL:/library:ro stllibrary-server
docker exec -it stl stl-server create-admin admin
```

## Running

### Desktop app

Start `stl-desktop` (or *stlLibrary* from the Start menu). Its start page offers:

- **This computer** — pick a data directory (by default `%LOCALAPPDATA%\stlLibrary` on
  Windows) and **Open**. The app starts a server for that window on a free local port and
  stops it when the window closes.
- **Servers** — add `http://host:8770` to connect to a server elsewhere; the list is
  remembered.

The first time a library is opened you create its administrator account. *File → Switch
server…* (Ctrl+L) returns to the start page; Ctrl+R reloads. Inside the app, the detail pane
can show an archive's folder in the file manager.

### Server

```bash
stl-server --data-dir D:\STL_DB --bind 127.0.0.1:8770
```

Open `http://127.0.0.1:8770/`. On first start, create the administrator from the server's own
machine (in the browser, or `stl-server create-admin NAME` on a headless host; the password
comes from `STL_ADMIN_PASSWORD` or is asked for). Put the server behind an HTTPS reverse proxy
before exposing it beyond `127.0.0.1`.

A library can be open in one process at a time: a second server (or the desktop app's local
server) on the same data directory is refused.

### First steps

1. **Jobs → Add folder**: browse to your archive folder, set a path pattern if your folders
   follow one, and **Crawl**. Jobs run in the server, one at a time; the page can be closed.
2. **Library**: filter, search, edit; *Columns* chooses the table's columns, *Images* shows a
   grid. Select models for *Mass edit*, the *AI tagger* or *Archives* (rescan, thumbnails,
   sort files, delete files).
3. **Jobs → Update all** re-crawls every folder later, keeping the records of archives moved
   between folders and removing those of archives that are gone.

## Server configuration

`<data dir>/server.json` (all keys optional), then environment variables, then the command
line:

```json
{
  "bind": "0.0.0.0:8770",
  "roots": ["D:/STL"],
  "path_map": [{"server": "D:/STL", "client": "\\\\nas\\stl"}],
  "unrar": "C:/Program Files/WinRAR/UnRAR.exe",
  "pdfium": "C:/libs/pdfium.dll",
  "onnxruntime": "C:/libs/onnxruntime.dll",
  "web_dir": "C:/stlLibrary/web",
  "ai": {"llama_server": "...", "model": "...", "mmproj": "...", "gpu_layers": 99}
}
```

| Setting | Environment | Purpose |
|---|---|---|
| `--data-dir` | `STL_DATA_DIR` | Library data directory |
| `bind` / `--bind` | `STL_BIND` | Address and port (default `127.0.0.1:8770`) |
| `roots` | | Folders editors may browse and crawl (administrators: anywhere; crawled folders count too) |
| `path_map` | | How clients see the server's paths (for *Open folder* and *Copy path*) |
| `unrar` | `STL_UNRAR_PATH` | UnRAR for compressed `.rar` members (default: `unrar` on `PATH`) |
| `pdfium` | `STL_PDFIUM_PATH` | pdfium for PDF previews ([builds](https://github.com/bblanchon/pdfium-binaries), 7881) |
| `onnxruntime` | `ORT_DYLIB_PATH` | ONNX Runtime 1.17+ for AI image embeddings |
| `web_dir` | `STL_WEB_DIR` | The built web client |
| `ai.url` | `STL_AI_URL` | A running OpenAI-compatible model server for the AI tagger |

Library settings (thumbnails per model and size, crawler warnings, sidecars, file deletion
and sorting, AI temperature, …) live in `<data dir>/settings.json` and are edited on the
**Settings** page by administrators.

### AI features

- **AI tagger** — needs llama.cpp's `llama-server` and the Qwen2.5-VL GGUF model
  (`Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf` and `mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf`, from
  [ggml-org/Qwen2.5-VL-3B-Instruct-GGUF](https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF)).
  Put them in `<data dir>/ai/bin` and `<data dir>/ai/models` (the layout the earlier desktop
  app's AI setup created) or point `"ai"` at them. The server starts `llama-server` on first
  use and stops it after ten idle minutes.
- **AI image embeddings (CLIP)** — needs ONNX Runtime and the CLIP image encoder at
  `<data dir>/models/clip_image_encoder.onnx` (or `STLLIBRARY_CLIP_MODEL`). Export it with
  `optimum-cli export onnx --model openai/clip-vit-base-patch32 --task zero-shot-image-classification clip_onnx/`
  and copy `clip_onnx/vision_model.onnx`. Then run *Maintenance → Compute missing
  embeddings*; the AI method appears in *Find by Image* and *Duplicates*.

### Backups and restore

Administrators back up from *Maintenance → Backups*; backups are kept in
`<data dir>/backups`. To restore, stop the server (and close the desktop app) and run:

```bash
stl-server restore D:\STL_DB\backups\stlLibrary_backup_<time>.7z --data-dir D:\STL_DB
```

The files it replaces are moved to `pre-restore-<time>` in the data directory.

### Known limitations

Compared with the earlier desktop application, which could fall back to Python libraries:

- an archive the built-in readers refuse is skipped (its fingerprint falls back to its size
  and date) rather than read another way;
- images only Pillow decoded (AVIF, CMYK JPEG) get no thumbnail;
- custom presupport patterns must use syntax the Rust regex engine reads the same way
  (a crawl logs a warning and skips the presupport check when one does not);
- the AI model and the CLIP encoder are installed by hand (see above), not downloaded from
  the application; the interface is in English only.

## Path Patterns

Crawls, the mass editor and *Sort files* read and write folder paths with `{token}`
placeholders.

| Placeholder | Aliases | Maps to |
|---|---|---|
| `{source}` | | Distribution / shop name |
| `{creator}` | `{author}` | Creator / designer name |
| `{title}` | `{name}` | Model title |
| `{date}` | `{month}` | Release date (`YYYY-MM` or `YYYY`) |
| `{sculptor}` | | Sculptor |
| `{collection}` | `{category}` | Collection |
| `{subcollection}` | `{subcategory}` | Subcollection / variant |
| `{release}` | | Release / bundle name |
| `{sequence}` | | Position in a series |
| `{url}` | | Source URL |
| `*` | | Ignored segment (any value) |

`{token:n}` uses only the first *n* characters: `{creator:1}/{creator}/{title}` renders creator
`Sanix` as `S/Sanix/<title>`. For archives organised as
`/ArtistName/2024-01 Some Release/model_name.zip`, use `{sculptor}/{release}/{title}`.

## Project Structure

```
stl_database/
├── build.ps1, build.sh      # Build the web client, the server and the desktop app
├── Dockerfile               # Server image
├── rust/
│   ├── stl-core/            # The library: archives, metadata, rules, thumbnails, storage, crawler
│   ├── stl-server/          # HTTP + WebSocket API (axum), jobs, accounts, AI, backups
│   ├── stl-desktop/         # Desktop app (Tauri): launcher page and window on a server
│   └── stl-proc/            # Child processes that end with their parent
├── web/                     # Web client (React, TypeScript, Mantine, TanStack)
├── resources/               # Application icon
├── tools/create_ai_model.py # Regenerates the tiny CLIP stand-in used by the server tests
└── docs/                    # Migration plan and design notes
```

For users: [docs/user-guide.md](docs/user-guide.md). For installing a server (Linux,
Docker, Windows, HTTPS, configuration, backups): [docs/deployment.md](docs/deployment.md).
Developer notes are in [CLAUDE.md](CLAUDE.md) and
[docs/rust-migration-plan.md](docs/rust-migration-plan.md).

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-nc-sa/4.0/) (CC BY-NC-SA 4.0).

You are free to share and adapt this work for non-commercial purposes, provided you give appropriate credit and distribute any derivative works under the same license.
