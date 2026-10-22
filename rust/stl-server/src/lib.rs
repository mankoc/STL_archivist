//! stlLibrary server (Phase 5 of `docs/rust-migration-plan.md`): the HTTP API
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
    _lock: LibraryLock,
}

pub type AppState = Arc<Library>;

impl Library {
    /// Lock the data directory, migrate the database and open the thumbnail store.
    pub fn open(config: Config) -> Result<Library, String> {
        let owner = format!("stl-server {}, pid {}", stl_core::VERSION, std::process::id());
        let lock = LibraryLock::acquire(&config.data_dir, &owner).map_err(|e| e.to_string())?;
        let report = storage::migrate(&config.db_path(), &storage::Vectors::default())
            .map_err(|e| format!("database {}: {e}", config.db_path().display()))?;
        tracing::info!(?report, "database ready");
        let thumbs = ThumbStore::open(&config.thumb_dir())
            .map_err(|e| format!("thumbnail store {}: {e}", config.thumb_dir().display()))?;
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
