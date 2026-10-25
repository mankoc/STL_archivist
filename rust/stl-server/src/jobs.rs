//! Server jobs: crawls and maintenance, one at a time in submission order
//! (SQLite has one writer, as in the desktop app). Progress, phases and log
//! lines go to the job's record and to every WebSocket client
//! ([`ServerEvent`]); a job stops at its next checkpoint when cancelled.

use std::collections::{BTreeMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use stl_core::control::Control;
use stl_core::crawler::{self, CrawlConfig, Event, Host, Request, Response};
use stl_core::storage::{self, crawl as crawl_db, duplicates, maintenance};
use tokio::sync::{Notify, broadcast};
use utoipa::ToSchema;

use crate::Library;
use crate::accounts;
use crate::library_settings::LibrarySettings;

/// Finished jobs kept for listing.
const KEEP_FINISHED: usize = 50;
/// Log lines kept per job.
const KEEP_LOG: usize = 2000;
/// How often a running job's progress is published.
const TICK: Duration = Duration::from_millis(500);
const DHASH_BATCH: usize = 500;
const PRUNE_BATCH: usize = 200;

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
    /// from `pattern` (Sort Files). Needs *file sorting* in the settings.
    SortFiles { ids: Vec<i64>, target_dir: String, pattern: String },
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
    pub fn kind(&self) -> &'static str {
        match self {
            JobRequest::Crawl { .. } => "crawl",
            JobRequest::UpdateFolders { .. } => "update_folders",
            JobRequest::ClearLibrary { .. } => "clear_library",
            JobRequest::VerifyArchives => "verify_archives",
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Queued,
    Running,
    Done,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct JobInfo {
    pub id: u64,
    pub kind: &'static str,
    pub request: JobRequest,
    pub state: JobState,
    /// Who submitted it.
    pub user: String,
    pub phase: Option<String>,
    pub done: u64,
    pub total: u64,
    /// The outcome (a summary, or the error).
    pub message: Option<String>,
    pub created_at: i64,
    pub started_at: Option<i64>,
    pub finished_at: Option<i64>,
}

/// What WebSocket clients receive (`/api/events`), as JSON text frames.
#[derive(Debug, Clone, Serialize, ToSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerEvent {
    /// A job was queued, started, progressed or finished.
    Job { job: JobInfo },
    /// One log line of a running job.
    JobLog { id: u64, line: String },
    /// The library changed (`models`, `lookups`, `crawl`, …): reload what is shown.
    LibraryChanged { what: String },
    /// Events were dropped for this client (it was too slow); reload everything.
    Resync,
}

pub struct Job {
    info: Mutex<JobInfo>,
    log: Mutex<VecDeque<String>>,
    stop: AtomicBool,
    ctl: Control,
    result: Mutex<Option<crate::dupes::JobResult>>,
    /// Archive paths whose folders' sidecars need a refresh once the job ends.
    sidecars: Mutex<Vec<String>>,
}

impl Job {
    /// What a finder job found (`None` for other jobs, or until it finishes).
    pub fn result(&self) -> Option<crate::dupes::JobResult> {
        self.result.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    fn set_result(&self, v: crate::dupes::JobResult) {
        *self.result.lock().unwrap_or_else(|e| e.into_inner()) = Some(v);
    }

    fn touch_sidecars(&self, paths: impl IntoIterator<Item = String>) {
        self.sidecars.lock().unwrap_or_else(|e| e.into_inner()).extend(paths);
    }

    pub fn info(&self) -> JobInfo {
        self.info.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    pub fn log(&self) -> Vec<String> {
        self.log.lock().unwrap_or_else(|e| e.into_inner()).iter().cloned().collect()
    }

    fn stopped(&self) -> bool {
        self.stop.load(Ordering::Relaxed)
    }
}

pub struct Jobs {
    list: Mutex<Vec<Arc<Job>>>,
    next_id: AtomicU64,
    wake: Arc<Notify>,
    started: OnceLock<()>,
    pub events: broadcast::Sender<ServerEvent>,
}

impl Default for Jobs {
    fn default() -> Self {
        Jobs {
            list: Mutex::new(Vec::new()),
            next_id: AtomicU64::new(1),
            wake: Arc::new(Notify::new()),
            started: OnceLock::new(),
            events: broadcast::channel(1024).0,
        }
    }
}

impl Jobs {
    pub fn list(&self) -> Vec<JobInfo> {
        self.list.lock().unwrap_or_else(|e| e.into_inner()).iter().map(|j| j.info()).collect()
    }

    pub fn get(&self, id: u64) -> Option<Arc<Job>> {
        self.list.lock().unwrap_or_else(|e| e.into_inner()).iter().find(|j| j.info().id == id).cloned()
    }

    fn send(&self, e: ServerEvent) {
        let _ = self.events.send(e); // no subscribers is fine
    }

    fn publish(&self, job: &Job) {
        self.send(ServerEvent::Job { job: job.info() });
    }

    fn next_queued(&self) -> Option<Arc<Job>> {
        self.list
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .find(|j| j.info().state == JobState::Queued)
            .cloned()
    }

    fn trim(&self) {
        let mut list = self.list.lock().unwrap_or_else(|e| e.into_inner());
        let finished = list.iter().filter(|j| matches!(j.info().state, JobState::Done | JobState::Failed | JobState::Cancelled)).count();
        let mut excess = finished.saturating_sub(KEEP_FINISHED);
        list.retain(|j| {
            let old = matches!(j.info().state, JobState::Done | JobState::Failed | JobState::Cancelled);
            if old && excess > 0 {
                excess -= 1;
                false
            } else {
                true
            }
        });
    }

    /// Cancel a job: a queued one at once, a running one at its next checkpoint.
    /// `false` when it has already finished.
    pub fn cancel(&self, job: &Job) -> bool {
        let state = job.info().state;
        match state {
            JobState::Queued => {
                finish(job, JobState::Cancelled, Some("cancelled before it started".into()));
                self.publish(job);
                true
            }
            JobState::Running => {
                job.stop.store(true, Ordering::Relaxed);
                job.ctl.request_stop();
                true
            }
            _ => false,
        }
    }
}

fn update(job: &Job, f: impl FnOnce(&mut JobInfo)) {
    f(&mut job.info.lock().unwrap_or_else(|e| e.into_inner()));
}

fn finish(job: &Job, state: JobState, message: Option<String>) {
    update(job, |i| {
        i.state = state;
        i.message = message;
        i.finished_at = Some(accounts::now());
    });
}

impl Library {
    /// Queue a job; it runs after the jobs before it.
    pub fn submit_job(self: &Arc<Self>, request: JobRequest, user: &str) -> JobInfo {
        let jobs = &self.jobs;
        let id = jobs.next_id.fetch_add(1, Ordering::Relaxed);
        let job = Arc::new(Job {
            info: Mutex::new(JobInfo {
                id,
                kind: request.kind(),
                request,
                state: JobState::Queued,
                user: user.to_string(),
                phase: None,
                done: 0,
                total: 0,
                message: None,
                created_at: accounts::now(),
                started_at: None,
                finished_at: None,
            }),
            log: Mutex::new(VecDeque::new()),
            stop: AtomicBool::new(false),
            ctl: Control::new(),
            result: Mutex::new(None),
            sidecars: Mutex::new(Vec::new()),
        });
        jobs.list.lock().unwrap_or_else(|e| e.into_inner()).push(Arc::clone(&job));
        jobs.trim();
        jobs.publish(&job);
        jobs.started.get_or_init(|| {
            tokio::spawn(worker(Arc::downgrade(self), Arc::clone(&jobs.wake)));
        });
        jobs.wake.notify_one();
        job.info()
    }

    /// Tell clients the library changed.
    pub fn changed(&self, what: &str) {
        self.jobs.send(ServerEvent::LibraryChanged { what: what.to_string() });
    }

    fn job_log(&self, job: &Job, line: String) {
        {
            let mut log = job.log.lock().unwrap_or_else(|e| e.into_inner());
            // The crawl reports some steps as both a phase and a log line.
            if log.back() == Some(&line) {
                return;
            }
            if log.len() >= KEEP_LOG {
                log.pop_front();
            }
            log.push_back(line.clone());
        }
        self.jobs.send(ServerEvent::JobLog { id: job.info().id, line });
    }
}

async fn worker(lib: Weak<Library>, wake: Arc<Notify>) {
    loop {
        let next = match lib.upgrade() {
            Some(l) => l.jobs.next_queued(),
            None => return,
        };
        let Some(job) = next else {
            wake.notified().await;
            continue;
        };
        let Some(l) = lib.upgrade() else { return };
        update(&job, |i| {
            i.state = JobState::Running;
            i.started_at = Some(accounts::now());
        });
        l.jobs.publish(&job);
        // Publish progress while the job runs.
        let ticker = {
            let (l, job) = (Arc::clone(&l), Arc::clone(&job));
            tokio::spawn(async move {
                let mut last = (u64::MAX, u64::MAX);
                loop {
                    tokio::time::sleep(TICK).await;
                    let (done, total) = job.ctl.progress();
                    if (done, total) != last && total > 0 {
                        update(&job, |i| (i.done, i.total) = (done, total));
                        l.jobs.publish(&job);
                        last = (done, total);
                    }
                }
            })
        };
        let (l2, job2) = (Arc::clone(&l), Arc::clone(&job));
        let outcome = tokio::task::spawn_blocking(move || run(&l2, &job2))
            .await
            .unwrap_or_else(|e| Err(format!("the job panicked: {e}")));
        ticker.abort();
        let (done, total) = job.ctl.progress();
        update(&job, |i| (i.done, i.total) = (done, total.max(done)));
        match outcome {
            Ok(summary) if job.stopped() => finish(&job, JobState::Cancelled, Some(format!("stopped: {summary}"))),
            Ok(summary) => finish(&job, JobState::Done, Some(summary)),
            Err(e) => {
                tracing::error!(job = job.info().id, "job failed: {e}");
                finish(&job, JobState::Failed, Some(e));
            }
        }
        tracing::info!(job = job.info().id, kind = job.info().kind, state = ?job.info().state, "job finished");
        l.jobs.publish(&job);
        let touched = std::mem::take(&mut *job.sidecars.lock().unwrap_or_else(|e| e.into_inner()));
        if !touched.is_empty() {
            l.schedule_sidecars(touched);
        }
        if !job.info().request.read_only() {
            l.changed(job.info().kind);
        }
    }
}

fn run(lib: &Library, job: &Job) -> Result<String, String> {
    let request = job.info().request;
    match request {
        JobRequest::Crawl { folder, pattern, force_rescan, recurse, keep_missing } => crawl(lib, job, folder, pattern, force_rescan, recurse, keep_missing),
        JobRequest::UpdateFolders { folders } => update_folders(lib, job, folders),
        JobRequest::ClearLibrary { .. } => clear_library(lib, job),
        JobRequest::VerifyArchives => verify(lib, job),
        JobRequest::RecomputeDhashes { all } => recompute_dhashes(lib, job, all),
        JobRequest::PruneImages { keep } => prune_images(lib, job, keep),
        JobRequest::CompactDatabase => compact(lib, job),
        JobRequest::ComputeEmbeddings { all } => compute_embeddings(lib, job, all),
        JobRequest::InstallComponents { components, variants } => install_components(lib, job, &components, &variants),
        JobRequest::RemoveComponents { components } => {
            let log = |line: String| lib.job_log(job, line);
            let n = crate::components::remove(&lib.place(), &log, &components)?;
            Ok(format!("{n} component(s) removed"))
        }
        JobRequest::Rescan { ids } => rescan(lib, job, &ids),
        JobRequest::GenerateThumbnails { ids, max_thumbnails } => generate_thumbnails(lib, job, &ids, max_thumbnails),
        JobRequest::DeleteFiles { ids } => delete_files(lib, job, &ids),
        JobRequest::SortFiles { ids, target_dir, pattern } => sort_files(lib, job, &ids, &target_dir, &pattern),
        JobRequest::RelocateLibrary { id, root, apply, fix_case } => relocate_library(lib, job, id, &root, apply, fix_case),
        JobRequest::AiSuggest { id, max_tags, ignore_meta } => ai_suggest(lib, job, id, max_tags, ignore_meta),
        JobRequest::AiTag { ids, overwrite, ignore_meta, max_tags } => ai_tag(lib, job, &ids, overwrite, ignore_meta, max_tags),
        JobRequest::GenerateSidecars => generate_sidecars(lib, job),
        JobRequest::RemoveSidecars => remove_sidecars(lib, job),
        JobRequest::CleanSidecars => clean_sidecars(lib, job),
        JobRequest::Backup => backup(lib, job),
        JobRequest::FindDuplicateEntries => duplicate_entries(lib, job),
        JobRequest::FindMoved { ids } => find_moved(lib, job, &ids),
        JobRequest::SearchFolder { folder, ids } => search_folder(lib, job, &folder, &ids),
        JobRequest::RecheckWarnings { warning } => recheck_warnings(lib, job, &warning),
        JobRequest::FindContentDuplicates { threshold } => find_content(lib, job, threshold),
        JobRequest::FindImageDuplicates { method, max_distance, threshold, max_models } => {
            find_images(lib, job, &method, max_distance, threshold, max_models)
        }
        JobRequest::FindMetadataDuplicates { columns, method, max_distance } => {
            find_metadata(lib, job, &columns, &method, max_distance)
        }
    }
}

// ── components ───────────────────────────────────────────────────────────────

fn bad_components(ids: &[String], variants: &BTreeMap<String, String>) -> bool {
    use crate::components::{IDS, variants as offered};
    ids.is_empty()
        || ids.iter().any(|c| !IDS.contains(&c.as_str()))
        || variants.iter().any(|(id, v)| !offered(id).contains(&v.as_str()))
}

fn components_hint() -> String {
    use crate::components::{IDS, variants};
    let v: Vec<String> = IDS.iter().filter(|id| !variants(id).is_empty()).map(|id| format!("{id}: {:?}", variants(id))).collect();
    let v = if v.is_empty() { String::new() } else { format!(", with variants among {}", v.join(", ")) };
    format!("choose components among {IDS:?}{v}")
}

fn install_components(lib: &Library, job: &Job, ids: &[String], variants: &BTreeMap<String, String>) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let n = crate::components::install(&lib.place(), &job.ctl, &log, ids, variants)?;
    Ok(format!("{n} component(s) installed"))
}

// ── archive tools ────────────────────────────────────────────────────────────

fn rescan(lib: &Library, job: &Job, ids: &[i64]) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let out = crate::archive_tools::rescan_models(lib, &job.ctl, &log, ids)?;
    job.touch_sidecars(out.touched);
    Ok(format!("{} of {} model(s) updated", out.done, ids.len()))
}

fn generate_thumbnails(lib: &Library, job: &Job, ids: &[i64], max: Option<usize>) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let max = max.unwrap_or_else(|| LibrarySettings::load(&lib.config.data_dir).max_thumbnails());
    let out = crate::archive_tools::generate_thumbnails(lib, &job.ctl, &log, ids, max)?;
    job.touch_sidecars(out.touched);
    Ok(format!("thumbnails generated for {} of {} model(s)", out.done, ids.len()))
}

