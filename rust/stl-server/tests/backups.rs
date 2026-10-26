//! Sidecar jobs and backups: generate / remove METADATA.7z, the backup job, its
//! routes, and restoring a server backup into a library that opens.

mod common;

use std::io::Write;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;
use stl_server::{Config, Library};

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

async fn run(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    let id = r.json()["id"].as_u64().unwrap();
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if !matches!(v["job"]["state"].as_str(), Some("queued" | "running")) {
            assert_eq!(v["job"]["state"], "done", "{v}");
            return v;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("job {id} did not finish");
}

async fn library() -> (TestLib, String, std::path::PathBuf) {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n")]);
    write_zip(&root.join("Other").join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n")]);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    (lib, ed, root)
}

#[tokio::test(flavor = "multi_thread")]
async fn sidecar_jobs() {
    let (lib, ed, root) = library().await;
    let sidecars = [root.join("Studio").join("METADATA.7z"), root.join("Other").join("METADATA.7z")];
    assert!(sidecars.iter().all(|p| p.is_file()), "the crawl wrote them");

    let v = run(&lib, &ed, json!({"kind": "remove_sidecars"})).await;
    assert_eq!(v["job"]["message"], "removed 2 sidecar(s) from 2 folder(s)");
    assert!(sidecars.iter().all(|p| !p.exists()));

    let v = run(&lib, &ed, json!({"kind": "generate_sidecars"})).await;
    assert_eq!(v["job"]["message"], "updated 2 of 2 folder(s)");
    assert!(sidecars.iter().all(|p| p.is_file()));

    // Cleaning: an out-of-date sidecar is rewritten, an orphan whose archives are
    // gone is removed, one whose archives exist but are not catalogued is kept,
    // and leftover temporary files go.
    let studio = std::fs::read(&sidecars[0]).unwrap();
    std::fs::write(&sidecars[1], &studio).unwrap(); // Other's now lists Dragon.zip
    std::fs::write(root.join("Other").join("METADATA.7z.tmp"), b"partial").unwrap();
    std::fs::create_dir_all(root.join("Gone")).unwrap();
    std::fs::write(root.join("Gone").join("METADATA.7z"), &studio).unwrap();
    std::fs::create_dir_all(root.join("Extra")).unwrap();
    std::fs::write(root.join("Extra").join("METADATA.7z"), &studio).unwrap();
    std::fs::copy(root.join("Studio").join("Dragon.zip"), root.join("Extra").join("Dragon.zip")).unwrap();
    let v = run(&lib, &ed, json!({"kind": "clean_sidecars"})).await;
    assert_eq!(v["job"]["message"], "4 sidecar folder(s): 1 rewritten, 1 removed, 1 kept", "{v}");
    let listed = |p: &std::path::Path| stl_core::storage::sidecar::listed_archives(p).unwrap();
    assert_eq!(listed(&sidecars[1]), ["Knight.zip"]);
    assert!(!root.join("Other").join("METADATA.7z.tmp").exists());
    assert!(!root.join("Gone").join("METADATA.7z").exists());
    assert!(root.join("Extra").join("METADATA.7z").is_file());
    assert_eq!(listed(&sidecars[0]), ["Dragon.zip"]);
    // A second run finds nothing to do.
    let v = run(&lib, &ed, json!({"kind": "clean_sidecars"})).await;
    assert_eq!(v["job"]["message"], "3 sidecar folder(s): 0 rewritten, 0 removed, 1 kept", "{v}");

    // Disabled in the settings: the job refuses.
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "generate_sidecars"}))).await;
    let id = r.json()["id"].as_u64().unwrap();
    tokio::time::sleep(Duration::from_millis(500)).await;
    let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(&ed), None).await.json();
    assert_eq!(v["job"]["state"], "failed", "{v}");
}

