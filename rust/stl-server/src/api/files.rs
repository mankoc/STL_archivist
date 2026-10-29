//! The server's files: browsing folders to crawl, downloading archives, and
//! how a client sees an archive's path ("open folder" only where it can).
//!
//! Editors browse and crawl within the library's roots (`roots` in
//! `server.json` and the crawled folders); administrators anywhere. Paths are
//! compared after resolving `..` and links, so a root cannot be escaped.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};

use axum::Json;
use axum::body::Body;
use axum::extract::{Path as UrlPath, Query, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde::{Deserialize, Serialize};
use stl_core::storage::{duplicates, model_edit};
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::{self, CurrentUser};
use crate::config::PathMap;
use crate::{ApiError, AppState, Library};

/// Most entries one listing returns.
const MAX_ENTRIES: usize = 5000;
const ARCHIVES: &[&str] = &["zip", "7z", "rar"];
const SIDECAR: &str = "METADATA.7z";

/// Canonical form for comparisons: resolved, forward slashes, no trailing
/// slash (case-folded on Windows, whose paths are case-insensitive).
fn canonical(p: &Path) -> Option<String> {
    let c = std::fs::canonicalize(p).ok()?;
    let s = c.to_string_lossy().replace('\\', "/");
    let s = s.strip_prefix("//?/").unwrap_or(&s).trim_end_matches('/').to_string();
    Some(if cfg!(windows) { s.to_lowercase() } else { s })
}

fn within(path: &str, root: &str) -> bool {
    path == root || path.starts_with(&format!("{root}/")) || root.is_empty()
}

/// The library's roots: configured ones and the crawled folders.
fn roots(lib: &Library) -> Result<Vec<String>, ApiError> {
    let mut out = lib.config.roots.clone();
    out.extend(lib.db.with(duplicates::crawled_folders)?.into_iter().map(|(p, _)| p));
    out.sort();
    out.dedup();
    Ok(out)
}

/// The spelling under which the library knows the folder `path`: the crawled
/// folder that is (or contains) the same directory, with the rest appended in
/// its separator style. Archive paths are compared as strings, so a crawl of
/// `C:/STL` must not duplicate every record of `C:\STL`. `None` when no crawled
/// folder covers it.
pub fn known_spelling(lib: &Library, path: &Path) -> Result<Option<String>, ApiError> {
    let Ok(real) = std::fs::canonicalize(path) else { return Ok(None) };
    let mut best: Option<(usize, String)> = None;
    for (stored, _) in lib.db.with(duplicates::crawled_folders)? {
        let Ok(root) = std::fs::canonicalize(&stored) else { continue };
        let Ok(rest) = real.strip_prefix(&root) else { continue };
        let depth = root.components().count();
        if best.as_ref().is_some_and(|(d, _)| *d >= depth) {
            continue;
        }
        let sep = if stored.contains('\\') { "\\" } else { "/" };
        let mut spelled = stored.trim_end_matches(['/', '\\']).to_string();
        for part in rest.components() {
            spelled.push_str(sep);
            spelled.push_str(&part.as_os_str().to_string_lossy());
        }
        best = Some((depth, if rest.as_os_str().is_empty() { stored } else { spelled }));
    }
    Ok(best.map(|(_, s)| s))
}

/// 403 unless `me` may read or crawl `path` (it must exist).
pub fn check_access(lib: &Library, me: &CurrentUser, path: &Path) -> Result<(), ApiError> {
    let target = canonical(path).ok_or_else(|| ApiError::not_found(format!("{} does not exist", path.display())))?;
    if me.user.role >= Role::Admin {
        return Ok(());
    }
    let ok = roots(lib)?.iter().filter_map(|r| canonical(Path::new(r))).any(|r| within(&target, &r));
    if ok { Ok(()) } else { Err(ApiError::new(StatusCode::FORBIDDEN, "outside the library's folders (ask an administrator to add a root)")) }
}

#[derive(Deserialize, utoipa::IntoParams)]
pub struct BrowseQuery {
    /// The folder to list; empty lists the starting points.
    #[serde(default)]
    pub path: String,
}

#[derive(Serialize, ToSchema)]
pub struct Entry {
    pub name: String,
    pub path: String,
    /// `folder` or `archive`.
    pub kind: &'static str,
    /// Archives only.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<u64>,
}

#[derive(Serialize, ToSchema)]
pub struct Listing {
    /// The listed folder (empty for the starting points).
    pub path: String,
    /// Its parent, when the user may go there.
    pub parent: Option<String>,
    /// Folders first, then archives, each by name.
    pub entries: Vec<Entry>,
    /// More entries exist than were returned.
    pub truncated: bool,
}

fn starting_points(lib: &Library, me: &CurrentUser) -> Result<Vec<Entry>, ApiError> {
    let mut paths = roots(lib)?;
    if me.user.role >= Role::Admin {
        if cfg!(windows) {
            paths.extend((b'A'..=b'Z').map(|d| format!("{}:/", d as char)).filter(|d| Path::new(d).is_dir()));
        } else {
            paths.push("/".into());
        }
    }
    Ok(paths
        .into_iter()
        .filter(|p| Path::new(p).is_dir())
        .map(|p| Entry { name: p.clone(), path: p, kind: "folder", size: None })
        .collect())
}

/// List a folder on the server (editor role): subfolders and archives.
#[utoipa::path(get, path = "/api/files/browse", tag = "files", params(BrowseQuery),
    responses((status = 200, body = Listing), (status = 403, body = crate::error::ErrorBody),
              (status = 404, body = crate::error::ErrorBody)))]
