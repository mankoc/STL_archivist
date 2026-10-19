//! Thumbnails. The store is content-addressed, so `/api/thumb/<digest>` never
//! changes and is served as immutable; `/api/images/<id>/thumbnail` redirects
//! to it (or serves a legacy entry, which has no digest yet, uncached).

use axum::extract::{Path, State};
use axum::http::{StatusCode, header};
use axum::response::{IntoResponse, Redirect, Response};

use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

// Private: thumbnails need a session, so shared caches must not keep them.
const IMMUTABLE: &str = "private, max-age=31536000, immutable";

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn unhex(s: &str) -> Option<Vec<u8>> {
    if !s.len().is_multiple_of(2) || !s.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok()).collect()
}

/// Where an image's thumbnail is served: its content address when it has one.
pub fn thumbnail_url(image_id: i64, digest: Option<&[u8]>) -> String {
    match digest {
        Some(d) => format!("/api/thumb/{}", hex(d)),
        None => format!("/api/images/{image_id}/thumbnail"),
    }
}

fn content_type(data: &[u8]) -> &'static str {
    if data.starts_with(b"GIF8") {
        "image/gif"
    } else if data.starts_with(b"\x89PNG") {
        "image/png"
    } else if data.starts_with(&[0xff, 0xd8]) {
        "image/jpeg"
    } else {
        "application/octet-stream"
    }
}

/// A thumbnail by content address.
#[utoipa::path(get, path = "/api/thumb/{digest}", tag = "thumbnails",
    params(("digest" = String, Path, description = "Hex digest from an image's `thumbnail` URL")),
    responses((status = 200, description = "JPEG or GIF bytes", content_type = "image/jpeg"),
              (status = 404, body = crate::error::ErrorBody)))]
pub async fn thumb(State(lib): State<AppState>, _me: CurrentUser, Path(digest): Path<String>) -> Result<Response, ApiError> {
    let key = unhex(&digest).ok_or_else(|| ApiError::not_found("no such thumbnail"))?;
    let data = lib.run(move |lib| Ok(lib.thumbs.blob(&key)?)).await?;
    let data = data.ok_or_else(|| ApiError::not_found("no such thumbnail"))?;
    let ct = content_type(&data);
    Ok(([(header::CONTENT_TYPE, ct), (header::CACHE_CONTROL, IMMUTABLE)], data).into_response())
}

/// An image's thumbnail: a redirect to its content address.
#[utoipa::path(get, path = "/api/images/{id}/thumbnail", tag = "thumbnails",
    params(("id" = i64, Path, description = "Image id")),
    responses((status = 308, description = "Redirect to `/api/thumb/<digest>`"),
              (status = 200, description = "Bytes of a legacy entry", content_type = "image/jpeg"),
              (status = 404, body = crate::error::ErrorBody)))]
pub async fn image_thumbnail(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>) -> Result<Response, ApiError> {
    if id < 0 {
        return Err(ApiError::not_found("no such thumbnail"));
    }
    let found = lib
        .run(move |lib| {
            Ok(match lib.thumbs.digests(&[id as u64])?.pop() {
                Some((_, Some(d))) => Some(Ok(d)),
                Some((_, None)) => lib.thumbs.get_many(&[id as u64])?.pop().map(|(_, b)| Err(b)),
                None => None,
            })
        })
        .await?;
    match found {
        Some(Ok(digest)) => Ok(Redirect::permanent(&thumbnail_url(id, Some(&digest))).into_response()),
        Some(Err(bytes)) => {
            let ct = content_type(&bytes);
            Ok((StatusCode::OK, [(header::CONTENT_TYPE, ct), (header::CACHE_CONTROL, "no-cache")], bytes).into_response())
        }
        None => Err(ApiError::not_found("no such thumbnail")),
    }
}

/// The media type of an original image, by its name; `None` for what is not
/// an image (PDF previews have no original to show).
fn image_type(name: &str) -> Option<&'static str> {
    let ext = name.rsplit_once('.')?.1.to_ascii_lowercase();
    Some(match ext.as_str() {
        "jpg" | "jpeg" => "image/jpeg",
        "png" => "image/png",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "avif" => "image/avif",
        "tif" | "tiff" => "image/tiff",
        _ => return None,
    })
}

/// An image at full size: the original the thumbnail was made from, read from
/// the archive (or from the file beside it). 404 when it cannot be had (an
/// image added by hand, a moved archive): show the thumbnail instead.
#[utoipa::path(get, path = "/api/images/{id}/original", tag = "thumbnails",
    params(("id" = i64, Path, description = "Image id")),
    responses((status = 200, description = "The image file", content_type = "image/jpeg"),
              (status = 404, body = crate::error::ErrorBody)))]
pub async fn image_original(State(lib): State<AppState>, _me: CurrentUser, Path(id): Path<i64>) -> Result<Response, ApiError> {
    let found = lib
        .run(move |lib| {
            let Some((archive, Some(name))) = lib.db.with(|c| stl_core::storage::views::image_source(c, id))? else {
                return Ok(None);
            };
            let Some(ct) = image_type(&name) else { return Ok(None) };
            let file = std::path::Path::new(&name);
            let data = if file.is_absolute() {
                std::fs::read(file).ok()
            } else {
                archive.and_then(|a| {
                    stl_core::archive::read_members_with(std::path::Path::new(&a), std::slice::from_ref(&name), &lib.config.unrar_tool())
                        .ok()
                        .and_then(|mut v| v.pop())
                        .map(|(_, b)| b)
                })
            };
            Ok::<_, ApiError>(data.map(|d| (ct, d)))
        })
        .await?;
    let (ct, data) = found.ok_or_else(|| ApiError::not_found("the original image is not available"))?;
    Ok(([(header::CONTENT_TYPE, ct), (header::CACHE_CONTROL, "private, max-age=3600")], data).into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_round_trip_and_types() {
        assert_eq!(unhex(&hex(&[0, 1, 0xab, 0xff])), Some(vec![0, 1, 0xab, 0xff]));
        assert_eq!(unhex("abc"), None);
        assert_eq!(unhex("zz"), None);
        assert_eq!(content_type(b"GIF89a.."), "image/gif");
        assert_eq!(content_type(&[0xff, 0xd8, 0xff]), "image/jpeg");
        assert_eq!(thumbnail_url(5, None), "/api/images/5/thumbnail");
        assert_eq!(thumbnail_url(5, Some(&[0xab])), "/api/thumb/ab");
        assert_eq!(image_type("renders/A.JPG"), Some("image/jpeg"));
        assert_eq!(image_type("preview.pdf"), None);
        assert_eq!(image_type("noext"), None);
    }
}
