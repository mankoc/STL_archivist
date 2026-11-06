//! Damaged input and hostile requests must not take the server down, and files
//! named in decomposed Unicode are found where they are.

mod common;

use std::io::Write;
use std::time::Duration;

use axum::http::{StatusCode, header};
use common::{LOCAL, TestLib, call, empty_library, request, send_from};
use serde_json::{Value, json};
use stl_server::accounts::Role;

/// A ZIP whose first entry claims 2^62 compressed bytes, through its ZIP64 field.
/// A second entry at the same offset leaves the overlap check nothing to compare.
fn zip_claiming_a_huge_member() -> Vec<u8> {
    let name = b"a.jpg";
    let mut z = Vec::new();
    z.extend(b"PK\x03\x04");
    z.extend([45, 0, 0, 0, 0, 0, 0, 0, 0, 0]); // version, flags, method, time, date
    z.extend([0; 4]); // CRC
    z.extend(u32::MAX.to_le_bytes());
    z.extend(u32::MAX.to_le_bytes());
    z.extend((name.len() as u16).to_le_bytes());
    z.extend([0, 0]);
    z.extend(name);
    z.push(b'x');
    let directory = z.len() as u32;
    let entry = |z: &mut Vec<u8>, name: &[u8]| {
        z.extend(b"PK\x01\x02");
        z.extend([45, 0, 45, 0, 0, 0, 0, 0, 0, 0, 0, 0]); // versions, flags, method, time, date
        z.extend([0; 4]); // CRC
        z.extend(u32::MAX.to_le_bytes());
        z.extend(u32::MAX.to_le_bytes());
        z.extend((name.len() as u16).to_le_bytes());
        z.extend(20u16.to_le_bytes()); // extra
        z.extend([0; 2 + 2 + 2 + 4]); // comment, disk, attributes
        z.extend(0u32.to_le_bytes()); // local header offset
        z.extend(name);
        z.extend(1u16.to_le_bytes()); // ZIP64: file size, compressed size
        z.extend(16u16.to_le_bytes());
        z.extend(1u64.to_le_bytes());
        z.extend((1u64 << 62).to_le_bytes());
    };
    entry(&mut z, name);
    entry(&mut z, b"b.jpg");
    let size = z.len() as u32 - directory;
    z.extend(b"PK\x05\x06");
    z.extend([0; 4]);
    z.extend(2u16.to_le_bytes());
    z.extend(2u16.to_le_bytes());
    z.extend(size.to_le_bytes());
    z.extend(directory.to_le_bytes());
    z.extend([0, 0]);
    z
}

#[test]
fn a_member_claiming_an_impossible_size_is_left_out() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("damaged.zip");
    std::fs::write(&path, zip_claiming_a_huge_member()).unwrap();
    assert_eq!(stl_core::archive::list_members(&path).unwrap().len(), 2, "the catalog itself reads");
    let read = stl_core::archive::read_members(&path, &["a.jpg".to_string()]);
    assert!(read.is_ok_and(|r| r.is_empty()), "no data, and above all no crash");
}

/// No account has such a name: it is refused without being remembered.
#[tokio::test(flavor = "multi_thread")]
async fn sign_ins_with_absurd_names_are_not_kept() {
    let lib = empty_library();
    let name = "x".repeat(10_000);
    for _ in 0..7 {
        let r = call(&lib.state, "POST", "/api/auth/login", None, Some(json!({"name": name, "password": "wrong"}))).await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    }
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

fn write_zip(path: &std::path::Path) {
    let mut z = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
    z.start_file("model.stl", zip::write::SimpleFileOptions::default()).unwrap();
    z.write_all(b"solid x\nendsolid\n").unwrap();
    z.finish().unwrap();
}

/// Records store names composed (NFC); the file keeps the form it was written in.
#[tokio::test(flavor = "multi_thread")]
async fn delete_files_finds_a_decomposed_name() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false, "allow_file_delete": true, "use_trash_folder": false}"#).unwrap();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    std::fs::create_dir_all(&root).unwrap();
    let file = root.join("Cafe\u{301}.zip");
    write_zip(&file);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let id: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models", [], |r| r.get(0))).unwrap();

    run(&lib, &ed, json!({"kind": "delete_files", "ids": [id]})).await;
    assert!(!file.exists(), "the record went, so the file must too");
}

#[tokio::test(flavor = "multi_thread")]
async fn sort_files_finds_a_decomposed_name() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false, "file_sorting_enabled": true}"#).unwrap();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    std::fs::create_dir_all(root.join("sorted")).unwrap();
    let file = root.join("Cafe\u{301}.zip");
    write_zip(&file);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let id: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models", [], |r| r.get(0))).unwrap();
    let target = root.join("sorted").to_string_lossy().into_owned();
    run(&lib, &ed, json!({"kind": "sort_files", "ids": [id], "target_dir": target, "pattern": "Moved"})).await;
    assert!(!file.exists() && root.join("sorted").join("Moved.zip").exists(), "the archive moved");
}

