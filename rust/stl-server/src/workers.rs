//! Compute workers: `stl-server worker` processes (started by the desktop app on
//! a machine with a GPU) that run CLIP embeddings and the AI tagger's model for
//! this server. A worker connects out to `/api/workers/connect` (a WebSocket,
//! with a session token of an editor), says what it can do ([`Hello`]), and
//! answers [`ToWorker::Embed`] / [`ToWorker::Chat`] requests. Everything else —
//! jobs, the database, the review — stays here.
//!
//! A connected worker is preferred. Without one, `compute: local` (the
//! default) uses this server's own ONNX Runtime and `llama-server`;
//! `compute: workers` waits for a worker in jobs, skips embeddings in crawls and
//! refuses Find by Image.
//!
//! A worker's CLIP encoder is used only when its file is the pinned download or
//! this server's own model (by SHA-256): vectors of another model would not
//! match the library's.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, mpsc};
use std::time::{Duration, SystemTime};

use axum::extract::ws::{Message, WebSocket};
use base64::Engine;
use serde::{Deserialize, Serialize};
use stl_core::control::Control;
use utoipa::ToSchema;

use crate::Library;
use crate::ai::{AiStatus, Prompt};
use crate::config::Compute;

const EMBED_TIMEOUT: Duration = Duration::from_secs(180);
/// A worker may first have to start its model server (up to four minutes).
const CHAT_TIMEOUT: Duration = Duration::from_secs(900);
const HELLO_TIMEOUT: Duration = Duration::from_secs(30);
const PING_EVERY: Duration = Duration::from_secs(30);
/// Requests and replies carry base64 images: room for a batch of thumbnails.
pub const MAX_MESSAGE: usize = 256 << 20;

// ── protocol ─────────────────────────────────────────────────────────────────

/// What a worker can do, sent first.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Hello {
    /// The worker's machine (shown to users).
    pub name: String,
    pub version: String,
    pub clip: Option<ClipInfo>,
    pub chat: Option<ChatInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ClipInfo {
    /// SHA-256 of the encoder file.
    pub sha256: String,
    /// Where it runs (`CPU`, `DirectML`, `CUDA`, …).
    pub provider: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ChatInfo {
    pub model: String,
}

/// Worker → server.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ToServer {
    Hello(Hello),
    /// CLIP vectors (base64 little-endian `f32`, `null` for an unreadable image).
    Vectors { id: u64, vectors: Vec<Option<String>> },
    /// The model's reply.
    Text { id: u64, text: String },
    Error { id: u64, message: String },
}

/// Server → worker.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ToWorker {
    /// The answer to [`Hello`]: what this server will ask for.
    Welcome { clip: bool, chat: bool, note: Option<String> },
    /// Images (base64) to embed.
    Embed { id: u64, images: Vec<String> },
    Chat { id: u64, images: Vec<String>, prompt: String, max_tokens: u32, temperature: f64 },
}

