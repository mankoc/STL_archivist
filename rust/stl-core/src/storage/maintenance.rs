//! Database Maintenance operations: a port of `app/services/maintenance.py`
//! (`verify_archives`, `find_duplicate_entries`, the queries of `prune_images`).

use std::collections::HashMap;
use std::path::Path;

use rayon::prelude::*;
use rusqlite::{Connection, OptionalExtension, Transaction, params};

use super::Result;
use crate::control::Control;
use crate::winpath::WinPath;

pub const STATUS_OK: &str = "OK";
pub const STATUS_MISSING: &str = "Missing";
pub const STATUS_FLAGGED: &str = "Flagged Missing";
pub const STATUS_MODIFIED: &str = "Modified";
pub const STATUS_DUPLICATE: &str = "Duplicate";

/// One result row (`_result_row`).
#[derive(Debug, Clone, PartialEq)]
pub struct ResultRow {
    pub id: i64,
    pub title: String,
    pub path: String,
    pub hash: String,
    pub size: Option<i64>,
    pub status: &'static str,
    pub match_path: String,
}

type Record = (i64, Option<String>, Option<String>, Option<String>, Option<i64>, bool);

fn records(conn: &Connection) -> Result<Vec<Record>> {
    let mut st = conn.prepare(&format!(
        "SELECT id, title, {}, archive_hash, archive_size, is_missing FROM models ORDER BY id",
        super::schema_sql::archive_path_sql("models")
    ))?;
    st.query_map([], |r| {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get::<_, Option<bool>>(5)?.unwrap_or(false)))
    })?
    .collect()
}

/// What `verify_archives` found.
#[derive(Debug, Default)]
pub struct Verified {
    pub total: usize,
    pub results: Vec<ResultRow>,
    /// Records flagged missing whose file is back (their flag is cleared).
    pub reappeared: Vec<i64>,
    pub stopped: bool,
}

/// `verify_archives`: is every archive still on disk, unchanged in size?
///
/// Files are checked in parallel, in chunks, so a stop request returns the
/// records checked so far, in order, as the Python loop did. Clearing the
/// missing flag of reappeared files is left to the caller ([`clear_missing`]).
pub fn verify(conn: &Connection, ctl: &Control) -> Result<Verified> {
    let recs = records(conn)?;
    let mut out = Verified { total: recs.len(), ..Default::default() };
    ctl.set_total(recs.len() as u64);
    for chunk in recs.chunks(256) {
        if ctl.is_stopped() {
            out.stopped = true;
            break;
        }
        let checked: Vec<(ResultRow, bool)> = chunk
            .par_iter()
            .map(|(id, title, path, hash, size, missing)| {
                let meta = path.as_deref().filter(|p| !p.is_empty()).and_then(|p| std::fs::metadata(Path::new(p)).ok());
                let is_file = meta.as_ref().is_some_and(|m| m.is_file());
                let (status, back) = if *missing && !is_file {
                    (STATUS_FLAGGED, false)
                } else if !is_file {
                    (STATUS_MISSING, false)
                } else if size.is_some_and(|s| meta.as_ref().unwrap().len() as i64 != s) {
                    (STATUS_MODIFIED, false)
                } else {
                    (STATUS_OK, *missing)
                };
                let row = ResultRow {
                    id: *id,
                    title: title.clone().unwrap_or_default(),
                    path: path.clone().unwrap_or_default(),
                    hash: hash.clone().unwrap_or_default(),
                    size: *size,
                    status,
                    match_path: String::new(),
                };
                (row, back)
            })
            .collect();
        for (row, back) in checked {
            if back {
                out.reappeared.push(row.id);
            }
            out.results.push(row);
        }
        ctl.advance(chunk.len() as u64);
    }
    Ok(out)
}

/// Clear `is_missing` for files that reappeared (one UPDATE). Sets `updated_at`,
/// as SQLAlchemy's bulk `update()` applies the column's `onupdate`.
pub fn clear_missing(tx: &Transaction, ids: &[i64]) -> Result<usize> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    tx.execute(
        "UPDATE models SET is_missing = 0, updated_at = ?2 WHERE id IN (SELECT value FROM json_each(?1))",
        rusqlite::params![json, super::edit::utc_now_text()],
    )
}

/// Number of catalogued models.
pub fn model_count(conn: &Connection) -> Result<usize> {
    conn.query_row("SELECT count(*) FROM models", [], |r| r.get::<_, i64>(0)).map(|n| n as usize)
}