#[tokio::test(flavor = "multi_thread")]
async fn backups_and_restore() {
    let (lib, ed, _root) = library().await;
    let admin = lib.user("admin", Role::Admin);
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "backup"}))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    assert_eq!(call(&lib.state, "GET", "/api/backups", Some(&ed), None).await.status, StatusCode::FORBIDDEN);

    let v = run(&lib, &admin, json!({"kind": "backup"})).await;
    assert!(v["job"]["message"].as_str().unwrap().ends_with(" MB"), "{v}");
    let list = call(&lib.state, "GET", "/api/backups", Some(&admin), None).await.json();
    assert_eq!(list.as_array().unwrap().len(), 1, "{list}");
    let name = list[0]["name"].as_str().unwrap().to_string();
    assert!(name.starts_with("stlLibrary_backup_") && name.ends_with(".7z"));

    let r = call(&lib.state, "GET", &format!("/api/backups/{name}"), Some(&admin), None).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(&r.body[..6], b"7z\xBC\xAF\x27\x1C", "a 7z archive");
    assert_eq!(r.body.len() as u64, list[0]["size"].as_u64().unwrap());
    let r = call(&lib.state, "GET", "/api/backups/server.db", Some(&admin), None).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);

    // The backup restores into an empty data directory as a working library.
    let target = tempfile::tempdir().unwrap();
    let file = target.path().join("backup.7z");
    std::fs::write(&file, &r_body(&lib, &admin, &name).await).unwrap();
    let data = target.path().join("data");
    std::fs::create_dir_all(&data).unwrap();
    let restored = stl_core::backup::restore(&file, &data, "t").unwrap();
    assert!(restored.thumbnails);
    assert!(restored.previous.is_none(), "nothing was replaced in an empty directory");
    let config = Config { data_dir: data.clone(), roots: vec![], ..lib.state.config.clone() };
    let opened = Arc::new(Library::open(config).unwrap());
    let n: i64 = opened.db.with(|c| c.query_row("SELECT count(*) FROM models", [], |r| r.get(0))).unwrap();
    assert_eq!(n, 2);
    drop(opened);

    let r = call(&lib.state, "DELETE", &format!("/api/backups/{name}"), Some(&admin), None).await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    assert_eq!(call(&lib.state, "GET", "/api/backups", Some(&admin), None).await.json(), json!([]));
}

async fn r_body(lib: &TestLib, token: &str, name: &str) -> Vec<u8> {
    call(&lib.state, "GET", &format!("/api/backups/{name}"), Some(token), None).await.body
}

