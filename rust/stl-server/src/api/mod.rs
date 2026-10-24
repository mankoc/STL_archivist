//! The `/api` routes and their OpenAPI document (`/api/openapi.json`).

pub mod auth;
pub mod edit;
pub mod files;
pub mod filter;
pub mod jobs;
pub mod libraries;
pub mod lookups;
pub mod models;
pub mod search;
pub mod settings;
pub mod ai;
pub mod archive;
pub mod backups;
pub mod components;
pub mod maintenance;
pub mod sort;
pub mod stats;
pub mod views;
pub mod system;
pub mod thumbs;
pub mod users;
pub mod workers;

use axum::Json;
use axum::Router;
use axum::extract::{Request, State};
use axum::http::Method;
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::routing::{delete, get, patch, post, put};
use utoipa::{Modify, OpenApi};

use crate::AppState;

#[derive(OpenApi)]
#[openapi(
    info(title = "StlArchivist API", description = "The StlArchivist server's HTTP API. Every route except \
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
        thumbs::image_original,
        thumbs::thumb,
        edit::cells,
        edit::get_editor,
        edit::save_editor,
        edit::choices,
        edit::subcollections,
        edit::mass_edit,
        edit::parse_paths,
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
        jobs::list,
        jobs::submit,
        jobs::detail,
        jobs::cancel,
        jobs::events,
        libraries::list,
        libraries::add,
        libraries::change,
        libraries::remove,
        libraries::change_folder,
        libraries::remove_folder,
        files::browse,
        files::download,
        settings::get,
        settings::put,
        archive::files,
        archive::archive_files,
        views::grid,
        sort::preview,
        views::folders,
        views::folder,
        archive::warnings,
        ai::status,
        ai::commit,
        backups::list,
        components::status,
        backups::download,
        backups::delete,
        maintenance::merge_moved,
        maintenance::remove,
        stats::library,
        stats::column,
        search::by_image,
        workers::list,
        maintenance::remove_missing,
        workers::token,
        workers::connect,
    ),
    components(schemas(crate::error::ErrorBody)),
    modifiers(&OperationIds)
)]
pub struct ApiDoc;

/// Operation ids from method and path (`get_models_by_id`): handler names
/// repeat across modules (`detail`, `list`, …), and generated clients key
/// operations by id.
struct OperationIds;

impl Modify for OperationIds {
    fn modify(&self, openapi: &mut utoipa::openapi::OpenApi) {
        for (path, item) in openapi.paths.paths.iter_mut() {
            let name: String = path
                .trim_start_matches("/api/")
                .split('/')
                .map(|seg| match seg.strip_prefix('{').and_then(|s| s.strip_suffix('}')) {
                    Some(param) => format!("by_{param}"),
                    None => seg.replace(['-', '.'], "_"),
                })
                .collect::<Vec<_>>()
                .join("_");
            for (method, op) in [
                ("get", &mut item.get),
                ("post", &mut item.post),
                ("put", &mut item.put),
                ("patch", &mut item.patch),
                ("delete", &mut item.delete),
            ] {
                if let Some(op) = op {
                    op.operation_id = Some(format!("{method}_{name}"));
                }
            }
        }
    }
}

pub fn router(state: AppState) -> Router<AppState> {
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
        .route("/api/models/parse-paths", post(edit::parse_paths))
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
        .route("/api/images/{id}/original", get(thumbs::image_original))
        .route("/api/thumb/{digest}", get(thumbs::thumb))
        .route("/api/jobs", get(jobs::list).post(jobs::submit))
        .route("/api/jobs/{id}", get(jobs::detail).delete(jobs::cancel))
        .route("/api/events", get(jobs::events))
        .route("/api/libraries", get(libraries::list).post(libraries::add))
        .route("/api/libraries/{id}", patch(libraries::change))
        .route("/api/libraries/{id}/remove", post(libraries::remove))
        .route("/api/libraries/folders/{id}", patch(libraries::change_folder))
        .route("/api/libraries/folders/{id}/remove", post(libraries::remove_folder))
        .route("/api/files/browse", get(files::browse))
        .route("/api/models/{id}/archive", get(files::download))
        .route("/api/settings/library", get(settings::get).put(settings::put))
        .route("/api/models/{id}/files", get(archive::files))
        .route("/api/models/{id}/archive-files", get(archive::archive_files))
        .route("/api/views/grid", post(views::grid))
        .route("/api/sort/preview", post(sort::preview))
        .route("/api/views/folder", post(views::folder))
        .route("/api/folders", get(views::folders))
        .route("/api/models/{id}/warnings", get(archive::warnings))
        .route("/api/ai/status", get(ai::status))
        .route("/api/ai/commit/{id}", post(ai::commit))
        .route("/api/backups", get(backups::list))
        .route("/api/components", get(components::status))
        .route("/api/backups/{name}", get(backups::download).delete(backups::delete))
        .route("/api/maintenance/merge-moved", post(maintenance::merge_moved))
        .route("/api/maintenance/remove", post(maintenance::remove))
        .route("/api/maintenance/remove-missing", post(maintenance::remove_missing))
        .route("/api/stats/library", get(stats::library))
        .route("/api/stats/column", get(stats::column))
        .route("/api/search/image", post(search::by_image))
        .route("/api/workers", get(workers::list))
        .route("/api/workers/token", post(workers::token))
        .route("/api/workers/connect", get(workers::connect))
        .route("/api/openapi.json", get(openapi))
        .layer(middleware::from_fn_with_state(state, announce_changes))
}

/// After a successful write, tell event-stream clients what kind of data changed.
async fn announce_changes(State(lib): State<AppState>, req: Request, next: Next) -> Response {
    let (method, path) = (req.method().clone(), req.uri().path().to_string());
    let resp = next.run(req).await;
    if method != Method::GET && resp.status().is_success() {
        let what = match path.as_str() {
            // POSTs that only read.
            "/api/models/ids" | "/api/models/rows" | "/api/models/parse-paths" | "/api/filter-tree" => None,
            p if p.starts_with("/api/models") || p.starts_with("/api/images") => Some("models"),
            p if p.starts_with("/api/maintenance") || p.starts_with("/api/ai/commit") || p.starts_with("/api/libraries") => Some("models"),
            p if p.starts_with("/api/lookups") || p.starts_with("/api/tags") || p.starts_with("/api/authors") => Some("lookups"),
            _ => None,
        };
        if let Some(what) = what {
            lib.changed(what);
        }
    }
    resp
}

async fn openapi() -> Json<utoipa::openapi::OpenApi> {
    Json(ApiDoc::openapi())
}

/// The OpenAPI document, pretty-printed with a final newline (what
/// `web/openapi.json` must hold).
pub fn openapi_json() -> String {
    ApiDoc::openapi().to_pretty_json().expect("the document serialises") + "
"
}