/// `find_duplicate_entries`: records cataloguing the same file more than once,
/// by identical real hash, else by identical `(file name, size)`.
pub fn duplicate_entries(conn: &Connection) -> Result<Vec<ResultRow>> {
    let mut st = conn.prepare(&format!("SELECT id, title, {}, archive_hash, archive_size FROM models ORDER BY id", super::schema_sql::archive_path_sql("models")))?;
    type Row = (i64, Option<String>, Option<String>, Option<String>, Option<i64>);
    let rows: Vec<Row> =
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?.collect::<Result<_>>()?;

    let mut hash_order: Vec<String> = Vec::new();
    let mut by_hash: HashMap<String, Vec<ResultRow>> = HashMap::new();
    let mut ns_order: Vec<(String, i64)> = Vec::new();
    let mut by_ns: HashMap<(String, i64), Vec<ResultRow>> = HashMap::new();
    for (id, title, path, hash, size) in rows {
        let rec = ResultRow {
            id,
            title: title.unwrap_or_default(),
            path: path.clone().unwrap_or_default(),
            hash: hash.clone().unwrap_or_default(),
            size,
            status: STATUS_DUPLICATE,
            match_path: String::new(),
        };
        match hash.filter(|h| !h.is_empty() && !h.starts_with("fallback:")) {
            Some(h) => {
                by_hash.entry(h.clone()).or_insert_with(|| {
                    hash_order.push(h.clone());
                    Vec::new()
                });
                by_hash.get_mut(&h).unwrap().push(rec);
            }
            None => {
                if let (Some(p), Some(s)) = (path.filter(|p| !p.is_empty()), size) {
                    let key = (WinPath::new(&p).name().to_string(), s);
                    by_ns.entry(key.clone()).or_insert_with(|| {
                        ns_order.push(key.clone());
                        Vec::new()
                    });
                    by_ns.get_mut(&key).unwrap().push(rec);
                }
            }
        }
    }
    fn emit(group: Vec<ResultRow>, results: &mut Vec<ResultRow>, reported: &mut std::collections::HashSet<i64>) {
        let first = group[0].path.clone();
        let second = group[1].path.clone();
        for mut rec in group {
            rec.match_path = if rec.path != first { first.clone() } else { second.clone() };
            reported.insert(rec.id);
            results.push(rec);
        }
    }
    let mut results = Vec::new();
    let mut reported = std::collections::HashSet::new();
    for h in hash_order {
        let group = by_hash.remove(&h).unwrap();
        if group.len() >= 2 {
            emit(group, &mut results, &mut reported);
        }
    }
    for key in ns_order {
        let group: Vec<ResultRow> =
            by_ns.remove(&key).unwrap().into_iter().filter(|r| !reported.contains(&r.id)).collect();
        if group.len() >= 2 {
            emit(group, &mut results, &mut reported);
        }
    }
    Ok(results)
}

/// `prune_images`, first query: models with more than `keep` images, by id.
pub fn models_over(conn: &Connection, keep: i64) -> Result<Vec<i64>> {
    let mut st = conn.prepare(
        "SELECT model_id FROM images GROUP BY model_id HAVING count(id) > ?1 ORDER BY model_id",
    )?;
    st.query_map([keep], |r| r.get(0))?.collect()
}

/// `prune_images`, per batch: `(models pruned, image ids beyond the first `keep`
/// of each model)`, lowest ids kept.
pub fn excess_images(conn: &Connection, model_ids: &[i64], keep: usize) -> Result<(usize, Vec<i64>)> {
    let json = format!("[{}]", model_ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let mut st = conn.prepare(
        "SELECT id, model_id FROM images WHERE model_id IN (SELECT value FROM json_each(?1)) ORDER BY model_id, id",
    )?;
    let mut per_model: HashMap<i64, Vec<i64>> = HashMap::new();
    let mut rows = st.query([json])?;
    while let Some(r) = rows.next()? {
        per_model.entry(r.get(1)?).or_default().push(r.get(0)?);
    }
    let mut pruned = 0;
    let mut excess = Vec::new();
    for mid in model_ids {
        if let Some(imgs) = per_model.get(mid)
            && imgs.len() > keep
        {
            pruned += 1;
            excess.extend_from_slice(&imgs[keep..]);
        }
    }
    Ok((pruned, excess))
}

/// Delete images by id (their vectors go with them through the TEMP trigger).
pub fn delete_images(tx: &Transaction, ids: &[i64]) -> Result<usize> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    tx.execute("DELETE FROM images WHERE id IN (SELECT value FROM json_each(?1))", [json])
}

