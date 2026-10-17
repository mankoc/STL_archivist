# Rust Migration Plan

**Status:** Phases 0 and 1 done (2026-10-17); Phase 2 done (2026-10-18) except PDF via pdfium; Phase 3 next. Written 2026-10-17.

**Goal:** move stlLibrary to Rust for **performance**, a **small native distribution** and a
**server / client** architecture like Jellyfin's: one server owns the library, and every user
interface is a client of its HTTP API, whether the server runs on the same machine or a remote
one.

The migration is **incremental**. A Rust core library grows underneath the existing PyQt6 app,
which keeps shipping at every step. The Rust server is then built on that core, the React web
client is built against the server, and a thin desktop shell comes last.

This plan supersedes `docs/server-migration-plan.md`: its "Qt-free core" becomes the Rust core,
and its FastAPI server becomes a Rust (axum) server.

---

## Decisions taken

| Topic | Decision |
|---|---|
| Goals | Performance, distribution (native binary instead of the ~84 MB PyInstaller bundle), server / client |
| Architecture | **Server-first, Jellyfin-style.** The server owns the database, thumbnails, files and jobs; clients talk to it over HTTP + WebSocket only. A local install runs the same server on `localhost` |
| Staging | Incremental: Rust core first, called from the PyQt app through **PyO3**; then server; then web client; desktop shell last |
| Client | **React + TypeScript** web client, served by the server; TypeScript API types generated from the Rust API (OpenAPI via `utoipa`) |
| Desktop app | Thin native shell (Tauri) around the web client: server picker, local-server launcher, native touches. No Tauri commands: all data goes through the HTTP API |
| Python app | Stays the shipping product until the web client reaches parity, then is **removed at once** (no period of shipping both; decided 2026-10-18) |
| Phase 4 | **As planned: the PyQt app becomes a shell over the Rust core.** Schema ownership and all database access move to Rust, and the PyQt dialogs, workers and crawler are rewired onto `stl_native` (decided 2026-10-18, over the cheaper option of building the core beside the PyQt app) |
| Accounts | **User accounts**, Jellyfin-style: per-user settings, read-only users, login UI and user administration (decided 2026-10-18) |
| Web UI library | **Mantine**, with TanStack Table + Virtual (decided 2026-10-18) |
| dHash | New Rust implementation with a new hash format; all hashes recomputed once (rule 2) |
| CLIP embeddings | Recomputed once with the Rust preprocessing; no bit-exact match required |
| PDF thumbnails | `pdfium-render` |
| RAR | Keep the external UnRAR tool |

### Why React for the client

About 20,000 of the app's 28,000 lines are PyQt UI, so the speed of rebuilding the UI decides
the speed of the migration. React has the most mature parts for this app's hardest screens: a
virtualised, sortable, editable 60k-row table (TanStack Table + Virtual), tree views for the
tri-state filter pane, a component library covering ~30 dialogs (Mantine or MUI), drag-and-drop
and popovers. Svelte or Vue would need more custom work for the table and the tree; a Rust
WebAssembly UI (Leptos, Dioxus) has the thinnest ecosystem. Generating TypeScript types from the
Rust API keeps the client and server from drifting, which recovers most of the benefit of a
single-language stack.

## Starting point

| Package | Lines | What it becomes |
|---|---|---|
| `app/ui` | 19,976 | React web client (TypeScript). Largest part of the work. |
| `app/utils` | 2,666 | Rust core modules (hashing, thumbnails, path patterns, string utils, CLIP, logging) |
| `app/crawler` | 2,651 | Rust core: archives, rules, metadata, sidecars, the pipeline |
| `app/services` | 1,320 | Rust core services, exposed by the server |
| `app/database` | 1,255 | Rust storage layer (rusqlite + sqlite-vec, LMDB via heed) |
| `app/core` | 228 | Rust settings |
| `tests` | 5,320 | Kept running against the Python app throughout; ported to `cargo test` per module |

Toolchain on the development machine: Rust 1.99 and Visual Studio 2022 Build Tools are
installed. **Node.js is not** and is needed from Phase 6 (web client).

