//! Compute workers (see `crate::workers`): who is connected, a token for a new
//! one, and the WebSocket a worker serves on.

use std::sync::Arc;

use axum::Json;
use axum::extract::{Path, State};
use axum::extract::ws::WebSocketUpgrade;
use axum::response::Response;
use serde::Serialize;
use utoipa::ToSchema;

use crate::accounts::{self, Role};
use crate::auth::{CurrentUser, WorkerUser};
use crate::workers::WorkersStatus;
use crate::{ApiError, AppState};

/// The compute mode and the connected workers.
#[utoipa::path(get, path = "/api/workers", tag = "workers", responses((status = 200, body = WorkersStatus)))]
pub async fn list(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<WorkersStatus>, ApiError> {
    Ok(Json(crate::workers::status(&lib)))
}

#[derive(Serialize, ToSchema)]
pub struct WorkerToken {
    /// A new compute worker session of the signed-in user, for `stl-server worker`
    /// (`STL_WORKER_TOKEN`): it opens `/api/workers/connect` and no other route.
    /// Signing out elsewhere does not end it; `DELETE /api/workers/tokens/{id}` does.
    pub token: String,
}

/// A compute worker's token (editors): the web client asks for one and hands it
/// to the desktop app, which starts the worker with it.
#[utoipa::path(post, path = "/api/workers/token", tag = "workers",
    responses((status = 200, body = WorkerToken), (status = 403, body = crate::error::ErrorBody)))]
pub async fn token(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<WorkerToken>, ApiError> {
    me.require(Role::Editor)?;
    let id = me.user.id;
    let token = lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::create_worker_session(c, id))?)).await?;
    Ok(Json(WorkerToken { token }))
}

/// Who may see or end which worker sessions: administrators every user's, editors their own.
fn owner(me: &CurrentUser) -> Result<Option<i64>, ApiError> {
    me.require(Role::Editor)?;
    Ok(if me.user.role >= Role::Admin { None } else { Some(me.user.id) })
}

/// The live compute worker sessions (editors: their own; administrators: everyone's).
#[utoipa::path(get, path = "/api/workers/tokens", tag = "workers",
    responses((status = 200, body = Vec<accounts::WorkerSession>), (status = 403, body = crate::error::ErrorBody)))]
pub async fn tokens(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<Vec<accounts::WorkerSession>>, ApiError> {
    let owner = owner(&me)?;
    Ok(Json(lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::worker_sessions(c, owner))?)).await?))
}

/// End a compute worker session (editors: their own; administrators: anyone's).
/// A worker connected with it is disconnected within a minute.
#[utoipa::path(delete, path = "/api/workers/tokens/{id}", tag = "workers",
    params(("id" = String, Path, description = "The session's id from GET /api/workers/tokens")),
    responses((status = 204), (status = 403, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody)))]
pub async fn revoke(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<String>) -> Result<axum::http::StatusCode, ApiError> {
    let owner = owner(&me)?;
    let who = me.user.name.clone();
    let ended = lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::end_worker_session(c, &id, owner))?)).await?;
    if !ended {
        return Err(ApiError::not_found("no such worker session"));
    }
    tracing::info!(by = %who, "compute worker session ended");
    Ok(axum::http::StatusCode::NO_CONTENT)
}

/// A compute worker's connection (editors): a WebSocket of JSON frames, the
/// worker's `hello` first (see `crate::workers::ToServer` / `ToWorker`).
#[utoipa::path(get, path = "/api/workers/connect", tag = "workers",
    responses((status = 101, description = "WebSocket of worker messages"), (status = 403, body = crate::error::ErrorBody)))]
pub async fn connect(State(lib): State<AppState>, WorkerUser(me): WorkerUser, ws: WebSocketUpgrade) -> Result<Response, ApiError> {
    me.require(Role::Editor)?;
    let lib = Arc::clone(&lib);
    Ok(ws.max_message_size(crate::workers::MAX_MESSAGE).on_upgrade(move |socket| crate::workers::serve(lib, me, socket)))
}
