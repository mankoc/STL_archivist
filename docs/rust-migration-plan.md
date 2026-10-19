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

**Steps** (each one committed with the app working and the tests passing in both modes):

| Step | Content | Python side afterwards |
|---|---|---|
| 4a | Crawler rules, path patterns, Soundex in Rust | `rules.py`, `path_pattern.py`, `string_utils.soundex` call the native functions (Python fallback kept) |
| 4b | Storage foundation: `rusqlite` connection with the same pragmas, sqlite-vec, and the schema migrations from `db.py` | `db.py` asks the native core to migrate before SQLAlchemy connects (rule 4 moves here) |
| 4c | Read services: `lookups`, `model_summary`, `library_query` (table rows, facet counts, search) | services return the same rows, computed natively |
| 4d | Write services: `editing`, `moves`, `file_deletion`, helpers' get-or-create | same signatures |
| 4e | `maintenance`, `image_similarity`, duplicate loaders, `vectors` | same signatures |
| 4f | Dialogs: every direct `session.query` in `app/ui` goes through a service (about 300 call sites, ~25 files), dialog by dialog | no ORM in `app/ui` |
| 4g | Crawler pipeline in Rust (metadata inference, upsert, move detection, sidecars, embeddings via `ort`); `CrawlerWorker` wraps `stl_native.crawl` | crawler is a wrapper |
| 4h | SQLAlchemy and the Python storage code removed; the Python fallbacks for ported code go with them, so the native core becomes required | PyQt shell |

The Python fallbacks stay until 4h. A step's golden tests compare the native result with
the Python implementation it replaces, as in earlier phases.

**4a: done 2026-10-18.** `rules.rs`, `path_pattern.rs`, `strings::soundex`; Python's
`detect_presupport`, `detect_warnings`, `explain_warnings`, `detect_scale`, `parse_path` and
`soundex` call them.

- `rules::MemberPath` reproduces Windows `pathlib` for member names (drive prefix, dropped `.`
  and empty segments, Python 3.14 `suffix`/`stem`).
- **Custom presupport patterns stay on Python.** fancy-regex and `re` disagree on some
  syntax without raising (`[[:alpha:]]`, `(?<name>…)`, `\x{41}`, `(?(1)a|b)`), so only the
  built-in defaults go native. Settled in 4g-1: patterns are validated when saved, and
  only syntax both engines read the same is accepted (portable patterns run natively).
- `parse_path` returns fields in Python's dict order (dialogs show them in that order) and
  caches compiled patterns. Release dates: ASCII digits only; Python's `int()` also takes
  other Unicode digits.
- Gate: no differences on the reference library. That covers 484,744 `parse_path` calls
  (4 patterns over every archive path, relative and absolute), the rules on the member
  lists of 2,998 archives, and Soundex of 53,841 names. `parse_path` is 2.5x faster than
  Python.
- Not ported: `apply_extracted_to_model` (needs the database; 4d).

**4b: done 2026-10-18.** `stl-core/src/storage` (rusqlite 0.40, bundled SQLite; sqlite-vec
0.1.9 registered as an auto-extension, the version Python uses). **The schema is owned by
the native core from here (rule 4):** `db.init_db` calls `stl_native.migrate_database` for
file databases. The Python steps stay as the fallback, and in-memory test databases use
them.

- `storage::migrate` repeats `init_db`'s seven steps in order, each in its own transaction:
  renames, dhash affinity, `create_all`, vec0 table and legacy embedding move, missing
  columns plus extra indexes plus `ANALYZE`, `scale_id` → `model_scales`, and `models_view`.
- The DDL is not retyped. `tools/gen_schema.py` compiles it from `models.py` with
  SQLAlchemy's SQLite dialect into `storage/schema_sql.rs`, together with `db.ENSURE_INDEXES`,
  `db.MODELS_VIEW_SQL` and the vec0 DDL, so `sqlite_master` holds byte-identical SQL.
  `.gitattributes` pins that file to LF, because CRLF would change the stored SQL.
  `tests/golden/test_schema_golden.py` fails if the file is stale. **After editing
  `models.py`, run `python tools/gen_schema.py`.**
- Python passes the embedding model ID and dimension, and whether its own sqlite3 can load
  sqlite-vec. Without that, the vector step is skipped exactly as in Python.
- The bundled SQLite has STAT4, so its `ANALYZE` also writes `sqlite_stat4`. The migration
  drops that table to keep the file identical to a Python-migrated one.
- Gate: five generated databases (fresh, the old category/author schema, an INTEGER dhash
  column, legacy embeddings with the right and with a wrong model) and a copy of the
  reference database. Migrated by each side, they agree on `sqlite_master`, every column
  declaration and every row (355,643 vectors, 882,264 STL file rows).