---

## Target layout

```text
stl_database/
├── app/ …                     # Python app, shrinking phase by phase
├── rust/
│   ├── Cargo.toml             # workspace
│   ├── stl-core/              # library: storage, archives, hashing, crawler, services
│   ├── stl-py/                # PyO3 bindings → `stl_native` Python module (maturin)
│   ├── stl-server/            # axum HTTP + WebSocket API, serves web/ (Phase 5)
│   └── stl-desktop/           # Tauri shell around the web client (Phase 7)
└── web/                       # React + TypeScript client (Phase 6)
```

`stl-core` has no UI, no Python and no HTTP dependency. Long operations take the same three
callbacks the Python services already use (`progress`, `log`, `should_stop`), so they wrap
naturally as PyO3 functions now and as server jobs with WebSocket progress later.

## Crate choices

| Python today | Rust | Notes |
|---|---|---|
| SQLAlchemy + sqlite3 | `rusqlite` (bundled SQLite) | Plain SQL; the schema is small. Migrations in Rust from Phase 4 |
| sqlite-vec (loadable extension) | `sqlite-vec` crate, registered with `sqlite3_auto_extension` | Statically linked, no DLL to ship |
| lmdb (py-lmdb) | `heed` | See the LMDB single-owner rule below |
| zipfile | `zip` | Name decoding must match `zipfile` (CP437 vs UTF-8 flag) |
| py7zr | `sevenz-rust2` | Check BCJ2 / PPMd archive support against the library |
| rarfile + external UnRAR | keep calling `UnRAR.exe` / `unrar` | The external tool keeps the licence situation unchanged |
| Pillow | `image` (+ `fast_image_resize`) | AVIF decode needs `dav1d`/`libavif` (C); GIF animation kept as GIF |
| pymupdf (PDF first page) | `pdfium-render` | Ships a ~5 MB pdfium library |
| xxhash | `xxhash-rust` | Used from Phase 0 (archive fingerprint) |
| numpy (Hamming, matmul) | plain Rust + `rayon`; `matrixmultiply` or BLAS for embeddings | Hamming scans with `count_ones()` vectorise well |
| onnxruntime (CLIP) | `ort` with the DirectML / CUDA execution providers | |
| rapidfuzz / string utils | `strsim` + own Soundex | |
| QSettings (registry) | `winreg` on Windows, config file elsewhere | Same keys: `HKCU\Software\MrIxolite\stlLibrary` |
| logging (rotating gzip) | `tracing` + `tracing-appender` | Same log file location |
| FastAPI (planned) | `axum` + `tokio`, `utoipa` for OpenAPI | |
| PyQt6 | React + TypeScript (`web/`), Tauri shell | |

---

## Compatibility rules while two languages share the data

The Python app and the Rust core read and write **the same database and thumbnail store**
during the whole migration. Breaking any of these rules damages users' libraries.

1. **Bit-identical fingerprints.** `archive_hash` is xxh64 over the members sorted by name,
   each as UTF-8 name bytes plus the CRC as 4 little-endian bytes. If Rust decodes a single
   member name differently from `zipfile` / `py7zr` / `rarfile`, every affected archive looks
   changed and is re-extracted. Golden tests over a corpus of real archives gate Phase 2.
2. **dHash.** Pillow's `LANCZOS` resize to 9×9 and the `image` crate's Lanczos3 do not produce
   identical pixels, so hashes differ by a few bits. **Decided:** the Rust hash gets a new,
   recognisable format (for example a version prefix) and every hash is recomputed once.
   Comparisons must never mix the two formats; the existing "outdated format" handling
   (`parse_dhash` rejecting other lengths) is extended to the new marker. CLIP embeddings
   follow the same approach: `vector_store_info` records the new model/preprocessing id, which
   already recreates the table and triggers a one-time recompute.
