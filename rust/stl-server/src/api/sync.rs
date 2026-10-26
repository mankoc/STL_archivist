//! Sync with other servers (administrators): the remotes this server pulls
//! from and pushes to (signing in there, path rules, a preview), and what a
//! server answers when another one syncs with it (its thumbnail keys, which
//! folders exist). The syncs themselves are the `sync` and `sync_export` jobs.

use axum::Json;
use axum::body::{Body, Bytes};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde::{Deserialize, Serialize};
use serde_json::json;
use utoipa::{IntoParams, ToSchema};

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::sync::{self, Direction, PathRule, Remote, RemoteInfo, Sent, SyncPreview};
use crate::{ApiError, AppState};

/// Most folders `POST /api/sync/exists` looks up at once.
const MAX_PATHS: usize = 10_000;

fn bad_gateway(message: String) -> ApiError {
    ApiError::new(StatusCode::BAD_GATEWAY, message)
}

fn instance_header(lib: &AppState, resp: &mut Response) {
    if let Ok(v) = HeaderValue::from_str(&lib.instance) {
        resp.headers_mut().insert(sync::INSTANCE_HEADER, v);
    }
}

fn stored(lib: &crate::Library, id: i64) -> Result<sync::Stored, ApiError> {
    lib.accounts.with(|c| sync::get(c, id))?.ok_or_else(|| ApiError::not_found(format!("no remote server {id}")))
}

fn check_name(name: &str) -> Result<String, ApiError> {
    let n = name.trim();
    if n.is_empty() || n.chars().count() > 100 {
        return Err(ApiError::bad_request("a remote's name has 1 to 100 characters"));
    }
    Ok(n.to_string())
}

fn check_rules(rules: Vec<PathRule>) -> Result<Vec<PathRule>, ApiError> {
    rules
        .into_iter()
        .map(|r| {
            let (local, remote) = (r.local.trim().to_string(), r.remote.trim().to_string());
            if local.is_empty() || remote.is_empty() {
                Err(ApiError::bad_request("each folder rule needs a folder here and one on the other server"))
            } else {
                Ok(PathRule { local, remote })
            }
        })
        .collect()
}

/// A unique-name clash as 409.
fn saved<T>(r: rusqlite::Result<T>) -> Result<T, ApiError> {
    r.map_err(|e| match e {
        rusqlite::Error::SqliteFailure(f, _) if f.code == rusqlite::ErrorCode::ConstraintViolation => {
            ApiError::new(StatusCode::CONFLICT, "another remote has that name")
        }
        e => e.into(),
    })
}

/// This server's thumbnail keys (each a length byte and the key): a pushing
/// server leaves those images out.
#[utoipa::path(get, path = "/api/sync/thumbnails", tag = "sync",
    responses((status = 200, content_type = "application/octet-stream", body = Vec<u8>), (status = 403, body = crate::error::ErrorBody)))]
pub async fn thumbnails(State(lib): State<AppState>, me: CurrentUser) -> Result<Response, ApiError> {
    me.require(Role::Admin)?;
    let keys = lib.run(|lib| lib.thumbs.blob_keys().map_err(|e| ApiError::internal(format!("thumbnail store: {e}")))).await?;
    let mut resp = Body::from(sync::encode_keys(&keys)).into_response();
    resp.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/octet-stream"));
    instance_header(&lib, &mut resp);
    Ok(resp)
}

#[derive(Serialize, ToSchema)]
pub struct HaveId {
    /// For the `sync_export` job's `have`.
    pub id: String,
}

/// Hand over a pulling server's thumbnail keys (the body, as `GET` gives them)
/// for a `sync_export` job; kept for a day.
#[utoipa::path(post, path = "/api/sync/thumbnails", tag = "sync",
    request_body(content_type = "application/octet-stream", description = "Thumbnail keys"),
    responses((status = 200, body = HaveId), (status = 400, body = crate::error::ErrorBody), (status = 403, body = crate::error::ErrorBody)))]
pub async fn have(State(lib): State<AppState>, me: CurrentUser, body: Bytes) -> Result<Response, ApiError> {
    me.require(Role::Admin)?;
    let id = lib.run(move |lib| sync::store_have(&lib.config.data_dir, &body).map_err(ApiError::bad_request)).await?;
    let mut resp = Json(HaveId { id }).into_response();
    instance_header(&lib, &mut resp);
    Ok(resp)
}

/// This server's database fingerprints (`storage::delta`): a pushing server
/// sends only the rows that differ. 409 when the database cannot be fingerprinted.
#[utoipa::path(get, path = "/api/sync/blocks", tag = "sync",
    responses((status = 200, content_type = "application/octet-stream", body = Vec<u8>), (status = 403, body = crate::error::ErrorBody),
              (status = 409, body = crate::error::ErrorBody)))]
