fn main() {
    // The launcher page may call its commands; a server's pages only `open_folder`
    // and the worker's (granted per server at runtime, see `grant`).
    const COMMANDS: &[&str] = &[
        "list_servers",
        "save_servers",
        "connect",
        "start_local",
        "pick_folder",
        "default_data_dir",
        "local_state",
        "open_folder",
        "start_worker",
        "stop_worker",
        "worker_state",
    ];
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)))
        .expect("tauri build");
}
