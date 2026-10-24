//! What is inside a model's archive: the stored printable file list (Show
//! Files) and the members behind its warnings (Warning Details).

use axum::Json;
use axum::extract::{Path, State};
use serde::Serialize;
use stl_core::storage::views;
use utoipa::ToSchema;

use crate::archive_tools::WarningDetails;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

#[derive(Serialize, ToSchema)]
pub struct StlFile {
    pub filename: String,
    pub size: Option<i64>,
    /// The file's content hash, when the crawl stored one.
    pub hash: Option<String>,
}

/// The printable files the crawl stored for a model (empty unless the library
/// stores STL file lists), by name.
#[utoipa::path(get, path = "/api/models/{id}/files", tag = "library", params(("id" = i64, Path)),
    responses((status = 200, body = Vec<StlFile>)))]
pub async fn files(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>) -> Result<Json<Vec<StlFile>>, ApiError> {
    let rows = lib.run(move |lib| Ok::<_, ApiError>(lib.db.with(|c| views::stl_files(c, id))?)).await?;
    Ok(Json(rows.into_iter().map(|(filename, size, hash)| StlFile { filename, size, hash }).collect()))
}

/// One file inside a model's archive.
#[derive(Serialize, ToSchema)]
pub struct ArchiveFile {
    /// The member's name in the archive (`/` separators).
    pub name: String,
    /// Uncompressed size, when the archive records it.
    pub size: Option<i64>,
    /// The member's CRC-32 from the archive's catalog, as 8 hex digits.
    pub crc: String,
}

/// Every file inside the model's archive (name, size, CRC-32), as the last
/// crawl or rescan read the archive's catalog; empty until then.
#[utoipa::path(get, path = "/api/models/{id}/archive-files", tag = "library", params(("id" = i64, Path)),
    responses((status = 200, body = Vec<ArchiveFile>)))]
pub async fn archive_files(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>) -> Result<Json<Vec<ArchiveFile>>, ApiError> {
    let rows = lib.run(move |lib| Ok::<_, ApiError>(lib.db.with(|c| views::archive_files(c, id))?)).await?;
    Ok(Json(rows.into_iter().map(|(name, size, crc)| ArchiveFile { name, size, crc: format!("{:08x}", crc as u32) }).collect()))
}

/// Which archive members trigger each of the model's stored warnings (the
/// archive's catalog is read now).
#[utoipa::path(get, path = "/api/models/{id}/warnings", tag = "library", params(("id" = i64, Path)),
    responses((status = 200, body = WarningDetails), (status = 404, body = crate::error::ErrorBody)))]
pub async fn warnings(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>) -> Result<Json<WarningDetails>, ApiError> {
    let details = lib
        .run(move |lib| {
            let (path, warning) = lib.db.with(|c| views::path_warning(c, id))?.ok_or_else(|| ApiError::not_found(format!("no model {id}")))?;
            Ok::<_, ApiError>(crate::archive_tools::warning_details(lib, path.as_deref(), warning.as_deref()))
        })
        .await?;
    Ok(Json(details))
}
