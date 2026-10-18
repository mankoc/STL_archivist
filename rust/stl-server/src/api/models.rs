//! The browser table: matching ids in display order, rows by id, one model's detail.

use std::collections::BTreeMap;

use axum::Json;
use std::net::SocketAddr;

use axum::extract::{ConnectInfo, Path, State};
use axum::http::HeaderMap;
use rusqlite::types::Value;
use serde::{Deserialize, Serialize};
use stl_core::storage::{browser, library};
use utoipa::ToSchema;

use super::filter::FilterSpecDto;
use super::thumbs::thumbnail_url;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

/// Most rows one `/api/models/rows` request may ask for.
pub const MAX_ROWS: usize = 2000;

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct Sort {
    /// One of `title`, `creator`, `source`, `sculptor_name`, `collection_name`,
    /// `subcollection_name`, `sequence`, `release_name`, `archive_path`,
    /// `presupports`, `created_at`, `archive_size`, `compression_rate`, `category_name`.
    pub column: String,
    #[serde(default)]
    pub descending: bool,
}

#[derive(Debug, Default, Serialize, Deserialize, ToSchema)]
#[serde(default, deny_unknown_fields)]
pub struct ModelQuery {
    pub filter: FilterSpecDto,
    /// Matched against title, creator, path and tags (case-insensitive).
    pub search: String,
    /// Display order; ties and no sort are ordered by id.
    pub sort: Option<Sort>,
}

#[derive(Debug, Serialize, ToSchema)]
pub struct IdList {
    pub ids: Vec<i64>,
}

/// The ids of the models matching the query, in display order.
#[utoipa::path(post, path = "/api/models/ids", tag = "library", request_body = ModelQuery,
    responses((status = 200, body = IdList), (status = 400, body = crate::error::ErrorBody)))]
pub async fn ids(State(lib): State<AppState>, _me: CurrentUser, Json(q): Json<ModelQuery>) -> Result<Json<IdList>, ApiError> {
    let spec = q.filter.to_spec()?;
    if let Some(s) = &q.sort
        && !library::SORT_COLUMNS.contains(&s.column.as_str())
    {
        return Err(ApiError::bad_request(format!("cannot sort by {:?}", s.column)));
    }
    let ids = lib
        .run(move |lib| {
            let sort = q.sort.as_ref().map(|s| (s.column.as_str(), s.descending));
            Ok(lib.db.with(|c| library::table_ids(c, &spec, &q.search, sort))?)
        })
        .await?;
    Ok(Json(IdList { ids }))
}

#[derive(Debug, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct RowsRequest {
    pub ids: Vec<i64>,
}

/// One table row.
#[derive(Debug, Serialize, ToSchema)]
pub struct Row {
    pub id: i64,
    pub title: Option<String>,
    pub creator: Option<String>,
    pub source: Option<String>,
    pub release_year: Option<i64>,
    pub release_month: Option<i64>,
    pub archive_path: Option<String>,
    pub presupports: Option<String>,
    pub warning: Option<String>,
    pub archive_size: Option<i64>,
    pub compression_rate: Option<f64>,
    pub sequence: Option<i64>,
    pub created_at: Option<String>,
    pub sculptor: Option<String>,
    pub collection: Option<String>,
    pub subcollection: Option<String>,
    pub category: Option<String>,
    pub release: Option<String>,
    /// Comma-separated, by name.
    pub scales: String,
    /// Comma-separated, by name.
    pub tags: String,
    pub to_print: bool,
}

impl From<library::PageRow> for Row {
    fn from(r: library::PageRow) -> Self {
        Row {
            id: r.id,
            title: r.title,
            creator: r.creator,
            source: r.source,
            release_year: r.release_year,
            release_month: r.release_month,
            archive_path: r.archive_path,
            presupports: r.presupports,
            warning: r.warning,
            archive_size: r.archive_size,
            compression_rate: r.compression_rate,
            sequence: r.sequence,
            created_at: r.created_at,
            sculptor: r.sculptor_name,
            collection: r.collection_name,
            subcollection: r.subcollection_name,
            category: r.category_name,
            release: r.release_name,
            scales: r.scales,
            tags: r.tags,
            to_print: r.to_print.unwrap_or(false),
        }
    }
}

