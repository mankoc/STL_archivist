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
    write_zip(&special.join("Set1").join("B.zip"));
    let folder = root.to_string_lossy().into_owned();
    run(&lib, &ed, json!({"kind": "crawl", "folder": folder, "pattern": "{sculptor}/{title}"})).await;

    // Part of the library has another layout: it gets a pattern of its own.
    write_zip(&special.join("Set1").join("C.zip"));
    let sub = special.to_string_lossy().into_owned();
    let v = run(&lib, &ed, json!({"kind": "crawl", "folder": sub, "in_library": true, "pattern": "{collection}/{title}"})).await;
    assert!(v["job"]["message"].as_str().unwrap().starts_with("1 new"), "{v}");
    assert_eq!(fields(&lib, "C.zip"), (None, Some("Set1".to_string())));
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    assert_eq!(libs[0]["folders"][0]["pattern"], "{sculptor}/{title}", "the library's stays");
    assert_eq!(libs[0]["folders"][0]["subfolder_patterns"], json!([{"folder": "Special", "pattern": "{collection}/{title}"}]));

    // A crawl of the whole library, as a monitored one, names no pattern: each
    // folder's is used.
    write_zip(&special.join("Set2").join("D.zip"));
    write_zip(&root.join("Studio").join("E.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": folder, "keep_missing": true})).await;
    assert_eq!(fields(&lib, "D.zip"), (None, Some("Set2".to_string())));
    assert_eq!(fields(&lib, "E.zip"), (Some("Studio".to_string()), None));

    // So does a crawl of the folder without a pattern.
    write_zip(&special.join("Set2").join("F.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": sub, "in_library": true})).await;
    assert_eq!(fields(&lib, "F.zip"), (None, Some("Set2".to_string())));

    // A rescan fills the fields the pattern gives, and keeps the others.
    lib.state.db.with(|c| c.execute("UPDATE models SET collection_id = NULL WHERE rel_path LIKE '%D.zip'", [])).unwrap();
    let id: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models WHERE rel_path LIKE '%D.zip'", [], |r| r.get(0))).unwrap();
    run(&lib, &ed, json!({"kind": "rescan", "ids": [id]})).await;
    assert_eq!(fields(&lib, "D.zip"), (None, Some("Set2".to_string())));

    // An empty pattern goes back to the library's.
    run(&lib, &ed, json!({"kind": "crawl", "folder": sub, "in_library": true, "pattern": ""})).await;
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    assert_eq!(libs[0]["folders"][0]["subfolder_patterns"], json!([]));
    write_zip(&special.join("Set2").join("G.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": folder})).await;
    assert_eq!(fields(&lib, "G.zip"), (Some("Set2".to_string()), None));
}
