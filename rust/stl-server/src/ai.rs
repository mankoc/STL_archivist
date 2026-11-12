//! The AI tagger's model: an OpenAI-compatible `llama-server` (llama.cpp) with
//! a vision model (Qwen3-VL by default, see [`MODELS`]). The server starts it on first use from
//! the desktop's install (`<data dir>/ai/bin`, `<data dir>/ai/models`) or the
//! paths in `server.json`, stops it after [`IDLE`] without requests and when
//! the server exits; or it talks to one already running (`ai.url`).
//! With the library setting `ai_provider: openrouter` the tagger asks an
//! OpenRouter model instead, and with `ai_provider: openai` any
//! OpenAI-compatible endpoint (Gemini's by default). API keys, and that
//! endpoint's address, are kept in `server.db`, never `settings.json`, which
//! travels in backups and syncs: a key only ever goes to the address an
//! administrator set beside it.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, Instant};

use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use stl_core::control::Control;
use stl_core::storage::model_editor::{AiFields, AiInputs};
use utoipa::ToSchema;

use crate::library_settings::LibrarySettings;
use crate::{Config, Library};

/// A model the `ai_model` component can install (its variant `id`).
pub struct Model {
    pub id: &'static str,
    pub label: &'static str,
    /// File names in `<data dir>/ai/models`.
    pub file: &'static str,
    pub mmproj: &'static str,
}

/// The models to choose from; the first is the default.
pub const MODELS: [Model; 2] = [
    Model {
        id: "qwen3-vl-4b",
        label: "Qwen3-VL-4B-Instruct (GGUF)",
        file: "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
        mmproj: "mmproj-Qwen3VL-4B-Instruct-F16.gguf",
    },
    Model {
        id: "qwen2.5-vl-3b",
        label: "Qwen2.5-VL-3B-Instruct (GGUF)",
        file: "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",
        mmproj: "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf",
    },
];

/// The model to use: the installed one, else the default.
fn local_model(config: &Config) -> &'static Model {
    let id = crate::components::installed_ai_model(&config.data_dir);
    MODELS.iter().find(|m| Some(m.id) == id).unwrap_or(&MODELS[0])
}

/// The name of the model this server's tagger uses (`ai.model`'s file name
/// when one is configured).
pub fn model_label(config: &Config) -> String {
    match &config.ai.model {
        Some(p) => Path::new(p).file_name().map_or_else(|| p.clone(), |n| n.to_string_lossy().into_owned()),
        None => local_model(config).label.into(),
    }
}
/// Context window: several downscaled images plus the prompt and reply.
const N_CTX: u32 = 8192;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(240);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(300);
/// A started `llama-server` stops after this long without requests.
pub const IDLE: Duration = Duration::from_secs(600);

/// `server.json` → `"ai"`: where the model comes from. Paths left out are
/// looked up in the desktop's install under the data directory.
#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct AiConfig {
    /// An OpenAI-compatible server that is already running (`http://host:port`);
    /// when set, nothing is started.
    pub url: Option<String>,
    /// The `llama-server` executable.
    pub llama_server: Option<String>,
    pub model: Option<String>,
    pub mmproj: Option<String>,
    /// Layers to offload to the GPU (`-ngl`); default: all for a CUDA install.
    pub gpu_layers: Option<u32>,
    /// OpenRouter's API base (default [`OPENROUTER_URL`]; for tests or a proxy).
    pub openrouter_url: Option<String>,
}

pub const OPENROUTER_URL: &str = "https://openrouter.ai/api";

const SECRETS_SCHEMA: &str = "CREATE TABLE IF NOT EXISTS server_secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL);";
const OPENROUTER_KEY: &str = "openrouter_api_key";
const NO_KEY: &str = "no OpenRouter API key is set (Settings, AI tagger)";
const OPENAI_KEY: &str = "openai_api_key";
const OPENAI_URL_KEY: &str = "openai_url";
const NO_OPENAI_KEY: &str = "no API key is set for the OpenAI-compatible endpoint (Settings, AI tagger)";
/// The OpenAI-compatible endpoint when none is set: Google's for Gemini.
pub const OPENAI_URL_DEFAULT: &str = "https://generativelanguage.googleapis.com/v1beta/openai";

/// `server.db`'s `server_secrets`.
pub fn ensure_schema(conn: &rusqlite::Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SECRETS_SCHEMA)
}

fn secret(conn: &rusqlite::Connection, name: &str) -> rusqlite::Result<Option<String>> {
    use rusqlite::OptionalExtension;
    conn.query_row("SELECT value FROM server_secrets WHERE name = ?1", [name], |r| r.get(0)).optional()
}

/// Set a `server_secrets` value; an empty one removes it.
fn set_secret(conn: &rusqlite::Connection, name: &str, value: &str) -> rusqlite::Result<()> {
    if value.is_empty() {
        conn.execute("DELETE FROM server_secrets WHERE name = ?1", [name])?;
    } else {
        conn.execute("INSERT OR REPLACE INTO server_secrets (name, value) VALUES (?1, ?2)", [name, value])?;
    }
    Ok(())
}

