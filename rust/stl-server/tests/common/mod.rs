//! Shared test helpers: a temporary library, the router with a peer address,
//! and request shortcuts.
#![allow(dead_code)]

use std::net::SocketAddr;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::connect_info::MockConnectInfo;
use axum::http::{HeaderMap, Request, StatusCode, header};
use http_body_util::BodyExt;
use serde_json::Value;
use stl_server::accounts::{self, Role};
use stl_server::{AppState, Config, Library, app};
use tower::ServiceExt;

pub const LOCAL: &str = "127.0.0.1:50000";
pub const REMOTE: &str = "192.0.2.7:50000";

pub struct TestLib {
    pub dir: tempfile::TempDir,
    pub state: AppState,
}

pub fn empty_library() -> TestLib {
    library_with(|_| {})
}

/// A temporary library whose configuration `adjust` may change first.
pub fn library_with(adjust: impl FnOnce(&mut Config)) -> TestLib {
    let dir = tempfile::tempdir().unwrap();
    let mut config = Config {
        data_dir: dir.path().to_path_buf(),
        bind: "127.0.0.1:0".parse().unwrap(),
        unrar: None,
        onnxruntime: None,
        pdfium: None,
        // Editors may crawl anything in the test's directory.
        roots: vec![dir.path().to_string_lossy().into_owned()],
        path_map: vec![],
        web_dir: None,
        ai: Default::default(), components_mirror: None,
    };
    adjust(&mut config);
    let state = Arc::new(Library::open(config).unwrap());
    TestLib { dir, state }
}

impl TestLib {
    /// A user with `role` and a session token for them.
    pub fn user(&self, name: &str, role: Role) -> String {
        self.state
            .accounts
            .with(|c| {
                let u = accounts::create_user(c, name, "password123", role)?;
                accounts::create_session(c, u.id)
            })
            .unwrap()
    }
}

pub struct Reply {
    pub status: StatusCode,
    pub headers: HeaderMap,
    pub body: Vec<u8>,
}

impl Reply {
    pub fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap_or(Value::Null)
    }
}

pub async fn send_from(state: &AppState, peer: &str, req: Request<Body>) -> Reply {
    let peer: SocketAddr = peer.parse().unwrap();
    let resp = app(state.clone()).layer(MockConnectInfo(peer)).oneshot(req).await.unwrap();
    let (parts, body) = resp.into_parts();
    Reply { status: parts.status, headers: parts.headers, body: body.collect().await.unwrap().to_bytes().to_vec() }
}

pub fn request(method: &str, uri: &str, token: Option<&str>, body: Option<Value>) -> Request<Body> {
    let mut b = Request::builder().method(method).uri(uri);
    if let Some(t) = token {
        b = b.header(header::AUTHORIZATION, format!("Bearer {t}"));
    }
    match body {
        Some(v) => b.header(header::CONTENT_TYPE, "application/json").body(Body::from(v.to_string())).unwrap(),
        None => b.body(Body::empty()).unwrap(),
    }
}

pub async fn call(state: &AppState, method: &str, uri: &str, token: Option<&str>, body: Option<Value>) -> Reply {
    send_from(state, LOCAL, request(method, uri, token, body)).await
}
