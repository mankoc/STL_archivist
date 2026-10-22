//! Optional components the server can download into the data directory: the AI
//! tagger's runtime (llama.cpp) and model, the CLIP image encoder, ONNX Runtime
//! and pdfium. Everything is pinned here (URL, size, SHA-256): clients choose
//! components by id, never by URL or path. Files land where the server already
//! looks for them (`ai`, `tools`), so an install needs no configuration.
//!
//! Downloads resume (`.part` files under `<data dir>/downloads`), are verified
//! before use, and archives are unpacked into a staging folder first, so a
//! failed install never leaves a half-installed component behind.

use std::fs::{self, File};
use std::collections::BTreeMap;
use std::io::{self, Read, Write};
use std::path::{Component as PathPart, Path, PathBuf};
use std::time::Duration;

use serde::Serialize;
use sha2::{Digest, Sha256};
use stl_core::control::Control;
use utoipa::ToSchema;

use crate::Library;

/// How an asset becomes files in the data directory.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Unpack {
    /// The download is the file itself, saved as `to`.
    File,
    /// A zip archive extracted into the folder `to`.
    Zip,
    /// A `.tar.gz` extracted into the folder `to`, without its top `strip` folders.
    Tar { strip: usize },
    /// Members of a zip, each saved as its destination (relative to the data
    /// directory; the first is `to`).
    ZipPick(&'static [(&'static str, &'static str)]),
    /// Members of a `.tar.gz`, as [`Unpack::ZipPick`].
    TarPick(&'static [(&'static str, &'static str)]),
}

/// One file to download.
#[derive(Debug, Clone, PartialEq)]
pub struct Asset {
    /// File name of the download (also its name on a mirror).
    pub file: &'static str,
    pub url: String,
    pub sha256: &'static str,
    pub size: u64,
    pub unpack: Unpack,
    /// Destination relative to the data directory (a file or a folder, see [`Unpack`]).
    pub to: &'static str,
}

impl Asset {
    /// The files it puts in the data directory (none for a folder).
    fn outputs(&self) -> Vec<&'static str> {
        match self.unpack {
            Unpack::File => vec![self.to],
            Unpack::ZipPick(m) | Unpack::TarPick(m) => m.iter().map(|(_, to)| *to).collect(),
            Unpack::Zip | Unpack::Tar { .. } => Vec::new(),
        }
    }
}

const LLAMA: &str = "https://github.com/ggml-org/llama.cpp/releases/download/b9789/";
const QWEN: &str = "https://huggingface.co/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF/resolve/5037fcf163dd95d1e41d1974465f0898ed108ca2/";
const ORT: &str = "https://github.com/microsoft/onnxruntime/releases/download/v1.22.0/";
const ORT_DML: &str = "https://api.nuget.org/v3-flatcontainer/microsoft.ml.onnxruntime.directml/1.22.0/";
const DIRECTML: &str = "https://api.nuget.org/v3-flatcontainer/microsoft.ai.directml/1.15.4/";
const PDFIUM: &str = "https://github.com/bblanchon/pdfium-binaries/releases/download/chromium%2F7881/";

macro_rules! asset {
    ($base:expr, $file:literal, $sha:literal, $size:literal, $unpack:expr, $to:literal) => {
        Asset { file: $file, url: format!("{}{}", $base, $file), sha256: $sha, size: $size, unpack: $unpack, to: $to }
    };
}

/// The component ids, in display order.
pub const IDS: [&str; 5] = ["ai_runtime", "ai_model", "clip_model", "onnxruntime", "pdfium"];

/// Which platform's assets this server uses (`None`: not offered here).
pub fn platform() -> Option<&'static str> {
    match (std::env::consts::OS, std::env::consts::ARCH) {
        ("windows", "x86_64") => Some("windows-x64"),
        ("linux", "x86_64") => Some("linux-x64"),
        _ => None,
    }
}

/// A component's variants on this platform (empty: it has none).
pub fn variants(id: &str) -> &'static [&'static str] {
    match (id, platform()) {
        ("ai_runtime", Some("windows-x64")) => &["cpu", "cuda"],
        ("ai_runtime", Some(_)) => &["cpu"],
        ("onnxruntime", Some("windows-x64")) => &["cpu", "directml"],
        ("onnxruntime", Some(_)) => &["cpu", "cuda"],
        _ => &[],
    }
}

/// The variant to offer for a component given the GPUs found: CUDA for an
/// NVIDIA card, DirectML on Windows (any DirectX 12 GPU), else the CPU.
pub fn default_variant(id: &str, gpus: &[&str]) -> Option<&'static str> {
    let vs = variants(id);
    let pick = if gpus.contains(&"nvidia") && vs.contains(&"cuda") {
        "cuda"
    } else if vs.contains(&"directml") {
        "directml"
    } else {
        "cpu"
    };
    vs.iter().copied().find(|v| *v == pick).or(vs.first().copied())
}

