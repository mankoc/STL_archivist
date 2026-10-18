//! Database Maintenance operations: a port of `app/services/maintenance.py`
//! (`verify_archives`, `find_duplicate_entries`, the queries of `prune_images`).

use std::collections::HashMap;
use std::path::Path;

use rayon::prelude::*;
use rusqlite::{Connection, Transaction};

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
    let mut st = conn.prepare(
        "SELECT id, title, archive_path, archive_hash, archive_size, is_missing FROM models ORDER BY id",
    )?;
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
    let mut st = conn.prepare("SELECT id, title, archive_path, archive_hash, archive_size FROM models ORDER BY id")?;
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
