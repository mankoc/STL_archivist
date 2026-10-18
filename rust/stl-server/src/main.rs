//! `stl-server [--data-dir DIR] [--bind ADDR:PORT]`; see `stl_server::config`.

use std::sync::Arc;

use stl_server::{Config, Library, app};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_env("STL_LOG").unwrap_or_else(|_| EnvFilter::new("info")))
        .init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Err(e) = run(&args).await {
        tracing::error!("{e}");
        std::process::exit(1);
    }
}

async fn run(args: &[String]) -> Result<(), String> {
    let config = Config::load(args)?;
    let bind = config.bind;
    tracing::info!(data_dir = %config.data_dir.display(), "opening library");
    let library = tokio::task::spawn_blocking(move || Library::open(config)).await.map_err(|e| e.to_string())??;
    let listener = tokio::net::TcpListener::bind(bind).await.map_err(|e| format!("bind {bind}: {e}"))?;
    tracing::info!("listening on http://{bind}");
    let library = Arc::new(library);
    axum::serve(listener, app(Arc::clone(&library)).into_make_service_with_connect_info::<std::net::SocketAddr>())
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
            tracing::info!("shutting down");
        })
        .await
        .map_err(|e| e.to_string())?;
    // Edits made just before shutdown still get their sidecars.
    tokio::task::spawn_blocking(move || library.export_pending_sidecars()).await.map_err(|e| e.to_string())
}
