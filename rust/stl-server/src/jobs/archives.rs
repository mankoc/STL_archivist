//! Jobs on archives and their helpers: components, Rescan and thumbnails, deleting, moving and sorting files, the AI tagger.

use super::*;

// ── components ───────────────────────────────────────────────────────────────

pub(super) fn bad_components(ids: &[String], variants: &BTreeMap<String, String>) -> bool {
    use crate::components::{IDS, variants as offered};
    ids.is_empty()
        || ids.iter().any(|c| !IDS.contains(&c.as_str()))
        || variants.iter().any(|(id, v)| !offered(id).contains(&v.as_str()))
}

pub(super) fn components_hint() -> String {
    use crate::components::{IDS, variants};
    let v: Vec<String> = IDS.iter().filter(|id| !variants(id).is_empty()).map(|id| format!("{id}: {:?}", variants(id))).collect();
    let v = if v.is_empty() { String::new() } else { format!(", with variants among {}", v.join(", ")) };
    format!("choose components among {IDS:?}{v}")
}

pub(super) fn install_components(lib: &Library, job: &Job, ids: &[String], variants: &BTreeMap<String, String>) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let n = crate::components::install(&lib.place(), &job.ctl, &log, ids, variants)?;
    Ok(format!("{n} component(s) installed"))
}

// ── archive tools ────────────────────────────────────────────────────────────

pub(super) fn rescan(lib: &Library, job: &Job, ids: &[i64]) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let out = crate::archive_tools::rescan_models(lib, &job.ctl, &log, ids)?;
    job.touch_sidecars(out.touched);
    Ok(format!("{} of {} model(s) updated", out.done, ids.len()))
}

pub(super) fn generate_thumbnails(lib: &Library, job: &Job, ids: &[i64], max: Option<usize>) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    let max = max.unwrap_or_else(|| LibrarySettings::load(&lib.config.data_dir).max_thumbnails());
    let out = crate::archive_tools::generate_thumbnails(lib, &job.ctl, &log, ids, max)?;
    job.touch_sidecars(out.touched);
    Ok(format!("thumbnails generated for {} of {} model(s)", out.done, ids.len()))
}

pub(super) fn delete_files(lib: &Library, job: &Job, ids: &[i64]) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(ids.len() as u64);
    let (deleted, failed, paths) = crate::archive_tools::delete_files(lib, &log, ids)?;
    job.ctl.advance(ids.len() as u64);
    job.touch_sidecars(paths);
    // Nothing deleted and something failed: the job failed, with the first reason.
    if deleted == 0
        && let Some(first) = failed.first()
    {
        let more = if failed.len() > 1 { format!(" (and {} more, see the log)", failed.len() - 1) } else { String::new() };
        return Err(format!("could not delete {first}{more}"));
    }
    Ok(format!("{deleted} archive(s) deleted{}", if failed.is_empty() { String::new() } else { format!(", {} failed", failed.len()) }))
}

/// Sort Files: plan, move every archive or none, then point the records at
/// the new paths in one transaction.
pub(super) fn relocate_library(lib: &Library, job: &Job, id: i64, root: &str, apply: bool, fix_case: bool) -> Result<String, String> {
    use storage::libraries;
    let name = lib.db.with(|c| libraries::get(c, id)).map_err(db_err)?.ok_or_else(|| format!("no library folder {id}"))?.name;
    lib.job_log(job, format!("Checking the archives of {name} under {root}…"));
    let check = lib.db.with(|c| Ok(libraries::check_relocate(c, id, root, Some(&job.ctl)))).map_err(db_err)??;
    for m in &check.missing_examples {
        lib.job_log(job, format!("Not found: {m}"));
    }
    let applied = apply && !job.stopped();
    if applied {
        lib.db.with_tx(|tx| libraries::relocate(tx, id, root, &check, fix_case)).map_err(db_err)?;
        lib.job_log(job, format!("{name} now lives in {root}."));
    }
    let summary = format!(
        "{} of {} found, {} with another letter case, {} missing{}",
        check.found,
        check.total,
        check.case_only.len(),
        check.missing,
        if applied { "; moved" } else { " (checked only)" }
    );
    job.set_result(crate::jobs::JobResult::Relocation(crate::jobs::Relocation {
        library: id,
        root: root.to_string(),
        total: check.total,
        found: check.found,
        case_only: check.case_only.len(),
        missing: check.missing,
        missing_examples: check.missing_examples.clone(),
        inner: check.inner.len(),
        applied,
    }));
    Ok(summary)
}

