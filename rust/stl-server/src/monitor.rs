//! Monitored folders: crawled folders with `monitored` set are checked every
//! `monitor_interval_minutes` (library setting) by size and date
//! (`stl_core::monitor`); when a crawl would find new, changed or vanished
//! archives, a crawl job is queued for the folder, submitted by [`USER`].
//! Vanished archives are marked missing, as any crawl does; *Remove missing
//! archives* in Maintenance deletes their records.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use stl_core::storage::libraries;

use crate::Library;
use crate::jobs::{JobInfo, JobRequest, JobState};
use crate::library_settings::LibrarySettings;

struct MonitoredFolder {
    path: String,
}

/// Who the monitor's crawl jobs are from.
pub const USER: &str = "monitor";
const TICK: Duration = Duration::from_secs(60);

/// When each monitored folder was last checked.
#[derive(Default)]
pub struct Monitor {
    checked: Mutex<HashMap<String, Instant>>,
}

/// A crawl of `folder` (or of every crawled folder) is already waiting or running.
fn busy(jobs: &[JobInfo], folder: &str) -> bool {
    jobs.iter().any(|j| {
        matches!(j.state, JobState::Queued | JobState::Running)
            && match &j.request {
                JobRequest::Crawl { folder: f, .. } => f == folder,
                JobRequest::UpdateFolders { .. } => true,
                _ => false,
            }
    })
}

/// Check the monitored folders that are due (all of them with `force`), and
/// queue a crawl for each that changed. Returns the queued jobs.
pub fn check(lib: &Arc<Library>, force: bool) -> Vec<JobInfo> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let every = Duration::from_secs(60 * u64::from(settings.monitor_interval_minutes()));
    let folders = match lib.db.with(libraries::list) {
        Ok(f) => f,
        Err(e) => {
            tracing::warn!("monitored folders: {e}");
            return Vec::new();
        }
    };
    let mut queued = Vec::new();
    for f in folders.into_iter().filter(|f| f.monitored && !f.root.is_empty()) {
        let f = MonitoredFolder { path: f.root };
        {
            let mut checked = lib.monitor.checked.lock().unwrap_or_else(|e| e.into_inner());
            if !force && checked.get(&f.path).is_some_and(|t| t.elapsed() < every) {
                continue;
            }
            checked.insert(f.path.clone(), Instant::now());
        }
        if busy(&lib.jobs.list(), &f.path) {
            continue;
        }
        let changes = match lib.db.with(|c| stl_core::monitor::changes(c, &f.path, settings.recurse_subdirs())) {
            Ok(c) => c,
            Err(e) => {
                tracing::warn!(folder = %f.path, "monitored folder: {e}");
                continue;
            }
        };
        if changes.any() {
            tracing::info!(folder = %f.path, new = changes.new, modified = changes.modified, gone = changes.gone, "monitored folder changed: crawling");
            let request = JobRequest::Crawl { folder: f.path.clone(), pattern: None, force_rescan: false, recurse: None, keep_missing: true };
            queued.push(lib.submit_job(request, USER));
        }
    }
    queued
}

/// Check monitored folders in the background for the server's lifetime.
pub fn start(lib: &Arc<Library>) {
    let weak = Arc::downgrade(lib);
    std::thread::spawn(move || {
        loop {
            std::thread::sleep(TICK);
            let Some(lib) = weak.upgrade() else { return };
            check(&lib, false);
        }
    });
}
