//! The API against a small temporary library, through the real router.

use std::sync::Arc;

use axum::body::Body;
use axum::http::{Request, StatusCode, header};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use stl_server::{AppState, Config, Library, app};
use tower::ServiceExt;

struct Fixture {
    _dir: tempfile::TempDir,
    state: AppState,
    ids: Vec<i64>,
}

/// Three models: two by Alice tagged `dragon` (one with two thumbnails sharing
/// a payload), one by Bob in another folder.
fn fixture() -> Fixture {
    let dir = tempfile::tempdir().unwrap();
    let config = Config { data_dir: dir.path().to_path_buf(), bind: "127.0.0.1:0".parse().unwrap() };
    let lib = Library::open(config).unwrap();
    let ids = lib
        .db
        .with_tx(|tx| {
            tx.execute_batch(
                "INSERT INTO sculptors (name) VALUES ('Alice'), ('Bob');
                 INSERT INTO tags (name) VALUES ('dragon');",
            )?;
            let mut ids = Vec::new();
            for (title, creator, path, sculptor) in [
                ("Red Dragon", "Alice", "F:/lib/Alice/red.7z", 1),
                ("Blue Dragon", "Alice", "F:/lib/Alice/blue.zip", 1),
                ("Knight", "Bob", "F:/lib/Bob/knight.7z", 2),
            ] {
                tx.execute(
                    "INSERT INTO models (title, creator, archive_path, archive_folder, archive_hash, sculptor_id, \
                     is_missing, to_print, release_year, release_month) VALUES (?1, ?2, ?3, ?4, ?1, ?5, 0, 0, 2024, 3)",
                    rusqlite::params![title, creator, path, path.rsplit_once('/').unwrap().0, sculptor],
                )?;
                ids.push(tx.last_insert_rowid());
            }
            tx.execute_batch(&format!(
                "INSERT INTO model_tags (model_id, tag_id) VALUES ({0}, 1), ({1}, 1);
                 INSERT INTO images (id, model_id, filename) VALUES (10, {0}, 'a.jpg'), (11, {0}, 'b.jpg'), (12, {2}, 'c.jpg');",
                ids[0], ids[1], ids[2]
            ))?;
            Ok::<_, rusqlite::Error>(ids)
        })
        .unwrap();
    lib.thumbs.put_many(&[(10, b"\xff\xd8jpeg-a"), (11, b"\xff\xd8jpeg-a")]).unwrap();
    lib.thumbs.put_raw(&[(12u64.to_be_bytes().to_vec(), b"GIF89a-legacy".to_vec())]).unwrap();
    Fixture { _dir: dir, state: Arc::new(lib), ids }
}

async fn send(state: &AppState, req: Request<Body>) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    let resp = app(state.clone()).oneshot(req).await.unwrap();
    let (parts, body) = resp.into_parts();
    (parts.status, parts.headers, body.collect().await.unwrap().to_bytes().to_vec())
}

async fn get(state: &AppState, uri: &str) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    send(state, Request::get(uri).body(Body::empty()).unwrap()).await
}

async fn post(state: &AppState, uri: &str, body: Value) -> (StatusCode, Value) {
    let req = Request::post(uri)
        .header(header::CONTENT_TYPE, "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    let (status, _, bytes) = send(state, req).await;
    (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null))
}

fn json_of(bytes: &[u8]) -> Value {
    serde_json::from_slice(bytes).unwrap()
}

#[tokio::test]
async fn info_reports_version_and_size() {
    let f = fixture();
    let (status, _, body) = get(&f.state, "/api/system/info").await;
    assert_eq!(status, StatusCode::OK);
    let v = json_of(&body);
    assert_eq!(v["name"], "stlLibrary");
    assert_eq!(v["models"], 3);
}

#[tokio::test]
async fn ids_filter_search_and_sort() {
    let f = fixture();
    let (s, v) = post(&f.state, "/api/models/ids", json!({})).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["ids"], json!(f.ids));
    let (_, v) = post(&f.state, "/api/models/ids", json!({"sort": {"column": "title"}})).await;
    assert_eq!(v["ids"], json!([f.ids[1], f.ids[2], f.ids[0]]));
    let (_, v) = post(&f.state, "/api/models/ids", json!({"filter": {"include": {"tag": ["dragon"]}}, "search": "red"})).await;
    assert_eq!(v["ids"], json!([f.ids[0]]));
    let (_, v) = post(&f.state, "/api/models/ids", json!({"filter": {"include": {"sculptor": [2]}}})).await;
    assert_eq!(v["ids"], json!([f.ids[2]]));
    let (_, v) =
        post(&f.state, "/api/models/ids", json!({"filter": {"include": {"date": [{"year": 2024, "month": 3}]}}})).await;
    assert_eq!(v["ids"].as_array().unwrap().len(), 3);
    let (_, v) = post(&f.state, "/api/models/ids", json!({"filter": {"exclude": {"path": ["F:/lib/Alice"]}}})).await;
    assert_eq!(v["ids"], json!([f.ids[2]]));
}

