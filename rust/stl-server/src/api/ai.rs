//! The AI tagger: whether a model is available, and saving a reviewed
//! suggestion (suggestions come from the `ai_suggest` job, batches from `ai_tag`).

use axum::Json;
use axum::extract::{Path, State};
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::ai::{AiFieldValues, AiStatus};
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

/// Whether the AI tagger can run, and with what.
#[utoipa::path(get, path = "/api/ai/status", tag = "ai", responses((status = 200, body = AiStatus)))]
pub async fn status(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<AiStatus>, ApiError> {
    Ok(Json(lib.run(|lib| Ok::<_, ApiError>(crate::workers::ai_status(lib))).await?))
}

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct AiCommit {
    pub fields: AiFieldValues,
    #[serde(default)]
    pub tags: Vec<String>,
    /// Replace values already set (default: fill empty fields only). Tags are merged.
    #[serde(default)]
    pub overwrite: bool,
}

#[derive(Serialize, ToSchema)]
pub struct AiSaved {
    pub saved: bool,
}

/// Save a reviewed suggestion to a model.
#[utoipa::path(post, path = "/api/ai/commit/{id}", tag = "ai", request_body = AiCommit, params(("id" = i64, Path)),
    responses((status = 200, body = AiSaved), (status = 404, body = crate::error::ErrorBody)))]
pub async fn commit(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Json(req): Json<AiCommit>) -> Result<Json<AiSaved>, ApiError> {
    me.require(Role::Editor)?;
    let tags: Vec<String> = req.tags.iter().map(|t| t.trim().to_string()).filter(|t| !t.is_empty()).collect();
    let path = lib
        .run(move |lib| crate::ai::commit(lib, id, req.fields, &tags, req.overwrite).map_err(ApiError::internal))
        .await?
        .ok_or_else(|| ApiError::not_found(format!("no model {id}")))?;
    lib.schedule_sidecars([path]);
    Ok(Json(AiSaved { saved: true }))
}
