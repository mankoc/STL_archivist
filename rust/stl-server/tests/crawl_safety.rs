//! What a crawl must not do: delete the record of an archive that is there,
//! take an unmounted share for deleted archives, or lose an archive's file list
//! because a member could not be read.

mod common;

use std::io::Write;
use std::path::Path;
use std::time::Duration;

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

async fn run_job(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    wait_job(lib, token, r.json()["id"].as_u64().unwrap()).await
}

async fn crawl(lib: &TestLib, token: &str, folder: &Path) -> Value {
    run_job(lib, token, json!({"kind": "crawl", "folder": folder.to_string_lossy()})).await
}

fn scalar(lib: &TestLib, sql: &str) -> i64 {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

fn library() -> (TestLib, String) {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let token = lib.user("ed", Role::Editor);
    (lib, token)
}

async fn monitor_pass(lib: &TestLib, token: &str) {
    let state = lib.state.clone();
    let jobs = tokio::task::spawn_blocking(move || stl_server::monitor::check(&state, true)).await.unwrap();
    for j in jobs {
        wait_job(lib, token, j.id).await;
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn an_archive_that_returns_keeps_its_record() {
    let (lib, ed) = library();
    let root = lib.dir.path().join("archives");
    let knight = root.join("Studio").join("Knight.zip");
    write_zip(&root.join("Studio").join("Dragon.zip"), "dragon.stl");
    write_zip(&knight, "knight.stl");
    crawl(&lib, &ed, &root).await;
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    let id = libs[0]["folders"][0]["id"].as_i64().unwrap();
    call(&lib.state, "PATCH", &format!("/api/libraries/folders/{id}"), Some(&ed), Some(json!({"monitored": true}))).await;
    lib.state.db.with(|c| c.execute("UPDATE models SET title = 'Edited' WHERE rel_path LIKE '%Knight.zip'", [])).unwrap();

    // Gone for a while: the monitor flags the record.
    let aside = lib.dir.path().join("aside.zip");
    std::fs::rename(&knight, &aside).unwrap();
    monitor_pass(&lib, &ed).await;
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE is_missing"), 1);

    // Back at the same path: the next monitor pass clears the flag by itself.
    std::fs::rename(&aside, &knight).unwrap();
    monitor_pass(&lib, &ed).await;
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE is_missing"), 0);

    // So does a plain crawl, and the record is the edited one.
    std::fs::rename(&knight, &aside).unwrap();
    monitor_pass(&lib, &ed).await;
    std::fs::rename(&aside, &knight).unwrap();
    let v = crawl(&lib, &ed, &root).await;
    assert_eq!(v["job"]["message"], "0 new, 0 updated, 2 unchanged", "{v}");
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models"), 2);
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE is_missing"), 0);
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE title = 'Edited'"), 1);
}

#[tokio::test(flavor = "multi_thread")]
async fn an_archive_really_gone_is_still_deleted() {
    let (lib, ed) = library();
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("a").join("One.zip"), "one.stl");
    write_zip(&root.join("a").join("Two.zip"), "two.stl");
    crawl(&lib, &ed, &root).await;
    std::fs::remove_file(root.join("a").join("Two.zip")).unwrap();
    let v = crawl(&lib, &ed, &root).await;
    assert_eq!(v["job"]["message"], "0 new, 0 updated, 1 unchanged, 1 deleted", "the job says so");
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models"), 1);
}

/// A share that is not mounted: its mount point is an empty folder.
#[tokio::test(flavor = "multi_thread")]
async fn an_empty_mount_point_keeps_the_library() {
    let (lib, ed) = library();
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("a").join("One.zip"), "one.stl");
    write_zip(&root.join("b").join("Two.zip"), "two.stl");
    crawl(&lib, &ed, &root).await;

    let away = lib.dir.path().join("away");
    std::fs::rename(&root, &away).unwrap();
    std::fs::create_dir_all(&root).unwrap();
    let v = crawl(&lib, &ed, &root).await;
    let message = v["job"]["message"].as_str().unwrap();
    assert!(message.contains("2 flagged missing") && message.contains("could not be read"), "{message}");
    assert!(v["log"].to_string().contains("is its drive or share connected"), "{v}");
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models"), 2);
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE is_missing"), 2);

    // Mounted again: everything is as it was.
    std::fs::remove_dir(&root).unwrap();
    std::fs::rename(&away, &root).unwrap();
    let v = crawl(&lib, &ed, &root).await;
    assert_eq!(v["job"]["message"], "0 new, 0 updated, 2 unchanged", "{v}");
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE is_missing"), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn update_folders_keeps_an_empty_folder_s_records() {
    let (lib, ed) = library();
    let (one, two) = (lib.dir.path().join("one"), lib.dir.path().join("two"));
    write_zip(&one.join("A.zip"), "a.stl");
    write_zip(&two.join("B.zip"), "b.stl");
    crawl(&lib, &ed, &one).await;
    crawl(&lib, &ed, &two).await;
    std::fs::remove_file(two.join("B.zip")).unwrap();
    run_job(&lib, &ed, json!({"kind": "update_folders"})).await;
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models"), 2, "flagged, not cleaned up");
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models WHERE is_missing"), 1);
}

/// Records below a folder that could not be read are neither gone nor back.
#[tokio::test(flavor = "multi_thread")]
async fn records_below_an_unreadable_folder_are_not_judged() {
    let (lib, ed) = library();
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("locked").join("A.zip"), "a.stl");
    write_zip(&root.join("open").join("B.zip"), "b.stl");
    crawl(&lib, &ed, &root).await;
    let folder = root.to_string_lossy().into_owned();
    let locked = root.join("locked").to_string_lossy().into_owned();
    let open = stl_core::relpath::path_key(&root.join("open").join("B.zip").to_string_lossy());
    let scan = lib
        .state
        .db
        .with(|c| {
            let id = c.query_row("SELECT id FROM libraries", [], |r| r.get::<_, i64>(0))?;
            let listed = std::collections::HashSet::from([open]);
            stl_core::storage::crawl::collect_missing(c, Some((id, &listed)), std::slice::from_ref(&locked), &folder, &[])
        })
        .unwrap();
    assert!(scan.missing.is_empty() && scan.back.is_empty(), "{scan:?}");
}