pub(super) fn sort_files(lib: &Library, job: &Job, ids: &[i64], target_dir: &str, pattern: &str, missing: &str) -> Result<String, String> {
    if !LibrarySettings::load(&lib.config.data_dir).file_sorting_enabled() {
        return Err("sorting files is disabled in the library settings (file sorting)".into());
    }
    let sources = lib.db.with(|c| storage::model_files::sort_sources(c, ids)).map_err(db_err)?;
    let rows = stl_core::sort_files::preview(&sources, ids, std::path::Path::new(target_dir), pattern, missing);
    for r in rows.iter().filter(|r| !r.moves()) {
        lib.job_log(job, format!("{}: {} ({})", r.status, r.from, r.to));
    }
    job.ctl.set_total(rows.len() as u64);
    let moved = stl_core::sort_files::execute(&rows)?;
    lib.db.with_tx(|tx| storage::model_files::set_archive_paths(tx, &moved, Some(target_dir))).map_err(db_err)?;
    job.ctl.advance(rows.len() as u64);
    for r in rows.iter().filter(|r| r.moves()) {
        lib.job_log(job, format!("Moved: {} → {}", r.from, r.to));
    }
    job.touch_sidecars(rows.iter().filter(|r| r.moves()).map(|r| r.from.clone()).chain(moved.iter().map(|(_, p)| p.clone())));
    let others = rows.iter().filter(|r| r.status == "FILE").count();
    Ok(if others > 0 {
        format!("moved {} archive(s) and {others} other file(s)", moved.len())
    } else {
        format!("moved {} archive(s)", moved.len())
    })
}

// ── AI tagger ────────────────────────────────────────────────────────────────

pub(super) fn ai_suggest(lib: &Library, job: &Job, id: i64, max_tags: usize, ignore_meta: bool) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(1);
    let s = crate::ai::suggest(lib, &job.ctl, &log, id, max_tags, ignore_meta).inspect_err(|e| log_rate_limit(lib, job, e))?;
    job.ctl.advance(1);
    let msg = format!("{}: {} tag(s) suggested", s.title, s.tags.len());
    job.set_result(crate::jobs::JobResult::AiSuggestion(Box::new(s)));
    Ok(msg)
}

pub(super) fn ai_fields(lib: &Library, job: &Job, id: i64) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(1);
    let f = crate::ai::infer_fields(lib, &job.ctl, &log, id).inspect_err(|e| log_rate_limit(lib, job, e))?;
    job.ctl.advance(1);
    let msg = format!("{}: metadata inferred", if f.title.is_empty() { format!("#{id}") } else { f.title.clone() });
    job.set_result(crate::jobs::JobResult::AiFields(Box::new(f)));
    Ok(msg)
}

pub(super) fn ai_pattern(lib: &Library, job: &Job, folder: &str, ids: &[i64]) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(1);
    let p = crate::ai::infer_pattern(lib, &job.ctl, &log, folder, ids).inspect_err(|e| log_rate_limit(lib, job, e))?;
    job.ctl.advance(1);
    let msg = format!("pattern {} (reads {} of {} sampled paths)", p.pattern, p.fits, p.sampled);
    job.set_result(crate::jobs::JobResult::AiPattern(Box::new(p)));
    Ok(msg)
}

