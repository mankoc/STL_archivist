//! Jobs over HTTP (crawl, maintenance, cancel) and the event stream.

mod common;

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;
use stl_server::jobs::ServerEvent;

fn jpeg(rgb: [u8; 3]) -> Vec<u8> {
    let img = image::RgbImage::from_pixel(80, 60, image::Rgb(rgb));
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

/// `<dir>/archives/Studio/{Dragon,Knight}.zip`, each with an STL and a render.
fn archives(lib: &TestLib) -> std::path::PathBuf {
    let root = lib.dir.path().join("archives");
    for (name, rgb) in [("Dragon", [200, 30, 30]), ("Knight", [30, 30, 200])] {
        write_zip(
            &root.join("Studio").join(format!("{name}.zip")),
            &[(&format!("{name}.stl"), b"solid x\nendsolid\n"), ("render.png", &jpeg(rgb))],
        );
    }
    root
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

async fn submit(lib: &TestLib, token: &str, body: Value) -> u64 {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    r.json()["id"].as_u64().unwrap()
}

fn count(lib: &TestLib, sql: &str) -> i64 {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn crawl_job_catalogues_a_folder() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = archives(&lib);
    // An image beside the archives: each new record takes it too (stored with the batch's thumbnails).
    std::fs::write(root.join("Studio").join("cover.jpg"), jpeg([30, 200, 30])).unwrap();
    let mut events = lib.state.jobs.events.subscribe();

    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    let v = wait(&lib, &ed, id).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    assert_eq!(v["job"]["message"], "2 new, 0 updated, 0 unchanged");
    assert_eq!(v["job"]["user"], "ed");
    assert!(!v["log"].as_array().unwrap().is_empty());
    assert_eq!(count(&lib, "SELECT count(*) FROM models"), 2);
    assert_eq!(count(&lib, "SELECT count(*) FROM models m JOIN sculptors s ON s.id = m.sculptor_id WHERE s.name = 'Studio'"), 2);
    assert_eq!(count(&lib, "SELECT count(*) FROM images WHERE thumbnail_path IS NOT NULL"), 4);
    assert_eq!(count(&lib, "SELECT count(*) FROM images WHERE filename LIKE '%cover.jpg'"), 2);
    // Every record keeps its archive's file list: each member with its own CRC.
    assert_eq!(count(&lib, "SELECT count(DISTINCT model_id) FROM archive_files"), 2);
    assert_eq!(count(&lib, "SELECT count(*) FROM archive_files WHERE crc = 0 AND size > 0"), 0);
    let ids: Vec<u64> = lib.state.db.with(|c| {
        let mut st = c.prepare("SELECT id FROM images")?;
        st.query_map([], |r| r.get::<_, i64>(0).map(|i| i as u64))?.collect::<rusqlite::Result<Vec<_>>>()
    }).unwrap();
    assert_eq!(lib.state.thumbs.get_many(&ids).unwrap().len(), 4, "thumbnails stored");
    assert!(root.join("Studio").join("METADATA.7z").is_file(), "sidecar written");

    // Events: the job went queued → running → done, and the library changed.
    let mut states = Vec::new();
    let mut changed = false;
    while let Ok(e) = events.try_recv() {
        match e {
            ServerEvent::Job { job } if job.id == id => states.push(format!("{:?}", job.state)),
            ServerEvent::LibraryChanged { what } => changed |= what == "crawl",
            _ => {}
        }
    }
    states.dedup();
    assert_eq!(states.first().map(String::as_str), Some("Queued"));
    assert_eq!(states.last().map(String::as_str), Some("Done"));
    assert!(changed);

    // The folder is tracked with its pattern; a second crawl finds nothing new.
    let libs = call(&lib.state, "GET", "/api/libraries", Some(&ed), None).await.json();
    assert_eq!(libs[0]["folders"][0]["pattern"], "{sculptor}/{title}");
    let files = count(&lib, "SELECT count(*) FROM archive_files");
    lib.state.db.with(|c| c.execute("DELETE FROM archive_files", [])).unwrap();
    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    assert_eq!(wait(&lib, &ed, id).await["job"]["message"], "0 new, 0 updated, 2 unchanged");
    assert_eq!(count(&lib, "SELECT count(*) FROM archive_files"), 0, "a crawl does not open unchanged archives");
    let id = submit(&lib, &ed, json!({"kind": "read_file_lists"})).await;
    wait(&lib, &ed, id).await;
    assert_eq!(count(&lib, "SELECT count(*) FROM archive_files"), files, "Read missing file lists gives them back");

    // Another spelling of the same folder is crawled under the stored one,
    // and so is a folder inside it, rather than duplicating every record.
    let other = format!("{}/./", root.to_string_lossy().replace('\\', "/"));
    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": other})).await;
    let v = wait(&lib, &ed, id).await;
    assert_eq!(v["job"]["message"], "0 new, 0 updated, 2 unchanged");
    assert_eq!(v["job"]["request"]["folder"], json!(root.to_string_lossy()));
    let sub = format!("{}/Studio", root.to_string_lossy().replace('\\', "/"));
    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": sub})).await;
    let v = wait(&lib, &ed, id).await;
    assert_eq!(v["job"]["request"]["folder"], json!(root.join("Studio").to_string_lossy()));
    assert_eq!(count(&lib, "SELECT count(*) FROM models"), 2);
}

#[tokio::test(flavor = "multi_thread")]
async fn images_beside_an_archive_count_as_its_images() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Bare").join("Golem.zip"), &[("golem.stl", b"solid g\nendsolid\n")]);
    write_zip(&root.join("Lonely").join("Imp.zip"), &[("imp.stl", b"solid i\nendsolid\n")]);
    std::fs::write(root.join("Bare").join("cover.jpg"), jpeg([90, 90, 90])).unwrap();
    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    assert_eq!(wait(&lib, &ed, id).await["job"]["state"], "done");
    let flagged = |title: &str| {
        count(&lib, &format!("SELECT count(*) FROM models WHERE title = '{title}' AND warning LIKE '%Archive without images%'"))
    };
    assert_eq!((flagged("Golem"), flagged("Imp")), (0, 1), "the cover beside Golem counts");
    // A rescan keeps it so.
    let golem: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models WHERE title = 'Golem'", [], |r| r.get(0))).unwrap();
    let id = submit(&lib, &ed, json!({"kind": "rescan", "ids": [golem]})).await;
    assert_eq!(wait(&lib, &ed, id).await["job"]["state"], "done");
    assert_eq!(flagged("Golem"), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn recrawls_keep_unchanged_archives_and_skip_known_sidecars() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = archives(&lib);
    let crawl = || async {
        let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
        let v = wait(&lib, &ed, id).await;
        assert_eq!(v["job"]["state"], "done", "{v}");
        v
    };
    crawl().await;
    assert!(root.join("Studio").join("METADATA.7z").is_file());

    // A stored time that differs from the file (touched, copied, another file system): the
    // catalog is read once, found unchanged, and the file's time is kept for the next crawl.
    lib.state.db.with(|c| c.execute("UPDATE models SET archive_mtime_ns = 1", [])).unwrap();
    let v = crawl().await;
    assert_eq!(v["job"]["message"], "0 new, 0 updated, 2 unchanged");
    assert_eq!(count(&lib, "SELECT count(*) FROM models WHERE archive_mtime_ns = 1"), 0);

    // Every archive known: the sidecar is not read. One record gone: it comes back from it.
    assert!(!v["log"].to_string().contains("METADATA.7z in"), "{v}");
    let dragon: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models WHERE title = 'Dragon'", [], |r| r.get(0))).unwrap();
    lib.state.db.with_tx(|tx| stl_core::storage::browser::delete_models(tx, &[dragon])).unwrap();
    let v = crawl().await;
    assert!(v["log"].to_string().contains("Imported 1 record(s) from METADATA.7z in Studio"), "{v}");
    assert_eq!(count(&lib, "SELECT count(*) FROM models WHERE title = 'Dragon'"), 1);
}

#[tokio::test(flavor = "multi_thread")]
async fn missing_file_lists_are_read_by_their_job_not_by_crawls() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = archives(&lib);
    let crawl = || async {
        let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
        let v = wait(&lib, &ed, id).await;
        assert_eq!(v["job"]["state"], "done", "{v}");
    };
    crawl().await;
    // Records crawled while the STL list was optional (and before file lists were kept).
    lib.state
        .db
        .with(|c| c.execute_batch("DELETE FROM stl_files; DELETE FROM archive_files; UPDATE models SET stl_list_hash = NULL;"))
        .unwrap();
    // A crawl opens only new and changed archives: these keep no lists.
    crawl().await;
    assert_eq!(count(&lib, "SELECT count(*) FROM archive_files"), 0);
    // Read missing file lists reads them, once.
    let id = submit(&lib, &ed, json!({"kind": "read_file_lists"})).await;
    let v = wait(&lib, &ed, id).await;
    assert_eq!(v["job"]["message"], "2 read, 0 unreadable or missing", "{v}");
    assert_eq!(count(&lib, "SELECT count(*) FROM stl_files"), 2, "one .stl in each archive");
    assert_eq!(count(&lib, "SELECT count(DISTINCT model_id) FROM archive_files"), 2);
    assert_eq!(count(&lib, "SELECT count(*) FROM models WHERE stl_list_hash IS NOT archive_hash"), 0);
    let id = submit(&lib, &ed, json!({"kind": "read_file_lists"})).await;
    assert_eq!(wait(&lib, &ed, id).await["job"]["message"], "0 read, 0 unreadable or missing");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_new_library_takes_archives_as_their_sidecars_describe_them() {
    // The first library crawls the folder and writes its sidecars (with each archive's size).
    let first = empty_library();
    let ed = first.user("ed", Role::Editor);
    let root = archives(&first);
    let id = submit(&first, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    wait(&first, &ed, id).await;
    assert!(root.join("Studio").join("METADATA.7z").is_file());

    // Another library (another machine) crawls the same folder: its records come from the
    // sidecar. Dragon is spoiled at its size: were it opened, it would be unreadable; Knight
    // has another size than the sidecar says: it changed, so it is read.
    let dragon = root.join("Studio").join("Dragon.zip");
    let size = std::fs::metadata(&dragon).unwrap().len() as usize;
    std::fs::write(&dragon, vec![0u8; size]).unwrap();
    std::fs::write(root.join("Studio").join("Knight.zip"), b"not a zip any more").unwrap();
    let second = empty_library();
    let admin = second.user("admin", Role::Admin);
    let id = submit(&second, &admin, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let v = wait(&second, &admin, id).await;
    assert!(v["log"].to_string().contains("Imported 2 record(s) from METADATA.7z"), "{v}");
    let unreadable = |name: &str| {
        count(&second, &format!("SELECT count(*) FROM models WHERE rel_path LIKE '%{name}' AND warning LIKE '%Unreadable archive%'"))
    };
    assert_eq!((unreadable("Dragon.zip"), unreadable("Knight.zip")), (0, 1), "Dragon not opened, Knight read");
    // Its size and time are kept: the next crawl leaves it alone too.
    assert_eq!(count(&second, "SELECT count(*) FROM models WHERE rel_path LIKE '%Dragon.zip' AND archive_mtime_ns IS NOT NULL"), 1);
}

#[tokio::test(flavor = "multi_thread")]
async fn size_only_change_detection_ignores_times() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = archives(&lib);
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"detect_changes_by_size": true}"#).unwrap();
    let crawl = || async {
        let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
        let v = wait(&lib, &ed, id).await;
        assert_eq!(v["job"]["state"], "done", "{v}");
    };
    crawl().await;
    // Times that differ but sizes that match: not read again, so the stored times stay.
    lib.state.db.with(|c| c.execute("UPDATE models SET archive_mtime_ns = 1", [])).unwrap();
    crawl().await;
    assert_eq!(count(&lib, "SELECT count(*) FROM models WHERE archive_mtime_ns = 1"), 2);
    let v = call(&lib.state, "GET", "/api/settings/library", Some(&ed), None).await.json();
    assert_eq!(v["detect_changes_by_size"], true);
}

#[tokio::test(flavor = "multi_thread")]
async fn empty_archives_are_read_and_old_failures_retried() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    std::fs::create_dir_all(&root).unwrap();
    // What 7-Zip writes for an archive of nothing (32 bytes).
    let mut empty = vec![0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c, 0x00, 0x04, 0x8d, 0x9b, 0xd5, 0x0f];
    empty.resize(32, 0);
    std::fs::write(root.join("Nothing.7z"), &empty).unwrap();
    let crawl = || async {
        let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
        let v = wait(&lib, &ed, id).await;
        assert_eq!(v["job"]["state"], "done", "{v}");
        v
    };
    let unreadable = "SELECT count(*) FROM models WHERE archive_hash LIKE 'fallback:%' OR warning LIKE '%Unreadable archive%'";
    let v = crawl().await;
    assert_eq!(count(&lib, unreadable), 0, "read as an archive without files");
    assert!(!v["log"].to_string().contains("cannot read archive"), "{v}");
    // A record an older version could not read is read again, though the file did not change.
    lib.state
        .db
        .with(|c| c.execute("UPDATE models SET archive_hash = 'fallback:32:1', warning = 'Unreadable archive'", []))
        .unwrap();
    crawl().await;
    assert_eq!(count(&lib, unreadable), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn maintenance_jobs() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let admin = lib.user("admin", Role::Admin);
    let root = archives(&lib);
    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    wait(&lib, &ed, id).await;

    // Verify: one archive disappears.
    std::fs::remove_file(root.join("Studio").join("Knight.zip")).unwrap();
    let id = submit(&lib, &ed, json!({"kind": "verify_archives"})).await;
    let v = wait(&lib, &ed, id).await;
    assert_eq!(v["job"]["state"], "done");
    assert_eq!(v["job"]["message"], "2 checked: 1 Missing, 1 OK; 0 back on disk");
    assert_eq!((v["job"]["done"].clone(), v["job"]["total"].clone()), (json!(2), json!(2)));

    // Perceptual hashes from the stored thumbnails.
    let id = submit(&lib, &ed, json!({"kind": "recompute_dhashes", "all": true})).await;
    assert_eq!(wait(&lib, &ed, id).await["job"]["message"], "2 computed, 0 skipped (no thumbnail or not decodable)");
    assert_eq!(count(&lib, "SELECT count(*) FROM images WHERE dhash LIKE '2:%' AND length(dhash) = 34"), 2);

    // Prune to zero images per model.
    let id = submit(&lib, &ed, json!({"kind": "prune_images", "keep": 0})).await;
    assert_eq!(wait(&lib, &ed, id).await["job"]["message"], "2 image(s) removed from 2 model(s)");
    assert_eq!(count(&lib, "SELECT count(*) FROM images"), 0);

    // Compacting needs an administrator.
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "compact_database"}))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    let id = submit(&lib, &admin, json!({"kind": "compact_database"})).await;
    let v = wait(&lib, &admin, id).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    let message = v["job"]["message"].as_str().unwrap();
    assert!(message.starts_with("database ") && message.contains("; thumbnails "), "{message}");
    // The pruned images' thumbnails are gone from the store, and it still works.
    let stats = lib.state.thumbs.stats().unwrap();
    assert_eq!((stats.thumbnails, stats.blobs), (0, 0), "{stats:?}");
    lib.state.thumbs.put_many(&[(999, b"after".as_slice())]).unwrap();
    assert_eq!(lib.state.thumbs.get_many(&[999]).unwrap().len(), 1);

    let jobs = call(&lib.state, "GET", "/api/jobs", Some(&ed), None).await.json();
    assert_eq!(jobs.as_array().unwrap().len(), 5);
}

