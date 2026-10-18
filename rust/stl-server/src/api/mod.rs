//! The `/api` routes and their OpenAPI document (`/api/openapi.json`).

pub mod auth;
pub mod edit;
pub mod filter;
pub mod lookups;
pub mod models;
pub mod system;
pub mod thumbs;
pub mod users;

use axum::Json;
use axum::Router;
use axum::routing::{delete, get, patch, post, put};
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
        edit::cells,
        edit::get_editor,
        edit::save_editor,
        edit::choices,
        edit::subcollections,
        edit::mass_edit,
        edit::delete_models,
        edit::add_images,
        edit::delete_image,
        lookups::list,
        lookups::add,
        lookups::info,
        lookups::rename,
        lookups::delete,
        lookups::merge,
        lookups::merge_tags,
        lookups::unused_tags,
        lookups::delete_unused_tags,
        lookups::authors,
        lookups::change_author,
        lookups::profile,
        lookups::save_profile,
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
        .route("/api/users/{id}", patch(users::update).delete(users::delete))
        .route("/api/me/settings", get(users::settings))
        .route("/api/me/settings/{key}", put(users::put_setting).delete(users::delete_setting))
        .route("/api/system/info", get(system::info))
        .route("/api/models/ids", post(models::ids))
        .route("/api/models/rows", post(models::rows))
        .route("/api/models/{id}", get(models::detail))
        .route("/api/models/cells", patch(edit::cells))
        .route("/api/models/{id}/editor", get(edit::get_editor).put(edit::save_editor))
        .route("/api/models/mass-edit", post(edit::mass_edit))
        .route("/api/models/delete", post(edit::delete_models))
        .route("/api/models/{id}/images", post(edit::add_images))
        .route("/api/images/{id}", delete(edit::delete_image))
        .route("/api/editor/choices", get(edit::choices))
        .route("/api/editor/subcollections", get(edit::subcollections))
        .route("/api/lookups/{kind}", get(lookups::list).post(lookups::add))
        .route("/api/lookups/{kind}/merge", post(lookups::merge))
        .route("/api/lookups/{kind}/{id}", get(lookups::info).patch(lookups::rename).delete(lookups::delete))
        .route("/api/tags/merge", post(lookups::merge_tags))
        .route("/api/tags/unused", get(lookups::unused_tags).delete(lookups::delete_unused_tags))
        .route("/api/authors", get(lookups::authors).post(lookups::change_author))
        .route("/api/authors/profile", get(lookups::profile).put(lookups::save_profile))
        .route("/api/filter-tree", post(filter::tree))
        .route("/api/images/{id}/thumbnail", get(thumbs::image_thumbnail))
        .route("/api/thumb/{digest}", get(thumbs::thumb))
        .route("/api/openapi.json", get(openapi))
}

async fn openapi() -> Json<utoipa::openapi::OpenApi> {
    Json(ApiDoc::openapi())
}