fn delete_files(lib: &Library, job: &Job, ids: &[i64]) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(ids.len() as u64);
    let (deleted, failed, paths) = crate::archive_tools::delete_files(lib, &log, ids)?;
    job.ctl.advance(ids.len() as u64);
    job.touch_sidecars(paths);
    Ok(format!("{deleted} archive(s) deleted{}", if failed > 0 { format!(", {failed} failed") } else { String::new() }))
}

/// Sort Files: plan, move every archive or none, then point the records at
/// the new paths in one transaction.
fn relocate_library(lib: &Library, job: &Job, id: i64, root: &str, apply: bool, fix_case: bool) -> Result<String, String> {
    use storage::libraries;
    let name = lib.db.with(|c| libraries::get(c, id)).map_err(db_err)?.ok_or_else(|| format!("no library folder {id}"))?.name;
    lib.job_log(job, format!("Checking the archives of {name} under {root}…"));
    let check = lib.db.with(|c| Ok(libraries::check_relocate(c, id, root, Some(&job.ctl)))).map_err(db_err)??;
    for m in &check.missing_examples {
        lib.job_log(job, format!("Not found: {m}"));
    }
    let applied = apply && !job.stopped();
    if applied {
        lib.db.with_tx(|tx| libraries::relocate(tx, id, root, &check, fix_case)).map_err(db_err)?;
        lib.job_log(job, format!("{name} now lives in {root}."));
    }
    let summary = format!(
        "{} of {} found, {} with another letter case, {} missing{}",
        check.found,
        check.total,
        check.case_only.len(),
        check.missing,
        if applied { "; moved" } else { " (checked only)" }
    );
    job.set_result(crate::dupes::JobResult::Relocation(crate::dupes::Relocation {
        library: id,
        root: root.to_string(),
        total: check.total,
        found: check.found,
        case_only: check.case_only.len(),
        missing: check.missing,
        missing_examples: check.missing_examples.clone(),
        inner: check.inner.len(),
        applied,
    }));
    Ok(summary)
}

