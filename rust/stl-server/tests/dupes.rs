//! The duplicate finder jobs: content, images (dHash) and metadata.

mod common;

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;
use stl_server::jobs::ServerEvent;

fn png(rgb: [u8; 3], stripe: bool) -> Vec<u8> {
    let img = image::RgbImage::from_fn(80, 60, |x, _| if stripe && x < 40 { image::Rgb([255, 255, 255]) } else { image::Rgb(rgb) });
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

async fn run(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    let v = wait(lib, token, r.json()["id"].as_u64().unwrap()).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    v
}

/// Studio/Dragon and Studio/Dragun share their files and render; Studio/Knight differs.
async fn library() -> (TestLib, String) {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"store_stl_file_list": true}"#).unwrap();
    let root = lib.dir.path().join("archives");
    let parts: &[(&str, &[u8])] = &[("body.stl", b"solid body\nendsolid\n"), ("head.stl", b"solid head\nendsolid\n")];
    let render = png([200, 30, 30], true);
    for name in ["Dragon", "Dragun"] {
        let mut members = parts.to_vec();
        members.push(("render.png", &render));
        write_zip(&root.join("Studio").join(format!("{name}.zip")), &members);
    }
    let other = png([30, 30, 200], false);
    write_zip(&root.join("Studio").join("Knight.zip"), &[("knight.stl", b"solid knight\nendsolid\n"), ("render.png", &other)]);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    (lib, ed)
}

fn titles(pair: &Value) -> (String, String) {
    (pair["title_a"].as_str().unwrap().into(), pair["title_b"].as_str().unwrap().into())
}

#[tokio::test(flavor = "multi_thread")]
async fn content_and_image_duplicates() {
    let (lib, ed) = library().await;
    let mut events = lib.state.jobs.events.subscribe();

    let v = run(&lib, &ed, json!({"kind": "find_content_duplicates", "threshold": 0.8})).await;
    assert_eq!(v["job"]["message"], "1 archive pair(s) found");
    let pairs = v["result"]["pairs"].as_array().unwrap();
    assert_eq!(titles(&pairs[0]), ("Dragon".into(), "Dragun".into()));
    assert_eq!((pairs[0]["overlap"].clone(), pairs[0]["similarity"].clone()), (json!(2), json!(1.0)));

    let v = run(&lib, &ed, json!({"kind": "find_image_duplicates", "method": "dhash", "max_distance": 0})).await;
    let r = &v["result"];
    assert_eq!((r["images"].clone(), r["items"].clone()), (json!(3), json!(2)), "{r}");
    let pairs = r["pairs"].as_array().unwrap();
    assert_eq!(pairs.len(), 1, "{r}");
    assert_eq!(titles(&pairs[0]), ("Dragon".into(), "Dragun".into()));
    assert_eq!((pairs[0]["pairs"].clone(), pairs[0]["best"].clone(), pairs[0]["imgs_a"].clone()), (json!(1), json!(0.0), json!(1)));

    // With every image shared by at most one model, the identical render is left out.
    let v = run(&lib, &ed, json!({"kind": "find_image_duplicates", "method": "dhash", "max_models": 1})).await;
    assert_eq!(v["result"]["pairs"], json!([]));
    assert_eq!(v["result"]["shared_skipped"], json!(1));

    // AI embeddings: Dragon's and Knight's images point almost the same way.
    let dim = stl_core::storage::schema_sql::EMBED_DIM as usize;
    let vec_of = |axis: usize, tilt: f32| {
        let mut v = vec![0f32; dim];
        v[axis] = 1.0;
        v[axis + 1] = tilt;
        v.iter().flat_map(|x| x.to_le_bytes()).collect::<Vec<u8>>()
    };
    lib.state
        .db
        .with_tx(|tx| {
            let image = |title: &str| -> rusqlite::Result<i64> {
                tx.query_row("SELECT i.id FROM images i JOIN models m ON m.id = i.model_id WHERE m.title = ?1", [title], |r| r.get(0))
            };
            let items = vec![(image("Dragon")?, vec_of(0, 0.0)), (image("Knight")?, vec_of(0, 0.2)), (image("Dragun")?, vec_of(5, 0.0))];
            stl_core::storage::vectors::store(tx, dim, &items)
        })
        .unwrap();
    let v = run(&lib, &ed, json!({"kind": "find_image_duplicates", "method": "clip", "threshold": 0.95})).await;
    let pairs = v["result"]["pairs"].as_array().unwrap();
    assert_eq!(pairs.len(), 1, "{v}");
    assert_eq!(titles(&pairs[0]), ("Dragon".into(), "Knight".into()));
    let best = pairs[0]["best"].as_f64().unwrap();
    assert!((best - 1.0 / 1.04f64.sqrt()).abs() < 1e-4, "{best}");

    // Finders only read: no library-changed announcement.
    while let Ok(e) = events.try_recv() {
        assert!(!matches!(e, ServerEvent::LibraryChanged { .. }), "{e:?}");
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn metadata_duplicates() {
    let (lib, ed) = library().await;
    let find = |method: &str, columns: Value| json!({"kind": "find_metadata_duplicates", "method": method, "columns": columns, "max_distance": 1});

    let v = run(&lib, &ed, find("exact", json!(["title", "sculptor"]))).await;
    let groups = v["result"]["groups"].as_array().unwrap();
    assert_eq!(groups.len(), 1, "{groups:?}");
    assert_eq!((groups[0]["column"].clone(), groups[0]["models"].as_array().unwrap().len()), (json!("Sculptor"), 3));

    let v = run(&lib, &ed, find("levenshtein", json!(["title"]))).await;
    let groups = v["result"]["groups"].as_array().unwrap();
    assert_eq!(groups.len(), 1, "{groups:?}");
    assert_eq!(groups[0]["similarity"], "Levenshtein: 1");
    let values: Vec<&str> = groups[0]["models"].as_array().unwrap().iter().map(|m| m["value"].as_str().unwrap()).collect();
    assert_eq!(values, ["Dragon", "Dragun"]);

    let v = run(&lib, &ed, find("soundex", json!(["title", "sculptor"]))).await;
    let groups = v["result"]["groups"].as_array().unwrap();
    assert_eq!(groups.len(), 1, "identical sculptors are left to exact matching: {groups:?}");
    assert_eq!(groups[0]["similarity"], "Soundex: D625");

    // Bad arguments are refused before queueing.
    for bad in [
        find("fuzzy", json!(["title"])),
        find("exact", json!([])),
        find("exact", json!(["colour"])),
        json!({"kind": "find_image_duplicates", "method": "pixels"}),
        json!({"kind": "find_content_duplicates", "threshold": 0}),
    ] {
        let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(bad.clone())).await;
        assert_eq!(r.status, StatusCode::BAD_REQUEST, "{bad}");
    }
    let viewer = lib.user("vi", Role::Viewer);
    let r = call(&lib.state, "POST", "/api/jobs", Some(&viewer), Some(find("exact", json!(["title"])))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
}
