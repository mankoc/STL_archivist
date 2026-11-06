//! What a job is asked to do.

use super::*;

/// What to run. Crawl settings not given here come from the library's
/// `settings.json`, as in the desktop app.
#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum JobRequest {
    /// Crawl a folder of archives.
    Crawl {
        folder: String,
        /// Folder path pattern (`{sculptor}/{release}/{title}`); default: the
        /// pattern stored for this crawled folder.
        #[serde(default)]
        pattern: Option<String>,
        /// Re-extract archives whose hash did not change.
        #[serde(default)]
        force_rescan: bool,
        /// Default: the `recurse_subdirs` setting.
        #[serde(default)]
        recurse: Option<bool>,
        /// Mark the records of archives gone from the folder missing instead
        /// of deleting them (monitored folders; *Remove missing archives*
        /// deletes them later).
        #[serde(default)]
        keep_missing: bool,
        /// A folder inside a library: crawl it as a part of that library (its
        /// pattern, its records), without making it a library folder.
        #[serde(default)]
        in_library: bool,
    },
    /// Re-crawl crawled folders (all of them, or `folders`) with their stored
    /// patterns, matching archives moved from one folder to another before
    /// deleting the records of archives that are gone (Update Folders).
    UpdateFolders {
        #[serde(default)]
        folders: Vec<String>,
    },
    /// Delete every record, lookup and thumbnail of the library (Delete
    /// Database; administrators). `confirm` must be `"DELETE"`.
    ClearLibrary { confirm: String },
    /// Check that every archive is still on disk, unchanged in size.
    VerifyArchives,
    /// Read the catalog of every archive whose file list or STL list was never
    /// read (crawls open only new and changed archives).
    ReadFileLists,
    /// Read every archive's `.url` and `info.json` again (as a crawl does) into
    /// empty fields, or over them with `overwrite`; tags are added.
    RereadLinks {
        #[serde(default)]
        overwrite: bool,
    },
    /// Compute perceptual hashes from stored thumbnails (all, or only missing / outdated).
    RecomputeDhashes {
        #[serde(default)]
        all: bool,
    },
    /// Keep at most `keep` images per model (the oldest).
    PruneImages { keep: usize },
    /// `VACUUM` the database and truncate its write-ahead log; drop unused
    /// thumbnails and rewrite the thumbnail store without its free pages.
    CompactDatabase,
    /// Compute CLIP embeddings from stored thumbnails (all, or only images without one).
    ComputeEmbeddings {
        #[serde(default)]
        all: bool,
    },
    /// Find Duplicate Archives → STL files: archive pairs whose file lists
    /// (name and size) overlap by at least `threshold` (0–1) of the larger list.
    FindContentDuplicates {
        #[serde(default = "default_content_threshold")]
        threshold: f64,
    },
    /// Find Duplicate Archives → Images: archive pairs with matching images.
    FindImageDuplicates {
        /// `dhash` (perceptual hash) or `clip` (AI embedding).
        method: String,
        /// Most differing bits (`dhash`).
        #[serde(default = "default_max_distance")]
        max_distance: u32,
        /// Least cosine similarity (`clip`).
        #[serde(default = "default_similarity")]
        threshold: f32,
        /// Images found in more models than this (banners, logos) are left out.
        #[serde(default = "default_max_models")]
        max_models: usize,
    },
    /// Write `METADATA.7z` in every folder holding catalogued archives (needs
    /// sidecars enabled in the library settings).
    GenerateSidecars,
    /// Delete `METADATA.7z` from every folder holding catalogued archives.
    RemoveSidecars,
    /// Tidy the `METADATA.7z` files under the crawled folders: rewrite those
    /// listing archives that are gone, delete those whose archives are all gone,
    /// and delete leftover `METADATA.7z.tmp` files.
    CleanSidecars,
    /// Back up the database, thumbnails and settings to
    /// `<data dir>/backups/stlLibrary_backup_<stamp>.7z` (administrators).
    Backup,
    /// Import a library package (a backup, from this server or another one)
    /// from the backups list (administrators). Lists its folders and checks
    /// their archives under `roots` (folder uuid → root here; a folder not
    /// given keeps its root when that is a folder here). With `apply` (and
    /// `confirm: "REPLACE"`), backs this library up (`…_pre_import.7z`) and
    /// replaces it with the package, the folders at their new roots; user
    /// accounts stay. `rules` (path starts) map the folders `roots` does not
    /// name; `backup_first: false` skips the backup (a sync's choice).
    ImportLibrary {
        name: String,
        #[serde(default)]
        roots: BTreeMap<String, String>,
        #[serde(default)]
        rules: Vec<crate::sync::RootRule>,
        #[serde(default)]
        apply: bool,
        #[serde(default)]
        confirm: String,
        #[serde(default = "yes")]
        backup_first: bool,
    },
    /// Sync with a remote server (administrators; `confirm: "REPLACE"`): `pull`
    /// replaces this library with the remote's, `push` the remote's with this
    /// one, each folder mapped by the remote's path rules. Only the thumbnails
    /// the receiving side lacks travel.
    Sync {
        /// The remote (`RemoteInfo.id`).
        remote: i64,
        direction: crate::sync::Direction,
        #[serde(default)]
        confirm: String,
        /// Whether the receiving side backs up first; default: the remote's setting.
        #[serde(default)]
        backup_first: Option<bool>,
    },
    /// Pack this library for a sync that pulls it (administrators; another
    /// server asks for it): `backups/…_sync.7z` without the thumbnails in
    /// `have` (the receiver's list, `POST /api/sync/thumbnails`), and with only
    /// the database's changes when `blocks` (its fingerprints, `POST
    /// /api/sync/blocks`) allow.
    SyncExport {
        have: String,
        #[serde(default)]
        blocks: Option<String>,
    },
    /// Download optional components into the data directory (administrators):
    /// `ai_runtime`, `ai_model`, `clip_model`, `onnxruntime`, `pdfium`.
    /// `variants` picks a variant per component (`ai_runtime`: `cpu`, or `cuda`
    /// on Windows; `onnxruntime`: `cpu`, `directml` on Windows, `cuda` on
    /// Linux); the others get the default for the GPUs found.
    InstallComponents {
        components: Vec<String>,
        #[serde(default)]
        variants: BTreeMap<String, String>,
    },
    /// Delete downloaded components (administrators).
    RemoveComponents { components: Vec<String> },
    /// Re-read the models' archives: metadata, presupports, warnings, scales,
    /// hash (Rescan).
    Rescan { ids: Vec<i64> },
    /// Replace the models' images with ones from their archives, at most
    /// `max_thumbnails` each (default: the library setting).
    GenerateThumbnails {
        ids: Vec<i64>,
        #[serde(default)]
        max_thumbnails: Option<usize>,
    },
    /// Move the models' archives into a folder tree under `target_dir` built
    /// from `pattern` (Sort Files), fields without a value becoming `missing`
    /// (default `_unknown`; `""` leaves them empty). Needs *file sorting* in the settings.
    SortFiles {
        ids: Vec<i64>,
        target_dir: String,
        pattern: String,
        #[serde(default)]
        missing: Option<String>,
    },
    /// Move a library's folder to a new root: check every archive there and,
    /// with `apply`, set the root (and those of the folders inside it). With
    /// `fix_case`, paths found only with another letter case take the
    /// spelling on disk. Nothing is re-read.
    RelocateLibrary {
        /// The folder (`LibraryFolder.id`).
        id: i64,
        root: String,
        #[serde(default)]
        apply: bool,
        #[serde(default)]
        fix_case: bool,
    },
    /// Move the models' archive files to the trash folder (or delete them,
    /// without one) and remove the records. Needs *allow file delete*.
    DeleteFiles { ids: Vec<i64> },
    /// Ask the AI tagger for one model's tags and metadata; nothing is saved
    /// (save the reviewed result with `/api/ai/commit/{id}`).
    AiSuggest {
        id: i64,
        /// At most this many tags (0: no limit).
        #[serde(default = "default_max_tags")]
        max_tags: usize,
        /// Leave the catalogued metadata out of the prompt.
        #[serde(default)]
        ignore_meta: bool,
    },
    /// Tag every model with the AI tagger and save the results: empty fields
    /// only, unless `overwrite`; tags are merged.
    AiTag {
        ids: Vec<i64>,
        #[serde(default)]
        overwrite: bool,
        #[serde(default)]
        ignore_meta: bool,
        #[serde(default = "default_max_tags")]
        max_tags: usize,
    },
    /// Records cataloguing the same archive more than once (same content hash,
    /// else same file name and size).
    FindDuplicateEntries,
    /// For each record, another record with the same content whose archive is on
    /// disk (Find Moved in DB); merge them with `/api/maintenance/merge-moved`.
    FindMoved { ids: Vec<i64> },
    /// Look under `folder` for the records' archives (by content hash) and point
    /// the records at the archives found.
    SearchFolder { folder: String, ids: Vec<i64> },
    /// Re-read every archive's file list and fix one warning line:
    /// `no_images` or `presupported`.
    RecheckWarnings { warning: String },
    /// Find Duplicates (metadata): models with equal or similar values.
    FindMetadataDuplicates {
        /// Column keys: title, creator, source, sculptor, collection,
        /// subcollection, category, release, scale.
        columns: Vec<String>,
        /// `exact`, `levenshtein` or `soundex`.
        method: String,
        /// Most edits (`levenshtein`).
        #[serde(default = "default_levenshtein")]
        max_distance: u32,
    },
}

