//! Jobs on sidecars, and backups.

use super::*;

// ── sidecars and backups ─────────────────────────────────────────────────────

pub(super) fn sidecar_folders(lib: &Library, job: &Job) -> Result<Vec<String>, String> {
    // Spelled as the archive paths' parents, as the sidecars store and match them.
    let paths = lib.db.with(duplicates::all_archive_paths).map_err(db_err)?;
    let folders: Vec<String> = paths.iter().filter_map(|p| crate::sidecars::folder_of(p)).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
    if folders.is_empty() {
        lib.job_log(job, "No folders with database records found.".into());
    }
    job.ctl.set_total(folders.len() as u64);
    Ok(folders)
}

pub(super) fn generate_sidecars(lib: &Library, job: &Job) -> Result<String, String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    if !settings.sidecars_enabled() {
        return Err("metadata sidecars are disabled in the library settings: enable them first".into());
    }
    let thumbs = settings.sidecar_thumbnails();
    let folders = sidecar_folders(lib, job)?;
    lib.job_log(job, format!("Processing {} folder(s); writing METADATA.7z (thumbnails: {})…", folders.len(), if thumbs { "yes" } else { "no" }));
    let (mut written, mut failed) = (0usize, 0usize);
    for (k, folder) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        let n = format!("[{}/{}]", k + 1, folders.len());
        if !std::path::Path::new(folder).is_dir() {
            lib.job_log(job, format!("{n} Skipped (folder not found): {folder}"));
        } else {
            match lib.db.with(|c| Ok::<_, rusqlite::Error>(storage::sidecar::export(c, Some(&lib.thumbs), folder, thumbs))).map_err(db_err)? {
                Ok(true) => {
                    written += 1;
                    lib.job_log(job, format!("{n} Updated: {folder}"));
                }
                Ok(false) => lib.job_log(job, format!("{n} Skipped (no records): {folder}")),
                Err(e) => {
                    failed += 1;
                    lib.job_log(job, format!("{n} ERROR in {folder}: {e:?}"));
                }
            }
        }
        job.ctl.advance(1);
    }
    Ok(format!("updated {written} of {} folder(s){}", folders.len(), if failed > 0 { format!(", {failed} failed") } else { String::new() }))
}

pub(super) fn remove_sidecars(lib: &Library, job: &Job) -> Result<String, String> {
    let folders = sidecar_folders(lib, job)?;
    let mut removed = 0usize;
    for (k, folder) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        let path = std::path::Path::new(folder).join(storage::sidecar::METADATA_FILENAME);
        let n = format!("[{}/{}]", k + 1, folders.len());
        if path.is_file() {
            match std::fs::remove_file(&path) {
                Ok(()) => {
                    removed += 1;
                    lib.job_log(job, format!("{n} Removed: {}", path.display()));
                }
                Err(e) => lib.job_log(job, format!("{n} ERROR removing {}: {e}", path.display())),
            }
        }
        job.ctl.advance(1);
    }
    Ok(format!("removed {removed} sidecar(s) from {} folder(s)", folders.len()))
}

/// Folders under the crawled folders holding a sidecar or a leftover temporary
/// one (symbolic links are not followed).
pub(super) fn folders_with_sidecars(roots: &[String], job: &Job) -> Vec<std::path::PathBuf> {
    let tmp_name = format!("{}.tmp", storage::sidecar::METADATA_FILENAME);
    let mut found = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut stack: Vec<std::path::PathBuf> = roots.iter().map(std::path::PathBuf::from).collect();
    while let Some(dir) = stack.pop() {
        if job.stopped() {
            break;
        }
        if !seen.insert(dir.to_string_lossy().to_lowercase()) {
            continue;
        }
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        let mut has = false;
        for e in rd.flatten() {
            let Ok(ft) = e.file_type() else { continue };
            if ft.is_dir() {
                stack.push(e.path());
            } else if ft.is_file() {
                let name = e.file_name();
                has |= name == storage::sidecar::METADATA_FILENAME || name.to_string_lossy() == tmp_name;
            }
        }
        if has {
            found.push(dir);
        }
    }
    found.sort();
    found
}