/// The OpenRouter API key, when one is set.
pub fn openrouter_key(conn: &rusqlite::Connection) -> rusqlite::Result<Option<String>> {
    secret(conn, OPENROUTER_KEY)
}

/// Set the OpenRouter API key; an empty one removes it.
pub fn set_openrouter_key(conn: &rusqlite::Connection, key: &str) -> rusqlite::Result<()> {
    set_secret(conn, OPENROUTER_KEY, key)
}

/// The OpenAI-compatible endpoint: its base address (requests go to
/// `<url>/chat/completions`) and API key.
pub struct Endpoint {
    pub url: String,
    pub key: Option<String>,
}

pub fn openai_endpoint(conn: &rusqlite::Connection) -> rusqlite::Result<Endpoint> {
    let url = secret(conn, OPENAI_URL_KEY)?.unwrap_or_else(|| OPENAI_URL_DEFAULT.into());
    Ok(Endpoint { url, key: secret(conn, OPENAI_KEY)? })
}

/// Set the endpoint's address (empty: the default) and, when given, its key
/// (empty removes it).
pub fn set_openai_endpoint(conn: &rusqlite::Connection, url: &str, key: Option<&str>) -> rusqlite::Result<()> {
    set_secret(conn, OPENAI_URL_KEY, if url == OPENAI_URL_DEFAULT { "" } else { url })?;
    match key {
        Some(k) => set_secret(conn, OPENAI_KEY, k),
        None => Ok(()),
    }
}

/// Where the tagger's model runs, from the library settings.
pub enum Provider {
    Local,
    OpenRouter { model: String },
    OpenAi { model: String },
}

impl Provider {
    pub fn of(lib: &Library) -> Provider {
        let s = LibrarySettings::load(&lib.config.data_dir);
        match s.ai_provider().as_str() {
            "openrouter" => Provider::OpenRouter { model: s.openrouter_model() },
            "openai" => Provider::OpenAi { model: s.openai_model() },
            _ => Provider::Local,
        }
    }
}

/// `/api/ai/status` with `ai_provider: openai`.
pub fn openai_status(lib: &Library, model: String) -> AiStatus {
    match lib.accounts.with(openai_endpoint) {
        Ok(Endpoint { key: Some(_), url }) => AiStatus { available: true, running: false, model, detail: format!("on {url}") },
        Ok(Endpoint { key: None, .. }) => AiStatus { available: false, running: false, model, detail: NO_OPENAI_KEY.into() },
        Err(e) => AiStatus { available: false, running: false, model, detail: format!("server.db: {e}") },
    }
}

/// One answer from the OpenAI-compatible endpoint.
fn ask_openai(lib: &Library, model: &str, p: Prompt) -> Result<String, String> {
    let ep = lib.accounts.with(openai_endpoint).map_err(|e| format!("server.db: {e}"))?;
    let key = ep.key.ok_or(NO_OPENAI_KEY)?;
    let url = format!("{}/chat/completions", ep.url.trim_end_matches('/'));
    complete(&url, Some(Remote { key: &key, model, openrouter: false }), p)
}

/// `/api/ai/status` with `ai_provider: openrouter`.
pub fn openrouter_status(lib: &Library, model: String) -> AiStatus {
    match lib.accounts.with(openrouter_key) {
        Ok(Some(_)) => AiStatus { available: true, running: false, model, detail: "on OpenRouter".into() },
        Ok(None) => AiStatus { available: false, running: false, model, detail: NO_KEY.into() },
        Err(e) => AiStatus { available: false, running: false, model, detail: format!("server.db: {e}") },
    }
}

/// One answer from an OpenRouter model.
fn ask_openrouter(lib: &Library, model: &str, p: Prompt) -> Result<String, String> {
    let key = lib.accounts.with(openrouter_key).map_err(|e| format!("server.db: {e}"))?.ok_or(NO_KEY)?;
    let base = lib.config.ai.openrouter_url.as_deref().unwrap_or(OPENROUTER_URL).trim_end_matches('/');
    complete(&format!("{base}/v1/chat/completions"), Some(Remote { key: &key, model, openrouter: true }), p)
}

/// One question for the model.
#[derive(Debug, Clone, Copy)]
pub struct Prompt<'a> {
    pub images: &'a [Vec<u8>],
    pub text: &'a str,
    pub max_tokens: u32,
    pub temperature: f64,
}

/// What the AI tagger can use.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct AiStatus {
    pub available: bool,
    /// A started `llama-server` is running (always false with `url`).
    pub running: bool,
    pub model: String,
    /// Why it is not available, or where it comes from.
    pub detail: String,
}

struct Launch {
    exe: PathBuf,
    model: PathBuf,
    mmproj: PathBuf,
    gpu_layers: Option<u32>,
}

struct Running {
    child: Child,
    base: String,
}