fn sort_files(lib: &Library, job: &Job, ids: &[i64], target_dir: &str, pattern: &str) -> Result<String, String> {
    if !LibrarySettings::load(&lib.config.data_dir).file_sorting_enabled() {
        return Err("sorting files is disabled in the library settings (file sorting)".into());
    }
    let sources = lib.db.with(|c| storage::browser::sort_sources(c, ids)).map_err(db_err)?;
    let rows = stl_core::sort_files::preview(&sources, ids, std::path::Path::new(target_dir), pattern);
    for r in rows.iter().filter(|r| !r.moves()) {
        lib.job_log(job, format!("{}: {} ({})", r.status, r.from, r.to));
    }
    job.ctl.set_total(rows.len() as u64);
    let moved = stl_core::sort_files::execute(&rows)?;
    lib.db.with_tx(|tx| storage::browser::set_archive_paths(tx, &moved, Some(target_dir))).map_err(db_err)?;
    job.ctl.advance(rows.len() as u64);
    for r in rows.iter().filter(|r| r.moves()) {
        lib.job_log(job, format!("Moved: {} → {}", r.from, r.to));
    }
    job.touch_sidecars(rows.iter().filter(|r| r.moves()).map(|r| r.from.clone()).chain(moved.iter().map(|(_, p)| p.clone())));
    Ok(format!("moved {} archive(s)", moved.len()))
}

// ── AI tagger ────────────────────────────────────────────────────────────────

fn ai_suggest(lib: &Library, job: &Job, id: i64, max_tags: usize, ignore_meta: bool) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(1);
    let s = crate::ai::suggest(lib, &job.ctl, &log, id, max_tags, ignore_meta)?;
    job.ctl.advance(1);
    let msg = format!("{}: {} tag(s) suggested", s.title, s.tags.len());
    job.set_result(crate::dupes::JobResult::AiSuggestion(Box::new(s)));
    Ok(msg)
}