pub async fn browse(State(lib): State<AppState>, me: CurrentUser, Query(q): Query<BrowseQuery>) -> Result<Json<Listing>, ApiError> {
    me.require(Role::Editor)?;
    let listing = lib
        .run(move |lib| {
            if q.path.trim().is_empty() {
                return Ok(Listing { path: String::new(), parent: None, entries: starting_points(lib, &me)?, truncated: false });
            }
            let dir = PathBuf::from(q.path.trim());
            check_access(lib, &me, &dir)?;
            let read = std::fs::read_dir(&dir).map_err(|e| ApiError::bad_request(format!("{}: {e}", dir.display())))?;
            let mut folders = Vec::new();
            let mut archives = Vec::new();
            for e in read.flatten() {
                let name = e.file_name().to_string_lossy().into_owned();
                let path = e.path().to_string_lossy().into_owned();
                let Ok(meta) = e.metadata() else { continue };
                if meta.is_dir() {
                    folders.push(Entry { name, path, kind: "folder", size: None });
                } else if Path::new(&name)
                    .extension()
                    .is_some_and(|x| ARCHIVES.contains(&x.to_string_lossy().to_lowercase().as_str()))
                    // The folder's sidecar is not a model archive (the crawler skips it too).
                    && !name.eq_ignore_ascii_case(SIDECAR)
                {
                    archives.push(Entry { name, path, kind: "archive", size: Some(meta.len()) });
                }
            }
            let key = |e: &Entry| e.name.to_lowercase();
            folders.sort_by_key(key);
            archives.sort_by_key(key);
            let mut entries = folders;
            entries.extend(archives);
            let truncated = entries.len() > MAX_ENTRIES;
            entries.truncate(MAX_ENTRIES);
            let parent = dir
                .parent()
                .filter(|p| !p.as_os_str().is_empty() && check_access(lib, &me, p).is_ok())
                .map(|p| p.to_string_lossy().into_owned());
            Ok(Listing { path: dir.to_string_lossy().into_owned(), parent, entries, truncated })
        })
        .await?;
    Ok(Json(listing))
}

/// Download a model's archive file.
#[utoipa::path(get, path = "/api/models/{id}/archive", tag = "files",
    params(("id" = i64, Path, description = "Model id")),
    responses((status = 200, description = "The archive file", content_type = "application/octet-stream"),
              (status = 404, body = crate::error::ErrorBody)))]
