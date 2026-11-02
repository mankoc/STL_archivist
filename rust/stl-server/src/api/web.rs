//! Import from a web page (editors): read a model's page, here or as the
//! desktop app captured it in a window, then apply what the review kept.

use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use stl_core::storage::model_edit;
use stl_core::web_page;
use utoipa::ToSchema;

use super::edit::store_thumbnails;
use super::models::ImageRef;
use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::library_settings::LibrarySettings;
use crate::web::{Client, FetchError, Fetched, image_name, parse_url};
use crate::{ApiError, AppState};

/// Largest page captured by the desktop app (the route's body limit).
pub const MAX_CAPTURED_BYTES: usize = 32 << 20;
/// Images two of which are taken as the same at most this many differing bits
/// apart (dHash, as Duplicates' default).
const SAME_IMAGE_BITS: u32 = 10;

impl From<FetchError> for ApiError {
    fn from(e: FetchError) -> Self {
        match e {
            FetchError::Refused(m) => ApiError::bad_request(m),
            FetchError::Failed(m) => ApiError::new(StatusCode::BAD_GATEWAY, m),
        }
    }
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct WebReadRequest {
    /// The model's page (http or https).
    pub url: String,
}

/// What a model's page says.
#[derive(Serialize, ToSchema)]
pub struct WebPageInfo {
    /// The page's address (its canonical one on the same site).
    pub url: String,
    pub title: Option<String>,
    pub creator: Option<String>,
    /// The site (`Cults3D`, `MyMiniFactory`, `Patreon`, …).
    pub source: Option<String>,
    pub description: Option<String>,
    /// Lowercase, de-duplicated.
    pub tags: Vec<String>,
    /// Image addresses, the page's own first.
    pub images: Vec<String>,
}

impl From<web_page::WebPage> for WebPageInfo {
    fn from(p: web_page::WebPage) -> Self {
        WebPageInfo { url: p.url, title: p.title, creator: p.creator, source: p.source, description: p.description, tags: p.tags, images: p.images }
    }
}

#[derive(Serialize, ToSchema)]
pub struct WebRead {
    /// What the page says, when the server could read it.
    pub page: Option<WebPageInfo>,
    /// Why it could not: the site answers only a browser (a bot check, a
    /// sign-in). The desktop app reads such pages in a window.
    pub blocked: Option<String>,
}

fn editor(me: &CurrentUser) -> Result<(), ApiError> {
    me.require(Role::Editor)
}

/// Read a model's page. Addresses on this computer or its network are refused
/// (unless `web_allow_private`); a site that answers only a browser is
/// reported in `blocked`.
#[utoipa::path(post, path = "/api/web/read", tag = "editing", request_body = WebReadRequest,
    responses((status = 200, body = WebRead), (status = 400, body = crate::error::ErrorBody),
              (status = 403, body = crate::error::ErrorBody), (status = 502, body = crate::error::ErrorBody)))]
pub async fn read(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<WebReadRequest>) -> Result<Json<WebRead>, ApiError> {
    editor(&me)?;
    parse_url(&req.url)?;
    let allow_private = lib.config.web_allow_private;
    let read = lib
        .run(move |_| {
            Ok(match Client::new(allow_private).page(&req.url)? {
                Fetched::Page { url, html } => WebRead { page: Some(web_page::read(&url, &html, &[]).into()), blocked: None },
                Fetched::Blocked(why) => WebRead { page: None, blocked: Some(why) },
            })
        })
        .await?;
    Ok(Json(read))
}

/// A page as the desktop app's window had it.
#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct CapturedPage {
    /// The page's address when it was captured.
    pub url: String,
    /// Its HTML (the document as the browser built it).
    pub html: String,
    /// Images the browser showed large on it.
    #[serde(default)]
    pub images: Vec<String>,
}

/// Read a page the desktop app captured (nothing is fetched).
#[utoipa::path(post, path = "/api/web/read-captured", tag = "editing", request_body = CapturedPage,
    responses((status = 200, body = WebPageInfo), (status = 400, body = crate::error::ErrorBody),
              (status = 403, body = crate::error::ErrorBody), (status = 413, body = crate::error::ErrorBody)))]
pub async fn read_captured(State(lib): State<AppState>, me: CurrentUser, Json(page): Json<CapturedPage>) -> Result<Json<WebPageInfo>, ApiError> {
    editor(&me)?;
    parse_url(&page.url)?;
    if page.images.len() > 1000 {
        return Err(ApiError::bad_request("at most 1000 images"));
    }
    let info = lib.run(move |_| Ok(web_page::read(&page.url, &page.html, &page.images).into())).await?;
    Ok(Json(info))
}