/// `_BatchThread`: suggest and save each model in turn. A failure on the first
/// model (most likely the setup) ends the job; later ones are logged and skipped.
fn ai_tag(lib: &Library, job: &Job, ids: &[i64], overwrite: bool, ignore_meta: bool, max_tags: usize) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(ids.len() as u64);
    let mut saved = 0usize;
    for (k, &id) in ids.iter().enumerate() {
        if job.stopped() {
            break;
        }
        match crate::ai::suggest(lib, &job.ctl, &log, id, max_tags, ignore_meta) {
            Ok(s) => {
                if let Some(path) = crate::ai::commit(lib, id, s.fields, &s.tags, overwrite)? {
                    saved += 1;
                    job.touch_sidecars([path]);
                }
            }
            Err(e) if k == 0 => return Err(e),
            Err(e) => lib.job_log(job, format!("Model #{id}: {e}")),
        }
        job.ctl.advance(1);
    }
    Ok(format!("saved {saved} of {} model(s)", ids.len()))
}

// ── sidecars and backups ─────────────────────────────────────────────────────

fn sidecar_folders(lib: &Library, job: &Job) -> Result<Vec<String>, String> {
    // Spelled as the archive paths' parents, as the sidecars store and match them.
    let paths = lib.db.with(duplicates::all_archive_paths).map_err(db_err)?;
    let folders: Vec<String> = paths.iter().filter_map(|p| crate::sidecars::folder_of(p)).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
    if folders.is_empty() {
        lib.job_log(job, "No folders with database records found.".into());
    }
    job.ctl.set_total(folders.len() as u64);
    Ok(folders)
}

fn generate_sidecars(lib: &Library, job: &Job) -> Result<String, String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    if !settings.sidecars_enabled() {
        return Err("metadata sidecars are disabled in the library settings: enable them first".into());
    }
    let thumbs = settings.sidecar_thumbnails();
    let folders = sidecar_folders(lib, job)?;
    lib.job_log(job, format!("Processing {} folder(s); writing METADATA.7z (thumbnails: {})…", folders.len(), if thumbs { "yes" } else { "no" }));
    let (mut written, mut failed) = (0usize, 0usize);
    for (k, folder) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        let n = format!("[{}/{}]", k + 1, folders.len());
        if !std::path::Path::new(folder).is_dir() {
            lib.job_log(job, format!("{n} Skipped (folder not found): {folder}"));
        } else {
            match lib.db.with(|c| Ok::<_, rusqlite::Error>(storage::sidecar::export(c, Some(&lib.thumbs), folder, thumbs))).map_err(db_err)? {
                Ok(true) => {
                    written += 1;
                    lib.job_log(job, format!("{n} Updated: {folder}"));
                }
                Ok(false) => lib.job_log(job, format!("{n} Skipped (no records): {folder}")),
                Err(e) => {
                    failed += 1;
                    lib.job_log(job, format!("{n} ERROR in {folder}: {e:?}"));
                }
            }
        }
        job.ctl.advance(1);
    }
    Ok(format!("updated {written} of {} folder(s){}", folders.len(), if failed > 0 { format!(", {failed} failed") } else { String::new() }))
}

fn remove_sidecars(lib: &Library, job: &Job) -> Result<String, String> {
    let folders = sidecar_folders(lib, job)?;
    let mut removed = 0usize;
    for (k, folder) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        let path = std::path::Path::new(folder).join(storage::sidecar::METADATA_FILENAME);
        let n = format!("[{}/{}]", k + 1, folders.len());
        if path.is_file() {
            match std::fs::remove_file(&path) {
                Ok(()) => {
                    removed += 1;
                    lib.job_log(job, format!("{n} Removed: {}", path.display()));
                }
                Err(e) => lib.job_log(job, format!("{n} ERROR removing {}: {e}", path.display())),
            }
        }
        job.ctl.advance(1);
    }
    Ok(format!("removed {removed} sidecar(s) from {} folder(s)", folders.len()))
}

/// Folders under the crawled folders holding a sidecar or a leftover temporary
/// one (symbolic links are not followed).
fn folders_with_sidecars(roots: &[String], job: &Job) -> Vec<std::path::PathBuf> {
    let tmp_name = format!("{}.tmp", storage::sidecar::METADATA_FILENAME);
    let mut found = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut stack: Vec<std::path::PathBuf> = roots.iter().map(std::path::PathBuf::from).collect();
    while let Some(dir) = stack.pop() {
        if job.stopped() {
            break;
        }
        if !seen.insert(dir.to_string_lossy().to_lowercase()) {
            continue;
        }
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        let mut has = false;
        for e in rd.flatten() {
            let Ok(ft) = e.file_type() else { continue };
            if ft.is_dir() {
                stack.push(e.path());
            } else if ft.is_file() {
                let name = e.file_name();
                has |= name == storage::sidecar::METADATA_FILENAME || name.to_string_lossy() == tmp_name;
            }
        }
        if has {
            found.push(dir);
        }
    }
    found.sort();
    found
}