3. **One owner for LMDB per process.** LMDB forbids opening the same environment twice in one
   process. py-lmdb and heed must therefore never both open `thumbnails.lmdb` inside the
   desktop app. When thumbnails move to Rust (Phase 3), *all* thumbnail access moves at once
   and `app/utils/thumbnailer.py` becomes a thin wrapper over `stl_native`.
4. **One owner for the schema.** Python (`db.py`) stays the only place that migrates the schema
   until Phase 4 hands it to Rust in one step. Rust code before that reads the schema as it is.
5. **SQLite concurrency.** Python and Rust use separate connections to the same file (WAL
   mode). Writes still serialise, so long Rust writes must commit in batches, as the crawler
   already does.
6. **Same on-disk formats.** Thumbnail keys (`R<id>` → `D<digest>`), `METADATA.7z` sidecar
   layout, `settings.json` keys and the `image_vec` / `vector_store_info` tables stay as they
   are. A user can switch between the Python and Rust builds at any point.
7. **Never two servers on one library.** From Phase 5 the server owns the data directory. The
   PyQt app and the server must not open the same library at the same time; the server takes a
   lock file in the data directory and the PyQt app refuses to open a locked library.

---

## Phases

Each phase ends with the Python app shipping, the full pytest suite green, and the new Rust
code covered by `cargo test` plus golden tests that compare its output with the Python
implementation on the same inputs.

### Phase 0: Workspace and bridge (small) — done 2026-10-17

- Cargo workspace under `rust/` (edition 2024); `stl-core` (library) and `stl-py` (PyO3 0.30,
  `abi3-py312`). The Python module is named **`stl_native`** so it does not clash with the
  `stl-core` crate. `python tools/build_native.py` runs `maturin develop --release` into the
  running interpreter's environment.
- `app/native.py` loads `stl_native` when installed and not disabled (`STL_NATIVE=0`); every
  ported function keeps its Python fallback. `main.py` logs the native status at startup.
- First port: the archive fingerprint behind `archive_hash` (`stl-core/src/fingerprint.rs`,
  called from `app.crawler.archive.members_fingerprint`). Golden tests in `tests/golden/`
  (5,000 random member lists plus real ZIP files) and a read-only pass over the reference
  library (all 51 ZIP, all 106 RAR, 150 sampled `.7z`) found no difference.
- `build_dist.py` builds the module before PyInstaller (`--no-native` skips it); the spec lists
  `stl_native`.
- The archive corpus with odd encodings and corrupt files was built in Phase 2a as
  generated archives (`tests/golden/test_archive_golden.py`), not checked-in files.

### Phase 1: Pure computation (small–medium) — done 2026-10-17

Stateless all-pairs searches, chosen by measured cost; arrays in, arrays out.

| Function (`stl_native`) | Core module | Used by | Reference library |
|---|---|---|---|
| `hamming_pairs` | `hamming.rs` (rayon, hardware popcount) | Duplicate Archives → Images, dHash | scan 13.9 s → 2.1 s; whole search 15.7 s → 3.9 s; identical pairs |
| `cosine_pairs` | `embedding.rs` (`gemm`, run-time AVX2/AVX-512) | Duplicate Archives → Images, AI embedding | scan 87 s → 29 s; whole search 66 s → 36 s; 19 of 1.66M pairs differ, all within 1e-6 of the threshold |
| `levenshtein_pairs` | `strings.rs` (banded DP, rayon) | Find Metadata Duplicates, lookup and author editors' *Merge similar* | 51k titles at distance ≤ 2: ~21 min (Python estimate) → 3.3 s; identical pairs |

- Long operations report progress and stop through `control::Control`; the binding's
  `run_job` polls it from the calling thread every 100 ms, so Python callbacks never run on
  Rust threads.
- `rust/.cargo/config.toml` targets x86-64-v2 (hardware popcount; the Hamming scan was 2x
  slower without it). `tools/build_native.py` runs Cargo from `rust/` so it applies.
- Duplicate aggregation (`model_pairs`) stays in NumPy: it takes under a second.

**Moved out of Phase 1:**

