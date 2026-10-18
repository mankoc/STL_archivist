//! stlLibrary desktop shell (Phase 7): a native window on an stl-server.
//!
//! The bundled launcher page (`ui/`) lists remembered servers and can start a
//! local library: the `stl-server` beside this program, on a free loopback
//! port. Connecting navigates the window to the server, which serves the web
//! client itself. The shell holds no library logic; the only command a
//! server's pages may call is `open_folder` (granted per server at connect).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::ipc::CapabilityBuilder;
use tauri::menu::{MenuBuilder, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

const MAIN: &str = "main";
const SERVERS_FILE: &str = "servers.json";
const LOCAL_START_TIMEOUT: Duration = Duration::from_secs(60);

/// A remembered server.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
struct Server {
    name: String,
    url: String,
}

/// What the shell remembers (`<app config dir>/servers.json`).
#[derive(Debug, Default, Serialize, Deserialize)]
struct Saved {
    #[serde(default)]
    servers: Vec<Server>,
    /// The data directory last opened as a local library.
    #[serde(default)]
    local_data_dir: Option<String>,
}

struct LocalServer {
    child: Child,
    url: String,
    data_dir: String,
}

impl Drop for LocalServer {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default)]
struct Shell {
    local: Mutex<Option<LocalServer>>,
    launcher: Mutex<Option<Url>>,
    granted: Mutex<HashSet<String>>,
}

fn saved_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app.path().app_config_dir().map_err(|e| e.to_string())?.join(SERVERS_FILE))
}

fn load_saved(app: &AppHandle) -> Saved {
    saved_path(app)
        .ok()
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default()
}

fn store_saved(app: &AppHandle, saved: &Saved) -> Result<(), String> {
    let path = saved_path(app)?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    std::fs::write(&path, serde_json::to_string_pretty(saved).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

/// `http(s)://host[:port]` with no path, query or credentials.
fn normalize_url(url: &str) -> Result<Url, String> {
    let u = Url::parse(url.trim()).map_err(|e| format!("{url:?} is not a URL: {e}"))?;
    if !matches!(u.scheme(), "http" | "https") || u.host_str().is_none() || !u.username().is_empty() || u.password().is_some() {
        return Err(format!("{url:?} is not an http(s) server address"));
    }
    Url::parse(&u.origin().ascii_serialization()).map_err(|e| e.to_string())
}

/// Whether an stl-server answers at `base`.
fn reachable(base: &Url, timeout: Duration) -> Result<(), String> {
    let agent: ureq::Agent = ureq::Agent::config_builder().timeout_global(Some(timeout)).build().into();
    let status = base.join("api/auth/status").map_err(|e| e.to_string())?;
    agent.get(status.as_str()).call().map(|_| ()).map_err(|e| format!("no stlLibrary server answers at {base}: {e}"))
}

#[tauri::command]
fn list_servers(app: AppHandle) -> Vec<Server> {
    load_saved(&app).servers
}

#[tauri::command]
fn save_servers(app: AppHandle, servers: Vec<Server>) -> Result<(), String> {
    let mut saved = load_saved(&app);
    saved.servers = servers
        .into_iter()
        .map(|s| Ok(Server { name: s.name.trim().to_string(), url: normalize_url(&s.url)?.to_string() }))
        .collect::<Result<_, String>>()?;
    store_saved(&app, &saved)
}

/// Let `origin`'s pages call `open_folder` (and nothing else).
fn grant(app: &AppHandle, shell: &Shell, origin: &Url) -> Result<(), String> {
    let key = origin.as_str().trim_end_matches('/').to_string();
    let mut granted = shell.granted.lock().unwrap_or_else(|e| e.into_inner());
    if granted.contains(&key) {
        return Ok(());
    }
    let id = format!("server-{}", granted.len() + 1);
    let cap = CapabilityBuilder::new(id).remote(format!("{key}/*")).window(MAIN).permission("allow-open-folder");
    app.add_capability(cap).map_err(|e| e.to_string())?;
    granted.insert(key);
    Ok(())
}

fn navigate(app: &AppHandle, url: Url) -> Result<(), String> {
    let window = app.get_webview_window(MAIN).ok_or("the window is closed")?;
    window.navigate(url).map_err(|e| e.to_string())?;
    let _ = window.show();
    let _ = window.set_focus();
    Ok(())
}

/// Open the web client of the server at `url`.
#[tauri::command]
async fn connect(app: AppHandle, url: String) -> Result<(), String> {
    let base = normalize_url(&url)?;
    let probe = base.clone();
    tauri::async_runtime::spawn_blocking(move || reachable(&probe, Duration::from_secs(5))).await.map_err(|e| e.to_string())??;
    grant(&app, &app.state::<Shell>(), &base)?;
    navigate(&app, base)
}

/// The `stl-server` program: beside this one (an install, or `target/<profile>`).
fn server_exe() -> Result<PathBuf, String> {
    let name = if cfg!(windows) { "stl-server.exe" } else { "stl-server" };
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let beside = exe.parent().map(|d| d.join(name)).filter(|p| p.is_file());
    beside.ok_or_else(|| format!("{name} was not found beside {}", exe.display()))
}

fn free_port() -> Result<u16, String> {
    let l = std::net::TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    Ok(l.local_addr().map_err(|e| e.to_string())?.port())
}

/// The last lines of the local server's log, for an error message.
fn log_tail(path: &Path) -> String {
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    lines[lines.len().saturating_sub(3)..].join("\n")
}

fn spawn_local(app: &AppHandle, data_dir: &str) -> Result<LocalServer, String> {
    let exe = server_exe()?;
    let port = free_port()?;
    let log_dir = app.path().app_log_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&log_dir).map_err(|e| e.to_string())?;
    let log_path = log_dir.join("stl-server.log");
    let log = std::fs::File::create(&log_path).map_err(|e| e.to_string())?;
    let mut cmd = Command::new(&exe);
    cmd.args(["--data-dir", data_dir, "--bind", &format!("127.0.0.1:{port}")])
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .stdout(log.try_clone().map_err(|e| e.to_string())?)
        .stderr(log);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = cmd.spawn().map_err(|e| format!("cannot start {}: {e}", exe.display()))?;
    // Even if this shell crashes, the server (and its library lock) goes with it.
    stl_proc::end_with_parent(&child);
    let url = format!("http://127.0.0.1:{port}");
    let base = Url::parse(&url).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + LOCAL_START_TIMEOUT;
    loop {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(format!("the local server stopped ({status}):\n{}", log_tail(&log_path)));
        }
        if reachable(&base, Duration::from_secs(2)).is_ok() {
            return Ok(LocalServer { child, url, data_dir: data_dir.to_string() });
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            return Err(format!("the local server did not answer within {}s:\n{}", LOCAL_START_TIMEOUT.as_secs(), log_tail(&log_path)));
        }
        std::thread::sleep(Duration::from_millis(300));
    }
}

