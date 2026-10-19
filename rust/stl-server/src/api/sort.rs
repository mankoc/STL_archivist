//! Sort files: preview where a pattern would move the selected archives (the
//! move itself is the `sort_files` job).

use std::path::PathBuf;

use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};
use stl_core::sort_files;
use stl_core::storage::browser;
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct SortRequest {
    pub ids: Vec<i64>,
    /// The folder the tree is built in (a server folder).
    pub target_dir: String,
    /// `{collection}/{creator}/{title}`: folders, then the file name.
    pub pattern: String,
}

#[derive(Serialize, ToSchema)]
pub struct SortRow {
    pub id: i64,
    /// `OK`, `COLLISION` (a free name is used), `MISS`, `SKIP` or `ERROR`.
    pub status: String,
    pub from: String,
    pub to: String,
}

/// Where the archives would go (nothing is moved).
#[utoipa::path(post, path = "/api/sort/preview", tag = "editing", request_body = SortRequest,
    responses((status = 200, body = Vec<SortRow>), (status = 403, body = crate::error::ErrorBody)))]
pub async fn preview(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<SortRequest>) -> Result<Json<Vec<SortRow>>, ApiError> {
    me.require(Role::Editor)?;
    if req.pattern.trim().is_empty() {
        return Err(ApiError::bad_request("give a path pattern"));
    }
    let rows = lib
        .run(move |lib| {
            super::files::check_access(lib, &me, &PathBuf::from(&req.target_dir))?;
            let sources = lib.db.with(|c| browser::sort_sources(c, &req.ids))?;
            Ok::<_, ApiError>(sort_files::preview(&sources, &req.ids, &PathBuf::from(&req.target_dir), &req.pattern))
        })
        .await?;
    Ok(Json(rows.into_iter().map(|r| SortRow { id: r.id, status: r.status.into(), from: r.from, to: r.to }).collect()))
}