impl Drop for Running {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
pub struct AiRuntime {
    running: Mutex<Option<Running>>,
    last_used: Mutex<Option<Instant>>,
}

fn exe_name() -> &'static str {
    if cfg!(windows) { "llama-server.exe" } else { "llama-server" }
}

/// `llama_server_exe`: directly in `dir`, else anywhere below it.
pub(crate) fn find_exe(dir: &Path) -> Option<PathBuf> {
    let direct = dir.join(exe_name());
    if direct.is_file() {
        return Some(direct);
    }
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        for e in std::fs::read_dir(&d).ok()?.flatten() {
            let p = e.path();
            if p.is_dir() {
                stack.push(p);
            } else if p.file_name().is_some_and(|n| n == exe_name()) {
                return Some(p);
            }
        }
    }
    None
}

fn launch_spec(config: &Config) -> Result<Launch, String> {
    let cfg = &config.ai;
    let ai_dir = config.data_dir.join("ai");
    let exe = match &cfg.llama_server {
        Some(p) => PathBuf::from(p),
        None => find_exe(&ai_dir.join("bin")).ok_or_else(|| {
            format!("llama-server was not found in {} (install the AI components from the desktop app, or set ai.llama_server in server.json)", ai_dir.join("bin").display())
        })?,
    };
    let m = local_model(config);
    let model = cfg.model.clone().map_or_else(|| ai_dir.join("models").join(m.file), PathBuf::from);
    let mmproj = cfg.mmproj.clone().map_or_else(|| ai_dir.join("models").join(m.mmproj), PathBuf::from);
    for p in [&exe, &model, &mmproj] {
        if !p.is_file() {
            return Err(format!("{} is missing", p.display()));
        }
    }
    let cuda = std::fs::read_to_string(ai_dir.join("bin").join("variant.txt")).is_ok_and(|v| v.trim() == "cuda");
    Ok(Launch { exe, model, mmproj, gpu_layers: cfg.gpu_layers.or(cuda.then_some(99)) })
}

fn agent(timeout: Duration) -> ureq::Agent {
    ureq::Agent::config_builder().timeout_global(Some(timeout)).build().into()
}

fn free_port() -> Result<u16, String> {
    let l = std::net::TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    Ok(l.local_addr().map_err(|e| e.to_string())?.port())
}

impl AiRuntime {
    pub fn status(&self, config: &Config) -> AiStatus {
        let running = self.running.lock().unwrap_or_else(|e| e.into_inner()).is_some();
        let model = model_label(config);
        if let Some(url) = &config.ai.url {
            return AiStatus { available: true, running: false, model, detail: format!("using {url}") };
        }
        match launch_spec(config) {
            Ok(l) => AiStatus { available: true, running, model, detail: format!("{}", l.exe.display()) },
            Err(e) => AiStatus { available: false, running, model, detail: e },
        }
    }

    /// The base URL of a ready model server, starting one when needed.
    fn ensure(self: &Arc<Self>, config: &Config, ctl: &Control, log: &dyn Fn(String)) -> Result<String, String> {
        *self.last_used.lock().unwrap_or_else(|e| e.into_inner()) = Some(Instant::now());
        if let Some(url) = &config.ai.url {
            return Ok(url.trim_end_matches('/').to_string());
        }
        let mut guard = self.running.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(r) = guard.as_mut() {
            if matches!(r.child.try_wait(), Ok(None)) {
                return Ok(r.base.clone());
            }
            *guard = None; // it exited
        }
        let spec = launch_spec(config)?;
        let port = free_port()?;
        let mut cmd = Command::new(&spec.exe);
        cmd.arg("-m").arg(&spec.model).arg("--mmproj").arg(&spec.mmproj);
        cmd.args(["--host", "127.0.0.1", "--port", &port.to_string(), "-c", &N_CTX.to_string()]);
        if let Some(n) = spec.gpu_layers {
            cmd.args(["-ngl", &n.to_string()]);
        }
        cmd.stdout(Stdio::null()).stderr(Stdio::null()).stdin(Stdio::null());
        stl_proc::no_window(&mut cmd);
        log("Starting the AI model server (loading the model)…".into());
        tracing::info!(exe = %spec.exe.display(), port, "starting llama-server");
        let child = cmd.spawn().map_err(|e| format!("cannot start {}: {e}", spec.exe.display()))?;
        stl_proc::end_with_parent(&child);
        let mut running = Running { child, base: format!("http://127.0.0.1:{port}") };
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let health = format!("{}/health", running.base);
        let client = agent(Duration::from_secs(3));
        loop {
            if !matches!(running.child.try_wait(), Ok(None)) {
                return Err("the AI model server exited during startup".into());
            }
            if ctl.is_stopped() {
                return Err("stopped".into());
            }
            let last = match client.get(&health).call() {
                Ok(_) => break,
                Err(e) => e.to_string(),
            };
            if Instant::now() > deadline {
                return Err(format!("the AI model server did not become ready within {}s ({last})", STARTUP_TIMEOUT.as_secs()));
            }
            std::thread::sleep(Duration::from_secs(1));
        }
        let base = running.base.clone();
        *guard = Some(running);
        drop(guard);
        self.watch_idle();
        Ok(base)
    }

