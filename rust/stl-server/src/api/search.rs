//! Find by Image: models whose thumbnails look like an uploaded image, by
//! perceptual hash (Hamming distance) or AI embedding (cosine similarity).
//! As in the desktop app, the closest images are considered (at most
//! [`MAX_RESULT_IMAGES`]) and the best image per model is kept.

use std::collections::HashMap;

use axum::Json;
use axum::extract::{Multipart, Query, State};
use serde::{Deserialize, Serialize};
use stl_core::storage::{vectors, views};
use utoipa::ToSchema;

use super::thumbs::thumbnail_url;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

/// Closest images considered (`image_similarity.MAX_RESULT_IMAGES`).
pub const MAX_RESULT_IMAGES: usize = 1024;

#[derive(Deserialize, utoipa::IntoParams)]
pub struct SearchQuery {
    /// `dhash` (default) or `clip`.
    pub method: Option<String>,
    /// dHash: most differing bits (0 = identical; default 10).
    pub max_distance: Option<u32>,
    /// CLIP: least cosine similarity (default 0.85).
    pub threshold: Option<f64>,
}

#[derive(Serialize, ToSchema)]
pub struct Match {
    pub model_id: i64,
    pub title: String,
    pub archive: String,
    pub thumbnail: Option<String>,
    /// Bits differing (dHash) or cosine similarity (CLIP).
    pub score: f64,
}

#[derive(Serialize, ToSchema)]
pub struct SearchResult {
    pub method: String,
    /// Best first.
    pub matches: Vec<Match>,
    /// All of the closest images matched: more may exist (tighten the threshold).
    pub capped: bool,
    /// dHash: stored hashes in an older format, not compared (run Recompute image hashes).
    pub outdated: usize,
}

fn parse_dhash(s: &str) -> Option<u128> {
    s.strip_prefix("2:").filter(|h| h.len() == 32).and_then(|h| u128::from_str_radix(h, 16).ok())
}

/// Search with an image (multipart, one file).
#[utoipa::path(post, path = "/api/search/image", tag = "search", params(SearchQuery),
    request_body(content_type = "multipart/form-data", description = "The image (64 MB at most)"),
    responses((status = 200, body = SearchResult), (status = 400, body = crate::error::ErrorBody),
              (status = 413, body = crate::error::ErrorBody)))]
pub async fn by_image(
    State(lib): State<AppState>,
    _me: CurrentUser,
    Query(q): Query<SearchQuery>,
    mut form: Multipart,
) -> Result<Json<SearchResult>, ApiError> {
    let mut image = None;
    while let Some(field) = form.next_field().await? {
        image = Some(field.bytes().await?.to_vec());
    }
    let image = image.ok_or_else(|| ApiError::bad_request("no image uploaded"))?;
    let method = q.method.clone().unwrap_or_else(|| "dhash".into());
    let result = match method.as_str() {
        "dhash" => {
            let max = q.max_distance.unwrap_or(10).min(128);
            lib.run(move |lib| {
                let query = stl_core::dhash::dhash(&image).map_err(|e| ApiError::bad_request(format!("not a readable image: {e}")))?;
                let rows = lib.db.with(views::image_hash_rows)?;
                let mut outdated = 0;
                let mut hits: Vec<(u32, usize)> = Vec::new();
                for (i, (_, hash, ..)) in rows.iter().enumerate() {
                    match parse_dhash(hash) {
                        Some(h) => {
                            let d = (h ^ query).count_ones();
                            if d <= max {
                                hits.push((d, i));
                            }
                        }
                        None => outdated += 1,
                    }
                }
                let capped = hits.len() > MAX_RESULT_IMAGES;
                hits.sort_by_key(|&(d, i)| (d, i));
                hits.truncate(MAX_RESULT_IMAGES);
                let mut best: Vec<(u32, usize)> = Vec::new();
                let mut seen = std::collections::HashSet::new();
                for (d, i) in hits {
                    if seen.insert(rows[i].0) {
                        best.push((d, i));
                    }
                }
                let picked: Vec<(f64, i64, String, String, Option<String>)> = best
                    .into_iter()
                    .map(|(d, i)| {
                        let (model_id, _, thumb, title, path) = &rows[i];
                        let archive = path.as_deref().map(|p| std::path::Path::new(p).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()).unwrap_or_default();
                        (f64::from(d), *model_id, title.clone().unwrap_or_default(), archive, thumb.clone())
                    })
                    .collect();
                Ok(SearchResult { method: "dhash".into(), matches: with_thumbnails(lib, picked)?, capped, outdated })
            })
            .await?
        }
        "clip" => {
            let threshold = q.threshold.unwrap_or(0.85);
            lib.run(move |lib| {
                let vectors = lib.embed(&[image]).map_err(ApiError::internal)?.ok_or_else(|| {
                    ApiError::bad_request(match lib.config.compute {
                        crate::config::Compute::Workers => "AI embeddings run on a compute worker, and none is connected",
                        crate::config::Compute::Local => "AI embeddings are unavailable on this server",
                    })
                })?;
                let vector = vectors.into_iter().next().flatten().ok_or_else(|| ApiError::bad_request("not a readable image"))?;
                let (found, capped) = lib.db.with(|c| vectors::find_similar_models(c, &vector, threshold, MAX_RESULT_IMAGES))?;
                Ok(SearchResult { method: "clip".into(), matches: with_thumbnails(lib, found)?, capped, outdated: 0 })
            })
            .await?
        }
        other => return Err(ApiError::bad_request(format!("unknown method {other:?} (dhash or clip)"))),
    };
    Ok(Json(result))
}

/// `(score, model id, title, archive name, thumbnail path)` with thumbnail URLs
/// (the thumbnail path is the image id).
fn with_thumbnails(lib: &crate::Library, rows: Vec<(f64, i64, String, String, Option<String>)>) -> Result<Vec<Match>, ApiError> {
    let ids: Vec<u64> = rows.iter().filter_map(|r| r.4.as_deref().and_then(|t| t.parse().ok())).collect();
    let digests: HashMap<u64, Option<Vec<u8>>> = lib.thumbs.digests(&ids)?.into_iter().collect();
    Ok(rows
        .into_iter()
        .map(|(score, model_id, title, archive, thumb)| {
            let image_id: Option<u64> = thumb.as_deref().and_then(|t| t.parse().ok());
            let thumbnail = image_id.and_then(|i| digests.get(&i).map(|d| thumbnail_url(i as i64, d.as_deref())));
            Match { model_id, title, archive, thumbnail, score }
        })
        .collect())
}