/// A sidecar that cannot be imported says so, and the crawl goes on.
#[tokio::test(flavor = "multi_thread")]
async fn a_broken_sidecar_is_reported() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("One.zip"), "one.stl");
    std::fs::write(root.join("METADATA.7z"), b"not a 7z file").unwrap();
    let v = crawl(&lib, &ed, &root).await;
    assert!(v["log"].to_string().contains("cannot import METADATA.7z"), "{v}");
    assert_eq!(scalar(&lib, "SELECT count(*) FROM models"), 1);
}

/// One member that cannot be read (here flagged encrypted; the same goes for a
/// RAR without UnRAR or a ZIP method the reader lacks) must not cost the file list.
#[tokio::test(flavor = "multi_thread")]
async fn an_unreadable_image_keeps_the_catalog() {
    let (lib, ed) = library();
    let root = lib.dir.path().join("archives");
    std::fs::create_dir_all(&root).unwrap();
    let path = root.join("Dragon.zip");
    {
        let mut z = zip::ZipWriter::new(std::fs::File::create(&path).unwrap());
        let o = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
        z.start_file("dragon.stl", o).unwrap();
        z.write_all(b"solid x\nendsolid\n").unwrap();
        z.start_file("render.jpg", o).unwrap();
        z.write_all(b"not really a jpeg").unwrap();
        z.finish().unwrap();
    }
    // Set the "encrypted" flag of render.jpg in the central directory.
    let mut bytes = std::fs::read(&path).unwrap();
    let directory: Vec<usize> = bytes.windows(4).enumerate().filter(|(_, w)| *w == b"PK\x01\x02").map(|(i, _)| i).collect();
    bytes[directory[1] + 8] |= 1;
    std::fs::write(&path, &bytes).unwrap();

    let v = crawl(&lib, &ed, &root).await;
    assert!(v["log"].to_string().contains("images not read"), "{v}");
    assert_eq!(scalar(&lib, "SELECT count(*) FROM stl_files"), 1);
    let warning: Option<String> = lib.state.db.with(|c| c.query_row("SELECT warning FROM models", [], |r| r.get(0))).unwrap();
    assert_eq!(warning, None, "it has images and printable files");

    // Generate Thumbnails must not take this for an archive without images.
    let id = scalar(&lib, "SELECT id FROM models");
    lib.state.db.with(|c| c.execute("INSERT INTO images (model_id, filename, thumbnail_path) VALUES (?1, 'kept.jpg', '1')", [id])).unwrap();
    run_job(&lib, &ed, json!({"kind": "generate_thumbnails", "ids": [id]})).await;
    assert_eq!(scalar(&lib, "SELECT count(*) FROM images"), 1, "the images it has stay");
}
