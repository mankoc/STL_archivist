//! Import from a web page: reading a page (a local site standing in for a
//! store), reading a page the desktop app captured, and importing into a model.

mod common;

use std::io::Write;
use std::time::Duration;

use axum::extract::Path as UrlPath;
use axum::http::{StatusCode, header};
use axum::response::{Html, IntoResponse, Redirect};
use axum::routing::get;
use common::{TestLib, call, empty_library, library_with};
use serde_json::{Value, json};
use stl_server::accounts::Role;

/// Two colours split left/right (`across`) or top/bottom: shapes dHash tells apart.
fn split_png(w: u32, h: u32, across: bool) -> Vec<u8> {
    let img = image::RgbImage::from_fn(w, h, |x, y| {
        let first = if across { x < w / 2 } else { y < h / 2 };
        image::Rgb(if first { [220, 30, 30] } else { [20, 40, 200] })
    });
    let mut out = std::io::Cursor::new(Vec::new());
    img.write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

const PAGE: &str = r#"<html><head><title>Ettercap Hunter | Test Store</title>
    <meta property="og:site_name" content="Test Store">
    <script type="application/ld+json">{"@type": "Product", "name": "Ettercap Hunter 3D print model",
      "brand": "Studio", "description": "A humanoid spider.", "keywords": "Spider, Monster",
      "image": ["/img/a.png", "/img/b.png"]}</script></head><body></body></html>"#;

/// The store: a model's page, a redirect to it, a bot check, a missing page and images.
async fn store() -> String {
    async fn image(UrlPath(name): UrlPath<String>) -> axum::response::Response {
        let png = |data: Vec<u8>| ([(header::CONTENT_TYPE, "image/png")], data).into_response();
        match name.as_str() {
            "a.png" => png(split_png(40, 30, true)),
            "a-large.png" => png(split_png(80, 60, true)),
            "b.png" => png(split_png(40, 30, false)),
            "text.png" => png(b"not an image".to_vec()),
            _ => StatusCode::NOT_FOUND.into_response(),
        }
    }
    let app = axum::Router::new()
        .route("/model", get(|| async { Html(PAGE) }))
        .route("/moved", get(|| async { Redirect::to("/model") }))
        .route("/check", get(|| async { (StatusCode::FORBIDDEN, Html("<html><head><title>Just a moment...</title></head></html>")) }))
        .route("/gone", get(|| async { StatusCode::NOT_FOUND }))
        .route("/img/{name}", get(image));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{addr}")
}

fn open_library() -> TestLib {
    library_with(|c| c.web_allow_private = true)
}

async fn post(lib: &TestLib, token: &str, uri: &str, body: Value) -> (StatusCode, Value) {
    let r = call(&lib.state, "POST", uri, Some(token), Some(body)).await;
    let v = if r.body.is_empty() { Value::Null } else { r.json() };
    (r.status, v)
}

#[tokio::test(flavor = "multi_thread")]
async fn reads_a_model_page() {
    let base = store().await;
    let lib = open_library();
    let ed = lib.user("ed", Role::Editor);
    let (status, v) = post(&lib, &ed, "/api/web/read", json!({"url": format!("{base}/moved")})).await;
    assert_eq!(status, StatusCode::OK, "{v}");
    let page = &v["page"];
    assert_eq!(page["url"], format!("{base}/model"), "the address after the redirect");
    assert_eq!((page["title"].as_str(), page["creator"].as_str()), (Some("Ettercap Hunter"), Some("Studio")));
    assert_eq!((page["source"].as_str(), page["description"].as_str()), (Some("Test Store"), Some("A humanoid spider.")));
    assert_eq!(page["tags"], json!(["spider", "monster"]));
    assert_eq!(page["images"], json!([format!("{base}/img/a.png"), format!("{base}/img/b.png")]));
    assert_eq!(v["blocked"], Value::Null);

    // A bot check: only a browser reads it.
    let (status, v) = post(&lib, &ed, "/api/web/read", json!({"url": format!("{base}/check")})).await;
    assert_eq!(status, StatusCode::OK, "{v}");
    assert_eq!(v["page"], Value::Null);
    assert!(v["blocked"].as_str().unwrap().contains("only a browser"), "{v}");

    assert_eq!(post(&lib, &ed, "/api/web/read", json!({"url": format!("{base}/gone")})).await.0, StatusCode::BAD_GATEWAY);
    assert_eq!(post(&lib, &ed, "/api/web/read", json!({"url": "ftp://example.org/x"})).await.0, StatusCode::BAD_REQUEST);
    assert_eq!(post(&lib, &ed, "/api/web/read", json!({"url": base, "other": 1})).await.0, StatusCode::UNPROCESSABLE_ENTITY);
    let viewer = lib.user("vi", Role::Viewer);
    assert_eq!(post(&lib, &viewer, "/api/web/read", json!({"url": format!("{base}/model")})).await.0, StatusCode::FORBIDDEN);

    // By default, nothing on this computer or its network is fetched.
    let closed = empty_library();
    let ed = closed.user("ed", Role::Editor);
    let (status, v) = post(&closed, &ed, "/api/web/read", json!({"url": format!("{base}/model")})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "{v}");
    assert!(v["error"].as_str().unwrap().contains("local network"), "{v}");
}

#[tokio::test(flavor = "multi_thread")]
async fn reads_a_page_the_desktop_app_captured() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let html = r#"<html><head><title>Dragon bust | Studio on Patreon</title></head>
        <body><a href="/c/studio/posts?filters%5Btag%5D=dragons">#Dragons</a></body></html>"#;
    let body = json!({"url": "https://www.patreon.com/posts/dragon-bust-1", "html": html, "images": ["https://c10.patreonusercontent.com/a.png"]});
    let (status, v) = post(&lib, &ed, "/api/web/read-captured", body).await;
    assert_eq!(status, StatusCode::OK, "{v}");
    assert_eq!((v["title"].as_str(), v["creator"].as_str(), v["source"].as_str()), (Some("Dragon bust"), Some("Studio"), Some("Patreon")));
    assert_eq!((v["tags"].clone(), v["images"].clone()), (json!(["dragons"]), json!(["https://c10.patreonusercontent.com/a.png"])));
    let (status, _) = post(&lib, &ed, "/api/web/read-captured", json!({"url": "file:///C:/x.html", "html": ""})).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
}