/// The assets of a component on this platform (in `variant`, for those with variants).
pub fn assets(id: &str, variant: &str) -> Vec<Asset> {
    let windows = platform() == Some("windows-x64");
    let linux = platform() == Some("linux-x64");
    match id {
        "ai_runtime" if windows && variant == "cuda" => vec![
            asset!(LLAMA, "llama-b9789-bin-win-cuda-12.4-x64.zip", "513021d210e01d65c4ff824a7a76503c3a99e2ed6936c8f50936b646f238ad85", 261_606_759, Unpack::Zip, "ai/bin"),
            asset!(LLAMA, "cudart-llama-bin-win-cuda-12.4-x64.zip", "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6", 391_443_627, Unpack::Zip, "ai/bin"),
        ],
        "ai_runtime" if windows => vec![asset!(
            LLAMA, "llama-b9789-bin-win-cpu-x64.zip", "b5e7b4ba66ae0a885cb670f88bfca35f73e45aeca565f1da4ce437b3a3cbae96", 17_292_034, Unpack::Zip, "ai/bin"
        )],
        "ai_runtime" if linux => vec![asset!(
            LLAMA, "llama-b9789-bin-ubuntu-x64.tar.gz", "549fad25d7dc09cdea17e8c410251bae93ac5451dafce100624c665daceee978", 15_634_777, Unpack::Tar { strip: 1 }, "ai/bin"
        )],
        "ai_model" if windows || linux => vec![
            asset!(QWEN, "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf", "d02fe9b69ad8cadbbd228e387667af66612c44bed29ffc8eb1e7caf9ac486c12", 1_929_901_056, Unpack::File, "ai/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"),
            asset!(QWEN, "mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf", "b9160fe9d814d1fadf68395677468534778b39ac33c2e7561b7b218626e60d5e", 1_338_428_128, Unpack::File, "ai/models/mmproj-Qwen2.5-VL-3B-Instruct-f16.gguf"),
        ],
        "clip_model" if windows || linux => vec![Asset {
            file: "vision_model.onnx",
            url: "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/d15189d7028b43f1d3e65039190477f6af591c2a/onnx/vision_model.onnx".into(),
            sha256: "fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40",
            size: 351_685_709,
            unpack: Unpack::File,
            to: "models/clip_image_encoder.onnx",
        }],
        // DirectML: ONNX Runtime's DirectML build and the DirectML it was built
        // against (newer than the one in System32; `tools` loads it first).
        "onnxruntime" if windows && variant == "directml" => vec![
            asset!(
                ORT_DML, "microsoft.ml.onnxruntime.directml.1.22.0.nupkg", "29f9872d786236b79aa83f94482f3a17c14297e4833768d6d0ed4883ee732e60", 17_898_472,
                Unpack::ZipPick(&[("runtimes/win-x64/native/onnxruntime.dll", "onnxruntime.dll")]), "onnxruntime.dll"
            ),
            asset!(
                DIRECTML, "microsoft.ai.directml.1.15.4.nupkg", "4e7cb7ddce8cf837a7a75dc029209b520ca0101470fcdf275c1f49736a3615b9", 202_292_617,
                Unpack::ZipPick(&[("bin/x64-win/DirectML.dll", "DirectML.dll")]), "DirectML.dll"
            ),
        ],
        "onnxruntime" if windows => vec![asset!(
            ORT, "onnxruntime-win-x64-1.22.0.zip", "174c616efc0271194488642a72f1a514e01487da4dfe84c49296d66e40ebe0da", 72_368_545,
            Unpack::ZipPick(&[("onnxruntime-win-x64-1.22.0/lib/onnxruntime.dll", "onnxruntime.dll")]), "onnxruntime.dll"
        )],
        // CUDA 12 and cuDNN 9 come from the system (or the container image).
        "onnxruntime" if linux && variant == "cuda" => vec![asset!(
            ORT, "onnxruntime-linux-x64-gpu-1.22.0.tgz", "2a19dbfa403672ec27378c3d40a68f793ac7a6327712cd0e8240a86be2b10c55", 227_317_516,
            Unpack::TarPick(&[
                ("onnxruntime-linux-x64-gpu-1.22.0/lib/libonnxruntime.so.1.22.0", "libonnxruntime.so"),
                ("onnxruntime-linux-x64-gpu-1.22.0/lib/libonnxruntime_providers_shared.so", "libonnxruntime_providers_shared.so"),
                ("onnxruntime-linux-x64-gpu-1.22.0/lib/libonnxruntime_providers_cuda.so", "libonnxruntime_providers_cuda.so"),
            ]),
            "libonnxruntime.so"
        )],
        "onnxruntime" if linux => vec![asset!(
            ORT, "onnxruntime-linux-x64-1.22.0.tgz", "8344d55f93d5bc5021ce342db50f62079daf39aaafb5d311a451846228be49b3", 7_798_730,
            Unpack::TarPick(&[("onnxruntime-linux-x64-1.22.0/lib/libonnxruntime.so.1.22.0", "libonnxruntime.so")]), "libonnxruntime.so"
        )],
        "pdfium" if windows => vec![asset!(
            PDFIUM, "pdfium-win-x64.tgz", "73cc0de638ac2095e7445bf56a38200a5b7c7ca0e9f4ba144598f2457377ac08", 3_733_154,
            Unpack::TarPick(&[("bin/pdfium.dll", "pdfium.dll")]), "pdfium.dll"
        )],
        "pdfium" if linux => vec![asset!(
            PDFIUM, "pdfium-linux-x64.tgz", "1470e21b8b4a3b4ad7f85684e2da11d94f3b69a86d81dee11b9b6709d927ac1d", 3_644_759,
            Unpack::TarPick(&[("lib/libpdfium.so", "libpdfium.so")]), "libpdfium.so"
        )],
        _ => Vec::new(),
    }
}

struct Info {
    label: &'static str,
    enables: &'static str,
    license: &'static str,
    license_url: &'static str,
}

