//! Native CLIP (ONNX Runtime) and PDF previews (pdfium) in server jobs.
//!
//! These need the runtime libraries: ONNX Runtime from `ORT_DYLIB_PATH` or
//! the project's Python environment (`onnxruntime` ships the DLL), pdfium from
//! `STL_PDFIUM_PATH`. Without them the tests are skipped (with a note).

mod common;

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use common::{TestLib, call};
use serde_json::{Value, json};
use stl_server::accounts::Role;
use stl_server::{Config, Library};

fn onnxruntime() -> Option<PathBuf> {
    let venv = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.venv/Lib/site-packages/onnxruntime/capi/onnxruntime.dll");
    std::env::var_os("ORT_DYLIB_PATH").map(PathBuf::from).or(Some(venv)).filter(|p| p.is_file())
}

fn pdfium() -> Option<PathBuf> {
    std::env::var_os("STL_PDFIUM_PATH").map(PathBuf::from).filter(|p| p.is_file())
}

/// A library whose data directory holds the tiny test CLIP model, with
/// embeddings during crawls switched on.
fn library(ort: Option<PathBuf>, pdf: Option<PathBuf>) -> TestLib {
    let dir = tempfile::tempdir().unwrap();
    let models = dir.path().join("models");
    std::fs::create_dir_all(&models).unwrap();
    std::fs::copy(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/data/tiny_clip.onnx"), models.join("clip_image_encoder.onnx")).unwrap();
    std::fs::write(dir.path().join("settings.json"), r#"{"ai_embeddings_during_crawl": true, "metadata_sidecar_enabled": false}"#).unwrap();
    let config = Config {
        data_dir: dir.path().to_path_buf(),
        bind: "127.0.0.1:0".parse().unwrap(),
        unrar: None,
        onnxruntime: ort.map(|p| p.to_string_lossy().into_owned()),
        pdfium: pdf.map(|p| p.to_string_lossy().into_owned()),
        roots: vec![dir.path().to_string_lossy().into_owned()],
        path_map: vec![],
        web_dir: None,
        ai: Default::default(),
    };
    TestLib { state: Arc::new(Library::open(config).unwrap()), dir }
}

fn png(rgb: [u8; 3]) -> Vec<u8> {
    let img = image::RgbImage::from_pixel(120, 90, image::Rgb(rgb));
    let mut out = std::io::Cursor::new(Vec::new());
    img.write_to(&mut out, image::ImageFormat::Png).unwrap();
    out.into_inner()
}

/// A one-page PDF (200 x 100 pt) filled red.
fn pdf() -> Vec<u8> {
    let content = b"1 0 0 rg 0 0 200 100 re f";
    let objects = [
        "<< /Type /Catalog /Pages 2 0 R >>".to_string(),
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_string(),
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Contents 4 0 R >>".to_string(),
        format!("<< /Length {} >>\nstream\n{}\nendstream", content.len(), String::from_utf8_lossy(content)),
    ];
    let mut out = b"%PDF-1.4\n".to_vec();
    let mut offsets = Vec::new();
    for (i, o) in objects.iter().enumerate() {
        offsets.push(out.len());
        out.extend_from_slice(format!("{} 0 obj\n{o}\nendobj\n", i + 1).as_bytes());
    }
    let xref = out.len();
    out.extend_from_slice(format!("xref\n0 {}\n0000000000 65535 f \n", objects.len() + 1).as_bytes());
    for off in offsets {
        out.extend_from_slice(format!("{off:010} 00000 n \n").as_bytes());
    }
    out.extend_from_slice(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n", objects.len() + 1).as_bytes());
    out
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

async fn run_job(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    let id = r.json()["id"].as_u64().unwrap();
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if !matches!(v["job"]["state"].as_str(), Some("queued" | "running")) {
            return v;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("job did not finish");
}

fn vectors(lib: &TestLib) -> Vec<(i64, Vec<f32>)> {
    let (ids, mat) = lib.state.db.with(|c| stl_core::storage::vectors::read_all(c, 512)).unwrap();
    ids.into_iter().zip(mat.chunks(512).map(<[f32]>::to_vec)).collect()
}

#[tokio::test(flavor = "multi_thread")]
async fn crawl_embeds_and_the_job_computes_missing_vectors() {
    let Some(ort) = onnxruntime() else {
        eprintln!("skipped: no ONNX Runtime library (set ORT_DYLIB_PATH)");
        return;
    };
    let lib = library(Some(ort), None);
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("a.zip"), &[("a.stl", b"solid\nendsolid\n"), ("red.png", &png([220, 20, 20]))]);
    write_zip(&root.join("b.zip"), &[("b.stl", b"solid\nendsolid\n"), ("blue.png", &png([20, 20, 220])), ("red.png", &png([220, 20, 20]))]);

    let v = run_job(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    let vecs = vectors(&lib);
    assert_eq!(vecs.len(), 3, "one vector per thumbnail, computed during the crawl");
    for (_, v) in &vecs {
        let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-4, "L2-normalised: {norm}");
    }
    let dot = |a: &[f32], b: &[f32]| a.iter().zip(b).map(|(x, y)| x * y).sum::<f32>();
    let (red_a, blue, red_b) = (&vecs[0].1, &vecs[1].1, &vecs[2].1);
    assert!(dot(red_a, red_b) > 0.9999, "the same image gives the same vector");
    assert!(dot(red_a, blue) < 0.99, "different images differ");

    // Drop two vectors; the job computes exactly those.
    lib.state.db.with(|c| c.execute("DELETE FROM image_vec WHERE image_id IN (SELECT id FROM images LIMIT 2)", [])).unwrap();
    let v = run_job(&lib, &ed, json!({"kind": "compute_embeddings"})).await;
    assert_eq!(v["job"]["message"], "2 computed, 0 skipped (no thumbnail, not decodable or a wrong model output)");
    assert_eq!(vectors(&lib).len(), 3);
    let v = run_job(&lib, &ed, json!({"kind": "compute_embeddings", "all": true})).await;
    assert!(v["job"]["message"].as_str().unwrap().starts_with("3 computed"));
}

#[tokio::test(flavor = "multi_thread")]
async fn without_onnx_runtime_the_job_explains() {
    let lib = library(None, None);
    if lib.state.tools.embedder(&lib.state.config).is_some() {
        eprintln!("skipped: ONNX Runtime was found anyway (ORT_DYLIB_PATH or beside the test binary)");
        return;
    }
    let ed = lib.user("ed", Role::Editor);
    let v = run_job(&lib, &ed, json!({"kind": "compute_embeddings"})).await;
    assert_eq!(v["job"]["state"], "failed");
    assert!(v["job"]["message"].as_str().unwrap().contains("ONNX Runtime"));
}

#[tokio::test(flavor = "multi_thread")]
async fn pdf_pages_become_thumbnails() {
    let Some(pdfium) = pdfium() else {
        eprintln!("skipped: no pdfium library (set STL_PDFIUM_PATH)");
        return;
    };
    let lib = library(None, Some(pdfium));
    let ed = lib.user("ed", Role::Editor);
    let root = lib.dir.path().join("archives");
    write_zip(&root.join("guide.zip"), &[("model.stl", b"solid\nendsolid\n"), ("instructions.pdf", &pdf())]);
    let v = run_job(&lib, &ed, json!({"kind": "crawl", "folder": root.to_string_lossy()})).await;
    assert_eq!(v["job"]["state"], "done", "{v}");
    let id: i64 = lib.state.db.with(|c| c.query_row("SELECT id FROM images", [], |r| r.get(0))).unwrap();
    let thumb = lib.state.thumbs.get_many(&[id as u64]).unwrap().pop().expect("a thumbnail").1;
    // jpeg-decoder, as the core: zune-jpeg (the image crate's decoder) misreads
    // the core encoder's non-interleaved scans, which libjpeg reads correctly.
    let mut dec = jpeg_decoder::Decoder::new(std::io::Cursor::new(&thumb));
    let px = dec.decode().unwrap();
    let info = dec.info().unwrap();
    assert_eq!((info.width, info.height), (400, 200), "2x zoom of a 200 x 100 pt page");
    let at = (100 * 400 + 200) * 3;
    let p = &px[at..at + 3];
    assert!(p[0] > 200 && p[1] < 60 && p[2] < 60, "the page is red: {p:?}");
}
