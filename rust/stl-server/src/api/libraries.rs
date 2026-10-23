//! Libraries (see `stl_core::storage::libraries`): the relocatable collections
//! crawls catalogue, each with one or more folders. Listing them, adding a
//! library or a folder to one (a folder, crawled or not yet), renaming a
//! library, setting a folder's pattern or monitoring, moving a folder to
//! another library, and removing a folder or a whole library. Moving a folder to a new root is the `relocate_library`
//! job (it checks every archive first).

use std::path::Path as FsPath;

use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use serde::{Deserialize, Serialize};
use stl_core::storage::libraries::{self, Folder, Group, Removed, Target};
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
    /// Records in its folders.
    pub models: i64,
    /// By root.
    pub folders: Vec<LibraryFolder>,
}

/// One folder of a library.
#[derive(Debug, Serialize, ToSchema)]
pub struct LibraryFolder {
    pub id: i64,
    /// The same on every instance (export, sync).
    pub uuid: String,
    /// Unique among folders (`stl-server libraries relocate` takes it).
    pub name: String,
    /// Where the folder is on the server.
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

impl From<Folder> for LibraryFolder {
    fn from(l: Folder) -> Self {
        let reachable = !l.root.is_empty() && FsPath::new(&l.root).is_dir();
        LibraryFolder {
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

impl From<Group> for LibraryInfo {
    fn from(g: Group) -> Self {
        let models = g.models();
        LibraryInfo { id: g.id, uuid: g.uuid, name: g.name, models, folders: g.folders.into_iter().map(LibraryFolder::from).collect() }
    }
}

fn library(lib: &crate::Library, id: i64) -> Result<LibraryInfo, ApiError> {
    Ok(lib.db.with(|c| libraries::group(c, id))?.ok_or_else(|| ApiError::not_found(format!("no library {id}")))?.into())
}

fn folder_exists(lib: &crate::Library, id: i64) -> Result<Folder, ApiError> {
    lib.db.with(|c| libraries::get(c, id))?.ok_or_else(|| ApiError::not_found(format!("no library folder {id}")))
}

/// Every library with its folders, by name.
#[utoipa::path(get, path = "/api/libraries", tag = "libraries", responses((status = 200, body = Vec<LibraryInfo>)))]
pub async fn list(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<Vec<LibraryInfo>>, ApiError> {
    let rows = lib.run(|lib| Ok(lib.db.with(libraries::groups)?)).await?;
    Ok(Json(rows.into_iter().map(LibraryInfo::from).collect()))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct NewLibrary {
    /// The folder on the server.
    pub path: String,
    /// The library to add the folder to; without it, a new library.
    pub library: Option<i64>,
    /// A new library's name (default: the folder's name).
    pub name: Option<String>,
    /// The folder's path pattern.
    pub pattern: Option<String>,
}

/// Add a folder (editors, inside their roots) to a library, or as a new
/// library: 201, or 200 with the folder already there (in that library).
/// Records of folders around it below the folder move into it.
#[utoipa::path(post, path = "/api/libraries", tag = "libraries", request_body = NewLibrary,
    responses((status = 201, body = LibraryInfo), (status = 200, body = LibraryInfo), (status = 400, body = crate::error::ErrorBody),
        (status = 403, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody)))]
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
            if let Some(g) = req.library {
                library(lib, g)?;
            }
            let path = crate::api::files::known_spelling(lib, FsPath::new(&path))?.unwrap_or(path);
            if let Some(f) = lib.db.with(|c| libraries::by_root(c, &path))? {
                return match req.library {
                    Some(g) if f.group_id != Some(g) => Err(ApiError::bad_request(format!("{path} is already a folder of another library"))),
                    _ => Ok((false, library(lib, f.group_id.unwrap_or_default())?)),
                };
            }
            let default = libraries::default_name(&path);
            let name = req.name.as_deref().map(str::trim).filter(|n| !n.is_empty()).unwrap_or(&default);
            let into = match req.library {
                Some(g) => Target::Group(g),
                None => Target::New(name),
            };
            let id = lib.db.with_tx(|tx| libraries::create(tx, &path, into, req.pattern.as_deref().filter(|p| !p.trim().is_empty()), None))?;
            let group = folder_exists(lib, id)?.group_id.ok_or_else(|| ApiError::internal("the folder has no library"))?;
            Ok((true, library(lib, group)?))
        })
        .await?;
    Ok((if created { StatusCode::CREATED } else { StatusCode::OK }, Json(info)))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct LibraryChange {
    pub name: String,
}

/// Rename a library (editors). A name another library has gets a number.
#[utoipa::path(patch, path = "/api/libraries/{id}", tag = "libraries", request_body = LibraryChange, params(("id" = i64, Path)),
    responses((status = 200, body = LibraryInfo), (status = 400, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody)))]