pub async fn blocks(State(lib): State<AppState>, me: CurrentUser) -> Result<Response, ApiError> {
    me.require(Role::Admin)?;
    let state = lib
        .run(|lib| {
            let fingerprints = lib.db.with(|c| {
                let tx = c.unchecked_transaction()?;
                Ok::<_, rusqlite::Error>(stl_core::storage::delta::state(&tx))
            })?;
            fingerprints.map_err(|e| ApiError::new(StatusCode::CONFLICT, e.to_string()))
        })
        .await?;
    let mut resp = Body::from(stl_core::storage::delta::encode(&state)).into_response();
    resp.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/octet-stream"));
    instance_header(&lib, &mut resp);
    Ok(resp)
}

/// Hand over a pulling server's database fingerprints (the body, as `GET`
/// gives them) for a `sync_export` job; kept for a day.
#[utoipa::path(post, path = "/api/sync/blocks", tag = "sync",
    request_body(content_type = "application/octet-stream", description = "Database fingerprints"),
    responses((status = 200, body = HaveId), (status = 400, body = crate::error::ErrorBody), (status = 403, body = crate::error::ErrorBody)))]
pub async fn post_blocks(State(lib): State<AppState>, me: CurrentUser, body: Bytes) -> Result<Response, ApiError> {
    me.require(Role::Admin)?;
    let id = lib.run(move |lib| sync::store_blocks(&lib.config.data_dir, &body).map_err(ApiError::bad_request)).await?;
    let mut resp = Json(HaveId { id }).into_response();
    instance_header(&lib, &mut resp);
    Ok(resp)
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct Paths {
    pub paths: Vec<String>,
}

/// Which of `paths` are folders on this server (a pushing server's preview).
#[utoipa::path(post, path = "/api/sync/exists", tag = "sync", request_body = Paths,
    responses((status = 200, body = Vec<bool>), (status = 403, body = crate::error::ErrorBody)))]
pub async fn exists(State(lib): State<AppState>, me: CurrentUser, Json(q): Json<Paths>) -> Result<Json<Vec<bool>>, ApiError> {
    me.require(Role::Admin)?;
    if q.paths.len() > MAX_PATHS {
        return Err(ApiError::bad_request(format!("at most {MAX_PATHS} folders at once")));
    }
    Ok(Json(lib.run(move |_| Ok(q.paths.iter().map(|p| std::path::Path::new(p).is_dir()).collect())).await?))
}

/// The remote servers this server syncs with.
#[utoipa::path(get, path = "/api/sync/remotes", tag = "sync",
    responses((status = 200, body = Vec<RemoteInfo>), (status = 403, body = crate::error::ErrorBody)))]
pub async fn list(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<Vec<RemoteInfo>>, ApiError> {
    me.require(Role::Admin)?;
    let all = lib.run(|lib| Ok(lib.accounts.with(sync::list)?)).await?;
    Ok(Json(all.into_iter().map(RemoteInfo::from).collect()))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct NewRemote {
    pub name: String,
    /// `http(s)://host:port` of the other server.
    pub url: String,
    /// An administrator there; this server signs in once and keeps only the session.
    pub user: String,
    pub password: String,
    #[serde(default)]
    pub rules: Vec<PathRule>,
}

/// Add a remote server: this server signs in there as an administrator (the
/// password is not kept).
#[utoipa::path(post, path = "/api/sync/remotes", tag = "sync", request_body = NewRemote,
    responses((status = 201, body = RemoteInfo), (status = 400, body = crate::error::ErrorBody), (status = 409, body = crate::error::ErrorBody),
              (status = 502, body = crate::error::ErrorBody)))]
pub async fn add(State(lib): State<AppState>, me: CurrentUser, Json(new): Json<NewRemote>) -> Result<(StatusCode, Json<RemoteInfo>), ApiError> {
    me.require(Role::Admin)?;
    let name = check_name(&new.name)?;
    let url = sync::clean_url(&new.url).map_err(ApiError::bad_request)?;
    let rules = check_rules(new.rules)?;
    let user = new.user.trim().to_string();
    let info = lib
        .run(move |lib| {
            let signed = Remote::new(&name, &url, None).sign_in(&user, &new.password).map_err(bad_gateway)?;
            let id = saved(lib.accounts.with(|c| sync::insert(c, &name, &url, &user, &signed.token, &rules)))?;
            Ok(RemoteInfo::from(stored(lib, id)?))
        })
        .await?;
    tracing::info!(by = %me.user.name, remote = %info.name, url = %info.url, "sync remote added");
    Ok((StatusCode::CREATED, Json(info)))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct RemoteChange {
    pub name: Option<String>,
    pub rules: Option<Vec<PathRule>>,
    pub backup_first: Option<bool>,
}

/// Rename a remote, set its folder rules, or whether the receiving side backs up first.
#[utoipa::path(patch, path = "/api/sync/remotes/{id}", tag = "sync", request_body = RemoteChange, params(("id" = i64, Path)),
    responses((status = 200, body = RemoteInfo), (status = 404, body = crate::error::ErrorBody), (status = 409, body = crate::error::ErrorBody)))]