/// Start (or reuse) a local server on `data_dir` and open it.
#[tauri::command]
async fn start_local(app: AppHandle, data_dir: String) -> Result<String, String> {
    let dir = data_dir.trim().to_string();
    if dir.is_empty() {
        return Err("choose a data directory".into());
    }
    std::fs::create_dir_all(&dir).map_err(|e| format!("{dir}: {e}"))?;
    let app2 = app.clone();
    let url = tauri::async_runtime::spawn_blocking(move || -> Result<String, String> {
        let shell = app2.state::<Shell>();
        let mut local = shell.local.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(l) = local.as_mut()
            && l.data_dir == dir
            && matches!(l.child.try_wait(), Ok(None))
        {
            return Ok(l.url.clone());
        }
        *local = None; // stop another library first
        let started = spawn_local(&app2, &dir)?;
        let url = started.url.clone();
        *local = Some(started);
        let mut saved = load_saved(&app2);
        saved.local_data_dir = Some(dir);
        store_saved(&app2, &saved)?;
        Ok(url)
    })
    .await
    .map_err(|e| e.to_string())??;
    let base = Url::parse(&url).map_err(|e| e.to_string())?;
    grant(&app, &app.state::<Shell>(), &base)?;
    navigate(&app, base)?;
    Ok(url)
}

#[derive(Serialize)]
struct LocalState {
    /// The data directory of the running local server.
    running: Option<String>,
    /// The one opened last.
    last: Option<String>,
}

#[tauri::command]
fn local_state(app: AppHandle) -> LocalState {
    let shell = app.state::<Shell>();
    let running = shell.local.lock().unwrap_or_else(|e| e.into_inner()).as_ref().map(|l| l.data_dir.clone());
    LocalState { running, last: load_saved(&app).local_data_dir }
}

/// The desktop app's default data directory.
#[tauri::command]
fn default_data_dir() -> String {
    let home = || std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from).unwrap_or_default();
    let dir = if cfg!(windows) {
        std::env::var_os("LOCALAPPDATA").map(PathBuf::from).unwrap_or_else(home).join("stlLibrary")
    } else if cfg!(target_os = "macos") {
        home().join("Library/Application Support/stlLibrary")
    } else {
        std::env::var_os("XDG_DATA_HOME").map(PathBuf::from).unwrap_or_else(|| home().join(".local/share")).join("stlLibrary")
    };
    dir.to_string_lossy().into_owned()
}

#[tauri::command]
async fn pick_folder(app: AppHandle) -> Option<String> {
    let picked = app.dialog().file().blocking_pick_folder()?;
    picked.into_path().ok().map(|p| p.to_string_lossy().into_owned())
}

