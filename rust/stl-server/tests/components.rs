//! Optional components over HTTP: status, admin-only jobs, validation, and the
//! jobs' handling of what is installed (no network: downloads are covered by
//! the unit tests in `components.rs`).

mod common;

use std::time::Duration;

use axum::http::StatusCode;
use common::{TestLib, call, empty_library};
use serde_json::{Value, json};
use stl_server::accounts::Role;

async fn finish(lib: &TestLib, token: &str, body: Value) -> Value {
    let r = call(&lib.state, "POST", "/api/jobs", Some(token), Some(body)).await;
    assert_eq!(r.status, StatusCode::ACCEPTED, "{:?}", r.json());
    let id = r.json()["id"].as_u64().unwrap();
    for _ in 0..300 {
        let v = call(&lib.state, "GET", &format!("/api/jobs/{id}"), Some(token), None).await.json();
        if !matches!(v["job"]["state"].as_str(), Some("queued" | "running")) {
            return v;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("job {id} did not finish");
}

fn state_of(v: &Value, id: &str) -> String {
    v["components"].as_array().unwrap().iter().find(|c| c["id"] == id).unwrap()["state"].as_str().unwrap().to_string()
}

#[tokio::test(flavor = "multi_thread")]
async fn status_install_and_remove() {
    let lib = empty_library();
    let admin = lib.user("admin", Role::Admin);
    let ed = lib.user("ed", Role::Editor);

    assert_eq!(call(&lib.state, "GET", "/api/components", Some(&ed), None).await.status, StatusCode::FORBIDDEN);
    let r = call(&lib.state, "POST", "/api/jobs", Some(&ed), Some(json!({"kind": "install_components", "components": ["pdfium"]}))).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);

    let v = call(&lib.state, "GET", "/api/components", Some(&admin), None).await.json();
    let ids: Vec<&str> = v["components"].as_array().unwrap().iter().map(|c| c["id"].as_str().unwrap()).collect();
    assert_eq!(ids, ["ai_runtime", "ai_model", "clip_model", "onnxruntime", "pdfium"]);
    if v["platform"].is_null() {
        return; // nothing is offered on this platform
    }
    assert_eq!(state_of(&v, "pdfium"), "missing");
    assert!(v["components"][1]["sizes"][0]["bytes"].as_u64().unwrap() > 3_000_000_000, "the model is over 3 GB");
    assert_eq!(v["components"][0]["variants"][0], "cpu");
    let onnx = &v["components"][3];
    assert_eq!(onnx["variants"].as_array().unwrap().len(), 2, "{onnx}");
    assert!(onnx["sizes"][1]["bytes"].as_u64().unwrap() > 200_000_000, "the GPU build is the larger one");
    assert!(v["gpus"].is_array() && v["clip_provider"].is_null());

    // An installed component is not downloaded again, and can be removed.
    let pdfium = lib.dir.path().join(format!("{}pdfium{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX));
    std::fs::write(&pdfium, b"stand-in").unwrap();
    let v = call(&lib.state, "GET", "/api/components", Some(&admin), None).await.json();
    assert_eq!(state_of(&v, "pdfium"), "installed");
    let job = finish(&lib, &admin, json!({"kind": "install_components", "components": ["pdfium"]})).await;
    assert_eq!(job["job"]["message"], "0 component(s) installed", "{job}");
    assert!(job["log"].as_array().unwrap().iter().any(|l| l.as_str().unwrap().contains("already installed")));
    let job = finish(&lib, &admin, json!({"kind": "remove_components", "components": ["pdfium"]})).await;
    assert_eq!(job["job"]["message"], "1 component(s) removed");
    assert!(!pdfium.exists());

    // Unknown components and variants are refused before queueing.
    for bad in [
        json!({"kind": "install_components", "components": ["everything"]}),
        json!({"kind": "install_components", "components": []}),
        json!({"kind": "install_components", "components": ["ai_runtime"], "variants": {"ai_runtime": "metal"}}),
        json!({"kind": "install_components", "components": ["pdfium"], "variants": {"pdfium": "cuda"}}),
        json!({"kind": "remove_components", "components": ["../x"]}),
    ] {
        let r = call(&lib.state, "POST", "/api/jobs", Some(&admin), Some(bad.clone())).await;
        assert_eq!(r.status, StatusCode::BAD_REQUEST, "{bad}");
    }
}
