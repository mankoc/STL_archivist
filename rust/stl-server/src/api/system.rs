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
    /// Beside the version for a development build ("nightly 9940de4"); none for a release.
    pub build: Option<&'static str>,
    /// Models in the library.
    pub models: i64,
    /// Update information (if available).
    pub update: Option<crate::updates::UpdateInfo>,
}

/// Name, version and library size.
#[utoipa::path(get, path = "/api/system/info", tag = "system",
    responses((status = 200, body = ServerInfo)))]
pub async fn info(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<ServerInfo>, ApiError> {
    let (models, update) = lib
        .run(move |lib| {
            let models: i64 = lib.db.with(|c| c.query_row("SELECT count(*) FROM models", [], |r| r.get(0)))?;
            let settings = lib.accounts.with(|c| crate::accounts::settings(c, me.user.id))?;
            let enabled = settings.get("check_updates").and_then(serde_json::Value::as_bool).unwrap_or(false);
            Ok((models, enabled.then(|| lib.updates.check(stl_core::VERSION))))
        })
        .await?;
    Ok(Json(ServerInfo { name: "stlLibrary", version: stl_core::VERSION, build: stl_core::build(), models, update }))
}
