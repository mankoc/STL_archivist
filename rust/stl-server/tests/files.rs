//! Server files: browsing within roots, archive downloads, client paths.

mod common;

use std::sync::Arc;

use axum::http::{StatusCode, header};
use common::{LOCAL, REMOTE, call, empty_library, request, send_from};
use serde_json::json;
use stl_server::accounts::Role;
use stl_server::config::PathMap;
use stl_server::{Config, Library};

#[tokio::test]
async fn editors_browse_inside_roots_admins_anywhere() {
    let lib = empty_library();
    let s = &lib.state;
    let ed = lib.user("ed", Role::Editor);
    let admin = lib.user("admin", Role::Admin);
    let viewer = lib.user("vi", Role::Viewer);
    let root = lib.dir.path();
    std::fs::create_dir_all(root.join("STL").join("Studio")).unwrap();
    std::fs::write(root.join("STL").join("b.zip"), b"x").unwrap();
    std::fs::write(root.join("STL").join("A.7z"), b"xyz").unwrap();
    std::fs::write(root.join("STL").join("notes.txt"), b"x").unwrap();
    std::fs::write(root.join("STL").join("METADATA.7z"), b"sidecar").unwrap();

    let uri = |p: &std::path::Path| format!("/api/files/browse?path={}", urlencode(&p.to_string_lossy()));
    let r = call(s, "GET", &uri(&root.join("STL")), Some(&ed), None).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    let v = r.json();
    let names: Vec<&str> = v["entries"].as_array().unwrap().iter().map(|e| e["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["Studio", "A.7z", "b.zip"], "folders first, then archives; other files and the sidecar hidden");
    assert_eq!(v["entries"][1]["size"], 3);
    assert!(v["parent"].is_string(), "the root itself is inside the roots");

    // The root's parent is outside: editors may not go there, admins may.
    let outside = root.parent().unwrap();
    assert_eq!(call(s, "GET", &uri(outside), Some(&ed), None).await.status, StatusCode::FORBIDDEN);
    let escape = root.join("STL").join("..").join("..");
    assert_eq!(call(s, "GET", &uri(&escape), Some(&ed), None).await.status, StatusCode::FORBIDDEN, "no escape through ..");
    assert_eq!(call(s, "GET", &uri(outside), Some(&admin), None).await.status, StatusCode::OK);
    assert_eq!(call(s, "GET", &uri(&root.join("STL")), Some(&viewer), None).await.status, StatusCode::FORBIDDEN);
    assert_eq!(call(s, "GET", &uri(&root.join("nope")), Some(&ed), None).await.status, StatusCode::NOT_FOUND);

    // Starting points: the roots (and drives for admins).
    let v = call(s, "GET", "/api/files/browse", Some(&ed), None).await.json();
    assert_eq!(v["entries"].as_array().unwrap().len(), 1);
    let v = call(s, "GET", "/api/files/browse", Some(&admin), None).await.json();
    assert!(v["entries"].as_array().unwrap().len() >= 2);

    // Crawling follows the same rule.
    let body = json!({"kind": "crawl", "folder": outside.to_string_lossy()});
    assert_eq!(call(s, "POST", "/api/jobs", Some(&ed), Some(body)).await.status, StatusCode::FORBIDDEN);
}

fn urlencode(s: &str) -> String {
    s.bytes().map(|b| if b.is_ascii_alphanumeric() || b"-._~/".contains(&b) { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

#[tokio::test]
async fn archives_download_and_client_paths() {
    let dir = tempfile::tempdir().unwrap();
    let archive = dir.path().join("STL").join("Drachen Büste.zip");
    std::fs::create_dir_all(archive.parent().unwrap()).unwrap();
    std::fs::write(&archive, b"PK-archive-bytes").unwrap();
    let config = Config {
        data_dir: dir.path().to_path_buf(),
        bind: "127.0.0.1:0".parse().unwrap(),
        unrar: None,
        onnxruntime: None,
        pdfium: None,
        roots: vec![],
        path_map: vec![PathMap { server: dir.path().join("STL").to_string_lossy().into_owned(), client: r"\\nas\stl".into() }],
        web_dir: None,
    };
    let state = Arc::new(Library::open(config).unwrap());
    let id = state
        .db
        .with(|c| {
            c.execute(
                "INSERT INTO models (title, archive_path, archive_hash, is_missing, to_print) VALUES ('t', ?1, 'h', 0, 0)",
                [archive.to_string_lossy()],
            )?;
            Ok::<_, rusqlite::Error>(c.last_insert_rowid())
        })
        .unwrap();
    let token = state.accounts.with(|c| {
        let u = stl_server::accounts::create_user(c, "vi", "password123", Role::Viewer)?;
        stl_server::accounts::create_session(c, u.id)
    }).unwrap();

    let r = send_from(&state, LOCAL, request("GET", &format!("/api/models/{id}/archive"), Some(&token), None)).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.body, b"PK-archive-bytes");
    assert_eq!(r.headers[header::CONTENT_LENGTH], "16");
    let disp = r.headers[header::CONTENT_DISPOSITION].to_str().unwrap();
    assert!(disp.contains("filename=\"Drachen B_ste.zip\"") && disp.contains("filename*=UTF-8''Drachen%20B%C3%BCste.zip"), "{disp}");

    let local = send_from(&state, LOCAL, request("GET", &format!("/api/models/{id}"), Some(&token), None)).await.json();
    assert_eq!(local["client_path"], archive.to_string_lossy().as_ref(), "a local client opens the path itself");
    let remote = send_from(&state, REMOTE, request("GET", &format!("/api/models/{id}"), Some(&token), None)).await.json();
    assert_eq!(remote["client_path"], r"\\nas\stl\Drachen Büste.zip", "a remote client gets the mapped path");

    std::fs::remove_file(&archive).unwrap();
    let r = send_from(&state, LOCAL, request("GET", &format!("/api/models/{id}/archive"), Some(&token), None)).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    assert_eq!(call(&state, "GET", "/api/models/999/archive", Some(&token), None).await.status, StatusCode::NOT_FOUND);
}