pub async fn change(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Json(req): Json<LibraryChange>) -> Result<Json<LibraryInfo>, ApiError> {
    me.require(Role::Editor)?;
    let name = req.name.trim().to_string();
    if name.is_empty() {
        return Err(ApiError::bad_request("a name is needed"));
    }
    let info = lib
        .run(move |lib| {
            library(lib, id)?;
            lib.db.with_tx(|tx| libraries::rename_group(tx, id, &name))?;
            library(lib, id)
        })
        .await?;
    Ok(Json(info))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct FolderChange {
    /// A path pattern; `""` clears it.
    pub pattern: Option<String>,
    pub monitored: Option<bool>,
    /// Move the folder (with its records) to this library.
    pub library: Option<i64>,
    /// Move the folder (with its records) to a new library with this name.
    pub new_library: Option<String>,
}

/// Set a library folder's pattern, monitor it, or move it with its records
/// to another library or a new one (editors). A library left without folders
/// goes.
#[utoipa::path(patch, path = "/api/libraries/folders/{id}", tag = "libraries", request_body = FolderChange, params(("id" = i64, Path)),
    responses((status = 200, body = LibraryFolder), (status = 400, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody)))]
pub async fn change_folder(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Json(req): Json<FolderChange>) -> Result<Json<LibraryFolder>, ApiError> {
    me.require(Role::Editor)?;
    let info = lib
        .run(move |lib| {
            folder_exists(lib, id)?;
            let new_library = req.new_library.as_deref().map(str::trim);
            if req.library.is_some() && new_library.is_some() {
                return Err(ApiError::bad_request("give library or new_library, not both"));
            }
            if new_library == Some("") {
                return Err(ApiError::bad_request("a new library needs a name"));
            }
            if let Some(g) = req.library {
                library(lib, g)?;
            }
            lib.db.with_tx(|tx| {
                let to = match new_library {
                    Some(name) => Some(libraries::new_group(tx, name)?),
                    None => req.library,
                };
                if let Some(to) = to {
                    libraries::move_folder(tx, id, to)?;
                }
                if let Some(p) = req.pattern.as_deref() {
                    libraries::set_pattern(tx, id, Some(p))?;
                }
                if let Some(m) = req.monitored {
                    libraries::set_monitored(tx, id, m)?;
                }
                Ok::<_, rusqlite::Error>(())
            })?;
            Ok(LibraryFolder::from(folder_exists(lib, id)?))
        })
        .await?;
    Ok(Json(info))
}

#[derive(Serialize, ToSchema)]
pub struct LibraryRemoved {
    /// The folder (of another library) around it that took its records.
    pub moved_to: Option<i64>,
    pub moved: usize,
    /// Records deleted (no folder around it). The archive files stay.
    pub deleted: usize,
}

fn removed(lib: &crate::Library, r: Removed) -> Result<LibraryRemoved, ApiError> {
    let ids: Vec<u64> = r.refs.iter().filter_map(|s| s.trim().parse().ok()).collect();
    lib.thumbs.delete_many(&ids)?;
    Ok(LibraryRemoved { moved_to: r.moved_to, moved: r.moved, deleted: r.deleted })
}

/// Remove a library with all its folders (editors): their records go to a
/// folder of another library around them, or are deleted with their images.
/// Files are not touched.
#[utoipa::path(post, path = "/api/libraries/{id}/remove", tag = "libraries", params(("id" = i64, Path)),
    responses((status = 200, body = LibraryRemoved), (status = 404, body = crate::error::ErrorBody)))]
pub async fn remove(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>) -> Result<Json<LibraryRemoved>, ApiError> {
    me.require(Role::Editor)?;
    let out = lib
        .run(move |lib| {
            library(lib, id)?;
            let r = lib.db.with_tx(|tx| libraries::remove_group(tx, id))?;
            removed(lib, r)
        })
        .await?;
    Ok(Json(out))
}

/// Remove one folder of a library (editors): its records go to the folder
/// around it, or are deleted with their images when there is none. A library
/// left without folders goes too. Files are not touched.
#[utoipa::path(post, path = "/api/libraries/folders/{id}/remove", tag = "libraries", params(("id" = i64, Path)),
    responses((status = 200, body = LibraryRemoved), (status = 404, body = crate::error::ErrorBody)))]
pub async fn remove_folder(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>) -> Result<Json<LibraryRemoved>, ApiError> {
    me.require(Role::Editor)?;
    let out = lib
        .run(move |lib| {
            folder_exists(lib, id)?;
            let r = lib.db.with_tx(|tx| libraries::remove(tx, id))?;
            removed(lib, r)
        })
        .await?;
    Ok(Json(out))
}