    /// Stop the started model server once it has been idle for [`IDLE`].
    fn watch_idle(self: &Arc<Self>) {
        let weak: Weak<Self> = Arc::downgrade(self);
        std::thread::spawn(move || {
            loop {
                std::thread::sleep(Duration::from_secs(30));
                let Some(rt) = weak.upgrade() else { return };
                let idle = rt.last_used.lock().unwrap_or_else(|e| e.into_inner()).is_none_or(|t| t.elapsed() > IDLE);
                let mut guard = rt.running.lock().unwrap_or_else(|e| e.into_inner());
                if guard.is_none() {
                    return;
                }
                if idle {
                    tracing::info!("stopping the idle llama-server");
                    *guard = None;
                    return;
                }
            }
        });
    }

    /// Stop a started model server (before its files are replaced).
    pub fn stop(&self) {
        self.restart();
    }

    /// Stop a started model server (a restart after corrupted output).
    fn restart(&self) {
        *self.running.lock().unwrap_or_else(|e| e.into_inner()) = None;
    }

    /// One answer from the model (started when needed): output that looks
    /// corrupted restarts a started model server and is asked for once more.
    pub fn ask(self: &Arc<Self>, config: &Config, ctl: &Control, log: &dyn Fn(String), p: Prompt) -> Result<String, String> {
        let base = self.ensure(config, ctl, log)?;
        let reply = self.chat(&base, p)?;
        if stl_core::ai_tagging::is_corrupted_output(&reply) && config.ai.url.is_none() {
            log("The model is in a bad state: reloading it…".into());
            self.restart();
            let base = self.ensure(config, ctl, log)?;
            return self.chat(&base, p);
        }
        Ok(reply)
    }

    fn chat(&self, base: &str, p: Prompt) -> Result<String, String> {
        *self.last_used.lock().unwrap_or_else(|e| e.into_inner()) = Some(Instant::now());
        complete(&format!("{base}/v1/chat/completions"), None, p)
    }
}

/// `chat`: images and a prompt in, the reply text out, from an OpenAI-compatible
/// server at `base`; `remote` is OpenRouter's (API key, model).
/// A remote model: its API key and name; OpenRouter also gets its own fields.
struct Remote<'a> {
    key: &'a str,
    model: &'a str,
    openrouter: bool,
}

fn complete(url: &str, remote: Option<Remote>, p: Prompt) -> Result<String, String> {
    let b64 = base64::engine::general_purpose::STANDARD;
    let mut content: Vec<Value> = p
        .images
        .iter()
        .map(|b| json!({"type": "image_url", "image_url": {"url": format!("data:image/jpeg;base64,{}", b64.encode(b))}}))
        .collect();
    content.push(json!({"type": "text", "text": p.text}));
    let mut body = json!({
        "messages": [{"role": "user", "content": content}],
        "max_tokens": p.max_tokens,
        "temperature": p.temperature,
        "stream": false,
    });
    // Error statuses carry their reason in the body (rate limits, a bad key).
    let client: ureq::Agent = ureq::Agent::config_builder().timeout_global(Some(REQUEST_TIMEOUT)).http_status_as_error(false).build().into();
    let mut req = client.post(url).content_type("application/json");
    if let Some(r) = remote {
        body["model"] = r.model.into();
        if r.openrouter {
            // The short answers asked for leave no room for thinking. (Other
            // servers may refuse fields they do not know, such as Gemini's.)
            body["reasoning"] = json!({"enabled": false});
            req = req.header("X-Title", "StlArchivist");
        }
        req = req.header("Authorization", &format!("Bearer {}", r.key));
    }
    let mut resp = req.send(body.to_string()).map_err(|e| format!("AI server request failed: {e}"))?;
    let status = resp.status();
    let text = resp.body_mut().read_to_string().map_err(|e| format!("AI server reply: {e}"))?;
    parse_chat_response(&text).map_err(|e| {
        let code = serde_json::from_str::<Value>(&text).ok().and_then(|v| v["error"]["code"].as_u64());
        if status.as_u16() == 429 || code == Some(429) {
            format!("{RATE_LIMITED}: {e}")
        } else if status.is_success() {
            e
        } else {
            format!("{e} (HTTP {status})")
        }
    })
}

/// How a rate-limited request's error starts (OpenRouter's free models have
/// per-minute and daily limits): the batch job stops on it.
pub const RATE_LIMITED: &str = "Rate limit reached";

/// `_parse_chat_response`: the assistant message of a chat-completions body
/// (or the error an OpenAI-compatible server put there).
pub fn parse_chat_response(raw: &str) -> Result<String, String> {
    let v: Value = serde_json::from_str(raw).map_err(|e| format!("AI server returned malformed JSON: {e}"))?;
    if let Some(msg) = v["error"]["message"].as_str() {
        // OpenRouter puts the provider's own reason (what to do) in `metadata.raw`.
        return Err(match v["error"]["metadata"]["raw"].as_str() {
            Some(raw) => format!("AI server: {msg}: {raw}"),
            None => format!("AI server: {msg}"),
        });
    }
    let content = v["choices"].get(0).ok_or_else(|| format!("AI server returned no choices: {v}"))?["message"]["content"]
        .as_str()
        .ok_or("AI server response missing message content.")?;
    Ok(content.trim().to_string())
}