fn info(id: &str) -> Info {
    match id {
        "ai_runtime" => Info {
            label: "AI tagger runtime (llama.cpp b9789)",
            enables: "AI tagger",
            license: "MIT",
            license_url: "https://github.com/ggml-org/llama.cpp/blob/master/LICENSE",
        },
        "ai_model" => Info {
            label: "AI tagger model (Qwen2.5-VL-3B-Instruct, GGUF)",
            enables: "AI tagger",
            license: "Qwen Research License",
            license_url: "https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/blob/main/LICENSE",
        },
        "clip_model" => Info {
            label: "CLIP image encoder (ViT-B/32, ONNX)",
            enables: "AI image similarity (Find by Image, Duplicates)",
            license: "MIT",
            license_url: "https://github.com/openai/CLIP/blob/main/LICENSE",
        },
        "onnxruntime" => Info {
            label: "ONNX Runtime 1.22.0",
            enables: "AI image similarity (Find by Image, Duplicates)",
            license: "MIT",
            license_url: "https://github.com/microsoft/onnxruntime/blob/main/LICENSE",
        },
        _ => Info {
            label: "pdfium (chromium 7881)",
            enables: "PDF previews",
            license: "Apache-2.0 / BSD-3-Clause",
            license_url: "https://github.com/bblanchon/pdfium-binaries/blob/master/LICENSE",
        },
    }
}

/// One component as the Maintenance page shows it.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct ComponentStatus {
    pub id: String,
    pub label: String,
    pub enables: String,
    pub license: String,
    pub license_url: String,
    /// `installed`, `missing`, `external` (configured elsewhere, not managed
    /// here) or `unavailable` (no download for this platform).
    pub state: String,
    /// Where it is, or why it is in that state.
    pub detail: String,
    /// Download sizes per variant (`default` for components without variants).
    pub sizes: Vec<VariantSize>,
    /// Its variants here (`cpu`, `cuda`, `directml`); empty when it has none.
    pub variants: Vec<String>,
    /// The variant to offer, from the GPUs found.
    pub default_variant: Option<String>,
    /// The installed variant.
    pub variant: Option<String>,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct VariantSize {
    pub variant: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct ComponentsInfo {
    pub components: Vec<ComponentStatus>,
    /// `windows-x64`, `linux-x64`, or null when nothing can be downloaded here.
    pub platform: Option<String>,
    /// GPU vendors found: `nvidia`, `amd`, `intel` (on Windows only `nvidia`
    /// is detected).
    pub gpus: Vec<String>,
    /// The execution provider of the loaded CLIP encoder (`CPU`, `CUDA`,
    /// `DirectML`, …); null until it is first used.
    pub clip_provider: Option<String>,
    /// Free space where the data directory is.
    pub free_bytes: Option<u64>,
}

fn lib_name(base: &str) -> String {
    format!("{}{base}{}", std::env::consts::DLL_PREFIX, std::env::consts::DLL_SUFFIX)
}

/// The GPU vendors in this machine. Linux: the PCI display devices in sysfs
/// (also visible in containers). Windows: an NVIDIA driver (`nvcuda.dll`).
pub fn gpus() -> Vec<&'static str> {
    let mut found = Vec::new();
    if cfg!(windows) {
        let root = std::env::var_os("SystemRoot").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
        if root.join("System32").join("nvcuda.dll").is_file() {
            found.push("nvidia");
        }
    } else {
        for dev in fs::read_dir("/sys/bus/pci/devices").into_iter().flatten().flatten() {
            let read = |f: &str| fs::read_to_string(dev.path().join(f)).unwrap_or_default();
            if let Some(v) = gpu_vendor(&read("class"), &read("vendor"))
                && !found.contains(&v)
            {
                found.push(v);
            }
        }
        if !found.contains(&"nvidia") && Path::new("/proc/driver/nvidia/version").exists() {
            found.push("nvidia");
        }
    }
    found
}

/// The vendor of a PCI device from its sysfs `class` and `vendor`, when it is
/// a display controller (class `0x03xxxx`).
fn gpu_vendor(class: &str, vendor: &str) -> Option<&'static str> {
    if !class.trim().starts_with("0x03") {
        return None;
    }
    match vendor.trim() {
        "0x10de" => Some("nvidia"),
        "0x1002" => Some("amd"),
        "0x8086" => Some("intel"),
        _ => None,
    }
}

/// Free bytes on the volume holding `path`.
pub fn free_bytes(path: &Path) -> Option<u64> {
    imp::free_bytes(path)
}

#[cfg(windows)]
mod imp {
    use std::os::windows::ffi::OsStrExt;
    use std::path::Path;

    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;

    pub fn free_bytes(path: &Path) -> Option<u64> {
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain([0]).collect();
        let mut free = 0u64;
        // SAFETY: `wide` is NUL-terminated and outlives the call; the out pointer is valid.
        let ok = unsafe { GetDiskFreeSpaceExW(wide.as_ptr(), &mut free, std::ptr::null_mut(), std::ptr::null_mut()) };
        (ok != 0).then_some(free)
    }
}

#[cfg(unix)]
mod imp {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;
    use std::path::Path;

    pub fn free_bytes(path: &Path) -> Option<u64> {
        let c = CString::new(path.as_os_str().as_bytes()).ok()?;
        // SAFETY: `c` is a valid C string and `st` a properly sized out value.
        let mut st: libc::statvfs = unsafe { std::mem::zeroed() };
        let ok = unsafe { libc::statvfs(c.as_ptr(), &mut st) } == 0;
        ok.then(|| st.f_bavail as u64 * st.f_frsize as u64)
    }
}

#[cfg(not(any(windows, unix)))]
mod imp {
    pub fn free_bytes(_: &std::path::Path) -> Option<u64> {
        None
    }
}

