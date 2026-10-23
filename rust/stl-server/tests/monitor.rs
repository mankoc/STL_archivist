//! Monitored folders: the switch, the check that queues a crawl only when
//! archives changed, archives that vanish becoming missing, and removing the
//! missing records.

mod common;

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;

fn write_zip(path: &Path, member: &str) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let mut z = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
    z.start_file(member, zip::write::SimpleFileOptions::default()).unwrap();
    z.write_all(b"solid x\nendsolid\n").unwrap();
    z.finish().unwrap();
}

async fn wait_job(lib: &TestLib, token: &str, id: u64) -> Value {
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

/// One monitor pass (forced, as if every folder were due); the crawls it
/// queued, after they finished.
async fn check(lib: &TestLib, token: &str) -> Vec<Value> {
    let state = lib.state.clone();
    let jobs = tokio::task::spawn_blocking(move || stl_server::monitor::check(&state, true)).await.unwrap();
    let mut out = Vec::new();
    for j in jobs {
        assert_eq!(j.user, stl_server::monitor::USER);
        out.push(wait_job(lib, token, j.id).await);
    }
    out
}

fn scalar(lib: &TestLib, sql: &str) -> i64 {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn monitored_folders_are_crawled_when_they_change() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let viewer = lib.user("vi", Role::Viewer);
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Studio").join("Dragon.zip"), "dragon.stl");
    let folder = root.to_string_lossy().into_owned();
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "crawl", "folder": folder}))).await;
    wait_job(&lib, &ed, r.json()["id"].as_u64().unwrap()).await;

    // Not monitored yet: nothing is checked.
    write_zip(&root.join("Studio").join("Knight.zip"), "knight.stl");
    assert!(check(&lib, &ed).await.is_empty());

    // The switch: editors, on a library.
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&viewer), None).await.json();
    let id = libs[0]["id"].as_i64().unwrap();
    let body = json!({"monitored": true});
    let uri = format!("/api/libraries/{id}");
    assert_eq!(call(&lib.state, "PATCH", &uri, Some(&viewer), Some(body.clone())).await.status, StatusCode::FORBIDDEN);
    assert_eq!(call(&lib.state, "PATCH", "/api/libraries/999", Some(&ed), Some(body.clone())).await.status, StatusCode::NOT_FOUND);
    assert_eq!(call(&lib.state, "PATCH", &uri, Some(&ed), Some(body)).await.status, StatusCode::OK);
    let folders = call(&lib.state, "GET", "/api/libraries", Some(&viewer), None).await.json();
    assert_eq!(folders[0]["monitored"], true, "{folders}");

    // A new archive: one crawl, by the monitor.
    let crawls = check(&lib, &ed).await;
    assert_eq!(crawls.len(), 1);
    assert_eq!(crawls[0]["job"]["message"], "1 new, 0 updated, 1 unchanged", "{}", crawls[0]);
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models"), 2);

    // Nothing changed: no crawl.
    assert!(check(&lib, &ed).await.is_empty());

    // A vanished archive is marked missing; Remove missing archives deletes it.
    std::fs::remove_file(root.join("Studio").join("Knight.zip")).unwrap();
    assert_eq!(check(&lib, &ed).await.len(), 1);
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE is_missing"), 1);
    assert!(check(&lib, &ed).await.is_empty(), "a missing record is not reported again");
    assert_eq!(call(&lib.state, "POST", "/api/maintenance/remove-missing", Some(&viewer), None).await.status, StatusCode::FORBIDDEN);
    let r = call(&lib.state, "POST", "/api/maintenance/remove-missing", Some(&ed), None).await;
    assert_eq!(r.json()["removed"], 1, "{:?}", r.json());
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models"), 1);

    // The background pass waits for the interval: the folder was checked a
    // moment ago, so a new archive waits for the next due check.
    write_zip(&root.join("Studio").join("Orc.zip"), "orc.stl");
    let state = lib.state.clone();
    assert!(tokio::task::spawn_blocking(move || stl_server::monitor::check(&state, false)).await.unwrap().is_empty(), "not due yet");
    assert_eq!(check(&lib, &ed).await.len(), 1);

    // The interval is a library setting.
    let mut settings = call(&lib.state, "GET", "/api/settings/library", Some(&ed), None).await.json();
    assert_eq!(settings["monitor_interval_minutes"], 30);
    let admin = lib.user("ad", Role::Admin);
    settings["monitor_interval_minutes"] = json!(0);
    assert_eq!(call(&lib.state, "PUT", "/api/settings/library", Some(&admin), Some(settings.clone())).await.status, StatusCode::BAD_REQUEST);
    settings["monitor_interval_minutes"] = json!(5);
    assert_eq!(call(&lib.state, "PUT", "/api/settings/library", Some(&admin), Some(settings)).await.json()["monitor_interval_minutes"], 5);
}
