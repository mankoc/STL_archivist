# Deploying StlArchivist

This guide is for whoever installs and runs StlArchivist: on one Windows PC, or as a server
that several people reach from their browsers or the desktop app. For using the program,
see the [user guide](user-guide.md).

- [How the pieces fit](#how-the-pieces-fit)
- [Option A: one Windows PC](#option-a-one-windows-pc)
- [Option B: a Linux server](#option-b-a-linux-server)
- [Option C: Docker](#option-c-docker)
- [Option D: a Windows server](#option-d-a-windows-server)
- [HTTPS and remote access](#https-and-remote-access)
- [Configuration reference](#configuration-reference)
- [Optional features](#optional-features)
- [Users and roles](#users-and-roles)
- [Moving an existing library](#moving-an-existing-library)
- [Backups, restore and upgrades](#backups-restore-and-upgrades)
- [Troubleshooting](#troubleshooting)

## How the pieces fit

| Piece | What it is |
|---|---|
| `stl-server` | The program that owns a library: database, thumbnails, crawls, jobs, users. Serves the web client and the API on one port (default `127.0.0.1:8770`). |
| Web client | The user interface, in `web/` beside the server (built from `web/` in the source). Any modern browser. |
| `stl-desktop` | The desktop app (Windows; Linux and macOS untested). Shows the web client in a window; can start a private server for a library on the same PC, or connect to a remote server. |
| Data directory | One folder per library: `models.db` (the model database), `thumbnails.lmdb/` (thumbnails), `settings.json` (library settings), `server.db` (user accounts), `server.json` (server settings), `backups/`. |

Archives stay where they are. The server reads them (and, when allowed, moves or deletes them)
at the paths it catalogued.

**One process per library.** A data directory can be open in only one server at a time (a
lock file, `library.lock`, enforces it). Stop one before starting another on the same library.

## Option A: one Windows PC

1. Run `StlArchivist_<version>_x64-setup.exe`. It installs for the current user; no
   administrator rights are needed.
2. Start **StlArchivist** from the Start menu.
3. Under **This computer**, keep or change the data directory (default
   `%LOCALAPPDATA%\stlLibrary`; pick an existing library's folder to open it) and click
   **Open**.
4. The first time, create the administrator account (name and a password of at least 8
   characters).

The program is installed in `%LOCALAPPDATA%\StlArchivist`; the default library folder is
`%LOCALAPPDATA%\stlLibrary` (the program's former name, kept so existing libraries open as
they are). Uninstalling removes only the program's own files, never the library, but you may
prefer a library folder of its own, on a drive with room for the
thumbnails.

The app starts its own server on a free local port and stops it when the window closes.
Nothing listens on the network. Jobs (crawls, maintenance) stop when the window closes; the
next crawl continues where it stopped.

To let other devices use the same library, run a server instead (options B–D) and connect
the desktop app to it under **Servers**.

## Option B: a Linux server

> The Linux build has not been tested yet. Report build or runtime errors.

### Build

Building needs Rust 1.93 or newer (rustup installs the current release) and Node.js 24. On
Debian or Ubuntu:

```bash
sudo apt update
sudo apt install -y build-essential pkg-config curl git
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source ~/.cargo/env
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt install -y nodejs

git clone <repository URL> stl_database
cd stl_database
./build.sh --server            # web client + rust/target/release/stl-server
```

### Install

```bash
sudo useradd --system --create-home --home-dir /var/lib/stllibrary stl
sudo mkdir -p /opt/stllibrary
sudo cp rust/target/release/stl-server /opt/stllibrary/
sudo cp -r web/dist /opt/stllibrary/web          # the server looks for web/ beside itself
```

`/var/lib/stllibrary/server.json`:

```json
{"bind": "127.0.0.1:8770", "roots": ["/srv/stl"]}
```

Use `"0.0.0.0:8770"` to listen on the network directly (only on a trusted LAN; see
[HTTPS](#https-and-remote-access)). `roots` are the folders editors may browse and crawl.

`/etc/systemd/system/stllibrary.service`:

```ini
[Unit]
Description=StlArchivist server
After=network-online.target
Wants=network-online.target

[Service]
User=stl
Group=stl
ExecStart=/opt/stllibrary/stl-server --data-dir /var/lib/stllibrary
Restart=on-failure
# More detail in the journal: STL_LOG=debug
Environment=STL_LOG=info

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R stl: /var/lib/stllibrary
sudo -u stl /opt/stllibrary/stl-server create-admin admin --data-dir /var/lib/stllibrary
sudo systemctl daemon-reload
sudo systemctl enable --now stllibrary
journalctl -u stllibrary -f        # the server's log
```

`create-admin` asks for the password (or reads `STL_ADMIN_PASSWORD`). It works only while the
library has no users yet; add more users in the web client afterwards.

**Permissions.** The `stl` user needs to read the archive folders. It also needs write
access to them for metadata sidecars (`METADATA.7z` per folder), *Sort files* and *Delete
files*. For a network share, mount it (for example with `cifs` and `uid=stl`) before the
service starts.

**RAR archives.** Stored (uncompressed) RAR members are read directly; compressed ones need
UnRAR: *Settings → Components* downloads it (RARLab's 7.23), or `sudo apt install unrar` (Debian *non-free*, Ubuntu *multiverse*).

## Option C: Docker

> The image has not been test-built yet.

The `Dockerfile` in the repository builds the server and the web client and includes pdfium
(PDF previews) and ONNX Runtime (AI embeddings).

```bash
docker build -t stllibrary-server .
docker run -d --name stl --restart unless-stopped \
  -p 8770:8770 \
  -v /srv/stl-data:/data \
  -v /srv/STL:/library \
  stllibrary-server
docker exec -it stl stl-server create-admin admin
```

Mount the archives read-only (`:ro`) if you do not want sidecars, sorting or deletion. Paths
inside the container are what the library records: crawl `/library/...`. UnRAR is not
included (it is not free software): download it in *Settings → Components* (it lands in the
data volume), or add it in a derived image and set `STL_UNRAR_PATH`.
The image's ONNX Runtime is the CPU build; for CLIP on an NVIDIA GPU, derive an image with
CUDA 12, cuDNN 9 and the `onnxruntime-linux-x64-gpu` build (`ORT_DYLIB_PATH` pointing at it),
and run it with `--gpus all` (NVIDIA Container Toolkit).

## Option D: a Windows server

Copy `stl-server.exe` and the `web` folder side by side (both are in the desktop app's
install folder, `%LOCALAPPDATA%\StlArchivist` by default, or in `rust\target\release` and
`web\dist` of a source build) and run:

```powershell
stl-server.exe --data-dir D:\STL_DB --bind 0.0.0.0:8770
```

To run it without anyone signed in, register it as a service with a service wrapper such as
[NSSM](https://nssm.cc) (`nssm install StlArchivist C:\StlArchivist\stl-server.exe --data-dir
D:\STL_DB`), or as a Task Scheduler task that starts at boot. Allow the port in Windows
Firewall. Run it under an account that can read (and, for sidecars, write) the archive
folders.

## HTTPS and remote access

The server speaks plain HTTP. On a trusted home network you can bind it to `0.0.0.0` and
open `http://<host>:8770/`. Anywhere else, keep it on `127.0.0.1` and put an HTTPS reverse
proxy in front. The proxy must pass WebSocket upgrades (live job progress uses
`/api/events`).

**Caddy** (automatic certificates):

```
stl.example.org {
    reverse_proxy 127.0.0.1:8770
}
```

**nginx:**

```nginx
server {
    listen 443 ssl;
    server_name stl.example.org;
    # ssl_certificate / ssl_certificate_key ...
    client_max_body_size 100m;          # image uploads
    location / {
        proxy_pass http://127.0.0.1:8770;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 1h;
    }
}
```

**First-run setup is refused through a proxy** and from other machines: create the first
administrator on the server itself (in a browser on that machine, or with
`stl-server create-admin`).

After five failed sign-ins, an account name is locked out for 15 minutes.

## Configuration reference

Settings come from defaults, then `<data dir>/server.json`, then environment variables, then
the command line (later wins).

| `server.json` | Environment | Command line | Default | Purpose |
|---|---|---|---|---|
| | `STL_DATA_DIR` | `--data-dir` | platform default¹ | The library's data directory |
| `bind` | `STL_BIND` | `--bind` | `127.0.0.1:8770` | Address and port to listen on |
| `roots` | | | `[]` | Folders editors may browse and crawl; libraries count too; administrators may browse anywhere |
| `path_map` | | | `[]` | `[{"server": "/srv/stl", "client": "\\\\nas\\stl"}]`: how clients see server paths (*Copy path*, *Open folder*); clients on the server's own machine see paths as they are |
| `unrar` | `STL_UNRAR_PATH` | | the downloaded component, else `unrar` on `PATH` | UnRAR tool |
| `pdfium` | `STL_PDFIUM_PATH` | | beside the server, then the data directory | pdfium library |
| `onnxruntime` | `ORT_DYLIB_PATH` | | beside the server, then the data directory | ONNX Runtime library |
| `components_mirror` | | | official sources | base URL serving the component downloads by file name |
| `web_dir` | `STL_WEB_DIR` | | `web/` beside the server | The built web client |
| `ai` | `STL_AI_URL` (for `ai.url`) | | `<data dir>/ai` | AI tagger model server, see below |
| `compute` | `STL_COMPUTE` | | `local` | `workers`: CLIP and the AI tagger run only on compute workers, see below |
| | `STL_LOG` | | `info` | Log detail (`error`, `warn`, `info`, `debug`) |

¹ Windows `%LOCALAPPDATA%\stlLibrary`, Linux `$XDG_DATA_HOME/stlLibrary` (usually
`~/.local/share/stlLibrary`), macOS `~/Library/Application Support/stlLibrary`.

**Library settings** (`<data dir>/settings.json`) are separate and edited in the web client
(*Settings → Library*, administrators): thumbnails per archive and size, parallel workers,
crawler warnings and presupport patterns, STL file lists, metadata sidecars, AI embeddings,
file deletion and trash folder, file sorting. The file is the same one the earlier desktop
application used.

Other command-line commands:

```text
stl-server create-admin NAME [--data-dir DIR]     first administrator (no users yet)
stl-server restore FILE.7z [--data-dir DIR]        restore a backup (server stopped)
stl-server openapi                                 print the API description
stl-server libraries [list] | relocate FOLDER NEW_ROOT [--apply] [--fix-case] [--data-dir DIR]
                                                   list libraries and their folders, or move a folder
                                                   (checks first; server stopped)
stl-server worker --server URL [--data-dir DIR]    compute worker for a remote server (token in
                                                   STL_WORKER_TOKEN; the desktop app starts it)
stl-server components status|install|remove [ID...] [--variant ID=V]... [--data-dir DIR]
                                                   the optional components of a folder, without
                                                   opening a library (JSON output)
```

The API is described at `/api/openapi.json` on a running server.

## Optional features

Each is enabled by making its files available; without them the rest works. On Windows x64
and Linux x64 the easiest way is *Settings → Components* (administrators),
which downloads pinned versions from their official sources into the data directory and
checks each file's SHA-256: llama.cpp build b9789 (`ai/bin/`; CPU, or CUDA 12.4 with its
runtime on Windows — Linux gets the CPU build), the Qwen2.5-VL model (`ai/models/`), the CLIP
encoder (`models/clip_image_encoder.onnx`, exported from `openai/clip-vit-base-patch32` by
Xenova/clip-vit-base-patch32), ONNX Runtime 1.22.0 (the data directory; CPU, DirectML on
Windows with DirectML 1.15.4 beside it, or CUDA on Linux) and pdfium 7881 (the data
directory). The page shows the GPUs found (Linux: the PCI display devices in sysfs; Windows:
an NVIDIA driver) and offers CUDA for an NVIDIA card and DirectML on Windows by default;
choosing another variant of an installed component replaces it (restart the server if ONNX
Runtime was already loaded). The server needs outbound HTTPS to github.com, huggingface.co
and api.nuget.org (DirectML) for this; set
`"components_mirror": "https://mirror.example/stl/"` in `server.json` to fetch the same file
names from your own mirror instead. Components the configuration points elsewhere are left
alone. Installing manually works as described below.

**PDF previews** (thumbnails of PDFs inside archives): pdfium from
[pdfium-binaries](https://github.com/bblanchon/pdfium-binaries) (build 7881): `pdfium.dll`,
`libpdfium.so` or `libpdfium.dylib`, beside the server, in the data directory, or set
`"pdfium"`.

**AI image embeddings** (*Find by Image* and *Duplicates* by AI similarity): ONNX Runtime
1.17 or newer (`onnxruntime.dll` / `libonnxruntime.so`, set `"onnxruntime"` if it is not
beside the server) and the CLIP image encoder at `<data dir>/models/clip_image_encoder.onnx`
(or `STLLIBRARY_CLIP_MODEL`). The encoder is the ONNX export of
`openai/clip-vit-base-patch32`'s vision model, the same file the earlier desktop app used:

```bash
mkdir -p <data dir>/models
curl -L -o <data dir>/models/clip_image_encoder.onnx \
  https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/d15189d7028b43f1d3e65039190477f6af591c2a/onnx/vision_model.onnx
```

Then run *Settings → Maintenance → Compute missing embeddings* once; new crawls embed their thumbnails
when *compute embeddings during crawl* is on in the library settings.

The encoder runs on a GPU when the loaded ONNX Runtime supports one for the GPUs found, and
falls back to the CPU otherwise (the log says `CLIP encoder runs on …`, and the components
card shows it once the encoder is loaded):

| Platform | GPU | ONNX Runtime build | Also needs |
|---|---|---|---|
| Windows | any DirectX 12 GPU | DirectML (`Microsoft.ML.OnnxRuntime.DirectML`) | `DirectML.dll` 1.15+ beside `onnxruntime.dll` |
| Linux | NVIDIA | `onnxruntime-linux-x64-gpu` (its `libonnxruntime_providers_*.so` beside it) | CUDA 12 and cuDNN 9 on the library path |
| Linux | AMD | a MIGraphX or ROCm build (AMD's packages) | ROCm |
| Linux | Intel | an OpenVINO build (Intel's packages) | OpenVINO, the GPU driver |

Only the first two are downloaded by the server; point `"onnxruntime"` at a vendor build for
the others. On a GPU, embeddings are several times faster (an RTX 3070 with DirectML: about
2 ms per image, against 18 ms on the CPU) and give the same vectors.

**AI tagger:** llama.cpp's `llama-server`
([releases](https://github.com/ggml-org/llama.cpp/releases): a CPU build, or CUDA for an
NVIDIA GPU) and the two model files from
[ggml-org/Qwen2.5-VL-3B-Instruct-GGUF](https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF):
`Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf` and `mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf`
(about 3.3 GB). Either use the layout the earlier desktop app's AI setup created —
`<data dir>/ai/bin/llama-server[.exe]` and `<data dir>/ai/models/` (a `variant.txt` with
`cuda` in `bin/` offloads to the GPU) — or point the server at them:

```json
{"ai": {"llama_server": "/opt/llama/llama-server",
        "model": "/opt/llama/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",
        "mmproj": "/opt/llama/models/mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf",
        "gpu_layers": 99}}
```

The server starts `llama-server` on the first request (loading takes from seconds on a GPU
to a minute or two on a CPU), and stops it after ten idle minutes or when it stops. To use a
model server that is already running (any OpenAI-compatible endpoint with vision), set
`{"ai": {"url": "http://127.0.0.1:8080"}}` instead.

### Compute workers: AI on another machine

A server on a small machine (a NAS, a mini PC without a GPU) can hand CLIP embeddings and the
AI tagger to a computer that has one. On that computer, open the server in the desktop app,
go to *Settings → Workers* and turn on **Use this computer for AI** (editors).
The desktop app then runs `stl-server worker`, which connects to the server (out from the
computer, over the same address and a WebSocket like the event stream, so it works behind
NAT and through the reverse proxy), and does the work with the components in a folder of
that computer. Under *This computer* the card lists them and downloads them — the same
choices as the server's *Settings → Components* (DirectML for CLIP on Windows, the CUDA AI
runtime for an NVIDIA card; the defaults follow the GPUs found) — and *Change folder…* picks
where they go (by default the local library's folder, if the app has opened one, else
`%LOCALAPPDATA%\stlLibrary\worker`). The worker pauses while components are installed and
starts again with them. The switch is remembered: the worker starts again whenever the app
connects to that server, and stops when the app closes. The card lists the connected
workers, what each does, and how many requests it answered.

Jobs, progress, the review of suggestions and the database stay on the server; only images
and prompts travel to the worker, and vectors and replies come back. A worker's CLIP encoder
is used only when it is the same model as the library's (the downloaded encoder, or the
server's own file, by SHA-256): vectors of another model would not match.

With a worker connected, the server always uses it. Without one, it depends on `compute`:

- `local` (default): the server's own ONNX Runtime and `llama-server`, when it has them.
- `workers` (`"compute": "workers"` in `server.json`, or `STL_COMPUTE=workers` for Docker):
  the server never runs the models itself. *Compute embeddings* and the AI tagger wait for a
  worker (and finish when one connects), crawls skip embeddings (compute them later), and
  *Find by Image* by AI similarity asks for a worker.

The worker's session is a new session of the user who turned it on, stored by the desktop
app (`servers.json` in its configuration folder); disabling that user, or turning the switch
off, ends it. Behind a reverse proxy, WebSocket upgrades must be passed on (they already are
for the event stream).

## Monitored folders

*Libraries*: the eye button (editors) monitors a library's folder. Every
*Check monitored folders every* minutes (*Settings → Library*, default 30) the server lists
the folder's archives and compares them with the library by size and modification date —
no archive is opened, so a check of an unchanged folder is cheap even on a NAS. When an
archive is new, changed or gone, it queues a crawl of the folder (shown in *Jobs* from the
user `monitor`; nothing is queued when nothing changed, or while a crawl of the folder or
*Update all* is waiting or running). The crawl adds and updates records as any crawl does;
records of archives that vanished are **marked missing** rather than deleted (an archive
that turns up in another library is matched by hash), and *Settings → Maintenance → Remove
missing archives* deletes the records of every missing archive. Checks run while the server
does; changes made while it was off are found at the first check after it starts. Changes
are picked up at the next check, not at once, which also works on network shares where file
notifications do not.

## Users and roles

| Role | Can |
|---|---|
| **viewer** | Browse, search, filter, download archives, Find by Image, Statistics, read job progress |
| **editor** | Also: edit models and lookups, add and remove images, crawl folders (within the roots), run maintenance and duplicate searches, AI tagging, sidecars, rescan, sort and delete files (when enabled in the library settings) |
| **admin** | Also: manage users, change library settings, browse all folders, back up, compact the database, delete all records |

Administrators add users in *Settings → Users* (name, password, role), change roles, reset
passwords, disable and delete accounts. Everyone can change their own password from the
user menu. Accounts live in `<data dir>/server.db`, apart from the library.

## Moving an existing library

The data directory is the whole library: copy it (with the server stopped), or use a backup.
Records store their archive paths relative to their **library folder** (a library is a name
and one or more folders, each with a root and a permanent id), in a form that is the same on
every operating system; the only machine-specific part is each folder's root.

**Same paths** (same machine, or the archives at the same paths): start the server on the
copied data directory. Done.

**Different paths** (another drive or mount point, a Linux server for a library catalogued on
Windows): start the server on the copied or restored data directory; the *Libraries* page shows
each folder that is not there as *root not found*. Click **Move** on each, choose the
folder where its archives now are, **Check** (every archive is looked up; found, missing and
found-with-another-letter-case are counted) and **Move**. Nothing is re-read: records, edits,
tags, thumbnails and embeddings stay. Moving from Windows to Linux, keep *adopt the letter
case found on disk* on, since Linux tells `Dragon.zip` and `dragon.zip` apart. On a headless
server:

```bash
stl-server libraries list --data-dir /srv/stl-data                                 # folders: id, name, root
stl-server libraries relocate "STL" /library/STL --data-dir /srv/stl-data            # check
stl-server libraries relocate "STL" /library/STL --apply --fix-case --data-dir /srv/stl-data
```

(with the server stopped: the command opens the library itself).

**From a version before libraries** (before 2026.43.7, or the PyQt desktop application): the
first start converts the library once — each crawled folder becomes a library, archives
outside every crawled folder get a library of their own (marked *made by the move to
libraries*), records of an archive catalogued twice under two spellings of its folder are
merged into one, and the database is rebuilt with relative paths. A backup is written first
(`backups/stlLibrary_backup_<time>_pre_libraries.7z`); the server refuses to convert without
it. The earlier versions cannot open the converted library.

**Without the database** (only the archives): crawl them. Folders with `METADATA.7z`
sidecars restore their records (titles, tags, lookups, thumbnails) from them instead of
starting from scratch.

## Backups, restore and upgrades

**Backups.** Administrators click *Settings → Backups → Back up now*. The server writes
`<data dir>/backups/stlLibrary_backup_<UTC time>.7z` with the database, the thumbnails and the
library settings, taken consistently while the server runs. Download backups from the same
card and keep copies elsewhere; delete old ones there too. User accounts (`server.db`) and
`server.json` are not in the backup: copy them yourself if needed.

**Restore.** Stop the server (and close the desktop app if it has the library open), then:

```bash
stl-server restore /path/to/stlLibrary_backup_<time>.7z --data-dir /var/lib/stllibrary
```

The files it replaces are moved to `pre-restore-<time>` in the data directory. Backups from
the earlier desktop application's *Backup / Restore* work too.

**Upgrades.** Stop the server, replace `stl-server` and the `web` folder (or run the new
installer), start it again. The database is upgraded automatically on start; make a backup
first.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "the library is open in another process: …" | Another server, the desktop app, or `restore` has the library open. Stop it. The message names the process. |
| Setup screen says it must run on the server | First-run setup is accepted only from the server's own machine and not through a proxy. Use a browser on that machine or `stl-server create-admin`. |
| The page loads but jobs never show progress | The proxy drops WebSocket upgrades on `/api/events`; see the nginx example. |
| "outside the library's folders" when adding a folder | Editors can only browse `roots` and libraries. Add the folder to `roots` in `server.json` (and restart), or let an administrator add it. |
| `.rar` archives have no thumbnails or fail | UnRAR is missing; download it in *Settings → Components*, install it, or set `unrar`. |
| No AI options | ONNX Runtime or the CLIP model is missing (embeddings), or `llama-server` / the model files are (tagger). *AI tagger* shows the reason. |
| Some archives are skipped or have no images | The built-in readers refuse a few unusual archives (the crawl log warns "cannot read archive"; each crawl tries them again), and CMYK JPEG and tiled ("grid") AVIF images are not decoded. |
| Thumbnails missing after moving the server | The `thumbnails.lmdb` folder was not copied with the database. |
