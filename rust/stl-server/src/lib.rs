//! StlArchivist server (Phase 5 of `docs/rust-migration-plan.md`): the HTTP API
//! over `stl-core`. The server owns the data directory (compatibility rule 7):
//! it holds the library lock, migrates the database and opens the thumbnail
//! store once. Accounts live in their own database (`accounts`).
//!
//! Database and store work is blocking; handlers run it on tokio's blocking
//! pool through [`Library::run`].

pub mod accounts;
pub mod ai;
pub mod archive_tools;
pub mod api;
pub mod auth;
pub mod components;
pub mod config;
pub mod dupes;
pub mod error;
pub mod jobs;
pub mod library_settings;
pub mod maint;
pub mod monitor;
pub mod sidecars;
pub mod tools;
pub mod worker;
pub mod workers;

use std::sync::Arc;

use stl_core::lock::LibraryLock;
use stl_core::storage::{self, pool::Pool};
use stl_core::thumbstore::ThumbStore;

pub use config::Config;
pub use error::ApiError;

/// Everything a request handler needs.
pub struct Library {
    pub config: Config,
    pub db: Pool,
    pub thumbs: ThumbStore,
    /// `server.db`: users, sessions, per-user settings.
    pub accounts: Pool,
    pub logins: auth::LoginThrottle,
    pub sidecars: sidecars::SidecarQueue,
    pub jobs: jobs::Jobs,
    pub tools: tools::Tools,
    pub ai: Arc<ai::AiRuntime>,
    /// Connected compute workers (see `workers`).
    pub workers: workers::Workers,
    /// When monitored folders were last checked (see `monitor`).
    pub monitor: monitor::Monitor,
    _lock: LibraryLock,
}

pub type AppState = Arc<Library>;

/// The move to libraries is one-way: back the library up first (into
/// `backups/`, named `…_pre_libraries.7z`), and refuse to go on without it.
fn backup_before_libraries(config: &Config, thumbs: &ThumbStore) -> Result<(), String> {
    let db = config.db_path();
    if !db.is_file() {
        return Ok(());
    }
    let conn = storage::open(&db).map_err(|e| format!("database {}: {e}", db.display()))?;
    if !storage::libraries::needs_migration(&conn).map_err(|e| e.to_string())? {
        return Ok(());
    }
    let name = api::backups::new_name().replace(".7z", "_pre_libraries.7z");
    let dest = api::backups::dir(&config.data_dir).join(name);
    let settings = config.data_dir.join(stl_core::backup::SETTINGS_FILE);
    tracing::info!(backup = %dest.display(), "backing up before moving archive paths to libraries");
    stl_core::backup::write(&conn, thumbs, settings.is_file().then_some(settings.as_path()), &dest, &stl_core::control::Control::new())
        .map_err(|e| format!("could not back up the library before moving it to libraries ({e}); nothing was changed"))?;
    Ok(())
}

impl Library {
    /// Lock the data directory, migrate the database and open the thumbnail store.
    pub fn open(config: Config) -> Result<Library, String> {
        let owner = format!("stl-server {}, pid {}", stl_core::VERSION, std::process::id());
        let lock = LibraryLock::acquire(&config.data_dir, &owner).map_err(|e| e.to_string())?;
        let thumbs = ThumbStore::open(&config.thumb_dir())
            .map_err(|e| format!("thumbnail store {}: {e}", config.thumb_dir().display()))?;
        backup_before_libraries(&config, &thumbs)?;
        let mut report = storage::migrate(&config.db_path(), &storage::Vectors::default())
            .map_err(|e| format!("database {}: {e}", config.db_path().display()))?;
        if let Some(p) = &mut report.paths {
            // The merged-away duplicates' thumbnails (taken out: not for the log).
            let refs = std::mem::take(&mut p.thumbnail_refs);
            tracing::warn!(
                libraries = p.libraries,
                extra_libraries = p.extra_libraries,
                models = p.models,
                duplicates_merged = p.duplicates_merged,
                thumbnails_deleted = refs.len(),
                "archive paths moved to libraries (relative paths); the earlier desktop app can no longer open this library"
            );
            let ids: Vec<u64> = refs.iter().filter_map(|r| r.trim().parse().ok()).collect();
            if !ids.is_empty() {
                thumbs.delete_many(&ids).map_err(|e| format!("thumbnail store: {e}"))?;
            }
        }
        tracing::info!(?report, "database ready");
        let accounts_path = config.data_dir.join(accounts::DB_FILE);
        let accounts = Pool::new(&accounts_path);
        accounts
            .with(accounts::ensure_schema)
            .map_err(|e| format!("accounts {}: {e}", accounts_path.display()))?;
        Ok(Library {
            db: Pool::new(&config.db_path()),
            thumbs,
            accounts,
            logins: auth::LoginThrottle::default(),
            sidecars: sidecars::SidecarQueue::default(),
            jobs: jobs::Jobs::default(),
            tools: tools::Tools::default(),
            ai: Arc::default(),
            workers: workers::Workers::default(),
            monitor: monitor::Monitor::default(),
            config,
            _lock: lock,
        })
    }

    /// Run blocking work `f` on the blocking pool.
    pub async fn run<T: Send + 'static>(
        self: &Arc<Self>,
        f: impl FnOnce(&Library) -> Result<T, ApiError> + Send + 'static,
    ) -> Result<T, ApiError> {
        let lib = Arc::clone(self);
        tokio::task::spawn_blocking(move || f(&lib)).await.map_err(|e| ApiError::internal(format!("task failed: {e}")))?
    }
}

/// The application router. Serve it with
/// `into_make_service_with_connect_info::<SocketAddr>()`: first-run setup
/// checks the peer address.
pub fn app(state: AppState) -> axum::Router {
    let web = state.config.web_dir();
    let router = api::router(Arc::clone(&state)).with_state(state);
    match web {
        // The client's files, and index.html for its routes (a single-page app).
        Some(dir) => {
            let index = tower_http::services::ServeFile::new(dir.join("index.html"));
            router.fallback_service(tower_http::services::ServeDir::new(dir).fallback(index))
        }
        None => router,
    }
}
