//! Native answers to what the desktop app does with Python libraries:
//! CLIP embeddings (ONNX Runtime) and PDF previews (pdfium). Both libraries
//! are loaded at runtime, so the server runs without them: a missing library
//! or model just turns the feature off, as a missing package does in the app.
//!
//! - ONNX Runtime: `onnxruntime` in `server.json`, `ORT_DYLIB_PATH`, or
//!   `onnxruntime.dll` / `libonnxruntime.so` / `.dylib` next to the server or in
//!   the data directory. The CLIP model is found as `image_embedding.model_path`
//!   does: `STLLIBRARY_CLIP_MODEL`, then `<data_dir>/models/clip_image_encoder.onnx`.
//!   The encoder runs on a GPU when the loaded ONNX Runtime build supports one
//!   for the GPUs found ([`gpu_providers`]: DirectML on Windows; CUDA, MIGraphX,
//!   ROCm or OpenVINO on Linux), else on the CPU.
//! - pdfium: `pdfium` in `server.json`, `STL_PDFIUM_PATH`, or the platform's
//!   pdfium library next to the server or in the data directory.

use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use ort::ep::{self, ExecutionProviderDispatch};
use ort::session::Session;
use ort::value::Tensor;
use pdfium_render::prelude::{PdfRenderConfig, Pdfium};

use crate::config::Config;

pub const MODEL_FILENAME: &str = "clip_image_encoder.onnx";
/// Images per inference call (`ai_embeddings_batch_size`'s default).
pub const BATCH: usize = 32;

/// `name` in the server's directory or the data directory, whichever exists.
fn beside(config: &Config, name: &str) -> Option<PathBuf> {
    let exe_dir = std::env::current_exe().ok().and_then(|p| p.parent().map(Path::to_path_buf));
    [exe_dir, Some(config.data_dir.clone())].into_iter().flatten().map(|d| d.join(name)).find(|p| p.is_file())
}

fn lib_name(base: &str) -> String {
    format!("{}{base}{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX)
}

// ── CLIP ─────────────────────────────────────────────────────────────────────

pub struct Embedder {
    session: Mutex<Session>,
    input: String,
    output: String,
    pub model: PathBuf,
    /// Where it runs: `CPU`, `DirectML`, `CUDA`, `MIGraphX`, `ROCm`, `OpenVINO`.
    pub provider: &'static str,
}

/// ONNX Runtime allows one environment per process; it is kept once loaded
/// (a failed load is retried, possibly with another configuration).
static ORT_ENV: OnceLock<ort::environment::Environment> = OnceLock::new();
static ORT_INIT: Mutex<()> = Mutex::new(());

fn ort_env(config: &Config) -> Result<&'static ort::environment::Environment, String> {
    let _guard = ORT_INIT.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(env) = ORT_ENV.get() {
        return Ok(env);
    }
    let dylib = config
        .onnxruntime
        .clone()
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("ORT_DYLIB_PATH").map(PathBuf::from))
        .or_else(|| beside(config, &lib_name("onnxruntime")))
        .ok_or("ONNX Runtime not found (set \"onnxruntime\" in server.json or ORT_DYLIB_PATH)")?;
    // ONNX Runtime loads DirectML.dll by name, which would find the older one
    // in System32: load the one installed beside it first.
    let directml = dylib.with_file_name("DirectML.dll");
    if cfg!(windows) && directml.is_file() {
        ort::util::preload_dylib(&directml).map_err(|e| format!("cannot load {}: {e}", directml.display()))?;
    }
    let env = ort::init_from(&dylib)
        .map_err(|e| format!("cannot load ONNX Runtime {}: {e}", dylib.display()))?
        .with_name("stlLibrary")
        .build()
        .map_err(|e| format!("ONNX Runtime: {e}"))?;
    Ok(ORT_ENV.get_or_init(|| env))
}

/// The GPU execution providers to try, best first, for the GPUs found. Each is
/// skipped when the ONNX Runtime build lacks it or it fails to start (no
/// driver, CUDA or ROCm libraries missing).
fn gpu_providers(gpus: &[&str]) -> Vec<(&'static str, ExecutionProviderDispatch)> {
    let mut eps = Vec::new();
    if cfg!(windows) {
        eps.push(("DirectML", ep::DirectML::default().build()));
        return eps;
    }
    for gpu in gpus {
        match *gpu {
            "nvidia" => eps.push(("CUDA", ep::CUDA::default().build())),
            "amd" => {
                eps.push(("MIGraphX", ep::MIGraphX::default().build()));
                eps.push(("ROCm", ep::ROCm::default().build()));
            }
            "intel" => eps.push(("OpenVINO", ep::OpenVINO::default().with_device_type("GPU").build())),
            _ => {}
        }
    }
    eps
}

