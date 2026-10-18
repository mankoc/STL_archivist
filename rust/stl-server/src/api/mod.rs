//! The `/api` routes and their OpenAPI document (`/api/openapi.json`).

pub mod auth;
pub mod filter;
pub mod models;
pub mod system;
pub mod thumbs;
pub mod users;

use axum::Json;
use axum::Router;
use axum::routing::{get, post, put};
use utoipa::OpenApi;

use crate::AppState;

#[derive(OpenApi)]
#[openapi(
    info(title = "stlLibrary API", description = "The stlLibrary server's HTTP API. Every route except \
        `/api/auth/status`, `/api/auth/setup`, `/api/auth/login` and this document needs a session: \
        `Authorization: Bearer <token>` or the `stl_session` cookie set at sign-in."),
    paths(
        auth::status,
        auth::setup,
        auth::login,
        auth::logout,
        auth::change_password,
        users::list,
        users::create,
        users::update,
        users::delete,
        users::settings,
        users::put_setting,
        users::delete_setting,
        system::info,
        models::ids,
        models::rows,
        models::detail,
        filter::tree,
        thumbs::image_thumbnail,
        thumbs::thumb,
    ),
    components(schemas(crate::error::ErrorBody))
)]
pub struct ApiDoc;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/auth/status", get(auth::status))
        .route("/api/auth/setup", post(auth::setup))
        .route("/api/auth/login", post(auth::login))
        .route("/api/auth/logout", post(auth::logout))
        .route("/api/auth/password", put(auth::change_password))
        .route("/api/users", get(users::list).post(users::create))
        .route("/api/users/{id}", axum::routing::patch(users::update).delete(users::delete))
        .route("/api/me/settings", get(users::settings))
        .route("/api/me/settings/{key}", put(users::put_setting).delete(users::delete_setting))
        .route("/api/system/info", get(system::info))
        .route("/api/models/ids", post(models::ids))
        .route("/api/models/rows", post(models::rows))
        .route("/api/models/{id}", get(models::detail))
        .route("/api/filter-tree", post(filter::tree))
        .route("/api/images/{id}/thumbnail", get(thumbs::image_thumbnail))
        .route("/api/thumb/{digest}", get(thumbs::thumb))
        .route("/api/openapi.json", get(openapi))
}

async fn openapi() -> Json<utoipa::openapi::OpenApi> {
    Json(ApiDoc::openapi())
}
