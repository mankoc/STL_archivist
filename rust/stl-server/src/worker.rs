//! `stl-server worker --server URL [--data-dir DIR] [--name NAME]`: a compute
//! worker for a remote server (see `crate::workers`). It opens no library: it
//! loads the CLIP encoder and the AI tagger's model from DIR's components
//! (`onnxruntime`, `models/`, `ai/`, as a library's Maintenance installs them),
//! connects to the server with the session token in `STL_WORKER_TOKEN`, and
//! answers its requests until stopped, reconnecting when the connection drops.
//! A refused token ends it (exit code 2), so the desktop app can ask for a new one.

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use stl_core::control::Control;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::{self, Message};

use crate::Config;
use crate::ai::{AiRuntime, MODEL_LABEL, Prompt};
use crate::tools::Tools;
use crate::workers::{ChatInfo, ClipInfo, Hello, MAX_MESSAGE, ToServer, ToWorker, b64, unb64, sha256_file};

/// Without a frame (the server pings every 30 s) the connection is dead.
const SILENCE: Duration = Duration::from_secs(90);
const MAX_BACKOFF: Duration = Duration::from_secs(60);

/// Why a session ended.
pub enum Ended {
    /// The server refused the token: stop.
    Refused(String),
    /// Anything else: connect again.
    Retry(String),
}

/// What this machine computes with.
pub struct Engines {
    config: Config,
    tools: Tools,
    ai: Arc<AiRuntime>,
    hello: Hello,
}

impl Engines {
    /// Load the encoder (hashing its file) and check the tagger's model.
    pub fn load(config: Config, name: String) -> Engines {
        let tools = Tools::default();
        let clip = tools.embedder(&config).and_then(|e| match sha256_file(&e.model) {
            Ok(sha256) => Some(ClipInfo { sha256, provider: e.provider.to_string() }),
            Err(err) => {
                tracing::warn!("cannot read {}: {err}", e.model.display());
                None
            }
        });
        let ai: Arc<AiRuntime> = Arc::default();
        let status = ai.status(&config);
        if !status.available {
            tracing::info!("AI tagger unavailable here: {}", status.detail);
        }
        let chat = status.available.then(|| ChatInfo { model: MODEL_LABEL.into() });
        let hello = Hello { name, version: stl_core::VERSION.to_string(), clip, chat };
        Engines { config, tools, ai, hello }
    }

    pub fn hello(&self) -> &Hello {
        &self.hello
    }

    /// Answer one request (blocking).
    pub fn answer(&self, msg: ToWorker) -> Option<ToServer> {
        match msg {
            ToWorker::Welcome { .. } => None,
            ToWorker::Embed { id, images } => Some(match self.tools.embedder(&self.config) {
                None => ToServer::Error { id, message: "no CLIP encoder on this worker".into() },
                Some(e) => {
                    let images: Vec<Vec<u8>> = images.iter().map(|s| unb64(s).unwrap_or_default()).collect();
                    let vectors = e.embed(&images).into_iter().map(|v| v.map(|v| b64(&v))).collect();
                    ToServer::Vectors { id, vectors }
                }
            }),
            ToWorker::Chat { id, images, prompt, max_tokens, temperature } => {
                let images: Vec<Vec<u8>> = images.iter().map(|s| unb64(s).unwrap_or_default()).collect();
                let log = |line: String| tracing::info!("{line}");
                let p = Prompt { images: &images, text: &prompt, max_tokens, temperature };
                Some(match self.ai.ask(&self.config, &Control::new(), &log, p) {
                    Ok(text) => ToServer::Text { id, text },
                    Err(message) => ToServer::Error { id, message },
                })
            }
        }
    }
}

/// `ws(s)://host/api/workers/connect` for a server's `http(s)://host/`.
pub fn connect_url(server: &str) -> Result<String, String> {
    let s = server.trim().trim_end_matches('/');
    let rest = if let Some(r) = s.strip_prefix("https://") {
        format!("wss://{r}")
    } else if let Some(r) = s.strip_prefix("http://") {
        format!("ws://{r}")
    } else {
        return Err(format!("{server:?} is not an http(s) server address"));
    };
    Ok(format!("{rest}/api/workers/connect"))
}

/// This machine's name.
fn host_name() -> String {
    std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .ok()
        .or_else(|| std::fs::read_to_string("/etc/hostname").ok().map(|s| s.trim().to_string()))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "worker".into())
}

