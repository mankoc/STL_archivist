//! Thumbnail views: the image grid and the archive browser.

mod common;

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;

fn png() -> Vec<u8> {
    let img = image::RgbImage::from_pixel(40, 30, image::Rgb([200, 0, 0]));
    let mut out = std::io::Cursor::new(Vec::new());
    img.write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

fn write_zip(path: &Path, members: &[(&str, &[u8])]) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let mut z = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
    let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (name, data) in members {
        z.start_file(*name, opts).unwrap();
        z.write_all(data).unwrap();
    }
    z.finish().unwrap();
}

async fn crawl(lib: &TestLib, token: &str, folder: &Path) {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(json!({"kind": "crawl", "folder": folder.to_string_lossy()}))).await;
    let id = r.json()["id"].as_u64().unwrap();
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if v["job"]["state"] == "done" {
            return;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("crawl did not finish");
}

fn titles(v: &Value) -> Vec<String> {
    v.as_array().unwrap().iter().map(|c| c["title"].as_str().unwrap_or("").to_string()).collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn grid_and_folders() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Viewer);
    let admin = lib.user("admin", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("A").join("Dragon.zip"), &[("d.stl", b"solid\nendsolid\n"), ("r.png", &png())]);
    write_zip(&root.join("A").join("Sub").join("Knight.zip"), &[("k.stl", b"solid\nendsolid\n")]);
    crawl(&lib, &admin, &root).await;
    let id = |t: &str| -> i64 { lib.state.db.with(|c| c.query_row("SELECT id FROM models WHERE title = ?1", [t], |r| r.get(0))).unwrap() };
    let (dragon, knight) = (id("Dragon"), id("Knight"));

    let g = call(&lib.state, "POST", "/api/views/grid", Some(&ed), Some(json!({"ids": [knight, dragon, 999]}))).await.json();
    assert_eq!(titles(&g), ["Knight", "Dragon"], "asked order, unknown ids left out");
    assert_eq!(g[1]["images"], 1);
    assert!(g[1]["thumbnail"].as_str().unwrap().starts_with("/api/thumb/"));
    assert_eq!(g[0]["thumbnail"], Value::Null);
    let many: Vec<i64> = (0..501).collect();
    let r = call(&lib.state, "POST", "/api/views/grid", Some(&ed), Some(json!({"ids": many}))).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);

    let folders = call(&lib.state, "GET", "/api/folders", Some(&ed), None).await.json();
    let folders: Vec<String> = folders.as_array().unwrap().iter().map(|f| f.as_str().unwrap().to_string()).collect();
    assert_eq!(folders.len(), 2, "{folders:?}");
    let a = folders.iter().find(|f| f.ends_with("/A")).unwrap().clone();
    let only = call(&lib.state, "POST", "/api/views/folder", Some(&ed), Some(json!({"folder": a}))).await.json();
    assert_eq!(titles(&only), ["Dragon"]);
    let all = call(&lib.state, "POST", "/api/views/folder", Some(&ed), Some(json!({"folder": a, "include_subfolders": true}))).await.json();
    assert_eq!(titles(&all), ["Dragon", "Knight"]);
}

#[tokio::test(flavor = "multi_thread")]
async fn original_images() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    let render = png();
    write_zip(&root.join("A").join("Dragon.zip"), &[("d.stl", b"solid\nendsolid\n"), ("renders/big.png", &render)]);
    crawl(&lib, &ed, &root).await;
    let image: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM images", [], |r| r.get(0))).unwrap();

    let r = call(&lib.state, "GET", &format!("/api/images/{image}/original"), Some(&ed), None).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.headers["content-type"], "image/png");
    assert_eq!(r.body, render, "the member's original bytes");

    // An image whose source is gone (here: no file name, as for an image added by hand).
    lib.state.db.with(|c| c.execute("UPDATE images SET filename = NULL", [])).unwrap();
    let r = call(&lib.state, "GET", &format!("/api/images/{image}/original"), Some(&ed), None).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    assert_eq!(call(&lib.state, "GET", "/api/images/999/original", Some(&ed), None).await.status, StatusCode::NOT_FOUND);
}
