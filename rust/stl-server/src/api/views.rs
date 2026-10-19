//! Thumbnail views: the image grid over the browser's models and the archive
//! browser by folder (the desktop's Image Grid and Archive Browser).

use std::collections::HashMap;

use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};
use stl_core::storage::views;
use utoipa::ToSchema;

use super::thumbs::thumbnail_url;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState, Library};

/// Most models one grid request may ask for.
const MAX_GRID: usize = 500;

/// One model as a thumbnail cell.
#[derive(Serialize, ToSchema)]
pub struct Cell {
    pub id: i64,
    pub title: Option<String>,
    pub creator: Option<String>,
    pub archive_path: Option<String>,
    /// The first thumbnail.
    pub thumbnail: Option<String>,
    /// How many images the model has.
    pub images: usize,
}

fn first_thumb(lib: &Library, thumbs: &[Vec<Option<String>>]) -> Result<Vec<Option<String>>, ApiError> {
    let firsts: Vec<Option<i64>> = thumbs.iter().map(|t| t.iter().flatten().find_map(|r| r.trim().parse().ok())).collect();
    let ids: Vec<u64> = firsts.iter().flatten().map(|&i| i as u64).collect();
    let digests: HashMap<u64, Option<Vec<u8>>> = lib.thumbs.digests(&ids)?.into_iter().collect();
    Ok(firsts.into_iter().map(|f| f.map(|i| thumbnail_url(i, digests.get(&(i as u64)).and_then(|d| d.as_deref())))).collect())
}

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct GridRequest {
    /// The models, in the order wanted (at most 500).
    pub ids: Vec<i64>,
}

/// Thumbnail cells for models, in the order asked.
#[utoipa::path(post, path = "/api/views/grid", tag = "library", request_body = GridRequest,
    responses((status = 200, body = Vec<Cell>), (status = 400, body = crate::error::ErrorBody)))]
pub async fn grid(State(lib): State<AppState>, _me: CurrentUser, Json(req): Json<GridRequest>) -> Result<Json<Vec<Cell>>, ApiError> {
    if req.ids.len() > MAX_GRID {
        return Err(ApiError::bad_request(format!("at most {MAX_GRID} models per request")));
    }
    let cells = lib
        .run(move |lib| {
            let rows = lib.db.with(|c| views::grid_models(c, &req.ids))?;
            let thumbs = first_thumb(lib, &rows.iter().map(|r| r.thumbs.clone()).collect::<Vec<_>>())?;
            let text = |r: &views::GridModel, k: &str| -> Option<String> {
                r.fields.iter().find(|(f, _)| *f == k).and_then(|(_, v)| match v {
                    rusqlite::types::Value::Text(s) => Some(s.clone()),
                    _ => None,
                })
            };
            let mut by_id: HashMap<i64, Cell> = rows
                .iter()
                .zip(thumbs)
                .map(|(r, thumbnail)| {
                    let cell = Cell {
                        id: r.id,
                        title: text(r, "title"),
                        creator: text(r, "creator"),
                        archive_path: text(r, "archive_path"),
                        thumbnail,
                        images: r.thumbs.len(),
                    };
                    (r.id, cell)
                })
                .collect();
            Ok::<_, ApiError>(req.ids.iter().filter_map(|id| by_id.remove(id)).collect::<Vec<_>>())
        })
        .await?;
    Ok(Json(cells))
}

/// The folders holding catalogued archives (forward slashes), sorted.
#[utoipa::path(get, path = "/api/folders", tag = "library", responses((status = 200, body = Vec<String>)))]
pub async fn folders(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<Vec<String>>, ApiError> {
    Ok(Json(lib.run(|lib| Ok::<_, ApiError>(lib.db.with(views::archive_folders)?)).await?))
}

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct FolderRequest {
    /// A folder from `/api/folders`.
    pub folder: String,
    #[serde(default)]
    pub include_subfolders: bool,
}

/// Thumbnail cells for the archives in a folder (and its subfolders), by path.
#[utoipa::path(post, path = "/api/views/folder", tag = "library", request_body = FolderRequest,
    responses((status = 200, body = Vec<Cell>)))]
pub async fn folder(State(lib): State<AppState>, _me: CurrentUser, Json(req): Json<FolderRequest>) -> Result<Json<Vec<Cell>>, ApiError> {
    let cells = lib
        .run(move |lib| {
            let rows = lib.db.with(|c| views::folder_models(c, &req.folder, req.include_subfolders))?;
            let thumbs = first_thumb(lib, &rows.iter().map(|r| r.thumbs.clone()).collect::<Vec<_>>())?;
            Ok::<_, ApiError>(
                rows.into_iter()
                    .zip(thumbs)
                    .map(|(r, thumbnail)| Cell {
                        id: r.id,
                        images: r.thumbs.len(),
                        title: r.title,
                        creator: r.creator,
                        archive_path: r.archive_path,
                        thumbnail,
                    })
                    .collect::<Vec<_>>(),
            )
        })
        .await?;
    Ok(Json(cells))
}
