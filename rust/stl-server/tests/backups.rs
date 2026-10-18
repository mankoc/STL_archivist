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