#[tokio::test(flavor = "multi_thread")]
async fn jobs_queue_and_cancel() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let viewer = lib.user("vi", Role::Viewer);
    let root = archives(&lib);
    let r = call(&lib.state, "POST", "/api/jobs", Some(&viewer), Some(json!({"kind": "verify_archives"}))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    assert!(call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "nope"}))).await.status.is_client_error());

    let first = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let second = submit(&lib, &ed, json!({"kind": "verify_archives"})).await;
    // The second waits for the first; cancel it before it starts (or while running).
    assert_eq!(call(&lib.state, "DELETE", &format!("/api/jobs/{second}"), Some(&ed), None).await.status, StatusCode::ACCEPTED);
    assert_eq!(wait(&lib, &ed, first).await["job"]["state"], "done");
    assert_eq!(wait(&lib, &ed, second).await["job"]["state"], "cancelled");
    assert_eq!(call(&lib.state, "DELETE", &format!("/api/jobs/{second}"), Some(&ed), None).await.status, StatusCode::CONFLICT);
    assert_eq!(call(&lib.state, "DELETE", "/api/jobs/999", Some(&ed), None).await.status, StatusCode::NOT_FOUND);

    // A folder that does not exist is refused before a job is queued.
    let body = json!({"kind": "crawl", "folder": lib.dir.path().join("nowhere").to_string_lossy()});
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(body)).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    assert!(r.json()["error"].as_str().unwrap().contains("does not exist"));
}

