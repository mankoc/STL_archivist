//! Server information.

use axum::Json;
use axum::extract::State;
use serde::Serialize;
use utoipa::ToSchema;

use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

#[derive(Serialize, ToSchema)]
pub struct ServerInfo {
    /// Always `"stlLibrary"`.
    pub name: &'static str,
    pub version: &'static str,
    /// Models in the library.
    pub models: i64,
}

/// Name, version and library size.
#[utoipa::path(get, path = "/api/system/info", tag = "system",
    responses((status = 200, body = ServerInfo)))]
pub async fn info(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<ServerInfo>, ApiError> {
    let models = lib
        .run(|lib| Ok(lib.db.with(|c| c.query_row("SELECT count(*) FROM models", [], |r| r.get(0)))?))
        .await?;
    Ok(Json(ServerInfo { name: "stlLibrary", version: stl_core::VERSION, models }))
}
