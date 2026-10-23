//! Libraries: the API over them, moving one to a new root without a re-crawl,
//! and opening a library from before libraries (backed up, then migrated).

mod common;

use std::io::Write;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;
use stl_server::Library;

fn png() -> Vec<u8> {
    let img = image::RgbImage::from_pixel(40, 30, image::Rgb([200, 0, 0]));
    let mut out = std::io::Cursor::new(Vec::new());
    img.write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

fn write_zip(path: &Path, member: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let mut z = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
    let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    z.start_file(member, opts).unwrap();
    z.write_all(b"solid x\nendsolid\n").unwrap();
    z.start_file("render.png", opts).unwrap();
    z.write_all(&png()).unwrap();
    z.finish().unwrap();
}

async fn wait(lib: &TestLib, token: &str, id: u64) -> Value {
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if !matches!(v["job"]["state"].as_str(), Some("queued" | "running")) {
            assert_eq!(v["job"]["state"], "done", "{v}");
            return v;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("job {id} did not finish");
}

async fn run(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    wait(lib, token, r.json()["id"].as_u64().unwrap()).await
}

fn scalar<T: rusqlite::types::FromSql>(lib: &TestLib, sql: &str) -> T {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

fn copy_dir(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for e in std::fs::read_dir(from).unwrap().flatten() {
        let dest = to.join(e.file_name());
        if e.file_type().unwrap().is_dir() {
            copy_dir(&e.path(), &dest);
        } else {
            std::fs::copy(e.path(), dest).unwrap();
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn libraries_are_listed_named_nested_and_removed() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let viewer = lib.user("vi", Role::Viewer);
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("STL");
    write_zip(&root.join("Gambody").join("Dragon.zip"), "dragon.stl");
    write_zip(&root.join("Other").join("Knight.zip"), "knight.stl");
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;

    let libs = call(&lib.state, "GET", "/api/libraries", Some(&viewer), None).await.json();
    let stl = &libs[0];
    assert_eq!((stl["name"].as_str(), stl["models"].as_i64(), stl["reachable"].as_bool()), (Some("STL"), Some(2), Some(true)), "{libs}");
    assert_eq!(stl["pattern"], "{sculptor}/{title}");
    assert_eq!(stl["uuid"].as_str().unwrap().len(), 36);
    let outer = stl["id"].as_i64().unwrap();

    // Adding a folder inside it makes an inner library that takes its records.
    let body = json!({"path": root.join("Gambody").to_string_lossy(), "name": "Gambody minis"});
    assert_eq!(call(&lib.state, "POST", "/api/libraries", Some(&viewer), Some(body.clone())).await.status, StatusCode::FORBIDDEN);
    let r = call(&lib.state, "POST", "/api/libraries", Some(&ed), Some(body.clone())).await;
    assert_eq!(r.status, StatusCode::CREATED, "{:?}", r.json());
    let inner = r.json()["id"].as_i64().unwrap();
    assert_eq!(r.json()["models"], 1, "the Dragon moved in");
    assert_eq!(call(&lib.state, "POST", "/api/libraries", Some(&ed), Some(body)).await.status, StatusCode::OK, "already there");
    let rel: String = scalar(&lib, "SELECT rel_path FROM models WHERE title = 'Dragon'");
    assert_eq!(rel, "Dragon.zip");
    // Libraries are a filter, a column and a sort key.
    let v = call(&lib.state, "POST", "/api/models/ids", Some(&viewer), Some(json!({"filter": {"include": {"library": [inner]}}}))).await.json();
    assert_eq!(v["ids"].as_array().unwrap().len(), 1, "{v}");
    let tree = call(&lib.state, "POST", "/api/filter-tree", Some(&viewer), Some(json!({"show": ["library"]}))).await.json();
    let names: Vec<&str> = tree["libraries"].as_array().unwrap().iter().map(|l| l["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["Gambody minis", "STL"]);
    assert_eq!(tree["counts"]["library"][inner.to_string()], 1, "{tree}");
    let ids = call(&lib.state, "POST", "/api/models/ids", Some(&viewer), Some(json!({"sort": {"column": "library_name", "descending": false}}))).await;
    assert_eq!(ids.status, StatusCode::OK, "{:?}", ids.json());
    let rows = call(&lib.state, "POST", "/api/models/rows", Some(&viewer), Some(json!({"ids": ids.json()["ids"]}))).await.json();
    assert_eq!(rows["rows"][0]["library"], "Gambody minis", "{rows}");
    assert_eq!(rows["rows"][0]["rel_path"], "Dragon.zip");
    let bad = json!({"path": lib.dir.path().join("nowhere").to_string_lossy()});
    assert_eq!(call(&lib.state, "POST", "/api/libraries", Some(&ed), Some(bad)).await.status, StatusCode::NOT_FOUND);

    // Rename (a taken name gets a number), pattern and monitoring.
    let r = call(&lib.state, "PATCH", &format!("/api/libraries/{inner}"), Some(&ed), Some(json!({"name": "stl", "monitored": true}))).await;
    assert_eq!((r.json()["name"].as_str(), r.json()["monitored"].as_bool()), (Some("stl (2)"), Some(true)), "{:?}", r.json());
    let r = call(&lib.state, "PATCH", &format!("/api/libraries/{outer}"), Some(&ed), Some(json!({"pattern": ""}))).await;
    assert_eq!(r.json()["pattern"], Value::Null);
    let r = call(&lib.state, "PATCH", &format!("/api/libraries/{outer}"), Some(&ed), Some(json!({"colour": "red"}))).await;
    assert_eq!(r.status, StatusCode::UNPROCESSABLE_ENTITY);

    // Crawling the outer library leaves the inner one's archives alone.
    let v = run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    assert_eq!(v["job"]["message"], "0 new, 0 updated, 1 unchanged", "{v}");
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM models"), 2);

    // Removing the inner library gives its records back.
    let r = call(&lib.state, "POST", &format!("/api/libraries/{inner}/remove"), Some(&ed), None).await;
    assert_eq!((r.json()["moved_to"].as_i64(), r.json()["moved"].as_i64(), r.json()["deleted"].as_i64()), (Some(outer), Some(1), Some(0)));
    let rel: String = scalar(&lib, "SELECT rel_path FROM models WHERE title = 'Dragon'");
    assert_eq!(rel, "Gambody/Dragon.zip");
    assert_eq!(call(&lib.state, "POST", &format!("/api/libraries/{inner}/remove"), Some(&ed), None).await.status, StatusCode::NOT_FOUND);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_library_moves_without_a_recrawl() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let ed = lib.user("ed", Role::Editor);
    let old = lib.dir.path().join("old").join("STL");
    write_zip(&old.join("Studio").join("Dragon.zip"), "dragon.stl");
    write_zip(&old.join("Studio").join("Knight.zip"), "knight.stl");
    run(&lib, &ed, json!({"kind": "crawl", "folder": old.to_string_lossy()})).await;
    lib.state.db.with(|c| c.execute("UPDATE models SET title = 'Edited by hand' WHERE rel_path = 'Studio/Dragon.zip'", [])).unwrap();
    let images_before: i64 = scalar(&lib, "SELECT count(*) FROM images WHERE thumbnail_path IS NOT NULL");
    assert_eq!(images_before, 2);
    let id = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json()[0]["id"].as_i64().unwrap();

    // The disk moves (one archive did not come along).
    let new = lib.dir.path().join("new").join("Collection");
    copy_dir(&old, &new);
    std::fs::remove_dir_all(lib.dir.path().join("old")).unwrap();
    std::fs::remove_file(new.join("Studio").join("Knight.zip")).unwrap();
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    assert_eq!(libs[0]["reachable"], false, "the root is gone");

    // Check first: nothing changes.
    let v = run(&lib, &ed, json!({"kind": "relocate_library", "id": id, "root": new.to_string_lossy()})).await;
    let r = &v["result"];
    assert_eq!(r["type"], "relocation", "{v}");
    assert_eq!((r["total"].as_u64(), r["found"].as_u64(), r["missing"].as_u64(), r["applied"].as_bool()), (Some(2), Some(1), Some(1), Some(false)));
    assert_eq!(r["missing_examples"], json!(["Studio/Knight.zip"]));
    let root: String = scalar(&lib, &format!("SELECT root FROM libraries WHERE id = {id}"));
    assert_eq!(root, old.to_string_lossy());

    // Then move: the root changes, and everything else stays.
    let v = run(&lib, &ed, json!({"kind": "relocate_library", "id": id, "root": new.to_string_lossy(), "apply": true})).await;
    assert_eq!(v["result"]["applied"], true);
    assert!(v["job"]["message"].as_str().unwrap().ends_with("; moved"), "{v}");
    let path: String = scalar(&lib, "SELECT archive_path FROM models_view WHERE title = 'Edited by hand'");
    assert_eq!(Path::new(&path), new.join("Studio").join("Dragon.zip"));
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM images WHERE thumbnail_path IS NOT NULL"), images_before, "thumbnails kept");
    assert_eq!(call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json()[0]["reachable"], true);
    // A crawl now finds the Dragon unchanged, and the Knight gone.
    let v = run(&lib, &ed, json!({"kind": "crawl", "folder": new.to_string_lossy()})).await;
    assert_eq!(v["job"]["message"], "0 new, 0 updated, 1 unchanged", "{v}");

    // A root that is no folder, or one outside the editor's roots, is refused.
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "relocate_library", "id": id, "root": "/definitely/not/here"}))).await;
    assert_ne!(r.status, StatusCode::ACCEPTED);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_library_from_before_libraries_is_backed_up_and_migrated() {
    let dir = tempfile::tempdir().unwrap();
    {
        let c = rusqlite::Connection::open(dir.path().join("stlLibrary.db")).unwrap();
        c.execute_batch(
            "CREATE TABLE crawled_folders (id INTEGER NOT NULL, path TEXT NOT NULL, pattern TEXT, PRIMARY KEY (id), UNIQUE (path));
             CREATE TABLE models (id INTEGER NOT NULL, title VARCHAR(512), archive_path TEXT NOT NULL, archive_folder VARCHAR(512),
                 archive_hash VARCHAR(64) NOT NULL, warning TEXT, is_missing BOOLEAN DEFAULT '0' NOT NULL,
                 to_print BOOLEAN DEFAULT '0' NOT NULL, PRIMARY KEY (id), UNIQUE (archive_path));
             INSERT INTO crawled_folders (path, pattern) VALUES ('D:/STL', '{title}');
             INSERT INTO models (title, archive_path, archive_folder, archive_hash) VALUES ('A', 'D:/STL/Studio/a.zip', 'D:/STL/Studio', 'h');",
        )
        .unwrap();
    }
    let mut config = common::library_with(|_| {}).state.config.clone();
    config.data_dir = dir.path().to_path_buf();
    config.roots = vec![];
    let state = Arc::new(Library::open(config.clone()).unwrap());
    let backups: Vec<String> = std::fs::read_dir(dir.path().join("backups"))
        .unwrap()
        .flatten()
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(backups.len(), 1);
    assert!(backups[0].starts_with("stlLibrary_backup_") && backups[0].ends_with("_pre_libraries.7z"), "{backups:?}");
    let (name, rel): (String, String) = state
        .db
        .with(|c| c.query_row("SELECT l.name, m.rel_path FROM models m JOIN libraries l ON l.id = m.library_id", [], |r| Ok((r.get(0)?, r.get(1)?))))
        .unwrap();
    assert_eq!((name.as_str(), rel.as_str()), ("STL", "Studio/a.zip"));
    drop(state);
    // Opened again: nothing to migrate, no second backup.
    let _state = Library::open(config).unwrap();
    assert_eq!(std::fs::read_dir(dir.path().join("backups")).unwrap().count(), 1);
}
