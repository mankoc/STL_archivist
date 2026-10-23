//! Database Maintenance jobs beyond the simple ones in `jobs.rs`: the record
//! lists of Verify and Find Duplicate Entries, Find Moved in DB, Search in
//! Folder and the warning re-checks (the desktop's `db_maintenance_dialog.py`).

use std::collections::{HashMap, HashSet};
use std::path::Path;

use rayon::prelude::*;
use serde::Serialize;
use stl_core::control::Control;
use stl_core::rules::{self, Checks, PresupportPatterns};
use stl_core::storage::{browser, crawl as crawl_db, maintenance};
use utoipa::ToSchema;

use crate::Library;
use crate::library_settings::LibrarySettings;

pub const STATUS_MOVED: &str = "Moved (in DB)";
pub const STATUS_FOUND: &str = "Found";
const RETIRED_WARNING_SUPPORTS: &str = "Archive with supports";
const WARNING_UNREADABLE: &str = "Unreadable archive";
const WARNING_BATCH: usize = 200;

/// One record of a maintenance list (`_result_row`).
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct RecordRow {
    pub id: i64,
    pub title: String,
    pub path: String,
    pub hash: String,
    pub size: Option<i64>,
    /// `Missing`, `Flagged Missing`, `Modified`, `Duplicate`, `Moved (in DB)` or `Found`.
    pub status: String,
    /// Another record's path (duplicates, moved) or where the archive was found.
    pub match_path: String,
}

impl From<maintenance::ResultRow> for RecordRow {
    fn from(r: maintenance::ResultRow) -> Self {
        RecordRow { id: r.id, title: r.title, path: r.path, hash: r.hash, size: r.size, status: r.status.into(), match_path: r.match_path }
    }
}

fn db(e: rusqlite::Error) -> String {
    format!("database: {e}")
}

fn file_name(path: &str) -> &str {
    path.rsplit(['\\', '/']).next().unwrap_or(path)
}