async fn finish_crawl(lib: &TestLib, token: &str, folder: &std::path::Path) {
    let (status, v) = post(lib, token, "/api/jobs", json!({"kind": "crawl", "folder": folder.to_string_lossy()})).await;
    assert_eq!(status, StatusCode::ACCEPTED, "{v}");
    let id = v["id"].as_u64().unwrap();
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        match v["job"]["state"].as_str() {
            Some("queued" | "running") => tokio::time::sleep(Duration::from_millis(100)).await,
            _ => return assert_eq!(v["job"]["state"], "done", "{v}"),
        }
    }
    panic!("the crawl did not finish");
}

fn scalar<T: rusqlite::types::FromSql>(lib: &TestLib, sql: &str) -> T {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn imports_fields_and_new_images() {
    let base = store().await;
    let lib = open_library();
    let ed = lib.user("ed", Role::Editor);
    // A model whose archive holds picture "a" (left/right).
    let root = lib.dir.path().join("archives");
    std::fs::create_dir_all(&root).unwrap();
    let mut z = zip::ZipWriter::new(std::fs::File::create(root.join("Ettercap.zip")).unwrap());
    let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (name, data) in [("ettercap.stl", b"solid e\nendsolid\n".to_vec()), ("a.png", split_png(40, 30, true))] {
        z.start_file(name, opts).unwrap();
        z.write_all(&data).unwrap();
    }
    z.finish().unwrap();
    finish_crawl(&lib, &ed, &root).await;
    let id: i64 = scalar(&lib, "SELECT id FROM models");
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM images"), 1);

    let img = |n: &str| format!("{base}/img/{n}");
    let body = json!({
        "title": "Ettercap Hunter", "creator": "Studio", "source": "", "url": format!("{base}/model"),
        "tags": ["spider"],
        "images": [img("a-large.png"), img("b.png"), img("b.png"), img("missing.png"), img("text.png")],
        "skip_known": true,
    });
    let (status, v) = post(&lib, &ed, &format!("/api/models/{id}/web-import"), body).await;
    assert_eq!(status, StatusCode::OK, "{v}");
    assert_eq!(v["changed"], true);
    assert_eq!(v["added"].as_array().unwrap().len(), 1, "only b is new: {v}");
    assert_eq!(v["skipped"], 2, "a as the archive's picture, b the second time: {v}");
    let failed = v["failed"].as_array().unwrap();
    assert!(failed[0].as_str().unwrap().starts_with(&img("missing.png")) && failed[1].as_str().unwrap().contains("not an image"), "{v}");
    let (title, creator, url): (String, String, String) =
        lib.state.db.with(|c| c.query_row("SELECT title, creator, url FROM models", [], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))).unwrap();
    assert_eq!((title.as_str(), creator.as_str(), url), ("Ettercap Hunter", "Studio", format!("{base}/model")));
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM model_tags"), 1);
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM images"), 2);

    // Nothing new: nothing changes.
    let (status, v) = post(&lib, &ed, &format!("/api/models/{id}/web-import"), json!({"title": "Ettercap Hunter", "images": [img("b.png")], "skip_known": true})).await;
    assert_eq!(status, StatusCode::OK, "{v}");
    assert_eq!((v["changed"].clone(), v["skipped"].clone()), (json!(false), json!(1)));

    assert_eq!(post(&lib, &ed, "/api/models/9999/web-import", json!({"title": "x"})).await.0, StatusCode::NOT_FOUND);
    assert_eq!(post(&lib, &ed, &format!("/api/models/{id}/web-import"), json!({"images": ["file:///C:/a.png"]})).await.0, StatusCode::BAD_REQUEST);
    assert_eq!(post(&lib, &ed, &format!("/api/models/{id}/web-import"), json!({"tilte": "x"})).await.0, StatusCode::UNPROCESSABLE_ENTITY);
    let viewer = lib.user("vi", Role::Viewer);
    assert_eq!(post(&lib, &viewer, &format!("/api/models/{id}/web-import"), json!({"title": "x"})).await.0, StatusCode::FORBIDDEN);
}