/// One connection: hello, then requests until it drops.
pub async fn session(url: &str, token: &str, engines: &Arc<Engines>) -> Result<(), Ended> {
    let mut req = url.into_client_request().map_err(|e| Ended::Refused(format!("{url}: {e}")))?;
    let auth = format!("Bearer {token}").parse().map_err(|_| Ended::Refused("the token is not valid text".into()))?;
    req.headers_mut().insert("authorization", auth);
    let config = WebSocketConfig::default().max_message_size(Some(MAX_MESSAGE)).max_frame_size(Some(MAX_MESSAGE));
    let (ws, _) = match tokio_tungstenite::connect_async_with_config(req, Some(config), true).await {
        Ok(c) => c,
        Err(tungstenite::Error::Http(resp)) if matches!(resp.status().as_u16(), 401 | 403) => {
            return Err(Ended::Refused(format!("the server refused the worker's session ({}); turn the worker on again", resp.status())));
        }
        Err(e) => return Err(Ended::Retry(format!("cannot connect to {url}: {e}"))),
    };
    let (mut sink, mut stream) = ws.split();
    let hello = serde_json::to_string(&ToServer::Hello(engines.hello.clone())).unwrap_or_default();
    sink.send(Message::Text(hello.into())).await.map_err(|e| Ended::Retry(e.to_string()))?;
    let (out_tx, mut out_rx) = tokio::sync::mpsc::unbounded_channel::<ToServer>();
    loop {
        tokio::select! {
            out = out_rx.recv() => {
                let Some(msg) = out else { continue };
                let text = serde_json::to_string(&msg).unwrap_or_default();
                sink.send(Message::Text(text.into())).await.map_err(|e| Ended::Retry(e.to_string()))?;
            }
            incoming = tokio::time::timeout(SILENCE, stream.next()) => {
                let msg = match incoming {
                    Err(_) => return Err(Ended::Retry("no word from the server; reconnecting".into())),
                    Ok(None) => return Ok(()),
                    Ok(Some(Err(e))) => return Err(Ended::Retry(e.to_string())),
                    Ok(Some(Ok(m))) => m,
                };
                let text = match msg {
                    Message::Text(t) => t,
                    Message::Close(_) => return Ok(()),
                    _ => continue, // pings are answered by tungstenite
                };
                let msg: ToWorker = match serde_json::from_str(&text) {
                    Ok(m) => m,
                    Err(e) => {
                        tracing::warn!("unreadable message from the server: {e}");
                        continue;
                    }
                };
                if let ToWorker::Welcome { clip, chat, note } = &msg {
                    tracing::info!(clip, chat, "connected to the server");
                    if let Some(n) = note {
                        tracing::warn!("{n}");
                    }
                    continue;
                }
                let (engines, tx) = (Arc::clone(engines), out_tx.clone());
                tokio::task::spawn_blocking(move || {
                    if let Some(reply) = engines.answer(msg) {
                        let _ = tx.send(reply);
                    }
                });
            }
        }
    }
}

/// Parse the arguments and serve until the token is refused.
pub async fn run(args: &[String]) -> Result<(), String> {
    let usage = "usage: stl-server worker --server URL [--data-dir DIR] [--name NAME] (token in STL_WORKER_TOKEN)";
    let (mut server, mut name, mut rest) = (None, None, Vec::new());
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--server" => server = Some(it.next().ok_or(usage)?.clone()),
            "--name" => name = Some(it.next().ok_or(usage)?.clone()),
            _ => rest.push(a.clone()),
        }
    }
    let url = connect_url(&server.ok_or(usage)?)?;
    let token = std::env::var("STL_WORKER_TOKEN").ok().filter(|t| !t.is_empty()).ok_or(usage)?;
    let config = Config::load(&rest)?;
    tracing::info!(data_dir = %config.data_dir.display(), "loading the compute engines");
    let name = name.unwrap_or_else(host_name);
    let engines = Arc::new(tokio::task::spawn_blocking(move || Engines::load(config, name)).await.map_err(|e| e.to_string())?);
    match (&engines.hello.clip, &engines.hello.chat) {
        (None, None) => return Err("nothing to compute here: install ONNX Runtime and the CLIP encoder, or the AI tagger, into the data directory".into()),
        (clip, chat) => tracing::info!(
            clip = clip.as_ref().map(|c| c.provider.as_str()).unwrap_or("none"),
            tagger = chat.is_some(),
            "compute worker ready"
        ),
    }
    let mut backoff = Duration::from_secs(1);
    loop {
        tracing::info!("connecting to {url}");
        match session(&url, &token, &engines).await {
            Ok(()) => {
                tracing::info!("the server closed the connection");
                backoff = Duration::from_secs(1);
            }
            Err(Ended::Refused(e)) => return Err(e),
            Err(Ended::Retry(e)) => tracing::warn!("{e}"),
        }
        tokio::time::sleep(backoff).await;
        backoff = (backoff * 2).min(MAX_BACKOFF);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_addresses() {
        assert_eq!(connect_url("http://nas:8770/").unwrap(), "ws://nas:8770/api/workers/connect");
        assert_eq!(connect_url("https://stl.example.org").unwrap(), "wss://stl.example.org/api/workers/connect");
        assert!(connect_url("nas:8770").is_err());
    }
}
