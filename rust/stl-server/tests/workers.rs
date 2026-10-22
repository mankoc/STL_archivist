//! Compute workers over a real connection: a scripted worker (no ONNX Runtime
//! or model needed) embeds during a crawl, answers the AI tagger, is refused
//! for a different CLIP model, and with `compute: workers` a job waits for it.

mod common;

use std::io::Write;
use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, library_with};
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use stl_server::accounts::Role;
use stl_server::config::Compute;
use stl_server::workers::{ChatInfo, ClipInfo, Hello, ToServer, ToWorker, b64};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;

fn png(rgb: [u8; 3]) -> Vec<u8> {
    let img = image::RgbImage::from_pixel(60, 40, image::Rgb(rgb));
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

/// A library with two archives (three images) and embeddings during crawls on.
fn library(compute: Compute) -> TestLib {
    let lib = library_with(|c| c.compute = compute);
    std::fs::write(lib.dir.path().join("settings.json"), r#"{"ai_embeddings_during_crawl": true, "metadata_sidecar_enabled": false}"#).unwrap();
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("Studio").join("Dragon.zip"), &[("dragon.stl", b"solid d\nendsolid\n"), ("red.png", &png([220, 20, 20]))]);
    write_zip(&root.join("Studio").join("Knight.zip"), &[("knight.stl", b"solid k\nendsolid\n"), ("a.png", &png([20, 20, 220])), ("b.png", &png([20, 220, 20]))]);
    lib
}

/// Serve the app on a free port; returns its base URL.
async fn serve(lib: &TestLib) -> String {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let app = stl_server::app(Arc::clone(&lib.state)).into_make_service_with_connect_info::<std::net::SocketAddr>();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    url
}

/// A unit vector of the model's size, different per image.
fn vector(image: &[u8]) -> String {
    let mut v = vec![0f32; 512];
    v[image.len() % 512] = 1.0;
    b64(&v.iter().flat_map(|x| x.to_le_bytes()).collect::<Vec<u8>>())
}

/// A scripted worker; returns what it was asked (and its welcome) as it goes.
async fn fake_worker(base: &str, token: &str, sha256: &str) -> (tokio::task::JoinHandle<()>, Arc<AtomicUsize>, ToWorker) {
    let mut req = stl_server::worker::connect_url(base).unwrap().into_client_request().unwrap();
    req.headers_mut().insert("authorization", format!("Bearer {token}").parse().unwrap());
    let (ws, _) = tokio_tungstenite::connect_async(req).await.unwrap();
    let (mut sink, mut stream) = ws.split();
    let hello = ToServer::Hello(Hello {
        name: "gpu-pc".into(),
        version: "test".into(),
        clip: Some(ClipInfo { sha256: sha256.into(), provider: "TestGPU".into() }),
        chat: Some(ChatInfo { model: "Test model".into() }),
    });
    sink.send(Message::Text(serde_json::to_string(&hello).unwrap().into())).await.unwrap();
    let welcome = match stream.next().await.unwrap().unwrap() {
        Message::Text(t) => serde_json::from_str::<ToWorker>(&t).unwrap(),
        other => panic!("{other:?}"),
    };
    let asked = Arc::new(AtomicUsize::new(0));
    let count = Arc::clone(&asked);
    let task = tokio::spawn(async move {
        while let Some(Ok(msg)) = stream.next().await {
            let Message::Text(t) = msg else { continue };
            count.fetch_add(1, Ordering::SeqCst);
            let reply = match serde_json::from_str::<ToWorker>(&t).unwrap() {
                ToWorker::Embed { id, images } => {
                    ToServer::Vectors { id, vectors: images.iter().map(|i| Some(vector(i.as_bytes()))).collect() }
                }
                ToWorker::Chat { id, prompt, .. } => ToServer::Text {
                    id,
                    text: if prompt.contains("EXACT format") { "TITLE: Red Dragon\nYEAR: 2024".into() } else { "dragon, red dragon".into() },
                },
                ToWorker::Welcome { .. } => continue,
            };
            sink.send(Message::Text(serde_json::to_string(&reply).unwrap().into())).await.unwrap();
        }
    });
    (task, asked, welcome)
}

async fn wait_job(lib: &TestLib, token: &str, id: u64) -> Value {
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

async fn run(lib: &TestLib, token: &str, body: Value) -> Value {
    let id = submit(lib, token, body).await;
    let v = wait_job(lib, token, id).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    v
}

fn vectors(lib: &TestLib) -> i64 {
    lib.state.db.with(|c| c.query_row("SELECT count(*) FROM image_vec", [], |r| r.get(0))).unwrap()
}

fn crawl(lib: &TestLib) -> Value {
    json!({"kind": "crawl", "folder": lib.dir.path().join("archives").to_string_lossy(), "pattern": "{sculptor}/{title}"})
}

#[tokio::test(flavor = "multi_thread")]
async fn a_worker_embeds_and_tags() {
    let lib = library(Compute::Local);
    let base = serve(&lib).await;
    let viewer = lib.user("vi", Role::Viewer);
    let ed = lib.user("ed", Role::Editor);

    // Tokens are for editors; a worker's session is a new one.
    assert_eq!(call(&lib.state, "POST", "/api/workers/token", Some(&viewer), None).await.status, StatusCode::FORBIDDEN);
    let token = call(&lib.state, "POST", "/api/workers/token", Some(&ed), None).await.json()["token"].as_str().unwrap().to_string();
    assert_ne!(token, ed);

    let (task, asked, welcome) = fake_worker(&base, &token, stl_server::components::CLIP_SHA256).await;
    assert_eq!(welcome, ToWorker::Welcome { clip: true, chat: true, note: None });
    let v = call(&lib.state, "GET", "/api/workers", Some(&viewer), None).await.json();
    assert_eq!(v["compute"], "local");
    let w = &v["workers"][0];
    assert_eq!((w["name"].as_str(), w["user"].as_str(), w["clip_provider"].as_str()), (Some("gpu-pc"), Some("ed"), Some("TestGPU")), "{v}");

    // The crawl's embeddings come from the worker.
    run(&lib, &ed, crawl(&lib)).await;
    assert_eq!(vectors(&lib), 3);
    assert!(asked.load(Ordering::SeqCst) >= 1);

    // So does the AI tagger.
    let status = call(&lib.state, "GET", "/api/ai/status", Some(&ed), None).await.json();
    assert!(status["detail"].as_str().unwrap().contains("gpu-pc"), "{status}");
    let dragon: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM models WHERE title = 'Dragon'", [], |r| r.get(0))).unwrap();
    let v = run(&lib, &ed, json!({"kind": "ai_suggest", "id": dragon})).await;
    assert_eq!(v["result"]["tags"], json!(["dragon", "red dragon"]), "{v}");
    assert_eq!(v["result"]["fields"]["title"], "Red Dragon");

    // A worker that leaves is forgotten.
    task.abort();
    let _ = task.await;
    for _ in 0..50 {
        if call(&lib.state, "GET", "/api/workers", Some(&ed), None).await.json()["workers"].as_array().unwrap().is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(call(&lib.state, "GET", "/api/workers", Some(&ed), None).await.json()["workers"].as_array().unwrap().is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn another_clip_model_is_not_used() {
    let lib = library(Compute::Local);
    if lib.state.tools.embedder(&lib.state.config).is_some() {
        eprintln!("skipped: this server has its own CLIP encoder");
        return;
    }
    let base = serve(&lib).await;
    let ed = lib.user("ed", Role::Editor);
    let (_task, asked, welcome) = fake_worker(&base, &ed, "0123").await;
    let ToWorker::Welcome { clip, chat, note } = welcome else { panic!() };
    assert!(!clip && chat && note.unwrap().contains("CLIP model differs"));
    run(&lib, &ed, crawl(&lib)).await;
    assert_eq!(vectors(&lib), 0, "no vectors from a different model");
    assert_eq!(asked.load(Ordering::SeqCst), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn with_compute_workers_jobs_wait_for_one() {
    let lib = library(Compute::Workers);
    let base = serve(&lib).await;
    let ed = lib.user("ed", Role::Editor);
    assert_eq!(call(&lib.state, "GET", "/api/workers", Some(&ed), None).await.json()["compute"], "workers");

    // Without a worker the crawl skips embeddings, and the job waits.
    run(&lib, &ed, crawl(&lib)).await;
    assert_eq!(vectors(&lib), 0, "this server does not embed itself");
    let id = submit(&lib, &ed, json!({"kind": "compute_embeddings"})).await;
    let mut waiting = false;
    for _ in 0..50 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(&ed), None).await.json();
        if v["log"].as_array().unwrap().iter().any(|l| l.as_str().unwrap().starts_with("Waiting for a compute worker")) {
            assert_eq!(v["job"]["state"], "running");
            waiting = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert!(waiting, "the job waits for a worker");

    let (_task, _, _) = fake_worker(&base, &ed, stl_server::components::CLIP_SHA256).await;
    let v = wait_job(&lib, &ed, id).await;
    assert_eq!(v["job"]["message"], "3 computed, 0 skipped (no thumbnail, not decodable or a wrong model output)", "{v}");
    assert_eq!(vectors(&lib), 3);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_refused_token_ends_the_worker() {
    let lib = library(Compute::Local);
    let base = serve(&lib).await;
    let url = stl_server::worker::connect_url(&base).unwrap();
    let engines = Arc::new(stl_server::worker::Engines::load(lib.state.config.clone(), "pc".into()));
    match stl_server::worker::session(&url, "not-a-session", &engines).await {
        Err(stl_server::worker::Ended::Refused(e)) => assert!(e.contains("401"), "{e}"),
        _ => panic!("the token should be refused"),
    }
}