fn png(rgb: [u8; 3]) -> Vec<u8> {
    let img = image::RgbImage::from_pixel(40, 30, image::Rgb(rgb));
    let mut out = std::io::Cursor::new(Vec::new());
    img.write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

/// The archives of the exported library, wherever they are put.
fn archives(root: &Path) {
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n"), ("dragon.png", &png([200, 30, 30]))]);
    write_zip(&root.join("Other").join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n")]);
}

async fn upload(lib: &TestLib, token: &str, bytes: Vec<u8>) -> common::Reply {
    let req = axum::http::Request::builder()
        .method("POST")
        .uri("/api/backups/upload")
        .header(axum::http::header::AUTHORIZATION, format!("Bearer {token}"))
        .header(axum::http::header::CONTENT_TYPE, "application/octet-stream")
        .body(axum::body::Body::from(bytes))
        .unwrap();
    common::send_from(&lib.state, common::LOCAL, req).await
}

async fn count(lib: &TestLib, sql: &str) -> i64 {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn export_and_import_with_new_roots() {
    // Library A: two archives, one with an image; exported as a package.
    let a = empty_library();
    let a_admin = a.user("admin", Role::Admin);
    let a_root = a.dir.path().join("archives");
    archives(&a_root);
    run(&a, &a_admin, json!({"kind": "crawl", "folder": a_root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    run(&a, &a_admin, json!({"kind": "backup"})).await;
    let name = call(&a.state, "GET", "/api/backups", Some(&a_admin), None).await.json()[0]["name"].as_str().unwrap().to_string();
    let package = r_body(&a, &a_admin, &name).await;

    // Library B, elsewhere: its own users and records; A's archives under another root.
    let b = empty_library();
    let admin = b.user("boss", Role::Admin);
    let ed = b.user("ed", Role::Editor);
    let mine = b.dir.path().join("mine");
    write_zip(&mine.join("Orc.zip"), &[("orc.stl", b"solid o\nendsolid\n")]);
    run(&b, &ed, json!({"kind": "crawl", "folder": mine.to_string_lossy(), "pattern": "{title}"})).await;
    let moved = b.dir.path().join("moved");
    archives(&moved);

    assert_eq!(upload(&b, &ed, package.clone()).await.status, StatusCode::FORBIDDEN);
    assert_eq!(upload(&b, &admin, b"not a package".to_vec()).await.status, StatusCode::BAD_REQUEST);
    // Packages are large: no body limit (refused for what it is, not for its size).
    assert_eq!(upload(&b, &admin, vec![7; 3 << 20]).await.status, StatusCode::BAD_REQUEST);
    let r = upload(&b, &admin, package).await;
    assert_eq!(r.status, StatusCode::CREATED, "{:?}", r.json());
    let pkg = r.json()["name"].as_str().unwrap().to_string();
    assert!(pkg.ends_with("_upload.7z"), "{pkg}");
    let listed = call(&b.state, "GET", "/api/backups", Some(&admin), None).await.json();
    assert_eq!(listed.as_array().unwrap().len(), 1, "only the package: {listed}");

    // Checking: the folder keeps its root while that exists here, or takes the one given.
    let v = run(&b, &admin, json!({"kind": "import_library", "name": pkg})).await;
    let folder = &v["result"]["folders"][0];
    assert_eq!(v["result"]["type"], "import", "{v}");
    assert_eq!(folder["root"], a_root.to_string_lossy().as_ref());
    assert_eq!((folder["models"].as_i64(), folder["found"].as_u64()), (Some(2), Some(2)), "{folder}");
    let uuid = folder["uuid"].as_str().unwrap().to_string();
    let nowhere = b.dir.path().join("nowhere").to_string_lossy().into_owned();
    let v = run(&b, &admin, json!({"kind": "import_library", "name": pkg, "roots": {&uuid: nowhere}})).await;
    let folder = &v["result"]["folders"][0];
    assert_eq!((folder["missing"].as_u64(), folder["root_exists"].as_bool()), (Some(2), Some(false)), "{folder}");
    assert_eq!(count(&b, "SELECT count(*) FROM models").await, 1, "checking changes nothing");

    // Applying needs an administrator and the confirmation.
    let apply = json!({"kind": "import_library", "name": pkg, "roots": {&uuid: moved.to_string_lossy()}, "apply": true});
    assert_eq!(call(&b.state, "POST", "/api/jobs", Some(&admin), Some(apply.clone())).await.status, StatusCode::BAD_REQUEST);
    let mut confirmed = apply.clone();
    confirmed["confirm"] = json!("REPLACE");
    assert_eq!(call(&b.state, "POST", "/api/jobs", Some(&ed), Some(confirmed.clone())).await.status, StatusCode::FORBIDDEN);
    let v = run(&b, &admin, confirmed).await;
    assert!(v["job"]["message"].as_str().unwrap().ends_with("imported"), "{v}");
    assert_eq!(v["result"]["applied"], true);

    // B is now A's library at its new root; B's users stay.
    let libs = call(&b.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    assert_eq!(libs.to_string().matches("\"root\"").count(), 1, "{libs}");
    assert!(libs.to_string().contains(&serde_json::to_string(&moved.to_string_lossy()).unwrap()), "{libs}");
    assert_eq!(count(&b, "SELECT count(*) FROM models").await, 2);
    assert_eq!(count(&b, "SELECT count(*) FROM models WHERE title = 'Orc'").await, 0);
    let image = count(&b, "SELECT min(id) FROM images").await;
    let r = call(&b.state, "GET", &format!("/api/images/{image}/thumbnail"), Some(&ed), None).await;
    let location = r.headers.get("location").and_then(|l| l.to_str().ok()).unwrap_or_default().to_string();
    let r = call(&b.state, "GET", &location, Some(&ed), None).await;
    assert_eq!(r.status, StatusCode::OK, "the thumbnails came along ({location})");
    assert!(!r.body.is_empty());
    let names: Vec<String> = call(&b.state, "GET", "/api/backups", Some(&admin), None)
        .await
        .json()
        .as_array()
        .unwrap()
        .iter()
        .map(|b| b["name"].as_str().unwrap().to_string())
        .collect();
    assert!(names.iter().any(|n| n.ends_with("_pre_import.7z")), "{names:?}");
    assert!(!b.dir.path().join("import").exists(), "the staging folder is gone");

    // While a library is being imported, writes are refused; reads go on.
    b.state.importing.store(true, std::sync::atomic::Ordering::SeqCst);
    let r = call(&b.state, "POST", "/api/models/delete", Some(&ed), Some(json!({"ids": [1]}))).await;
    assert_eq!(r.status, StatusCode::CONFLICT);
    assert_eq!(call(&b.state, "GET", "/api/libraries", Some(&ed), None).await.status, StatusCode::OK);
    b.state.importing.store(false, std::sync::atomic::Ordering::SeqCst);
}