/// Titles of the given models (for result rows).
fn titles(lib: &Library, ids: &[i64]) -> Result<HashMap<i64, String>, String> {
    lib.db
        .with(|c| {
            let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
            let mut st = c.prepare("SELECT id, coalesce(title, '') FROM models WHERE id IN (SELECT value FROM json_each(?1))")?;
            st.query_map([json], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
        })
        .map_err(db)
}

/// Find Moved in DB: for each record, another record with the same content
/// hash (else the same file name and size) whose archive is on disk.
pub fn find_moved(lib: &Library, ctl: &Control, ids: &[i64]) -> Result<Vec<RecordRow>, String> {
    let rows = lib.db.with(maintenance::location_rows).map_err(db)?;
    let mut by_hash: HashMap<&str, Vec<(&str, i64)>> = HashMap::new();
    let mut by_name_size: HashMap<(&str, i64), Vec<(&str, i64)>> = HashMap::new();
    for (id, path, hash, size) in &rows {
        let Some(path) = path.as_deref().filter(|p| !p.is_empty()) else { continue };
        if let Some(h) = hash.as_deref().filter(|h| !h.is_empty()) {
            by_hash.entry(h).or_default().push((path, *id));
        }
        if let Some(s) = size {
            by_name_size.entry((file_name(path), *s)).or_default().push((path, *id));
        }
    }
    let wanted: HashSet<i64> = ids.iter().copied().collect();
    let titles = titles(lib, ids)?;
    ctl.set_total(wanted.len() as u64);
    let mut out = Vec::new();
    for (id, path, hash, size) in rows.iter().filter(|r| wanted.contains(&r.0)) {
        ctl.advance(1);
        let mut candidates: &[(&str, i64)] = &[];
        if let Some(h) = hash.as_deref().filter(|h| !h.is_empty()) {
            candidates = by_hash.get(h).map_or(&[], Vec::as_slice);
        }
        if candidates.is_empty()
            && let (Some(p), Some(s)) = (path.as_deref().filter(|p| !p.is_empty()), size)
        {
            candidates = by_name_size.get(&(file_name(p), *s)).map_or(&[], Vec::as_slice);
        }
        if let Some((m, _)) = candidates.iter().find(|(m, mid)| mid != id && Path::new(m).exists()) {
            out.push(RecordRow {
                id: *id,
                title: titles.get(id).cloned().unwrap_or_default(),
                path: path.clone().unwrap_or_default(),
                hash: hash.clone().unwrap_or_default(),
                size: *size,
                status: STATUS_MOVED.into(),
                match_path: m.to_string(),
            });
        }
    }
    Ok(out)
}

/// The fingerprint the crawler would store for `path` (native readers only;
/// archives they refuse get the size/mtime fallback, as on a server crawl).
fn fingerprint(path: &str) -> String {
    stl_core::archive::fingerprint(Path::new(path)).unwrap_or_else(|_| match crawl_db::stat(path) {
        Some((size, mtime)) => format!("fallback:{size}:{mtime}"),
        None => "fallback:0:0".into(),
    })
}

/// Search in Folder: archives under `folder` whose content hash matches one of
/// the records; matches are pointed at their new path. Returns the found rows
/// and the archive paths touched (old and new) for the sidecars.
pub fn search_folder(
    lib: &Library,
    ctl: &Control,
    log: &dyn Fn(String),
    folder: &str,
    ids: &[i64],
) -> Result<(Vec<RecordRow>, Vec<String>), String> {
    let wanted: HashSet<i64> = ids.iter().copied().collect();
    let mut targets: HashMap<String, (i64, Option<String>, Option<i64>)> = HashMap::new();
    for (id, path, hash, size) in lib.db.with(maintenance::location_rows).map_err(db)? {
        if let (true, Some(h)) = (wanted.contains(&id), hash.filter(|h| !h.is_empty())) {
            targets.entry(h).or_insert((id, path, size));
        }
    }
    // A fallback fingerprint carries the file size: skip candidates of another size.
    let fallback_size = |h: &str| h.strip_prefix("fallback:").and_then(|r| r.split(':').next()?.parse::<i64>().ok());
    let candidates = stl_core::crawler::list_archives(folder, true, &[]);
    if candidates.is_empty() {
        log("No archive files found in the selected folder.".into());
        return Ok((Vec::new(), Vec::new()));
    }
    log(format!("Scanning {} archive(s) in {folder} for {} missing record(s)…", candidates.len(), targets.len()));
    ctl.set_total(candidates.len() as u64);
    let titles = titles(lib, ids)?;
    let (mut found, mut touched) = (Vec::new(), Vec::new());
    for chunk in candidates.chunks(64) {
        if ctl.is_stopped() || targets.is_empty() {
            break;
        }
        let only_fallback = targets.keys().all(|h| h.starts_with("fallback:"));
        let sizes: HashSet<i64> = targets.keys().filter_map(|h| fallback_size(h)).collect();
        let hashed: Vec<(String, String)> = chunk
            .par_iter()
            .filter(|c| !only_fallback || crawl_db::stat(c).is_some_and(|(s, _)| sizes.contains(&s)))
            .map(|c| (c.clone(), fingerprint(c)))
            .collect();
        ctl.advance(chunk.len() as u64);
        let mut moves = Vec::new();
        for (candidate, h) in hashed {
            if let Some((id, old, size)) = targets.remove(&h) {
                log(format!("Found: {} → record #{id}", file_name(&candidate)));
                moves.push((id, candidate.clone()));
                touched.extend(old.clone());
                touched.push(candidate.clone());
                found.push(RecordRow {
                    id,
                    title: titles.get(&id).cloned().unwrap_or_default(),
                    path: old.unwrap_or_default(),
                    hash: h,
                    size,
                    status: STATUS_FOUND.into(),
                    match_path: candidate.clone(),
                });
            }
        }
        if !moves.is_empty() {
            lib.db.with_tx(|tx| browser::set_archive_paths(tx, &moves, Some(folder))).map_err(db)?;
        }
    }
    log(format!("Search complete: {} of {} missing archive(s) located.", found.len(), found.len() + targets.len()));
    Ok((found, touched))
}

/// Which warning a re-check owns.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Recheck {
    NoImages,
    Presupported,
}

impl Recheck {
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "no_images" => Some(Recheck::NoImages),
            "presupported" => Some(Recheck::Presupported),
            _ => None,
        }
    }

    fn line(self) -> &'static str {
        match self {
            Recheck::NoImages => rules::WARNING_NO_IMAGES,
            Recheck::Presupported => rules::WARNING_PRESUPPORTED,
        }
    }

    fn retired(self) -> &'static [&'static str] {
        match self {
            Recheck::NoImages => &[],
            Recheck::Presupported => &[RETIRED_WARNING_SUPPORTS],
        }
    }
}

/// `sort_warning_lines`: canonical order, unknown lines last (stable).
fn sort_warning_lines(lines: &mut [String]) {
    const ORDER: [&str; 6] = [
        rules::WARNING_PRESUPPORTED,
        rules::WARNING_NO_IMAGES,
        rules::WARNING_NESTED,
        rules::WARNING_NO_PRINTABLE,
        crawl_db::WARNING_LOW_COMPRESSION,
        WARNING_UNREADABLE,
    ];
    lines.sort_by_key(|l| ORDER.iter().position(|o| o == l).unwrap_or(ORDER.len()));
}