/// What the model suggests for one model.
#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
pub struct AiSuggestion {
    pub model_id: i64,
    pub title: String,
    pub archive_path: String,
    /// The images sent (their thumbnails), in order.
    pub image_ids: Vec<i64>,
    pub tags: Vec<String>,
    pub fields: AiFieldValues,
}

/// The metadata fields the tagger fills (`normalize_meta`).
#[derive(Debug, Clone, Default, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct AiFieldValues {
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub creator: String,
    #[serde(default)]
    pub sculptor: String,
    #[serde(default)]
    pub scale: String,
    #[serde(default)]
    pub description: String,
    /// `patreon`, `kickstarter`, `myminifactory` or empty.
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub year: Option<i64>,
}

impl From<AiFields> for AiFieldValues {
    fn from(f: AiFields) -> Self {
        AiFieldValues {
            title: f.title,
            creator: f.creator,
            sculptor: f.sculptor,
            scale: f.scale,
            description: f.description,
            source: f.source,
            year: f.year,
        }
    }
}

impl From<AiFieldValues> for AiFields {
    fn from(f: AiFieldValues) -> Self {
        AiFields {
            title: f.title,
            creator: f.creator,
            sculptor: f.sculptor,
            scale: f.scale,
            description: f.description,
            source: f.source,
            year: f.year,
        }
    }
}

/// A still JPEG for the model: animated GIF thumbnails are flattened (the
/// model looks at one frame, and they would be sent labelled as JPEG).
fn still(bytes: Vec<u8>) -> Vec<u8> {
    if bytes.starts_with(b"GIF8") {
        stl_core::thumbnail::make_thumbnail(&bytes, 4096, false).unwrap_or(bytes)
    } else {
        bytes
    }
}

/// One model's inputs and the images sent about it (their thumbnails).
struct Asking {
    inputs: AiInputs,
    images: Vec<Vec<u8>>,
    stems: Vec<String>,
    image_ids: Vec<i64>,
    title: String,
}

fn prepare(lib: &Library, model_id: i64) -> Result<Asking, String> {
    use stl_core::ai_tagging as T;
    let inputs: AiInputs = lib
        .db
        .with(|c| stl_core::storage::model_editor::ai_model_inputs(c, model_id))
        .map_err(|e| format!("database: {e}"))?
        .ok_or_else(|| format!("no model {model_id}"))?;
    let ids: Vec<u64> = inputs.images.iter().map(|&(i, _)| i as u64).collect();
    let thumbs: std::collections::HashMap<u64, Vec<u8>> = lib.thumbs.get_many(&ids).map_err(|e| e.to_string())?.into_iter().collect();
    let (mut images, mut stems, mut image_ids) = (Vec::new(), Vec::new(), Vec::new());
    for (id, filename) in &inputs.images {
        if let Some(b) = thumbs.get(&(*id as u64)) {
            images.push(still(b.clone()));
            stems.push(T::image_stem(*id, filename.as_deref()));
            image_ids.push(*id);
        }
    }
    let title = inputs.title.clone().filter(|t| !t.is_empty()).unwrap_or_else(|| stl_core::rules::MemberPath::new(&inputs.archive_path).stem().to_string());
    Ok(Asking { inputs, images, stems, image_ids, title })
}

/// Ask the library's model: OpenRouter or an OpenAI-compatible endpoint when
/// chosen; else a compute worker when there is one (see `workers`), else this
/// server's model.
fn ask_model(lib: &Library, ctl: &Control, log: &dyn Fn(String), images: &[Vec<u8>], text: &str, max_tokens: u32) -> Result<String, String> {
    let temperature = LibrarySettings::load(&lib.config.data_dir).ai_temperature();
    let p = Prompt { images, text, max_tokens, temperature };
    match Provider::of(lib) {
        Provider::OpenRouter { model } => return ask_openrouter(lib, &model, p),
        Provider::OpenAi { model } => return ask_openai(lib, &model, p),
        Provider::Local => {}
    }
    match lib.workers.chat(lib.config.compute, ctl, log, p) {
        Some(reply) => reply,
        None => lib.ai.ask(&lib.config, ctl, log, p),
    }
}