#[tokio::test(flavor = "multi_thread")]
async fn websocket_streams_events() {
    use futures_util::StreamExt;
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = stl_server::app(lib.state.clone()).into_make_service_with_connect_info::<std::net::SocketAddr>();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    // Without a session the upgrade is refused.
    assert!(tokio_tungstenite::connect_async(format!("ws://{addr}/api/events")).await.is_err());

    let mut req = tokio_tungstenite::tungstenite::client::IntoClientRequest::into_client_request(format!("ws://{addr}/api/events")).unwrap();
    req.headers_mut().insert("cookie", format!("stl_session={ed}").parse().unwrap());
    let (mut ws, _) = tokio_tungstenite::connect_async(req).await.unwrap();
    tokio::time::sleep(Duration::from_millis(100)).await; // subscribed before the job starts

    lib.state.db.with(|c| c.execute("INSERT INTO tags (name) VALUES ('t')", [])).unwrap();
    let r = call(&lib.state, "POST", "/api/tags/merge", Some(&ed), Some(json!({"ids": [1], "target": "u"}))).await;
    assert_eq!(r.status, StatusCode::NO_CONTENT);
    let id = submit(&lib, &ed, json!({"kind": "verify_archives"})).await;

    let mut seen_change = false;
    let mut seen_done = false;
    while !(seen_change && seen_done) {
        let msg = tokio::time::timeout(Duration::from_secs(10), ws.next()).await.expect("an event").unwrap().unwrap();
        let v: Value = serde_json::from_str(msg.to_text().unwrap()).unwrap();
        match v["type"].as_str().unwrap() {
            "library_changed" if v["what"] == "lookups" => seen_change = true,
            "job" if v["job"]["id"] == id && v["job"]["state"] == "done" => seen_done = true,
            _ => {}
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn update_folders_keeps_records_moved_between_folders() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let (a, b) = (lib.dir.path().join("a"), lib.dir.path().join("b"));
    write_zip(&a.join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n")]);
    write_zip(&a.join("Gone.zip"), &[("gone.stl", b"solid g\nendsolid\n")]);
    write_zip(&b.join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n")]);
    for f in [&a, &b] {
        let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": f.to_string_lossy()})).await;
        wait(&lib, &ed, id).await;
    }
    lib.state.db.with(|c| c.execute("UPDATE models SET title = 'Edited' WHERE rel_path LIKE '%Dragon.zip'", [])).unwrap();
    std::fs::rename(a.join("Dragon.zip"), b.join("Dragon.zip")).unwrap();
    std::fs::remove_file(a.join("Gone.zip")).unwrap();

    let id = submit(&lib, &ed, json!({"kind": "update_folders"})).await;
    let v = wait(&lib, &ed, id).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    assert!(v["job"]["message"].as_str().unwrap().starts_with("2 folder(s)"), "{v}");
    assert_eq!(v["job"]["counts"], json!({"new": 0, "updated": 0, "deleted": 1}), "{v}");
    assert_eq!(v["changes"]["new"], json!([]), "Dragon moved: not new");
    assert_eq!(v["changes"]["deleted"], json!([a.join("Gone.zip").to_string_lossy()]), "{v}");
    assert_eq!(count(&lib, "SELECT count(*) FROM models"), 2, "Gone is deleted, Dragon kept once");
    let (title, path): (String, String) = lib
        .state
        .db
        .with(|c| c.query_row("SELECT title, archive_path FROM models_view WHERE archive_path LIKE '%Dragon.zip'", [], |r| Ok((r.get(0)?, r.get(1)?))))
        .unwrap();
    assert_eq!(title, "Edited");
    assert_eq!(path, b.join("Dragon.zip").to_string_lossy());
}

#[tokio::test(flavor = "multi_thread")]
async fn clear_library_needs_an_admin_and_confirmation() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let admin = lib.user("admin", Role::Admin);
    let root = archives(&lib);
    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    wait(&lib, &ed, id).await;
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "clear_library", "confirm": "DELETE"}))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    let r = call(&lib.state, "POST", "/api/jobs", Some(&admin), Some(json!({"kind": "clear_library", "confirm": "yes"}))).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    let id = submit(&lib, &admin, json!({"kind": "clear_library", "confirm": "DELETE"})).await;
    assert_eq!(wait(&lib, &admin, id).await["job"]["message"], "library cleared: 2 model(s) deleted");
    assert_eq!(count(&lib, "SELECT count(*) FROM models"), 0);
    assert_eq!(count(&lib, "SELECT count(*) FROM libraries"), 0);
    let stats = lib.state.thumbs.stats().unwrap();
    assert_eq!((stats.thumbnails, stats.blobs), (0, 0), "thumbnails dropped");
}
