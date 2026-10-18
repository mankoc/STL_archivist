//! stlLibrary server (Phase 5 of `docs/rust-migration-plan.md`): the HTTP API
//! over `stl-core`. The server owns the data directory (compatibility rule 7):
//! it holds the library lock, migrates the database and opens the thumbnail
//! store once.
//!
//! Database and store work is blocking; handlers run it on tokio's blocking
//! pool through [`AppState::run`].

pub mod api;
pub mod config;
pub mod error;

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
        Ok(Library { db: Pool::new(&config.db_path()), thumbs, config, _lock: lock })
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

/// The application router.
pub fn app(state: AppState) -> axum::Router {
    api::router().with_state(state)
}