fn session(env: &ort::environment::Environment, model: &Path, provider: Option<&ExecutionProviderDispatch>) -> Result<Session, String> {
    let mut b = Session::builder(env).map_err(|e| e.to_string())?;
    if let Some(p) = provider {
        if p.name() == "DmlExecutionProvider" {
            // DirectML supports neither memory patterns nor parallel execution.
            b = b.with_memory_pattern(false).map_err(|e| e.to_string())?;
        }
        p.register(&mut b).map_err(|e| e.to_string())?;
    }
    b.commit_from_file(model).map_err(|e| e.to_string())
}

pub fn model_path(config: &Config) -> Option<PathBuf> {
    std::env::var_os("STLLIBRARY_CLIP_MODEL")
        .map(PathBuf::from)
        .into_iter()
        .chain([config.data_dir.join("models").join(MODEL_FILENAME)])
        .find(|p| p.is_file())
}

impl Embedder {
    pub fn load(config: &Config) -> Result<Embedder, String> {
        let model = model_path(config).ok_or_else(|| {
            format!("CLIP model not found (place {MODEL_FILENAME} in <data dir>/models or set STLLIBRARY_CLIP_MODEL)")
        })?;
        let env = ort_env(config)?;
        let mut found = None;
        for (name, p) in gpu_providers(&crate::components::gpus()) {
            match session(env, &model, Some(&p)) {
                Ok(s) => {
                    found = Some((s, name));
                    break;
                }
                Err(e) => tracing::info!("CLIP on {name} unavailable: {e}"),
            }
        }
        let (session, provider) = match found {
            Some(f) => f,
            None => (session(env, &model, None).map_err(|e| format!("cannot load the CLIP model {}: {e}", model.display()))?, "CPU"),
        };
        tracing::info!("CLIP encoder runs on {provider}");
        let input = session.inputs().first().map(|o| o.name().to_string()).ok_or("the model has no input")?;
        let names: Vec<String> = session.outputs().iter().map(|o| o.name().to_string()).collect();
        // As image_embedding._get_session: prefer pooler_output (Xenova exports).
        let output = if names.iter().any(|n| n == "pooler_output") { "pooler_output".to_string() } else {
            names.first().cloned().ok_or("the model has no output")?
        };
        Ok(Embedder { session: Mutex::new(session), input, output, model, provider })
    }

    /// L2-normalised float32 vectors (as little-endian bytes) for images;
    /// `None` for an image that cannot be decoded or a failed batch.
    pub fn embed(&self, images: &[Vec<u8>]) -> Vec<Option<Vec<u8>>> {
        let mut out = vec![None; images.len()];
        for start in (0..images.len()).step_by(BATCH) {
            let end = (start + BATCH).min(images.len());
            let mut idx = Vec::new();
            let mut data = Vec::new();
            for (i, img) in images.iter().enumerate().take(end).skip(start) {
                if let Ok(t) = stl_core::clip::preprocess(img) {
                    idx.push(i);
                    data.extend_from_slice(&t);
                }
            }
            if idx.is_empty() {
                continue;
            }
            match self.run(idx.len(), data) {
                Ok(vectors) => {
                    for (i, v) in idx.into_iter().zip(vectors) {
                        out[i] = Some(v);
                    }
                }
                Err(e) => tracing::warn!("CLIP batch failed: {e}"),
            }
        }
        out
    }

    fn run(&self, n: usize, data: Vec<f32>) -> Result<Vec<Vec<u8>>, String> {
        let edge = stl_core::clip::INPUT as usize;
        let tensor = Tensor::from_array(([n, 3, edge, edge], data)).map_err(|e| e.to_string())?;
        let mut session = self.session.lock().unwrap_or_else(|e| e.into_inner());
        let outputs = session.run(ort::inputs![self.input.as_str() => tensor]).map_err(|e| e.to_string())?;
        let (shape, values) = outputs[self.output.as_str()].try_extract_tensor::<f32>().map_err(|e| e.to_string())?;
        let dim = shape.last().copied().unwrap_or(0) as usize;
        if dim == 0 || values.len() != n * dim {
            return Err(format!("unexpected output shape {shape:?}"));
        }
        Ok(values
            .chunks(dim)
            .map(|v| {
                let mut v = v.to_vec();
                stl_core::clip::normalize(&mut v);
                v.iter().flat_map(|x| x.to_le_bytes()).collect()
            })
            .collect())
    }
}