fn clean_sidecars(lib: &Library, job: &Job) -> Result<String, String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let thumbs = settings.sidecar_thumbnails();
    // Folders with records, by their case-folded spelling: the stored spelling and
    // the (case-folded) archive names catalogued there.
    let paths = lib.db.with(duplicates::all_archive_paths).map_err(db_err)?;
    let mut catalogued: std::collections::HashMap<String, (String, std::collections::BTreeSet<String>)> = Default::default();
    for p in &paths {
        let Some(f) = crate::sidecars::folder_of(p) else { continue };
        let name = p[f.len()..].trim_start_matches(['/', '\\']).to_lowercase();
        catalogued.entry(f.to_lowercase()).or_insert_with(|| (f, Default::default())).1.insert(name);
    }
    let roots: Vec<String> = lib.db.with(duplicates::crawled_folders).map_err(db_err)?.into_iter().map(|(p, _)| p).collect();
    lib.job_log(job, format!("Looking for sidecars under {} crawled folder(s)…", roots.len()));
    let folders = folders_with_sidecars(&roots, job);
    job.ctl.set_total(folders.len() as u64);
    let (mut rewritten, mut removed, mut kept, mut failed) = (0usize, 0usize, 0usize, 0usize);
    for (k, dir) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        let n = format!("[{}/{}]", k + 1, folders.len());
        let tmp = dir.join(format!("{}.tmp", storage::sidecar::METADATA_FILENAME));
        if tmp.is_file() {
            match std::fs::remove_file(&tmp) {
                Ok(()) => lib.job_log(job, format!("{n} Removed leftover: {}", tmp.display())),
                Err(e) => {
                    failed += 1;
                    lib.job_log(job, format!("{n} ERROR removing {}: {e}", tmp.display()));
                }
            }
        }
        let path = dir.join(storage::sidecar::METADATA_FILENAME);
        if !path.is_file() {
            job.ctl.advance(1);
            continue;
        }
        let listed = storage::sidecar::listed_archives(&path);
        let gone = match &listed {
            Ok(names) => names.iter().filter(|a| !dir.join(a).exists()).count(),
            Err(_) => 0,
        };
        let present = listed.as_ref().map(|v| v.len() - gone).unwrap_or(0);
        let entry = catalogued.get(&dir.to_string_lossy().to_lowercase());
        // A catalogued folder's sidecar is stale when it lists other archives than the records.
        let stale = match (&listed, entry) {
            (Ok(names), Some((_, records))) => names.iter().map(|a| a.to_lowercase()).collect::<std::collections::BTreeSet<_>>() != *records,
            _ => true,
        };
        let action = match (&listed, entry.map(|(f, _)| f)) {
            // Catalogued folder: rewrite from the database when stale or unreadable.
            (_, Some(f)) if stale => {
                if !settings.sidecars_enabled() {
                    kept += 1;
                    format!("Kept (out of date, but sidecars are disabled): {}", path.display())
                } else {
                    match lib.db.with(|c| Ok::<_, rusqlite::Error>(storage::sidecar::export(c, Some(&lib.thumbs), f, thumbs))).map_err(db_err)? {
                        Ok(_) => {
                            rewritten += 1;
                            match &listed {
                                Ok(_) => format!("Rewritten (out of date): {}", path.display()),
                                Err(_) => format!("Rewritten (unreadable): {}", path.display()),
                            }
                        }
                        Err(e) => {
                            failed += 1;
                            format!("ERROR rewriting {}: {e:?}", path.display())
                        }
                    }
                }
            }
            (_, Some(_)) => {
                job.ctl.advance(1);
                continue; // up to date as far as its archives go
            }
            (Err(e), None) => {
                kept += 1;
                format!("Kept (cannot read it: {e:?}): {}", path.display())
            }
            // Not catalogued: useless once none of its archives exist; otherwise a crawl imports them.
            (Ok(_), None) if present == 0 => match std::fs::remove_file(&path) {
                Ok(()) => {
                    removed += 1;
                    format!("Removed (none of its archives exist): {}", path.display())
                }
                Err(e) => {
                    failed += 1;
                    format!("ERROR removing {}: {e}", path.display())
                }
            },
            (Ok(_), None) => {
                kept += 1;
                format!("Kept ({present} archive(s) not in the library; crawl the folder to import them): {}", path.display())
            }
        };
        lib.job_log(job, format!("{n} {action}"));
        job.ctl.advance(1);
    }
    let mut msg = format!("{} sidecar folder(s): {rewritten} rewritten, {removed} removed, {kept} kept", folders.len());
    if failed > 0 {
        msg.push_str(&format!(", {failed} failed"));
    }
    Ok(msg)
}

fn backup(lib: &Library, job: &Job) -> Result<String, String> {
    let name = crate::api::backups::new_name();
    let dest = crate::api::backups::dir(&lib.config.data_dir).join(&name);
    lib.job_log(job, format!("Backing up the database, thumbnails and settings to {}…", dest.display()));
    let settings = lib.config.data_dir.join(stl_core::backup::SETTINGS_FILE);
    let size = lib
        .db
        .with(|c| Ok::<_, rusqlite::Error>(stl_core::backup::write(c, &lib.thumbs, Some(&settings), &dest, &job.ctl)))
        .map_err(db_err)?
        .map_err(|e| e.to_string())?;
    Ok(format!("{name}: {:.1} MB", size as f64 / (1024.0 * 1024.0)))
}

// ── maintenance lists ────────────────────────────────────────────────────────

fn records(rows: Vec<crate::maint::RecordRow>) -> crate::dupes::JobResult {
    crate::dupes::JobResult::Records { rows }
}

fn duplicate_entries(lib: &Library, job: &Job) -> Result<String, String> {
    lib.job_log(job, "Scanning for duplicate entries…".into());
    let rows: Vec<_> = lib.db.with(maintenance::duplicate_entries).map_err(db_err)?.into_iter().map(Into::into).collect();
    Ok(finder_done(job, records(rows), "duplicate record(s) found"))
}

fn find_moved(lib: &Library, job: &Job, ids: &[i64]) -> Result<String, String> {
    lib.job_log(job, format!("Searching {} missing record(s) in the database…", ids.len()));
    let rows = crate::maint::find_moved(lib, &job.ctl, ids)?;
    Ok(finder_done(job, records(rows), "record(s) found re-crawled elsewhere"))
}

fn search_folder(lib: &Library, job: &Job, folder: &str, ids: &[i64]) -> Result<String, String> {
    if !std::path::Path::new(folder).is_dir() {
        return Err(format!("{folder} is not a folder the server can read"));
    }
    let log = |line: String| lib.job_log(job, line);
    let (rows, touched) = crate::maint::search_folder(lib, &job.ctl, &log, folder, ids)?;
    job.touch_sidecars(touched);
    Ok(finder_done(job, records(rows), "archive(s) found and relocated"))
}

fn recheck_warnings(lib: &Library, job: &Job, warning: &str) -> Result<String, String> {
    let which = crate::maint::Recheck::parse(warning).ok_or("unknown warning")?;
    let log = |line: String| lib.job_log(job, line);
    let r = crate::maint::recheck_warnings(lib, &job.ctl, &log, which)?;
    job.touch_sidecars(r.touched);
    Ok(format!("{} cleared, {} added, {} skipped (missing or unreadable)", r.cleared, r.added, r.skipped))
}

// ── duplicate finders ────────────────────────────────────────────────────────

fn finder_done(job: &Job, result: crate::dupes::JobResult, what: &str) -> String {
    let n = result.len();
    job.set_result(result);
    format!("{n} {what}")
}

fn find_content(lib: &Library, job: &Job, threshold: f64) -> Result<String, String> {
    lib.job_log(job, format!("Comparing STL file lists (at least {:.0}% shared)…", threshold * 100.0));
    match crate::dupes::content(lib, &job.ctl, threshold) {
        Err(e) if job.stopped() => Ok(e),
        r => Ok(finder_done(job, r?, "archive pair(s) found")),
    }
}