#[tokio::test(flavor = "multi_thread")]
async fn answers_are_compressed_but_not_archives() {
    let lib = empty_library();
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"metadata_sidecar_enabled": false}"#).unwrap();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    std::fs::create_dir_all(&root).unwrap();
    write_zip(&root.join("One.zip"));
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let id: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models", [], |r| r.get(0))).unwrap();

    let get = |method: &'static str, uri: String, body: Option<Value>| {
        let mut req = request(method, &uri, Some(&ed), body);
        req.headers_mut().insert(header::ACCEPT_ENCODING, "gzip".parse().unwrap());
        send_from(&lib.state, LOCAL, req)
    };
    let tree = get("POST", "/api/filter-tree".into(), Some(json!({"show": ["path"], "counts": true}))).await;
    assert_eq!(tree.headers.get(header::CONTENT_ENCODING).map(|v| v.to_str().unwrap()), Some("gzip"));
    let archive = get("GET", format!("/api/models/{id}/archive"), None).await;
    assert_eq!(archive.status, StatusCode::OK);
    assert!(archive.headers.get(header::CONTENT_ENCODING).is_none(), "an archive is compressed already");
    assert!(archive.headers.get(header::CONTENT_LENGTH).is_some(), "and its download keeps its size");
}

/// A ZIP of stored members, those named in `locked` marked as encrypted.
fn zip_with_locked(names: &[&str], locked: &[&str]) -> Vec<u8> {
    let stored = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    let mut z = zip::ZipWriter::new(std::io::Cursor::new(Vec::new()));
    for name in names {
        z.start_file(*name, stored).unwrap();
        z.write_all(b"image").unwrap();
    }
    let mut bytes = z.finish().unwrap().into_inner();
    let entries: Vec<usize> = bytes.windows(4).enumerate().filter(|(_, w)| *w == b"PK\x01\x02").map(|(i, _)| i).collect();
    for at in entries {
        let len = u16::from_le_bytes([bytes[at + 28], bytes[at + 29]]) as usize;
        let name = std::str::from_utf8(&bytes[at + 46..at + 46 + len]).unwrap();
        if locked.contains(&name) {
            bytes[at + 8] |= 1;
        }
    }
    bytes
}

#[test]
fn an_encrypted_member_leaves_the_others_readable() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("some.zip");
    std::fs::write(&path, zip_with_locked(&["a.jpg", "b.jpg", "c.jpg"], &["b.jpg"])).unwrap();
    let wanted: Vec<String> = ["a.jpg", "b.jpg", "c.jpg"].map(String::from).to_vec();
    let read = stl_core::archive::read_members(&path, &wanted).unwrap();
    assert_eq!(read.iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(), ["a.jpg", "c.jpg"]);
}

/// With nothing read, callers must hear why: "no images" would be a different answer.
#[test]
fn an_archive_with_nothing_readable_is_still_an_error() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("all.zip");
    std::fs::write(&path, zip_with_locked(&["a.jpg", "b.jpg"], &["a.jpg", "b.jpg"])).unwrap();
    let wanted: Vec<String> = ["a.jpg", "b.jpg"].map(String::from).to_vec();
    assert!(stl_core::archive::read_members(&path, &wanted).is_err());
}

/// The desktop app stops its local server by closing its standard input, which
/// runs the shutdown that exports pending sidecars, where killing it would not.
#[test]
fn closing_standard_input_stops_the_server_cleanly() {
    use std::io::BufRead;
    use std::process::{Command, Stdio};
    let dir = tempfile::tempdir().unwrap();
    let mut server = Command::new(env!("CARGO_BIN_EXE_stl-server"))
        .args(["--data-dir", &dir.path().to_string_lossy(), "--bind", "127.0.0.1:0"])
        .env("STL_STOP_WITH_STDIN", "1")
        .env("NO_COLOR", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let output = std::io::BufReader::new(server.stdout.take().unwrap());
    let mut lines = output.lines();
    assert!(lines.by_ref().map_while(Result::ok).any(|l| l.contains("listening on")), "the server started");
    std::thread::spawn(move || lines.for_each(drop));

    drop(server.stdin.take());
    for _ in 0..200 {
        if let Some(status) = server.try_wait().unwrap() {
            assert!(status.success(), "{status}");
            return;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    server.kill().unwrap();
    panic!("the server kept running");
}