// ── PDF ──────────────────────────────────────────────────────────────────────

pub struct PdfRenderer {
    pdfium: Mutex<Pdfium>,
}

impl PdfRenderer {
    pub fn load(config: &Config) -> Result<PdfRenderer, String> {
        let path = config
            .pdfium
            .clone()
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("STL_PDFIUM_PATH").map(PathBuf::from))
            .or_else(|| beside(config, &lib_name("pdfium")))
            .ok_or("pdfium not found (set \"pdfium\" in server.json or STL_PDFIUM_PATH)")?;
        let bindings = Pdfium::bind_to_library(&path).map_err(|e| format!("cannot load pdfium {}: {e}", path.display()))?;
        Ok(PdfRenderer { pdfium: Mutex::new(Pdfium::new(bindings)) })
    }

    /// The first page at 2x zoom on white (as `pdf_first_page_to_image_bytes`),
    /// as a thumbnail no larger than `max_size`.
    pub fn thumbnail(&self, pdf: &[u8], max_size: u32) -> Option<Vec<u8>> {
        let png = {
            let pdfium = self.pdfium.lock().unwrap_or_else(|e| e.into_inner());
            let doc = pdfium.load_pdf_from_byte_slice(pdf, None).ok()?;
            let page = doc.pages().get(0).ok()?;
            let bitmap = page.render_with_config(&PdfRenderConfig::new().scale_page_by_factor(2.0)).ok()?;
            let img = bitmap.as_image().ok()?.into_rgb8();
            let mut png = std::io::Cursor::new(Vec::new());
            img.write_to(&mut png, image::ImageFormat::Png).ok()?;
            png.into_inner()
        };
        stl_core::thumbnail::make_thumbnail(&png, max_size, false).ok()
    }
}

/// How long a failed load is remembered before it is tried again.
const RETRY_AFTER: Duration = Duration::from_secs(300);

/// The tools, loaded on first use and kept once loaded. A failure is logged
/// once and tried again after [`RETRY_AFTER`], or at once after [`Tools::retry`]
/// (when components were just installed).
#[derive(Default)]
pub struct Tools {
    embedder: OnceLock<Embedder>,
    embedder_failed: Mutex<Option<Instant>>,
    pdf: OnceLock<PdfRenderer>,
    pdf_failed: Mutex<Option<Instant>>,
}

fn cached<'a, T>(cell: &'a OnceLock<T>, failed: &Mutex<Option<Instant>>, what: &str, load: impl FnOnce() -> Result<T, String>) -> Option<&'a T> {
    if let Some(v) = cell.get() {
        return Some(v);
    }
    let mut failed = failed.lock().unwrap_or_else(|e| e.into_inner());
    if failed.is_some_and(|t| t.elapsed() < RETRY_AFTER) {
        return None;
    }
    match load() {
        Ok(v) => {
            tracing::info!("{what} available");
            *failed = None;
            let _ = cell.set(v);
            cell.get()
        }
        Err(e) => {
            if failed.is_none() {
                tracing::info!("{what} unavailable: {e}");
            }
            *failed = Some(Instant::now());
            None
        }
    }
}

impl Tools {
    pub fn embedder(&self, config: &Config) -> Option<&Embedder> {
        cached(&self.embedder, &self.embedder_failed, "CLIP embeddings", || Embedder::load(config))
    }

    /// Where the loaded CLIP encoder runs (`None`: not loaded yet).
    pub fn clip_provider(&self) -> Option<&'static str> {
        self.embedder.get().map(|e| e.provider)
    }

    /// ONNX Runtime is loaded in this process (a new one needs a restart).
    pub fn ort_loaded(&self) -> bool {
        ORT_ENV.get().is_some()
    }

    pub fn pdf(&self, config: &Config) -> Option<&PdfRenderer> {
        cached(&self.pdf, &self.pdf_failed, "PDF previews", || PdfRenderer::load(config))
    }

    /// Forget earlier failures: try loading again on next use.
    pub fn retry(&self) {
        *self.embedder_failed.lock().unwrap_or_else(|e| e.into_inner()) = None;
        *self.pdf_failed.lock().unwrap_or_else(|e| e.into_inner()) = None;
    }
}
