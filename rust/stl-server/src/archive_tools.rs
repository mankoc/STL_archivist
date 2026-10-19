//! Archive tools on selected models (the desktop browser's context menu):
//! Rescan, Generate Thumbnails, Delete Files, and Warning Details.

use std::path::{Path, PathBuf};

use serde::Serialize;
use stl_core::control::Control;
use stl_core::rescan::{self, Options};
use stl_core::rules::{self, Checks, PresupportPatterns};
use stl_core::storage::{browser, jobs as jobs_db};
use utoipa::ToSchema;

use crate::Library;
use crate::library_settings::LibrarySettings;

fn db(e: rusqlite::Error) -> String {
    format!("database: {e}")
}

fn options(lib: &Library, settings: &LibrarySettings) -> Options {
    Options {
        unrar: lib.config.unrar_tool(),
        warn: settings.checks(),
        presupport_patterns: settings.presupport_patterns(),
        store_stl: settings.store_stl_file_list(),
    }
}

fn file_name(path: &str) -> &str {
    path.rsplit(['\\', '/']).next().unwrap_or(path)
}

/// What a per-model job did: counts, and the archive paths it changed.
#[derive(Default)]
pub struct Outcome {
    pub done: usize,
    pub touched: Vec<String>,
}

/// Rescan: re-read each archive's metadata, rules and hash, keeping user edits
/// the desktop's rescan keeps (`storage::jobs::apply_rescan`).
pub fn rescan_models(lib: &Library, ctl: &Control, log: &dyn Fn(String), ids: &[i64]) -> Result<Outcome, String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let opts = options(lib, &settings);
    ctl.set_total(ids.len() as u64);
    let mut out = Outcome::default();
    for &id in ids {
        if ctl.is_stopped() {
            break;
        }
        ctl.advance(1);
        let Some((Some(path), size, mtime, hash)) = lib.db.with(|c| jobs_db::rescan_state(c, id)).map_err(db)? else { continue };
        match rescan::rescan(&path, size, mtime, &hash, &opts) {
            Ok(values) => {
                let applied = lib.db.with_tx(|tx| Ok::<_, rusqlite::Error>(jobs_db::apply_rescan(tx, id, &values))).map_err(db)?;
                match applied {
                    Ok(true) => {
                        out.done += 1;
                        out.touched.push(path);
                    }
                    Ok(false) => {}
                    Err(e) => log(format!("{}: {e}", file_name(&path))),
                }
            }
            Err(e) => log(format!("{}: {e}", file_name(&path))),
        }
    }
    Ok(out)
}

/// Generate Thumbnails: replace each model's images with up to `max` from its archive.
pub fn generate_thumbnails(lib: &Library, ctl: &Control, log: &dyn Fn(String), ids: &[i64], max: usize) -> Result<Outcome, String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let opts = options(lib, &settings);
    let size = settings.max_thumb_size();
    ctl.set_total(ids.len() as u64);
    let mut out = Outcome::default();
    for (k, &id) in ids.iter().enumerate() {
        if ctl.is_stopped() {
            log("Thumbnail generation cancelled".into());
            break;
        }
        ctl.advance(1);
        let Some((Some(path), ..)) = lib.db.with(|c| jobs_db::rescan_state(c, id)).map_err(db)? else { continue };
        log(format!("[{}/{}] {}", k + 1, ids.len(), file_name(&path)));
        let t = match rescan::thumbnails(&path, max, size, &opts) {
            Ok(t) => t,
            Err(e) => {
                log(format!("  WARNING: {e}, skipping"));
                continue;
            }
        };
        for f in &t.failed {
            log(format!("  WARNING: thumbnail conversion failed for {f}"));
        }
        let stored = lib
            .db
            .with_tx(|tx| jobs_db::replace_thumbnails(tx, Some(&lib.thumbs), id, &t.thumbs, t.warning.as_deref()))
            .map_err(db)?;
        if let Some(n) = stored {
            log(format!("  {n} thumbnail(s) stored"));
            out.done += 1;
            out.touched.push(path);
        }
    }
    Ok(out)
}

/// Where Delete Files puts archives: the trash folder, or nowhere (deleted).
fn trash(settings: &LibrarySettings) -> Option<PathBuf> {
    let path = settings.trash_folder_path();
    (settings.use_trash_folder() && !path.is_empty()).then(|| PathBuf::from(path))
}

/// `_move_to_trash`: move the archive into the trash folder (a free name) and
/// write a JSON recovery note beside it.
fn move_to_trash(file: &Path, trash: &Path, title: Option<&str>, creator: Option<&str>, hash: Option<&str>) -> Result<(), String> {
    let stem = file.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let suffix = file.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    let mut dest = trash.join(file.file_name().ok_or("no file name")?);
    let mut n = 1;
    while dest.exists() {
        dest = trash.join(format!("{stem}_{n}{suffix}"));
        n += 1;
    }
    if std::fs::rename(file, &dest).is_err() {
        // Another drive: copy, then remove.
        std::fs::copy(file, &dest).map_err(|e| e.to_string())?;
        std::fs::remove_file(file).map_err(|e| e.to_string())?;
    }
    let note = serde_json::json!({
        "original_path": file.to_string_lossy(),
        "title": title,
        "creator": creator,
        "archive_hash": hash,
        "deleted_at": stl_core::storage::edit::utc_now_text(),
    });
    let mut sidecar = dest.clone().into_os_string();
    sidecar.push(".json");
    if let Err(e) = std::fs::write(&sidecar, serde_json::to_string_pretty(&note).unwrap_or_default()) {
        tracing::warn!("could not write the recovery note {}: {e}", Path::new(&sidecar).display());
    }
    Ok(())
}

