//! `stl-server [--data-dir DIR] [--bind ADDR:PORT]`: run the server (see `stl_server::config`).
//! `stl-server create-admin NAME [--data-dir DIR]`: create the first administrator
//! (password from `STL_ADMIN_PASSWORD` or the first line of standard input), for
//! installs where `POST /api/auth/setup` cannot come from the server's own machine
//! (a container, a headless host).
//! `stl-server openapi`: print the OpenAPI document (`web/` generates its types from it).
//! `stl-server restore BACKUP.7z [--data-dir DIR]`: restore a backup (the server's or the
//! desktop app's) into the data directory, with the library closed: the server and the
//! desktop app must not be running. The replaced files go to `pre-restore-<stamp>`.
//! `stl-server worker --server URL [--data-dir DIR] [--name NAME]`: compute CLIP
//! embeddings and AI tags for a remote server (see `stl_server::worker`); it ends
//! with exit code 2 when it cannot work (a refused token, nothing installed).

use std::sync::Arc;

use stl_server::{Config, Library, accounts, app};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_env("STL_LOG").unwrap_or_else(|_| EnvFilter::new("info")))
        .init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("create-admin") => create_admin(&args[1..]),
        Some("restore") => restore(&args[1..]),
        Some("worker") => {
            if let Err(e) = stl_server::worker::run(&args[1..]).await {
                tracing::error!("{e}");
                std::process::exit(2);
            }
            Ok(())
        }
        Some("openapi") => {
            print!("{}", stl_server::api::openapi_json());
            Ok(())
        }
        _ => run(&args).await,
    };
    if let Err(e) = result {
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

fn create_admin(args: &[String]) -> Result<(), String> {
    let (name, rest) = args.split_first().ok_or("usage: stl-server create-admin NAME [--data-dir DIR]")?;
    let config = Config::load(rest)?;
    let password = match std::env::var("STL_ADMIN_PASSWORD") {
        Ok(p) => p,
        Err(_) => {
            eprint!("Password for {name}: ");
            let mut line = String::new();
            std::io::stdin().read_line(&mut line).map_err(|e| e.to_string())?;
            line.trim_end_matches(['\r', '\n']).to_string()
        }
    };
    // Only the accounts database: this works while the server runs.
    let path = config.data_dir.join(accounts::DB_FILE);
    let pool = stl_core::storage::pool::Pool::new(&path);
    pool.with(accounts::ensure_schema).map_err(|e| format!("{}: {e}", path.display()))?;
    let user = pool
        .with_tx(|tx| {
            if accounts::user_count(tx)? > 0 {
                return Err(accounts::AccountError::Conflict("users exist already; an administrator can add more".into()));
            }
            accounts::create_user(tx, name, &password, accounts::Role::Admin)
        })
        .map_err(|e| e.to_string())?;
    println!("Created administrator {:?}.", user.name);
    Ok(())
}

fn restore(args: &[String]) -> Result<(), String> {
    let (src, rest) = args.split_first().ok_or("usage: stl-server restore BACKUP.7z [--data-dir DIR]")?;
    let config = Config::load(rest)?;
    let src = std::path::Path::new(src);
    if !src.is_file() {
        return Err(format!("{} is not a file", src.display()));
    }
    // Refuses while the server or the desktop app has the library open.
    let _lock = stl_core::lock::LibraryLock::acquire(&config.data_dir, "stl-server restore").map_err(|e| e.to_string())?;
    let stamp: String = stl_core::storage::edit::utc_now_text().chars().take(19).filter(char::is_ascii_digit).collect();
    let r = stl_core::backup::restore(src, &config.data_dir, &stamp).map_err(|e| e.to_string())?;
    println!("Restored {} into {}.", src.display(), config.data_dir.display());
    if let Some(previous) = &r.previous {
        println!("Replaced files were moved to {}.", previous.display());
    }
    if !r.thumbnails {
        println!("The backup held no thumbnails; the current thumbnail store was kept.");
    }
    Ok(())
}
