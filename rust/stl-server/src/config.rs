//! Server configuration: defaults, then `<data_dir>/server.json`, then the
//! environment (`STL_DATA_DIR`, `STL_BIND`, `STL_UNRAR_PATH`), then the
//! command line (`--data-dir`, `--bind`). Later sources win.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};

use serde::Deserialize;

/// The library database's file name inside the data directory (as the desktop app).
pub const DB_FILE: &str = "stlLibrary.db";
/// The thumbnail store's directory inside the data directory.
pub const THUMB_DIR: &str = "thumbnails.lmdb";
/// The optional settings file inside the data directory.
pub const SETTINGS_FILE: &str = "server.json";

pub const DEFAULT_BIND: &str = "127.0.0.1:8770";

#[derive(Debug, Clone)]
pub struct Config {
    pub data_dir: PathBuf,
    pub bind: SocketAddr,
    /// The UnRAR tool for `.rar` archives (a name on `PATH` or a path); `None`: `unrar`.
    pub unrar: Option<String>,
    /// The ONNX Runtime library for CLIP embeddings (see `tools`).
    pub onnxruntime: Option<String>,
    /// The pdfium library for PDF previews (see `tools`).
    pub pdfium: Option<String>,
    /// Folders editors may browse and crawl (admins: anywhere). Crawled
    /// folders count as roots too.
    pub roots: Vec<String>,
    /// How clients see the server's paths, for "open folder" (see `api::files`).
    pub path_map: Vec<PathMap>,
    /// The built web client (`web/dist`); see [`Config::web_dir`].
    pub web_dir: Option<String>,
}

/// A server path prefix and how clients reach it (`"D:/STL"` → `"\\nas\stl"`).
#[derive(Debug, Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PathMap {
    pub server: String,
    pub client: String,
}

/// What `server.json` may set.
#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileSettings {
    bind: Option<String>,
    unrar: Option<String>,
    onnxruntime: Option<String>,
    pdfium: Option<String>,
    #[serde(default)]
    roots: Vec<String>,
    #[serde(default)]
    path_map: Vec<PathMap>,
    web_dir: Option<String>,
}

impl Config {
    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join(DB_FILE)
    }

    pub fn thumb_dir(&self) -> PathBuf {
        self.data_dir.join(THUMB_DIR)
    }

    /// The web client's folder: `web_dir` (`server.json` / `STL_WEB_DIR`), else
    /// `web` beside the server, else `web/dist` in the working directory (a
    /// development checkout); `None` when none holds an `index.html`.
    pub fn web_dir(&self) -> Option<PathBuf> {
        let exe = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join("web")));
        self.web_dir
            .clone()
            .map(PathBuf::from)
            .into_iter()
            .chain(exe)
            .chain([PathBuf::from("web/dist"), PathBuf::from("../web/dist")])
            .find(|d| d.join("index.html").is_file())
    }

    /// The UnRAR executable as a full path, or `""` when none is found (as
    /// `archive._unrar_tool`: a name is looked up on `PATH`, a path must exist).
    pub fn unrar_tool(&self) -> String {
        let tool = self.unrar.clone().unwrap_or_else(|| "unrar".into());
        which(&tool).unwrap_or_else(|| if Path::new(&tool).is_file() { tool } else { String::new() })
    }

    /// Resolve the configuration from `args` (without the program name) and the
    /// process environment.
    pub fn load(args: &[String]) -> Result<Config, String> {
        Self::resolve(args, |k| std::env::var(k).ok())
    }

    fn resolve(args: &[String], env: impl Fn(&str) -> Option<String>) -> Result<Config, String> {
        let mut data_dir: Option<PathBuf> = env("STL_DATA_DIR").map(PathBuf::from);
        let mut bind_arg: Option<String> = None;
        let mut it = args.iter();
        while let Some(a) = it.next() {
            let mut value = |name: &str| it.next().cloned().ok_or(format!("{name} needs a value"));
            match a.as_str() {
                "--data-dir" => data_dir = Some(PathBuf::from(value("--data-dir")?)),
                "--bind" => bind_arg = Some(value("--bind")?),
                other => return Err(format!("unknown argument {other:?} (use --data-dir DIR, --bind ADDR:PORT)")),
            }
        }
        let data_dir = data_dir.unwrap_or_else(default_data_dir);
        let file = read_settings(&data_dir)?;
        let bind = bind_arg.or_else(|| env("STL_BIND")).or(file.bind).unwrap_or_else(|| DEFAULT_BIND.into());
        let bind = bind.parse().map_err(|e| format!("bind address {bind:?}: {e}"))?;
        let unrar = env("STL_UNRAR_PATH").or(file.unrar).filter(|u| !u.is_empty());
        Ok(Config { data_dir, bind, unrar, onnxruntime: file.onnxruntime, pdfium: file.pdfium, roots: file.roots, path_map: file.path_map, web_dir: env("STL_WEB_DIR").or(file.web_dir) })
    }
}