/// What the review kept: fields replace the model's (absent or empty ones are
/// left), tags are added, images downloaded and added.
#[derive(Deserialize, ToSchema, Default)]
#[serde(default, deny_unknown_fields)]
pub struct WebImport {
    pub title: Option<String>,
    pub creator: Option<String>,
    pub source: Option<String>,
    pub url: Option<String>,
    pub description: Option<String>,
    pub tags: Vec<String>,
    /// Image addresses (http or https).
    pub images: Vec<String>,
    /// Skip images that look like one the model has, or an earlier one of `images`.
    pub skip_known: bool,
}

#[derive(Serialize, ToSchema)]
pub struct WebImported {
    /// Whether a field or tag changed.
    pub changed: bool,
    pub added: Vec<ImageRef>,
    /// Images skipped as already there.
    pub skipped: usize,
    /// Images that could not be downloaded or read: `address: why`.
    pub failed: Vec<String>,
}

/// Apply a page's reviewed fields and images to a model.
#[utoipa::path(post, path = "/api/models/{id}/web-import", tag = "editing", request_body = WebImport,
    params(("id" = i64, Path, description = "Model id")),
    responses((status = 200, body = WebImported), (status = 400, body = crate::error::ErrorBody),
              (status = 403, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody)))]
pub async fn import(State(lib): State<AppState>, me: CurrentUser, Path(id): Path<i64>, Json(req): Json<WebImport>) -> Result<Json<WebImported>, ApiError> {
    editor(&me)?;
    if req.images.len() > web_page::MAX_IMAGES {
        return Err(ApiError::bad_request(format!("at most {} images", web_page::MAX_IMAGES)));
    }
    for u in &req.images {
        parse_url(u)?;
    }
    let path = lib.run(move |lib| Ok(lib.db.with(|c| model_edit::archive_paths(c, &[id]))?.remove(&id))).await?;
    let Some(path) = path else { return Err(ApiError::not_found(format!("no model {id}"))) };

    let allow_private = lib.config.web_allow_private;
    let urls = req.images.clone();
    let downloads: Vec<Result<Vec<u8>, String>> = lib
        .run(move |_| {
            let client = Client::new(allow_private);
            Ok(urls.par_iter().map(|u| client.image(u).map_err(|e| e.to_string())).collect())
        })
        .await?;

    let result = lib
        .run(move |lib| {
            let meta = stl_core::metadata::Metadata {
                title: req.title,
                creator: req.creator,
                distributor: req.source,
                url: req.url,
                description: req.description,
                tags: req.tags,
                ..Default::default()
            };
            let changed = lib.db.with_tx(|tx| model_edit::links_commit(tx, id, &meta, true))?;

            let mut known: Vec<u128> = Vec::new();
            if req.skip_known {
                let have = lib.db.with(|c| model_edit::model_for_editor(c, id))?.map(|m| m.images).unwrap_or_default();
                let thumbs = lib.thumbs.get_many(&have.iter().map(|&(i, _)| i as u64).collect::<Vec<_>>())?;
                known = stl_core::dhash::dhash_many(&thumbs.iter().map(|(_, t)| t.as_slice()).collect::<Vec<_>>()).into_iter().flatten().collect();
            }
            let size = LibrarySettings::load(&lib.config.data_dir).max_thumb_size();
            let (mut thumbs, mut failed, mut skipped) = (Vec::new(), Vec::new(), 0);
            for (url, data) in req.images.iter().zip(downloads) {
                let data = match data {
                    Ok(d) => d,
                    Err(e) => {
                        failed.push(format!("{url}: {e}"));
                        continue;
                    }
                };
                if req.skip_known
                    && let Ok(hash) = stl_core::dhash::dhash(&data)
                {
                    if known.iter().any(|k| (k ^ hash).count_ones() <= SAME_IMAGE_BITS) {
                        skipped += 1;
                        continue;
                    }
                    known.push(hash);
                }
                match stl_core::thumbnail::make_thumbnail(&data, size, true) {
                    Ok(t) => thumbs.push((image_name(url), t)),
                    Err(e) => failed.push(format!("{url}: not an image ({e})")),
                }
            }
            let added = if thumbs.is_empty() { Vec::new() } else { store_thumbnails(lib, id, thumbs)? };
            Ok(WebImported { changed, added, skipped, failed })
        })
        .await?;
    if result.changed || !result.added.is_empty() {
        lib.schedule_sidecars([path]);
    }
    Ok(Json(result))
}
