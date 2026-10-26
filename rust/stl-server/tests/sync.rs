//! Sync between two servers over real HTTP: adding a remote (signing in),
//! previews, pull and push with path rules, only missing thumbnails sent, and
//! the refusals (an expired sign-in, a remote that is this server).

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

/// The remote's archives, wherever they are put.
fn archives(root: &Path) {
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n"), ("dragon.png", &png([200, 30, 30]))]);
    write_zip(&root.join("Studio").join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n"), ("knight.png", &png([30, 30, 200]))]);
}

/// Serve `lib` on a free local port; returns its address.
async fn serve(lib: &TestLib) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let app = stl_server::app(lib.state.clone()).into_make_service_with_connect_info::<std::net::SocketAddr>();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{addr}")
}

/// Run a job to its end; returns its detail.
async fn finish(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    let id = r.json()["id"].as_u64().unwrap();
    for _ in 0..600 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if !matches!(v["job"]["state"].as_str(), Some("queued" | "running")) {
            return v;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("job {id} did not finish");
}

async fn done(lib: &TestLib, token: &str, body: Value) -> Value {
    let v = finish(lib, token, body).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    v
}

async fn titles(lib: &TestLib) -> Vec<String> {
    lib.state
        .db
        .with(|c| c.prepare("SELECT title FROM models ORDER BY title")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<Vec<String>>>())
        .unwrap()
}

async fn thumbnails_resolve(lib: &TestLib, token: &str) {
    let ids: Vec<i64> =
        lib.state.db.with(|c| c.prepare("SELECT id FROM images ORDER BY id")?.query_map([], |r| r.get(0))?.collect::<rusqlite::Result<Vec<i64>>>()).unwrap();
    assert!(!ids.is_empty());
    for id in ids {
        let r = call(&lib.state, "GET", &format!("/api/images/{id}/thumbnail"), Some(token), None).await;
        let location = r.headers.get("location").and_then(|l| l.to_str().ok()).unwrap_or_default().to_string();
        let r = call(&lib.state, "GET", &location, Some(token), None).await;
        assert_eq!(r.status, StatusCode::OK, "image {id}: {location}");
    }
}

fn log(v: &Value) -> String {
    v["log"].as_array().unwrap().iter().filter_map(Value::as_str).collect::<Vec<_>>().join("\n")
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn pull_and_push_mirror_the_library() {
    // The remote: two archives with an image each.
    let remote = empty_library();
    let boss = remote.user("boss", Role::Admin);
    let remote_root = remote.dir.path().join("archives");
    archives(&remote_root);
    done(&remote, &boss, json!({"kind": "crawl", "folder": remote_root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    let url = serve(&remote).await;

    // This server: its own archive, whose image is also one of the remote's;
    // the remote's archives are copied here under another root.
    let local = empty_library();
    let me = local.user("me", Role::Admin);
    let ed = local.user("ed", Role::Editor);
    let own = local.dir.path().join("own");
    write_zip(&own.join("Orc.zip"), &[("orc.stl", b"solid o\nendsolid\n"), ("orc.png", &png([200, 30, 30]))]);
    done(&local, &me, json!({"kind": "crawl", "folder": own.to_string_lossy(), "pattern": "{title}"})).await;
    let mirror = local.dir.path().join("mirror");
    archives(&mirror);

    // Adding the remote signs in there; the token stays on this server.
    let bad = json!({"name": "nas", "url": url, "user": "boss", "password": "wrong"});
    assert_eq!(call(&local.state, "POST", "/api/sync/remotes", Some(&me), Some(bad)).await.status, StatusCode::BAD_GATEWAY);
    let add = json!({"name": "nas", "url": url, "user": "boss", "password": "password123"});
    assert_eq!(call(&local.state, "POST", "/api/sync/remotes", Some(&ed), Some(add.clone())).await.status, StatusCode::FORBIDDEN);
    let r = call(&local.state, "POST", "/api/sync/remotes", Some(&me), Some(add.clone())).await;
    assert_eq!(r.status, StatusCode::CREATED, "{:?}", r.json());
    let id = r.json()["id"].as_i64().unwrap();
    assert_eq!(call(&local.state, "POST", "/api/sync/remotes", Some(&me), Some(add)).await.status, StatusCode::CONFLICT);
    let listed = call(&local.state, "GET", "/api/sync/remotes", Some(&me), None).await.json();
    assert!(listed[0].get("token").is_none() && listed[0]["user"] == "boss", "{listed}");
    let rules = json!({"rules": [{"local": mirror.to_string_lossy(), "remote": remote_root.to_string_lossy()}]});
    assert_eq!(call(&local.state, "PATCH", &format!("/api/sync/remotes/{id}"), Some(&me), Some(rules)).await.status, StatusCode::OK);

    // Pull: the preview maps the remote's folder by the rule; nothing changes yet.
    let p = call(&local.state, "GET", &format!("/api/sync/remotes/{id}/preview?direction=pull"), Some(&me), None).await.json();
    assert_eq!(p["problem"], Value::Null, "{p}");
    assert_eq!(p["folders"][0]["root"], remote_root.to_string_lossy().as_ref());
    assert_eq!((p["folders"][0]["target"].as_str(), p["folders"][0]["exists"].as_bool()), (Some(mirror.to_string_lossy().as_ref()), Some(true)), "{p}");
    let r = call(&local.state, "POST", "/api/jobs", Some(&me), Some(json!({"kind": "sync", "remote": id, "direction": "pull"}))).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST, "needs the confirmation");
    let v = done(&local, &me, json!({"kind": "sync", "remote": id, "direction": "pull", "confirm": "REPLACE"})).await;
    assert!(log(&v).contains("1 of 2 thumbnail image(s) sent"), "the shared image stays home:\n{}", log(&v));
    assert_eq!(titles(&local).await, ["Dragon", "Knight"]);
    let libs = call(&local.state, "GET", "/api/libraries", Some(&me), None).await.json();
    assert_eq!(libs[0]["folders"][0]["root"], mirror.to_string_lossy().as_ref(), "{libs}");
    thumbnails_resolve(&local, &me).await;
    assert_eq!(call(&remote.state, "GET", "/api/backups", Some(&boss), None).await.json(), json!([]), "the remote's package is gone");
    let listed = call(&local.state, "GET", "/api/sync/remotes", Some(&me), None).await.json();
    assert!(listed[0]["last_result"].as_str().unwrap().starts_with("pulled from nas"), "{listed}");

    // An edit here, then push: the remote gets it at its own root, keeps its users.
    let knight = local.state.db.with(|c| c.query_row("SELECT id FROM models WHERE title = 'Knight'", [], |r| r.get::<_, i64>(0))).unwrap();
    let edit = json!({"edits": [{"id": knight, "field": "title", "value": "Knight (painted)"}]});
    assert_eq!(call(&local.state, "PATCH", "/api/models/cells", Some(&me), Some(edit)).await.status, StatusCode::OK);
    let p = call(&local.state, "GET", &format!("/api/sync/remotes/{id}/preview?direction=push"), Some(&me), None).await.json();
    assert_eq!((p["folders"][0]["target"].as_str(), p["folders"][0]["exists"].as_bool()), (Some(remote_root.to_string_lossy().as_ref()), Some(true)), "{p}");
    let v = done(&local, &me, json!({"kind": "sync", "remote": id, "direction": "push", "confirm": "REPLACE"})).await;
    assert!(log(&v).contains("0 of 2 thumbnail image(s) sent"), "the remote has them all:\n{}", log(&v));
    assert!(log(&v).contains("nas: "), "the remote's log is copied here");
    assert_eq!(titles(&remote).await, ["Dragon", "Knight (painted)"]);
    let libs = call(&remote.state, "GET", "/api/libraries", Some(&boss), None).await.json();
    assert_eq!(libs[0]["folders"][0]["root"], remote_root.to_string_lossy().as_ref());
    thumbnails_resolve(&remote, &boss).await;
    let names: Vec<String> = call(&remote.state, "GET", "/api/backups", Some(&boss), None)
        .await
        .json()
        .as_array()
        .unwrap()
        .iter()
        .map(|b| b["name"].as_str().unwrap().to_string())
        .collect();
    assert!(names.len() == 1 && names[0].ends_with("_pre_import.7z"), "only the remote's own backup stays: {names:?}");

    // Smart: both sides now hold the same rows, so an edit travels alone;
    // and this time the receiving side does not back up first.
    let backups = |lib: &TestLib| std::fs::read_dir(lib.dir.path().join("backups")).map(|d| d.count()).unwrap_or(0);
    let (local_backups, remote_backups) = (backups(&local), backups(&remote));
    let dragon = remote.state.db.with(|c| c.query_row("SELECT id FROM models WHERE title = 'Dragon'", [], |r| r.get::<_, i64>(0))).unwrap();
    let edit = json!({"edits": [{"id": dragon, "field": "title", "value": "Dragon (remote edit)"}]});
    assert_eq!(call(&remote.state, "PATCH", "/api/models/cells", Some(&boss), Some(edit)).await.status, StatusCode::OK);
    let v = done(&local, &me, json!({"kind": "sync", "remote": id, "direction": "pull", "confirm": "REPLACE", "backup_first": false})).await;
    assert!(log(&v).contains("only the changes"), "{}", log(&v));
    assert!(log(&v).contains("Applied the changes"), "{}", log(&v));
    assert_eq!(titles(&local).await, ["Dragon (remote edit)", "Knight (painted)"]);
    let libs = call(&local.state, "GET", "/api/libraries", Some(&me), None).await.json();
    assert_eq!(libs[0]["folders"][0]["root"], mirror.to_string_lossy().as_ref(), "the folder rule still applies");
    thumbnails_resolve(&local, &me).await;
    assert_eq!(backups(&local), local_backups, "no backup this time");

    let knight = local.state.db.with(|c| c.query_row("SELECT id FROM models WHERE title = 'Knight (painted)'", [], |r| r.get::<_, i64>(0))).unwrap();
    let edit = json!({"edits": [{"id": knight, "field": "title", "value": "Knight (varnished)"}]});
    assert_eq!(call(&local.state, "PATCH", "/api/models/cells", Some(&me), Some(edit)).await.status, StatusCode::OK);
    let v = done(&local, &me, json!({"kind": "sync", "remote": id, "direction": "push", "confirm": "REPLACE", "backup_first": false})).await;
    assert!(log(&v).contains("only the changes"), "{}", log(&v));
    assert_eq!(titles(&remote).await, ["Dragon (remote edit)", "Knight (varnished)"]);
    let libs = call(&remote.state, "GET", "/api/libraries", Some(&boss), None).await.json();
    assert_eq!(libs[0]["folders"][0]["root"], remote_root.to_string_lossy().as_ref());
    thumbnails_resolve(&remote, &boss).await;
    assert_eq!(backups(&remote), remote_backups);

    // A revoked sign-in fails the sync and asks to sign in again.
    local.state.accounts.with(|c| c.execute("UPDATE sync_remotes SET token = 'revoked'", [])).unwrap();
    let v = finish(&local, &me, json!({"kind": "sync", "remote": id, "direction": "pull", "confirm": "REPLACE"})).await;
    assert_eq!(v["job"]["state"], "failed");
    assert!(v["job"]["message"].as_str().unwrap().contains("sign in to it again"), "{v}");
    let again = json!({"user": "boss", "password": "password123"});
    assert_eq!(call(&local.state, "POST", &format!("/api/sync/remotes/{id}/login"), Some(&me), Some(again)).await.status, StatusCode::OK);
    assert_eq!(call(&local.state, "GET", &format!("/api/sync/remotes/{id}/preview?direction=pull"), Some(&me), None).await.status, StatusCode::OK);

    // A "remote" that is this server is refused (it would wait for itself).
    let here = serve(&local).await;
    let r = call(&local.state, "POST", "/api/sync/remotes", Some(&me), Some(json!({"name": "self", "url": here, "user": "me", "password": "password123"}))).await;
    let self_id = r.json()["id"].as_i64().unwrap();
    let v = finish(&local, &me, json!({"kind": "sync", "remote": self_id, "direction": "pull", "confirm": "REPLACE"})).await;
    assert_eq!(v["job"]["state"], "failed");
    assert!(v["job"]["message"].as_str().unwrap().contains("is this server itself"), "{v}");
}
