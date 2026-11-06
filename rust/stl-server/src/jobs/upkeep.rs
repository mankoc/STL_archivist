//! Maintenance: verifying, hashes, embeddings, pruning, compacting.

use super::*;

// ── maintenance ──────────────────────────────────────────────────────────────

pub(super) fn db_err(e: rusqlite::Error) -> String {
    format!("database: {e}")
}

pub(super) fn verify(lib: &Library, job: &Job) -> Result<String, String> {
    let v = lib.db.with(|c| maintenance::verify(c, &job.ctl)).map_err(db_err)?;
    let cleared = if v.reappeared.is_empty() {
        0
    } else {
        lib.db.with_tx(|tx| maintenance::clear_missing(tx, &v.reappeared)).map_err(db_err)?
    };
    let mut counts = std::collections::BTreeMap::<&str, usize>::new();
    for r in &v.results {
        *counts.entry(r.status).or_default() += 1;
        if r.status != maintenance::STATUS_OK {
            lib.job_log(job, format!("{}: {}", r.status, r.path));
        }
    }
    let parts: Vec<String> = counts.iter().map(|(s, n)| format!("{n} {s}")).collect();
    let checked = v.results.len();
    let problems = v.results.into_iter().filter(|r| r.status != maintenance::STATUS_OK).map(Into::into).collect();
    job.set_result(records(problems));
    Ok(format!("{checked} checked: {}; {cleared} back on disk", parts.join(", ")))
}

/// Logs a batch job's progress about every 5 % (and at the end), so the log of a
/// long job shows it moving.
pub(super) fn log_progress(lib: &Library, job: &Job, done: usize, total: usize, last: &mut usize, line: impl FnOnce() -> String) {
    if done >= *last + (total / 20).max(1) || (done == total && done != *last) {
        *last = done;
        lib.job_log(job, format!("[{done}/{total}] {}", line()));
    }
}

pub(super) fn recompute_dhashes(lib: &Library, job: &Job, all: bool) -> Result<String, String> {
    const DHASH_STR_LEN: i64 = 34; // "2:" + 32 hex digits
    let ids = lib.db.with(|c| maintenance::dhash_targets(c, all, DHASH_STR_LEN)).map_err(db_err)?;
    job.ctl.set_total(ids.len() as u64);
    lib.job_log(job, format!("Hashing {} image(s)…", ids.len()));
    let (mut computed, mut skipped, mut logged) = (0usize, 0usize, 0usize);
    for chunk in ids.chunks(DHASH_BATCH) {
        if job.stopped() {
            break;
        }
        let as_u64: Vec<u64> = chunk.iter().map(|&i| i as u64).collect();
        let mut thumbs: std::collections::HashMap<i64, Vec<u8>> =
            lib.thumbs.get_many(&as_u64).map_err(|e| e.to_string())?.into_iter().map(|(i, b)| (i as i64, b)).collect();
        let missing: Vec<i64> = chunk.iter().copied().filter(|i| !thumbs.contains_key(i)).collect();
        if !missing.is_empty() {
            thumbs.extend(lib.db.with(|c| maintenance::legacy_thumbnails(c, &missing)).map_err(db_err)?);
        }
        let have: Vec<i64> = chunk.iter().copied().filter(|i| thumbs.contains_key(i)).collect();
        let datas: Vec<&[u8]> = have.iter().map(|i| thumbs[i].as_slice()).collect();
        let rows: Vec<(i64, String)> = have
            .iter()
            .zip(stl_core::dhash::dhash_many(&datas))
            .filter_map(|(&i, h)| h.map(|h| (i, format!("2:{h:032x}"))))
            .collect();
        if !rows.is_empty() {
            lib.db.with_tx(|tx| maintenance::set_dhashes(tx, &rows)).map_err(db_err)?;
        }
        computed += rows.len();
        skipped += chunk.len() - rows.len();
        job.ctl.advance(chunk.len() as u64);
        log_progress(lib, job, computed + skipped, ids.len(), &mut logged, || format!("{computed} computed, {skipped} skipped"));
    }
    Ok(format!("{computed} computed, {skipped} skipped (no thumbnail or not decodable)"))
}

pub(super) fn prune_images(lib: &Library, job: &Job, keep: usize) -> Result<String, String> {
    let ids = lib.db.with(|c| maintenance::models_over(c, keep as i64)).map_err(db_err)?;
    job.ctl.set_total(ids.len() as u64);
    let (mut models, mut images) = (0usize, 0usize);
    for chunk in ids.chunks(PRUNE_BATCH) {
        if job.stopped() {
            break;
        }
        let (pruned, excess) = lib.db.with(|c| maintenance::excess_images(c, chunk, keep)).map_err(db_err)?;
        if !excess.is_empty() {
            lib.db.with_tx(|tx| maintenance::delete_images(tx, &excess)).map_err(db_err)?;
            let refs: Vec<u64> = excess.iter().map(|&i| i as u64).collect();
            lib.thumbs.delete_many(&refs).map_err(|e| e.to_string())?;
        }
        models += pruned;
        images += excess.len();
        job.ctl.advance(chunk.len() as u64);
    }
    Ok(format!("{images} image(s) removed from {models} model(s)"))
}

