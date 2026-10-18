//! Database Maintenance: record lists, Search in Folder, Find Moved in DB and
//! Merge Moved, duplicate entries, removal and the warning re-checks.

mod common;

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;

fn png(rgb: [u8; 3]) -> Vec<u8> {
    let img = image::RgbImage::from_pixel(40, 30, image::Rgb(rgb));
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

async fn wait(lib: &TestLib, token: &str, id: u64) -> Value {
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if !matches!(v["job"]["state"].as_str(), Some("queued" | "running")) {
            return v;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("job {id} did not finish");
}

async fn run(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    let v = wait(lib, token, r.json()["id"].as_u64().unwrap()).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    v
}

fn rows(v: &Value) -> &Vec<Value> {
    assert_eq!(v["result"]["type"], "records", "{v}");
    v["result"]["rows"].as_array().unwrap()
}

fn scalar<T: rusqlite::types::FromSql>(lib: &TestLib, sql: &str) -> T {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

fn path_s(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

/// `archives/Studio/{Dragon,Knight}.zip` (Knight has no image), crawled.
async fn library() -> (TestLib, String, std::path::PathBuf) {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n"), ("render.png", &png([200, 0, 0]))]);
    write_zip(&root.join("Studio").join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n")]);
    run(&lib, &ed, json!({"kind": "crawl", "folder": path_s(&root), "pattern": "{sculptor}/{title}"})).await;
    (lib, ed, root)
}

#[tokio::test(flavor = "multi_thread")]
async fn verify_search_folder_and_merge_moved() {
    let (lib, ed, root) = library().await;
    let knight: i64 = scalar(&lib, "SELECT id FROM models WHERE title = 'Knight'");
    lib.state.db.with(|c| c.execute("UPDATE models SET title = 'Sir Knight' WHERE id = ?1", [knight])).unwrap();

    // The archive moves out of the library: Verify lists it as missing.
    let away = lib.dir.path().join("away");
    std::fs::create_dir_all(&away).unwrap();
    std::fs::rename(root.join("Studio").join("Knight.zip"), away.join("Knight.zip")).unwrap();
    let v = run(&lib, &ed, json!({"kind": "verify_archives"})).await;
    let missing = rows(&v);
    assert_eq!(missing.len(), 1, "only problems are listed: {missing:?}");
    assert_eq!((missing[0]["id"].as_i64(), missing[0]["status"].as_str()), (Some(knight), Some("Missing")));

    // Editors may only search inside the library's folders; a missing folder is refused.
    let outside = tempfile::tempdir().unwrap();
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "search_folder", "folder": path_s(outside.path()), "ids": [knight]}))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN, "{:?}", r.json());
    let gone = path_s(&lib.dir.path().join("nowhere"));
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "search_folder", "folder": gone, "ids": [knight]}))).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);

    // Search in Folder finds it by content hash and points the record there.
    let v = run(&lib, &ed, json!({"kind": "search_folder", "folder": path_s(&away), "ids": [knight]})).await;
    assert_eq!(v["job"]["message"], "1 archive(s) found and relocated");
    let found = rows(&v);
    assert_eq!((found[0]["status"].as_str(), found[0]["match_path"].as_str()), (Some("Found"), Some(path_s(&away.join("Knight.zip")).as_str())));
    let path: String = scalar(&lib, &format!("SELECT archive_path FROM models WHERE id = {knight}"));
    assert_eq!(path, path_s(&away.join("Knight.zip")));

    // A copy is crawled elsewhere (a second record), then the original goes away.
    let elsewhere = lib.dir.path().join("elsewhere");
    std::fs::create_dir_all(&elsewhere).unwrap();
    std::fs::copy(away.join("Knight.zip"), elsewhere.join("Knight.zip")).unwrap();
    run(&lib, &ed, json!({"kind": "crawl", "folder": path_s(&elsewhere)})).await;
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM models"), 3);
    std::fs::remove_file(away.join("Knight.zip")).unwrap();
    let v = run(&lib, &ed, json!({"kind": "find_moved", "ids": [knight]})).await;
    let moved = rows(&v);
    assert_eq!(moved.len(), 1);
    assert_eq!((moved[0]["status"].as_str(), moved[0]["match_path"].as_str()), (Some("Moved (in DB)"), Some(path_s(&elsewhere.join("Knight.zip")).as_str())));

    // Merging keeps the edited metadata at the new location.
    let r = call(&lib.state, "POST", "/api/maintenance/merge-moved", Some(&ed),
        Some(json!({"items": [{"id": knight, "path": moved[0]["match_path"]}]}))).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    assert_eq!(r.json(), json!({"merged": 1, "errors": 0}));
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM models"), 2);
    let title: String = scalar(&lib, &format!("SELECT title FROM models WHERE archive_path = '{}'", path_s(&elsewhere.join("Knight.zip")).replace('\'', "''")));
    assert_eq!(title, "Sir Knight");
}