- **dHash computation (new format) → Phase 2.** The format changes because Rust image
  decoding differs from Pillow's; computing hashes in Rust while Python remains a fallback
  would let one library hold hashes from two algorithms. In Phase 2 Rust owns image decoding,
  so the switch and the one-time recompute happen once.
- **Path patterns, crawler rules, Soundex → Phase 4**, with the crawler that uses them. They
  run once per archive and are already fast; porting them earlier would add compatibility
  surface without a speed gain.

### Phase 2: Archives and images (medium, highest compatibility risk)

- Archive reading: listing, CRC fingerprint (`archive_hash`), member sizes, extraction of
  wanted files, nested-archive detection; RAR through the external tool.
- Image pipeline: decode (JPEG/PNG/WebP/AVIF/GIF/BMP/TIFF/PDF first page), resize, JPEG encode,
  animated-GIF passthrough.
- dHash in the new format (moved from Phase 1, rule 2): computed by Rust only, with a
  one-time recompute of every stored hash; the Python fallback stops producing hashes.
- Gate: golden `archive_hash` equality on the whole corpus **and** on a read-only pass over the
  reference library (every stored hash recomputed by Rust must equal the stored value).

Split into 2a (archive reading), 2b (image pipeline) and 2c (new dHash and recompute).

**2a: archive reading — done 2026-10-17.**

| Function (`stl_native`) | Core module | Python caller |
|---|---|---|
| `archive_members` / `archive_hash_file` | `archive/zip.rs`, `archive/sevenz.rs` | `_native_catalog` → `archive_hash`, `list_contents`, `list_member_sizes`, `list_stl_members`, `extract_archive_contents` |
| `read_archive_members` | same | `_read_members` → `extract_archive_contents`, `read_file` |

- Principle: return exactly what the Python reader returns, or raise. Unsupported cases
  (RAR, encrypted or non-deflate ZIP members, a local header whose name differs from the
  central directory, a ZIP size mismatch, an unparsable 7z) raise, and Python reads that
  archive. Rust must also fail wherever Python fails, so the two never disagree on which
  archives are readable.
- ZIP: a port of CPython 3.14 `zipfile` (`_EndRecData`, `_RealGetContents`, `_decodeExtra`,
  the `ZipFile.open` checks), including `NameToInfo` last-wins and the overlapped-entries guard.
  Members `zipfile` would skip are skipped.
- 7z: `sevenz-rust2` 0.24, decoding only the blocks holding wanted files. Its `BlockDecoder`
  walks `first_file .. first_file + num_unpack_sub_streams`. If an entry without a stream lies
  inside that range, the block's last files are silently dropped; such archives go to py7zr.
- Gate, reference library:
  - **Catalog:** every `.zip`/`.7z` archive (60,487). 60,377 match the stored hash. The other
    110 all match what Python computes today: 25 changed after their crawl, 73 once failed in
    Python and now read, and 6 near-empty `.7z` files raise in Rust and fall back to Python.
  - **Extraction:** a random 3,000-archive sample, every wanted member: all 3,000 identical
    to `read_batch` (29 GB of extracted bytes compared).
  - **Speed:** catalogs are ~50–100x faster from the file cache. Extraction is ~1.3x faster;
    it is bound by LZMA decompression and disk.
- Not done: nested-archive detection stays in Python until Phase 4 (it uses the catalog,
  which is native already).

**2b: image pipeline — done 2026-10-17.** `make_thumbnail` (`thumbnail.rs`), used by
`thumbnailer.make_thumbnail` with Pillow as the fallback.

