//! Archive tools on selected models: Show Files, Warning Details, Rescan,
//! Generate Thumbnails and Delete Files; the model viewer's files.

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

async fn finish(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    let id = r.json()["id"].as_u64().unwrap();
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if !matches!(v["job"]["state"].as_str(), Some("queued" | "running")) {
            return v;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("job {id} did not finish");
}

async fn run(lib: &TestLib, token: &str, body: Value) -> Value {
    let v = finish(lib, token, body).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    v
}

fn scalar<T: rusqlite::types::FromSql>(lib: &TestLib, sql: &str) -> T {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn files_warnings_rescan_and_thumbnails() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"store_stl_file_list": true, "max_thumbnails": 5}"#).unwrap();
    let root = lib.dir.path().join("archives");
    let (red, green) = (png([200, 0, 0]), png([0, 200, 0]));
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon_presupported.stl", b"solid d\nendsolid\n"), ("a.png", &red), ("b.png", &green)]);
    write_zip(&root.join("Studio").join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n")]);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    let dragon: i64 = scalar(&lib, "SELECT id FROM models WHERE title = 'Dragon'");
    let knight: i64 = scalar(&lib, "SELECT id FROM models WHERE title = 'Knight'");

    let files = call(&lib.state, "GET", &format!("/api/models/{dragon}/files"), Some(&ed), None).await.json();
    assert_eq!(files[0]["filename"], "dragon_presupported.stl", "{files}");

    // Every file of the archive, with the CRC-32 its catalog records.
    let all = call(&lib.state, "GET", &format!("/api/models/{dragon}/archive-files"), Some(&ed), None).await.json();
    let mut zip = zip::ZipArchive::new(std::fs::File::open(root.join("Studio").join("Dragon.zip")).unwrap()).unwrap();
    let mut want: Vec<Value> = (0..zip.len())
        .map(|i| {
            let f = zip.by_index(i).unwrap();
            json!({"name": f.name().unwrap(), "size": f.size(), "crc": format!("{:08x}", f.crc32())})
        })
        .collect();
    want.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    assert_eq!(all, Value::Array(want));

    // Warning Details: the presupported member, and an absence without members.
    let w = call(&lib.state, "GET", &format!("/api/models/{dragon}/warnings"), Some(&ed), None).await.json();
    assert_eq!(w["error"], Value::Null, "{w}");
    assert_eq!(w["sections"][0]["warning"], "Models with presupported stl");
    assert_eq!(w["sections"][0]["members"][0]["name"], "dragon_presupported.stl");
    let w = call(&lib.state, "GET", &format!("/api/models/{knight}/warnings"), Some(&ed), None).await.json();
    assert_eq!(w["sections"][0]["warning"], "Archive without images", "{w}");
    assert_eq!(w["sections"][0]["members"], json!([]));
    assert_eq!(call(&lib.state, "GET", "/api/models/9999/warnings", Some(&ed), None).await.status, StatusCode::NOT_FOUND);

    // Rescan picks up a changed archive: an image now, so the warning goes.
    write_zip(&root.join("Studio").join("Knight.zip"), &[("knight.stl", b"solid k2\nendsolid\n"), ("k.png", &red)]);
    let v = run(&lib, &ed, json!({"kind": "rescan", "ids": [knight]})).await;
    assert_eq!(v["job"]["message"], "1 of 1 model(s) updated");
    let warning: Option<String> = scalar(&lib, &format!("SELECT warning FROM models WHERE id = {knight}"));
    assert_eq!(warning, None);

    // Generate Thumbnails, at most one per model this time.
    let v = run(&lib, &ed, json!({"kind": "generate_thumbnails", "ids": [dragon, knight], "max_thumbnails": 1})).await;
    assert_eq!(v["job"]["message"], "thumbnails generated for 2 of 2 model(s)");
    assert_eq!(scalar::<i64>(&lib, &format!("SELECT count(*) FROM images WHERE model_id = {dragon}")), 1);
    assert_eq!(scalar::<i64>(&lib, &format!("SELECT count(*) FROM images WHERE model_id = {knight}")), 1);
    let image: i64 = scalar(&lib, &format!("SELECT id FROM images WHERE model_id = {dragon}"));
    assert_eq!(lib.state.thumbs.get_many(&[image as u64]).unwrap().len(), 1, "the thumbnail is stored");
}

#[tokio::test(flavor = "multi_thread")]
async fn viewer_lists_and_loads_model_files() {
    let lib = empty_library();
    let viewer = lib.user("vi", Role::Viewer);
    let root = lib.dir.path().join("archives");
    let triangle = b"solid t
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 0 1 0
endloop
endfacet
endsolid t
";
    let archive = root.join("Studio").join("Dragon.zip");
    write_zip(&archive, &[("parts/Dragon.STL", triangle), ("broken.obj", b"f 1 2 3
"), ("a.png", &png([1, 2, 3]))]);
    run(&lib, &lib.user("ed", Role::Editor), json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let dragon: i64 = scalar(&lib, "SELECT id FROM models");

    let files = call(&lib.state, "GET", &format!("/api/models/{dragon}/viewable"), Some(&viewer), None).await.json();
    assert_eq!(files, json!([{"name": "broken.obj", "size": 8}, {"name": "parts/Dragon.STL", "size": triangle.len()}]));

    let r = call(&lib.state, "GET", &format!("/api/models/{dragon}/view?name=parts%2FDragon.STL"), Some(&viewer), None).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(r.headers["content-type"], "application/octet-stream");
    let scene = meshpeek_core::wire::decode(&r.body).unwrap();
    assert_eq!(scene.objects[0].mesh.n_faces(), 1);

    let (state, token) = (&lib.state, viewer.as_str());
    let status = |uri: String| async move { call(state, "GET", &uri, Some(token), None).await.status };
    assert_eq!(status(format!("/api/models/{dragon}/view?name=missing.stl")).await, StatusCode::NOT_FOUND);
    assert_eq!(status(format!("/api/models/{dragon}/view?name=a.png")).await, StatusCode::BAD_REQUEST);
    assert_eq!(status(format!("/api/models/{dragon}/view?name=broken.obj")).await, StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(status(format!("/api/models/{dragon}/view?name=x.stl&other=1")).await, StatusCode::BAD_REQUEST);
    assert_eq!(status("/api/models/9999/viewable".into()).await, StatusCode::NOT_FOUND);
    std::fs::remove_file(&archive).unwrap();
    assert_eq!(status(format!("/api/models/{dragon}/viewable")).await, StatusCode::NOT_FOUND);
}

#[tokio::test(flavor = "multi_thread")]
async fn delete_files_to_the_trash() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n")]);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    let dragon: i64 = scalar(&lib, "SELECT id FROM models");

    // Off by default.
    let v = finish(&lib, &ed, json!({"kind": "delete_files", "ids": [dragon]})).await;
    assert_eq!(v["job"]["state"], "failed", "{v}");
    assert!(root.join("Studio").join("Dragon.zip").is_file());

    let trash = lib.dir.path().join("trash");
    let settings = json!({"allow_file_delete": true, "use_trash_folder": true, "trash_folder_path": trash.to_string_lossy()});
    std::fs::write(lib.dir.path().join("settings.json"), settings.to_string()).unwrap();
    let v = run(&lib, &ed, json!({"kind": "delete_files", "ids": [dragon]})).await;
    assert_eq!(v["job"]["message"], "1 archive(s) deleted");
    assert!(!root.join("Studio").join("Dragon.zip").exists());
    assert!(trash.join("Dragon.zip").is_file());
    let note: Value = serde_json::from_str(&std::fs::read_to_string(trash.join("Dragon.zip.json")).unwrap()).unwrap();
    assert!(note["original_path"].as_str().unwrap().ends_with("Dragon.zip"));
    assert_eq!(scalar::<i64>(&lib, "SELECT count(*) FROM models"), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn sort_files_by_pattern() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("In").join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n")]);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    let dragon: i64 = scalar(&lib, "SELECT id FROM models");
    let target = root.join("Sorted");
    std::fs::create_dir_all(&target).unwrap();
    let body = json!({"ids": [dragon], "target_dir": target.to_string_lossy(), "pattern": "{sculptor}/{title}"});

    let rows = call(&lib.state, "POST", "/api/sort/preview", Some(&ed), Some(body.clone())).await.json();
    assert_eq!(rows[0]["status"], "OK", "{rows}");
    assert_eq!(rows[0]["to"], json!(target.join("In").join("Dragon.zip").to_string_lossy()));

    // Off by default.
    let mut job = body.clone();
    job["kind"] = json!("sort_files");
    assert_eq!(finish(&lib, &ed, job.clone()).await["job"]["state"], "failed");
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"file_sorting_enabled": true}"#).unwrap();
    let v = run(&lib, &ed, job).await;
    assert_eq!(v["job"]["message"], "moved 1 archive(s)");
    let path: String = scalar(&lib, "SELECT archive_path FROM models_view");
    assert_eq!(path, target.join("In").join("Dragon.zip").to_string_lossy());
    assert!(std::path::Path::new(&path).is_file());
    assert!(!root.join("In").join("Dragon.zip").exists());
}