fn read_settings(data_dir: &Path) -> Result<FileSettings, String> {
    let path = data_dir.join(SETTINGS_FILE);
    match std::fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(FileSettings::default()),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

/// `shutil.which`: `name` on `PATH` (with `.exe` / `.cmd` / `.bat` on Windows).
fn which(name: &str) -> Option<String> {
    let path = Path::new(name);
    if path.components().count() > 1 {
        return None;
    }
    let exts: &[&str] = if cfg!(windows) { &["", ".exe", ".cmd", ".bat"] } else { &[""] };
    std::env::split_paths(&std::env::var_os("PATH")?).find_map(|dir| {
        exts.iter().map(|e| dir.join(format!("{name}{e}"))).find(|p| p.is_file()).map(|p| p.to_string_lossy().into_owned())
    })
}

/// The desktop app's platform default (`app/core/settings.py`).
pub fn default_data_dir() -> PathBuf {
    let home = || std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from).unwrap_or_default();
    if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(home).join("stlLibrary")
    } else if cfg!(target_os = "macos") {
        home().join("Library/Application Support/stlLibrary")
    } else {
        std::env::var_os("XDG_DATA_HOME").map(PathBuf::from).unwrap_or_else(|| home().join(".local/share")).join("stlLibrary")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(a: &[&str]) -> Vec<String> {
        a.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn later_sources_win() {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path().to_str().unwrap().to_string();
        std::fs::write(dir.path().join(SETTINGS_FILE), r#"{"bind": "0.0.0.0:9000"}"#).unwrap();
        let env = |k: &str| (k == "STL_DATA_DIR").then(|| d.clone());
        let c = Config::resolve(&[], env).unwrap();
        assert_eq!(c.data_dir, dir.path());
        assert_eq!(c.bind.to_string(), "0.0.0.0:9000");
        let c = Config::resolve(&args(&["--bind", "127.0.0.1:1234"]), env).unwrap();
        assert_eq!(c.bind.to_string(), "127.0.0.1:1234");
        let env2 = |k: &str| match k {
            "STL_DATA_DIR" => Some(d.clone()),
            "STL_BIND" => Some("127.0.0.1:5555".into()),
            _ => None,
        };
        assert_eq!(Config::resolve(&[], env2).unwrap().bind.to_string(), "127.0.0.1:5555");
        std::fs::write(dir.path().join(SETTINGS_FILE), r#"{"unrar": "C:/Tools/UnRAR.exe"}"#).unwrap();
        assert_eq!(Config::resolve(&[], env).unwrap().unrar.as_deref(), Some("C:/Tools/UnRAR.exe"));
    }

    #[test]
    fn errors_are_reported() {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path().to_str().unwrap();
        assert!(Config::resolve(&args(&["--data-dir", d, "--bind", "nope"]), |_| None).is_err());
        assert!(Config::resolve(&args(&["--data-dir"]), |_| None).is_err());
        assert!(Config::resolve(&args(&["--data-dir", d, "--frobnicate"]), |_| None).is_err());
        std::fs::write(dir.path().join(SETTINGS_FILE), r#"{"bnid": "x"}"#).unwrap();
        assert!(Config::resolve(&args(&["--data-dir", d]), |_| None).is_err(), "unknown keys are refused");
    }
}