#[derive(Debug, Serialize, ToSchema)]
pub struct Rows {
    pub rows: Vec<Row>,
}

/// Rows for `ids`, in that order; unknown ids are skipped.
#[utoipa::path(post, path = "/api/models/rows", tag = "library", request_body = RowsRequest,
    responses((status = 200, body = Rows), (status = 400, body = crate::error::ErrorBody)))]
pub async fn rows(State(lib): State<AppState>, _me: CurrentUser, Json(req): Json<RowsRequest>) -> Result<Json<Rows>, ApiError> {
    if req.ids.len() > MAX_ROWS {
        return Err(ApiError::bad_request(format!("at most {MAX_ROWS} rows per request")));
    }
    let rows = lib.run(move |lib| Ok(lib.db.with(|c| library::page_rows(c, &req.ids))?)).await?;
    Ok(Json(Rows { rows: rows.into_iter().map(Row::from).collect() }))
}

#[derive(Debug, Serialize, ToSchema)]
pub struct ImageRef {
    pub id: i64,
    /// Where the thumbnail is served; absent when the image has none.
    pub thumbnail: Option<String>,
}

#[derive(Debug, Serialize, ToSchema)]
pub struct ModelDetail {
    pub id: i64,
    /// `title`, `creator`, `source`, `sculptor_name`, `collection_name`,
    /// `subcollection_name`, `category_name`, `release_name`, `release_year`,
    /// `release_month`, `archive_path`, `archive_size`, `is_missing`, `tags`
    /// (comma-separated) and `image_count`.
    #[schema(value_type = Object)]
    pub fields: BTreeMap<String, serde_json::Value>,
    pub images: Vec<ImageRef>,
    /// The archive's path as this client sees it, when it can open it there
    /// (a client on the server's machine, or a `path_map` entry); otherwise
    /// offer `/api/models/{id}/archive`.
    pub client_path: Option<String>,
}

fn json_value(v: Value) -> serde_json::Value {
    match v {
        Value::Null => serde_json::Value::Null,
        Value::Integer(i) => i.into(),
        Value::Real(f) => serde_json::Number::from_f64(f).map_or(serde_json::Value::Null, Into::into),
        Value::Text(s) => s.into(),
        Value::Blob(_) => serde_json::Value::Null,
    }
}

/// One model's display fields and its images.
#[utoipa::path(get, path = "/api/models/{id}", tag = "library",
    params(("id" = i64, Path, description = "Model id")),
    responses((status = 200, body = ModelDetail), (status = 404, body = crate::error::ErrorBody)))]
pub async fn detail(
    State(lib): State<AppState>,
    _me: CurrentUser,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Path(id): Path<i64>,
) -> Result<Json<ModelDetail>, ApiError> {
    let detail = lib
        .run(move |lib| {
            let (summary, images) = lib.db.with(|c| {
                Ok::<_, rusqlite::Error>((library::model_summary(c, id)?, browser::model_images(c, id)?))
            })?;
            let Some(summary) = summary else { return Ok(None) };
            let ids: Vec<u64> = images.iter().map(|&(i, _)| i as u64).collect();
            let digests: std::collections::HashMap<u64, Option<Vec<u8>>> =
                lib.thumbs.digests(&ids)?.into_iter().collect();
            let images = images
                .into_iter()
                .map(|(image_id, _)| ImageRef {
                    id: image_id,
                    thumbnail: digests.get(&(image_id as u64)).map(|d| thumbnail_url(image_id, d.as_deref())),
                })
                .collect();
            let client_path = summary
                .iter()
                .find(|(k, _)| k == "archive_path")
                .and_then(|(_, v)| if let Value::Text(p) = v { Some(p.clone()) } else { None })
                .and_then(|p| super::files::client_path(&p, &peer, &headers, &lib.config.path_map));
            let fields = summary.into_iter().filter(|(k, _)| k != "id").map(|(k, v)| (k, json_value(v))).collect();
            Ok(Some(ModelDetail { id, fields, images, client_path }))
        })
        .await?;
    detail.map(Json).ok_or_else(|| ApiError::not_found(format!("no model {id}")))
}