// ── Database Maintenance dialog (services.maintenance) ──────────────────────

/// Images without a stored embedding (all images with `all`), by id, with
/// their legacy thumbnail blob.
pub fn embedding_targets(conn: &Connection, all: bool) -> Result<Vec<(i64, Option<Vec<u8>>)>> {
    let sql = if all {
        "SELECT id, thumbnail FROM images ORDER BY id".to_owned()
    } else {
        format!(
            "SELECT id, thumbnail FROM images WHERE id NOT IN (SELECT image_id FROM {}) ORDER BY id",
            super::schema_sql::VECTOR_TABLE
        )
    };
    let mut st = conn.prepare(&sql)?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

/// `(id, archive_path, warning)` of every model, by id.
pub type WarningRow = (i64, Option<String>, Option<String>);

pub fn warning_records(conn: &Connection) -> Result<Vec<WarningRow>> {
    let mut st = conn.prepare(&format!("SELECT id, {}, warning FROM models ORDER BY id", super::schema_sql::archive_path_sql("models")))?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect()
}

/// Set `warning` of each model; `updated_at` changes only where it differs.
pub fn set_warnings(tx: &Transaction, items: &[(i64, Option<String>)]) -> Result<()> {
    let now = super::edit::utc_now_text();
    let mut st = tx.prepare("UPDATE models SET warning = ?2, updated_at = ?3 WHERE id = ?1 AND warning IS NOT ?2")?;
    for (id, w) in items {
        st.execute(params![id, w, now])?;
    }
    Ok(())
}

pub type LocationRow = (i64, Option<String>, Option<String>, Option<i64>);

/// `(id, archive_path, archive_hash, archive_size)` of every model, by id.
pub fn location_rows(conn: &Connection) -> Result<Vec<LocationRow>> {
    let mut st = conn.prepare(&format!("SELECT id, {}, archive_hash, archive_size FROM models ORDER BY id", super::schema_sql::archive_path_sql("models")))?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?.collect()
}

/// Merge each missing record into the record at `match_path` (`moves.merge_into_missing`).
/// Returns `(merged, errors, thumbnail refs)`; an error is a record that no longer exists.
pub fn merge_moved(tx: &Transaction, items: &[(i64, String)]) -> Result<(usize, usize, Vec<String>)> {
    let (mut merged, mut errors, mut refs) = (0, 0, Vec::new());
    for (old_id, path) in items {
        let old = tx.query_row("SELECT 1 FROM models WHERE id = ?1", [old_id], |_| Ok(())).optional()?;
        let new: Option<i64> = tx
            .query_row(
                &format!("SELECT models.id FROM {} ORDER BY models.id LIMIT 1", super::crawl::LOCATED),
                [super::crawl::located_json(tx, std::slice::from_ref(path))?],
                |r| r.get(0),
            )
            .optional()?;
        match (old, new) {
            (Some(()), Some(new_id)) => {
                refs.extend(super::edit::merge_into_missing(tx, *old_id, new_id)?);
                merged += 1;
            }
            _ => errors += 1,
        }
    }
    Ok((merged, errors, refs))
}

/// Delete models, then prune unused lookups: `(thumbnail refs, {table: rows pruned})`.
pub type Removed = (Vec<String>, Vec<(&'static str, usize)>);

pub fn remove_records(tx: &Transaction, ids: &[i64]) -> Result<Removed> {
    let refs = super::browser::delete_models(tx, ids)?;
    Ok((refs, super::edit::prune_unused_lookups(tx)?))
}

/// Bytes held by free pages (`PRAGMA freelist_count * page_size`).
pub fn free_bytes(conn: &Connection) -> Result<i64> {
    let free: i64 = conn.query_row("PRAGMA freelist_count", [], |r| r.get(0))?;
    let page: i64 = conn.query_row("PRAGMA page_size", [], |r| r.get(0))?;
    Ok(free * page)
}


// ── database administration (app.database.db, filter_cache, maintenance) ──

/// `compact_database`: `VACUUM` with temporary data on disk, then
/// `wal_checkpoint(TRUNCATE)`. Returns whether the WAL was truncated (no
/// reader kept it busy).
pub fn compact(conn: &Connection) -> Result<bool> {
    conn.execute_batch("PRAGMA temp_store=FILE")?;
    let vacuum = conn.execute_batch("VACUUM");
    conn.execute_batch("PRAGMA temp_store=MEMORY")?;
    vacuum?;
    let busy: i64 = conn.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |r| r.get(0))?;
    Ok(busy == 0)
}

/// `clear_db`: delete every row of every table (children first), keeping the schema.
pub fn clear_all(tx: &Transaction) -> Result<()> {
    for table in [
        "model_tags", "model_scales", "stl_files", "archive_files", "images", "models", "subcollections", "collections", "sculptors",
        "categories", "releases", "scales", "tags", "creators", "libraries", "library_groups", "filter_cache",
    ] {
        tx.execute(&format!("DELETE FROM {table}"), [])?;
    }
    tx.execute(&format!("DELETE FROM {}", super::schema_sql::VECTOR_TABLE), []).ok();
    Ok(())
}

pub fn filter_cache_get(conn: &Connection) -> Result<Option<String>> {
    conn.query_row("SELECT payload FROM filter_cache WHERE id = 1", [], |r| r.get(0)).optional()
}

pub fn filter_cache_set(tx: &Transaction, payload: &str) -> Result<()> {
    tx.execute(
        "INSERT INTO filter_cache (id, payload, updated_at) VALUES (1, ?1, ?2) \
         ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
        params![payload, super::edit::utc_now_text()],
    )?;
    Ok(())
}

pub fn filter_cache_clear(tx: &Transaction) -> Result<()> {
    tx.execute("DELETE FROM filter_cache WHERE id = 1", [])?;
    Ok(())
}

/// Whether any of `paths` is a model's archive path (`folder_has_db_records`).
pub fn any_archive_path(conn: &Connection, paths: &[String]) -> Result<bool> {
    conn.query_row(
        &format!("SELECT EXISTS (SELECT 1 FROM {})", super::crawl::LOCATED),
        [super::crawl::located_json(conn, paths)?],
        |r| r.get(0),
    )
}

/// Image ids that need a dHash: all, or those without a current one
/// (none, or a length other than `current_len`), by id.
pub fn dhash_targets(conn: &Connection, all: bool, current_len: i64) -> Result<Vec<i64>> {
    let sql = if all {
        "SELECT id FROM images ORDER BY id"
    } else {
        "SELECT id FROM images WHERE dhash IS NULL OR length(dhash) != ?1 ORDER BY id"
    };
    let mut st = conn.prepare(sql)?;
    if all {
        st.query_map([], |r| r.get(0))?.collect()
    } else {
        st.query_map([current_len], |r| r.get(0))?.collect()
    }
}

/// Legacy thumbnail blobs (the `images.thumbnail` column) of `ids`.
pub fn legacy_thumbnails(conn: &Connection, ids: &[i64]) -> Result<Vec<(i64, Vec<u8>)>> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let mut st = conn.prepare(
        "SELECT id, thumbnail FROM images WHERE id IN (SELECT value FROM json_each(?1)) AND thumbnail IS NOT NULL",
    )?;
    st.query_map([json], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

pub fn set_dhashes(tx: &Transaction, rows: &[(i64, String)]) -> Result<()> {
    let mut st = tx.prepare("UPDATE images SET dhash = ?2 WHERE id = ?1")?;
    for (id, h) in rows {
        st.execute(params![id, h])?;
    }
    Ok(())
}

#[cfg(test)]
mod dialog_tests {
    use super::*;
    use crate::storage::{Vectors, migrate, open};

    #[test]
    fn dialog_writes() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let mut conn = open(&path).unwrap();
        conn.execute_batch(
            "INSERT INTO sculptors (name) VALUES ('unused');
             INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'F', 'F:'), ('u2', 'G', 'G:');
             INSERT INTO models (title, library_id, rel_path, archive_hash, warning, is_missing, to_print)
             VALUES ('A', 1, 'a.7z', 'h', 'w', 1, 0), ('B', 2, 'a.7z', 'h', NULL, 0, 0), ('C', 1, 'c', 'x', NULL, 0, 0);",
        )
        .unwrap();
        let tx = conn.transaction().unwrap();
        set_warnings(&tx, &[(1, None), (3, Some("n".into()))]).unwrap();
        assert_eq!(merge_moved(&tx, &[(1, "G:/a.7z".into()), (1, "nope".into())]).unwrap(), (1, 1, vec![]));
        let (refs, pruned) = remove_records(&tx, &[3]).unwrap();
        tx.commit().unwrap();
        assert!(refs.is_empty());
        assert_eq!(pruned, [("sculptors", 1)]);
        let rows = location_rows(&conn).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].1, Some(crate::relpath::join("G:", "a.7z")), "merged into B's location");
        assert!(free_bytes(&conn).unwrap() >= 0);
    }
}
