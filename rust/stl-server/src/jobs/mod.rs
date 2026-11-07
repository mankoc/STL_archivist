//! Server jobs: crawls and maintenance, one at a time in submission order
//! (SQLite has one writer, as in the desktop app). Progress, phases and log
//! lines go to the job's record and to every WebSocket client
//! ([`ServerEvent`]); a job stops at its next checkpoint when cancelled.

mod archives;
mod crawling;
mod finders;
mod request;
mod result;
mod sidecars;
mod transfer;
mod upkeep;

use std::collections::{BTreeMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use stl_core::control::Control;
use stl_core::crawler::{self, CrawlConfig, Event, Host};
use stl_core::storage::{self, crawl as crawl_db, duplicates, maintenance};
use tokio::sync::{Notify, broadcast};
use utoipa::ToSchema;

use crate::Library;
use crate::accounts;
use crate::library_settings::LibrarySettings;

use archives::*;
use crawling::*;
use finders::*;
use sidecars::*;
use transfer::*;
use upkeep::*;
pub use request::JobRequest;
pub use result::*;

/// Finished jobs kept for listing.
const KEEP_FINISHED: usize = 50;
/// Log lines kept per job.
const KEEP_LOG: usize = 2000;
/// Archive paths kept per list of a job's changes (the counts go on).
const KEEP_CHANGES: usize = 10_000;
/// How often a running job's progress is published.
const TICK: Duration = Duration::from_millis(500);
const DHASH_BATCH: usize = 500;
const PRUNE_BATCH: usize = 200;

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
    // Shared: `Job::info` runs at every progress step, and this can list 100,000 ids.
    #[schema(value_type = JobRequest)]
    pub request: Arc<JobRequest>,
    pub state: JobState,
    /// Who submitted it.
    pub user: String,
    pub phase: Option<String>,
    pub done: u64,
    pub total: u64,
    /// Archives added, updated and deleted so far (crawls and Update libraries).
    pub counts: Option<JobCounts>,
    /// The outcome (a summary, or the error).
    pub message: Option<String>,
    pub created_at: i64,
    pub started_at: Option<i64>,
    pub finished_at: Option<i64>,
    /// Paused: a running job waits at its next checkpoint, a queued one is
    /// passed over, until it is resumed.
    pub paused: bool,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, ToSchema)]
pub struct JobCounts {
    pub new: u64,
    pub updated: u64,
    pub deleted: u64,
}

/// The archives behind a job's [`JobCounts`] (at most 10,000 in each list).
#[derive(Debug, Clone, Default, Serialize, ToSchema)]
pub struct JobChanges {
    pub new: Vec<String>,
    pub updated: Vec<String>,
    pub deleted: Vec<String>,
}

#[derive(Clone, Copy)]
enum Change {
    New,
    Updated,
    Deleted,
}

/// What WebSocket clients receive (`/api/events`), as JSON text frames.
#[derive(Debug, Clone, Serialize, ToSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ServerEvent {
    /// A job was queued, started, progressed or finished.
    Job { job: Box<JobInfo> },
    /// One log line of a running job.
    JobLog { id: u64, line: String },
    /// The library changed (`models`, `lookups`, `crawl`, …): reload what is shown.
    LibraryChanged { what: String },
    /// Events were dropped for this client (it was too slow); reload everything.
    Resync,
    /// Finished jobs were cleared: reload the job list.
    JobsCleared,
}

pub struct Job {
    info: Mutex<JobInfo>,
    /// The request as events carry it ([`JobRequest::brief`]).
    brief: Arc<JobRequest>,
    log: Mutex<VecDeque<String>>,
    stop: AtomicBool,
    ctl: Control,
    result: Mutex<Option<crate::jobs::JobResult>>,
    /// Archive paths whose folders' sidecars need a refresh once the job ends.
    sidecars: Mutex<Vec<String>>,
    changes: Mutex<JobChanges>,
}