pub async fn change(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Json(ch): Json<RemoteChange>) -> Result<Json<RemoteInfo>, ApiError> {
    me.require(Role::Admin)?;
    let name = ch.name.as_deref().map(check_name).transpose()?;
    let rules = ch.rules.map(check_rules).transpose()?;
    let info = lib
        .run(move |lib| {
            stored(lib, id)?;
            saved(lib.accounts.with(|c| sync::update(c, id, name.as_deref(), rules.as_deref(), ch.backup_first)))?;
            Ok(RemoteInfo::from(stored(lib, id)?))
        })
        .await?;
    Ok(Json(info))
}

/// Forget a remote (its session there stays until it expires).
#[utoipa::path(delete, path = "/api/sync/remotes/{id}", tag = "sync", params(("id" = i64, Path)),
    responses((status = 204), (status = 404, body = crate::error::ErrorBody)))]
pub async fn delete(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>) -> Result<StatusCode, ApiError> {
    me.require(Role::Admin)?;
    let gone = lib.run(move |lib| Ok(lib.accounts.with(|c| sync::delete(c, id))?)).await?;
    if gone { Ok(StatusCode::NO_CONTENT) } else { Err(ApiError::not_found(format!("no remote server {id}"))) }
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct SignIn {
    pub user: String,
    pub password: String,
}

/// Sign in to a remote again (its session expired or was revoked).
#[utoipa::path(post, path = "/api/sync/remotes/{id}/login", tag = "sync", request_body = SignIn, params(("id" = i64, Path)),
    responses((status = 200, body = RemoteInfo), (status = 404, body = crate::error::ErrorBody), (status = 502, body = crate::error::ErrorBody)))]
pub async fn login(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Json(s): Json<SignIn>) -> Result<Json<RemoteInfo>, ApiError> {
    me.require(Role::Admin)?;
    let info = lib
        .run(move |lib| {
            let r = stored(lib, id)?;
            let user = s.user.trim().to_string();
            let signed = Remote::new(&r.name, &r.url, None).sign_in(&user, &s.password).map_err(bad_gateway)?;
            lib.accounts.with(|c| sync::set_token(c, id, &user, &signed.token))?;
            Ok(RemoteInfo::from(stored(lib, id)?))
        })
        .await?;
    Ok(Json(info))
}

#[derive(Deserialize, IntoParams)]
pub struct PreviewQuery {
    /// `pull` or `push`.
    pub direction: Direction,
}

/// What a sync with a remote would do: the versions, and where each of the
/// sending side's folders would go on the receiving side. Nothing is sent.
#[utoipa::path(get, path = "/api/sync/remotes/{id}/preview", tag = "sync", params(("id" = i64, Path), PreviewQuery),
    responses((status = 200, body = SyncPreview), (status = 404, body = crate::error::ErrorBody), (status = 502, body = crate::error::ErrorBody)))]
pub async fn preview(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Query(q): Query<PreviewQuery>) -> Result<Json<SyncPreview>, ApiError> {
    me.require(Role::Admin)?;
    let preview = lib
        .run(move |lib| {
            let r = stored(lib, id)?;
            let client = Remote::new(&r.name, &r.url, Some(&r.token));
            let (version,) = client.status().map_err(bad_gateway)?;
            let rules: Vec<_> = r.rules.iter().map(|p| p.toward(q.direction)).collect();
            let folders = match q.direction {
                Direction::Pull => {
                    let libraries = client.get_json("/api/libraries").map_err(bad_gateway)?;
                    sync::preview_folders(sync::remote_folders(&libraries), &rules, |asked| {
                        Ok(asked.iter().map(|p| std::path::Path::new(p).is_dir()).collect())
                    })
                }
                Direction::Push => {
                    let groups = lib.db.with(stl_core::storage::libraries::groups)?;
                    let sent = groups
                        .into_iter()
                        .flat_map(|g| {
                            let library = g.name.clone();
                            g.folders.into_iter().map(move |f| Sent { uuid: f.uuid, name: f.name, library: library.clone(), root: f.root, models: f.models })
                        })
                        .collect();
                    sync::preview_folders(sent, &rules, |asked| {
                        let v = client.post_json("/api/sync/exists", &json!({"paths": asked}))?;
                        serde_json::from_value(v).map_err(|e| format!("{}: unexpected answer ({e})", r.name))
                    })
                }
            }
            .map_err(bad_gateway)?;
            Ok(SyncPreview {
                direction: q.direction,
                local_version: stl_core::VERSION.to_string(),
                problem: sync::check_versions(q.direction, &r.name, &version).err(),
                remote_version: version,
                folders,
            })
        })
        .await?;
    Ok(Json(preview))
}
