//! Libraries (see `stl_core::storage::libraries`): the relocatable collections
//! crawls catalogue. Listing them, adding one (a folder, crawled or not yet),
//! renaming, setting its pattern or monitoring, and removing it. Moving one to a
//! new root is the `relocate_library` job (it checks every archive first).

use std::path::Path as FsPath;

use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use serde::{Deserialize, Serialize};
use stl_core::storage::libraries::{self, Library};
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

#[derive(Debug, Serialize, ToSchema)]
pub struct LibraryInfo {
    pub id: i64,
    /// The same on every instance (export, sync).
    pub uuid: String,
    pub name: String,
    /// Where the library is on the server.
    pub root: String,
    /// Path pattern its crawls use.
    pub pattern: Option<String>,
    pub monitored: bool,
    pub last_crawled_at: Option<String>,
    /// Archives found by its last crawl.
    pub archive_count: Option<i64>,
    /// Records in it.
    pub models: i64,
    /// `migration` when the move to libraries made it for archives outside
    /// every crawled folder.
    pub origin: Option<String>,
    /// Its root is a folder on the server (else it must be relocated).
    pub reachable: bool,
}

impl From<Library> for LibraryInfo {
    fn from(l: Library) -> Self {
        let reachable = !l.root.is_empty() && FsPath::new(&l.root).is_dir();
        LibraryInfo {
            id: l.id,
            uuid: l.uuid,
            name: l.name,
            root: l.root,
            pattern: l.pattern,
            monitored: l.monitored,
            last_crawled_at: l.last_crawled_at,
            archive_count: l.archive_count,
            models: l.models,
            origin: l.origin,
            reachable,
        }
    }
}

/// Every library, by name.
#[utoipa::path(get, path = "/api/libraries", tag = "libraries", responses((status = 200, body = Vec<LibraryInfo>)))]
pub async fn list(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<Vec<LibraryInfo>>, ApiError> {
    let rows = lib.run(|lib| Ok(lib.db.with(libraries::list)?)).await?;
    Ok(Json(rows.into_iter().map(LibraryInfo::from).collect()))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct NewLibrary {
    /// The library's root folder on the server.
    pub path: String,
    pub name: Option<String>,
    pub pattern: Option<String>,
}

/// Add a library at a folder (editors, inside their roots): 201, or 200 with
/// the library already there. Records of libraries around it below the folder
/// move into it.
#[utoipa::path(post, path = "/api/libraries", tag = "libraries", request_body = NewLibrary,
    responses((status = 201, body = LibraryInfo), (status = 200, body = LibraryInfo), (status = 403, body = crate::error::ErrorBody)))]
pub async fn add(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<NewLibrary>) -> Result<(StatusCode, Json<LibraryInfo>), ApiError> {
    me.require(Role::Editor)?;
    let path = req.path.trim().to_string();
    if path.is_empty() {
        return Err(ApiError::bad_request("a folder is needed"));
    }
    let (created, info) = lib
        .run(move |lib| {
            crate::api::files::check_access(lib, &me, FsPath::new(&path))?;
            if !FsPath::new(&path).is_dir() {
                return Err(ApiError::bad_request(format!("{path} is not a folder")));
            }
            let path = crate::api::files::known_spelling(lib, FsPath::new(&path))?.unwrap_or(path);
            let existed = lib.db.with(|c| libraries::by_root(c, &path))?.is_some();
            let id = lib.db.with_tx(|tx| {
                let id = libraries::create(tx, &path, req.name.as_deref().filter(|n| !n.trim().is_empty()), None, None)?;
                if let Some(p) = req.pattern.as_deref() {
                    libraries::set_pattern(tx, id, Some(p))?;
                }
                Ok::<_, rusqlite::Error>(id)
            })?;
            let l = lib.db.with(|c| libraries::get(c, id))?.ok_or_else(|| ApiError::internal("the library vanished"))?;
            Ok((!existed, LibraryInfo::from(l)))
        })
        .await?;
    Ok((if created { StatusCode::CREATED } else { StatusCode::OK }, Json(info)))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct LibraryChange {
    pub name: Option<String>,
    /// A path pattern; `""` clears it.
    pub pattern: Option<String>,
    pub monitored: Option<bool>,
}

/// Rename a library, set its pattern, or monitor it (editors). A name another
/// library has gets a number.
#[utoipa::path(patch, path = "/api/libraries/{id}", tag = "libraries", request_body = LibraryChange, params(("id" = i64, Path)),
    responses((status = 200, body = LibraryInfo), (status = 404, body = crate::error::ErrorBody)))]
pub async fn change(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Json(req): Json<LibraryChange>) -> Result<Json<LibraryInfo>, ApiError> {
    me.require(Role::Editor)?;
    let info = lib
        .run(move |lib| {
            if lib.db.with(|c| libraries::get(c, id))?.is_none() {
                return Err(ApiError::not_found(format!("no library {id}")));
            }
            lib.db.with_tx(|tx| {
                if let Some(n) = req.name.as_deref().filter(|n| !n.trim().is_empty()) {
                    libraries::rename(tx, id, n)?;
                }
                if let Some(p) = req.pattern.as_deref() {
                    libraries::set_pattern(tx, id, Some(p))?;
                }
                if let Some(m) = req.monitored {
                    libraries::set_monitored(tx, id, m)?;
                }
                Ok::<_, rusqlite::Error>(())
            })?;
            Ok(LibraryInfo::from(lib.db.with(|c| libraries::get(c, id))?.ok_or_else(|| ApiError::not_found("gone"))?))
        })
        .await?;
    Ok(Json(info))
}

#[derive(Serialize, ToSchema)]
pub struct LibraryRemoved {
    /// The library around it that took its records.
    pub moved_to: Option<i64>,
    pub moved: usize,
    /// Records deleted (no library around it). The archive files stay.
    pub deleted: usize,
}

/// Remove a library (editors): its records go to the library around it, or
/// are deleted with their images when there is none. Files are not touched.
#[utoipa::path(post, path = "/api/libraries/{id}/remove", tag = "libraries", params(("id" = i64, Path)),
    responses((status = 200, body = LibraryRemoved), (status = 404, body = crate::error::ErrorBody)))]
pub async fn remove(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>) -> Result<Json<LibraryRemoved>, ApiError> {
    me.require(Role::Editor)?;
    let out = lib
        .run(move |lib| {
            if lib.db.with(|c| libraries::get(c, id))?.is_none() {
                return Err(ApiError::not_found(format!("no library {id}")));
            }
            let r = lib.db.with_tx(|tx| libraries::remove(tx, id))?;
            let ids: Vec<u64> = r.refs.iter().filter_map(|s| s.trim().parse().ok()).collect();
            lib.thumbs.delete_many(&ids)?;
            Ok(LibraryRemoved { moved_to: r.moved_to, moved: r.moved, deleted: r.deleted })
        })
        .await?;
    Ok(Json(out))
}