- **Two SQLite copies in one process.** rusqlite's SQLite and Python's are separate
  libraries. On POSIX, both holding one database file in one process can corrupt it
  (closing a file in one drops the other's locks). `migrate` closes before SQLAlchemy
  connects, so 4b is safe everywhere. From 4c, native database access while SQLAlchemy is
  open is limited to Windows until 4h removes SQLAlchemy.

**4c: done 2026-10-18.** `storage::library` exposed as `stl_native.LibraryDb` (a pool of
connections). It serves `library_query.table_ids` / `page_rows`,
`filter_tree.filter_tree_data` (the browser's filter pane, first moved out of
`_FilterTreeWorker` into a Qt-free service), `lookups.usage_counts` and
`model_summary`. `app/database/native_db.library()` returns it only on Windows and for a
file database (see the two-SQLite note above); otherwise the Python services run.

- The predicates reproduce SQLAlchemy's SQL, quirks included. Exclusions are `NOT IN` /
  `NOT LIKE`, so NULLs are excluded too. `lower()` is applied to the column (ASCII-only in
  SQLite), while values are lowered the Unicode way. `LIKE` patterns are not escaped.
  Folder and file-type values use `winpath::WinPath`, a port of `pathlib.WindowsPath`.
- Before the comparison was possible, the order became deterministic in both
  implementations: table ties are ordered by id, and tags/scales are joined in id order.
- Gate: `tests/golden/test_library_golden.py` compares a random 400-model library with
  hostile values across 300 table queries, all page rows and 122 filter trees. On the
  reference library (copy), 108 table queries, all 60,593 rows, 27 filter trees and the
  usage counts were identical. Native vs Python: page rows 4x faster (0.3 s vs 1.3 s for
  every row), filter trees 2.9x (5.4 s vs 15.5 s for 27), table ids 1.2x.
- The unfiltered filter tree is still cached as JSON by the Python worker
  (`filter_cache.py`).

**4d: done 2026-10-18.** `storage::edit`: lookup get-or-create, `apply_cell_edit(s)` (every
browser cell field, with the tag/scale/subcollection rules), `relocate`, `delete_model`,
`merge_into_missing` and `prune_unused_lookups`. All of them work on ids inside one
`BEGIN IMMEDIATE` transaction (`LibraryDb.with_tx`).

- In use now: the browser's cell edits (`_CellEditRunnable`), and `prune_unused_lookups`
  when its session has not started a transaction. The Python services take an ORM session
  and leave the commit to the caller, so callers that mix them into larger ORM transactions
  (editor dialog, AI tagger, crawler, maintenance merges) switch over with their own steps
  (4f, 4g). The Rust functions are ready and golden-tested for them.
- ORM behaviour kept:
  - `updated_at` changes only when a column of the model row really changes.
  - Deletes rely on the schema's `ON DELETE CASCADE`, plus Python's TEMP trigger for
    `image_vec`, which `storage::open` now adds to every native connection.
  - `str.strip()` / `str.lower()` semantics and Python's `int()` rules for the sequence
    field (sign, underscores).
  - `archive_folder_for` uses `PurePosixPath`.
- Input the port does not reproduce (dates or numbers in non-ASCII digits) raises
  `ValueError` before anything is written, and the edit runs in Python.
- Gate: `tests/golden/test_edit_golden.py` applies 60 random edit batches, plus moves,
  deletes and pruning, to two copies of a library, one through the Python services and one
  natively, and compares every table.

**4e: done 2026-10-18.**

- `storage::vectors` ports `count`, `read_all`, `store` and `prune_orphans`. `read_all`
  decodes the chunk shadow tables with the same version guard, and falls back to a row scan.
  It also ports `find_similar_models` (KNN in a `MATERIALIZED` CTE, best image per model,
  stable order) and `load_model_vectors`.
- `storage::maintenance` ports `verify_archives` (stats files in parallel, in chunks, so
  Stop returns the checked prefix) and `find_duplicate_entries`. For `prune_images` it
  supplies the models over the limit, the excess ids per batch and the image deletes; the
  thumbnail deletes stay in the Python loop, before the database delete, as before.
- Python uses them through `native_db.library()`. The Python queries now order by id, so
  their results are deterministic. Vector writes (`vectors.store`) run inside the embedding
  worker's ORM transaction and keep the Python path until that worker moves.
- Found while comparing: SQLAlchemy's bulk `update()` applies `onupdate`, so clearing the
  missing flag of reappeared files also sets `updated_at`. The native code does the same.
- Gate: `tests/golden/test_maintenance_golden.py`. On the reference library (copy), every
  result was identical: verify (60,593 rows), duplicates (557 rows), `read_all` (355,643
  vectors), `load_model_vectors` and nine KNN searches. Warm-cache verify takes 0.78 s
  natively against 1.42 s in Python; vector reads and KNN take about the same time, since
  both already ran in C.

**4f-1: done 2026-10-18 (lookup editors).** The tag, category, collection, subcollection,
scale, author and creator-profile dialogs no longer touch the ORM. They call
`services/lookup_admin.py`, which runs natively (`storage::admin`) or falls back to the
dialogs' former ORM code, moved verbatim. The ORM's side effects are now explicit:
- deleting a category, collection or subcollection nulls the models' reference and sets
  `updated_at`, and a collection takes its subcollections with it;
- deleting a tag or scale only unlinks it;
- bulk creator renames set `updated_at`;
- the tag-merge target is matched with SQL `lower()` against a Unicode-lowered name, now
  `ORDER BY id`.

The author rename no longer asks its "merge?" question inside an open transaction.
`tests/golden/test_admin_golden.py` runs 150 random editor operations both ways and
compares every result and both databases.

**4f-2: done 2026-10-18 (model editors).** The editor dialog, the AI tagger and the mass
editor use `services/model_edit.py`, backed natively by `storage::model_edit`:
- the completion lists and the model snapshot;
- the editor save;
- the AI commit, with its fill-only-empty rules;
- the mass editor's choices, paths and save.

The mass save ports `apply_extracted_to_model`, the `{token}` context (with Windows
`pathlib` `folder` / `filename`), every manual field, lookup get-or-create, scale and tag
rules, find & replace, and trimming, all in one transaction.
- **Find & replace runs natively only where Rust and `re` agree:** a literal find
  (case-insensitive only for an ASCII find text) and a replacement without backslashes.
  Python expands `\1`, `\n` and `\g<0>`. Regex mode, and a tag list that would link one
  tag twice (an IntegrityError in Python), raise before writing and run in Python. 4h
  settled it: regex mode runs natively with Python's template rules, and a duplicate tag
  is linked once.
- Lists the ORM loaded in unspecified order (a model's scales, tags, images, STL files)
  are now explicitly in id (or file name) order in both implementations.
  `apply_extracted_to_model`'s parentless subcollection lookup is `ORDER BY id`.
- `tests/golden/test_model_edit_golden.py`: 120 random steps of reads, editor saves, AI
  commits and mass saves on two copies. Native refusals leave the database untouched;
  the final databases are compared.

**4f-3: done 2026-10-18 (browser, statistics, sort).** Three new services, each backed
natively:
- `services/library_stats.py` (`storage::stats`): the Statistics and Column
  Distribution dialogs.
- `services/browser_data.py` (`storage::browser`):
  - `lookup_cache` lists and the tag delegate's completions;
  - the info panel's url / description / creator profile, and the thumbnail list;
  - dropped images (records first, thumbnails next, then paths or deletes for failures);
  - image delete, Add Tag, Delete from DB (one transaction);
  - the sort dialog's sources and its final path update.
- `file_deletion.delete_archives` no longer takes a session: it reads the rows, moves or
  unlinks the files, then deletes the records whose file step worked in one transaction.

Behaviour changes, in both implementations:
- Rankings break count ties by value or lookup id. Before, the order of ties was unspecified.
- **Bug fix:** the sort dialog now updates `archive_folder` with `archive_path`. Before,
  moved archives kept their old folder in folder views until the next crawl.

`render_path_pattern` accepts a dict of fields as well as a model.
`tests/golden/test_browser_golden.py` runs 80 random reads and writes on two copies and
compares every statistic. On the reference library (60,593 models) every read is
identical. `sort_sources` for 2,000 models takes 4.6 ms natively against 66 ms in Python.
Still in Python: the browser's re-crawl and rescan actions, which run the crawler's upsert
and move with it in 4g.

**4f-4a: done 2026-10-18 (viewers).** `services/model_views.py`, backed natively by
`storage::views`, serves these dialogs:
- the archive contents list and compare;
- the archive browser (folders, and the cells of a folder with or without subfolders,
  matched with the same `LIKE`);
- the image grid;
- Find Duplicate Archives' metadata rows;
- Find by Image's dHash rows.

`model_summary` and `find_similar_models` accept `session=None`, so Find by Image
opens no session itself. Models, images, tags and scales come in id order, and STL files
by name.
- `tests/golden/test_views_golden.py` covers every read.
- On the reference library every read is identical. `duplicate_rows` takes 0.13 s
  against 5.0 s in Python, and `grid_models` for 500 models takes 5 ms against 242 ms.

Moved to 4g, because they are crawler work: the Update Crawled Folders reconciliation, the
crawler tab, and the browser's rescan and thumbnail jobs.

**4f-4b: done 2026-10-18 (content duplicates, folder lists).** `services/content_duplicates.py`
is backed natively by `storage::duplicates`. It covers:
- the STL file-list overlap scan of Find Duplicate Archives (content), cancellable through
  `run_job`; ties are ordered by ids;
- the result popups' thumbnail ids;
- the image scans' reads (`image_duplicates` with `session=None`; `_present_images` is now
  in image id order);
- the crawled-folder list and its remove;
- the sidecar dialogs' folder list.

`export_folder_metadata` accepts `session=None`, so the sidecar dialog and
`sidecar_exporter` open no session. The export itself stays in Python and is ported with
the sidecars in 4g.
- `tests/golden/test_content_duplicates_golden.py` covers the reads, the scan at five
  thresholds, the dHash scan through both paths, and stopping.
- On the reference library every result is identical. The STL scan takes 1.3 s against
  2.9 s in Python.

**4f-4c: done 2026-10-18 (Database Maintenance dialog).** The dialog's remaining database
work goes through `services/maintenance.py`, backed natively by `storage::maintenance`:
- embedding targets (after pruning orphan vectors) and storing vectors;
- the warning re-check's reads and writes;
- location rows for Find Moved;
- the moved-record merge (one transaction);
- record removal with lookup pruning;
- free-space bytes for Compact.

Two bug fixes, in both implementations:
- Remove Records now deletes the removed models' thumbnails. They used to stay in LMDB.
- Search for missing archives now sets `archive_folder` with `archive_path`
  (`browser_data.set_archive_paths`).

`tests/golden/test_maintenance_dialog_golden.py` covers every function, including vectors.

**4f-4d: done 2026-10-18 (crawler tab folder list, cell edits). 4f is complete.**
- The crawler tab's folder list, add and remove go through `content_duplicates`
  (`add_crawled_folder` is native too).
- The browser's cell-edit runnable calls `editing.apply_cell_edits`, which holds the
  native path and the Python fallback.

ORM use left in `app/ui` is crawler work only, so it moves to 4g with the crawler:
- `browser_jobs.RescanWorker` and `ThumbnailGeneratorWorker`;
- the browser's re-crawl preview and apply;
- the Update Crawled Folders cross-folder reconciliation and deferred deletions.

**4g sub-steps.** Each one is committed on its own:
- 4g-1: custom-pattern policy;
- 4g-2: metadata inference;
- 4g-3: sidecar export / import;
- 4g-4: the crawl's database phases (diff, early moves, upsert, missing / moved
  reconciliation);
