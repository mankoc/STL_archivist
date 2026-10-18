//! Library backups (administrators): the `backup` job writes them to
//! `<data dir>/backups`; these routes list, download and delete them. Restoring
//! replaces the open database, so it is the offline `stl-server restore` command.

use std::path::{Path, PathBuf};

use axum::Json;
use axum::body::Body;
use axum::extract::{Path as UrlPath, State};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde::Serialize;
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

const PREFIX: &str = "stlLibrary_backup_";

/// Where backups go.
pub fn dir(data_dir: &Path) -> PathBuf {
    data_dir.join("backups")
}

/// `stlLibrary_backup_<YYYYmmdd_HHMMSS>.7z` (UTC), the desktop's naming.
pub fn new_name() -> String {
    let now = stl_core::storage::edit::utc_now_text(); // "YYYY-MM-DD HH:MM:SS.ffffff"
    let digits: String = now.chars().take(19).filter(char::is_ascii_digit).collect();
    format!("{PREFIX}{}_{}.7z", &digits[..8], &digits[8..])
}

/// A name this server could have written: no separators, no way out of the folder.
fn valid(name: &str) -> bool {
    name.starts_with(PREFIX)
        && name.ends_with(".7z")
        && name.len() < 100
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'.')
        && !name.contains("..")
}

fn path_of(lib: &AppState, name: &str) -> Result<PathBuf, ApiError> {
    if !valid(name) {
        return Err(ApiError::bad_request(format!("{name:?} is not a backup name")));
    }
    let path = dir(&lib.config.data_dir).join(name);
    if !path.is_file() {
        return Err(ApiError::not_found(format!("no backup {name}")));
    }
    Ok(path)
}

#[derive(Serialize, ToSchema)]
pub struct BackupFile {
    pub name: String,
    pub size: u64,
    /// Unix seconds.
    pub modified: i64,
}

/// The backups on the server, newest first.
#[utoipa::path(get, path = "/api/backups", tag = "backups",
    responses((status = 200, body = Vec<BackupFile>), (status = 403, body = crate::error::ErrorBody)))]
pub async fn list(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<Vec<BackupFile>>, ApiError> {
    me.require(Role::Admin)?;
    let folder = dir(&lib.config.data_dir);
    let mut out: Vec<BackupFile> = std::fs::read_dir(&folder)
        .map(|rd| {
            rd.flatten()
                .filter_map(|e| {
                    let name = e.file_name().to_string_lossy().into_owned();
                    let md = e.metadata().ok().filter(|m| m.is_file() && valid(&name))?;
                    let modified = md
                        .modified()
                        .ok()
                        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                        .map_or(0, |d| d.as_secs() as i64);
                    Some(BackupFile { name, size: md.len(), modified })
                })
                .collect()
        })
        .unwrap_or_default();
    out.sort_by(|a, b| b.name.cmp(&a.name));
    Ok(Json(out))
}

/// Download a backup.
#[utoipa::path(get, path = "/api/backups/{name}", tag = "backups", params(("name" = String, Path)),
    responses((status = 200, content_type = "application/x-7z-compressed", body = Vec<u8>), (status = 404, body = crate::error::ErrorBody)))]
pub async fn download(State(lib): State<AppState>, me: CurrentUser, UrlPath(name): UrlPath<String>) -> Result<Response, ApiError> {
    me.require(Role::Admin)?;
    let path = path_of(&lib, &name)?;
    let file = tokio::fs::File::open(&path).await.map_err(|_| ApiError::not_found(format!("no backup {name}")))?;
    let len = file.metadata().await.map(|m| m.len()).ok();
    let mut resp = Body::from_stream(tokio_util::io::ReaderStream::new(file)).into_response();
    let h = resp.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("application/x-7z-compressed"));
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{name}\"")) {
        h.insert(header::CONTENT_DISPOSITION, v);
    }
    if let Some(len) = len {
        h.insert(header::CONTENT_LENGTH, HeaderValue::from(len));
    }
    Ok(resp)
}

/// Delete a backup.
#[utoipa::path(delete, path = "/api/backups/{name}", tag = "backups", params(("name" = String, Path)),
    responses((status = 204), (status = 404, body = crate::error::ErrorBody)))]
pub async fn delete(State(lib): State<AppState>, me: CurrentUser, UrlPath(name): UrlPath<String>) -> Result<StatusCode, ApiError> {
    me.require(Role::Admin)?;
    let path = path_of(&lib, &name)?;
    std::fs::remove_file(path).map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(StatusCode::NO_CONTENT)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names() {
        let n = new_name();
        assert!(valid(&n), "{n}");
        assert_eq!(n.len(), PREFIX.len() + 15 + 3);
        assert!(!valid("stlLibrary_backup_../../x.7z"));
        assert!(!valid("server.db"));
        assert!(!valid("stlLibrary_backup_1\\x.7z"));
    }
}
