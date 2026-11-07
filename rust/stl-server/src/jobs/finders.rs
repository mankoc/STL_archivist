//! The maintenance lists and the duplicate finders.

use super::*;

// ── maintenance lists ────────────────────────────────────────────────────────

pub(super) fn records(rows: Vec<crate::maint::RecordRow>) -> crate::jobs::JobResult {
    crate::jobs::JobResult::Records { rows }
}

pub(super) fn duplicate_entries(lib: &Library, job: &Job) -> Result<String, String> {
    lib.job_log(job, "Scanning for duplicate entries…".into());
    let rows: Vec<_> = lib.db.with(maintenance::duplicate_entries).map_err(db_err)?.into_iter().map(Into::into).collect();
    Ok(finder_done(job, records(rows), "duplicate record(s) found"))
}

pub(super) fn find_moved(lib: &Library, job: &Job, ids: &[i64]) -> Result<String, String> {
    lib.job_log(job, format!("Searching {} missing record(s) in the database…", ids.len()));
    let rows = crate::maint::find_moved(lib, &job.ctl, ids)?;
    Ok(finder_done(job, records(rows), "record(s) found re-crawled elsewhere"))
}

pub(super) fn search_folder(lib: &Library, job: &Job, folder: &str, ids: &[i64]) -> Result<String, String> {
    if !std::path::Path::new(folder).is_dir() {
        return Err(format!("{folder} is not a folder the server can read"));
    }
    let log = |line: String| lib.job_log(job, line);
    let (rows, touched) = crate::maint::search_folder(lib, &job.ctl, &log, folder, ids)?;
    job.touch_sidecars(touched);
    Ok(finder_done(job, records(rows), "archive(s) found and relocated"))
}

pub(super) fn read_file_lists(lib: &Library, job: &Job) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let r = crate::maint::read_file_lists(lib, &job.ctl, &log)?;
    job.touch_sidecars(r.touched);
    Ok(format!("{} read, {} unreadable or missing", r.read, r.failed))
}

pub(super) fn reread_links(lib: &Library, job: &Job, overwrite: bool) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let r = crate::maint::reread_links(lib, &job.ctl, &log, overwrite)?;
    job.touch_sidecars(r.touched);
    Ok(format!("{} updated, {} unchanged, {} unreadable", r.updated, r.unchanged, r.failed))
}

pub(super) fn recheck_warnings(lib: &Library, job: &Job, warning: &str) -> Result<String, String> {
    let which = crate::maint::Recheck::parse(warning).ok_or("unknown warning")?;
    let log = |line: String| lib.job_log(job, line);
    let r = crate::maint::recheck_warnings(lib, &job.ctl, &log, which)?;
    job.touch_sidecars(r.touched);
    Ok(format!("{} cleared, {} added, {} skipped (missing or unreadable)", r.cleared, r.added, r.skipped))
}

// ── duplicate finders ────────────────────────────────────────────────────────

pub(super) fn finder_done(job: &Job, result: crate::jobs::JobResult, what: &str) -> String {
    let n = result.len();
    job.set_result(result);
    format!("{n} {what}")
}

pub(super) fn find_content(lib: &Library, job: &Job, threshold: f64) -> Result<String, String> {
    lib.job_log(job, format!("Comparing STL file lists (at least {:.0}% shared)…", threshold * 100.0));
    match crate::dupes::content(lib, &job.ctl, threshold) {
        Err(e) if job.stopped() => Ok(e),
        r => Ok(finder_done(job, r?, "archive pair(s) found")),
    }
}

pub(super) fn find_images(lib: &Library, job: &Job, method: &str, max_distance: u32, threshold: f32, max_models: usize) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let r = if method == "clip" {
        crate::dupes::image_clip(lib, &job.ctl, &log, threshold, max_models)
    } else {
        crate::dupes::image_dhash(lib, &job.ctl, &log, max_distance, max_models)
    };
    match r {
        Err(e) if job.stopped() => Ok(e),
        r => {
            let r = r?;
            if matches!(&r, crate::jobs::JobResult::Images(i) if i.truncated) {
                lib.job_log(job, format!("Only the best {} pairs are kept: tighten the threshold.", crate::dupes::MAX_RESULTS));
            }
            Ok(finder_done(job, r, "archive pair(s) found"))
        }
    }
}

pub(super) fn find_metadata(lib: &Library, job: &Job, columns: &[String], method: &str, max_distance: u32) -> Result<String, String> {
    lib.job_log(job, format!("Comparing {} column(s) ({method})…", columns.len()));
    let r = crate::dupes::metadata(lib, &job.ctl, columns, method, max_distance)?;
    if r.len() >= crate::dupes::MAX_RESULTS {
        lib.job_log(job, format!("Only the first {} groups are kept: compare fewer columns or allow fewer edits.", crate::dupes::MAX_RESULTS));
    }
    Ok(finder_done(job, r, "group(s) found"))
}