fn default_content_threshold() -> f64 {
    0.8
}
fn default_max_distance() -> u32 {
    10
}
fn default_similarity() -> f32 {
    0.9
}
fn default_max_models() -> usize {
    10
}
fn default_levenshtein() -> u32 {
    3
}
fn default_max_tags() -> usize {
    20
}

impl JobRequest {
    /// This request without its list of ids: what clients need to show a job, as
    /// `GET /api/jobs/{id}` has the rest.
    pub(super) fn brief(&self) -> JobRequest {
        let mut brief = self.clone();
        match &mut brief {
            JobRequest::Rescan { ids }
            | JobRequest::GenerateThumbnails { ids, .. }
            | JobRequest::SortFiles { ids, .. }
            | JobRequest::DeleteFiles { ids }
            | JobRequest::AiTag { ids, .. }
            | JobRequest::FindMoved { ids }
            | JobRequest::SearchFolder { ids, .. } => ids.clear(),
            _ => {}
        }
        brief
    }

    pub fn kind(&self) -> &'static str {
        match self {
            JobRequest::Crawl { .. } => "crawl",
            JobRequest::UpdateFolders { .. } => "update_folders",
            JobRequest::ClearLibrary { .. } => "clear_library",
            JobRequest::VerifyArchives => "verify_archives",
            JobRequest::ReadFileLists => "read_file_lists",
            JobRequest::RereadLinks { .. } => "reread_links",
            JobRequest::RecomputeDhashes { .. } => "recompute_dhashes",
            JobRequest::PruneImages { .. } => "prune_images",
            JobRequest::CompactDatabase => "compact_database",
            JobRequest::ComputeEmbeddings { .. } => "compute_embeddings",
            JobRequest::InstallComponents { .. } => "install_components",
            JobRequest::RemoveComponents { .. } => "remove_components",
            JobRequest::Rescan { .. } => "rescan",
            JobRequest::GenerateThumbnails { .. } => "generate_thumbnails",
            JobRequest::DeleteFiles { .. } => "delete_files",
            JobRequest::SortFiles { .. } => "sort_files",
            JobRequest::RelocateLibrary { .. } => "relocate_library",
            JobRequest::AiSuggest { .. } => "ai_suggest",
            JobRequest::AiTag { .. } => "ai_tag",
            JobRequest::GenerateSidecars => "generate_sidecars",
            JobRequest::RemoveSidecars => "remove_sidecars",
            JobRequest::CleanSidecars => "clean_sidecars",
            JobRequest::Backup => "backup",
            JobRequest::ImportLibrary { .. } => "import_library",
            JobRequest::Sync { .. } => "sync",
            JobRequest::SyncExport { .. } => "sync_export",
            JobRequest::FindDuplicateEntries => "find_duplicate_entries",
            JobRequest::FindMoved { .. } => "find_moved",
            JobRequest::SearchFolder { .. } => "search_folder",
            JobRequest::RecheckWarnings { .. } => "recheck_warnings",
            JobRequest::FindContentDuplicates { .. } => "find_content_duplicates",
            JobRequest::FindImageDuplicates { .. } => "find_image_duplicates",
            JobRequest::FindMetadataDuplicates { .. } => "find_metadata_duplicates",
        }
    }

    /// Whether the job only reads the library (no change announcement after it).
    pub fn read_only(&self) -> bool {
        matches!(
            self,
            JobRequest::RelocateLibrary { apply: false, .. }
                | JobRequest::FindDuplicateEntries
                | JobRequest::AiSuggest { .. }
                | JobRequest::GenerateSidecars
                | JobRequest::RemoveSidecars
                | JobRequest::CleanSidecars
                | JobRequest::Backup
                | JobRequest::ImportLibrary { apply: false, .. }
                | JobRequest::Sync { direction: crate::sync::Direction::Push, .. }
                | JobRequest::SyncExport { .. }
                | JobRequest::FindMoved { .. }
                | JobRequest::FindContentDuplicates { .. }
                | JobRequest::FindImageDuplicates { .. }
                | JobRequest::FindMetadataDuplicates { .. }
        )
    }

    /// Reject arguments the job would refuse anyway, before queueing it.
    pub fn validate(&self) -> Result<(), String> {
        match self {
            JobRequest::FindContentDuplicates { threshold } if !(*threshold > 0.0 && *threshold <= 1.0) => {
                Err("threshold must be above 0 and at most 1".into())
            }
            JobRequest::ClearLibrary { confirm } if confirm != "DELETE" => Err("confirm with \"DELETE\"".into()),
            JobRequest::ImportLibrary { apply: true, confirm, .. } if confirm != "REPLACE" => Err("confirm with \"REPLACE\"".into()),
            JobRequest::ImportLibrary { name, .. } if !crate::api::backups::valid(name) => Err(format!("{name:?} is not a package name")),
            JobRequest::Sync { confirm, .. } if confirm != "REPLACE" => Err("confirm with \"REPLACE\"".into()),
            JobRequest::SyncExport { have, blocks }
                if [Some(have), blocks.as_ref()].into_iter().flatten().any(|id| id.is_empty() || !id.bytes().all(|b| b.is_ascii_digit())) =>
            {
                Err(format!("{have:?} is not a list from another server"))
            }
            JobRequest::InstallComponents { components, variants } if bad_components(components, variants) => {
                Err(components_hint())
            }
            JobRequest::RemoveComponents { components } if bad_components(components, &BTreeMap::new()) => Err(components_hint()),
            JobRequest::RecheckWarnings { warning } if crate::maint::Recheck::parse(warning).is_none() => {
                Err(format!("unknown warning {warning:?} (no_images or presupported)"))
            }
            JobRequest::FindMoved { ids }
            | JobRequest::SearchFolder { ids, .. }
            | JobRequest::AiTag { ids, .. }
            | JobRequest::Rescan { ids }
            | JobRequest::GenerateThumbnails { ids, .. }
            | JobRequest::DeleteFiles { ids }
            | JobRequest::SortFiles { ids, .. }
                if ids.is_empty() =>
            {
                Err("no records given".into())
            }
            JobRequest::FindImageDuplicates { method, max_distance, threshold, max_models } => {
                if method != "dhash" && method != "clip" {
                    return Err(format!("unknown method {method:?} (dhash or clip)"));
                }
                if *max_distance > 64 || !(0.0..=1.0).contains(threshold) || *max_models == 0 {
                    return Err("max_distance must be at most 64, threshold between 0 and 1, max_models at least 1".into());
                }
                Ok(())
            }
            JobRequest::FindMetadataDuplicates { columns, method, max_distance } => {
                if columns.is_empty() {
                    return Err("choose at least one column".into());
                }
                if let Some(c) = columns.iter().find(|c| !crate::dupes::COLUMNS.iter().any(|(k, ..)| k == c)) {
                    return Err(format!("unknown column {c:?}"));
                }
                if !["exact", "levenshtein", "soundex"].contains(&method.as_str()) {
                    return Err(format!("unknown method {method:?} (exact, levenshtein or soundex)"));
                }
                if *max_distance == 0 || *max_distance > 20 {
                    return Err("max_distance must be between 1 and 20".into());
                }
                Ok(())
            }
            _ => Ok(()),
        }
    }
}
