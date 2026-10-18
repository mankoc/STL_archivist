//! Debounced `METADATA.7z` refresh after edits (as `app/ui/sidecar_exporter.py`):
//! edited folders are collected for a quiet period, then each is exported
//! once, one at a time, on the blocking pool. A no-op when the library's
//! `metadata_sidecar_enabled` setting is off. Pending folders are exported on
//! shutdown ([`Library::export_pending_sidecars`]).

use std::collections::BTreeSet;
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::time::Duration;

use tokio::sync::Notify;

use crate::Library;
use crate::library_settings::LibrarySettings;

pub const DEBOUNCE: Duration = Duration::from_millis(1500);

#[derive(Default)]
pub struct SidecarQueue {
    pending: Mutex<BTreeSet<String>>,
    wake: Arc<Notify>,
    started: OnceLock<()>,
}

impl SidecarQueue {
    /// Folders waiting for an export.
    pub fn pending(&self) -> Vec<String> {
        self.pending.lock().unwrap_or_else(|e| e.into_inner()).iter().cloned().collect()
    }

    fn take(&self) -> Vec<String> {
        std::mem::take(&mut *self.pending.lock().unwrap_or_else(|e| e.into_inner())).into_iter().collect()
    }
}

/// The folder an archive lies in, as the exporter expects it (`str(Path(p).parent)`).
pub fn folder_of(archive_path: &str) -> Option<String> {
    std::path::Path::new(archive_path).parent().map(|p| p.to_string_lossy().into_owned()).filter(|p| !p.is_empty())
}

impl Library {
    /// Queue the folders of `archive_paths` for a sidecar refresh. Call after
    /// the edit's transaction committed, from within the tokio runtime.
    pub fn schedule_sidecars<I: IntoIterator<Item = String>>(self: &Arc<Self>, archive_paths: I) {
        if !LibrarySettings::load(&self.config.data_dir).sidecars_enabled() {
            return;
        }
        let folders: Vec<String> = archive_paths.into_iter().filter_map(|p| folder_of(&p)).collect();
        if folders.is_empty() {
            return;
        }
        self.sidecars.pending.lock().unwrap_or_else(|e| e.into_inner()).extend(folders);
        self.sidecars.started.get_or_init(|| {
            tokio::spawn(worker(Arc::downgrade(self), Arc::clone(&self.sidecars.wake)));
        });
        self.sidecars.wake.notify_one();
    }

    /// Export every pending folder now, on this thread.
    pub fn export_pending_sidecars(&self) {
        let folders = self.sidecars.take();
        if folders.is_empty() {
            return;
        }
        let thumbs = LibrarySettings::load(&self.config.data_dir).sidecar_thumbnails();
        for folder in folders {
            // An archive moved out of a folder that is gone since: nothing to refresh.
            if !std::path::Path::new(&folder).is_dir() {
                tracing::debug!(%folder, "sidecar skipped: the folder no longer exists");
                continue;
            }
            let r = self.db.with(|c| {
                Ok::<_, rusqlite::Error>(stl_core::storage::sidecar::export(c, Some(&self.thumbs), &folder, thumbs))
            });
            match r {
                Ok(Ok(written)) => tracing::debug!(%folder, written, "sidecar refreshed"),
                // Best effort, as in the desktop app: a failed sidecar never fails an edit.
                Ok(Err(e)) => tracing::warn!(%folder, "sidecar export failed: {e:?}"),
                Err(e) => tracing::warn!(%folder, "sidecar export failed: {e}"),
            }
        }
    }
}

async fn worker(lib: Weak<Library>, wake: Arc<Notify>) {
    loop {
        wake.notified().await;
        // Wait until edits stop arriving for a while.
        while tokio::time::timeout(DEBOUNCE, wake.notified()).await.is_ok() {}
        let Some(l) = lib.upgrade() else { return };
        let _ = tokio::task::spawn_blocking(move || l.export_pending_sidecars()).await;
    }
}
