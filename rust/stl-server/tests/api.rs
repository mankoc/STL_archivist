//! The library API against a small temporary library, through the real router.

mod common;

use axum::http::{StatusCode, header};
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;
use stl_server::{Config, Library};

struct Fixture {
    lib: TestLib,
    ids: Vec<i64>,
    /// A viewer's session: reading needs no more.
    token: String,
}

/// Three models: two by Alice tagged `dragon` (one with two thumbnails sharing
/// a payload), one by Bob in another folder.
fn fixture() -> Fixture {
    let lib = empty_library();
    let ids = lib
        .state
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
    lib.state.thumbs.put_many(&[(10, b"\xff\xd8jpeg-a"), (11, b"\xff\xd8jpeg-a")]).unwrap();
    lib.state.thumbs.put_raw(&[(12u64.to_be_bytes().to_vec(), b"GIF89a-legacy".to_vec())]).unwrap();
    let token = lib.user("reader", Role::Viewer);
    Fixture { lib, ids, token }
}

impl Fixture {
    async fn get(&self, uri: &str) -> common::Reply {
        call(&self.lib.state, "GET", uri, Some(&self.token), None).await
    }

    async fn post(&self, uri: &str, body: Value) -> (StatusCode, Value) {
        let r = call(&self.lib.state, "POST", uri, Some(&self.token), Some(body)).await;
        (r.status, r.json())
    }
}

#[tokio::test]
async fn library_routes_need_a_session() {
    let f = fixture();
    for (method, uri, body) in [
        ("GET", "/api/system/info", None),
        ("POST", "/api/models/ids", Some(json!({}))),
        ("POST", "/api/models/rows", Some(json!({"ids": [1]}))),
        ("GET", "/api/models/1", None),
        ("POST", "/api/filter-tree", Some(json!({}))),
        ("GET", "/api/images/10/thumbnail", None),
        ("GET", "/api/thumb/00", None),
    ] {
        let r = call(&f.lib.state, method, uri, None, body.clone()).await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED, "{method} {uri}");
        let r = call(&f.lib.state, method, uri, Some("not-a-session"), body).await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED, "{method} {uri} with a bad token");
    }
}

#[tokio::test]
async fn info_reports_version_and_size() {
    let f = fixture();
    let r = f.get("/api/system/info").await;
    assert_eq!(r.status, StatusCode::OK);
    let v = r.json();
    assert_eq!(v["name"], "stlLibrary");
    assert_eq!(v["models"], 3);
}

#[tokio::test]
async fn ids_filter_search_and_sort() {
    let f = fixture();
    let (s, v) = f.post("/api/models/ids", json!({})).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["ids"], json!(f.ids));
    let (_, v) = f.post("/api/models/ids", json!({"sort": {"column": "title"}})).await;
    assert_eq!(v["ids"], json!([f.ids[1], f.ids[2], f.ids[0]]));
    let (_, v) = f.post("/api/models/ids", json!({"filter": {"include": {"tag": ["dragon"]}}, "search": "red"})).await;
    assert_eq!(v["ids"], json!([f.ids[0]]));
    let (_, v) = f.post("/api/models/ids", json!({"filter": {"include": {"sculptor": [2]}}})).await;
    assert_eq!(v["ids"], json!([f.ids[2]]));
    let (_, v) = f.post("/api/models/ids", json!({"filter": {"include": {"date": [{"year": 2024, "month": 3}]}}})).await;
    assert_eq!(v["ids"].as_array().unwrap().len(), 3);
    let (_, v) = f.post("/api/models/ids", json!({"filter": {"exclude": {"path": ["F:/lib/Alice"]}}})).await;
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
        let (s, v) = f.post("/api/models/ids", body.clone()).await;
        assert!(s.is_client_error(), "{body} gave {s}");
        if s == StatusCode::BAD_REQUEST {
            assert!(v["error"].is_string());
        }
    }
    let (s, _) = f.post("/api/models/rows", json!({"ids": vec![1; 2001]})).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
    let (s, _) = f.post("/api/filter-tree", json!({"show": ["nope"]})).await;
    assert_eq!(s, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn rows_keep_the_requested_order() {
    let f = fixture();
    let (s, v) = f.post("/api/models/rows", json!({"ids": [f.ids[2], 999, f.ids[0]]})).await;
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
    let (s, v) = f.post("/api/filter-tree", json!({"show": ["author", "sculptor", "tags", "path", "date"]})).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["counts"]["tag"]["dragon"], 2);
    assert_eq!(v["counts"]["sculptor"], json!({"1": 2, "2": 1}));
    assert_eq!(v["counts"]["group"]["sculptor"], 3);
    assert_eq!(v["sculptors"], json!([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]));
    assert_eq!(v["path_counts"]["F:/lib"], 3);
    assert_eq!(v["dates"], json!([{"year": 2024, "month": 3}]));
    assert_eq!(v["scoped"], false);
    let (_, v) = f.post("/api/filter-tree", json!({"filter": {"include": {"tag": ["dragon"]}}, "show": ["sculptor"]})).await;
    assert_eq!(v["scoped"], true);
    assert_eq!(v["scoped_counts"]["sculptor"]["1"], 2);
}