#[tokio::test]
async fn bad_requests_are_refused() {
    let f = fixture();
    for body in [
        json!({"filter": {"include": {"nope": ["x"]}}}),
        json!({"filter": {"has_value": ["nope"]}}),
        json!({"sort": {"column": "tags"}}),
        json!({"unknown": 1}),
    ] {
        let (s, v) = post(&f.state, "/api/models/ids", body.clone()).await;
        assert!(s.is_client_error(), "{body} gave {s}");
        if s == StatusCode::BAD_REQUEST {
            assert!(v["error"].is_string());
        }
    }
    let (s, _) = post(&f.state, "/api/models/rows", json!({"ids": vec![1; 2001]})).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, _) = post(&f.state, "/api/filter-tree", json!({"show": ["nope"]})).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn rows_keep_the_requested_order() {
    let f = fixture();
    let (s, v) = post(&f.state, "/api/models/rows", json!({"ids": [f.ids[2], 999, f.ids[0]]})).await;
    assert_eq!(s, StatusCode::OK);
    let rows = v["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["title"], "Knight");
    assert_eq!(rows[0]["sculptor"], "Bob");
    assert_eq!(rows[1]["tags"], "dragon");
    assert_eq!(rows[1]["to_print"], false);
}

#[tokio::test]
async fn filter_tree_counts() {
    let f = fixture();
    let (s, v) = post(&f.state, "/api/filter-tree", json!({"show": ["author", "sculptor", "tags", "path", "date"]})).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["counts"]["tag"]["dragon"], 2);
    assert_eq!(v["counts"]["sculptor"], json!({"1": 2, "2": 1}));
    assert_eq!(v["counts"]["group"]["sculptor"], 3);
    assert_eq!(v["sculptors"], json!([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]));
    assert_eq!(v["path_counts"]["F:/lib"], 3);
    assert_eq!(v["dates"], json!([{"year": 2024, "month": 3}]));
    assert_eq!(v["scoped"], false);
    let (_, v) =
        post(&f.state, "/api/filter-tree", json!({"filter": {"include": {"tag": ["dragon"]}}, "show": ["sculptor"]})).await;
    assert_eq!(v["scoped"], true);
    assert_eq!(v["scoped_counts"]["sculptor"]["1"], 2);
}

#[tokio::test]
async fn model_detail_and_thumbnails() {
    let f = fixture();
    let (s, _, body) = get(&f.state, &format!("/api/models/{}", f.ids[0])).await;
    assert_eq!(s, StatusCode::OK);
    let v = json_of(&body);
    assert_eq!(v["fields"]["title"], "Red Dragon");
    assert_eq!(v["fields"]["image_count"], 2);
    let images = v["images"].as_array().unwrap();
    assert_eq!(images[0]["thumbnail"], images[1]["thumbnail"], "one payload, one address");
    let url = images[0]["thumbnail"].as_str().unwrap().to_string();
    assert!(url.starts_with("/api/thumb/"));

    let (s, h, body) = get(&f.state, &url).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(body, b"\xff\xd8jpeg-a");
    assert_eq!(h[header::CONTENT_TYPE], "image/jpeg");
    assert!(h[header::CACHE_CONTROL].to_str().unwrap().contains("immutable"));

    let (s, h, _) = get(&f.state, "/api/images/10/thumbnail").await;
    assert_eq!(s, StatusCode::PERMANENT_REDIRECT);
    assert_eq!(h[header::LOCATION], url.as_str());

    let (_, _, body) = get(&f.state, &format!("/api/models/{}", f.ids[2])).await;
    assert_eq!(json_of(&body)["images"][0]["thumbnail"], "/api/images/12/thumbnail", "legacy entry");
    let (s, h, body) = get(&f.state, "/api/images/12/thumbnail").await;
    assert_eq!((s, body.as_slice()), (StatusCode::OK, &b"GIF89a-legacy"[..]));
    assert_eq!(h[header::CONTENT_TYPE], "image/gif");

    for uri in ["/api/models/999", "/api/images/99/thumbnail", "/api/thumb/00", "/api/thumb/zz", "/api/images/-1/thumbnail"] {
        assert_eq!(get(&f.state, uri).await.0, StatusCode::NOT_FOUND, "{uri}");
    }
}

#[tokio::test]
async fn openapi_lists_every_route() {
    let f = fixture();
    let (s, _, body) = get(&f.state, "/api/openapi.json").await;
    assert_eq!(s, StatusCode::OK);
    let v = json_of(&body);
    let paths: Vec<&str> = v["paths"].as_object().unwrap().keys().map(String::as_str).collect();
    for p in ["/api/system/info", "/api/models/ids", "/api/models/rows", "/api/models/{id}", "/api/filter-tree",
              "/api/images/{id}/thumbnail", "/api/thumb/{digest}"] {
        assert!(paths.contains(&p), "{p} missing from {paths:?}");
    }
}

#[test]
fn a_second_server_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let config = Config { data_dir: dir.path().to_path_buf(), bind: "127.0.0.1:0".parse().unwrap() };
    let first = Library::open(config.clone()).unwrap();
    let err = Library::open(config.clone()).err().expect("second open must fail");
    assert!(err.contains("stl-server"), "{err}");
    drop(first);
    Library::open(config).unwrap();
}
