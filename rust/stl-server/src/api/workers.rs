//! Compute workers (see `crate::workers`): who is connected, a token for a new
//! one, and the WebSocket a worker serves on.

use std::sync::Arc;

use axum::Json;
use axum::extract::State;
use axum::extract::ws::WebSocketUpgrade;
use axum::response::Response;
use serde::Serialize;
use utoipa::ToSchema;

use crate::accounts::{self, Role};
use crate::auth::CurrentUser;
use crate::workers::WorkersStatus;
use crate::{ApiError, AppState};

/// The compute mode and the connected workers.
#[utoipa::path(get, path = "/api/workers", tag = "workers", responses((status = 200, body = WorkersStatus)))]
pub async fn list(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<WorkersStatus>, ApiError> {
    Ok(Json(crate::workers::status(&lib)))
}

#[derive(Serialize, ToSchema)]
pub struct WorkerToken {
    /// A new session of the signed-in user, for `stl-server worker`
    /// (`STL_WORKER_TOKEN`). Signing out elsewhere does not end it.
    pub token: String,
}

/// A session token for a compute worker (editors): the web client asks for
/// one and hands it to the desktop app, which starts the worker with it.
#[utoipa::path(post, path = "/api/workers/token", tag = "workers",
    responses((status = 200, body = WorkerToken), (status = 403, body = crate::error::ErrorBody)))]
pub async fn token(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<WorkerToken>, ApiError> {
    me.require(Role::Editor)?;
    let id = me.user.id;
    let token = lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::create_session(c, id))?)).await?;
    Ok(Json(WorkerToken { token }))
}

/// A compute worker's connection (editors): a WebSocket of JSON frames, the
/// worker's `hello` first (see `crate::workers::ToServer` / `ToWorker`).
#[utoipa::path(get, path = "/api/workers/connect", tag = "workers",
    responses((status = 101, description = "WebSocket of worker messages"), (status = 403, body = crate::error::ErrorBody)))]
pub async fn connect(State(lib): State<AppState>, me: CurrentUser, ws: WebSocketUpgrade) -> Result<Response, ApiError> {
    me.require(Role::Editor)?;
    let lib = Arc::clone(&lib);
    Ok(ws.max_message_size(crate::workers::MAX_MESSAGE).on_upgrade(move |socket| crate::workers::serve(lib, me.user.name, socket)))
}