- 4g-5: the `stl_native.crawl` orchestrator, with embeddings via `ort`;
- 4g-6: rescan, thumbnail generation, re-crawl and Update Crawled Folders on top of it.

**4g-1: done 2026-10-18 (custom presupport patterns).** The policy: a custom pattern
runs natively only if it is *portable*, meaning it uses only syntax that `re` and
fancy-regex read the same way, case-insensitively. That syntax is:
- printable ASCII literals;
- `.`, `\d`, `\D`, `\A`, `\Z`, and escaped special characters;
- classes without nesting, set operations or a leading `]`;
- `(...)`, `(?:...)` and lookarounds;
- `* + ? {n} {n,} {n,m}`, optionally lazy;
- `^` and `$`.

`rules.portable_pattern_error` (Python) and `rules::portable_pattern` (Rust) implement the
same grammar. Rust translates `$` to `(?=\n?\z)` and `\Z` to `\z`, so `$` keeps `re`'s
"end or before a final newline" meaning.

Configuration refuses a non-portable pattern on save and says why; it checks with both
validators when the native core is loaded. A non-portable list saved earlier keeps running
in Python (through the crawl host) until Phase 8.
`tests/golden/test_portable_patterns_golden.py` checks 4,000 random patterns: both
validators accept the same ones, and every accepted pattern flags the same members in both
rule sets. The test names probe case folding (`ſ`, the Kelvin sign, `İ`, `ß`) and `$` before
a final newline.

**4g-2: done 2026-10-18 (metadata inference).** `stl-core/src/metadata.rs` ports
`extract_metadata` (path inference, `.url` contents, `info.json`) and
`archive.find_url_file` (`.url` members, then the folder, then `*-links` / `*_links`
folders). `metadata.extract_metadata` calls it and passes only the `.url` and `info.json`
members across. Python semantics are reproduced explicitly:
- `\b` is a boundary of `[\p{L}\p{N}_]`, `\d` is `\p{Nd}`, and `\s` is `str.isspace()`;
- `str.strip`, `str.splitlines`, and `bytes.decode` with `errors="ignore"` / `"replace"`;
- Windows `pathlib` `parts`, and case-insensitive `relative_to`;
- `sorted(glob)` on lowercased names.

`pyjson.rs` writes JSON exactly as Python's `json.dumps(indent=2)` does, including
`repr(float)`. 4g-3's sidecars use it.

The port leaves these cases to Python:
- `info.json` that `serde_json` rejects (Python also reads `NaN`), that is not an object
  (Python raises), or that has non-text values where Python would store them as they are;
- a `.url` search that would have to open a RAR archive (RAR moves to Rust with the
  crawler).

`tests/golden/test_metadata_golden.py` covers 3,000 random paths (Unicode digits and marks,
CJK years, UNC and relative roots), and `.url` placement and `info.json` variants on real zip
files. Gate on the reference library: path inference for all 60,593 archives and full
extraction of 2,000 sampled zip / 7z archives, with no differences and no fallbacks.

**4g-3: done 2026-10-18 (METADATA.7z sidecars).** `storage::sidecar` ports
`export_folder_metadata` and `import_folder_metadata`. `folder_metadata` uses them when
called with `session=None`, as the crawler, the sidecar dialog and `sidecar_exporter` now
call it.

The export:
- writes the same `metadata.json`, in `json.dumps(indent=2)` layout, dict order and
  `repr(float)` (`pyjson.rs`), and the same thumbnail members;
- reads thumbnails from the native `ThumbStore` that the Python thumbnailer holds
  (`thumbnailer.native_store()`), keeping one LMDB owner per process;
- stores thumbnails uncompressed (7z COPY; JPEGs gain nothing from LZMA2), which made it
  8.7x faster than py7zr on reference data;
- runs in one read connection.

The import:
- plans every record before writing, and refuses anything where Python would raise,
  store a non-text value or read the JSON differently;
- inserts the records in one transaction;
- writes the thumbnails in one LMDB transaction.

Behaviour changes, in both implementations:
- models, images, tags and scales in id order, and STL files by name;
- imported records get `archive_folder` (they had none).

`tests/golden/test_sidecar_golden.py` compares:
- exports, with and without thumbnails;
- imports of Python-written and native-written sidecars into two empty databases,
  including the stored thumbnails;
- 32 malformed sidecars (identical results, or a native refusal that writes nothing).

Gate: 30 reference folders (301 models, 1,248 real thumbnails, read through a read-only copy
of the live store) export identically.

**4g-4: done 2026-10-18 (the crawl's database phases).** `storage::crawl` ports the crawl's
database phases:
- Phase 1's stored-hash lookup;
- Phase 2's diff (unreadable warnings, hash backfill, size / mtime updates, early moves by
  hash);
- Phase 4's upsert, in one transaction per batch with a savepoint per archive and the
  consecutive-failure stop;
- collecting missing records (with a parallel existence check), move reconciliation,
  marking records missing, and the crawled-folder record.

`CrawlerWorker` uses them when `native_db.library()` is available and the thumbnail store
is native (or closed).

Python still computes what needs its semantics or libraries, and passes the results in:
- metadata, rule results, path-pattern matches with their parsed dates and numbers;
- precomputed thumbnails and dHashes, folder-image thumbnails, and CLIP vectors.

The upsert replays the ORM's operation order. SQLAlchemy flushes a mapper's INSERTs before
its DELETEs, and SQLite reuses the highest rowid, so new image rows are inserted before old
or failed ones are deleted, and image ids match.

**Behaviour change (both implementations):** the crawler flushes before applying the path
pattern. Before, whether a pattern could overwrite a release, sculptor or collection just
set from `info.json` or a force field depended on SQLAlchemy autoflush timing, which in turn
depended on the per-batch lookup cache. Now "fill only empty fields" always holds.