/// `_generate_suggestions`: tags and metadata for one model. Output that looks
/// corrupted restarts the model server and is asked for once more.
pub fn suggest(lib: &Library, ctl: &Control, log: &dyn Fn(String), model_id: i64, max_tags: usize, ignore_meta: bool) -> Result<AiSuggestion, String> {
    use stl_core::ai_tagging as T;
    let a = prepare(lib, model_id)?;
    let known = if ignore_meta { Vec::new() } else { T::known_meta(&a.inputs) };
    let ask = |text: &str, max_tokens: u32| ask_model(lib, ctl, log, &a.images, text, max_tokens);
    log(format!("{}: generating tags…", a.title));
    let raw_tags = ask(&T::build_tags_prompt(&a.stems, &a.inputs.archive_path, &known), 120)?;
    let tags = T::clean_tags(&raw_tags, max_tags);
    if ctl.is_stopped() {
        return Err("stopped".into());
    }
    log(format!("{}: extracting metadata…", a.title));
    let raw_meta = ask(&T::build_meta_prompt(&a.inputs.archive_path, &known), 200)?;
    let meta = T::parse_meta(&raw_meta, &a.inputs.archive_path, &a.stems, &a.inputs.files);
    Ok(AiSuggestion {
        model_id,
        title: a.title,
        archive_path: a.inputs.archive_path,
        image_ids: a.image_ids,
        tags,
        fields: T::normalize_meta(&meta).into(),
    })
}

/// What the model infers for the editor's fields ("AI data"); empty when unknown.
#[derive(Debug, Clone, Default, Serialize, ToSchema)]
pub struct AiEditorFields {
    pub model_id: i64,
    pub title: String,
    pub creator: String,
    /// The editor's *Type*.
    pub source: String,
    /// `YYYY-MM` or `YYYY`.
    pub date: String,
    pub sculptor: String,
    pub release: String,
    pub collection: String,
    pub subcollection: String,
    pub category: String,
    /// Only scales written in the path, file names or metadata.
    pub scales: Vec<String>,
    pub description: String,
    pub tags: Vec<String>,
}

/// Library names shown in the "AI data" prompt per list, at most.
const VOCAB_CAP: usize = 40;

/// "AI data": as many of the editor's fields as the model can infer from the
/// images, path, file names and catalogued metadata, reusing the library's
/// names (creators, sculptors, collections, scales…). Nothing is saved.
pub fn infer_fields(lib: &Library, ctl: &Control, log: &dyn Fn(String), model_id: i64) -> Result<AiEditorFields, String> {
    use stl_core::ai_tagging as T;
    let a = prepare(lib, model_id)?;
    let choices = lib.db.with(stl_core::storage::model_editor::editor_choices).map_err(|e| format!("database: {e}"))?;
    let list = |name: &str| choices.iter().find(|(n, _)| *n == name).map(|(_, v)| v.as_slice()).unwrap_or_default();
    let lists: [(&str, &[String]); 8] = [
        ("CREATOR", list("creators")),
        ("TYPE", list("sources")),
        ("SCULPTOR", list("sculptors")),
        ("RELEASE", list("releases")),
        ("COLLECTION", list("collections")),
        ("SUBCOLLECTION", list("subcollections")),
        ("CATEGORY", list("categories")),
        ("SCALE", list("scales")),
    ];
    let context = std::iter::once(a.inputs.archive_path.as_str()).chain(a.inputs.files.iter().map(String::as_str)).collect::<Vec<_>>().join(" ");
    let vocab: Vec<(&str, Vec<&str>)> = lists.iter().map(|(k, names)| (*k, T::relevant_names(names, &context, VOCAB_CAP))).collect();
    log(format!("{}: inferring metadata…", a.title));
    let prompt = T::build_fields_prompt(&a.inputs.archive_path, &T::known_meta(&a.inputs), &vocab);
    let raw = ask_model(lib, ctl, log, &a.images, &prompt, 400)?;
    let parsed = T::parse_fields(&raw, &a.inputs.archive_path, &a.stems, &a.inputs.files, &lists);
    let get = |k: &str| parsed.iter().find(|(key, _)| key == k).map(|(_, v)| v.clone()).unwrap_or_default();
    let split = |k: &str| get(k).split(", ").filter(|s| !s.is_empty()).map(str::to_string).collect();
    Ok(AiEditorFields {
        model_id,
        title: get("TITLE"),
        creator: get("CREATOR"),
        source: get("TYPE"),
        date: get("DATE"),
        sculptor: get("SCULPTOR"),
        release: get("RELEASE"),
        collection: get("COLLECTION"),
        subcollection: get("SUBCOLLECTION"),
        category: get("CATEGORY"),
        scales: split("SCALE"),
        description: get("DESCRIPTION"),
        tags: split("TAGS"),
    })
}