- Crates: `image` 0.25 (PNG, GIF, WebP, BMP, TIFF; default features off), `jpeg-decoder`
  (DCT-scaled decode to ≥ 2x the target, like Pillow's `reducing_gap=2`; Pillow never used it
  here because `convert` loaded the full image first), `fast_image_resize` (Lanczos3, SIMD),
  `jpeg-encoder` (quality 75, 4:2:0, optimised Huffman tables, as Pillow's `optimize=True`).
- Same visible behaviour as Pillow: `Image.thumbnail` sizes computed from the source size
  (not the DCT-scaled one, which rounds differently), alpha dropped, EXIF orientation
  ignored, animated GIFs kept up to 60 frames / 2 MB.
- Reference library, 1,022 images: sizes identical; mean pixel difference 1.2/255 (max 4.9);
  1.7x faster overall (PNG 2.2x, JPEG 1.4x). JPEG entropy decoding dominates: DCT scaling
  saves only ~15%, and `zune-jpeg` was not much faster. libjpeg-turbo would be faster but
  needs a C toolchain in the build; not worth it yet.
- Fallback to Pillow: AVIF (needs dav1d, a C library), CMYK JPEG, anything undecodable.
- Deviation, deliberate: 16-bit greyscale PNGs are scaled to 8 bits; Pillow clipped them to white.
- Not done: PDF first page through pdfium. PyMuPDF still renders it, and the PNG goes through
  the native thumbnail. pdfium needs a bundled `pdfium.dll` (pdfium-binaries), which is
  packaging work best done with the server build (Phase 5).

**2c: dHash format 2 — done 2026-10-17.** `dhash` / `dhash_many` (`dhash.rs`).

- Same method as Pillow's hash (8-bit BT.601 luma, Lanczos3 to 9×9, strict comparison, same
  bit layout); computed after shrinking to fit 512 px so an original and its thumbnail
  agree. Stored as `"2:" + 32 hex`; older hashes have another length and count as missing.
- Measured on 2,000 reference thumbnails against Pillow's hash: re-encode at JPEG q50 mean
  1.1 vs 1.2 bits, 2/3 resize 0.4 vs 0.4, 3% crop + brightness p95 19 vs 20; random pairs
  p1 42 vs 42. Exact float area averaging (with or without rounding) was measured and
  rejected: less robust to re-encoding (q50 within 10 bits 99.0%), weaker separation (p1 38).
- Native only (no Python fallback writes hashes). Without the native core, searches use the
  stored current hashes and Find by Image offers embeddings only.
- One-time recompute: `services.maintenance.recompute_dhashes`, batches of 1,024. Hashing a
  batch takes ~0.3 s; reading thumbnails (~10 ms each in id order on the reference hard disk)
  dominates, about an hour for 365k images. Reading in blob-key order could cut that if
  it ever matters.

### Phase 3: Thumbnail store (small–medium)

- `heed`-based store with the existing key layout, dedup by xxh3-128, `delete_thumbnails`
  batching, compaction and the dedup sweep.
- `app/utils/thumbnailer.py` becomes a wrapper over `stl_native` in one change (rule 3).

**Done 2026-10-18.** `stl-core/src/thumbstore.rs` (heed 0.22, LMDB mdb.master), exposed as
`stl_native.ThumbStore`.

- `thumbnailer.py` picks a backend once per open store: `_NativeBackend` when the native core
  has `ThumbStore`, else `_LmdbBackend` (py-lmdb, kept as the fallback until Phase 8). All
  access goes through the backend, so the two never hold one store in one process.
- Same keys byte for byte (`R<id>` → `D<xxh3-128 BE>[slot]`, legacy bare ids), the same
  collision slots, and the same map growth: start at max(10 GB, file + 2 GB), grow by 2 GB on
  MDB_MAP_FULL. A `RwLock` keeps every transaction out while the map resizes, which LMDB
  requires.
- The dedup sweep and compaction stay in Python as loops over batch calls
  (`convert_legacy`, `orphan_blobs`, `delete_keys`, `copy_compacted`), so their progress
  callbacks stay on the calling thread.
- Opening never writes: the main database is opened through a read transaction.
- Gate: golden tests write with one backend and read with the other, and compare raw key
  dumps. On the reference store, 30,000 random thumbnails and the full statistics matched.
- Speed is not the point here; py-lmdb is already C. The point is that the server needs the
  store without Python.

### Phase 4: Storage and the crawler (large)

- Rust storage layer on `rusqlite`: models, lookups, images, STL files, crawled folders,
  filter cache, `image_vec` via sqlite-vec; schema migrations move from `db.py` to Rust here
  (rule 4).
