//! Sign-in: status, first-run setup, login, logout, own password.

use std::net::SocketAddr;

use axum::Json;
use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde::{Deserialize, Serialize};
use utoipa::ToSchema;

use crate::accounts::{self, Role, User};
use crate::auth::{self, CurrentUser};
use crate::{ApiError, AppState};

#[derive(Serialize, ToSchema)]
pub struct AuthStatus {
    pub version: &'static str,
    /// No user exists yet: the first administrator must be created
    /// (`POST /api/auth/setup`, from the server's own machine).
    pub setup_required: bool,
    /// The signed-in user, if the request carries a live session.
    pub user: Option<User>,
}

/// Whether setup is needed and who is signed in. Needs no session.
#[utoipa::path(get, path = "/api/auth/status", tag = "auth", responses((status = 200, body = AuthStatus)))]
pub async fn status(State(lib): State<AppState>, headers: HeaderMap) -> Result<Json<AuthStatus>, ApiError> {
    let token = auth::request_token(&headers);
    let (count, user) = lib
        .run(move |lib| {
            Ok(lib.accounts.with(|c| {
                let user = match &token {
                    Some(t) => accounts::session_user(c, t)?,
                    None => None,
                };
                Ok::<_, accounts::AccountError>((accounts::user_count(c)?, user))
            })?)
        })
        .await?;
    Ok(Json(AuthStatus { version: stl_core::VERSION, setup_required: count == 0, user }))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct Credentials {
    pub name: String,
    pub password: String,
}

#[derive(Serialize, ToSchema)]
pub struct Session {
    /// For `Authorization: Bearer`; browsers can rely on the cookie instead.
    pub token: String,
    pub user: User,
}

fn signed_in(token: String, user: User, headers: &HeaderMap) -> Response {
    let cookie = auth::session_cookie(&token, headers);
    ([(header::SET_COOKIE, cookie)], Json(Session { token, user })).into_response()
}

/// Create the first administrator and sign in. Only while no user exists, and
/// only from the server's own machine (not through a proxy).
#[utoipa::path(post, path = "/api/auth/setup", tag = "auth", request_body = Credentials,
    responses((status = 200, body = Session), (status = 403, body = crate::error::ErrorBody),
              (status = 409, body = crate::error::ErrorBody)))]
pub async fn setup(
    State(lib): State<AppState>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Json(c): Json<Credentials>,
) -> Result<Response, ApiError> {
    if !peer.ip().is_loopback() || auth::proxied(&headers) {
        return Err(ApiError::new(StatusCode::FORBIDDEN, "set up the first administrator on the server's own machine"));
    }
    let (token, user) = lib
        .run(move |lib| {
            lib.accounts
                .with_tx(|tx| {
                    if accounts::user_count(tx)? > 0 {
                        return Err(accounts::AccountError::Conflict("setup is done; sign in instead".into()));
                    }
                    let user = accounts::create_user(tx, &c.name, &c.password, Role::Admin)?;
                    Ok((accounts::create_session(tx, user.id)?, user))
                })
                .map_err(ApiError::from)
        })
        .await?;
    tracing::info!(user = %user.name, "first administrator created");
    Ok(signed_in(token, user, &headers))
}

/// Sign in. After five failures a name is locked out for 15 minutes.
#[utoipa::path(post, path = "/api/auth/login", tag = "auth", request_body = Credentials,
    responses((status = 200, body = Session), (status = 401, body = crate::error::ErrorBody),
              (status = 429, body = crate::error::ErrorBody)))]
pub async fn login(State(lib): State<AppState>, headers: HeaderMap, Json(c): Json<Credentials>) -> Result<Response, ApiError> {
    if let Some(left) = lib.logins.locked_for(&c.name, accounts::now()) {
        return Err(ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            format!("too many failed sign-ins; try again in {} minutes", (left + 59) / 60),
        ));
    }
    let name = c.name.clone();
    let found = lib
        .run(move |lib| {
            Ok(lib.accounts.with(|db| {
                let Some(user) = accounts::authenticate(db, &c.name, &c.password)? else { return Ok(None) };
                Ok::<_, accounts::AccountError>(Some((accounts::create_session(db, user.id)?, user)))
            })?)
        })
        .await?;
    match found {
        Some((token, user)) => {
            lib.logins.succeeded(&name);
            Ok(signed_in(token, user, &headers))
        }
        None => {
            lib.logins.failed(&name, accounts::now());
            tracing::warn!(name = %name, "failed sign-in");
            Err(ApiError::new(StatusCode::UNAUTHORIZED, "wrong name or password"))
        }
    }
}

/// End this session.
#[utoipa::path(post, path = "/api/auth/logout", tag = "auth", responses((status = 204)))]
pub async fn logout(State(lib): State<AppState>, me: CurrentUser) -> Result<Response, ApiError> {
    lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::end_session(c, &me.token))?)).await?;
    Ok((StatusCode::NO_CONTENT, [(header::SET_COOKIE, auth::clear_cookie())]).into_response())
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct PasswordChange {
    pub current: String,
    pub new: String,
}

/// Change your own password. Ends all your sessions, this one included.
#[utoipa::path(put, path = "/api/auth/password", tag = "auth", request_body = PasswordChange,
    responses((status = 204), (status = 400, body = crate::error::ErrorBody)))]
pub async fn change_password(
    State(lib): State<AppState>,
    me: CurrentUser,
    Json(p): Json<PasswordChange>,
) -> Result<Response, ApiError> {
    lib.run(move |lib| Ok(lib.accounts.with(|c| accounts::change_own_password(c, me.user.id, &p.current, &p.new))?))
        .await?;
    Ok((StatusCode::NO_CONTENT, [(header::SET_COOKIE, auth::clear_cookie())]).into_response())
}