/// Delete Files: move each archive to the trash folder (or delete it), then
/// remove the records whose file step succeeded. Needs *allow file delete* in
/// the library settings.
pub fn delete_files(lib: &Library, log: &dyn Fn(String), ids: &[i64]) -> Result<(usize, usize, Vec<String>), String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    if !settings.allow_file_delete() {
        return Err("deleting archive files is disabled in the library settings (allow file delete)".into());
    }
    let trash = trash(&settings);
    if let Some(t) = &trash {
        std::fs::create_dir_all(t).map_err(|e| format!("trash folder {}: {e}", t.display()))?;
    }
    let rows = lib.db.with(|c| browser::deletion_rows(c, ids)).map_err(db)?;
    let (mut done, mut failed, mut paths) = (Vec::new(), 0usize, Vec::new());
    for (id, path, title, creator, hash) in rows {
        let path = path.unwrap_or_default();
        let file = Path::new(&path);
        let step = if !file.exists() {
            Ok(())
        } else if let Some(t) = &trash {
            move_to_trash(file, t, title.as_deref(), creator.as_deref(), hash.as_deref())
        } else {
            std::fs::remove_file(file).map_err(|e| e.to_string())
        };
        match step {
            Ok(()) => {
                log(format!("{} {}", if trash.is_some() { "Moved to trash:" } else { "Deleted:" }, path));
                done.push(id);
                paths.push(path);
            }
            Err(e) => {
                failed += 1;
                log(format!("ERROR {path}: {e}"));
            }
        }
    }
    let refs = lib.db.with_tx(|tx| browser::delete_models(tx, &done)).map_err(db)?;
    let thumb_ids: Vec<u64> = refs.iter().filter_map(|r| r.trim().parse().ok()).collect();
    lib.thumbs.delete_many(&thumb_ids).map_err(|e| e.to_string())?;
    Ok((done.len(), failed, paths))
}

#[derive(Serialize, ToSchema)]
pub struct WarningMember {
    pub name: String,
    pub size: Option<u64>,
}

#[derive(Serialize, ToSchema)]
pub struct WarningSection {
    pub warning: String,
    /// The members that trigger it now (empty for warnings raised by an
    /// absence, or ones that no longer reproduce).
    pub members: Vec<WarningMember>,
}

#[derive(Serialize, ToSchema)]
pub struct WarningDetails {
    pub sections: Vec<WarningSection>,
    /// Why the archive's files could not be listed.
    pub error: Option<String>,
}

/// Warning Details: which archive members trigger each stored warning line
/// (every check on, as the desktop's `_WarningDetailRunnable`).
pub fn warning_details(lib: &Library, path: Option<&str>, warning: Option<&str>) -> WarningDetails {
    let lines: Vec<String> = warning.unwrap_or("").split('\n').map(str::trim).filter(|l| !l.is_empty()).map(str::to_string).collect();
    let mut matches: Vec<(&'static str, Vec<String>)> = Vec::new();
    let mut sizes: std::collections::HashMap<String, Option<u64>> = std::collections::HashMap::new();
    let error = match path.map(Path::new).filter(|p| p.is_file()) {
        None => Some("The archive is no longer at the catalogued path, so its files cannot be listed.".to_string()),
        Some(p) => match stl_core::archive::list_members(p) {
            Err(e) => Some(format!("The archive could not be read: {e}")),
            Ok(members) if members.is_empty() => Some("The archive could not be opened, so its files cannot be listed.".into()),
            Ok(members) => {
                let names: Vec<String> = members.iter().map(|m| m.name.clone()).collect();
                for m in members {
                    sizes.insert(m.name, m.size);
                }
                let settings = LibrarySettings::load(&lib.config.data_dir);
                match PresupportPatterns::new(Some(&settings.presupport_patterns())) {
                    Ok(pats) => match rules::explain_warnings(&names, Checks::default(), &pats) {
                        Ok(m) => {
                            matches = m;
                            None
                        }
                        Err(e) => Some(e.to_string()),
                    },
                    Err(e) => Some(format!("the presupport patterns use syntax only the desktop app reads ({e})")),
                }
            }
        },
    };
    let sections = lines
        .into_iter()
        .map(|line| {
            let members = matches
                .iter()
                .find(|(w, _)| *w == line)
                .map(|(_, ms)| ms.iter().map(|n| WarningMember { name: n.clone(), size: sizes.get(n).copied().flatten() }).collect())
                .unwrap_or_default();
            WarningSection { warning: line, members }
        })
        .collect();
    WarningDetails { sections, error }
}