#[tokio::test]
async fn model_detail_and_thumbnails() {
    let f = fixture();
    let r = f.get(&format!("/api/models/{}", f.ids[0])).await;
    assert_eq!(r.status, StatusCode::OK);
    let v = r.json();
    assert_eq!(v["fields"]["title"], "Red Dragon");
    assert_eq!(v["fields"]["image_count"], 2);
    let images = v["images"].as_array().unwrap();
    assert_eq!(images[0]["thumbnail"], images[1]["thumbnail"], "one payload, one address");
    let url = images[0]["thumbnail"].as_str().unwrap().to_string();
    assert!(url.starts_with("/api/thumb/"));

    let r = f.get(&url).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.body, b"\xff\xd8jpeg-a");
    assert_eq!(r.headers[header::CONTENT_TYPE], "image/jpeg");
    let cache = r.headers[header::CACHE_CONTROL].to_str().unwrap();
    assert!(cache.contains("immutable") && cache.starts_with("private"), "{cache}");

    let r = f.get("/api/images/10/thumbnail").await;
    assert_eq!(r.status, StatusCode::PERMANENT_REDIRECT);
    assert_eq!(r.headers[header::LOCATION], url.as_str());

    let v = f.get(&format!("/api/models/{}", f.ids[2])).await.json();
    assert_eq!(v["images"][0]["thumbnail"], "/api/images/12/thumbnail", "legacy entry");
    let r = f.get("/api/images/12/thumbnail").await;
    assert_eq!((r.status, r.body.as_slice()), (StatusCode::OK, &b"GIF89a-legacy"[..]));
    assert_eq!(r.headers[header::CONTENT_TYPE], "image/gif");

    for uri in ["/api/models/999", "/api/images/99/thumbnail", "/api/thumb/00", "/api/thumb/zz", "/api/images/-1/thumbnail"] {
        assert_eq!(f.get(uri).await.status, StatusCode::NOT_FOUND, "{uri}");
    }
}

#[tokio::test]
async fn openapi_lists_every_route() {
    let f = fixture();
    let r = call(&f.lib.state, "GET", "/api/openapi.json", None, None).await;
    assert_eq!(r.status, StatusCode::OK, "the document is public");
    let v = r.json();
    let paths: Vec<&str> = v["paths"].as_object().unwrap().keys().map(String::as_str).collect();
    for p in [
        "/api/auth/status", "/api/auth/setup", "/api/auth/login", "/api/auth/logout", "/api/auth/password",
        "/api/users", "/api/users/{id}", "/api/me/settings", "/api/me/settings/{key}",
        "/api/system/info", "/api/models/ids", "/api/models/rows", "/api/models/{id}", "/api/filter-tree",
        "/api/images/{id}/thumbnail", "/api/thumb/{digest}",
        "/api/models/cells", "/api/models/{id}/editor", "/api/models/mass-edit", "/api/models/delete",
        "/api/models/{id}/images", "/api/images/{id}", "/api/editor/choices", "/api/editor/subcollections",
        "/api/lookups/{kind}", "/api/lookups/{kind}/merge", "/api/lookups/{kind}/{id}", "/api/tags/merge",
        "/api/tags/unused", "/api/authors", "/api/authors/profile",
        "/api/jobs", "/api/jobs/{id}", "/api/events", "/api/crawled-folders", "/api/crawled-folders/remove",
    ] {
        assert!(paths.contains(&p), "{p} missing from {paths:?}");
    }
}

#[test]
fn a_second_server_is_refused() {
    let dir = tempfile::tempdir().unwrap();
    let config = Config { data_dir: dir.path().to_path_buf(), bind: "127.0.0.1:0".parse().unwrap(), unrar: None, onnxruntime: None, pdfium: None, roots: vec![], path_map: vec![], web_dir: None };
    let first = Library::open(config.clone()).unwrap();
    let err = Library::open(config.clone()).err().expect("second open must fail");
    assert!(err.contains("stl-server"), "{err}");
    drop(first);
    Library::open(config).unwrap();
}

#[test]
fn web_client_types_are_current() {
    // web/ generates its TypeScript types from this document: after changing a
    // route or schema, run `npm run gen:api` in web/.
    let committed = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../web/openapi.json"))
        .expect("web/openapi.json")
        .replace("\r\n", "\n");
    assert!(committed == stl_server::api::openapi_json(), "web/openapi.json is stale: run `npm run gen:api` in web/");
}