impl Job {
    /// What a finder job found (`None` for other jobs, or until it finishes).
    pub fn result(&self) -> Option<crate::jobs::JobResult> {
        self.result.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    fn set_result(&self, v: crate::jobs::JobResult) {
        *self.result.lock().unwrap_or_else(|e| e.into_inner()) = Some(v);
    }

    /// The archives a crawl added, updated and deleted (`None` for jobs without counts).
    pub fn changes(&self) -> Option<JobChanges> {
        self.info().counts.map(|_| self.changes.lock().unwrap_or_else(|e| e.into_inner()).clone())
    }

    fn record(&self, change: Change, paths: impl IntoIterator<Item = String>) {
        let mut ch = self.changes.lock().unwrap_or_else(|e| e.into_inner());
        let list = match change {
            Change::New => &mut ch.new,
            Change::Updated => &mut ch.updated,
            Change::Deleted => &mut ch.deleted,
        };
        let mut n = 0;
        for p in paths {
            n += 1;
            if list.len() < KEEP_CHANGES {
                list.push(p);
            }
        }
        update(self, |i| {
            let c = i.counts.get_or_insert_with(JobCounts::default);
            match change {
                Change::New => c.new += n,
                Change::Updated => c.updated += n,
                Change::Deleted => c.deleted += n,
            }
        });
    }

    /// Archives recorded as new that were moved from another folder: neither new nor deleted.
    fn unrecord_moved(&self, new_paths: &[&str]) {
        let keys: HashSet<String> = new_paths.iter().map(|p| stl_core::relpath::path_key(p)).collect();
        let mut ch = self.changes.lock().unwrap_or_else(|e| e.into_inner());
        ch.new.retain(|p| !keys.contains(&stl_core::relpath::path_key(p)));
        update(self, |i| {
            let c = i.counts.get_or_insert_with(JobCounts::default);
            c.new = c.new.saturating_sub(keys.len() as u64);
        });
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

    /// A checkpoint: whether to stop; while paused, it waits until resumed or cancelled.
    fn stopped(&self) -> bool {
        self.ctl.is_stopped() || self.stop.load(Ordering::Relaxed)
    }

    fn finished(&self) -> bool {
        matches!(self.info().state, JobState::Done | JobState::Failed | JobState::Cancelled)
    }
}

pub struct Jobs {
    list: Mutex<Vec<Arc<Job>>>,
    next_id: AtomicU64,
    wake: Arc<Notify>,
    started: OnceLock<()>,
    /// The server is stopping: no job starts any more ([`Jobs::close`]).
    closed: AtomicBool,
    pub events: broadcast::Sender<ServerEvent>,
}

impl Default for Jobs {
    fn default() -> Self {
        Jobs {
            list: Mutex::new(Vec::new()),
            next_id: AtomicU64::new(1),
            wake: Arc::new(Notify::new()),
            started: OnceLock::new(),
            closed: AtomicBool::new(false),
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
        let info = JobInfo { request: Arc::clone(&job.brief), ..job.info() };
        self.send(ServerEvent::Job { job: Box::new(info) });
    }

    fn next_queued(&self) -> Option<Arc<Job>> {
        if self.is_closed() {
            return None;
        }
        self.list
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .find(|j| {
                let i = j.info();
                i.state == JobState::Queued && !i.paused
            })
            .cloned()
    }

    fn trim(&self) {
        let mut list = self.list.lock().unwrap_or_else(|e| e.into_inner());
        let mut excess = list.iter().filter(|j| j.finished()).count().saturating_sub(KEEP_FINISHED);
        list.retain(|j| {
            if j.finished() && excess > 0 {
                excess -= 1;
                false
            } else {
                true
            }
        });
    }

    /// Remove the finished jobs, except those holding a result: the duplicate,
    /// record and AI tagger pages read theirs from the job. Gives how many went.
    pub fn clear_finished(&self) -> usize {
        let mut list = self.list.lock().unwrap_or_else(|e| e.into_inner());
        let before = list.len();
        list.retain(|j| !j.finished() || j.result().is_some());
        let cleared = before - list.len();
        drop(list);
        if cleared > 0 {
            self.send(ServerEvent::JobsCleared);
        }
        cleared
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

    /// The server is stopping: no job starts any more, the queued ones are cancelled
    /// and the running one is asked to stop at its next checkpoint.
    pub fn close(&self) {
        self.closed.store(true, Ordering::SeqCst);
        let jobs: Vec<Arc<Job>> = self.list.lock().unwrap_or_else(|e| e.into_inner()).clone();
        for job in jobs {
            match job.info().state {
                JobState::Queued => {
                    finish(&job, JobState::Cancelled, Some("the server stopped before it started".into()));
                    self.publish(&job);
                }
                JobState::Running => {
                    job.stop.store(true, Ordering::Relaxed);
                    job.ctl.request_stop();
                }
                _ => {}
            }
        }
    }

    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::SeqCst)
    }

    /// A job is running.
    pub fn running(&self) -> bool {
        self.list.lock().unwrap_or_else(|e| e.into_inner()).iter().any(|j| j.info().state == JobState::Running)
    }

    /// Pause or resume a queued or running job. `false` when it has already finished.
    pub fn pause(&self, job: &Job, paused: bool) -> bool {
        if !matches!(job.info().state, JobState::Queued | JobState::Running) {
            return false;
        }
        update(job, |i| i.paused = paused);
        job.ctl.set_paused(paused);
        self.publish(job);
        if !paused {
            self.wake.notify_one();
        }
        true
    }
}

fn update(job: &Job, f: impl FnOnce(&mut JobInfo)) {
    f(&mut job.info.lock().unwrap_or_else(|e| e.into_inner()));
}

fn finish(job: &Job, state: JobState, message: Option<String>) {
    update(job, |i| {
        i.state = state;
        i.paused = false;
        i.message = message;
        i.finished_at = Some(accounts::now());
    });
}

impl Library {
    /// Queue a job; it runs after the jobs before it.
    pub fn submit_job(self: &Arc<Self>, request: JobRequest, user: &str) -> JobInfo {
        let jobs = &self.jobs;
        let id = jobs.next_id.fetch_add(1, Ordering::Relaxed);
        let brief = Arc::new(request.brief());
        let job = Arc::new(Job {
            brief,
            info: Mutex::new(JobInfo {
                id,
                kind: request.kind(),
                request: Arc::new(request),
                state: JobState::Queued,
                user: user.to_string(),
                phase: None,
                done: 0,
                total: 0,
                counts: None,
                message: None,
                created_at: accounts::now(),
                started_at: None,
                finished_at: None,
                paused: false,
            }),
            log: Mutex::new(VecDeque::new()),
            stop: AtomicBool::new(false),
            ctl: Control::new(),
            result: Mutex::new(None),
            sidecars: Mutex::new(Vec::new()),
            changes: Mutex::new(JobChanges::default()),
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
        if what != crate::ROWS_CHANGED {
            self.generation.fetch_add(1, Ordering::SeqCst);
        }
        self.jobs.send(ServerEvent::LibraryChanged { what: what.to_string() });
    }

    /// When the server stops: no job starts any more, queued ones are cancelled and the
    /// running one is asked to stop. Waits until it has, at most `grace`; `false` when it
    /// is still running (it then ends with the process).
    pub async fn stop_jobs(&self, grace: Duration) -> bool {
        self.jobs.close();
        let deadline = tokio::time::Instant::now() + grace;
        while self.jobs.running() {
            if tokio::time::Instant::now() >= deadline {
                return false;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        true
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
        // Only a job still queued starts: one cancelled since it was picked stays cancelled.
        let mut starts = false;
        update(&job, |i| {
            if i.state == JobState::Queued {
                i.state = JobState::Running;
                i.started_at = Some(accounts::now());
                starts = true;
            }
        });
        if !starts {
            continue;
        }
        l.jobs.publish(&job);
        // Publish progress while the job runs.
        let ticker = {
            let (l, job) = (Arc::clone(&l), Arc::clone(&job));
            tokio::spawn(async move {
                let (mut last, mut last_counts) = ((u64::MAX, u64::MAX), None);
                loop {
                    tokio::time::sleep(TICK).await;
                    let (done, total) = job.ctl.progress();
                    let counts = job.info().counts;
                    let moved = (done, total) != last && total > 0;
                    if moved || counts != last_counts {
                        if moved {
                            update(&job, |i| (i.done, i.total) = (done, total));
                            last = (done, total);
                        }
                        l.jobs.publish(&job);
                        last_counts = counts;
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
            Ok(summary) if job.stop.load(Ordering::Relaxed) => finish(&job, JobState::Cancelled, Some(format!("stopped: {summary}"))),
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
    let request = JobRequest::clone(&job.info().request);
    match request {
        JobRequest::Crawl { folder, pattern, force_rescan, recurse, keep_missing, in_library } => {
            crawl(lib, job, folder, pattern, force_rescan, recurse, keep_missing, in_library)
        }
        JobRequest::UpdateFolders { folders } => update_folders(lib, job, folders),
        JobRequest::ClearLibrary { .. } => clear_library(lib, job),
        JobRequest::VerifyArchives => verify(lib, job),
        JobRequest::ReadFileLists => read_file_lists(lib, job),
        JobRequest::RereadLinks { overwrite } => reread_links(lib, job, overwrite),
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
        JobRequest::SortFiles { ids, target_dir, pattern, missing } => {
            sort_files(lib, job, &ids, &target_dir, &pattern, missing.as_deref().unwrap_or(stl_core::sort_files::UNKNOWN))
        }
        JobRequest::RelocateLibrary { id, root, apply, fix_case } => relocate_library(lib, job, id, &root, apply, fix_case),
        JobRequest::AiSuggest { id, max_tags, ignore_meta } => ai_suggest(lib, job, id, max_tags, ignore_meta),
        JobRequest::AiTag { ids, overwrite, ignore_meta, max_tags } => ai_tag(lib, job, &ids, overwrite, ignore_meta, max_tags),
        JobRequest::GenerateSidecars => generate_sidecars(lib, job),
        JobRequest::RemoveSidecars => remove_sidecars(lib, job),
        JobRequest::CleanSidecars => clean_sidecars(lib, job),
        JobRequest::Backup => backup(lib, job),
        JobRequest::ImportLibrary { name, roots, rules, apply, backup_first, .. } => {
            import_library(lib, job, &name, &roots, &rules, apply, backup_first)
        }
        JobRequest::Sync { remote, direction, backup_first, .. } => sync(lib, job, remote, direction, backup_first),
        JobRequest::SyncExport { have, blocks } => sync_export(lib, job, &have, blocks.as_deref()),
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
