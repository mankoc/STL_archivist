//! Crawls: the server's side of `stl_core::crawler`.

use super::*;

// ── crawl ────────────────────────────────────────────────────────────────────

/// The crawl's host on the server: PDF previews and CLIP vectors come from
/// [`crate::tools`] when their libraries are present (see `stl_core::crawler`).
struct ServerHost<'a> {
    lib: &'a Library,
    job: &'a Job,
    max_thumb_size: u32,
    /// Collects the archives the crawl added (Update Folders' move matching).
    new_paths: Option<&'a Mutex<Vec<String>>>,
}

impl Host for ServerHost<'_> {
    fn pdf_thumbnail(&self, pdf: &[u8]) -> Option<Vec<u8>> {
        self.lib.tools.pdf(&self.lib.config)?.thumbnail(pdf, self.max_thumb_size)
    }

    fn embed(&self, jpegs: &[Vec<u8>]) -> Option<Vec<Option<Vec<u8>>>> {
        self.lib.embed(jpegs).unwrap_or_else(|e| {
            self.lib.job_log(self.job, format!("Warning: {e}"));
            None
        })
    }

    fn emit(&self, event: Event) {
        match event {
            Event::Log(line) => self.lib.job_log(self.job, line),
            Event::Warning(line) => self.lib.job_log(self.job, format!("Warning: {line}")),
            Event::Phase(p) => {
                update(self.job, |i| i.phase = Some(p.clone()));
                self.lib.jobs.publish(self.job);
                self.lib.job_log(self.job, p);
            }
            Event::Progress(done, total) => {
                self.job.ctl.set_total(total as u64);
                self.job.ctl.advance(done as u64);
            }
            Event::Result(path, status) if status == "new" => {
                self.job.record(Change::New, [path.clone()]);
                if let Some(paths) = self.new_paths {
                    paths.lock().unwrap_or_else(|e| e.into_inner()).push(path);
                }
            }
            Event::Result(path, _) => self.job.record(Change::Updated, [path]),
            Event::Deleted(path) => self.job.record(Change::Deleted, [path]),
        }
    }

    fn should_stop(&self) -> bool {
        self.job.stopped()
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn crawl(
    lib: &Library,
    job: &Job,
    folder: String,
    pattern: Option<String>,
    force_rescan: bool,
    recurse: Option<bool>,
    keep_missing: bool,
    in_library: bool,
) -> Result<String, String> {
    let library_root = if in_library {
        let roots = lib.db.with(storage::libraries::Roots::load).map_err(db_err)?;
        let (id, rel) = roots.locate_folder(&folder).ok_or_else(|| format!("{folder} is not inside a library"))?;
        // A library folder's root is crawled as itself.
        if rel.is_empty() { None } else { roots.root(id).map(str::to_string) }
    } else {
        None
    };
    if let Some(root) = &library_root {
        lib.job_log(job, format!("Crawling {folder} as a part of the library folder {root}."));
    }
    let s = crawl_folder(lib, job, folder, pattern, force_rescan, recurse, keep_missing, None, library_root)?;
    Ok(crawl_message(&s))
}

/// A crawl's outcome, with whatever it did to records beyond reading archives.
pub(super) fn crawl_message(s: &crawler::Summary) -> String {
    let mut message = format!("{} new, {} updated, {} unchanged", s.new, s.updated, s.unchanged);
    for (n, what) in [(s.moved, "moved"), (s.deleted, "deleted"), (s.marked, "flagged missing")] {
        if n > 0 {
            message.push_str(&format!(", {n} {what}"));
        }
    }
    if s.listing_incomplete {
        message.push_str("; the folder could not be read in full (see the log)");
    }
    message
}

/// `_UpdateAllWorker`: crawl each folder with deletion deferred, then match
/// records left missing against archives new in another folder (keeping their
/// metadata), then delete the records of archives that are really gone.
pub(super) fn update_folders(lib: &Library, job: &Job, folders: Vec<String>) -> Result<String, String> {
    let folders = if folders.is_empty() {
        lib.db.with(duplicates::crawled_folders).map_err(db_err)?.into_iter().map(|(p, _)| p).collect()
    } else {
        folders
    };
    if folders.is_empty() {
        return Ok("no crawled folders".into());
    }
    let several = folders.len() > 1;
    let new_paths = Mutex::new(Vec::new());
    let (mut new, mut updated, mut unchanged) = (0, 0, 0);
    let mut crawled = Vec::new();
    // What is flagged under these is gone: their listings were complete.
    let mut listed = Vec::new();
    for (k, folder) in folders.iter().enumerate() {
        if job.stopped() {
            break;
        }
        lib.job_log(job, format!("[{}/{}] {folder}", k + 1, folders.len()));
        match crawl_folder(lib, job, folder.clone(), None, false, None, several, Some(&new_paths), None) {
            Ok(s) => {
                (new, updated, unchanged) = (new + s.new, updated + s.updated, unchanged + s.unchanged);
                crawled.push(folder.clone());
                if !s.listing_incomplete {
                    listed.push(folder.clone());
                }
            }
            Err(e) => lib.job_log(job, format!("ERROR {folder}: {e}")),
        }
    }
    let (mut moved_n, mut deleted_n) = (0, 0);
    if several && !job.stopped() {
        let new_paths = new_paths.into_inner().unwrap_or_else(|e| e.into_inner());
        update(job, |i| i.phase = Some("Cross-folder move detection…".into()));
        let (moved, refs) = lib.db.with_tx(|tx| storage::jobs::cross_folder(tx, &new_paths)).map_err(db_err)?;
        delete_thumb_refs(lib, &refs)?;
        for (old, new) in &moved {
            lib.job_log(job, format!("Cross-folder move: {old} → {new}"));
        }
        update(job, |i| i.phase = Some("Cleaning up deleted files…".into()));
        let (deleted, refs) = lib.db.with_tx(|tx| storage::jobs::delete_missing_under(tx, &listed)).map_err(db_err)?;
        delete_thumb_refs(lib, &refs)?;
        for p in &deleted {
            lib.job_log(job, format!("Deleted: {p}"));
        }
        job.unrecord_moved(&moved.iter().map(|(_, new)| new.as_str()).collect::<Vec<_>>());
        job.record(Change::Deleted, deleted.iter().cloned());
        (moved_n, deleted_n) = (moved.len(), deleted.len());
        job.touch_sidecars(moved.into_iter().flat_map(|(a, b)| [a, b]).chain(deleted));
    }
    Ok(format!("{} folder(s): {new} new, {updated} updated, {unchanged} unchanged, {moved_n} moved between folders, {deleted_n} deleted", crawled.len()))
}

/// Delete Database: every table emptied, then every thumbnail dropped.
pub(super) fn clear_library(lib: &Library, job: &Job) -> Result<String, String> {
    let ids: Vec<u64> = lib
        .db
        .with(|c| c.prepare("SELECT id FROM images")?.query_map([], |r| r.get::<_, i64>(0))?.collect::<rusqlite::Result<Vec<_>>>())
        .map_err(db_err)?
        .into_iter()
        .map(|i| i as u64)
        .collect();
    let models = lib.db.with(maintenance::model_count).map_err(db_err)?;
    lib.db.with_tx(maintenance::clear_all).map_err(db_err)?;
    lib.thumbs.delete_many(&ids).map_err(|e| e.to_string())?;
    lib.thumbs.delete_orphan_blobs().map_err(|e| e.to_string())?;
    lib.job_log(job, format!("Deleted {models} model(s) and {} thumbnail(s).", ids.len()));
    Ok(format!("library cleared: {models} model(s) deleted"))
}

pub(super) fn delete_thumb_refs(lib: &Library, refs: &[String]) -> Result<(), String> {
    let ids: Vec<u64> = stl_core::storage::crawl::ref_ids(refs);
    lib.thumbs.delete_many(&ids).map_err(|e| e.to_string())
}

#[allow(clippy::too_many_arguments)]
pub(super) fn crawl_folder(
    lib: &Library,
    job: &Job,
    folder: String,
    pattern: Option<String>,
    force_rescan: bool,
    recurse: Option<bool>,
    defer_missing_deletion: bool,
    new_paths: Option<&Mutex<Vec<String>>>,
    library_root: Option<String>,
) -> Result<crawler::Summary, String> {
    if !std::path::Path::new(&folder).is_dir() {
        return Err(format!("{folder} is not a folder the server can read"));
    }
    let settings = LibrarySettings::load(&lib.config.data_dir);
    let keep_pattern = pattern.clone().filter(|_| library_root.is_some());
    // No pattern for a part of a library folder: the folder's own.
    let pattern = match pattern.filter(|p| !p.is_empty() || library_root.is_none()) {
        Some(p) => p,
        None => lib
            .db
            .with(duplicates::crawled_folders)
            .map_err(|e| e.to_string())?
            .into_iter()
            .find(|(p, _)| p == library_root.as_ref().unwrap_or(&folder))
            .and_then(|(_, pat)| pat)
            .unwrap_or_default(),
    };
    let unrar = lib.config.unrar_tool();
    let cfg = CrawlConfig {
        source_dir: folder.clone(),
        keep_pattern,
        library_root,
        pattern,
        recurse: recurse.unwrap_or_else(|| settings.recurse_subdirs()),
        force_rescan,
        size_only: settings.detect_changes_by_size(),
        max_thumbnails: settings.max_thumbnails(),
        max_thumb_size: settings.max_thumb_size(),
        max_workers: settings.max_workers(),
        unrar,
        sidecars: settings.sidecars_enabled(),
        sidecar_thumbnails: settings.sidecar_thumbnails(),
        warn: settings.checks(),
        presupport_patterns: settings.presupport_patterns(),
        embed: settings.embed_during_crawl() && lib.can_embed(),
        defer_missing_deletion,
        upsert: crawl_db::UpsertConfig {
            warn_low_compression: settings.warn_low_compression(),
            low_compression_threshold: settings.low_compression_threshold(),
            embed_dim: storage::schema_sql::EMBED_DIM as usize,
        },
    };
    let host = ServerHost { lib, job, max_thumb_size: cfg.max_thumb_size, new_paths };
    crawler::crawl(&lib.config.db_path(), Some(&lib.thumbs), &cfg, &host).map_err(|e| e.to_string())
}
