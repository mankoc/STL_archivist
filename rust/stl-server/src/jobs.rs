//! Server jobs: crawls and maintenance, one at a time in submission order
//! (SQLite has one writer, as in the desktop app). Progress, phases and log
//! lines go to the job's record and to every WebSocket client
//! ([`ServerEvent`]); a job stops at its next checkpoint when cancelled.

use std::collections::VecDeque;
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
    },
    /// Check that every archive is still on disk, unchanged in size.
    VerifyArchives,
    /// Compute perceptual hashes from stored thumbnails (all, or only missing / outdated).
    RecomputeDhashes {
        #[serde(default)]
        all: bool,
    },
    /// Keep at most `keep` images per model (the oldest).
    PruneImages { keep: usize },
    /// `VACUUM` the database and truncate its write-ahead log.
    CompactDatabase,
    /// Compute CLIP embeddings from stored thumbnails (all, or only images without one).
    ComputeEmbeddings {
        #[serde(default)]
        all: bool,
    },
}

impl JobRequest {
    pub fn kind(&self) -> &'static str {
        match self {
            JobRequest::Crawl { .. } => "crawl",
            JobRequest::VerifyArchives => "verify_archives",
            JobRequest::RecomputeDhashes { .. } => "recompute_dhashes",
            JobRequest::PruneImages { .. } => "prune_images",
            JobRequest::CompactDatabase => "compact_database",
            JobRequest::ComputeEmbeddings { .. } => "compute_embeddings",
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
}

impl Job {
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
        l.changed(job.info().kind);
    }
}

fn run(lib: &Library, job: &Job) -> Result<String, String> {
    let request = job.info().request;
    match request {
        JobRequest::Crawl { folder, pattern, force_rescan, recurse } => crawl(lib, job, folder, pattern, force_rescan, recurse),
        JobRequest::VerifyArchives => verify(lib, job),
        JobRequest::RecomputeDhashes { all } => recompute_dhashes(lib, job, all),
        JobRequest::PruneImages { keep } => prune_images(lib, job, keep),
        JobRequest::CompactDatabase => compact(lib, job),
        JobRequest::ComputeEmbeddings { all } => compute_embeddings(lib, job, all),
    }
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
}

impl Host for ServerHost<'_> {
    fn call(&self, request: Request) -> Response {
        let config = &self.lib.config;
        match request {
            Request::Thumbnail { data, pdf: true } => match self.lib.tools.pdf(config) {
                Some(r) => Response::Thumbnail(r.thumbnail(&data, self.max_thumb_size)),
                None => Response::None,
            },
            Request::Embed(images) => match self.lib.tools.embedder(config) {
                Some(e) => Response::Vectors(e.embed(&images)),
                None => Response::None,
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
) -> Result<String, String> {
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
        store_stl: settings.store_stl_file_list(),
        max_thumbnails: settings.max_thumbnails(),
        max_thumb_size: settings.max_thumb_size(),
        max_workers: settings.max_workers(),
        unrar,
        sidecars: settings.sidecars_enabled(),
        sidecar_thumbnails: settings.sidecar_thumbnails(),
        warn: settings.checks(),
        presupport_patterns: patterns,
        embed: settings.embed_during_crawl() && lib.tools.embedder(&lib.config).is_some(),
        log_unchanged: false,
        defer_missing_deletion: false,
        upsert: crawl_db::UpsertConfig {
            store_stl: settings.store_stl_file_list(),
            warn_low_compression: settings.warn_low_compression(),
            low_compression_threshold: settings.low_compression_threshold(),
            embed_dim: storage::schema_sql::EMBED_DIM as usize,
            ..Default::default()
        },
    };
    let host = ServerHost { lib, job, max_thumb_size: cfg.max_thumb_size };
    let summary = crawler::crawl(&lib.config.db_path(), Some(&lib.thumbs), &cfg, &host).map_err(|e| e.to_string())?;
    Ok(format!("{} new, {} updated, {} unchanged", summary.new, summary.updated, summary.unchanged))
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
    Ok(format!("{} checked: {}; {cleared} back on disk", v.results.len(), parts.join(", ")))
}

fn recompute_dhashes(lib: &Library, job: &Job, all: bool) -> Result<String, String> {
    const DHASH_STR_LEN: i64 = 34; // "2:" + 32 hex digits
    let ids = lib.db.with(|c| maintenance::dhash_targets(c, all, DHASH_STR_LEN)).map_err(db_err)?;
    job.ctl.set_total(ids.len() as u64);
    let (mut computed, mut skipped) = (0usize, 0usize);
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
    let embedder = lib.tools.embedder(&lib.config).ok_or_else(|| {
        "CLIP embeddings are unavailable: the server needs ONNX Runtime and the CLIP model (see the server log)".to_string()
    })?;
    let orphans = lib.db.with_tx(vectors::prune_orphans).map_err(db_err)?;
    if orphans > 0 {
        lib.job_log(job, format!("Removed {orphans} embedding(s) of deleted images."));
    }
    let rows = lib.db.with(|c| maintenance::embedding_targets(c, all)).map_err(db_err)?;
    job.ctl.set_total(rows.len() as u64);
    let dim = storage::schema_sql::EMBED_DIM as usize;
    let (mut computed, mut skipped) = (0usize, 0usize);
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
        let items: Vec<(i64, Vec<u8>)> =
            have.iter().zip(embedder.embed(&images)).filter_map(|(&i, v)| v.map(|v| (i, v))).collect();
        let stored = lib.db.with_tx(|tx| vectors::store(tx, dim, &items)).map_err(db_err)?;
        computed += stored;
        skipped += chunk.len() - stored;
        job.ctl.advance(chunk.len() as u64);
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
    Ok(format!(
        "{:.1} MB → {:.1} MB{}",
        before as f64 / 1e6,
        after as f64 / 1e6,
        if truncated { "" } else { " (the write-ahead log is truncated at a later checkpoint)" }
    ))
}
