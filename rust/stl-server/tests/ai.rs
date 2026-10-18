//! The AI tagger against a stand-in OpenAI-compatible server (`ai.url`): the
//! suggestion job, saving a reviewed suggestion, and the batch job.

mod common;

use std::io::Write;
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use common::{TestLib, call, empty_library, library_with};
use serde_json::{Value, json};
use stl_server::accounts::Role;

fn png() -> Vec<u8> {
    let img = image::RgbImage::from_pixel(40, 30, image::Rgb([200, 0, 0]));
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

/// The requests the stand-in received.
type Seen = Arc<Mutex<Vec<Value>>>;

async fn completions(State(seen): State<Seen>, Json(body): Json<Value>) -> Json<Value> {
    let prompt = body["messages"][0]["content"].as_array().unwrap().last().unwrap()["text"].as_str().unwrap().to_string();
    seen.lock().unwrap().push(body);
    let reply = if prompt.contains("EXACT format") {
        "TITLE: The Dark Knight\nCREATOR: n/a\nSCULPTOR: Alice\nCOLLECTION: comics\nSUBCOLLECTION: DC\nSCALE: 54mm\nSOURCE: Patreon\nYEAR: 2023\nDESCRIPTION: A caped hero."
    } else {
        "Batman, batman with cape, DC Comics"
    };
    Json(json!({"choices": [{"message": {"role": "assistant", "content": reply}}]}))
}

/// Start the stand-in on a free port; returns its URL.
async fn fake_model(seen: Seen) -> String {
    let app = axum::Router::new().route("/v1/chat/completions", axum::routing::post(completions)).with_state(seen);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    url
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

fn scalar<T: rusqlite::types::FromSql>(lib: &TestLib, sql: &str) -> T {
    lib.state.db.with(|c| c.query_row(sql, [], |r| r.get(0))).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn suggest_commit_and_batch() {
    let seen: Seen = Arc::default();
    let url = fake_model(Arc::clone(&seen)).await;
    let lib = library_with(|c| c.ai.url = Some(url.clone()));
    let ed = lib.user("ed", Role::Editor);
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"store_stl_file_list": true}"#).unwrap();
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon 32mm.stl", b"solid d\nendsolid\n"), ("render.png", &png())]);
    write_zip(&root.join("Studio").join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n")]);
    run(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy(), "pattern": "{sculptor}/{title}"})).await;
    let dragon: i64 = scalar(&lib, "SELECT id FROM models WHERE title = 'Dragon'");

    let status = call(&lib.state, "GET", "/api/ai/status", Some(&ed), None).await.json();
    assert_eq!(status["available"], true, "{status}");

    // A suggestion: nothing is saved yet.
    let v = run(&lib, &ed, json!({"kind": "ai_suggest", "id": dragon})).await;
    let s = &v["result"];
    assert_eq!(s["type"], "ai_suggestion", "{v}");
    assert_eq!(s["tags"], json!(["Batman", "cape", "DC Comics"]));
    assert_eq!(s["fields"]["title"], "The Dark Knight");
    assert_eq!(s["fields"]["source"], "patreon");
    assert_eq!(s["fields"]["year"], 2023);
    assert_eq!(s["fields"]["scale"], "32 mm", "scales come from the text, not the model's guess");
    assert_eq!(s["image_ids"].as_array().unwrap().len(), 1);
    {
        let seen = seen.lock().unwrap();
        assert_eq!(seen.len(), 2, "a tags request and a metadata request");
        let content = seen[0]["messages"][0]["content"].as_array().unwrap();
        assert!(content[0]["image_url"]["url"].as_str().unwrap().starts_with("data:image/jpeg;base64,"));
        let prompt = content.last().unwrap()["text"].as_str().unwrap();
        assert!(prompt.contains("- Sculptor: Studio") && prompt.contains("dragon 32mm.stl"), "{prompt}");
        assert_eq!(seen[0]["temperature"], 0.2);
    }
    assert_eq!(scalar::<String>(&lib, &format!("SELECT title FROM models WHERE id = {dragon}")), "Dragon");

    // Saving the reviewed suggestion: empty fields only, tags merged.
    let mut fields = s["fields"].clone();
    fields["description"] = json!("Edited by hand.");
    let body = json!({"fields": fields, "tags": ["Batman", " cape ", ""]});
    let r = call(&lib.state, "POST", &format!("/api/ai/commit/{dragon}"), Some(&ed), Some(body.clone())).await;
    assert_eq!(r.status, StatusCode::OK, "{:?}", r.json());
    assert_eq!(scalar::<String>(&lib, &format!("SELECT title FROM models WHERE id = {dragon}")), "Dragon", "kept");
    assert_eq!(scalar::<String>(&lib, &format!("SELECT description FROM models WHERE id = {dragon}")), "Edited by hand.");
    let tags: i64 = scalar(&lib, &format!("SELECT count(*) FROM model_tags WHERE model_id = {dragon}"));
    assert_eq!(tags, 2);
    let mut over = body.clone();
    over["overwrite"] = json!(true);
    call(&lib.state, "POST", &format!("/api/ai/commit/{dragon}"), Some(&ed), Some(over)).await;
    assert_eq!(scalar::<String>(&lib, &format!("SELECT title FROM models WHERE id = {dragon}")), "The Dark Knight");
    let r = call(&lib.state, "POST", "/api/ai/commit/9999", Some(&ed), Some(body.clone())).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    let viewer = lib.user("vi", Role::Viewer);
    let r = call(&lib.state, "POST", &format!("/api/ai/commit/{dragon}"), Some(&viewer), Some(body)).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);

    // The batch job saves every model.
    let knight: i64 = scalar(&lib, "SELECT id FROM models WHERE title = 'Knight'");
    let v = run(&lib, &ed, json!({"kind": "ai_tag", "ids": [dragon, knight], "max_tags": 2})).await;
    assert_eq!(v["job"]["message"], "saved 2 of 2 model(s)");
    let sculptor: String = scalar(&lib, &format!("SELECT s.name FROM models m JOIN sculptors s ON s.id = m.sculptor_id WHERE m.id = {knight}"));
    assert_eq!(sculptor, "Studio", "filled fields are kept");
    let collection: String = scalar(&lib, &format!("SELECT c.name FROM models m JOIN collections c ON c.id = m.collection_id WHERE m.id = {knight}"));
    assert_eq!(collection, "comics");
}

#[tokio::test(flavor = "multi_thread")]
async fn unavailable_without_a_model() {
    let lib = empty_library();
    let ed = lib.user("ed", Role::Editor);
    let status = call(&lib.state, "GET", "/api/ai/status", Some(&ed), None).await.json();
    assert_eq!(status["available"], false);
    assert!(status["detail"].as_str().unwrap().contains("llama-server"), "{status}");
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "ai_tag", "ids": []}))).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
}