pub(super) fn clean_sidecars(lib: &Library, job: &Job) -> Result<String, String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let thumbs = settings.sidecar_thumbnails();
    // Folders with records, by their case-folded spelling: the stored spelling and
    // the (case-folded) archive names catalogued there.
    let paths = lib.db.with(duplicates::all_archive_paths).map_err(db_err)?;
    let mut catalogued: std::collections::HashMap<String, (String, std::collections::BTreeSet<String>)> = Default::default();
    for p in &paths {
        let Some(f) = crate::sidecars::folder_of(p) else { continue };
        let name = p[f.len()..].trim_start_matches(['/', '\\']).to_lowercase();
        catalogued.entry(f.to_lowercase()).or_insert_with(|| (f, Default::default())).1.insert(name);
    }
    let roots: Vec<String> = lib.db.with(duplicates::crawled_folders).map_err(db_err)?.into_iter().map(|(p, _)| p).collect();
    lib.job_log(job, format!("Looking for sidecars under {} crawled folder(s)…", roots.len()));
    let folders = folders_with_sidecars(&roots, job);
    job.ctl.set_total(folders.len() as u64);
    let (mut rewritten, mut removed, mut kept, mut failed) = (0usize, 0usize, 0usize, 0usize);
    for (k, dir) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        let n = format!("[{}/{}]", k + 1, folders.len());
        let tmp = dir.join(format!("{}.tmp", storage::sidecar::METADATA_FILENAME));
        if tmp.is_file() {
            match std::fs::remove_file(&tmp) {
                Ok(()) => lib.job_log(job, format!("{n} Removed leftover: {}", tmp.display())),
                Err(e) => {
                    failed += 1;
                    lib.job_log(job, format!("{n} ERROR removing {}: {e}", tmp.display()));
                }
            }
        }
        let path = dir.join(storage::sidecar::METADATA_FILENAME);
        if !path.is_file() {
            job.ctl.advance(1);
            continue;
        }
        let listed = storage::sidecar::listed_archives(&path);
        let gone = match &listed {
            Ok(names) => names.iter().filter(|a| !dir.join(a).exists()).count(),
            Err(_) => 0,
        };
        let present = listed.as_ref().map(|v| v.len() - gone).unwrap_or(0);
        let entry = catalogued.get(&dir.to_string_lossy().to_lowercase());
        // A catalogued folder's sidecar is stale when it lists other archives than the records.
        let stale = match (&listed, entry) {
            (Ok(names), Some((_, records))) => names.iter().map(|a| a.to_lowercase()).collect::<std::collections::BTreeSet<_>>() != *records,
            _ => true,
        };
        let action = match (&listed, entry.map(|(f, _)| f)) {
            // Catalogued folder: rewrite from the database when stale or unreadable.
            (_, Some(f)) if stale => {
                if !settings.sidecars_enabled() {
                    kept += 1;
                    format!("Kept (out of date, but sidecars are disabled): {}", path.display())
                } else {
                    match lib.db.with(|c| Ok::<_, rusqlite::Error>(storage::sidecar::export(c, Some(&lib.thumbs), f, thumbs))).map_err(db_err)? {
                        Ok(_) => {
                            rewritten += 1;
                            match &listed {
                                Ok(_) => format!("Rewritten (out of date): {}", path.display()),
                                Err(_) => format!("Rewritten (unreadable): {}", path.display()),
                            }
                        }
                        Err(e) => {
                            failed += 1;
                            format!("ERROR rewriting {}: {e:?}", path.display())
                        }
                    }
                }
            }
            (_, Some(_)) => {
                job.ctl.advance(1);
                continue; // up to date as far as its archives go
            }
            (Err(e), None) => {
                kept += 1;
                format!("Kept (cannot read it: {e:?}): {}", path.display())
            }
            // Not catalogued: useless once none of its archives exist; otherwise a crawl imports them.
            (Ok(_), None) if present == 0 => match std::fs::remove_file(&path) {
                Ok(()) => {
                    removed += 1;
                    format!("Removed (none of its archives exist): {}", path.display())
                }
                Err(e) => {
                    failed += 1;
                    format!("ERROR removing {}: {e}", path.display())
                }
            },
            (Ok(_), None) => {
                kept += 1;
                format!("Kept ({present} archive(s) not in the library; crawl the folder to import them): {}", path.display())
            }
        };
        lib.job_log(job, format!("{n} {action}"));
        job.ctl.advance(1);
    }
    let mut msg = format!("{} sidecar folder(s): {rewritten} rewritten, {removed} removed, {kept} kept", folders.len());
    if failed > 0 {
        msg.push_str(&format!(", {failed} failed"));
    }
    Ok(msg)
}

pub(super) fn backup(lib: &Library, job: &Job) -> Result<String, String> {
    let name = crate::api::backups::new_name();
    let dest = crate::api::backups::dir(&lib.config.data_dir).join(&name);
    lib.job_log(job, format!("Backing up the database, thumbnails and settings to {}…", dest.display()));
    let settings = lib.config.data_dir.join(stl_core::backup::SETTINGS_FILE);
    let size = lib
        .db
        .with(|c| Ok::<_, rusqlite::Error>(stl_core::backup::write(c, &lib.thumbs, Some(&settings), &dest, &job.ctl)))
        .map_err(db_err)?
        .map_err(|e| e.to_string())?;
    Ok(format!("{name}: {:.1} MB", size as f64 / (1024.0 * 1024.0)))
}
