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

/// Whether an OpenRouter API key is set (the key itself is never sent back).
#[derive(Serialize, ToSchema)]
pub struct OpenRouterKeyState {
    pub key_set: bool,
}

/// A new OpenRouter API key; empty removes it.
#[derive(Debug, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct OpenRouterKey {
    pub key: String,
}

/// The OpenAI-compatible endpoint: its address, and whether a key is set (the
/// key itself is never sent back).
#[derive(Serialize, ToSchema)]
pub struct OpenAiEndpointState {
    pub url: String,
    pub key_set: bool,
}

/// A new address for the OpenAI-compatible endpoint (empty: Gemini's) and,
/// when given, its API key (empty removes it).
#[derive(Debug, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct OpenAiEndpoint {
    pub url: String,
    #[serde(default)]
    pub key: Option<String>,
}

fn check_key(key: &str) -> Result<(), ApiError> {
    if key.len() > 500 || !key.chars().all(|c| c.is_ascii_graphic()) {
        return Err(ApiError::bad_request("the key is printable ASCII without spaces"));
    }
    Ok(())
}

/// The AI tagger's OpenAI-compatible endpoint.
#[utoipa::path(get, path = "/api/ai/openai", tag = "ai", responses((status = 200, body = OpenAiEndpointState)))]
pub async fn openai_get(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<OpenAiEndpointState>, ApiError> {
    let ep = lib.accounts.with(crate::ai::openai_endpoint).map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(OpenAiEndpointState { url: ep.url, key_set: ep.key.is_some() }))
}

/// Set the OpenAI-compatible endpoint's address and key (administrators).
/// Both are kept in `server.db`, so the key only goes to the address set here.
#[utoipa::path(put, path = "/api/ai/openai", tag = "ai", request_body = OpenAiEndpoint,
    responses((status = 200, body = OpenAiEndpointState), (status = 400, body = crate::error::ErrorBody),
              (status = 403, body = crate::error::ErrorBody)))]
pub async fn openai_put(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<OpenAiEndpoint>) -> Result<Json<OpenAiEndpointState>, ApiError> {
    me.require(Role::Admin)?;
    let url = req.url.trim().trim_end_matches('/');
    let url = if url.is_empty() { crate::ai::OPENAI_URL_DEFAULT } else { url };
    let scheme_ok = url.strip_prefix("https://").or_else(|| url.strip_prefix("http://")).is_some_and(|rest| !rest.is_empty());
    if !scheme_ok || url.len() > 500 || url.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err(ApiError::bad_request("url is an http(s) address such as https://api.openai.com/v1"));
    }
    let key = req.key.as_deref().map(str::trim);
    if let Some(k) = key {
        check_key(k)?;
    }
    let ep = lib
        .accounts
        .with(|c| {
            crate::ai::set_openai_endpoint(c, url, key)?;
            crate::ai::openai_endpoint(c)
        })
        .map_err(|e| ApiError::internal(e.to_string()))?;
    tracing::info!(by = %me.user.name, url, key_changed = key.is_some(), "OpenAI-compatible endpoint changed");
    Ok(Json(OpenAiEndpointState { url: ep.url, key_set: ep.key.is_some() }))
}

/// Whether the AI tagger has an OpenRouter API key.
#[utoipa::path(get, path = "/api/ai/openrouter", tag = "ai", responses((status = 200, body = OpenRouterKeyState)))]
pub async fn openrouter_get(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<OpenRouterKeyState>, ApiError> {
    let key = lib.accounts.with(crate::ai::openrouter_key).map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(OpenRouterKeyState { key_set: key.is_some() }))
}

/// Set or remove the OpenRouter API key (administrators). Kept in `server.db`.
#[utoipa::path(put, path = "/api/ai/openrouter", tag = "ai", request_body = OpenRouterKey,
    responses((status = 200, body = OpenRouterKeyState), (status = 400, body = crate::error::ErrorBody),
              (status = 403, body = crate::error::ErrorBody)))]
pub async fn openrouter_put(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<OpenRouterKey>) -> Result<Json<OpenRouterKeyState>, ApiError> {
    me.require(Role::Admin)?;
    let key = req.key.trim();
    check_key(key)?;
    lib.accounts.with(|c| crate::ai::set_openrouter_key(c, key)).map_err(|e| ApiError::internal(e.to_string()))?;
    tracing::info!(by = %me.user.name, set = !key.is_empty(), "OpenRouter API key changed");
    Ok(Json(OpenRouterKeyState { key_set: !key.is_empty() }))
}