- The four-phase crawler pipeline in Rust, including move detection, sidecar import/export and
  CLIP embedding during the crawl (`ort`). `CrawlerWorker` becomes a QThread wrapper over a
  `stl_native.crawl(config, sink)` call.
- Services move with it: `library_query`, `moves`, `maintenance`, `editing`, `lookups`,
  `file_deletion`, `model_summary`, `image_similarity`, `image_duplicates`.
- Path pattern parsing, crawler rules (`detect_presupport`, `detect_warnings`,
  `detect_scale`) and Soundex (moved from Phase 1).
- From here the Python app is a PyQt shell over the Rust core.

### Phase 5: Server (medium)

`stl-server` on axum, the product's centre from here on:

- REST API over the core services, documented as OpenAPI with `utoipa`; the TypeScript client
  types are generated from it.
- Jobs (crawl, maintenance, duplicate scans, embeddings) run in the server with progress,
  logs and cancellation over a WebSocket; library-change events go over the same socket so open
  clients refresh.
- Thumbnails at `/thumb/<digest>`: the store is content-addressed, so responses are cacheable
  forever (`Cache-Control: immutable`).
- **Authentication** (user accounts or tokens) and HTTPS through a reverse proxy, because the
  server may be reachable remotely.
- **Server-side files:** folders to crawl are browsed through the server; images dropped in the
  client are uploaded; "open in file manager" is offered only when the client sees the same
  path (local server or a configured path mapping), otherwise "download archive".
- Library lock (rule 7); configuration through environment and a settings file; Docker image.

### Phase 6: Web client (largest)

React + TypeScript in `web/`, served by the server, built against the API while the PyQt app
keeps shipping, in this order:

1. Browser view: filter tree with include/exclude and counts, search, virtualised table
   (TanStack Table + Virtual), thumbnail pane with drag-and-drop upload, detail/edit drawer.
2. Crawler page with live log and progress.
3. Settings.
4. Lookup editors and the mass editor.
5. Maintenance, duplicate finders, Find by Image, statistics, sidecars, backup/restore.
6. AI tagger (llama.cpp server stays an external process, run by the server).

### Phase 7: Desktop shell (small)

Tauri app that hosts the web client: connect to a remote server or start a bundled local one on
`localhost`, remember servers, tray icon, native menus and shortcuts, open local folders when
the path is visible. No business logic and no Tauri commands for data.

### Phase 8: Retire Python (small)

Once the web client has parity: remove `app/`, PyInstaller and the PyO3 bindings; ship the
server (installer and Docker image) and the desktop shell.

## Effort and risk overview

| Phase | Relative effort | Risk | Main risk |
|---|---|---|---|
| 0. Workspace and bridge | Small | Low | Windows build for Python 3.14 |
| 1. Pure computation | Small–medium | Low–medium | New dHash format rollout (rule 2) |
| 2. Archives and images | Medium | **High** | `archive_hash` name decoding (rule 1), AVIF/PDF/RAR |
| 3. Thumbnail store | Small–medium | Medium | LMDB single owner (rule 3) |
| 4. Storage and crawler | Large | Medium–high | Schema hand-over, behaviour parity of the crawler |
| 5. Server | Medium | Medium | Auth and remote-file semantics |
| 6. Web client | **Largest** | Medium | ~20k lines of UI behaviour to reproduce |
| 7. Desktop shell | Small | Low | |
| 8. Retire Python | Small | Low | |

Phases 0–3 deliver most of the performance goal with the current UI. The server arrives with
Phase 5 and the new UI and native distribution with Phases 6–7.

## Open decisions

None at the moment; the three open questions were decided on 2026-10-18 (see the table above).

## Verification per phase

- `pytest` green against the Python app with the native module installed and with it absent.
- `cargo test` for every ported module.
- Golden tests: Python vs Rust on the same inputs (exact equality where rule 1 or 6 applies).
- Benchmarks on the reference library (read-only copy) before and after, recorded in the
  changelog.