/// What a re-check changed.
#[derive(Debug, Default)]
pub struct Rechecked {
    pub cleared: usize,
    pub added: usize,
    pub skipped: usize,
    /// Archive paths of the records changed (for the sidecars).
    pub touched: Vec<String>,
}

/// Check Images / Presupport Warning: re-read every archive's file list and fix
/// this warning line only. Adding needs the warning enabled in the settings;
/// a stale line is always cleared. Unreadable archives are skipped.
pub fn recheck_warnings(lib: &Library, ctl: &Control, log: &dyn Fn(String), which: Recheck) -> Result<Rechecked, String> {
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let checks = settings.checks();
    let may_add = match which {
        Recheck::NoImages => checks.no_images,
        Recheck::Presupported => checks.presupported,
    };
    let patterns = if which == Recheck::Presupported {
        let custom = settings.presupport_patterns();
        PresupportPatterns::new(Some(&custom)).map_err(|e| {
            format!("the presupport patterns use syntax only the desktop app reads ({e}); run this check there")
        })?
    } else {
        PresupportPatterns::new::<&str>(None).map_err(|e| e.to_string())?
    };
    let records = lib.db.with(maintenance::warning_records).map_err(db)?;
    if records.is_empty() {
        log("No records found in the database.".into());
        return Ok(Rechecked::default());
    }
    log(match which {
        Recheck::NoImages => format!("Checking {} archive(s) for image files…", records.len()),
        Recheck::Presupported => format!("Checking {} archive(s) for presupported .stl files…", records.len()),
    });
    let only = Checks {
        presupported: which == Recheck::Presupported,
        no_images: which == Recheck::NoImages,
        nested_archive: false,
        no_printable: false,
    };
    let evaluate = |path: &Option<String>| -> Option<bool> {
        let p = Path::new(path.as_deref().filter(|p| !p.is_empty())?);
        if !p.is_file() {
            return None;
        }
        let names: Vec<String> = stl_core::archive::list_members(p).ok()?.into_iter().map(|m| m.name).collect();
        if names.is_empty() {
            return None;
        }
        let found = rules::explain_warnings(&names, only, &patterns).ok()?;
        Some(found.iter().any(|(w, _)| *w == which.line()))
    };
    ctl.set_total(records.len() as u64);
    let mut out = Rechecked::default();
    for chunk in records.chunks(WARNING_BATCH) {
        if ctl.is_stopped() {
            log("Stopped by user.".into());
            break;
        }
        let verdicts: Vec<Option<bool>> = chunk.par_iter().map(|(_, path, _)| evaluate(path)).collect();
        ctl.advance(chunk.len() as u64);
        let mut pending = Vec::new();
        for ((id, path, warning), verdict) in chunk.iter().zip(verdicts) {
            let mut lines: Vec<String> =
                warning.as_deref().unwrap_or("").split('\n').filter(|l| !l.trim().is_empty()).map(str::to_string).collect();
            let before = lines.len();
            lines.retain(|l| !which.retired().contains(&l.as_str()));
            let retired = before - lines.len();
            out.cleared += retired;
            match verdict {
                None => {
                    out.skipped += 1;
                    if retired == 0 {
                        continue;
                    }
                }
                Some(should) => {
                    let flagged = lines.iter().any(|l| l == which.line());
                    if !should && flagged {
                        lines.retain(|l| l != which.line());
                        out.cleared += 1;
                    } else if should && !flagged && may_add {
                        lines.push(which.line().to_string());
                        out.added += 1;
                    } else if retired == 0 {
                        continue;
                    }
                }
            }
            sort_warning_lines(&mut lines);
            pending.push((*id, if lines.is_empty() { None } else { Some(lines.join("\n")) }));
            out.touched.extend(path.clone());
        }
        if !pending.is_empty() {
            lib.db.with_tx(|tx| maintenance::set_warnings(tx, &pending)).map_err(db)?;
        }
    }
    log(format!(
        "Done: {} stale warning(s) cleared, {} added, {} skipped (archive missing or unreadable).",
        out.cleared, out.added, out.skipped
    ));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn warning_lines_sort_canonically() {
        let mut v: Vec<String> =
            ["custom", rules::WARNING_NO_PRINTABLE, rules::WARNING_PRESUPPORTED].iter().map(|s| s.to_string()).collect();
        sort_warning_lines(&mut v);
        assert_eq!(v, [rules::WARNING_PRESUPPORTED, rules::WARNING_NO_PRINTABLE, "custom"]);
    }

    #[test]
    fn file_names_split_either_separator() {
        assert_eq!(file_name("C:\\a\\b.zip"), "b.zip");
        assert_eq!(file_name("/a/b.zip"), "b.zip");
        assert_eq!(file_name("b.zip"), "b.zip");
    }
}
