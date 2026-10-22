//! The AI tagger's model: an OpenAI-compatible `llama-server` (llama.cpp) with
//! Qwen2.5-VL, as in the desktop app. The server starts it on first use from
//! the desktop's install (`<data dir>/ai/bin`, `<data dir>/ai/models`) or the
//! paths in `server.json`, stops it after [`IDLE`] without requests and when
//! the server exits; or it talks to one already running (`ai.url`).

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, Weak};
use std::time::{Duration, Instant};

use base64::Engine;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use stl_core::control::Control;
use stl_core::storage::model_edit::{AiFields, AiInputs};
use utoipa::ToSchema;

use crate::library_settings::LibrarySettings;
use crate::{Config, Library};

/// The desktop's model files (`app/utils/ai_runtime.py`).
pub const MODEL_FILE: &str = "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf";
pub const MMPROJ_FILE: &str = "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf";
pub const MODEL_LABEL: &str = "Qwen2.5-VL-3B-Instruct (GGUF)";
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
    let model = cfg.model.clone().map_or_else(|| ai_dir.join("models").join(MODEL_FILE), PathBuf::from);
    let mmproj = cfg.mmproj.clone().map_or_else(|| ai_dir.join("models").join(MMPROJ_FILE), PathBuf::from);
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
        if let Some(url) = &config.ai.url {
            return AiStatus { available: true, running: false, model: MODEL_LABEL.into(), detail: format!("using {url}") };
        }
        match launch_spec(config) {
            Ok(l) => AiStatus { available: true, running, model: MODEL_LABEL.into(), detail: format!("{}", l.exe.display()) },
            Err(e) => AiStatus { available: false, running, model: MODEL_LABEL.into(), detail: e },
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
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
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
        let reply = self.chat(&base, p.images, p.text, p.max_tokens, p.temperature)?;
        if stl_core::ai_tagging::is_corrupted_output(&reply) && config.ai.url.is_none() {
            log("The model is in a bad state: reloading it…".into());
            self.restart();
            let base = self.ensure(config, ctl, log)?;
            return self.chat(&base, p.images, p.text, p.max_tokens, p.temperature);
        }
        Ok(reply)
    }

    /// `chat`: images and a prompt in, the reply text out.
    fn chat(&self, base: &str, images: &[Vec<u8>], prompt: &str, max_tokens: u32, temperature: f64) -> Result<String, String> {
        *self.last_used.lock().unwrap_or_else(|e| e.into_inner()) = Some(Instant::now());
        let b64 = base64::engine::general_purpose::STANDARD;
        let mut content: Vec<Value> = images
            .iter()
            .map(|b| json!({"type": "image_url", "image_url": {"url": format!("data:image/jpeg;base64,{}", b64.encode(b))}}))
            .collect();
        content.push(json!({"type": "text", "text": prompt}));
        let body = json!({
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": false,
        });
        let mut resp = agent(REQUEST_TIMEOUT)
            .post(&format!("{base}/v1/chat/completions"))
            .content_type("application/json")
            .send(body.to_string())
            .map_err(|e| format!("AI server request failed: {e}"))?;
        let text = resp.body_mut().read_to_string().map_err(|e| format!("AI server reply: {e}"))?;
        parse_chat_response(&text)
    }
}

/// `_parse_chat_response`: the assistant message of a chat-completions body.
pub fn parse_chat_response(raw: &str) -> Result<String, String> {
    let v: Value = serde_json::from_str(raw).map_err(|e| format!("AI server returned malformed JSON: {e}"))?;
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
    pub collection: String,
    #[serde(default)]
    pub subcollection: String,
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
            collection: f.collection,
            subcollection: f.subcollection,
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
            collection: f.collection,
            subcollection: f.subcollection,
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

/// `_generate_suggestions`: tags and metadata for one model. Output that looks
/// corrupted restarts the model server and is asked for once more.
pub fn suggest(lib: &Library, ctl: &Control, log: &dyn Fn(String), model_id: i64, max_tags: usize, ignore_meta: bool) -> Result<AiSuggestion, String> {
    use stl_core::ai_tagging as T;
    let inputs: AiInputs = lib
        .db
        .with(|c| stl_core::storage::model_edit::ai_model_inputs(c, model_id))
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
    let known = if ignore_meta { Vec::new() } else { T::known_meta(&inputs) };
    let temperature = LibrarySettings::load(&lib.config.data_dir).ai_temperature();
    let title = inputs.title.clone().filter(|t| !t.is_empty()).unwrap_or_else(|| stl_core::rules::MemberPath::new(&inputs.archive_path).stem().to_string());

    // A compute worker when there is one (see `workers`), else this server's model.
    let ask = |text: &str, max_tokens: u32| -> Result<String, String> {
        let p = Prompt { images: &images, text, max_tokens, temperature };
        match lib.workers.chat(lib.config.compute, ctl, log, p) {
            Some(reply) => reply,
            None => lib.ai.ask(&lib.config, ctl, log, p),
        }
    };
    log(format!("{title}: generating tags…"));
    let raw_tags = ask(&T::build_tags_prompt(&stems, &inputs.archive_path, &known), 120)?;
    let tags = T::clean_tags(&raw_tags, max_tags);
    if ctl.is_stopped() {
        return Err("stopped".into());
    }
    log(format!("{title}: extracting metadata…"));
    let raw_meta = ask(&T::build_meta_prompt(&inputs.archive_path, &known), 200)?;
    let meta = T::parse_meta(&raw_meta, &inputs.archive_path, &stems, &inputs.files);
    Ok(AiSuggestion {
        model_id,
        title,
        archive_path: inputs.archive_path,
        image_ids,
        tags,
        fields: T::normalize_meta(&meta).into(),
    })
}

/// `ai_commit`: write the fields (only empty ones unless `overwrite`) and merge
/// the tags. Returns the archive path, or `None` when the model is gone.
pub fn commit(lib: &Library, model_id: i64, fields: AiFieldValues, tags: &[String], overwrite: bool) -> Result<Option<String>, String> {
    let fields: AiFields = fields.into();
    let done = lib
        .db
        .with_tx(|tx| stl_core::storage::model_edit::ai_commit(tx, model_id, &fields, tags, overwrite))
        .map_err(|e| e.to_string())?;
    if !done {
        return Ok(None);
    }
    let path = lib.db.with(|c| stl_core::storage::model_edit::archive_paths(c, &[model_id])).map_err(|e| e.to_string())?;
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
    }
}