fn find_images(lib: &Library, job: &Job, method: &str, max_distance: u32, threshold: f32, max_models: usize) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let r = if method == "clip" {
        crate::dupes::image_clip(lib, &job.ctl, &log, threshold, max_models)
    } else {
        crate::dupes::image_dhash(lib, &job.ctl, &log, max_distance, max_models)
    };
    match r {
        Err(e) if job.stopped() => Ok(e),
        r => {
            let r = r?;
            if matches!(&r, crate::dupes::JobResult::Images(i) if i.truncated) {
                lib.job_log(job, format!("Only the best {} pairs are kept: tighten the threshold.", crate::dupes::MAX_RESULTS));
            }
            Ok(finder_done(job, r, "archive pair(s) found"))
        }
    }
}

fn find_metadata(lib: &Library, job: &Job, columns: &[String], method: &str, max_distance: u32) -> Result<String, String> {
    lib.job_log(job, format!("Comparing {} column(s) ({method})…", columns.len()));
    let r = crate::dupes::metadata(lib, &job.ctl, columns, method, max_distance)?;
    Ok(finder_done(job, r, "group(s) found"))
}

// ── crawl ────────────────────────────────────────────────────────────────────

/// The crawl's host on the server. There is no Python: PDF previews and CLIP
/// vectors come from [`crate::tools`] when their libraries are present; every
/// other request gets `Response::None` and the crawl goes on without that
/// piece (see `stl_core::crawler`).
struct ServerHost<'a> {
    lib: &'a Library,
    job: &'a Job,
    max_thumb_size: u32,
    /// Collects the archives the crawl added (Update Folders' move matching).
    new_paths: Option<&'a Mutex<Vec<String>>>,
}

impl Host for ServerHost<'_> {
    fn call(&self, request: Request) -> Response {
        let config = &self.lib.config;
        match request {
            Request::Thumbnail { data, pdf: true } => match self.lib.tools.pdf(config) {
                Some(r) => Response::Thumbnail(r.thumbnail(&data, self.max_thumb_size)),
                None => Response::None,
            },
            Request::Embed(images) => match self.lib.embed(&images) {
                Ok(Some(v)) => Response::Vectors(v),
                Ok(None) => Response::None,
                Err(e) => {
                    self.lib.job_log(self.job, format!("Warning: {e}"));
                    Response::None
                }
            },
            _ => Response::None,
        }
    }

    fn emit(&self, event: Event) {
        match event {
            Event::Log(line) => self.lib.job_log(self.job, line),
            Event::Warning(line) => self.lib.job_log(self.job, format!("Warning: {line}")),
            Event::Phase(p) => {
                update(self.job, |i| i.phase = Some(p.clone()));
                self.lib.jobs.publish(self.job);
                self.lib.job_log(self.job, p);
            }
            Event::Progress(done, total) => {
                self.job.ctl.set_total(total as u64);
                self.job.ctl.advance(done as u64);
            }
            Event::Result(path, status) if status == "new" => {
                if let Some(paths) = self.new_paths {
                    paths.lock().unwrap_or_else(|e| e.into_inner()).push(path);
                }
            }
            Event::Counts(..) | Event::Result(..) | Event::Moved(..) | Event::Deleted(..) => {}
        }
    }

    fn should_stop(&self) -> bool {
        self.job.stopped()
    }
}

fn crawl(
    lib: &Library,
    job: &Job,
    folder: String,
    pattern: Option<String>,
    force_rescan: bool,
    recurse: Option<bool>,
    keep_missing: bool,
) -> Result<String, String> {
    let s = crawl_folder(lib, job, folder, pattern, force_rescan, recurse, keep_missing, None)?;
    Ok(format!("{} new, {} updated, {} unchanged", s.new, s.updated, s.unchanged))
}

/// `_UpdateAllWorker`: crawl each folder with deletion deferred, then match
/// records left missing against archives new in another folder (keeping their
/// metadata), then delete the records of archives that are really gone.
fn update_folders(lib: &Library, job: &Job, folders: Vec<String>) -> Result<String, String> {
    let folders = if folders.is_empty() {
        lib.db.with(duplicates::crawled_folders).map_err(db_err)?.into_iter().map(|(p, _)| p).collect()
    } else {
        folders
    };
    if folders.is_empty() {
        return Ok("no crawled folders".into());
    }
    let several = folders.len() > 1;
    let new_paths = Mutex::new(Vec::new());
    let (mut new, mut updated, mut unchanged) = (0, 0, 0);
    let mut crawled = Vec::new();
    for (k, folder) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        lib.job_log(job, format!("[{}/{}] {folder}", k + 1, folders.len()));
        match crawl_folder(lib, job, folder.clone(), None, false, None, several, Some(&new_paths)) {
            Ok(s) => {
                (new, updated, unchanged) = (new + s.new, updated + s.updated, unchanged + s.unchanged);
                crawled.push(folder.clone());
            }
            Err(e) => lib.job_log(job, format!("ERROR {folder}: {e}")),
        }
    }
    let (mut moved_n, mut deleted_n) = (0, 0);
    if several && !job.stopped() {
        let new_paths = new_paths.into_inner().unwrap_or_else(|e| e.into_inner());
        update(job, |i| i.phase = Some("Cross-folder move detection…".into()));
        let (moved, refs) = lib.db.with_tx(|tx| storage::jobs::cross_folder(tx, &new_paths)).map_err(db_err)?;
        delete_thumb_refs(lib, &refs)?;
        for (old, new) in &moved {
            lib.job_log(job, format!("Cross-folder move: {old} → {new}"));
        }
        update(job, |i| i.phase = Some("Cleaning up deleted files…".into()));
        let (deleted, refs) = lib.db.with_tx(|tx| storage::jobs::delete_missing_under(tx, &crawled)).map_err(db_err)?;
        delete_thumb_refs(lib, &refs)?;
        for p in &deleted {
            lib.job_log(job, format!("Deleted: {p}"));
        }
        (moved_n, deleted_n) = (moved.len(), deleted.len());
        job.touch_sidecars(moved.into_iter().flat_map(|(a, b)| [a, b]).chain(deleted));
    }
    Ok(format!("{} folder(s): {new} new, {updated} updated, {unchanged} unchanged, {moved_n} moved between folders, {deleted_n} deleted", crawled.len()))
}