`tests/golden/test_crawl_golden.py` runs a three-step crawl scenario through both
implementations over an identically rebuilt tree at one path, and compares databases,
thumbnails and signals after every step. The scenario covers:
- info.json tags and release, folder images, a failed thumbnail, a pattern;
- forced values, a low-compression warning and an unreadable archive;
- a changed archive, an early move, a deletion, a new archive, and a forced rescan.

Gate: two real library folders (38 archives, 122 thumbnails) crawl to identical
databases and stores.

Still to do in 4g:
- 4g-5: the orchestration itself (hashing, extraction, thumbnails, embeddings) in Rust;
- 4g-6: the browser's rescan, thumbnail generation and re-crawl, and the Update Crawled
  Folders reconciliation, on top of these phases.

**4g-6: done 2026-10-18 (archive jobs; no ORM left in `app/ui`).**
- `services/archive_jobs.py`, backed natively by `storage::jobs`, takes over these writes:
  - Generate Thumbnails: replacing a model's images (its row operations follow the ORM
    order);
  - Rescan: applying the rescanned values;
  - Update Crawled Folders: the cross-folder reconciliation and the deferred-deletion
    clean-up.

  The workers still extract archives and build thumbnails in Python.
- `CrawlerWorker.run()` is split into `_reset_state` and `_process_batch`.
- The browser's Recrawl applies changes through the new `CrawlerWorker.recrawl(paths)`, a
  forced rescan through the crawler's own phases (native when available), one worker per
  folder. It uses the real crawler configuration; before, a default `CrawlerConfig()`
  carried no UnRAR path. Its preview reads through `model_views`.

`tests/golden/test_archive_jobs_golden.py` compares every job on two database copies.
An offscreen smoke run did a crawl, Generate Thumbnails, Rescan and Recrawl on copies of
real archives, all through the native paths.

What is left of SQLAlchemy in `app/ui`: engine-level actions (Compact Database's
`get_engine`, Clear Database), which go with 4h.

**4g-5a: done 2026-10-18 (RAR in Rust).** `archive/rar.rs` ports what `rarfile` 4.2 returns:
- **The catalog:** RAR 2.9 / 3 / 4 and RAR5 headers, with `rarfile`'s stop rules (header CRC
  error, EOF, an unknown RAR5 block, encrypted headers). Names are decoded the same way:
  RAR3's compressed-Unicode names, then UTF-8, then UTF-16LE, then cp1252 with replacement.
  Directories and old file versions are dropped.
- **Member reads:** stored members are read directly; compressed ones through
  `UnRAR.exe p`, the tool `rarfile` uses. Data with a wrong size or CRC is left out, as
  `rarfile`'s read raises for it.

These are left to the Python reader:
- SFX archives, volumes and comments;
- malformed headers, and time fields `rarfile` would reject;
- encrypted members, BLAKE2-only members, and member names UnRAR would read as a pattern.

`archive.py` routes `.rar` to it (`_native_catalog`, `_read_members` with `_unrar_tool`).
`tests/golden/test_rar_golden.py` builds RAR5 archives with `Rar.exe` (stored, compressed,
solid, Unicode names, directories, empty files, comments, encrypted headers and data,
volumes, extra times, BLAKE2), and compares catalogs and reads with `rarfile`, or checks the
native refusal. WinRAR 7 no longer writes RAR4, so RAR4 is covered by the gate.

Gate: all 106 `.rar` archives of the reference library give identical catalogs; every
stored `archive_hash` is reproduced; 36 sampled archives read identically. The catalog
reads take a few milliseconds, against 6 s through `rarfile`.

**4g-5b: done 2026-10-18 (the crawl in Rust).** `stl-core/src/crawler.rs` runs the whole
pipeline:
- listing;
- pre-crawl sidecar import;
- per batch: hashing (parallel, reusing stored hashes), diff and early moves, extraction
  and thumbnail precomputation (parallel), and the upsert;
- sidecar export, move reconciliation, missing records, the crawled-folder record and
  lookup pruning.

`CrawlerWorker.run()` calls it through `LibraryDb.crawl` when the native database and
thumbnail store are available. Recrawl keeps the Python orchestration over the native
database phases.

**Requests to the host.** What the core cannot do as the Python app does becomes a
`crawler::Request` to its `Host`:
- archives the native readers refuse;
- Pillow-only images, and PDF pages (PyMuPDF);
- CLIP vectors;
- metadata and rules the native ports leave to Python, and Unicode-digit parses;
- sidecars the native port refuses.

The PyO3 host queues requests and events and serves them on the calling Python thread
(woken by a condition variable), so Python callbacks still never run on Rust threads.
`_NativeCrawlServices` answers them with the functions the Python pipeline uses, so a
native crawl gives the Python crawl's results. A host without Python (the server) answers
`Response::None` for now; pdfium and `ort` will answer there later (Phase 5).

**Behaviour change (both implementations):** a non-recursive crawl no longer treats the
folder's own `METADATA.7z` as an archive.

`tests/golden/test_crawl_golden.py` runs its scenario in two modes against the ORM crawl,
and compares databases, thumbnails and signals after each step:
- the whole native crawl;
- Python orchestration over the native database phases.

The scenario now also covers a PDF page and a CMYK JPEG, both answered by the host. Gate:
three real library folders, including one of RAR archives (57 archives, 127 thumbnails),
crawl to identical databases and stores.

**4h-1: done 2026-10-18 (test databases on disk).** Every test now gets its own database
file (`conftest.library_db`) instead of an in-memory database, so the native core serves it
as it serves the app. `tests/factory.py` builds and reads test data with plain `sqlite3`;
4h-3 moves the remaining ORM-built fixtures to it.

`native_db.session_clean` now also refuses a session that has flushed changes it has not
committed: a native connection cannot see them, and native reads used to miss them there.

**4h-2/3: done 2026-10-18 (SQLAlchemy removed; the native core is required).**

- `app/database/models.py`, `helpers.py`, the engine and sessions are gone. `db.init_db`
  only migrates the file (in-memory databases are refused) and opens the thumbnail store;
  every read and write goes through `native_db.library()`, on every platform. With one
  SQLite copy left in the process, the POSIX restriction on native access ends.
- `rust/stl-core/src/storage/schema_sql.rs` is the schema's source of truth;
  `tools/gen_schema.py` and the schema golden test are removed.
- `app/native.py`: `module()` raises `RuntimeError` when `stl_native` is missing, and
  `STL_NATIVE` is no longer read. Python implementations remain only for input the port
  refuses (archive readers, Pillow thumbnails, non-portable rule patterns, metadata edge
  cases), reached through the crawl host or a `ValueError` fallback.
- The services lost their Python branches; the golden tests that compared them (admin,
  archive jobs, browser, content duplicates, crawl, edit, library, maintenance, model edit,
  schema, sidecar, views) are removed. The thumbnail store's py-lmdb backend and its golden
  test are removed too: `ThumbStore` is the only backend. The remaining golden tests cover
  functions that keep a Python implementation.
