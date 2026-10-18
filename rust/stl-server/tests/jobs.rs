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
    let mut events = lib.state.jobs.events.subscribe();

    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    let v = wait(&lib, &ed, id).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    assert_eq!(v["job"]["message"], "2 new, 0 updated, 0 unchanged");
    assert_eq!(v["job"]["user"], "ed");
    assert!(!v["log"].as_array().unwrap().is_empty());
    assert_eq!(count(&lib, "SELECT count(*) FROM models"), 2);
    assert_eq!(count(&lib, "SELECT count(*) FROM models m JOIN sculptors s ON s.id = m.sculptor_id WHERE s.name = 'Studio'"), 2);
    assert_eq!(count(&lib, "SELECT count(*) FROM images WHERE thumbnail_path IS NOT NULL"), 2);
    let ids: Vec<u64> = lib.state.db.with(|c| {
        let mut st = c.prepare("SELECT id FROM images")?;
        st.query_map([], |r| r.get::<_, i64>(0).map(|i| i as u64))?.collect::<rusqlite::Result<Vec<_>>>()
    }).unwrap();
    assert_eq!(lib.state.thumbs.get_many(&ids).unwrap().len(), 2, "thumbnails stored");
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
    let folders = call(&lib.state, "GET", "/api/crawled-folders", Some(&ed), None).await.json();
    assert_eq!(folders[0]["pattern"], "{sculptor}/{title}");
    let id = submit(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    assert_eq!(wait(&lib, &ed, id).await["job"]["message"], "0 new, 0 updated, 2 unchanged");

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
    assert!(v["job"]["message"].as_str().unwrap().contains("MB"));

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