/// The editor form with the inferred fields written in: empty fields only,
/// unless `overwrite`; scales when it has none (replaced with `overwrite`);
/// new tags added. The labels of the fields that changed.
pub fn fill_form(form: &mut stl_core::storage::model_editor::EditorForm, f: &AiEditorFields, overwrite: bool) -> Vec<&'static str> {
    let mut changed = Vec::new();
    let pairs: [(&'static str, &mut String, &str); 10] = [
        ("title", &mut form.title, &f.title),
        ("creator", &mut form.creator, &f.creator),
        ("type", &mut form.source, &f.source),
        ("date", &mut form.date, &f.date),
        ("sculptor", &mut form.sculptor, &f.sculptor),
        ("release", &mut form.release, &f.release),
        ("collection", &mut form.collection, &f.collection),
        ("subcollection", &mut form.subcollection, &f.subcollection),
        ("category", &mut form.category, &f.category),
        ("description", &mut form.description, &f.description),
    ];
    for (label, field, value) in pairs {
        let v = value.trim();
        if !v.is_empty() && (overwrite || field.trim().is_empty()) && field.trim() != v {
            *field = v.to_string();
            changed.push(label);
        }
    }
    if !f.scales.is_empty() && (overwrite || form.scales.is_empty()) && form.scales != f.scales {
        form.scales = f.scales.clone();
        changed.push("scales");
    }
    let have: std::collections::HashSet<String> = form.tags.iter().map(|t| t.to_lowercase()).collect();
    let new: Vec<String> = f.tags.iter().filter(|t| !t.is_empty() && !have.contains(&t.to_lowercase())).cloned().collect();
    if !new.is_empty() {
        form.tags.extend(new);
        changed.push("tags");
    }
    changed
}

/// "AI fill" for one model: infer its fields and save them ([`fill_form`]).
/// The archive path when something was saved.
pub fn fill(lib: &Library, ctl: &Control, log: &dyn Fn(String), model_id: i64, overwrite: bool) -> Result<Option<String>, String> {
    use stl_core::storage::model_editor as E;
    let f = infer_fields(lib, ctl, log, model_id)?;
    let Some(model) = lib.db.with(|c| E::model_for_editor(c, model_id)).map_err(|e| format!("database: {e}"))? else {
        return Ok(None);
    };
    let mut form = model.form();
    let changed = fill_form(&mut form, &f, overwrite);
    if changed.is_empty() {
        log(format!("#{model_id}: nothing new"));
        return Ok(None);
    }
    let saved = lib.db.with_tx(|tx| E::save_model_editor(tx, model_id, &form)).map_err(|e| e.to_string())?;
    log(format!("{}: {}", if form.title.is_empty() { format!("#{model_id}") } else { form.title.clone() }, changed.join(", ")));
    Ok(saved.flatten())
}

/// A path pattern the model inferred for a folder (`ai_pattern`).
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct AiPattern {
    pub folder: String,
    pub pattern: String,
    /// What it reads from a few of the sampled archives (paths below the folder).
    pub samples: Vec<crate::api::libraries::PatternSample>,
    /// Sampled archives it reads something from…
    pub fits: usize,
    /// …of this many.
    pub sampled: usize,
}

/// Archive paths shown to the model when inferring a pattern, at most.
const PATTERN_SAMPLES: usize = 40;

/// The path pattern for crawling `folder`, inferred by the model from archive
/// paths below it (spread over its sub-folders) and the library's known names.
pub fn infer_pattern(lib: &Library, ctl: &Control, log: &dyn Fn(String), folder: &str, ids: &[i64]) -> Result<AiPattern, String> {
    use stl_core::ai_tagging as T;
    let mut rel: Vec<String> = if ids.is_empty() {
        let root = folder.trim_end_matches(['/', '\\']);
        stl_core::crawler::list_archives(root, true, &[])
            .into_iter()
            .filter_map(|p| p.get(root.len()..).map(|r| r.trim_start_matches(['/', '\\']).replace('\\', "/")))
            .collect()
    } else {
        // The models' paths below their library folders (what a mass edit pattern reads the end of).
        let json = serde_json::to_string(ids).map_err(|e| e.to_string())?;
        lib.db
            .with(|c| {
                let mut st = c.prepare("SELECT rel_path FROM models WHERE id IN (SELECT value FROM json_each(?1))")?;
                st.query_map([json], |r| r.get::<_, String>(0))?.collect::<rusqlite::Result<Vec<_>>>()
            })
            .map_err(|e| format!("database: {e}"))?
    };
    rel.sort();
    if rel.is_empty() {
        return Err(if ids.is_empty() { format!("no archives below {folder}") } else { "none of the models exist".into() });
    }
    let samples = T::spread(&rel, PATTERN_SAMPLES);
    let choices = lib.db.with(stl_core::storage::model_editor::editor_choices).map_err(|e| format!("database: {e}"))?;
    let context = samples.join(" ");
    let vocab: Vec<(&str, Vec<&str>)> = [("creator", "creators"), ("sculptor", "sculptors"), ("release", "releases"), ("collection", "collections"), ("subcollection", "subcollections")]
        .into_iter()
        .map(|(token, list)| {
            let names = choices.iter().find(|(n, _)| *n == list).map(|(_, v)| v.as_slice()).unwrap_or_default();
            (token, T::relevant_names(names, &context, VOCAB_CAP))
        })
        .collect();
    log(format!("Asking the model about {} of {} archive path(s)…", samples.len(), rel.len()));
    let raw = ask_model(lib, ctl, log, &[], &T::build_pattern_prompt(&samples, &vocab), 100)?;
    let pattern = T::parse_pattern(&raw).ok_or_else(|| format!("the model's answer is not a usable pattern: {raw}"))?;
    let reads: Vec<(String, stl_core::path_pattern::Fields)> = samples.iter().map(|p| (p.clone(), stl_core::path_pattern::parse_path(&pattern, p))).collect();
    let fits = reads.iter().filter(|(_, f)| !f.is_empty()).count();
    log(format!("Pattern: {pattern} (reads {fits} of {} sampled paths)", reads.len()));
    let shown = T::spread(&reads, 5)
        .into_iter()
        .map(|(path, fields)| crate::api::libraries::PatternSample {
            path,
            fields: fields.into_iter().map(|(field, value)| crate::api::libraries::SampleField { field, value }).collect(),
        })
        .collect();
    Ok(AiPattern { folder: folder.to_string(), pattern, samples: shown, fits, sampled: reads.len() })
}