pub(super) fn compute_embeddings(lib: &Library, job: &Job, all: bool) -> Result<String, String> {
    use stl_core::storage::vectors;
    let log = |line: String| lib.job_log(job, line);
    if !lib.wait_for_embedder(&job.ctl, &log)? {
        return Ok("stopped".into());
    }
    let orphans = lib.db.with_tx(vectors::prune_orphans).map_err(db_err)?;
    if orphans > 0 {
        lib.job_log(job, format!("Removed {orphans} embedding(s) of deleted images."));
    }
    let rows = lib.db.with(|c| maintenance::embedding_targets(c, all)).map_err(db_err)?;
    job.ctl.set_total(rows.len() as u64);
    lib.job_log(job, format!("Computing embeddings of {} image(s)…", rows.len()));
    let dim = storage::schema_sql::EMBED_DIM as usize;
    let (mut computed, mut skipped, mut logged) = (0usize, 0usize, 0usize);
    for chunk in rows.chunks(crate::tools::BATCH * 4) {
        if job.stopped() {
            break;
        }
        let ids: Vec<u64> = chunk.iter().map(|(i, _)| *i as u64).collect();
        let mut thumbs: std::collections::HashMap<i64, Vec<u8>> =
            lib.thumbs.get_many(&ids).map_err(|e| e.to_string())?.into_iter().map(|(i, b)| (i as i64, b)).collect();
        for (id, legacy) in chunk {
            if let (false, Some(b)) = (thumbs.contains_key(id), legacy) {
                thumbs.insert(*id, b.clone());
            }
        }
        let have: Vec<i64> = chunk.iter().map(|(i, _)| *i).filter(|i| thumbs.contains_key(i)).collect();
        let images: Vec<Vec<u8>> = have.iter().map(|i| thumbs.remove(i).unwrap_or_default()).collect();
        // A compute worker may leave mid-job: wait for another (or fail as at the start).
        let vectors = loop {
            match lib.embed(&images)? {
                Some(v) => break v,
                None if lib.wait_for_embedder(&job.ctl, &log)? => {}
                None => return Ok(format!("stopped: {computed} computed, {skipped} skipped")),
            }
        };
        let items: Vec<(i64, Vec<u8>)> = have.iter().zip(vectors).filter_map(|(&i, v)| v.map(|v| (i, v))).collect();
        let stored = lib.db.with_tx(|tx| vectors::store(tx, dim, &items)).map_err(db_err)?;
        computed += stored;
        skipped += chunk.len() - stored;
        job.ctl.advance(chunk.len() as u64);
        log_progress(lib, job, computed + skipped, rows.len(), &mut logged, || format!("{computed} computed, {skipped} skipped"));
    }
    Ok(format!("{computed} computed, {skipped} skipped (no thumbnail, not decodable or a wrong model output)"))
}

pub(super) fn compact(lib: &Library, job: &Job) -> Result<String, String> {
    let size = |p: &std::path::Path| std::fs::metadata(p).map(|m| m.len()).unwrap_or(0);
    let path = lib.config.db_path();
    let before = size(&path);
    lib.job_log(job, "Rewriting the database without free pages…".into());
    lib.db.close(); // idle connections would keep the write-ahead log busy
    let conn = storage::open(&path).map_err(db_err)?;
    let truncated = maintenance::compact(&conn).map_err(db_err)?;
    drop(conn);
    let after = size(&path);
    let mb = |b: u64| b as f64 / 1e6;
    lib.job_log(job, format!("Database: {:.1} MB → {:.1} MB.", mb(before), mb(after)));
    let swept = lib.thumbs.delete_orphan_blobs().map_err(|e| e.to_string())?;
    lib.job_log(job, format!("Removed {swept} thumbnail(s) no image uses; rewriting the thumbnail store (thumbnails wait meanwhile)…"));
    let (thumbs_before, thumbs_after) = lib.thumbs.compact().map_err(|e| format!("thumbnail store: {e}"))?;
    Ok(format!(
        "database {:.1} MB → {:.1} MB{}; thumbnails {:.1} MB → {:.1} MB",
        mb(before),
        mb(after),
        if truncated { "" } else { " (the write-ahead log is truncated at a later checkpoint)" },
        mb(thumbs_before),
        mb(thumbs_after)
    ))
}
