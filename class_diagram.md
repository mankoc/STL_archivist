# stlLibrary — Class Structure and Dependencies

Generated on 2026-10-04 from the source tree with an AST scan (classes, base classes, Qt
signals and `app.*` imports). The diagrams use [Mermaid](https://mermaid.js.org/), which GitHub
and VS Code render.

**Reading the graphs:** `A --> B` means *A imports B at module level*. `A -.-> B` means the import
is **deferred** (inside a function), usually to break an import cycle (see
[Dependency notes](#9-dependency-notes)).

Contents:

1. [Package dependencies](#1-package-dependencies)
2. [Module dependencies (non-UI)](#2-module-dependencies-non-ui)
3. [Data model](#3-data-model)
4. [Crawler](#4-crawler)
5. [Services and core](#5-services-and-core)
6. [UI composition](#6-ui-composition)
7. [UI class hierarchy](#7-ui-class-hierarchy)
8. [Background workers](#8-background-workers)
9. [Dependency notes](#9-dependency-notes)

---

## 1. Package dependencies

Edge labels count the import statements between the two packages (module-level / deferred).

```mermaid
flowchart TD
    main["main.py"]
    ui["app.ui<br/>PyQt6 widgets, dialogs, workers"]
    crawler["app.crawler<br/>archive pipeline"]
    services["app.services<br/>Qt-free business logic"]
    core["app.core<br/>Qt-free settings"]
    config["app.config<br/>CrawlerConfig"]
    database["app.database<br/>ORM, engine, sqlite-vec"]
    utils["app.utils<br/>thumbnails, hashing, icons, logging"]
    constants["app.constants"]

    main --> ui
    main --> database
    main -.-> utils

    ui -- "39 / 28" --> database
    ui -- "26 / 28" --> utils
    ui -- "12 / 9" --> services
    ui -- "2 / 12" --> crawler
    ui -- "2 / 1" --> core
    ui --> constants
    ui -.-> config

    crawler -- "5 / 1" --> database
    crawler -- "3 / 2" --> utils
    crawler --> services
    crawler --> config
    crawler --> constants

    services -- "6 / 2" --> database
    services -- "3 / 1" --> utils

    core --> config
    core -.-> crawler
    config -.-> crawler

    database --> utils
    utils -- "3 / 1" --> database
    utils -.-> core
    utils -.-> ui
```

The intended layering is `ui → services → database`, with `crawler` alongside `services`, and
`core`, `config` and `utils` underneath. The UI still queries the database directly in many
places (39 module-level imports of `app.database`); `app/services` is where that logic is
being moved (see `docs/refactoring-plan.md`). The upward edges `utils → ui`, `utils → database`
and `core/config → crawler` are covered in [section 9](#9-dependency-notes).

## 2. Module dependencies (non-UI)

```mermaid
flowchart LR
    subgraph CFG["config / core"]
        config["config"]
        settings["core.settings"]
        constants["constants"]
    end

    subgraph CRAWL["crawler"]
        crawler_mod["crawler<br/>CrawlerWorker"]
        archive["archive"]
        metadata["metadata"]
        rules["rules"]
        folder_metadata["folder_metadata"]
    end

    subgraph SVC["services"]
        editing["editing"]
        file_deletion["file_deletion"]
        image_similarity["image_similarity"]
        library_query["library_query"]
        lookups["lookups"]
        maintenance["maintenance"]
        model_summary["model_summary"]
        moves["moves"]
    end

    subgraph DB["database"]
        db["db"]
        models["models"]
        helpers["helpers"]
        vectors["vectors"]
        filter_cache["filter_cache"]
    end

    subgraph UT["utils"]
        thumbnailer["thumbnailer"]
        image_embedding["image_embedding"]
        image_hash["image_hash"]
        path_pattern["path_pattern"]
        string_utils["string_utils"]
        name_dedup["name_dedup"]
        file_matching["file_matching"]
        logger["logger"]
        material_icons["material_icons"]
        sound["sound"]
        ai_runtime["ai_runtime"]
        ai_server["ai_server"]
    end

    settings --> config
    settings -.-> rules
    config -.-> rules

    crawler_mod --> config
    crawler_mod --> archive
    crawler_mod --> metadata
    crawler_mod --> rules
    crawler_mod --> folder_metadata
    crawler_mod --> db
    crawler_mod --> helpers
    crawler_mod --> models
    crawler_mod --> vectors
    crawler_mod --> moves
    crawler_mod --> image_hash
    crawler_mod --> path_pattern
    crawler_mod --> thumbnailer
    crawler_mod -.-> image_embedding
    archive --> constants
    rules --> constants
    metadata --> archive
    folder_metadata --> helpers
    folder_metadata -.-> archive
    folder_metadata -.-> models
    folder_metadata -.-> thumbnailer

    editing --> helpers
    editing --> models
    editing --> path_pattern
    file_deletion --> models
    file_deletion --> thumbnailer
    image_similarity --> vectors
    image_similarity --> image_embedding
    library_query --> models
    lookups --> models
    maintenance -.-> db
    maintenance -.-> models
    maintenance -.-> thumbnailer

    db --> models
    db --> vectors
    db -.-> thumbnailer
    helpers --> models
    filter_cache -.-> db
    filter_cache -.-> models
    vectors --> image_embedding

    image_embedding -.-> settings
    image_embedding -.-> vectors
    path_pattern --> helpers
    path_pattern --> models
    file_matching --> models
    name_dedup --> string_utils
    logger -.-> settings
    material_icons -.-> settings
    ai_runtime -.-> settings
    ai_server --> ai_runtime
    ai_server -.-> thumbnailer
```

`services.moves` and `services.model_summary` import nothing from `app` (they work on the
session and ORM objects they are given), and neither does `utils.string_utils`.

## 3. Data model

SQLAlchemy ORM classes in `app/database/models.py`, plus the sqlite-vec table managed by
`app/database/vectors.py`. Thumbnails themselves live in LMDB (`app/utils/thumbnailer.py`),
keyed by `Image.id`.

```mermaid
classDiagram
    direction LR

    class STLModel {
        +id: int
        +title: str
        +creator: str
        +source: str
        +release_year: int
        +release_month: int
        +url: str
        +description: str
        +archive_path: str  unique
        +archive_folder: str  forward slashes
        +archive_hash: str
        +archive_size: int
        +archive_mtime_ns: int
        +stl_list_hash: str
        +sequence: int
        +presupports: str
        +warning: str
        +compression_rate: float
        +is_missing: bool
        +to_print: bool
        +created_at: datetime
        +updated_at: datetime
    }

    class Image {
        +id: int
        +model_id: int
        +filename: str
        +thumbnail_path: str  LMDB key
        +thumbnail: bytes  legacy
        +dhash: str  128-bit hex
    }

    class image_vec {
        <<sqlite-vec vec0>>
        +image_id: int  = Image.id
        +embedding: float[512]  cosine
    }

    class vector_store_info {
        <<table>>
        +model: str
        +dim: int
    }

    class STLFile {
        +model_id: int
        +filename: str
        +file_size: int
        +archive_hash: str
    }

    class Tag {
        +id: int
        +name: str
    }
    class Scale {
        +id: int
        +name: str
    }
    class Sculptor {
        +id: int
        +name: str
    }
    class Collection {
        +id: int
        +name: str
    }
    class Subcollection {
        +id: int
        +collection_id: int
        +name: str
    }
    class Category {
        +id: int
        +name: str
    }
    class Release {
        +id: int
        +name: str
    }
    class Creator {
        +name: str
        +full_name: str
        +patreon_url: str
        +artstation_url: str
        +gumroad_url: str
        +myminifactory_url: str
    }
    class CrawledFolder {
        +id: int
        +path: str
        +pattern: str
        +last_crawled_at: datetime
        +archive_count: int
        +new_count: int
        +updated_count: int
    }
    class FilterCache {
        +id: int
        +payload: str  JSON
        +updated_at: datetime
    }

    STLModel "1" *-- "*" Image : images
    STLModel "1" *-- "*" STLFile : stl_files
    STLModel "*" -- "*" Tag : model_tags
    STLModel "*" -- "*" Scale : model_scales
    STLModel "*" --> "0..1" Sculptor
    STLModel "*" --> "0..1" Collection
    STLModel "*" --> "0..1" Subcollection
    STLModel "*" --> "0..1" Category
    STLModel "*" --> "0..1" Release
    Collection "1" *-- "*" Subcollection : subcollections
    Image "1" -- "0..1" image_vec : TEMP trigger deletes
    image_vec ..> vector_store_info : one model per table
    STLModel ..> Creator : creator name, no FK
```

- `*--` marks cascading ownership: deleting a model deletes its images and STL file list.
- `image_vec` is a virtual table and has no foreign key. A per-connection `TEMP` trigger removes
  a vector when its image is deleted (see `vectors.py`).
- `Creator` is matched to `STLModel.creator` by name; there is no foreign key.
- `models_view` (created in `db._ensure_views`) joins `models` with the five lookup tables for
  the browser table.

## 4. Crawler

```mermaid
classDiagram
    class QThread
    class CrawlerWorker {
        +source_dir: Path
        -_config: CrawlerConfig
        -_move_candidates: dict
        +progress(int, int)
        +log(str)
        +warning(str)
        +phase(str)
        +crawl_finished()
        +counts_changed(int, int, int)
        +archive_result(str, str)
        +file_moved(str, str)
        +file_deleted(str)
        +run()
        +stop()
        -_collect_missing()
        -_phase_hash()  1 parallel
        -_phase_diff()  2 serial
        -_match_early_move()
        -_phase_extract()  3 parallel
        -_phase_upsert()  4 serial
        -_upsert_archive()
        -_update_thumbnails()
        -_reconcile_moved_files()
        -_mark_records_missing()
        -_post_export_metadata()
    }
    class CrawlerConfig {
        <<dataclass>>
        +max_thumbnails, max_thumb_size
        +max_workers, unrar_path
        +recurse_subdirs, force_rescan
        +store_stl_file_list
        +metadata_sidecar_enabled
        +embed_during_crawl
        +defer_missing_deletion
        +warn_* flags
        +force_* overrides
    }
    class ArchiveReader {
        <<abstract>>
        +iter_members() MemberInfo
        +read_batch(names) dict
        +close()
    }
    class _ZipReader
    class _SevenZReader
    class _RarReader
    class MemberInfo {
        <<dataclass>>
        +name: str
        +crc: int
        +size: int
    }
    class ModelMetadata {
        <<dataclass>>
        +title, creator, distributor
        +release_date, release, url
        +description, tags
    }

    CrawlerWorker --|> QThread
    CrawlerWorker --> CrawlerConfig : configured by
    CrawlerWorker ..> ArchiveReader : archive_hash / extract_archive_contents
    CrawlerWorker ..> ModelMetadata : extract_metadata()
    _ZipReader --|> ArchiveReader
    _SevenZReader --|> ArchiveReader
    _RarReader --|> ArchiveReader
    ArchiveReader ..> MemberInfo : yields
```

Collaborators of `CrawlerWorker`, by module:

| Module | Used for |
|---|---|
| `crawler.archive` | `iter_archives`, `archive_hash` (CRC fingerprint), `extract_archive_contents`, `list_contents` |
| `crawler.metadata` | `extract_metadata` → `ModelMetadata` |
| `crawler.rules` | `detect_presupport`, `detect_warnings`, `detect_scale` |
| `crawler.folder_metadata` | `METADATA.7z` import before a crawl and export after it |
| `services.moves` | `index_by_hash`, `relocate`, `merge_into_missing`, `delete_model` |
| `database.helpers` | `get_or_create_*` lookups, `prune_unused_lookups` |
| `database.vectors` | storing CLIP embeddings computed during the crawl |
| `utils.thumbnailer` | batched thumbnail writes and `delete_thumbnails` |
| `utils.image_hash`, `utils.path_pattern` | dHash, `{token}` path patterns |

`CrawlerConfig` is built by `core.settings.build_crawler_config()`, so the crawler never reads UI
settings directly.

## 5. Services and core

Qt-free modules, shown as classes with the `<<module>>` stereotype listing their public functions.
`FilterSpec` and `DeletionReport` are the only real classes.

```mermaid
classDiagram
    class library_query {
        <<module>>
        +table_where(spec, view)
        +facet_scopes(spec, view)
        +search_condition(text, view)
        +conditions(spec, view)
        +path_id_select(paths)
        +prune_ancestor_paths(paths)
    }
    class FilterSpec {
        <<dataclass>>
        +include
        +exclude
        +has_value
        +no_value
        +ids
        +facet_active()
        +is_empty()
        +pruned_paths()
    }
    class editing {
        <<module>>
        +apply_cell_edit(session, model, col, value)
        +assign_tags() / assign_scales()
        +set_sculptor() / set_category() / set_release()
        +set_collection() / set_subcollection()
    }
    class lookups {
        <<module>>
        +usage_counts(session, kind)
        +usage_count(session, kind, id)
    }
    class image_similarity {
        <<module>>
        +MAX_RESULT_IMAGES = 1024
        +find_similar_models(session, vec, threshold)
        +load_model_vectors(session)
    }
    class model_summary {
        <<module>>
        +model_summary(session, model_id)
    }
    class moves {
        <<module>>
        +archive_folder_for(path)
        +relocate(model, path, hash, size, mtime)
        +merge_into_missing(session, old, new)
        +delete_model(session, model)
        +index_by_hash(missing)
    }
    class maintenance {
        <<module>>
        +verify_archives(...)
        +find_duplicate_entries(...)
        +prune_images(...)
        +compact_database(engine, path)
        +database_free_bytes(session)
    }
    class file_deletion {
        <<module>>
        +delete_archives(session, ids, use_trash, trash_path)
    }
    class DeletionReport {
        <<dataclass>>
        +deleted: int
        +failed: list
        +total()
    }
    class settings {
        <<module app.core>>
        +register_providers(...)
        +get_data_dir() / get_unrar_path()
        +get_* per-library settings
        +build_crawler_config()
    }
    class vectors {
        <<module app.database>>
        +on_connect(conn) / available()
        +ensure_schema(engine)
        +store(session, items)
        +read_all(session)
        +current_ids() / prune_orphans() / count()
    }

    library_query ..> FilterSpec : takes
    file_deletion ..> DeletionReport : returns
    image_similarity --> vectors
    settings ..> CrawlerConfig : builds
    class CrawlerConfig
```

## 6. UI composition

Who creates whom at runtime, and the signals that tie the result dialogs back to the browser.

```mermaid
flowchart LR
    MW["MainWindow"]
    BT["BrowserTab"]
    CD["CrawlerDialog"]
    CT["CrawlerTab"]
    CW["CrawlerWorker"]
    UCF["UpdateCrawledFoldersDialog"]
    UAW["_UpdateAllWorker"]

    MW -->|hosts| BT
    MW -->|opens| CD
    CD -->|hosts| CT
    CT -->|runs| CW
    CT -->|opens| UCF
    MW -->|opens| UCF
    UCF -->|runs| UAW
    UAW -->|one per folder| CW

    subgraph MWD["opened from MainWindow"]
        CFG["ConfigDialog"]
        SIDE["MetadataSidecarDialog"]
        FD["FindDuplicatesDialog"]
        FCD["FindContentDuplicatesDialog"]
        FBI["FindByImageDialog"]
        AB["ArchiveBrowserDialog"]
        DBM["DbMaintenanceDialog"]
        BR["BackupRestoreDialog"]
        ST["StatisticsDialog"]
        CS["ColumnStatsDialog"]
        HA["HelpDialog / AboutDialog"]
    end
    MW --> MWD

    subgraph BTP["BrowserTab parts"]
        FT["FilterTreeWidget<br/>+ _FilterTreeWorker"]
        TM["_SqlTableModel<br/>+ _IdLoaderThread, _PageFetchRunnable"]
        DLG["delegates<br/>Source, Sculptor, Collection, ..."]
        TL["ThumbnailLoaderWorker"]
        JOBS["browser_jobs<br/>_CellEditRunnable, _ImageDropRunnable,<br/>RescanWorker, ThumbnailGeneratorWorker"]
    end
    BT --> BTP

    subgraph BTD["opened from BrowserTab"]
        ED["EditorDialog / MassEditorDialog"]
        AIT["AITaggerDialog"]
        IG["ImageGridDialog / ImageViewerDialog"]
        FL["FileListDialog / FileComparisonDialog"]
        SO["SortFilesDialog / RecrawlPreviewDialog"]
        LE["Tag / Category / Collection / Subcollection /<br/>Scale / Creator / Author editors"]
    end
    BT --> BTD

    FD -. "show_models_requested" .-> BT
    FCD -. "show_models_requested,<br/>show_duplicate_pairs_requested" .-> BT
    FBI -. "show_models_requested" .-> BT
    IG -. "show_selection_requested" .-> BT
```

Dashed arrows are Qt signals connected in `MainWindow` (for example
`FindByImageDialog.show_models_requested → BrowserTab.show_model_ids`).

## 7. UI class hierarchy

Widgets, models and delegates. Every dialog is a direct `QDialog` subclass; they are listed in
the table below the diagram instead of drawn.

```mermaid
classDiagram
    class QMainWindow
    class QWidget
    class QDialog
    class QTreeWidget
    class QAbstractTableModel
    class QStyledItemDelegate
    class QLineEdit
    class QLabel
    class QFrame

    MainWindow --|> QMainWindow
    BrowserTab --|> QWidget
    CrawlerTab --|> QWidget
    _CollapsibleSection --|> QWidget
    BarChartWidget --|> QWidget
    _MatchDetail --|> QWidget
    FilterTreeWidget --|> QTreeWidget
    _SqlTableModel --|> QAbstractTableModel

    _ComboDelegate --|> QStyledItemDelegate
    SourceDelegate --|> _ComboDelegate
    SculptorDelegate --|> _ComboDelegate
    CollectionDelegate --|> _ComboDelegate
    SubcollectionDelegate --|> _ComboDelegate
    CategoryDelegate --|> _ComboDelegate
    ReleaseDelegate --|> _ComboDelegate
    ScaleDelegate --|> QStyledItemDelegate
    PresupportsDelegate --|> QStyledItemDelegate
    TagDelegate --|> QStyledItemDelegate
    FilterItemDelegate --|> QStyledItemDelegate

    TagEdit --|> QLineEdit
    _TagLineEdit --|> QLineEdit
    ClickableLabel --|> QLabel
    _FitImageLabel --|> QLabel
    _ResultCell --|> QFrame
    _ThumbnailPopup --|> QFrame

    BrowserTab *-- FilterTreeWidget
    BrowserTab *-- _SqlTableModel
    MainWindow *-- BrowserTab
```

| Module (`app/ui/…`) | `QDialog` subclasses |
|---|---|
| `config_dialog` | `ConfigDialog` |
| `crawler_dialog` | `CrawlerDialog` |
| `editor_dialog` | `EditorDialog` |
| `mass_editor_dialog` | `MassEditorDialog` |
| `ai_tagger_dialog`, `ai_setup_dialog` | `AITaggerDialog`, `AISetupDialog` |
| `db_maintenance_dialog` | `DbMaintenanceDialog` |
| `find_duplicates_dialog` | `FindDuplicatesDialog` |
| `find_content_duplicates_dialog` | `FindContentDuplicatesDialog` |
| `find_by_image_dialog` | `FindByImageDialog` |
| `archive_browser_dialog` | `ArchiveBrowserDialog`, `_ArchiveImageViewer`, `_FullImageViewer` |
| `image_grid_dialog` | `ImageGridDialog`, `_GridImageViewer` |
| `browser_dialogs` | `ImageViewerDialog` |
| `browser_jobs` | `RescanProgressDialog` |
| `file_list_dialog` | `FileListDialog`, `FileComparisonDialog` |
| `sort_dialog` | `SortFilesDialog`, `_ConfirmMoveDialog` |
| `recrawl_dialog` | `RecrawlPreviewDialog` |
| `update_crawled_folders_dialog` | `UpdateCrawledFoldersDialog` |
| `metadata_sidecar_dialog` | `MetadataSidecarDialog` |
| `backup_restore_dialog` | `BackupRestoreDialog` |
| `statistics_dialog`, `column_stats_dialog` | `StatisticsDialog`, `ColumnStatsDialog` |
| `tag_editor_dialog` | `TagEditorDialog`, `_MergeSimilarDialog` |
| `category_editor_dialog` | `CategoryEditorDialog`, `_MergeCategoriesDialog` |
| `collection_editor_dialog` | `CollectionEditorDialog`, `_MergeCollectionsDialog` |
| `subcollection_editor_dialog` | `SubcollectionEditorDialog`, `_MergeSubcollectionsDialog` |
| `author_editor_dialog` | `AuthorEditorDialog`, `_MergeSimilarAuthorsDialog` |
| `scale_editor_dialog`, `creator_editor_dialog` | `ScaleEditorDialog`, `CreatorEditorDialog` |
| `help_dialog`, `about_dialog` | `HelpDialog`, `AboutDialog` |

## 8. Background workers

Every `QThread` subclass names its result signal `<what>_finished` (never `finished`, which would
hide Qt's own; `tests/test_worker_signal_shadowing.py` enforces this). Where the logic has moved
to `app/services`, the worker only wraps the service function.

```mermaid
classDiagram
    class QThread
    class QRunnable
    class QObject
    class _WarningRecheckWorker
    ImageWarningWorker --|> _WarningRecheckWorker
    PresupportWarningWorker --|> _WarningRecheckWorker
    _WarningRecheckWorker --|> QThread
    CrawlerWorker --|> QThread
    _UpdateAllWorker --|> QThread
    _IdLoaderThread --|> QThread
    _PageFetchRunnable --|> QRunnable
    _FilterTreeWorker --|> QRunnable
    _ExportRunnable --|> QRunnable
    ThumbnailLoaderWorker --|> QObject
```

| Worker | Base | Module (`app/…`) | Result signal | Wraps |
|---|---|---|---|---|
| `CrawlerWorker` | `QThread` | `crawler/crawler` | `crawl_finished` | four-phase pipeline |
| `_UpdateAllWorker` | `QThread` | `ui/update_crawled_folders_dialog` | `all_finished` | `CrawlerWorker` per folder + `services.moves` |
| `VerifyWorker` | `QThread` | `ui/db_maintenance_dialog` | `verify_finished` | `services.maintenance.verify_archives` |
| `SearchWorker` | `QThread` | `ui/db_maintenance_dialog` | `search_finished` | relocate missing archives |
| `DHashWorker` | `QThread` | `ui/db_maintenance_dialog` | `hash_finished` | `utils.image_hash` |
| `EmbeddingWorker` | `QThread` | `ui/db_maintenance_dialog` | `embed_finished` | `utils.image_embedding` + `database.vectors.store` |
| `ImageWarningWorker`, `PresupportWarningWorker` | `_WarningRecheckWorker` | `ui/db_maintenance_dialog` | `check_finished` | `crawler.rules` |
| `DedupWorker` | `QThread` | `ui/db_maintenance_dialog` | `dedup_finished` | `utils.thumbnailer.deduplicate_store` |
| `CompactWorker` | `QThread` | `ui/db_maintenance_dialog` | `compact_finished` | `utils.thumbnailer.compact_store` |
| `CompactDatabaseWorker` | `QThread` | `ui/db_maintenance_dialog` | `db_compact_finished` | `services.maintenance.compact_database` |
| `PruneImagesWorker` | `QThread` | `ui/db_maintenance_dialog` | `prune_finished` | `services.maintenance.prune_images` |
| `FindDuplicateEntriesWorker` | `QThread` | `ui/db_maintenance_dialog` | `duplicates_finished` | `services.maintenance.find_duplicate_entries` |
| `MovedInDbWorker` | `QThread` | `ui/db_maintenance_dialog` | `moved_finished` | match missing records to re-crawled ones |
| `_ContentDupWorker`, `_ImageDupWorker`, `_EmbeddingDupWorker` | `QThread` | `ui/find_content_duplicates_dialog` | `scan_finished` | `services.image_similarity.load_model_vectors` (embedding) |
| `_SearchWorker` | `QThread` | `ui/find_duplicates_dialog` | `search_finished` | `utils.string_utils` |
| `_ScanWorker` | `QThread` | `ui/tag_editor_dialog` | `scan_finished` | `utils.name_dedup` |
| `StatsWorker` / `ColumnStatsWorker` | `QThread` | `ui/statistics_dialog`, `ui/column_stats_dialog` | `stats_ready` / `data_ready` | aggregate queries |
| `MetadataSidecarWorker`, `RemoveSidecarsWorker` | `QThread` | `ui/metadata_sidecar_dialog` | `export_finished`, `remove_finished` | `crawler.folder_metadata` |
| `_BackupWorker`, `_RestoreWorker` | `QThread` | `ui/backup_restore_dialog` | `backup_finished`, `restore_finished` | file copy |
| `RescanWorker`, `ThumbnailGeneratorWorker` | `QThread` | `ui/browser_jobs` | `finished_ok`, `generation_finished` | per-model rescan / thumbnails |
| `_InferenceThread`, `_BatchThread` | `QThread` | `ui/ai_tagger_dialog` | `tags_ready`, `completed` | `utils.ai_server` (llama.cpp) |
| `_InstallWorker` | `QThread` | `ui/ai_setup_dialog` | `install_finished` | `utils.ai_runtime` |
| `_ClipDownloadWorker` | `QThread` | `ui/config_dialog` | `download_finished` | CLIP model download |
| `_IdLoaderThread` | `QThread` | `ui/browser_table_model` | `ids_ready` | `services.library_query.table_where` |
| `_PageFetchRunnable` | `QRunnable` | `ui/browser_table_model` | (callback) | paged row fetch |
| `_FilterTreeWorker` | `QRunnable` | `ui/browser_filter_tree` | (callback) | `services.library_query.facet_scopes` |
| `_CellEditRunnable`, `_ImageDropRunnable`, `_WarningDetailRunnable` | `QRunnable` | `ui/browser_jobs` | (callback) | `services.editing`, thumbnails, warning details |
| `_ExportRunnable` | `QRunnable` | `ui/sidecar_exporter` | — | debounced `METADATA.7z` export |
| `ThumbnailLoaderWorker` | `QObject` | `ui/thumb_loader` | `bytes_ready` | LMDB thumbnail reads |

## 9. Dependency notes

Import cycles and upward dependencies found by the scan. All of them are broken with deferred
imports today, which is why they don't fail at import time.

| Cycle / upward edge | Where | Why |
|---|---|---|
| `database.vectors → utils.image_embedding` and `utils.image_embedding -.-> database.vectors` | `image_embedding._vector_store_ok()` | `is_available()` also checks that sqlite-vec loaded |
| `utils.path_pattern → database.helpers / models` | module level | path patterns write onto ORM models; arguably belongs in `services` |
| `utils.file_matching → database.models` | module level | queries models when comparing archives |
| `utils.sound -.-> ui.config_dialog` | deferred | the notification-sound settings are still UI-only |
| `config -.-> crawler.rules`, `core.settings -.-> crawler.rules` | deferred | default presupport patterns live in `crawler.rules` |
| `ui.config_dialog -.-> ui.crawler_tab` and `ui.crawler_tab → ui.config_dialog` | deferred / module level | config dialog refreshes the open crawler tab |
| `utils.* -.-> core.settings` | deferred | logger, icons, AI runtime and CLIP locator need the data directory |

Moving `path_pattern` and `file_matching` into `app/services`, and the sound settings into
`app/core/settings.py`, would remove the remaining upward edges out of `app/utils`.