/// Show a folder (or the folder of a file) in the system's file manager. The
/// path must be absolute and exist; nothing is opened or run.
#[tauri::command]
fn open_folder(path: String) -> Result<(), String> {
    let p = PathBuf::from(&path);
    if !p.is_absolute() || !p.exists() {
        return Err(format!("{path} is not reachable from this computer"));
    }
    if p.is_dir() {
        tauri_plugin_opener::open_path(&p, None::<&str>).map_err(|e| e.to_string())
    } else {
        tauri_plugin_opener::reveal_item_in_dir(&p).map_err(|e| e.to_string())
    }
}

/// Back to the launcher page.
fn show_launcher(app: &AppHandle) {
    let url = app.state::<Shell>().launcher.lock().unwrap_or_else(|e| e.into_inner()).clone();
    if let Some(url) = url {
        let _ = navigate(app, url);
    }
}

fn reload(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(MAIN) {
        let _ = w.eval("location.reload()");
    }
}

fn on_menu(app: &AppHandle, id: &str) {
    match id {
        "launcher" => show_launcher(app),
        "reload" => reload(app),
        "show" => {
            if let Some(w) = app.get_webview_window(MAIN) {
                let _ = w.show();
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }
        "quit" => app.exit(0),
        _ => {}
    }
}

fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle();
    let launcher = MenuItemBuilder::with_id("launcher", "Switch server…").accelerator("CmdOrCtrl+L").build(handle)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit").accelerator("CmdOrCtrl+Q").build(handle)?;
    let reload_item = MenuItemBuilder::with_id("reload", "Reload").accelerator("CmdOrCtrl+R").build(handle)?;
    let file = SubmenuBuilder::new(handle, "File").item(&launcher).separator().item(&quit).build()?;
    let view = SubmenuBuilder::new(handle, "View")
        .item(&reload_item)
        .item(&PredefinedMenuItem::fullscreen(handle, None)?)
        .build()?;
    let edit = SubmenuBuilder::new(handle, "Edit")
        .item(&PredefinedMenuItem::undo(handle, None)?)
        .item(&PredefinedMenuItem::redo(handle, None)?)
        .separator()
        .item(&PredefinedMenuItem::cut(handle, None)?)
        .item(&PredefinedMenuItem::copy(handle, None)?)
        .item(&PredefinedMenuItem::paste(handle, None)?)
        .item(&PredefinedMenuItem::select_all(handle, None)?)
        .build()?;
    let menu = MenuBuilder::new(handle).items(&[&file, &edit, &view]).build()?;

    let window = WebviewWindowBuilder::new(handle, MAIN, WebviewUrl::App("index.html".into()))
        .title("stlLibrary")
        .inner_size(1400.0, 900.0)
        .min_inner_size(800.0, 500.0)
        .menu(menu)
        .build()?;
    *app.state::<Shell>().launcher.lock().unwrap_or_else(|e| e.into_inner()) = Some(window.url()?);

    let tray_menu = MenuBuilder::new(handle)
        .item(&MenuItemBuilder::with_id("show", "Show stlLibrary").build(handle)?)
        .item(&MenuItemBuilder::with_id("launcher", "Switch server…").build(handle)?)
        .separator()
        .item(&MenuItemBuilder::with_id("quit", "Quit").build(handle)?)
        .build()?;
    let mut tray = TrayIconBuilder::with_id("tray").tooltip("stlLibrary").menu(&tray_menu).on_menu_event(|app, e| on_menu(app, e.id().as_ref()));
    if let Some(icon) = handle.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(handle)?;
    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Shell::default())
        .invoke_handler(tauri::generate_handler![
            list_servers,
            save_servers,
            connect,
            start_local,
            pick_folder,
            default_data_dir,
            local_state,
            open_folder
        ])
        .on_menu_event(|app, e| on_menu(app, e.id().as_ref()))
        .setup(setup)
        .build(tauri::generate_context!())
        .expect("the stlLibrary shell could not start");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            // Stop the local server (and with it the library lock).
            *app.state::<Shell>().local.lock().unwrap_or_else(|e| e.into_inner()) = None;
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn server_urls() {
        assert_eq!(normalize_url(" http://nas:8770/some/page?x=1 ").unwrap().as_str(), "http://nas:8770/");
        assert_eq!(normalize_url("https://stl.example.org").unwrap().as_str(), "https://stl.example.org/");
        assert!(normalize_url("file:///C:/x").is_err());
        assert!(normalize_url("http://user:pw@nas").is_err());
        assert!(normalize_url("nas:8770").is_err());
    }

    #[test]
    fn open_folder_refuses_relative_and_missing_paths() {
        assert!(open_folder("relative/dir".into()).is_err());
        assert!(open_folder("C:/definitely/not/here/xyz".into()).is_err());
    }
}