/// A metadata problem the model found in one model (`ai_check`).
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct AiIssue {
    pub model_id: i64,
    pub title: String,
    pub archive_path: String,
    /// `Title`, `Creator`, `Sculptor`, `Category`, `Collection`, `Subcollection`, `Scale`, `Source`, `Year` or `Description`.
    pub field: String,
    /// The catalogued value.
    pub value: String,
    pub problem: String,
    /// What the model proposes instead (empty: nothing).
    pub suggestion: String,
}

/// The metadata problems the model finds in one model's catalogued fields,
/// judged against its path, file names and the library's names (text only).
pub fn check(lib: &Library, ctl: &Control, log: &dyn Fn(String), model_id: i64) -> Result<Vec<AiIssue>, String> {
    use stl_core::ai_tagging as T;
    let a = prepare(lib, model_id)?;
    let choices = lib.db.with(stl_core::storage::model_editor::editor_choices).map_err(|e| format!("database: {e}"))?;
    let context = std::iter::once(a.inputs.archive_path.as_str())
        .chain(a.inputs.files.iter().map(String::as_str))
        .chain(a.inputs.meta.iter().map(|(_, v)| v.as_str()))
        .collect::<Vec<_>>()
        .join(" ");
    let vocab: Vec<(&str, Vec<&str>)> = [("Creator", "creators"), ("Sculptor", "sculptors"), ("Category", "categories"), ("Collection", "collections"), ("Subcollection", "subcollections"), ("Scale", "scales")]
        .into_iter()
        .map(|(label, list)| {
            let names = choices.iter().find(|(n, _)| *n == list).map(|(_, v)| v.as_slice()).unwrap_or_default();
            (label, T::relevant_names(names, &context, VOCAB_CAP))
        })
        .collect();
    log(format!("{}: checking metadata…", a.title));
    let raw = ask_model(lib, ctl, log, &[], &T::build_check_prompt(&a.inputs.archive_path, &T::known_meta(&a.inputs), &vocab), 300)?;
    let value = |f: &str| a.inputs.meta.iter().find(|(k, _)| k == f).map(|(_, v)| v.clone()).unwrap_or_default();
    Ok(T::parse_issues(&raw, &a.inputs.meta)
        .into_iter()
        .map(|i| AiIssue {
            model_id,
            title: a.title.clone(),
            archive_path: a.inputs.archive_path.clone(),
            value: value(&i.field),
            field: i.field,
            problem: i.problem,
            suggestion: i.suggestion,
        })
        .collect())
}

/// `ai_commit`: write the fields (only empty ones unless `overwrite`) and merge
/// the tags. Returns the archive path, or `None` when the model is gone.
pub fn commit(lib: &Library, model_id: i64, fields: AiFieldValues, tags: &[String], overwrite: bool) -> Result<Option<String>, String> {
    let fields: AiFields = fields.into();
    let done = lib
        .db
        .with_tx(|tx| stl_core::storage::model_editor::ai_commit(tx, model_id, &fields, tags, overwrite))
        .map_err(|e| e.to_string())?;
    if !done {
        return Ok(None);
    }
    let path = lib.db.with(|c| stl_core::storage::model_editor::archive_paths(c, &[model_id])).map_err(|e| e.to_string())?;
    Ok(path.into_values().next())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chat_responses() {
        assert_eq!(parse_chat_response(r#"{"choices":[{"message":{"content":"  a, b \n"}}]}"#).unwrap(), "a, b");
        assert!(parse_chat_response(r#"{"choices":[]}"#).unwrap_err().contains("no choices"));
        assert!(parse_chat_response("nope").unwrap_err().contains("malformed"));
        assert!(parse_chat_response(r#"{"choices":[{"message":{}}]}"#).is_err());
        assert_eq!(parse_chat_response(r#"{"error":{"message":"Rate limit exceeded","code":429}}"#).unwrap_err(), "AI server: Rate limit exceeded");
        assert_eq!(
            parse_chat_response(r#"{"error":{"message":"Provider returned error","code":429,"metadata":{"raw":"m is rate-limited upstream"}}}"#).unwrap_err(),
            "AI server: Provider returned error: m is rate-limited upstream"
        );
    }
}
