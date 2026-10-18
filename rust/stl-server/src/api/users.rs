//! User administration (admin role) and per-user settings (any user).

use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use serde::Deserialize;
use utoipa::ToSchema;

use crate::accounts::{self, Role, User};
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

/// Every user, by name.
#[utoipa::path(get, path = "/api/users", tag = "users",
    responses((status = 200, body = Vec<User>), (status = 403, body = crate::error::ErrorBody)))]
pub async fn list(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<Vec<User>>, ApiError> {
    me.require(Role::Admin)?;
    Ok(Json(lib.run(|lib| Ok(lib.accounts.with(accounts::list_users)?)).await?))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct NewUser {
    pub name: String,
    pub password: String,
    pub role: Role,
}

/// Create a user.
#[utoipa::path(post, path = "/api/users", tag = "users", request_body = NewUser,
    responses((status = 201, body = User), (status = 409, body = crate::error::ErrorBody)))]
pub async fn create(
    State(lib): State<AppState>,
    me: CurrentUser,
    Json(n): Json<NewUser>,
) -> Result<(StatusCode, Json<User>), ApiError> {
    me.require(Role::Admin)?;
    let user = lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::create_user(c, &n.name, &n.password, n.role))?)).await?;
    tracing::info!(by = %me.user.name, user = %user.name, role = user.role.as_str(), "user created");
    Ok((StatusCode::CREATED, Json(user)))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct UserChange {
    pub role: Option<Role>,
    pub disabled: Option<bool>,
    /// A new password; ends the user's sessions.
    pub password: Option<String>,
}

/// Change a user's role, enabled state or password. The last enabled
/// administrator cannot be demoted or disabled.
#[utoipa::path(patch, path = "/api/users/{id}", tag = "users", request_body = UserChange,
    params(("id" = i64, Path, description = "User id")),
    responses((status = 200, body = User), (status = 404, body = crate::error::ErrorBody),
              (status = 409, body = crate::error::ErrorBody)))]
pub async fn update(
    State(lib): State<AppState>,
    me: CurrentUser,
    Path(id): Path<i64>,
    Json(ch): Json<UserChange>,
) -> Result<Json<User>, ApiError> {
    me.require(Role::Admin)?;
    let user = lib
        .run(move |lib| Ok(lib.accounts.with_tx(|tx| accounts::update_user(tx, id, ch.role, ch.disabled, ch.password.as_deref()))?))
        .await?;
    tracing::info!(by = %me.user.name, user = %user.name, "user changed");
    Ok(Json(user))
}

/// Delete a user (not the last enabled administrator).
#[utoipa::path(delete, path = "/api/users/{id}", tag = "users",
    params(("id" = i64, Path, description = "User id")),
    responses((status = 204), (status = 404, body = crate::error::ErrorBody), (status = 409, body = crate::error::ErrorBody)))]
pub async fn delete(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>) -> Result<StatusCode, ApiError> {
    me.require(Role::Admin)?;
    lib.run(move |lib| Ok(lib.accounts.with_tx(|tx| accounts::delete_user(tx, id))?)).await?;
    tracing::info!(by = %me.user.name, user = id, "user deleted");
    Ok(StatusCode::NO_CONTENT)
}

/// Your own settings: key → any JSON value.
#[utoipa::path(get, path = "/api/me/settings", tag = "users",
    responses((status = 200, body = Object)))]
pub async fn settings(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<serde_json::Value>, ApiError> {
    let s = lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::settings(c, me.user.id))?)).await?;
    Ok(Json(serde_json::Value::Object(s)))
}

/// Store one of your settings (the body is its JSON value, at most 64 KB).
#[utoipa::path(put, path = "/api/me/settings/{key}", tag = "users", request_body = Object,
    params(("key" = String, Path, description = "Setting key: a-z, A-Z, 0-9, '.', '_', '-'")),
    responses((status = 204), (status = 400, body = crate::error::ErrorBody)))]
pub async fn put_setting(
    State(lib): State<AppState>,
    me: CurrentUser,
    Path(key): Path<String>,
    Json(value): Json<serde_json::Value>,
) -> Result<StatusCode, ApiError> {
    lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::set_setting(c, me.user.id, &key, &value))?)).await?;
    Ok(StatusCode::NO_CONTENT)
}

/// Remove one of your settings.
#[utoipa::path(delete, path = "/api/me/settings/{key}", tag = "users",
    params(("key" = String, Path, description = "Setting key")),
    responses((status = 204)))]
pub async fn delete_setting(State(lib): State<AppState>, me: CurrentUser, Path(key): Path<String>) -> Result<StatusCode, ApiError> {
    lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::delete_setting(c, me.user.id, &key))?)).await?;
    Ok(StatusCode::NO_CONTENT)
}
