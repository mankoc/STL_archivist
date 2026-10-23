//! `stl-server [--data-dir DIR] [--bind ADDR:PORT]`: run the server (see `stl_server::config`).
//! `stl-server create-admin NAME [--data-dir DIR]`: create the first administrator
//! (password from `STL_ADMIN_PASSWORD` or the first line of standard input), for
//! installs where `POST /api/auth/setup` cannot come from the server's own machine
//! (a container, a headless host).
//! `stl-server openapi`: print the OpenAPI document (`web/` generates its types from it).
//! `stl-server restore BACKUP.7z [--data-dir DIR]`: restore a backup (the server's or the
//! desktop app's) into the data directory, with the library closed: the server and the
//! desktop app must not be running. The replaced files go to `pre-restore-<stamp>`.
//! `stl-server components status|install|remove [ID...] [--variant ID=V]... [--data-dir DIR]`:
//! the optional components of a data directory, without opening its library
//! (the desktop app manages its compute worker's folder with it).
//! `stl-server libraries [list] [--data-dir DIR]`: the libraries and their roots;
//! `stl-server libraries relocate NAME|ID NEW_ROOT [--apply] [--fix-case] [--data-dir DIR]`:
//! check a library's archives under a new root and, with `--apply`, move it there
//! (after restoring a backup on another machine). The server must not be running.
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
        Some("components") => stl_server::components::cli(&args[1..]),
        Some("libraries") => libraries(&args[1..]),
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
    stl_server::monitor::start(&library);
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

fn libraries(args: &[String]) -> Result<(), String> {
    use stl_core::storage::libraries as libs;
    let usage = "usage: stl-server libraries [list] | relocate NAME|ID NEW_ROOT [--apply] [--fix-case] [--data-dir DIR]";
    let (mut words, mut rest, mut apply, mut fix_case) = (Vec::new(), Vec::new(), false, false);
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--apply" => apply = true,
            "--fix-case" => fix_case = true,
            "--data-dir" => rest.extend([a.clone(), it.next().ok_or(usage)?.clone()]),
            _ => words.push(a.clone()),
        }
    }
    let config = Config::load(&rest)?;
    let lib = Library::open(config)?;
    let all = lib.db.with(libs::list).map_err(|e| e.to_string())?;
    match words.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        [] | ["list"] => {
            for l in &all {
                let state = if std::path::Path::new(&l.root).is_dir() { "ok" } else { "ROOT NOT FOUND" };
                println!("{:>4}  {:<30} {:>7} model(s)  {}  [{state}]", l.id, l.name, l.models, l.root);
            }
            Ok(())
        }
        ["relocate", which, root] => {
            let l = all
                .iter()
                .find(|l| l.id.to_string() == *which || l.name.eq_ignore_ascii_case(which) || l.uuid == *which)
                .ok_or_else(|| format!("no library {which:?} (see stl-server libraries list)"))?;
            let check = lib.db.with(|c| Ok(libs::check_relocate(c, l.id, root, None))).map_err(|e: rusqlite::Error| e.to_string())??;
            println!("{}: {} of {} archive(s) found under {root}", l.name, check.found, check.total);
            if !check.case_only.is_empty() {
                println!("  {} found only with another letter case{}", check.case_only.len(), if fix_case { " (fixed)" } else { " (--fix-case to adopt their spelling)" });
            }
            for m in &check.missing_examples {
                println!("  not found: {m}");
            }
            if !apply {
                println!("Nothing changed: add --apply to move the library.");
                return Ok(());
            }
            lib.db.with_tx(|tx| libs::relocate(tx, l.id, root, &check, fix_case)).map_err(|e| e.to_string())?;
            println!("{} now lives in {root}.", l.name);
            Ok(())
        }
        _ => Err(usage.into()),
    }
}
