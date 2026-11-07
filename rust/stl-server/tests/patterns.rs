//! The path pattern each folder was crawled with is kept: later crawls of the
//! library, monitored ones included, and rescans use the same one.

mod common;

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;

fn write_zip(path: &Path) {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    let mut z = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
    z.start_file("model.stl", zip::write::SimpleFileOptions::default()).unwrap();
    z.write_all(b"solid x\nendsolid\n").unwrap();
    z.finish().unwrap();
}

async fn run(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    let id = r.json()["id"].as_u64().unwrap();
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

/// `(sculptor, collection)` of the archive named `name`.
fn fields(lib: &TestLib, name: &str) -> (Option<String>, Option<String>) {
    lib.state
        .db
        .with(|c| {
            c.query_row(
                "SELECT sculptor_name, collection_name FROM models_view WHERE rel_path LIKE '%' || ?1",
                [name],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
        })
        .unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_folder_keeps_the_pattern_it_was_crawled_with() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    let special = root.join("Special");
    write_zip(&root.join("Studio").join("A.zip"));
    write_zip(&special.join("B.zip"));
    let folder = root.to_string_lossy().into_owned();
    run(&lib, &ed, json!({"kind": "crawl", "folder": folder, "pattern": "{sculptor}/{title}"})).await;

    // One folder has another layout: it gets a pattern of its own, for its own archives.
    write_zip(&special.join("C.zip"));
    write_zip(&special.join("Set1").join("D.zip"));
    let sub = special.to_string_lossy().into_owned();
    let v = run(&lib, &ed, json!({"kind": "crawl", "folder": sub, "in_library": true, "pattern": "{collection}/{title}"})).await;
    assert!(v["job"]["message"].as_str().unwrap().starts_with("2 new"), "{v}");
    assert_eq!(fields(&lib, "C.zip"), (None, Some("Special".to_string())));
    assert_eq!(fields(&lib, "D.zip"), (Some("Set1".to_string()), None), "its sub-folder keeps the library's");
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    assert_eq!(libs[0]["folders"][0]["pattern"], "{sculptor}/{title}", "the library's stays");
    assert_eq!(libs[0]["folders"][0]["subfolder_patterns"], json!([{"folder": "Special", "pattern": "{collection}/{title}"}]));

    // Every folder with archives, with its own pattern or none.
    let id = libs[0]["folders"][0]["id"].as_i64().unwrap();
    let list = call(&lib.state, "GET", &format!("/api/libraries/folders/{id}/folders"), Some(&ed), None).await.json();
    assert_eq!(
        list,
        json!([
            {"folder": "Special", "archives": 2, "pattern": "{collection}/{title}"},
            {"folder": "Special/Set1", "archives": 1, "pattern": null},
            {"folder": "Studio", "archives": 1, "pattern": null},
        ])
    );

    // A crawl of the whole library, as a monitored one, names no pattern: each
    // folder's is used.
    write_zip(&special.join("E.zip"));
    write_zip(&root.join("Studio").join("F.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": folder, "keep_missing": true})).await;
    assert_eq!(fields(&lib, "E.zip"), (None, Some("Special".to_string())));
    assert_eq!(fields(&lib, "F.zip"), (Some("Studio".to_string()), None));

    // A rescan fills the fields the pattern gives, and keeps the others.
    lib.state.db.with(|c| c.execute("UPDATE models SET collection_id = NULL WHERE rel_path LIKE '%E.zip'", [])).unwrap();
    let id: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models WHERE rel_path LIKE '%E.zip'", [], |r| r.get(0))).unwrap();
    run(&lib, &ed, json!({"kind": "rescan", "ids": [id]})).await;
    assert_eq!(fields(&lib, "E.zip"), (None, Some("Special".to_string())));

    // An empty pattern goes back to the library's.
    run(&lib, &ed, json!({"kind": "crawl", "folder": sub, "in_library": true, "pattern": ""})).await;
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    assert_eq!(libs[0]["folders"][0]["subfolder_patterns"], json!([]));
    write_zip(&special.join("G.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": folder})).await;
    assert_eq!(fields(&lib, "G.zip"), (Some("Special".to_string()), None));
}

#[tokio::test(flavor = "multi_thread")]
async fn a_pattern_can_be_tried_on_a_folders_archives() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let ed = lib.user("ed", Role::Editor);
    let viewer = lib.user("vi", Role::Viewer);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Studio").join("Dragon").join("One.zip"));
    write_zip(&root.join("Studio").join("Dragon").join("Two.zip"));
    write_zip(&root.join("Studio").join("Dragon").join("Deeper").join("Three.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;

    let folder = root.join("Studio").join("Dragon").to_string_lossy().replace('\\', "/");
    let uri = |pattern: &str| format!("/api/libraries/pattern-sample?folder={}&pattern={}", enc(&folder), enc(pattern));
    let r = call(&lib.state, "GET", &uri("{sculptor}/{collection}/{title}"), Some(&ed), None).await.json();
    // The archives directly in the folder, in any order: not those of its sub-folder.
    let mut paths: Vec<&str> = r.as_array().unwrap().iter().map(|s| s["path"].as_str().unwrap()).collect();
    paths.sort();
    assert_eq!(paths, ["Studio/Dragon/One.zip", "Studio/Dragon/Two.zip"]);
    let one = r.as_array().unwrap().iter().find(|s| s["path"] == "Studio/Dragon/One.zip").unwrap();
    assert_eq!(
        one["fields"],
        json!([{"field": "sculptor_name", "value": "Studio"}, {"field": "collection_name", "value": "Dragon"}, {"field": "title", "value": "One"}])
    );
    let r = call(&lib.state, "GET", &uri(""), Some(&ed), None).await.json();
    assert!(r.as_array().unwrap().iter().all(|s| s["fields"] == json!([])), "{r}");
    let r = call(&lib.state, "GET", &uri("{title}"), Some(&viewer), None).await;
    assert_eq!(r.status, axum::http::StatusCode::FORBIDDEN);
}

fn enc(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_stored_folder_pattern_can_be_changed_and_forgotten() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Sub").join("One.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let id: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM libraries", [], |r| r.get(0))).unwrap();
    let set = async |folder: &str, pattern: &str| {
        call(&lib.state, "PATCH", &format!("/api/libraries/folders/{id}"), Some(&ed), Some(json!({"subfolder": {"folder": folder, "pattern": pattern}}))).await
    };
    let r = set("Sub", "{title}").await;
    assert_eq!(r.json()["subfolder_patterns"], json!([{"folder": "Sub", "pattern": "{title}"}]));
    let r = set("Sub", "{sculptor}/{title}").await;
    assert_eq!(r.json()["subfolder_patterns"][0]["pattern"], "{sculptor}/{title}");
    let r = set("Sub", "").await;
    assert_eq!(r.json()["subfolder_patterns"], json!([]));
    assert_eq!(set("../Sub", "x").await.status, axum::http::StatusCode::BAD_REQUEST);
}