/// Delete Database: every table emptied, then every thumbnail dropped.
fn clear_library(lib: &Library, job: &Job) -> Result<String, String> {
    let ids: Vec<u64> = lib
        .db
        .with(|c| c.prepare("SELECT id FROM images")?.query_map([], |r| r.get::<_, i64>(0))?.collect::<rusqlite::Result<Vec<_>>>())
        .map_err(db_err)?
        .into_iter()
        .map(|i| i as u64)
        .collect();
    let models = lib.db.with(maintenance::model_count).map_err(db_err)?;
    lib.db.with_tx(maintenance::clear_all).map_err(db_err)?;
    lib.thumbs.delete_many(&ids).map_err(|e| e.to_string())?;
    lib.thumbs.delete_orphan_blobs().map_err(|e| e.to_string())?;
    lib.job_log(job, format!("Deleted {models} model(s) and {} thumbnail(s).", ids.len()));
    Ok(format!("library cleared: {models} model(s) deleted"))
}

fn delete_thumb_refs(lib: &Library, refs: &[String]) -> Result<(), String> {
    let ids: Vec<u64> = refs.iter().filter_map(|r| r.trim().parse().ok()).collect();
    lib.thumbs.delete_many(&ids).map_err(|e| e.to_string())
}

#[allow(clippy::too_many_arguments)]
fn crawl_folder(
    lib: &Library,
    job: &Job,
    folder: String,
    pattern: Option<String>,
    force_rescan: bool,
    recurse: Option<bool>,
    defer_missing_deletion: bool,
    new_paths: Option<&Mutex<Vec<String>>>,
) -> Result<crawler::Summary, String> {
    if !std::path::Path::new(&folder).is_dir() {
        return Err(format!("{folder} is not a folder the server can read"));
    }
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let pattern = match pattern {
        Some(p) => p,
        None => lib
            .db
            .with(duplicates::crawled_folders)
            .map_err(|e| e.to_string())?
            .into_iter()
            .find(|(p, _)| p == &folder)
            .and_then(|(_, pat)| pat)
            .unwrap_or_default(),
    };
    let patterns = settings.presupport_patterns();
    if patterns.iter().any(|p| stl_core::rules::portable_pattern(p).is_err()) {
        lib.job_log(job, "Warning: the custom presupport patterns use syntax only the desktop app reads; \
                          presupport warnings are skipped in this crawl."
            .into());
    }
    let unrar = lib.config.unrar_tool();
    let cfg = CrawlConfig {
        source_dir: folder.clone(),
        pattern,
        recurse: recurse.unwrap_or_else(|| settings.recurse_subdirs()),
        force_rescan,
        size_only: settings.detect_changes_by_size(),
        max_thumbnails: settings.max_thumbnails(),
        max_thumb_size: settings.max_thumb_size(),
        max_workers: settings.max_workers(),
        unrar,
        sidecars: settings.sidecars_enabled(),
        sidecar_thumbnails: settings.sidecar_thumbnails(),
        warn: settings.checks(),
        presupport_patterns: patterns,
        embed: settings.embed_during_crawl() && lib.can_embed(),
        log_unchanged: false,
        defer_missing_deletion,
        upsert: crawl_db::UpsertConfig {
            warn_low_compression: settings.warn_low_compression(),
            low_compression_threshold: settings.low_compression_threshold(),
            embed_dim: storage::schema_sql::EMBED_DIM as usize,
            ..Default::default()
        },
    };
    let host = ServerHost { lib, job, max_thumb_size: cfg.max_thumb_size, new_paths };
    crawler::crawl(&lib.config.db_path(), Some(&lib.thumbs), &cfg, &host).map_err(|e| e.to_string())
}

// ── maintenance ──────────────────────────────────────────────────────────────

fn db_err(e: rusqlite::Error) -> String {
    format!("database: {e}")
}

fn verify(lib: &Library, job: &Job) -> Result<String, String> {
    let v = lib.db.with(|c| maintenance::verify(c, &job.ctl)).map_err(db_err)?;
    let cleared = if v.reappeared.is_empty() {
        0
    } else {
        lib.db.with_tx(|tx| maintenance::clear_missing(tx, &v.reappeared)).map_err(db_err)?
    };
    let mut counts = std::collections::BTreeMap::<&str, usize>::new();
    for r in &v.results {
        *counts.entry(r.status).or_default() += 1;
        if r.status != maintenance::STATUS_OK {
            lib.job_log(job, format!("{}: {}", r.status, r.path));
        }
    }
    let parts: Vec<String> = counts.iter().map(|(s, n)| format!("{n} {s}")).collect();
    let checked = v.results.len();
    let problems = v.results.into_iter().filter(|r| r.status != maintenance::STATUS_OK).map(Into::into).collect();
    job.set_result(records(problems));
    Ok(format!("{checked} checked: {}; {cleared} back on disk", parts.join(", ")))
}

/// Logs a batch job's progress about every 5 % (and at the end), so the log of a
/// long job shows it moving.
fn log_progress(lib: &Library, job: &Job, done: usize, total: usize, last: &mut usize, line: impl FnOnce() -> String) {
    if done >= *last + (total / 20).max(1) || (done == total && done != *last) {
        *last = done;
        lib.job_log(job, format!("[{done}/{total}] {}", line()));
    }
}

