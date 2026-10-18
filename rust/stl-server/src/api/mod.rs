//! The `/api` routes and their OpenAPI document (`/api/openapi.json`).

pub mod filter;
pub mod models;
pub mod system;
pub mod thumbs;

use axum::Json;
use axum::Router;
use axum::routing::{get, post};
use utoipa::OpenApi;

use crate::AppState;

#[derive(OpenApi)]
#[openapi(
    info(title = "stlLibrary API", description = "The stlLibrary server's HTTP API."),
    paths(
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
