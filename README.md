# StlArchivist

**StlArchivist** manages local collections of 3D-printable model archives. It scans folders of
`.zip`, `.7z` and `.rar` files, extracts metadata and thumbnails, and stores everything in a
library database so you can browse, search, tag and de-duplicate your collection without
unpacking anything.

It is a server (`stl-server`, Rust) with a web client (React) that runs in any browser, and
a small desktop app (`stl-desktop`, Tauri) that shows the same client in its own window and
can open a library on your computer without setting up a server.

**Guides:** [using StlArchivist](docs/user-guide.md) · [deploying a server](docs/deployment.md) ·
[the HTTP API](docs/api.md) ·
and in the interface, the **?** button: searchable in-depth help on every feature

Version: 2026.43.20

> The earlier PyQt6 desktop application lives on in the repository's history (and on its
> own branch). This version opens its data directory and, since 2026.43.7, converts it once
> to libraries with relative paths (backing it up first); the earlier application cannot
> open the converted library. Before that conversion it opened the same data directory
> (`stlLibrary.db`, `thumbnails.lmdb/`, `settings.json`). Since 2026.43.10 the model
> database is `models.db`; a data directory holding only `stlLibrary.db` opens as a new,
> empty library (crawl the folders again).

## Features

- **Archive scanning** — crawls folders of archives in parallel using CRC-based
  fingerprints (no decompression needed to notice what changed); moved archives keep their
  record, metadata and thumbnails, even when moved between libraries
- **Status bar** — the running job's progress and the number of archives catalogued, on
  every page
- **Libraries** — named collections of one or more folders (one library over several disks);
  records store paths relative to their folder, in a form that is the same on Windows, Linux
  and macOS, so a folder moves to another disk, mount point or machine (and from Windows to
  Linux) by pointing it at its new place — no re-crawl, nothing lost; folders can sit inside
  one another
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
- **Monitored folders** — crawled automatically when their archives change (checked by size
  and date at an interval); archives that vanish are marked missing
- **Maintenance** (*Settings* tabs) — verify archives, find moved and duplicate records, search a folder for
  missing archives, remove the records of missing archives, re-check warnings, recompute image hashes and embeddings, prune images,
  compact the database, rescan archives, regenerate thumbnails, delete files to a trash
  folder, sort files into a folder tree by pattern
- **Metadata sidecars** — a `METADATA.7z` per archive folder lets a new library restore its
  records without re-crawling; *Clean sidecars* rewrites stale ones and deletes orphans
- **Backups** — database, thumbnails and settings in one `.7z`; restore with
  `stl-server restore`