pub async fn download(State(lib): State<AppState>, _me: CurrentUser, UrlPath(id): UrlPath<i64>) -> Result<Response, ApiError> {
    let path = lib.run(move |lib| Ok(lib.db.with(|c| model_edit::archive_paths(c, &[id]))?.remove(&id))).await?;
    let path = stl_core::relpath::on_disk(&path.ok_or_else(|| ApiError::not_found(format!("no model {id}")))?);
    let file = tokio::fs::File::open(&path).await.map_err(|_| ApiError::not_found("the archive is not on disk"))?;
    let len = file.metadata().await.map(|m| m.len()).ok();
    let name = Path::new(&path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "archive".into());
    // RFC 6266: an ASCII fallback plus the UTF-8 name.
    let ascii: String = name.chars().map(|c| if c.is_ascii() && c != '"' && c != '\\' { c } else { '_' }).collect();
    let utf8: String = name.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-._~".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect();
    let disposition = format!("attachment; filename=\"{ascii}\"; filename*=UTF-8''{utf8}");
    let mut resp = Body::from_stream(tokio_util::io::ReaderStream::new(file)).into_response();
    let h = resp.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("application/octet-stream"));
    if let Ok(v) = HeaderValue::from_str(&disposition) {
        h.insert(header::CONTENT_DISPOSITION, v);
    }
    if let Some(len) = len {
        h.insert(header::CONTENT_LENGTH, HeaderValue::from(len));
    }
    Ok(resp)
}

/// The path a client sees for a server path: the same path for a client on
/// the server's machine, a mapped one where `path_map` covers it, else none.
pub fn client_path(path: &str, peer: &SocketAddr, headers: &HeaderMap, map: &[PathMap]) -> Option<String> {
    if peer.ip().is_loopback() && !auth::proxied(headers) {
        return Some(path.to_string());
    }
    let norm = |s: &str| s.replace('\\', "/").trim_end_matches('/').to_string();
    let p = norm(path);
    map.iter().find_map(|m| {
        let prefix = norm(&m.server);
        let rest = if cfg!(windows) || prefix.contains(':') {
            p.get(..prefix.len()).filter(|h| h.eq_ignore_ascii_case(&prefix)).map(|_| &p[prefix.len()..])
        } else {
            p.strip_prefix(&prefix)
        }?;
        if !(rest.is_empty() || rest.starts_with('/')) {
            return None;
        }
        let client = m.client.trim_end_matches(['/', '\\']);
        let sep = if client.contains('\\') { "\\" } else { "/" };
        Some(format!("{client}{}", rest.replace('/', sep)))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn client_paths() {
        let local: SocketAddr = "127.0.0.1:1".parse().unwrap();
        let remote: SocketAddr = "192.0.2.1:1".parse().unwrap();
        let map = vec![PathMap { server: "D:/STL/".into(), client: r"\\nas\stl".into() }];
        let h = HeaderMap::new();
        assert_eq!(client_path(r"D:\STL\a\b.zip", &local, &h, &map).as_deref(), Some(r"D:\STL\a\b.zip"));
        assert_eq!(client_path(r"D:\STL\a\b.zip", &remote, &h, &map).as_deref(), Some(r"\\nas\stl\a\b.zip"));
        assert_eq!(client_path(r"d:/stl/x.7z", &remote, &h, &map).as_deref(), Some(r"\\nas\stl\x.7z"));
        assert_eq!(client_path(r"D:\STLX\x.7z", &remote, &h, &map), None, "a prefix must end at a separator");
        assert_eq!(client_path(r"E:\x.7z", &remote, &h, &map), None);
        let mut proxied = HeaderMap::new();
        proxied.insert("x-forwarded-for", "192.0.2.1".parse().unwrap());
        assert_eq!(client_path(r"E:\x.7z", &local, &proxied, &map), None, "a proxied client is not local");
    }

    #[test]
    fn within_roots() {
        assert!(within("d:/stl/a", "d:/stl"));
        assert!(within("d:/stl", "d:/stl"));
        assert!(!within("d:/stlx", "d:/stl"));
    }
}