#[tokio::test(flavor = "multi_thread")]
async fn duplicate_entries_and_removal() {
    let (lib, ed, root) = library().await;
    let copy = lib.dir.path().join("copy");
    std::fs::create_dir_all(&copy).unwrap();
    std::fs::copy(root.join("Studio").join("Dragon.zip"), copy.join("Dragon.zip")).unwrap();
    run(&lib, &ed, json!({"kind": "crawl", "folder": path_s(&copy)})).await;

    let v = run(&lib, &ed, json!({"kind": "find_duplicate_entries"})).await;
    let dups = rows(&v);
    assert_eq!(dups.len(), 2, "{dups:?}");
    assert!(dups.iter().all(|r| r["status"] == "Duplicate" && !r["match_path"].as_str().unwrap().is_empty()));

    let id = dups[1]["id"].as_i64().unwrap();
    let viewer = lib.user("vi", Role::Viewer);
    let r = call(&lib.state, "POST", "/api/maintenance/remove", Some(&viewer), Some(json!({"ids": [id]}))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    let r = call(&lib.state, "POST", "/api/maintenance/remove", Some(&ed), Some(json!({"ids": [id]}))).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    assert_eq!(r.json()["removed"], 1);
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM models"), 2);
    let v = run(&lib, &ed, json!({"kind": "find_duplicate_entries"})).await;
    assert!(rows(&v).is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn warning_rechecks() {
    let (lib, ed, _root) = library().await;
    // Stale lines: Dragon has an image; a retired line sits on Knight.
    lib.state
        .db
        .with(|c| {
            c.execute("UPDATE models SET warning = 'Archive without images' WHERE title = 'Dragon'", [])?;
            c.execute("UPDATE models SET warning = 'Archive with supports' WHERE title = 'Knight'", [])
        })
        .unwrap();

    let v = run(&lib, &ed, json!({"kind": "recheck_warnings", "warning": "no_images"})).await;
    assert_eq!(v["job"]["message"], "1 cleared, 1 added, 0 skipped (missing or unreadable)");
    let dragon: Option<String> = scalar(&lib, "SELECT warning FROM models WHERE title = 'Dragon'");
    assert_eq!(dragon, None);
    let knight: Option<String> = scalar(&lib, "SELECT warning FROM models WHERE title = 'Knight'");
    assert_eq!(knight.as_deref(), Some("Archive without images\nArchive with supports"), "unknown lines last");

    let v = run(&lib, &ed, json!({"kind": "recheck_warnings", "warning": "presupported"})).await;
    assert_eq!(v["job"]["message"], "1 cleared, 0 added, 0 skipped (missing or unreadable)");
    let knight: Option<String> = scalar(&lib, "SELECT warning FROM models WHERE title = 'Knight'");
    assert_eq!(knight.as_deref(), Some("Archive without images"));

    for bad in [json!({"kind": "recheck_warnings", "warning": "colour"}), json!({"kind": "find_moved", "ids": []})] {
        let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(bad.clone())).await;
        assert_eq!(r.status, StatusCode::BAD_REQUEST, "{bad}");
    }
}