/// "AI fill" over a selection: infer and save each model's fields. A failure on
/// the first model (most likely the setup) or a rate limit ends the job; other
/// failures are logged and skipped.
pub(super) fn ai_fill(lib: &Library, job: &Job, ids: &[i64], overwrite: bool) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(ids.len() as u64);
    let mut saved = 0usize;
    for (k, &id) in ids.iter().enumerate() {
        if job.stopped() {
            break;
        }
        match crate::ai::fill(lib, &job.ctl, &log, id, overwrite) {
            Ok(Some(path)) => {
                saved += 1;
                job.touch_sidecars([path]);
            }
            Ok(None) => {}
            Err(e) if e.starts_with(crate::ai::RATE_LIMITED) => {
                log_rate_limit(lib, job, &e);
                return Err(format!("{e} (saved {saved} of {} model(s))", ids.len()));
            }
            Err(e) if k == 0 => return Err(e),
            Err(e) => log(format!("Error: model #{id}: {e}")),
        }
        job.ctl.advance(1);
    }
    Ok(format!("filled {saved} of {} model(s)", ids.len()))
}

/// Check each model's metadata with the model; the problems go in the result.
/// A failure on the first model (most likely the setup) or a rate limit ends
/// the job with what was found so far; other failures are logged and skipped.
pub(super) fn ai_check(lib: &Library, job: &Job, ids: &[i64]) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(ids.len() as u64);
    let mut rows = Vec::new();
    let mut checked = 0usize;
    for (k, &id) in ids.iter().enumerate() {
        if job.stopped() {
            break;
        }
        match crate::ai::check(lib, &job.ctl, &log, id) {
            Ok(found) => {
                for i in &found {
                    log(format!("{}: {} — {}", i.title, i.field, i.problem));
                }
                rows.extend(found);
                checked += 1;
            }
            Err(e) if e.starts_with(crate::ai::RATE_LIMITED) || k == 0 => {
                log_rate_limit(lib, job, &e);
                job.set_result(crate::jobs::JobResult::AiIssues { rows });
                return Err(e);
            }
            Err(e) => log(format!("Error: model #{id}: {e}")),
        }
        job.ctl.advance(1);
    }
    let msg = format!("{} problem(s) in {checked} of {} model(s)", rows.len(), ids.len());
    job.set_result(crate::jobs::JobResult::AiIssues { rows });
    Ok(msg)
}

/// An error line (`Error: `, shown in red) when the model's rate limit was hit.
fn log_rate_limit(lib: &Library, job: &Job, e: &str) {
    if e.starts_with(crate::ai::RATE_LIMITED) {
        lib.job_log(job, format!("Error: {e}. Stopped; try again later."));
    }
}

/// `_BatchThread`: suggest and save each model in turn. A failure on the first
/// model (most likely the setup) or a rate limit ends the job; other failures
/// are logged and skipped.
pub(super) fn ai_tag(lib: &Library, job: &Job, ids: &[i64], overwrite: bool, ignore_meta: bool, max_tags: usize) -> Result<String, String> {
    let log = |line: String| lib.job_log(job, line);
    job.ctl.set_total(ids.len() as u64);
    let mut saved = 0usize;
    for (k, &id) in ids.iter().enumerate() {
        if job.stopped() {
            break;
        }
        match crate::ai::suggest(lib, &job.ctl, &log, id, max_tags, ignore_meta) {
            Ok(s) => {
                if let Some(path) = crate::ai::commit(lib, id, s.fields, &s.tags, overwrite)? {
                    saved += 1;
                    job.touch_sidecars([path]);
                }
            }
            Err(e) if e.starts_with(crate::ai::RATE_LIMITED) => {
                log_rate_limit(lib, job, &e);
                return Err(format!("{e} (saved {saved} of {} model(s))", ids.len()));
            }
            Err(e) if k == 0 => return Err(e),
            Err(e) => lib.job_log(job, format!("Error: model #{id}: {e}")),
        }
        job.ctl.advance(1);
    }
    Ok(format!("saved {saved} of {} model(s)", ids.len()))
}