/// Where a component was found outside the data directory's managed places.
fn external(lib: &Library, id: &str) -> Option<String> {
    let c = &lib.config;
    let env = |k: &str| std::env::var_os(k).map(|v| format!("{k}={}", v.to_string_lossy()));
    match id {
        "ai_runtime" => c.ai.url.clone().map(|u| format!("ai.url = {u}")).or_else(|| c.ai.llama_server.clone().map(|p| format!("ai.llama_server = {p}"))),
        "ai_model" => c.ai.url.clone().map(|u| format!("ai.url = {u}")).or_else(|| c.ai.model.clone().map(|p| format!("ai.model = {p}"))),
        "clip_model" => env("STLLIBRARY_CLIP_MODEL"),
        "onnxruntime" => c.onnxruntime.clone().map(|p| format!("onnxruntime = {p}")).or_else(|| env("ORT_DYLIB_PATH")).or_else(|| beside_server(&lib_name("onnxruntime"))),
        "pdfium" => c.pdfium.clone().map(|p| format!("pdfium = {p}")).or_else(|| env("STL_PDFIUM_PATH")).or_else(|| beside_server(&lib_name("pdfium"))),
        _ => None,
    }
}

fn beside_server(name: &str) -> Option<String> {
    let p = std::env::current_exe().ok()?.parent()?.join(name);
    p.is_file().then(|| format!("{} (beside the server)", p.display()))
}

/// A component's installed variant: the AI runtime's `variant.txt`; for ONNX
/// Runtime, the variant whose extra files (DirectML, the CUDA provider) are
/// present; `cpu` otherwise.
fn installed_variant(data: &Path, id: &str) -> String {
    if id == "ai_runtime" {
        return fs::read_to_string(data.join("ai").join("bin").join("variant.txt")).map(|v| v.trim().to_string()).unwrap_or_else(|_| "cpu".into());
    }
    let main = lib_name("onnxruntime");
    let has_extra = |v: &&str| assets(id, v).iter().flat_map(|a| a.outputs()).any(|f| f != main && data.join(f).is_file());
    variants(id).iter().filter(|v| **v != "cpu").find(|v| has_extra(v)).unwrap_or(&"cpu").to_string()
}

/// Delete the files every variant of a component puts in the data directory.
fn remove_outputs(data: &Path, id: &str) -> io::Result<()> {
    let names: Vec<&str> = if variants(id).is_empty() { vec![""] } else { variants(id).to_vec() };
    for f in names.iter().flat_map(|v| assets(id, v)).flat_map(|a| a.outputs()) {
        match fs::remove_file(data.join(f)) {
            Err(e) if e.kind() != io::ErrorKind::NotFound => return Err(e),
            _ => {}
        }
    }
    Ok(())
}

fn installed(lib: &Library, id: &str) -> Option<String> {
    let data = &lib.config.data_dir;
    let file = |rel: &str| {
        let p = data.join(rel);
        p.is_file().then(|| p.display().to_string())
    };
    match id {
        "ai_runtime" => crate::ai::find_exe(&data.join("ai").join("bin")).map(|p| p.display().to_string()),
        "ai_model" => {
            let files = assets(id, "");
            let all = !files.is_empty() && files.iter().all(|a| data.join(a.to).is_file());
            all.then(|| data.join("ai").join("models").display().to_string())
        }
        "clip_model" => file("models/clip_image_encoder.onnx"),
        "onnxruntime" => file(&lib_name("onnxruntime")),
        "pdfium" => file(&lib_name("pdfium")),
        _ => None,
    }
}

/// What the Maintenance page lists.
pub fn status(lib: &Library) -> ComponentsInfo {
    let data = &lib.config.data_dir;
    let gpus = gpus();
    let components = IDS
        .iter()
        .map(|&id| {
            let i = info(id);
            let sizes: Vec<VariantSize> = if variants(id).is_empty() {
                vec![VariantSize { variant: "default".into(), bytes: assets(id, "").iter().map(|a| a.size).sum() }]
            } else {
                variants(id).iter().map(|v| VariantSize { variant: v.to_string(), bytes: assets(id, v).iter().map(|a| a.size).sum() }).collect()
            };
            let (state, detail) = if let Some(where_) = external(lib, id) {
                ("external", where_)
            } else if let Some(where_) = installed(lib, id) {
                ("installed", where_)
            } else if assets(id, variants(id).first().copied().unwrap_or("")).is_empty() {
                ("unavailable", format!("no download for {} {}", std::env::consts::OS, std::env::consts::ARCH))
            } else {
                ("missing", String::new())
            };
            let variant = (!variants(id).is_empty() && state == "installed").then(|| installed_variant(data, id));
            ComponentStatus {
                id: id.into(),
                label: i.label.into(),
                enables: i.enables.into(),
                license: i.license.into(),
                license_url: i.license_url.into(),
                state: state.into(),
                detail,
                sizes,
                variants: variants(id).iter().map(|v| v.to_string()).collect(),
                default_variant: default_variant(id, &gpus).map(str::to_string),
                variant,
            }
        })
        .collect();
    ComponentsInfo {
        components,
        platform: platform().map(str::to_string),
        gpus: gpus.iter().map(|g| g.to_string()).collect(),
        clip_provider: lib.tools.clip_provider().map(str::to_string),
        free_bytes: free_bytes(data),
    }
}

// ── downloading ──────────────────────────────────────────────────────────────

fn agent() -> ureq::Agent {
    ureq::Agent::config_builder()
        .timeout_connect(Some(Duration::from_secs(30)))
        .timeout_recv_response(Some(Duration::from_secs(60)))
        .user_agent(concat!("stlLibrary/", env!("CARGO_PKG_VERSION")))
        .build()
        .into()
}