fn recompute_dhashes(lib: &Library, job: &Job, all: bool) -> Result<String, String> {
    const DHASH_STR_LEN: i64 = 34; // "2:" + 32 hex digits
    let ids = lib.db.with(|c| maintenance::dhash_targets(c, all, DHASH_STR_LEN)).map_err(db_err)?;
    job.ctl.set_total(ids.len() as u64);
    lib.job_log(job, format!("Hashing {} image(s)…", ids.len()));
    let (mut computed, mut skipped, mut logged) = (0usize, 0usize, 0usize);
    for chunk in ids.chunks(DHASH_BATCH) {
        if job.stopped() {
            break;
        }
        let as_u64: Vec<u64> = chunk.iter().map(|&i| i as u64).collect();
        let mut thumbs: std::collections::HashMap<i64, Vec<u8>> =
            lib.thumbs.get_many(&as_u64).map_err(|e| e.to_string())?.into_iter().map(|(i, b)| (i as i64, b)).collect();
        let missing: Vec<i64> = chunk.iter().copied().filter(|i| !thumbs.contains_key(i)).collect();
        if !missing.is_empty() {
            thumbs.extend(lib.db.with(|c| maintenance::legacy_thumbnails(c, &missing)).map_err(db_err)?);
        }
        let have: Vec<i64> = chunk.iter().copied().filter(|i| thumbs.contains_key(i)).collect();
        let datas: Vec<&[u8]> = have.iter().map(|i| thumbs[i].as_slice()).collect();
        let rows: Vec<(i64, String)> = have
            .iter()
            .zip(stl_core::dhash::dhash_many(&datas))
            .filter_map(|(&i, h)| h.map(|h| (i, format!("2:{h:032x}"))))
            .collect();
        if !rows.is_empty() {
            lib.db.with_tx(|tx| maintenance::set_dhashes(tx, &rows)).map_err(db_err)?;
        }
        computed += rows.len();
        skipped += chunk.len() - rows.len();
        job.ctl.advance(chunk.len() as u64);
        log_progress(lib, job, computed + skipped, ids.len(), &mut logged, || format!("{computed} computed, {skipped} skipped"));
    }
    Ok(format!("{computed} computed, {skipped} skipped (no thumbnail or not decodable)"))
}

fn prune_images(lib: &Library, job: &Job, keep: usize) -> Result<String, String> {
    let ids = lib.db.with(|c| maintenance::models_over(c, keep as i64)).map_err(db_err)?;
    job.ctl.set_total(ids.len() as u64);
    let (mut models, mut images) = (0usize, 0usize);
    for chunk in ids.chunks(PRUNE_BATCH) {
        if job.stopped() {
            break;
        }
        let (pruned, excess) = lib.db.with(|c| maintenance::excess_images(c, chunk, keep)).map_err(db_err)?;
        if !excess.is_empty() {
            lib.db.with_tx(|tx| maintenance::delete_images(tx, &excess)).map_err(db_err)?;
            let refs: Vec<u64> = excess.iter().map(|&i| i as u64).collect();
            lib.thumbs.delete_many(&refs).map_err(|e| e.to_string())?;
        }
        models += pruned;
        images += excess.len();
        job.ctl.advance(chunk.len() as u64);
    }
    Ok(format!("{images} image(s) removed from {models} model(s)"))
}

fn compute_embeddings(lib: &Library, job: &Job, all: bool) -> Result<String, String> {
    use stl_core::storage::vectors;
    let log = |line: String| lib.job_log(job, line);
    if !lib.wait_for_embedder(&job.ctl, &log)? {
        return Ok("stopped".into());
    }
    let orphans = lib.db.with_tx(vectors::prune_orphans).map_err(db_err)?;
    if orphans > 0 {
        lib.job_log(job, format!("Removed {orphans} embedding(s) of deleted images."));
    }
    let rows = lib.db.with(|c| maintenance::embedding_targets(c, all)).map_err(db_err)?;
    job.ctl.set_total(rows.len() as u64);
    lib.job_log(job, format!("Computing embeddings of {} image(s)…", rows.len()));
    let dim = storage::schema_sql::EMBED_DIM as usize;
    let (mut computed, mut skipped, mut logged) = (0usize, 0usize, 0usize);
    for chunk in rows.chunks(crate::tools::BATCH * 4) {
        if job.stopped() {
            break;
        }
        let ids: Vec<u64> = chunk.iter().map(|(i, _)| *i as u64).collect();
        let mut thumbs: std::collections::HashMap<i64, Vec<u8>> =
            lib.thumbs.get_many(&ids).map_err(|e| e.to_string())?.into_iter().map(|(i, b)| (i as i64, b)).collect();
        for (id, legacy) in chunk {
            if let (false, Some(b)) = (thumbs.contains_key(id), legacy) {
                thumbs.insert(*id, b.clone());
            }
        }
        let have: Vec<i64> = chunk.iter().map(|(i, _)| *i).filter(|i| thumbs.contains_key(i)).collect();
        let images: Vec<Vec<u8>> = have.iter().map(|i| thumbs.remove(i).unwrap_or_default()).collect();
        // A compute worker may leave mid-job: wait for another (or fail as at the start).
        let vectors = loop {
            match lib.embed(&images)? {
                Some(v) => break v,
                None if lib.wait_for_embedder(&job.ctl, &log)? => {}
                None => return Ok(format!("stopped: {computed} computed, {skipped} skipped")),
            }
        };
        let items: Vec<(i64, Vec<u8>)> = have.iter().zip(vectors).filter_map(|(&i, v)| v.map(|v| (i, v))).collect();
        let stored = lib.db.with_tx(|tx| vectors::store(tx, dim, &items)).map_err(db_err)?;
        computed += stored;
        skipped += chunk.len() - stored;
        job.ctl.advance(chunk.len() as u64);
        log_progress(lib, job, computed + skipped, rows.len(), &mut logged, || format!("{computed} computed, {skipped} skipped"));
    }
    Ok(format!("{computed} computed, {skipped} skipped (no thumbnail, not decodable or a wrong model output)"))
}

fn compact(lib: &Library, job: &Job) -> Result<String, String> {
    let size = |p: &std::path::Path| std::fs::metadata(p).map(|m| m.len()).unwrap_or(0);
    let path = lib.config.db_path();
    let before = size(&path);
    lib.job_log(job, "Rewriting the database without free pages…".into());
    lib.db.close(); // idle connections would keep the write-ahead log busy
    let conn = storage::open(&path).map_err(db_err)?;
    let truncated = maintenance::compact(&conn).map_err(db_err)?;
    drop(conn);
    let after = size(&path);
    let mb = |b: u64| b as f64 / 1e6;
    lib.job_log(job, format!("Database: {:.1} MB → {:.1} MB.", mb(before), mb(after)));
    let swept = lib.thumbs.delete_orphan_blobs().map_err(|e| e.to_string())?;
    lib.job_log(job, format!("Removed {swept} thumbnail(s) no image uses; rewriting the thumbnail store (thumbnails wait meanwhile)…"));
    let (thumbs_before, thumbs_after) = lib.thumbs.compact().map_err(|e| format!("thumbnail store: {e}"))?;
    Ok(format!(
        "database {:.1} MB → {:.1} MB{}; thumbnails {:.1} MB → {:.1} MB",
        mb(before),
        mb(after),
        if truncated { "" } else { " (the write-ahead log is truncated at a later checkpoint)" },
        mb(thumbs_before),
        mb(thumbs_after)
    ))
}
