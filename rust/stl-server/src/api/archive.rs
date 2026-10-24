//! What is inside a model's archive: the stored printable file list (Show
//! Files), the members behind its warnings (Warning Details), and its model
//! files for the viewer (`stl_core::view`).

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde::{Deserialize, Serialize};
use stl_core::storage::{model_edit, views};
use stl_core::view::{self, ViewError};
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

/// A model file inside an archive that the viewer can show.
#[derive(Serialize, ToSchema)]
pub struct ViewableFile {
    /// The member's name in the archive (`/` separators).
    pub name: String,
    /// Uncompressed size, when the archive records it.
    pub size: Option<i64>,
}

/// The archive's path on the server, when the model exists and the file is there.
fn archive_on_disk(lib: &crate::Library, id: i64) -> Result<std::path::PathBuf, ApiError> {
    let path = lib.db.with(|c| model_edit::archive_paths(c, &[id]))?.remove(&id).ok_or_else(|| ApiError::not_found(format!("no model {id}")))?;
    let path = std::path::PathBuf::from(path);
    if !path.is_file() {
        return Err(ApiError::not_found("the archive is not on disk"));
    }
    Ok(path)
}

/// The model files in the archive the viewer can show (STL, OBJ, PLY, 3MF,
/// Lychee and CHITUBOX files), from the archive's catalog read now, by name.
#[utoipa::path(get, path = "/api/models/{id}/viewable", tag = "library", params(("id" = i64, Path)),
    responses((status = 200, body = Vec<ViewableFile>), (status = 404, body = crate::error::ErrorBody),
              (status = 422, body = crate::error::ErrorBody)))]
pub async fn viewable(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>) -> Result<Json<Vec<ViewableFile>>, ApiError> {
    let members = lib
        .run(move |lib| {
            let path = archive_on_disk(lib, id)?;
            view::viewable_members(&path).map_err(|e| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, format!("the archive could not be read: {e}")))
        })
        .await?;
    Ok(Json(members.into_iter().map(|m| ViewableFile { name: m.name, size: m.size.map(|s| s as i64) }).collect()))
}

#[derive(Deserialize, utoipa::IntoParams)]
#[serde(deny_unknown_fields)]
pub struct ViewQuery {
    /// The model file's name in the archive, as `viewable` lists it.
    pub name: String,
}

/// One model file of the archive, loaded for the viewer: the scene (models,
/// supports, build plates) in MeshPeek's wire format (`meshpeek_core::wire`).
#[utoipa::path(get, path = "/api/models/{id}/view", tag = "library", params(("id" = i64, Path), ViewQuery),
    responses((status = 200, description = "The scene", content_type = "application/octet-stream"),
              (status = 400, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody),
              (status = 422, body = crate::error::ErrorBody)))]
pub async fn view(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>, Query(q): Query<ViewQuery>) -> Result<Response, ApiError> {
    let bytes = lib
        .run(move |lib| {
            let path = archive_on_disk(lib, id)?;
            view::load_member(&path, &q.name, &lib.config.unrar_tool()).map_err(|e| match e {
                ViewError::Missing(_) => ApiError::not_found(e.to_string()),
                ViewError::NotViewable(_) => ApiError::bad_request(e.to_string()),
                ViewError::Archive(_) | ViewError::Model(_) => ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, format!("{}: {e}", q.name)),
            })
        })
        .await?;
    let mut resp = bytes.into_response();
    resp.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/octet-stream"));
    Ok(resp)
}
