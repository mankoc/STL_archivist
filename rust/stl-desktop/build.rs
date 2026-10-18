fn main() {
    // The launcher page may call every command; a server's pages only `open_folder`
    // (granted per server at runtime, see `connect`).
    const COMMANDS: &[&str] = &[
        "list_servers",
        "save_servers",
        "connect",
        "start_local",
        "pick_folder",
        "default_data_dir",
        "local_state",
        "open_folder",
    ];
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)))
        .expect("tauri build");
}