pub fn b64(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

pub fn unb64(s: &str) -> Result<Vec<u8>, String> {
    base64::engine::general_purpose::STANDARD.decode(s).map_err(|e| e.to_string())
}

// ── the hub ──────────────────────────────────────────────────────────────────

/// A connected worker as users see it.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct WorkerInfo {
    pub id: u64,
    /// The worker's machine.
    pub name: String,
    /// The user whose session it uses.
    pub user: String,
    pub version: String,
    /// Where its CLIP encoder runs, when this server uses it.
    pub clip_provider: Option<String>,
    /// The AI tagger's model, when it has one.
    pub chat_model: Option<String>,
    /// Why part of it is not used (a different CLIP model).
    pub note: Option<String>,
    /// Unix seconds.
    pub connected_at: i64,
    /// Requests it is working on.
    pub busy: u32,
    /// Requests it has answered.
    pub done: u64,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct WorkersStatus {
    /// `local` or `workers` (see `server.json` → `compute`).
    pub compute: String,
    pub workers: Vec<WorkerInfo>,
}

#[derive(Clone, Copy, PartialEq)]
enum Need {
    Clip,
    Chat,
}

enum Reply {
    Vectors(Vec<Option<String>>),
    Text(String),
}

/// Why a request got no answer.
enum Failed {
    /// The worker went away: try another one (or wait).
    Gone,
    /// The worker answered with an error.
    Error(String),
}

struct Connected {
    info: WorkerInfo,
    clip: bool,
    chat: bool,
    tx: tokio::sync::mpsc::UnboundedSender<ToWorker>,
}

#[derive(Default)]
struct Hub {
    workers: Vec<Connected>,
    /// Request id → (worker id, where its reply goes).
    pending: HashMap<u64, (u64, mpsc::Sender<Result<Reply, Failed>>)>,
}

#[derive(Default)]
pub struct Workers {
    hub: Mutex<Hub>,
    /// Signalled when a worker connects.
    joined: Condvar,
    next_id: AtomicU64,
    /// SHA-256 of this server's own CLIP model, by (path, size, mtime).
    own_clip: Mutex<Option<(PathBuf, u64, SystemTime, String)>>,
}

impl Workers {
    fn lock(&self) -> std::sync::MutexGuard<'_, Hub> {
        self.hub.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn id(&self) -> u64 {
        self.next_id.fetch_add(1, Ordering::Relaxed) + 1
    }

    pub fn list(&self) -> Vec<WorkerInfo> {
        self.lock().workers.iter().map(|w| w.info.clone()).collect()
    }

    fn has(&self, need: Need) -> bool {
        self.lock().workers.iter().any(|w| if need == Need::Clip { w.clip } else { w.chat })
    }

    /// A worker for `need` is connected now.
    pub fn can_embed(&self) -> bool {
        self.has(Need::Clip)
    }

    fn register(&self, info: WorkerInfo, clip: bool, chat: bool, tx: tokio::sync::mpsc::UnboundedSender<ToWorker>) {
        tracing::info!(worker = %info.name, user = %info.user, clip, chat, "compute worker connected");
        self.lock().workers.push(Connected { info, clip, chat, tx });
        self.joined.notify_all();
    }

    /// Forget a worker; its unanswered requests fail with [`Failed::Gone`].
    fn unregister(&self, id: u64) {
        let mut hub = self.lock();
        if let Some(i) = hub.workers.iter().position(|w| w.info.id == id) {
            let w = hub.workers.remove(i);
            tracing::info!(worker = %w.info.name, "compute worker disconnected");
        }
        let gone: Vec<u64> = hub.pending.iter().filter(|(_, (w, _))| *w == id).map(|(r, _)| *r).collect();
        for r in gone {
            if let Some((_, tx)) = hub.pending.remove(&r) {
                let _ = tx.send(Err(Failed::Gone));
            }
        }
    }

    fn answer(&self, worker: u64, msg: ToServer) {
        let (id, reply) = match msg {
            ToServer::Vectors { id, vectors } => (id, Ok(Reply::Vectors(vectors))),
            ToServer::Text { id, text } => (id, Ok(Reply::Text(text))),
            ToServer::Error { id, message } => (id, Err(Failed::Error(message))),
            ToServer::Hello(_) => return,
        };
        let mut hub = self.lock();
        if hub.pending.get(&id).is_some_and(|(w, _)| *w == worker) {
            let (_, tx) = hub.pending.remove(&id).expect("checked");
            if let Some(w) = hub.workers.iter_mut().find(|w| w.info.id == worker) {
                w.info.busy = w.info.busy.saturating_sub(1);
                w.info.done += 1;
            }
            let _ = tx.send(reply);
        }
    }

    /// Send a request to the least busy worker for `need`; `None` when there is none.
    fn request(&self, need: Need, make: impl FnOnce(u64) -> ToWorker, timeout: Duration) -> Option<Result<Reply, Failed>> {
        let id = self.id();
        let (tx, rx) = mpsc::channel();
        let worker = {
            let mut hub = self.lock();
            let w = hub.workers.iter_mut().filter(|w| if need == Need::Clip { w.clip } else { w.chat }).min_by_key(|w| w.info.busy)?;
            if w.tx.send(make(id)).is_err() {
                return Some(Err(Failed::Gone));
            }
            w.info.busy += 1;
            let wid = w.info.id;
            hub.pending.insert(id, (wid, tx));
            wid
        };
        Some(match rx.recv_timeout(timeout) {
            Ok(r) => r,
            Err(_) => {
                let mut hub = self.lock();
                hub.pending.remove(&id);
                if let Some(w) = hub.workers.iter_mut().find(|w| w.info.id == worker) {
                    w.info.busy = w.info.busy.saturating_sub(1);
                }
                Err(Failed::Error(format!("the compute worker did not answer within {}s", timeout.as_secs())))
            }
        })
    }

    /// Block until a worker for `need` connects, or `ctl` is stopped (false).
    fn wait_for(&self, need: Need, ctl: &Control) -> bool {
        let mut hub = self.lock();
        loop {
            if hub.workers.iter().any(|w| if need == Need::Clip { w.clip } else { w.chat }) {
                return true;
            }
            if ctl.is_stopped() {
                return false;
            }
            hub = self.joined.wait_timeout(hub, Duration::from_secs(1)).unwrap_or_else(|e| e.into_inner()).0;
        }
    }

    /// CLIP vectors from a worker. `None`: no worker can embed now.
    pub fn embed(&self, images: &[Vec<u8>]) -> Option<Result<Vec<Option<Vec<u8>>>, String>> {
        loop {
            let encoded: Vec<String> = images.iter().map(|b| b64(b)).collect();
            match self.request(Need::Clip, |id| ToWorker::Embed { id, images: encoded }, EMBED_TIMEOUT)? {
                Ok(Reply::Vectors(v)) if v.len() == images.len() => {
                    return Some(Ok(v.into_iter().map(|v| v.and_then(|s| unb64(&s).ok())).collect()));
                }
                Ok(_) => return Some(Err("the compute worker sent a malformed reply".into())),
                Err(Failed::Error(e)) => return Some(Err(format!("compute worker: {e}"))),
                Err(Failed::Gone) => continue, // another worker, or none
            }
        }
    }

    /// The AI tagger's reply from a worker. `None`: use this server's model
    /// (`compute: local` without a worker); with `compute: workers` this waits
    /// for a worker.
    pub fn chat(&self, compute: Compute, ctl: &Control, log: &dyn Fn(String), p: Prompt) -> Option<Result<String, String>> {
        let encoded: Vec<String> = p.images.iter().map(|b| b64(b)).collect();
        let mut waited = false;
        loop {
            let make = |id| ToWorker::Chat { id, images: encoded.clone(), prompt: p.text.to_string(), max_tokens: p.max_tokens, temperature: p.temperature };
            match self.request(Need::Chat, make, CHAT_TIMEOUT) {
                Some(Ok(Reply::Text(t))) => return Some(Ok(t)),
                Some(Ok(_)) => return Some(Err("the compute worker sent a malformed reply".into())),
                Some(Err(Failed::Error(e))) => return Some(Err(format!("compute worker: {e}"))),
                Some(Err(Failed::Gone)) => continue,
                None if compute == Compute::Local => return None,
                None => {
                    if !waited {
                        log("Waiting for a compute worker (turn on 'Use this computer for AI' in the desktop app)…".into());
                        waited = true;
                    }
                    if !self.wait_for(Need::Chat, ctl) {
                        return Some(Err("stopped".into()));
                    }
                }
            }
        }
    }

    /// SHA-256 of this server's CLIP model, if it has one (cached while the
    /// file is unchanged).
    fn own_clip_sha(&self, path: Option<PathBuf>) -> Option<String> {
        let path = path?;
        let meta = std::fs::metadata(&path).ok()?;
        let (size, mtime) = (meta.len(), meta.modified().ok()?);
        let mut cache = self.own_clip.lock().unwrap_or_else(|e| e.into_inner());
        if let Some((p, s, m, sha)) = cache.as_ref()
            && *p == path
            && *s == size
            && *m == mtime
        {
            return Some(sha.clone());
        }
        let sha = sha256_file(&path).ok()?;
        *cache = Some((path, size, mtime, sha.clone()));
        Some(sha)
    }
}

pub fn sha256_file(path: &Path) -> std::io::Result<String> {
    use sha2::{Digest, Sha256};
    use std::io::Read;
    let mut f = std::fs::File::open(path)?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(h.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

// ── what the rest of the server calls ────────────────────────────────────────

impl Library {
    /// CLIP vectors for `images`: from a worker, else (`compute: local`) this
    /// server's encoder. `Ok(None)`: nothing can compute them now.
    pub fn embed(&self, images: &[Vec<u8>]) -> Result<Option<Vec<Option<Vec<u8>>>>, String> {
        if let Some(r) = self.workers.embed(images) {
            return r.map(Some);
        }
        if self.config.compute == Compute::Local
            && let Some(e) = self.tools.embedder(&self.config)
        {
            return Ok(Some(e.embed(images)));
        }
        Ok(None)
    }

    /// Whether [`Library::embed`] can compute vectors now.
    pub fn can_embed(&self) -> bool {
        self.workers.can_embed() || (self.config.compute == Compute::Local && self.tools.embedder(&self.config).is_some())
    }

    /// For a job: block until embeddings can be computed. Without any way
    /// (`compute: local` and no encoder here) this fails; with
    /// `compute: workers` it waits for a worker. `Ok(false)`: stopped.
    pub fn wait_for_embedder(&self, ctl: &Control, log: &dyn Fn(String)) -> Result<bool, String> {
        if self.can_embed() {
            return Ok(true);
        }
        if self.config.compute == Compute::Local {
            return Err("CLIP embeddings are unavailable: the server needs ONNX Runtime and the CLIP model (see the server log), or a connected compute worker".into());
        }
        log("Waiting for a compute worker (turn on 'Use this computer for AI' in the desktop app)…".into());
        Ok(self.workers.wait_for(Need::Clip, ctl))
    }
}

/// `/api/ai/status`: a connected worker, or this server's model.
pub fn ai_status(lib: &Library) -> AiStatus {
    let workers = lib.workers.lock();
    if let Some(w) = workers.workers.iter().find(|w| w.chat) {
        return AiStatus {
            available: true,
            running: true,
            model: w.info.chat_model.clone().unwrap_or_default(),
            detail: format!("on the compute worker {}", w.info.name),
        };
    }
    drop(workers);
    match lib.config.compute {
        Compute::Workers => AiStatus {
            available: true,
            running: false,
            model: crate::ai::MODEL_LABEL.into(),
            detail: "runs on a compute worker; none is connected, so requests wait for one".into(),
        },
        Compute::Local => lib.ai.status(&lib.config),
    }
}

pub fn status(lib: &Library) -> WorkersStatus {
    WorkersStatus {
        compute: match lib.config.compute {
            Compute::Local => "local",
            Compute::Workers => "workers",
        }
        .into(),
        workers: lib.workers.list(),
    }
}

// ── one connection ───────────────────────────────────────────────────────────

/// Serve one worker's WebSocket: read its [`Hello`], register it, pass
/// requests out and replies in until it disconnects.
pub async fn serve(lib: Arc<Library>, user: String, mut socket: WebSocket) {
    let hello = match tokio::time::timeout(HELLO_TIMEOUT, socket.recv()).await {
        Ok(Some(Ok(Message::Text(t)))) => match serde_json::from_str::<ToServer>(&t) {
            Ok(ToServer::Hello(h)) => h,
            _ => return,
        },
        _ => return,
    };
    let (clip, note) = match &hello.clip {
        None => (false, None),
        Some(c) => {
            let lib2 = Arc::clone(&lib);
            let own = tokio::task::spawn_blocking(move || lib2.workers.own_clip_sha(crate::tools::model_path(&lib2.config)))
                .await
                .ok()
                .flatten();
            if c.sha256 == crate::components::CLIP_SHA256 || own.as_deref() == Some(c.sha256.as_str()) {
                (true, None)
            } else {
                (false, Some("its CLIP model differs from this library's, so it is not used for embeddings".to_string()))
            }
        }
    };
    let chat = hello.chat.is_some();
    let welcome = ToWorker::Welcome { clip, chat, note: note.clone() };
    if socket.send(Message::Text(serde_json::to_string(&welcome).unwrap_or_default().into())).await.is_err() {
        return;
    }
    let id = lib.workers.id();
    let info = WorkerInfo {
        id,
        name: hello.name.clone(),
        user,
        version: hello.version.clone(),
        clip_provider: if clip { hello.clip.as_ref().map(|c| c.provider.clone()) } else { None },
        chat_model: hello.chat.as_ref().map(|c| c.model.clone()),
        note,
        connected_at: crate::accounts::now(),
        busy: 0,
        done: 0,
    };
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<ToWorker>();
    lib.workers.register(info, clip, chat, tx);
    let mut ping = tokio::time::interval(PING_EVERY);
    loop {
        tokio::select! {
            out = rx.recv() => {
                let Some(msg) = out else { break };
                let text = serde_json::to_string(&msg).unwrap_or_default();
                if socket.send(Message::Text(text.into())).await.is_err() {
                    break;
                }
            }
            incoming = socket.recv() => match incoming {
                Some(Ok(Message::Text(t))) => match serde_json::from_str::<ToServer>(&t) {
                    Ok(msg) => lib.workers.answer(id, msg),
                    Err(e) => tracing::warn!("compute worker sent an unreadable message: {e}"),
                },
                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                Some(Ok(_)) => {}
            },
            _ = ping.tick() => {
                if socket.send(Message::Ping(Default::default())).await.is_err() {
                    break;
                }
            }
        }
    }
    lib.workers.unregister(id);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protocol_round_trips() {
        let hello = ToServer::Hello(Hello {
            name: "pc".into(),
            version: "1".into(),
            clip: Some(ClipInfo { sha256: "ab".into(), provider: "DirectML".into() }),
            chat: None,
        });
        let text = serde_json::to_string(&hello).unwrap();
        assert!(text.starts_with(r#"{"type":"hello","name":"pc""#), "{text}");
        assert_eq!(serde_json::from_str::<ToServer>(&text).unwrap(), hello);
        let embed = ToWorker::Embed { id: 7, images: vec![b64(b"img")] };
        assert_eq!(serde_json::from_str::<ToWorker>(&serde_json::to_string(&embed).unwrap()).unwrap(), embed);
        assert_eq!(unb64(&b64(b"\x00\xff")).unwrap(), b"\x00\xff");
    }

    #[test]
    fn requests_go_to_a_worker_and_fail_over() {
        let hub = Arc::new(Workers::default());
        assert!(hub.embed(&[b"x".to_vec()]).is_none(), "no worker: nothing to ask");
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        let info = |id| WorkerInfo {
            id,
            name: "pc".into(),
            user: "ed".into(),
            version: String::new(),
            clip_provider: None,
            chat_model: None,
            note: None,
            connected_at: 0,
            busy: 0,
            done: 0,
        };
        hub.register(info(1), true, false, tx);
        let h = Arc::clone(&hub);
        let answer = std::thread::spawn(move || {
            let Some(ToWorker::Embed { id, images }) = rx.blocking_recv() else { panic!() };
            assert_eq!(images, vec![b64(b"x")]);
            h.answer(1, ToServer::Vectors { id, vectors: vec![Some(b64(&[1, 2, 3, 4]))] });
            // The second request: the worker leaves without answering.
            let Some(ToWorker::Embed { .. }) = rx.blocking_recv() else { panic!() };
            h.unregister(1);
        });
        assert_eq!(hub.embed(&[b"x".to_vec()]).unwrap().unwrap(), vec![Some(vec![1, 2, 3, 4])]);
        assert!(hub.embed(&[b"x".to_vec()]).is_none(), "the only worker left: nothing to ask");
        answer.join().unwrap();
        assert_eq!(hub.list().len(), 0);
    }
}