fn sha256_of(path: &Path) -> io::Result<String> {
    let mut f = File::open(path)?;
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

/// Download `url` to `dest`, resuming a `dest.part` left by an earlier try, and
/// check its size and hash. Progress goes to `ctl` (bytes); a stop request
/// keeps the `.part` for the next try.
pub fn download(url: &str, dest: &Path, size: u64, sha256: &str, ctl: &Control) -> Result<(), String> {
    if dest.is_file() && fs::metadata(dest).map(|m| m.len()).ok() == Some(size) && sha256_of(dest).ok().as_deref() == Some(sha256) {
        ctl.advance(size);
        return Ok(());
    }
    if let Some(dir) = dest.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let part = PathBuf::from(format!("{}.part", dest.display()));
    let mut have = fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
    if have > size {
        let _ = fs::remove_file(&part);
        have = 0;
    }
    if have < size {
        let mut req = agent().get(url);
        if have > 0 {
            req = req.header("Range", &format!("bytes={have}-"));
        }
        let mut resp = req.call().map_err(|e| format!("download {url}: {e}"))?;
        let resumed = resp.status().as_u16() == 206;
        if !resumed {
            have = 0; // the server sent the whole file
        }
        let mut out = fs::OpenOptions::new()
            .create(true)
            .write(true)
            .append(resumed)
            .truncate(!resumed)
            .open(&part)
            .map_err(|e| format!("{}: {e}", part.display()))?;
        ctl.advance(have);
        let mut reader = resp.body_mut().as_reader();
        let mut buf = vec![0u8; 1 << 20];
        loop {
            if ctl.is_stopped() {
                return Err("stopped".into());
            }
            let n = reader.read(&mut buf).map_err(|e| format!("download {url}: {e}"))?;
            if n == 0 {
                break;
            }
            out.write_all(&buf[..n]).map_err(|e| format!("{}: {e}", part.display()))?;
            have += n as u64;
            ctl.advance(n as u64);
        }
        out.flush().map_err(|e| e.to_string())?;
    } else {
        ctl.advance(size);
    }
    if have != size {
        return Err(format!("{url}: got {have} bytes, expected {size}; try again to resume"));
    }
    let got = sha256_of(&part).map_err(|e| e.to_string())?;
    if got != sha256 {
        let _ = fs::remove_file(&part);
        return Err(format!("{url}: the download does not match its checksum (got {got}); it was deleted"));
    }
    fs::rename(&part, dest).map_err(|e| format!("{}: {e}", dest.display()))
}

// ── unpacking ────────────────────────────────────────────────────────────────

/// `name` without its first `strip` folders, refused when it would leave the
/// destination (absolute, `..`, a drive).
fn safe_relative(name: &str, strip: usize) -> Option<PathBuf> {
    let parts: Vec<&str> = name.split(['/', '\\']).filter(|p| !p.is_empty() && *p != ".").collect();
    if parts.iter().any(|p| *p == ".." || p.contains(':')) || name.starts_with('/') || name.starts_with('\\') {
        return None;
    }
    let kept: PathBuf = parts.iter().skip(strip).collect();
    let ok = kept.components().all(|c| matches!(c, PathPart::Normal(_)));
    (ok && !kept.as_os_str().is_empty()).then_some(kept)
}

/// Where a picked member is unpacked: `into/<its index in picks>`.
fn picked(into: &Path, picks: &[(&str, &str)], name: &str) -> Option<PathBuf> {
    picks.iter().position(|(m, _)| *m == name).map(|i| into.join(i.to_string()))
}

/// Fails unless every picked member was unpacked.
fn check_picks(archive: &Path, into: &Path, picks: &[(&str, &str)]) -> Result<(), String> {
    match picks.iter().enumerate().find(|(i, _)| !into.join(i.to_string()).is_file()) {
        Some((_, (member, _))) => Err(format!("{} does not contain {member}", archive.display())),
        None => Ok(()),
    }
}

/// Unpack a zip into `into`, or with `picks` only those members (see [`picked`]).
fn unpack_zip(archive: &Path, into: &Path, picks: Option<&[(&str, &str)]>) -> Result<(), String> {
    let mut zip = zip::ZipArchive::new(File::open(archive).map_err(|e| e.to_string())?).map_err(|e| format!("{}: {e}", archive.display()))?;
    for i in 0..zip.len() {
        let mut entry = zip.by_index(i).map_err(|e| e.to_string())?;
        let name = entry.name().map(|n| n.to_string()).map_err(|e| e.to_string())?;
        let target = match picks {
            Some(p) => match picked(into, p, &name) {
                Some(t) => t,
                None => continue,
            },
            None => match safe_relative(&name, 0) {
                Some(rel) => into.join(rel),
                None => return Err(format!("{}: unsafe member {name:?}", archive.display())),
            },
        };
        if entry.is_dir() {
            fs::create_dir_all(&target).map_err(|e| e.to_string())?;
            continue;
        }
        if let Some(dir) = target.parent() {
            fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        let mut out = File::create(&target).map_err(|e| format!("{}: {e}", target.display()))?;
        io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
    }
    picks.map_or(Ok(()), |p| check_picks(archive, into, p))
}

/// Unpack a `.tar.gz` as [`unpack_zip`], without the top `strip` folders.
fn unpack_tar(archive: &Path, into: &Path, strip: usize, picks: Option<&[(&str, &str)]>) -> Result<(), String> {
    let gz = flate2::read::GzDecoder::new(File::open(archive).map_err(|e| e.to_string())?);
    let mut tar = tar::Archive::new(gz);
    tar.set_preserve_permissions(true);
    for entry in tar.entries().map_err(|e| e.to_string())? {
        let mut entry = entry.map_err(|e| e.to_string())?;
        let name = entry.path().map_err(|e| e.to_string())?.to_string_lossy().replace('\\', "/");
        let target = match picks {
            Some(p) => match picked(into, p, name.trim_start_matches("./")) {
                Some(t) => t,
                None => continue,
            },
            None => match safe_relative(&name, strip) {
                Some(rel) => into.join(rel),
                None if name.split('/').filter(|p| !p.is_empty() && *p != ".").count() <= strip => continue, // the stripped folders
                None => return Err(format!("{}: unsafe member {name:?}", archive.display())),
            },
        };
        if let Some(dir) = target.parent() {
            fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        // Symlinks stay links (the llama.cpp libraries' version names);
        // `unpack` refuses links pointing outside the destination.
        entry.unpack(&target).map_err(|e| format!("{}: {e}", target.display()))?;
    }
    picks.map_or(Ok(()), |p| check_picks(archive, into, p))
}

/// Move everything in `from` into `to`, replacing files of the same name.
fn merge_into(from: &Path, to: &Path) -> Result<(), String> {
    fs::create_dir_all(to).map_err(|e| e.to_string())?;
    for e in fs::read_dir(from).map_err(|e| e.to_string())?.flatten() {
        let dest = to.join(e.file_name());
        let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
        if is_dir {
            merge_into(&e.path(), &dest)?;
        } else {
            if dest.is_file() || dest.is_symlink() {
                fs::remove_file(&dest).map_err(|x| format!("{}: {x} (is it in use?)", dest.display()))?;
            }
            fs::rename(e.path(), &dest).map_err(|x| format!("{}: {x}", dest.display()))?;
        }
    }
    Ok(())
}

/// Download and put one asset in place under `data`. `mirror`: a base URL
/// that replaces the asset's own (the file name is appended).
pub fn install_asset(data: &Path, asset: &Asset, mirror: Option<&str>, ctl: &Control) -> Result<(), String> {
    let url = match mirror {
        Some(m) => format!("{}/{}", m.trim_end_matches('/'), asset.file),
        None => asset.url.clone(),
    };
    let target = data.join(asset.to);
    if asset.unpack == Unpack::File {
        return download(&url, &target, asset.size, asset.sha256, ctl);
    }
    let archive = data.join("downloads").join(asset.file);
    download(&url, &archive, asset.size, asset.sha256, ctl)?;
    let staging = data.join("downloads").join(format!(".unpack-{}", asset.file));
    let _ = fs::remove_dir_all(&staging);
    fs::create_dir_all(&staging).map_err(|e| e.to_string())?;
    let result = (|| match asset.unpack {
        Unpack::Zip => {
            unpack_zip(&archive, &staging, None)?;
            merge_into(&staging, &target)
        }
        Unpack::Tar { strip } => {
            unpack_tar(&archive, &staging, strip, None)?;
            merge_into(&staging, &target)
        }
        Unpack::ZipPick(picks) | Unpack::TarPick(picks) => {
            if matches!(asset.unpack, Unpack::ZipPick(_)) {
                unpack_zip(&archive, &staging, Some(picks))?;
            } else {
                unpack_tar(&archive, &staging, 0, Some(picks))?;
            }
            for (i, (_, to)) in picks.iter().enumerate() {
                let target = data.join(to);
                if let Some(dir) = target.parent() {
                    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
                }
                if target.exists() {
                    fs::remove_file(&target).map_err(|e| format!("{}: {e} (is it in use? restart the server first)", target.display()))?;
                }
                fs::rename(staging.join(i.to_string()), &target).map_err(|e| format!("{}: {e}", target.display()))?;
            }
            Ok(())
        }
        Unpack::File => unreachable!("handled above"),
    })();
    let _ = fs::remove_dir_all(&staging);
    if result.is_ok() {
        let _ = fs::remove_file(&archive);
    }
    result
}

/// Install components, each in its variant from `chosen` (id → variant) or
/// its default. Already installed ones are skipped, unless another variant is
/// asked for.
pub fn install(lib: &Library, ctl: &Control, log: &dyn Fn(String), ids: &[String], chosen: &BTreeMap<String, String>) -> Result<usize, String> {
    let data = &lib.config.data_dir;
    let gpus = gpus();
    let mut plan: Vec<(String, String, Vec<Asset>)> = Vec::new();
    for id in ids {
        if let Some(where_) = external(lib, id) {
            log(format!("{}: configured elsewhere ({where_}); skipped", info(id).label));
            continue;
        }
        let variant = chosen.get(id).map(String::as_str).or(default_variant(id, &gpus)).unwrap_or("").to_string();
        let present = installed(lib, id).is_some();
        if present && (variants(id).is_empty() || installed_variant(data, id) == variant) {
            log(format!("{}: already installed", info(id).label));
            continue;
        }
        let assets = assets(id, &variant);
        if assets.is_empty() {
            return Err(format!("{} has no download for this platform", info(id).label));
        }
        plan.push((id.clone(), variant, assets));
    }
    let total: u64 = plan.iter().flat_map(|(_, _, a)| a.iter().map(|a| a.size)).sum();
    if total == 0 {
        return Ok(0);
    }
    // Archives are unpacked beside their download: count them twice.
    let needed: u64 = plan.iter().flat_map(|(_, _, a)| a.iter().map(|a| if a.unpack == Unpack::File { a.size } else { a.size * 3 })).sum();
    if let Some(free) = free_bytes(data)
        && free < needed
    {
        return Err(format!("not enough disk space: {} MB needed, {} MB free", needed / 1_000_000, free / 1_000_000));
    }
    ctl.set_total(total);
    let mirror = lib.config.components_mirror.clone();
    for (id, variant, assets) in &plan {
        let label = match variant.as_str() {
            "" => info(id).label.to_string(),
            v => format!("{} ({v})", info(id).label),
        };
        log(format!("{label}: downloading {} MB…", assets.iter().map(|a| a.size).sum::<u64>() / 1_000_000));
        if id == "ai_runtime" {
            lib.ai.stop(); // a running llama-server keeps its files locked
            if installed(lib, id).is_some() {
                let _ = fs::remove_dir_all(data.join("ai").join("bin"));
            }
        } else if installed(lib, id).is_some() {
            // Another variant: none of its files may stay (they decide the variant).
            remove_outputs(data, id).map_err(|e| format!("{label}: {e} (it is in use; restart the server and try again)"))?;
        }
        for a in assets {
            install_asset(data, a, mirror.as_deref(), ctl)?;
        }
        if id == "ai_runtime" {
            fs::write(data.join("ai").join("bin").join("variant.txt"), variant).map_err(|e| e.to_string())?;
        }
        log(format!("{label}: installed"));
        if id == "onnxruntime" && lib.tools.ort_loaded() {
            log("ONNX Runtime is already loaded: restart the server to use the new one".into());
        }
    }
    lib.tools.retry(); // load ONNX Runtime / pdfium on next use
    Ok(plan.len())
}

/// Remove installed components (their files under the data directory only).
pub fn remove(lib: &Library, log: &dyn Fn(String), ids: &[String]) -> Result<usize, String> {
    let data = &lib.config.data_dir;
    let mut removed = 0;
    for id in ids {
        if installed(lib, id).is_none() {
            continue;
        }
        let result = match id.as_str() {
            "ai_runtime" => {
                lib.ai.stop();
                fs::remove_dir_all(data.join("ai").join("bin"))
            }
            _ => remove_outputs(data, id),
        };
        match result {
            Ok(()) => {
                removed += 1;
                log(format!("{}: removed", info(id).label));
            }
            Err(e) => log(format!("{}: {e} (it may be in use; restart the server and try again)", info(id).label)),
        }
    }
    Ok(removed)
}

#[cfg(test)]
mod tests {
    use std::io::{BufRead, BufReader};
    use std::net::TcpListener;

    use super::*;

    fn sha(bytes: &[u8]) -> String {
        Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
    }

    /// A tiny HTTP server for `files` (name → bytes) that honours `Range`.
    fn serve(files: Vec<(&'static str, Vec<u8>)>) -> String {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let files = files.clone();
                std::thread::spawn(move || {
                    let mut reader = BufReader::new(stream.try_clone().unwrap());
                    let mut first = String::new();
                    reader.read_line(&mut first).unwrap();
                    let mut range = 0usize;
                    loop {
                        let mut l = String::new();
                        reader.read_line(&mut l).unwrap();
                        if l.trim().is_empty() {
                            break;
                        }
                        if let Some(r) = l.to_ascii_lowercase().strip_prefix("range: bytes=") {
                            range = r.trim().trim_end_matches('-').parse().unwrap_or(0);
                        }
                    }
                    let path = first.split_whitespace().nth(1).unwrap_or("/").trim_start_matches('/').to_string();
                    let mut out = stream;
                    match files.iter().find(|(n, _)| *n == path) {
                        Some((_, body)) => {
                            let part = &body[range.min(body.len())..];
                            let status = if range > 0 { "206 Partial Content" } else { "200 OK" };
                            write!(out, "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", part.len()).unwrap();
                            out.write_all(part).unwrap();
                        }
                        None => write!(out, "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap(),
                    }
                });
            }
        });
        format!("http://{addr}")
    }

    fn zip_of(members: &[(&str, &[u8])]) -> Vec<u8> {
        let mut z = zip::ZipWriter::new(io::Cursor::new(Vec::new()));
        for (n, d) in members {
            z.start_file(*n, zip::write::SimpleFileOptions::default()).unwrap();
            z.write_all(d).unwrap();
        }
        z.finish().unwrap().into_inner()
    }

    fn tgz_of(members: &[(&str, &[u8])]) -> Vec<u8> {
        let gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        let mut t = tar::Builder::new(gz);
        for (n, d) in members {
            let mut h = tar::Header::new_gnu();
            h.set_size(d.len() as u64);
            h.set_mode(0o755);
            h.set_cksum();
            t.append_data(&mut h, n, *d).unwrap();
        }
        t.into_inner().unwrap().finish().unwrap()
    }

    fn asset(file: &'static str, body: &[u8], unpack: Unpack, to: &'static str) -> Asset {
        let sha: &'static str = Box::leak(sha(body).into_boxed_str());
        Asset { file, url: String::new(), sha256: sha, size: body.len() as u64, unpack, to }
    }

    #[test]
    fn manifest_is_complete_here() {
        if platform().is_none() {
            return;
        }
        for id in IDS {
            let vs = if variants(id).is_empty() { vec![""] } else { variants(id).to_vec() };
            for v in vs {
                let a = assets(id, v);
                assert!(!a.is_empty(), "{id} {v}");
                assert!(a.iter().all(|a| a.sha256.len() == 64 && a.size > 0 && a.url.ends_with(a.file)), "{id} {v}");
                assert!(a.iter().all(|a| a.outputs().first().is_none_or(|o| *o == a.to)), "{id} {v}: `to` is the first output");
            }
        }
    }

    #[test]
    fn gpus_and_default_variants() {
        assert_eq!(gpu_vendor("0x030000
", "0x10de
"), Some("nvidia"));
        assert_eq!(gpu_vendor("0x030200", "0x1002"), Some("amd"));
        assert_eq!(gpu_vendor("0x038000", "0x8086"), Some("intel"));
        assert_eq!(gpu_vendor("0x060400", "0x8086"), None, "a PCI bridge is no GPU");
        assert_eq!(gpu_vendor("0x030000", "0x1234"), None);
        match platform() {
            Some("windows-x64") => {
                assert_eq!(default_variant("onnxruntime", &[]), Some("directml"));
                assert_eq!(default_variant("ai_runtime", &["nvidia"]), Some("cuda"));
                assert_eq!(default_variant("ai_runtime", &["amd"]), Some("cpu"));
            }
            Some(_) => {
                assert_eq!(default_variant("onnxruntime", &["intel", "nvidia"]), Some("cuda"));
                assert_eq!(default_variant("onnxruntime", &["amd"]), Some("cpu"));
                assert_eq!(default_variant("ai_runtime", &["nvidia"]), Some("cpu"));
            }
            None => assert_eq!(default_variant("onnxruntime", &["nvidia"]), None),
        }
        assert_eq!(default_variant("pdfium", &["nvidia"]), None);
    }

    #[test]
    fn onnx_variant_follows_its_files() {
        if platform().is_none() {
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        let data = dir.path();
        fs::write(data.join(lib_name("onnxruntime")), b"x").unwrap();
        assert_eq!(installed_variant(data, "onnxruntime"), "cpu");
        let gpu = variants("onnxruntime")[1];
        for a in assets("onnxruntime", gpu) {
            for f in a.outputs() {
                fs::write(data.join(f), b"x").unwrap();
            }
        }
        assert_eq!(installed_variant(data, "onnxruntime"), gpu);
        remove_outputs(data, "onnxruntime").unwrap();
        assert_eq!(fs::read_dir(data).unwrap().count(), 0, "every variant's files are removed");
    }

    #[test]
    fn downloads_resume_verify_and_unpack() {
        let model = b"model bytes".repeat(1000);
        let exe = b"MZ fake llama".to_vec();
        let zip = zip_of(&[("llama-server.exe", &exe), ("ggml.dll", b"dll")]);
        let tgz = tgz_of(&[("pkg/lib/libthing.so.1", b"so"), ("pkg/README", b"r")]);
        let base = serve(vec![
            ("model.gguf", model.clone()),
            ("llama.zip", zip.clone()),
            ("pkg.tgz", tgz.clone()),
            ("bad.bin", b"tampered".to_vec()),
        ]);
        let dir = tempfile::tempdir().unwrap();
        let data = dir.path();
        let ctl = Control::new();

        // A file, resumed from a partial download.
        let a = asset("model.gguf", &model, Unpack::File, "ai/models/model.gguf");
        fs::create_dir_all(data.join("ai/models")).unwrap();
        fs::write(data.join("ai/models/model.gguf.part"), &model[..500]).unwrap();
        install_asset(data, &a, Some(&base), &ctl).unwrap();
        assert_eq!(fs::read(data.join("ai/models/model.gguf")).unwrap(), model);
        assert!(!data.join("ai/models/model.gguf.part").exists());

        // A zip into a folder; a tar member picked and renamed.
        install_asset(data, &asset("llama.zip", &zip, Unpack::Zip, "ai/bin"), Some(&base), &ctl).unwrap();
        assert_eq!(fs::read(data.join("ai/bin/llama-server.exe")).unwrap(), exe);
        let picks: &'static [(&str, &str)] = &[("pkg/lib/libthing.so.1", "libthing.so"), ("pkg/README", "docs/README")];
        install_asset(data, &asset("pkg.tgz", &tgz, Unpack::TarPick(picks), "libthing.so"), Some(&base), &ctl).unwrap();
        assert_eq!(fs::read(data.join("libthing.so")).unwrap(), b"so");
        assert_eq!(fs::read(data.join("docs/README")).unwrap(), b"r");
        let missing: &'static [(&str, &str)] = &[("pkg/README", "r2"), ("pkg/nothing", "n")];
        let err = install_asset(data, &asset("pkg.tgz", &tgz, Unpack::TarPick(missing), "r2"), Some(&base), &ctl).unwrap_err();
        assert!(err.contains("does not contain pkg/nothing") && !data.join("r2").exists(), "{err}");
        install_asset(data, &asset("pkg.tgz", &tgz, Unpack::Tar { strip: 1 }, "out"), Some(&base), &ctl).unwrap();
        assert_eq!(fs::read(data.join("out/lib/libthing.so.1")).unwrap(), b"so");
        assert!(!data.join("downloads/pkg.tgz").exists(), "archives are deleted after unpacking");

        // A checksum mismatch is refused and leaves nothing behind.
        let mut bad = asset("bad.bin", b"original", Unpack::File, "bad.bin");
        bad.size = 8;
        let err = install_asset(data, &bad, Some(&base), &ctl).unwrap_err();
        assert!(err.contains("checksum"), "{err}");
        assert!(!data.join("bad.bin").exists() && !data.join("bad.bin.part").exists());
    }

    #[test]
    fn unsafe_names_are_refused() {
        assert_eq!(safe_relative("a/b.dll", 0), Some(PathBuf::from("a").join("b.dll")));
        assert_eq!(safe_relative("top/a.so", 1), Some(PathBuf::from("a.so")));
        assert_eq!(safe_relative("../evil", 0), None);
        assert_eq!(safe_relative("/etc/passwd", 0), None);
        assert_eq!(safe_relative("C:/x", 0), None);
        let dir = tempfile::tempdir().unwrap();
        let zip = zip_of(&[("../escape.txt", b"x")]);
        let path = dir.path().join("e.zip");
        fs::write(&path, zip).unwrap();
        assert!(unpack_zip(&path, &dir.path().join("out"), None).is_err());
        assert!(!dir.path().join("escape.txt").exists());
    }
}