- Decisions settled: Unicode digits in dates and sequences are parsed natively
  (`pyunicode.rs`, Python's `unicodedata` 16.0 table); regex Find & Replace in the mass
  editor uses the native regex dialect (fancy-regex) with Python's replacement-template
  rules (`\1`, `\g<name>`, `\n`); a tag listed twice is linked once. The crawler's
  `ParseDate` / `ParseInt` host requests are gone.
- Additional 4h work: admin operations (`compact`, `clear_all`, the filter cache, dHash
  targets and writes, the `archive_folder` backfill) moved to `storage::maintenance`.
- Tests build data with `tests/factory.py` (plain `sqlite3`) and call the services.
  Vectors are written and read through `LibraryDb`, because plain `sqlite3` has neither
  sqlite-vec nor the per-connection delete trigger.
- Dependencies dropped: SQLAlchemy, lmdb, sqlite-vec (Python package).

### Phase 5: Server (medium) — done 2026-10-18

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

Phase 5 runs in steps:

| Step | Scope |
|---|---|
| 5a | `stl-server` crate: configuration, library lock, read-only library API (table ids and rows, filter tree, model detail, thumbnails), OpenAPI |
| 5b | User accounts and authentication: users, roles (admin, editor, read-only), sessions or tokens, per-user settings |
| 5c | Write endpoints: cell edits, model editor, lookup editors, mass editor, deletes, image upload |
| 5d | Jobs over a WebSocket (crawl, maintenance, duplicate scans, embeddings) with progress, logs and cancellation; library-change events; native PDF (pdfium) and CLIP (ort) so a crawl needs no Python |
| 5e | Server-side files (folder browsing, archive download, path mapping), Docker image |

**5a: done 2026-10-18.**

- `rust/stl-server` (axum 0.8, tokio, utoipa 5): `Library::open` takes the lock, migrates
  the database and opens the thumbnail store; handlers run database and store work on the
  blocking pool (`Library::run`). Configuration: defaults, then `<data_dir>/server.json`
  (`{"bind": ...}`), then `STL_DATA_DIR` / `STL_BIND`, then `--data-dir` / `--bind`. The
  default data directory is the desktop app's platform default; the default address is
  `127.0.0.1:8770` (no authentication yet, so localhost only).
- Routes: `GET /api/system/info`, `POST /api/models/ids` (filter, search, sort → ids in
  display order), `POST /api/models/rows` (at most 2,000 ids), `GET /api/models/{id}`,
  `POST /api/filter-tree`, `GET /api/images/{id}/thumbnail` (308 to the content address),
  `GET /api/thumb/{digest}` (`Cache-Control: immutable`), `GET /api/openapi.json`.
  The filter crosses as `{include: {facet: [values]}, exclude, has_value, no_value, ids}`
  with ids for lookup facets and `{year, month}` for dates; filter-tree counts are keyed by
  facet name (the core's `sculptor_id` kinds are renamed).
- `stl_core::storage::pool::Pool` replaces the bindings' own pool, and
  `ThumbStore::digests` / `blob` expose content addresses.
- **Rule 7:** `stl_core::lock::LibraryLock` holds an OS file lock on `library.lock` for the
  owner's lifetime (released by the OS on a crash); `library.owner` names the owner for the
  refusal message, because Windows locks are mandatory. The server and the desktop app
  (`main.py`, `stl_native.LibraryLock`) both take it, so a second app instance on the same
  library is refused too.
- Verified with the release binary on a crawled library: ids, rows, detail, thumbnails
  (JPEG, immutable, redirect), scoped filter tree, a second server and the desktop app
  refused, and the lock free again after the server was killed.

**5b: done 2026-10-18 (accounts).**

- Accounts live in `<data_dir>/server.db` (`stl-server/src/accounts.rs`), not in the
  library database, so the library schema stays as the desktop app expects (rule 6):
  `users` (name unique case-insensitively, Argon2id hash, role, disabled), `sessions`
  (SHA-256 of a 32-byte random token, sliding 30-day expiry) and `user_settings`
  (JSON values, 64 KB each).
- Roles are ordered `viewer` < `editor` < `admin`; every library route needs a session
  (`auth::CurrentUser`, from `Authorization: Bearer` or the `stl_session` cookie:
  HttpOnly, SameSite=Strict, `Secure` behind an HTTPS proxy). Write routes (5c) will
  require `editor`, user administration requires `admin`. Thumbnails are now
  `Cache-Control: private`.
- First run: `POST /api/auth/setup` creates the first administrator, only while no user
  exists and only from a loopback peer without proxy headers, so a reachable server cannot
  be claimed remotely. The server is served with `ConnectInfo` for that check.
- Routes: `GET /api/auth/status`, `POST /api/auth/setup|login|logout`,
  `PUT /api/auth/password`, `GET|POST /api/users`, `PATCH|DELETE /api/users/{id}`,
  `GET /api/me/settings`, `PUT|DELETE /api/me/settings/{key}`.
- Rules: the last enabled administrator can be neither demoted, disabled nor deleted; a
  password change or disabling ends the user's sessions; five failed sign-ins lock a name
  out for 15 minutes; unknown names cost the same hash check as known ones.

**5c: done 2026-10-18 (editing).**

- `api/edit.rs` (editor role): `PATCH /api/models/cells` (browser cell edits),
  `GET|PUT /api/models/{id}/editor`, `GET /api/editor/choices|subcollections`,
  `POST /api/models/mass-edit` (the server parses the path pattern per model, so the plan
  carries the pattern instead of results; `manual` fields are applied in the desktop's
  order), `POST /api/models/delete` (library records and thumbnails; files stay),
  `POST /api/models/{id}/images` (multipart; thumbnails at the library's
  `max_thumb_size`, the native decoder only, so AVIF and CMYK JPEG are refused until
  Phase 8 drops Pillow) and `DELETE /api/images/{id}`.
- `api/lookups.rs`: the lookup editors (`/api/lookups/{kind}` list, add, info, rename,
  delete, merge), tag merge and unused-tag cleanup, creator names (`POST /api/authors`
  with `rename` / `merge` / `delete`) and creator profiles.
- Sidecars: `sidecars.rs` mirrors `sidecar_exporter.py` (1.5 s debounce, one folder at a
  time on the blocking pool, `metadata_sidecar_enabled` / `metadata_thumbnails` from the
  library's `settings.json`, pending folders exported at shutdown). Cell edits and editor
  saves refresh them as in the desktop app; mass edits and image changes do too.
- Gate: a cell edit through the release server wrote `METADATA.7z`, and the desktop app
  restored the edited title and tags from it into a fresh library.

**5d-1: done 2026-10-18 (jobs and events).** 5d is split: 5d-1 runs jobs, 5d-2 answers the
crawl host's PDF and CLIP requests natively.

- `jobs.rs`: one job at a time in submission order (SQLite has one writer, as in the
  desktop app): `crawl`, `verify_archives`, `recompute_dhashes`, `prune_images`,
  `compact_database` (admin). Each has a state (queued, running, done, failed,
  cancelled), a phase, `done` / `total` (published every 500 ms from a
  `stl_core::control::Control`), a summary or error, and its last 2,000 log lines.
  Cancelling stops a running job at its next checkpoint (what it finished stays) or drops
  a queued one.
- A crawl job builds `CrawlConfig` from the library's `settings.json` with the desktop's
  keys and defaults (presupport patterns included, with the legacy-default upgrade), the
  folder's stored pattern unless one is given, and the UnRAR tool from `server.json` /
  `STL_UNRAR_PATH` or `unrar` on `PATH`. Its host (`ServerHost`) answers every request with
  `Response::None`, so archives the native readers refuse get the fallback fingerprint,
  and Pillow-only images and PDFs get no thumbnail. Embeddings are off. A crawl
  with non-portable custom presupport patterns logs that presupport warnings are skipped.
- Routes: `GET|POST /api/jobs`, `GET|DELETE /api/jobs/{id}`, `GET|POST
  /api/crawled-folders`, `POST /api/crawled-folders/remove`, and `GET /api/events`: a
  WebSocket of `ServerEvent` JSON frames (`job`, `job_log`, `library_changed`, `resync`
  when a slow client missed events). A middleware announces `library_changed`
  (`models` / `lookups`) after every successful write; finished jobs announce their kind.
- Gate: a server crawl job and the desktop crawl of the same folder (zip and 7z;
  info.json, .url, nested archive, presupported STL, GIF) gave identical models, tags,
  scales, images, perceptual hashes and thumbnail bytes.

**5d-2: done 2026-10-18 (native PDF and CLIP).**

- `stl_core::clip::preprocess` ports `image_embedding._preprocess` (full-size decode,
  shorter side to 224 with Python's round-half-even, Catmull-Rom bicubic, centre crop,
  CLIP normalisation). `tests/golden/test_clip_golden.py` compares it with Python through
  `stl_native.clip_preprocess`: mean difference under 0.01, tensor cosine above 0.9999.
- `stl-server/src/tools.rs` loads both libraries at runtime, so the server runs without
  them (the feature is then off, as a missing package turns it off in the app):
  - `Embedder`: ONNX Runtime through `ort` 2.0.0-rc.14 with `load-dynamic`, `api-17`
    (runtime 1.17 or newer). It is found through `onnxruntime` in `server.json`,
    `ORT_DYLIB_PATH`, or beside the server or in the data directory. The model is found as
    `model_path` finds it, and `pooler_output` is preferred as in Python. Vectors are
    L2-normalised and stored under the same `EMBED_MODEL_ID`.
  - `PdfRenderer`: pdfium through `pdfium-render` 0.9 (`pdfium_7881`). It renders page 1
    at 2× on white, as `pdf_first_page_to_image_bytes`, then applies the usual thumbnail
    rules.
- The crawl host answers `Thumbnail { pdf: true }` and `Embed` with them. Crawls embed
  when `ai_embeddings_enabled` and `ai_embeddings_during_crawl` are on and the embedder
  loads. A new job, `compute_embeddings {all}`, mirrors Database Maintenance → Compute
  Image Embeddings (orphans pruned first). Pillow-only images and archives the native
  readers refuse still get `Response::None` until Phase 8.
- Gate: with the same model on the same thumbnails, server and desktop vectors agreed to
  cosine ≥ 0.99997. The tests use a 6 KB stand-in model (`tests/data/tiny_clip.onnx`) and
  skip without the runtime libraries.
- Quirk found: zune-jpeg (the `image` crate's JPEG decoder) misreads the core encoder's
  thumbnails (non-interleaved baseline scans, component ids 0–2); libjpeg-turbo, and so
  Pillow and browsers, read them correctly. Rust code decodes JPEGs with `jpeg-decoder`,
  as the core does.

**5e: done 2026-10-18 (server files, Docker).**

- `api/files.rs`: `GET /api/files/browse?path=` (editor) lists subfolders and archives.
  Editors are limited to the library's roots (`roots` in `server.json` plus the crawled
  folders); administrators can browse anywhere, and the starting points include the drives
  or `/`. Paths are compared canonically (links and `..` resolved, case-folded on
  Windows). Crawl jobs follow the same rule, and a missing folder is refused at submission.
- `GET /api/models/{id}/archive` streams the archive (`Content-Disposition` with an
  RFC 6266 UTF-8 name). Model details carry `client_path`: the archive path for a client
  on the server's machine (loopback, not proxied), the mapped path where `path_map` in
  `server.json` covers it (`[{"server": "D:/STL", "client": "\\\\nas\\stl"}]`), otherwise
  null. The web client offers "open folder" only with a `client_path`, and the download
  otherwise.
- `stl-server restore FILE.7z` (6i) restores a backup with the library closed.
- `stl-server create-admin NAME` creates the first administrator in `server.db` (only
  while no user exists; password from `STL_ADMIN_PASSWORD` or standard input). It does not
  take the library lock, so it works beside a running server, in a container or on a
  headless host, where setup cannot come from loopback.
- `Dockerfile`: a multi-stage build of `stl-server` on Debian trixie, with pdfium (build
  7881) and ONNX Runtime 1.22.0 preinstalled and their paths set. Data lives in `/data`,
  on port 8770, running as uid 1000. UnRAR is not free software and is left to a derived
  image. **Not built here** (Docker is not installed on the development machine).
- Open for later: a server on another OS than the library's creator sees Windows paths
  (`D:\...`); moving a library between machines still goes through re-crawling or the
  sidecars.

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

**Toolchain:** Node.js 24 LTS (portable, not installed system-wide on the development
machine). Pinned majors whose APIs are known: React 19, Mantine 8, TanStack Query 5 /
Table 8 / Virtual 3, Vite 7, TypeScript 5.9, Vitest 3 (Mantine 9, Table 9, Vite 8 and
TypeScript 7 were out but not taken). API types are generated: `npm run gen:api` writes
`web/openapi.json` (from `stl-server openapi`) and `src/api/schema.d.ts` (openapi-typescript);
the cargo test `web_client_types_are_current` fails when the committed document is stale.
Operation ids are derived from method and path (`get_models_by_id`), because handler names
repeat across modules.

**6a: done 2026-10-18 (browser view).**

- `web/`: sign-in and first-run setup screens, the app shell with the user menu (change
  password, sign out), and the browser:
  - the filter pane: every desktop section, with the include / exclude / off cycle,
    has-value / no-value groups, scoped counts (a section the server does not scope falls
    back to its unscoped counts), date years with months, collections with
    subcollections, the folder tree, and value search in long sections;
  - the virtualised table: all ids in one request, rows fetched in blocks of 100 as they
    scroll into view, server-side sorting by header click (ascending → descending → id
    order), resizable columns;
  - the detail pane: fields, tags, thumbnails, the archive download, and "Copy path" when
    the server reports a `client_path`.
- `/api/events` invalidates the library queries on `library_changed`. A 401 shows the
  sign-in screen again.
- The server serves the built client: `web_dir` (`server.json` / `STL_WEB_DIR`), else
  `web/` beside the binary, else `web/dist` from the working directory, with an SPA
  fallback to `index.html`. The Dockerfile builds the client in a Node stage.
- Verified in headless Edge (DevTools protocol) against the server on a crawled library:
  sign-in, filtering by tag (counts and row count), selection, the thumbnail through the
  authenticated content-address route, and no console errors.

**6b: done 2026-10-18 (editing in the browser).** For editors (the UI hides it from viewers;
the server checks again):

- table selection as in a desktop list (click, Ctrl+click, Shift+click ranges;
  `selection.ts`, tested);
- inline cell editing: double-click, Enter or leaving the cell saves, Esc cancels; the
  desktop's cell fields through `PATCH /api/models/cells`;
- the editor drawer: every editor field, completion lists from `/api/editor/choices`,
  subcollections of the chosen collection, tags and scales as tag inputs;
- images in the detail pane: drag-and-drop (or click) upload, remove on hover;
- *Remove from library* for the selection, after a confirmation.

Verified in headless Edge: an inline creator edit, a title change through the drawer, an
image upload through the drop zone's file input and its removal, and removing two
Ctrl-selected models.

**6c: done 2026-10-18 (Jobs page).**

- Pages in the header (hash routes, `#/jobs`); `JobsPage` holds the crawled folders (add
  through a server folder picker over `/api/files/browse`; crawl one with a path pattern and
  force-rescan option, crawl all, stop tracking), the maintenance actions (verify, image
  hashes, embeddings, prune, compact for admins), the job list with progress, and a live log.
- Events: `job` events patch the cached job list (progress twice a second, no refetch);
  `job_log` lines go through an in-page event bus to the open log; finished jobs refresh the
  library views and the folder list.
- Server fixes found on the way: the folder listing no longer counts a folder's
  `METADATA.7z` as an archive, and a job log skips a line repeated by phase and log events.
- Verified in headless Edge: adding a folder through the picker, crawling it with a pattern
  (the folder shows its pattern afterwards), Verify archives, and the new models in the
  library.

**6d: done 2026-10-18 (settings).**

- Server: `GET|PUT /api/settings/library` (`api/settings.rs`) over the desktop's
  `settings.json`. Anyone signed in can read it; administrators can change it. A PUT
  carries every field, keeps the keys the server does not know (the desktop's
  appearance and file settings), writes atomically (temporary file, then rename), and
  validates ranges and presupport patterns with `rules::portable_pattern`.
- Web: the Settings page has three tabs. *Account* sets the colour scheme, saved as
  `color_scheme` in `/api/me/settings` and applied at sign-in. *Library* is the crawler,
  warning and sidecar settings, read-only for non-admins. *Users* (admins) adds users,
  changes roles, enables or disables accounts, sets passwords and deletes users.
- Verified in headless Edge: an admin saved a setting, switched to the light theme and
  added a viewer. Signed in as that viewer, the editing controls and the Users tab were
  hidden and the library settings read-only, showing the saved value.

**6e: done 2026-10-18 (lookup editors, mass editor).**

- Server: `POST /api/models/parse-paths` previews a path pattern per model (the mass
  editor's "Show parsed values"); the mass edit's `manual` map is typed in the OpenAPI
  document.
- Web:
  - The mass editor drawer (Mass edit on a selection): path pattern with a parsed-values
    table, the manual fields with Clear, presupports, lookups, scale and tags, find &
    replace (case, regex, fields), Overwrite and Trim. `massEdit.ts` builds the request
    (tested).
  - The Lookups page: Tags, Categories, Collections, Subcollections (by collection) and
    Scales, with search, add, rename, delete (showing what loses the value) and merge
    two. Tags can be merged into a name and unused tags deleted. Creators can be renamed,
    merged into another, cleared, and have their profile edited.
- Verified in headless Edge: a Shift-selected mass edit with a pattern preview (sculptors
  from the folders, creator and tags applied); adding and deleting an unused tag; and
  adding, renaming and deleting a category.

**6f: done 2026-10-18 (statistics, Find by Image).**

- Server:
  - `GET /api/stats/library`: scalars and rankings from `storage::stats`.
  - `GET /api/stats/column?key=&top=`: Column Distribution with the desktop's labels
    ("(unknown)", "(not set)", "(none)", month names).
  - `POST /api/search/image`: multipart upload. With `method=dhash&max_distance=` it
    computes the native dHash and compares it by Hamming distance with stored format-2
    hashes, reporting how many outdated hashes were skipped. With
    `method=clip&threshold=` it embeds the image through `tools::Embedder` and runs
    `vectors::find_similar_models`. Both consider at most the 1,024 closest images and
    keep the best one per model, as the desktop does.
- Web:
  - The Statistics page: totals, rankings, and Column Distribution with bars.
  - The Find by Image page: drop an image, choose the method and threshold (results
    update as the slider moves), open a match's details in a dialog.
- Verified in headless Edge: the totals, the distribution by tag, and a dHash search
  through the drop zone that opened a match.

**6g: done 2026-10-18 (duplicate finders).**

- Core: `stl_core::image_pairs::model_pairs` ports `image_duplicates.shared_items` /
  `model_pairs` (items shared by more than N models, with their near variants, are left
  out); `stl_native.model_pairs` and `tests/golden/test_image_pairs_golden.py` (200
  random cases) keep it equal to the Python version.
- Server (`stl-server/src/dupes.rs`), three job kinds whose typed result
  (`FinderResult`) is returned in `JobDetail.result`:
  - `find_content_duplicates { threshold }`: `duplicates::stl_content_pairs`.
  - `find_image_duplicates { method, max_distance, threshold, max_models }`: dHash
    (stored format-2 hashes, missing ones computed from thumbnails and not saved) or CLIP
    vectors, collapsed into distinct items, `near_pairs_128` / `near_pairs_cosine`, then
    `model_pairs`; best first, at most 100,000 pairs.
  - `find_metadata_duplicates { columns, method, max_distance }`: the desktop Find
    Duplicates search (exact, Levenshtein over lowercased distinct values, Soundex
    groups without code 0000 or all-identical values).
  - Arguments are validated before queueing (400); finders are read-only, so no
    `library_changed` follows them.
  - A crawl of a folder known under another spelling (or inside a crawled folder) is
    rewritten to the stored spelling (`files::known_spelling`): archive paths are
    compared as strings, so `C:/STL` used to duplicate every record of `C:\STL`.
- Web: the Duplicates page (STL files / Images / Metadata tabs) submits the job, shows
  its progress, and shows the newest finished job's result (filter, pages of 100,
  *Compare* opens up to three detail panes side by side). `jobs.addJob` keeps a quick
  job's events from being overwritten by the submit response.
- Verified in headless Edge: a duplicated archive found by STL files, Levenshtein on
  titles, the image scan and Compare, and the results shown again after a reload.

**6h: done 2026-10-18 (Database Maintenance).**

- Server (`stl-server/src/maint.rs`; the job result type is now `JobResult`, with a
  `records` variant of `RecordRow`s, the desktop's `_result_row`):
  - `verify_archives` also returns its non-OK records; `find_duplicate_entries` returns
    `maintenance::duplicate_entries`.
  - `find_moved { ids }`: Find Moved in DB (same hash, else same file name and size,
    archive on disk).
  - `search_folder { folder, ids }`: walks the folder (`crawler::list_archives`), hashes
    candidates in parallel with the native fingerprint (refused archives get the
    size/mtime fallback, as in a server crawl; a size pre-filter when every target has a
    fallback hash) and points matches at their new path (`browser::set_archive_paths`).
    The folder goes through `files::check_access`, as a crawl's does.
  - `recheck_warnings { warning: no_images | presupported }`: the desktop's
    `_WarningRecheckWorker`: native catalogs in parallel, only that warning line changes
    (plus the retired "Archive with supports"), lines in `WARNING_ORDER`, unreadable
    archives skipped; custom presupport patterns that are not portable refuse the job.
  - `POST /api/maintenance/merge-moved` (`maintenance::merge_moved`) and
    `POST /api/maintenance/remove` (`maintenance::remove_records`, pruning unused
    lookups); both delete thumbnails after the commit and schedule sidecars.
  - Jobs that write record paths or warnings queue their folders' sidecars
    (`Job::touch_sidecars`, flushed when the job ends); the exporter skips folders that
    no longer exist.
- Web: the Maintenance page (records, warnings, images and storage, the current job's
  log) with the record list and its actions; the Jobs page keeps crawled folders and
  the queue.
- Verified in headless Edge: an archive moved and found by Search in folder; a copy
  crawled elsewhere, Find moved in DB and Merge moved; a duplicate entry removed; the
  images warning re-check.

**6i: done 2026-10-18 (sidecars, backup and restore).**

- Core: `stl_core::backup`. `write` takes snapshots of the open library (`VACUUM INTO`
  for the database, `ThumbStore::copy_compacted` for the thumbnails) and packs them with
  `settings.json` into the desktop's layout (`stlLibrary.db`, `settings.json`,
  `thumbnails.lmdb/data.mdb`; thumbnails stored, not compressed again), through a
  temporary file renamed into place. `restore` extracts only those entries (no paths
  out of the folder), checks the database has a `models` table, moves the files it
  replaces (and the old `-wal` / `-shm`) to `pre-restore-<stamp>`, then moves the new
  ones in. `py7zr` (the desktop's reader) lists and extracts the server's backups.
- Server:
  - Jobs `generate_sidecars` (refused while sidecars are disabled), `remove_sidecars`,
    and `backup` (admin) writing `<data dir>/backups/stlLibrary_backup_<UTC stamp>.7z`.
  - `GET /api/backups`, `GET|DELETE /api/backups/{name}` (admin; names validated).
  - `stl-server restore FILE.7z [--data-dir DIR]` takes the library lock, so it refuses
    while the server or the desktop app has the library open.
- Web: Maintenance page cards for sidecars (generate, remove all) and backups (back up
  now, download, delete, the restore command).
- Verified in headless Edge: removing and regenerating the sidecars of three folders and
  a backup downloaded through the page; `stl-server restore` refused while the server ran
  and restored into a copy of the data directory.

**6j: done 2026-10-18 (AI tagger). Phase 6 is complete.**

- Core: `stl_core::ai_tagging` ports `app/utils/ai_tagging.py`: `clean_tags` (atomize,
  dedupe, cumulative chains), `is_corrupted_output`, the two prompts, `parse_meta`
  (Python `splitlines`, repeated keys, scales from the path / image names / file list)
  and `normalize_meta`, `known_meta`. The Python side was refactored so those pieces are
  pure functions there too (`parse_meta`, `normalize_meta`, `collect_known_meta`);
  `tests/golden/test_ai_tagging_golden.py` (650 cases) compares them through
  `stl_native.ai_clean_tags` / `ai_is_corrupted` / `ai_prompts` / `ai_parse_meta`.
- Server (`stl-server/src/ai.rs`): `AiRuntime` starts `llama-server` (from
  `<data dir>/ai`, the desktop's install with its `variant.txt`, or `server.json` →
  `ai`), waits for `/health`, talks OpenAI chat completions over `ureq` (plain HTTP),
  restarts after corrupted output, and stops after ten idle minutes or with the server
  (a forced kill of the server leaves it running until then). `ai.url` / `STL_AI_URL`
  use a running server instead.
  - Jobs `ai_suggest { id, max_tags, ignore_meta }` (result: `ai_suggestion`, nothing
    saved) and `ai_tag { ids, overwrite, ignore_meta, max_tags }` (saves each; a failure
    on the first model ends the job, as the desktop batch does).
  - `GET /api/ai/status`, `POST /api/ai/commit/{id}` (`model_edit::ai_commit`: empty
    fields only unless `overwrite`, tags merged).
  - `announce_changes` now covers `/api/maintenance/*` and `/api/ai/commit`, and no
    longer announces the read-only `/api/models/parse-paths`.
- Web: *AI tagger* on the browser selection: a drawer to review suggestions one by one
  (thumbnails sent, tag pills, editable fields, Save & next / Skip) or process all.
- Tests: `tests/ai.rs` against a stand-in OpenAI-compatible server; verified in headless
  Edge with the real model (CUDA llama-server, first suggestion in 32 s with loading).

**6k: done 2026-10-19 (archive tools).** Parity work found by an audit of the desktop's
menus before Phase 8.

- Core: `stl_core::rescan`: `rescan` (the desktop `RescanWorker`: hash only when size or
  time changed, native extraction, metadata with the archive's folder as source, rules,
  scales from the name and members, the narrower rescan STL suffixes, `round4`) and
  `thumbnails` (images by name until the limit succeeds, failures kept, printable check
  always on as in `ThumbnailGeneratorWorker`). Archives the native readers refuse are
  reported, not changed.
- Server (`archive_tools.rs`): jobs `rescan`, `generate_thumbnails { max_thumbnails? }`,
  `delete_files` (needs `allow_file_delete`; trash folder with a JSON recovery note per
  `_move_to_trash`, else deletion; records removed after the file step, thumbnails after
  the commit); `GET /api/models/{id}/files`, `GET /api/models/{id}/warnings`
  (`explain_warnings` with every check, the stored lines in order).
- Web: `ArchiveInfo` in the detail pane; the *Archives* menu on a selection.

**6l: done 2026-10-19 (image grid, archive browser).** `POST /api/views/grid {ids}` (at
most 500, in the order asked) and `POST /api/views/folder {folder, include_subfolders}`
return thumbnail cells (`views::grid_models` / `folder_models`, first thumbnail and image
count); `GET /api/folders`. Web: `CellGrid` (virtualised rows, `grid.ts` layout tested),
the browser's Table / Images toggle (`GridView` loads 120-id pages for the visible rows),
and the Folders page.

**6m: done 2026-10-19 (sort files).** `stl_core::sort_files` ports `sort_dialog.py`:
the token values of `render_path_pattern`, `_sanitise_segment`, `_compute_target`, the
preview statuses, and the move (free names, all or none with rollback, emptied source
folders removed). `POST /api/sort/preview` and the `sort_files` job (needs
`file_sorting_enabled`; the target folder goes through `check_access`). Web: `SortModal`.

### Phase 7: Desktop shell (small)

Tauri app that hosts the web client: connect to a remote server or start a bundled local one on
`localhost`, remember servers, tray icon, native menus and shortcuts, open local folders when
the path is visible. No business logic and no Tauri commands for data.

**Done 2026-10-18.**

- `rust/stl-desktop` (Tauri 2.12, `tauri-plugin-dialog`, `tauri-plugin-opener`): a bundled
  launcher page (`ui/`, plain HTML/JS, strict CSP) lists remembered servers
  (`<app config dir>/servers.json`) and opens a local library: the `stl-server` beside the
  shell, on a free loopback port, with its output in `<app log dir>/stl-server.log` (its
  last lines are shown when it fails to start, e.g. while the desktop app holds the
  library lock). Connecting checks `/api/auth/status`, then navigates the window to the
  server, which serves the web client.
- Commands are scoped by an app manifest (`build.rs`): the launcher's capability allows
  the launcher commands; each server origin gets a runtime capability with only
  `open_folder` (an absolute path that exists: a folder is opened, a file revealed).
  Verified: a server page's `list_servers` call is refused.
- Menus (File: Switch server Ctrl+L, Quit; Edit; View: Reload Ctrl+R, full screen) and a
  tray icon (Show, Switch server, Quit). Closing the window stops the local server.
- `rust/stl-proc`: `end_with_parent(&Child)` puts a child in a kill-on-close Windows job
  object, so the local server (and the server's `llama-server`) end even when the parent
  is killed. Verified by force-killing the shell.
- Web: `src/desktop.ts` (`inDesktopShell`, `openFolder`) and *Open folder* in the detail
  pane when the server reports a client path.
- Verified by driving the shell's WebView2 over the DevTools protocol
  (`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=…`): launcher, local
  library, sign-in, the table, the IPC scope, graceful and forced exits.
- Not done: installers (Phase 8 ships the server and the shell); macOS / Linux builds are
  untested.

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
