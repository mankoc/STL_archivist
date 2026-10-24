fn main() {
    // The launcher page may call its commands; a server's pages only `open_folder`,
    // the MeshPeek ones and the worker's (granted per server at runtime, see `grant`).
    const COMMANDS: &[&str] = &[
        "list_servers",
        "save_servers",
        "connect",
        "start_local",
        "pick_folder",
        "default_data_dir",
        "local_state",
        "open_folder",
        "meshpeek_state",
        "pick_meshpeek",
        "clear_meshpeek",
        "open_in_meshpeek",
        "start_worker",
        "stop_worker",
        "worker_state",
        "worker_components",
        "change_worker_components",
        "worker_install_state",
        "pick_worker_folder",
    ];
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)))
        .expect("tauri build");
}
