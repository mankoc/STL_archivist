//! The library's settings (`settings.json`, shared with the desktop app): what
//! the crawler and sidecars use. Anyone signed in reads them, administrators
//! change them. Keys this server does not know are kept as they are.

use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::library_settings::{FILE, LibrarySettings};
use crate::{ApiError, AppState};

/// The settings, with their effective values (defaults filled in).
#[derive(Debug, Clone, Serialize, Deserialize, ToSchema, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    /// Thumbnails taken per archive.
    pub max_thumbnails: usize,
    /// Longest thumbnail edge in pixels.
    pub max_thumb_size: u32,
    /// Parallel crawl workers.
    pub max_workers: usize,
    pub recurse_subdirs: bool,
    pub store_stl_file_list: bool,
    /// Write `METADATA.7z` sidecars into archive folders.
    pub metadata_sidecar_enabled: bool,
    /// Include thumbnails in sidecars.
    pub metadata_thumbnails: bool,
    pub warn_presupported_stl: bool,
    pub warn_no_images: bool,
    pub warn_nested_archive: bool,
    pub warn_no_printable: bool,
    pub warn_low_compression: bool,
    /// Compression rate above which an archive counts as poorly compressed.
    pub low_compression_threshold: f64,
    /// Regular expressions for presupported STL file names (the portable subset).
    pub presupport_patterns: Vec<String>,
    pub ai_embeddings_enabled: bool,
    pub ai_embeddings_during_crawl: bool,
    /// *Delete files* may remove archives from disk.
    pub allow_file_delete: bool,
    /// Deleted archives go to `trash_folder_path` (when set) instead of being deleted.
    pub use_trash_folder: bool,
    pub trash_folder_path: String,
    /// *Sort files* may move archives.
    pub file_sorting_enabled: bool,
    /// The AI tagger's sampling temperature (0 to 2; lower is more literal).
    pub ai_temperature: f64,
}

impl Settings {
    fn current(s: &LibrarySettings) -> Settings {
        let checks = s.checks();
        Settings {
            max_thumbnails: s.max_thumbnails(),
            max_thumb_size: s.max_thumb_size(),
            max_workers: s.max_workers(),
            recurse_subdirs: s.recurse_subdirs(),
            store_stl_file_list: s.store_stl_file_list(),
            metadata_sidecar_enabled: s.sidecars_enabled(),
            metadata_thumbnails: s.sidecar_thumbnails(),
            warn_presupported_stl: checks.presupported,
            warn_no_images: checks.no_images,
            warn_nested_archive: checks.nested_archive,
            warn_no_printable: checks.no_printable,
            warn_low_compression: s.warn_low_compression(),
            low_compression_threshold: s.low_compression_threshold(),
            presupport_patterns: s.presupport_patterns(),
            ai_embeddings_enabled: s.ai_embeddings_enabled(),
            ai_embeddings_during_crawl: s.ai_embeddings_during_crawl(),
            allow_file_delete: s.allow_file_delete(),
            use_trash_folder: s.use_trash_folder(),
            trash_folder_path: s.trash_folder_path(),
            file_sorting_enabled: s.file_sorting_enabled(),
            ai_temperature: s.ai_temperature(),
        }
    }

    fn validate(&self) -> Result<(), ApiError> {
        if !(1..=10_000).contains(&self.max_thumb_size) {
            return Err(ApiError::bad_request("max_thumb_size is 1 to 10000 pixels"));
        }
        if self.max_thumbnails > 1000 {
            return Err(ApiError::bad_request("max_thumbnails is at most 1000"));
        }
        if !(1..=64).contains(&self.max_workers) {
            return Err(ApiError::bad_request("max_workers is 1 to 64"));
        }
        if !(0.0..=1.0).contains(&self.low_compression_threshold) {
            return Err(ApiError::bad_request("low_compression_threshold is between 0 and 1"));
        }
        if !(0.0..=2.0).contains(&self.ai_temperature) {
            return Err(ApiError::bad_request("ai_temperature is between 0 and 2"));
        }
        for p in &self.presupport_patterns {
            stl_core::rules::portable_pattern(p).map_err(|e| ApiError::bad_request(format!("pattern {p:?}: {e}")))?;
        }
        Ok(())
    }
}

/// The library settings.
#[utoipa::path(get, path = "/api/settings/library", tag = "settings", responses((status = 200, body = Settings)))]
pub async fn get(State(lib): State<AppState>, _me: CurrentUser) -> Json<Settings> {
    Json(Settings::current(&LibrarySettings::load(&lib.config.data_dir)))
}

/// Change the library settings (administrators). Takes every field; the
/// desktop app reads the same file.
#[utoipa::path(put, path = "/api/settings/library", tag = "settings", request_body = Settings,
    responses((status = 200, body = Settings), (status = 400, body = crate::error::ErrorBody),
              (status = 403, body = crate::error::ErrorBody)))]
pub async fn put(State(lib): State<AppState>, me: CurrentUser, Json(new): Json<Settings>) -> Result<Json<Settings>, ApiError> {
    me.require(Role::Admin)?;
    new.validate()?;
    let saved = lib
        .run(move |lib| {
            let path = lib.config.data_dir.join(FILE);
            let mut doc: serde_json::Map<String, serde_json::Value> = std::fs::read_to_string(&path)
                .ok()
                .and_then(|t| serde_json::from_str(&t).ok())
                .unwrap_or_default();
            let serde_json::Value::Object(fields) = serde_json::to_value(&new).expect("settings serialise") else {
                unreachable!("a struct serialises to an object")
            };
            doc.extend(fields);
            // Write beside, then replace: a crash never leaves half a file.
            let tmp = path.with_extension("json.tmp");
            let text = serde_json::to_string_pretty(&doc).expect("json serialises");
            std::fs::write(&tmp, text).and_then(|_| std::fs::rename(&tmp, &path)).map_err(|e| ApiError::internal(e.to_string()))?;
            Ok(Settings::current(&LibrarySettings::load(&lib.config.data_dir)))
        })
        .await?;
    tracing::info!(by = %me.user.name, "library settings changed");
    Ok(Json(saved))
}