- **Users** — viewer, editor and administrator roles; per-user preferences
- **Statistics** — totals, rankings and the distribution of any column
- **Model viewer** — the 3D models inside an archive (STL, OBJ, PLY, 3MF, Lychee, CHITUBOX)
  in the browser, with the renderer of [MeshPeek](https://github.com/mankoc/meshpeek)
  (WebGPU or WebGL2): a folder tree of the files, several shown together, per-file colour,
  material and wireframe, a view cube; in the desktop app, **Open in MeshPeek** when it is installed (found
  on the `PATH` or in Program Files, or chosen in *Settings → Account*)

## Installation

### Windows installer

The [Releases](../../releases) page has `StlArchivist_<version>_x64-setup.exe`: the desktop
app with the server and the web client. It installs for the current user and needs no
administrator rights (Windows 10 and 11 ship the WebView2 runtime it uses).

Releases are built by GitHub Actions: push a tag named after the version
(`git tag v2026.26.0 && git push origin v2026.26.0`) and the *Windows installer* workflow
publishes `StlArchivist_<version>_x64-setup.exe` on a release. It can also be run by hand from
the Actions tab.

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

Or double-click **`build-installer.bat`**: it finds Node.js (on `PATH`, in `NODE_HOME` or its
usual folders), installs the Tauri CLI when missing, builds the installer and shows where it
is. `build-installer.bat --no-pause` does the same unattended (for a scheduled task; exit
code 0 on success).

```bash
# Linux / macOS
./build.sh             # everything (the desktop app needs WebKitGTK on Linux)
./build.sh --server    # only the web client and the server, for a headless host
```

The model viewer's WebAssembly is committed (`web/src/viewer/wasm`), so building needs no
WebAssembly tools. After changing `rust/stl-viewer` or the vendored MeshPeek crates,
regenerate it with `npm run gen:viewer` (in `web/`; needs `rustup target add
wasm32-unknown-unknown` and `cargo install wasm-bindgen-cli --version 0.2.129`).

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

Start `stl-desktop` (or *StlArchivist* from the Start menu). Its start page offers:

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

1. **Libraries → Add library**: browse to your archive folder, set a path pattern if your folders
   follow one, and **Crawl**. Jobs run in the server, one at a time; the page can be closed.
2. **Library**: filter, search, edit; *Columns* chooses the table's columns, *Images* shows a
   grid. Select models for *Mass edit*, the *AI tagger* or *Archives* (rescan, thumbnails,
   sort files, delete files).
3. **Libraries → Update all** re-crawls every library later, keeping the records of archives moved
   between libraries and removing those of archives that are gone.
4. Or let the server keep up: the eye button on a library **monitors** it. The server
   compares it with the library every 30 minutes (*Settings → Library*) by file size and
   date, and crawls it when archives were added, changed or removed. Removed archives are
   marked missing; *Settings → Maintenance → Remove missing archives* deletes their records.

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
| `roots` | | Folders editors may browse and crawl (administrators: anywhere; libraries count too) |
| `path_map` | | How clients see the server's paths (for *Open folder* and *Copy path*) |
| `unrar` | `STL_UNRAR_PATH` | UnRAR for compressed `.rar` members (default: the one *Settings → Components* downloads, else `unrar` on `PATH`) |
| `pdfium` | `STL_PDFIUM_PATH` | pdfium for PDF previews ([builds](https://github.com/bblanchon/pdfium-binaries), 7881) |
| `onnxruntime` | `ORT_DYLIB_PATH` | ONNX Runtime 1.17+ for AI image embeddings |
| `web_dir` | `STL_WEB_DIR` | The built web client |
| `ai.url` | `STL_AI_URL` | A running OpenAI-compatible model server for the AI tagger |

Library settings (thumbnails per model and size, crawler warnings, sidecars, file deletion
and sorting, AI temperature, …) live in `<data dir>/settings.json` and are edited on the
**Settings** page by administrators.

### AI features

Administrators can download everything below, and UnRAR, from *Settings → Components*
(Windows x64 and Linux x64; checked by SHA-256, UnRAR on Windows by RARLab's signature). Where a GPU helps, the page offers it and
picks the default from the GPUs it finds: the AI tagger's runtime with CUDA (Windows, NVIDIA),
and ONNX Runtime with DirectML (Windows, any DirectX 12 GPU) or CUDA (Linux, NVIDIA; CUDA 12
and cuDNN 9 must be installed). To install by hand:

- **AI tagger** — needs llama.cpp's `llama-server` and the Qwen2.5-VL GGUF model
  (`Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf` and `mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf`, from
  [ggml-org/Qwen2.5-VL-3B-Instruct-GGUF](https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF)).
  Put them in `<data dir>/ai/bin` and `<data dir>/ai/models` (the layout the earlier desktop
  app's AI setup created) or point `"ai"` at them. The server starts `llama-server` on first
  use and stops it after ten idle minutes.
- **AI image embeddings (CLIP)** — needs ONNX Runtime and the CLIP image encoder at
  `<data dir>/models/clip_image_encoder.onnx` (or `STLLIBRARY_CLIP_MODEL`): `onnx/vision_model.onnx`
  from [Xenova/clip-vit-base-patch32](https://huggingface.co/Xenova/clip-vit-base-patch32).
  Then run *Settings → Maintenance → Compute missing embeddings*; the AI method appears in *Find by
  Image* and *Duplicates*. The encoder runs on a GPU when the ONNX Runtime build supports
  one for the GPUs found (DirectML; CUDA, MIGraphX, ROCm or OpenVINO on Linux), else on the
  CPU; the components card shows which.
- **Compute workers** — a server on a machine without a GPU can let a computer with one do
  the embeddings and tagging: in the desktop app, *Settings → Workers → Use this
  computer for AI*; the same card downloads the models into a folder of that computer you
  choose. With `"compute": "workers"` (or `STL_COMPUTE=workers`) the server never
  runs the models itself and its jobs wait for a worker. See the deployment guide.

### Backups and restore

Administrators back up from *Settings → Backups*; backups are kept in
`<data dir>/backups`. To restore, stop the server (and close the desktop app) and run:

```bash
stl-server restore D:\STL_DB\backups\stlLibrary_backup_<time>.7z --data-dir D:\STL_DB
```

The files it replaces are moved to `pre-restore-<time>` in the data directory.

### Known limitations

Compared with the earlier desktop application, which could fall back to Python libraries:

- an archive the built-in readers refuse is skipped (its fingerprint falls back to its size
  and date) rather than read another way;
- CMYK JPEG and tiled ("grid") AVIF images get no thumbnail (other AVIF images do);
- custom presupport patterns must use syntax the Rust regex engine reads the same way
  (a crawl logs a warning and skips the presupport check when one does not);
- the interface is in English only.

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
├── build-installer.bat      # Builds the Windows installer unattended (wraps build.ps1)
├── Dockerfile               # Server image
├── rust/
│   ├── stl-core/            # The library: archives, metadata, rules, thumbnails, storage, crawler
│   ├── stl-server/          # HTTP + WebSocket API (axum), jobs, accounts, AI, backups
│   ├── stl-desktop/         # Desktop app (Tauri): launcher page and window on a server
│   ├── stl-proc/            # Child processes that end with their parent
│   ├── meshpeek-core/       # MeshPeek's model loaders (vendored; see its README.md)
│   ├── meshpeek-render/     # MeshPeek's wgpu renderer (vendored)
│   └── stl-viewer/          # The web client's model viewer: meshpeek-render in WebAssembly
├── web/                     # Web client (React, TypeScript, Mantine, TanStack)
├── resources/               # Application icon (the STL Archivist emblem; copies in rust/stl-desktop/icons, rust/stl-desktop/ui and web/public)
├── tools/create_ai_model.py # Regenerates the tiny CLIP stand-in used by the server tests
├── tools/api_doc.py         # Writes docs/api.md (the API reference) from web/openapi.json
└── docs/                    # Migration plan and design notes
```

For users: [docs/user-guide.md](docs/user-guide.md). For installing a server (Linux,
Docker, Windows, HTTPS, configuration, backups): [docs/deployment.md](docs/deployment.md).
Developer notes are in [CLAUDE.md](CLAUDE.md) and
[docs/rust-migration-plan.md](docs/rust-migration-plan.md).

## License

This project is licensed under the [Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-nc-sa/4.0/) (CC BY-NC-SA 4.0).

You are free to share and adapt this work for non-commercial purposes, provided you give appropriate credit and distribute any derivative works under the same license.
