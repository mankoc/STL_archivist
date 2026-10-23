//! CLIP embeddings in the sqlite-vec table `image_vec`: a port of
//! `app/database/vectors.py` (`count`, `read_all`, `store`, `prune_orphans`)
//! and `app/services/image_similarity.py` (`find_similar_models`,
//! `load_model_vectors`).

use std::collections::HashMap;

use rusqlite::{Connection, Transaction, params};

use super::Result;
use super::schema_sql::VECTOR_TABLE;
use crate::winpath::WinPath;

/// sqlite-vec's ceiling on `k` in a KNN query.
pub const MAX_KNN: usize = 4096;

/// sqlite-vec versions whose chunk layout [`read_all`] decodes directly
/// (`vectors._CHUNK_LAYOUT_VERSIONS`).
const CHUNK_LAYOUT_VERSIONS: &[&str] = &["v0.1.6", "v0.1.7", "v0.1.8", "v0.1.9"];

pub fn count(conn: &Connection) -> Result<usize> {
    conn.query_row(&format!("SELECT count(*) FROM {VECTOR_TABLE}"), [], |r| r.get::<_, i64>(0)).map(|n| n as usize)
}

/// Every stored vector: `(image_ids, row-major float32 matrix)`.
///
/// Decodes sqlite-vec's chunk shadow tables (fast) for checked versions when
/// the decoded rows match `count(*)`; otherwise scans the table.
pub fn read_all(conn: &Connection, dim: usize) -> Result<(Vec<i64>, Vec<f32>)> {
    if let Some(out) = read_chunks(conn, dim)? {
        return Ok(out);
    }
    read_rows(conn, dim)
}

fn read_chunks(conn: &Connection, dim: usize) -> Result<Option<(Vec<i64>, Vec<f32>)>> {
    let version: String = conn.query_row("SELECT vec_version()", [], |r| r.get(0))?;
    if !CHUNK_LAYOUT_VERSIONS.contains(&version.as_str()) {
        return Ok(None);
    }
    let expected = count(conn)?;
    let mut ids = Vec::with_capacity(expected);
    let mut mat = Vec::with_capacity(expected * dim);
    let mut st = conn.prepare(&format!(
        "SELECT c.size, c.rowids, c.validity, v.vectors FROM {VECTOR_TABLE}_chunks c \
         JOIN {VECTOR_TABLE}_vector_chunks00 v ON v.rowid = c.chunk_id"
    ))?;
    let mut rows = st.query([])?;
    while let Some(r) = rows.next()? {
        let size = r.get::<_, i64>(0)? as usize;
        let rowids = r.get_ref(1)?.as_blob()?;
        let validity = r.get_ref(2)?.as_blob()?;
        let vecs = r.get_ref(3)?.as_blob()?;
        if rowids.len() != size * 8 || validity.len() != size.div_ceil(8) || vecs.len() != size * dim * 4 {
            return Ok(None);
        }
        for slot in 0..size {
            if validity[slot / 8] >> (slot % 8) & 1 == 0 {
                continue;
            }
            if ids.len() == expected {
                return Ok(None);
            }
            ids.push(i64::from_le_bytes(rowids[slot * 8..slot * 8 + 8].try_into().unwrap()));
            let v = &vecs[slot * dim * 4..(slot + 1) * dim * 4];
            mat.extend(v.as_chunks::<4>().0.iter().map(|b| f32::from_le_bytes(*b)));
        }
    }
    if ids.len() != expected {
        return Ok(None);
    }
    Ok(Some((ids, mat)))
}

fn read_rows(conn: &Connection, dim: usize) -> Result<(Vec<i64>, Vec<f32>)> {
    let expected = count(conn)?;
    let mut ids = Vec::with_capacity(expected);
    let mut mat = Vec::with_capacity(expected * dim);
    let mut st = conn.prepare(&format!("SELECT image_id, embedding FROM {VECTOR_TABLE}"))?;
    let mut rows = st.query([])?;
    while let Some(r) = rows.next()? {
        if ids.len() == expected {
            break;
        }
        ids.push(r.get(0)?);
        let blob = r.get_ref(1)?.as_blob()?;
        mat.extend(blob.as_chunks::<4>().0.iter().map(|b| f32::from_le_bytes(*b)));
    }
    Ok((ids, mat))
}

/// `vectors.store`: insert or replace `(image_id, float32 blob)` pairs; blobs of
/// the wrong size are skipped. Returns how many were stored.
pub fn store(tx: &Transaction, dim: usize, items: &[(i64, Vec<u8>)]) -> Result<usize> {
    let rows: Vec<&(i64, Vec<u8>)> = items.iter().filter(|(_, b)| b.len() == dim * 4).collect();
    for (id, _) in &rows {
        tx.execute(&format!("DELETE FROM {VECTOR_TABLE} WHERE image_id = ?1"), [id])?;
    }
    for (id, blob) in &rows {
        tx.execute(&format!("INSERT INTO {VECTOR_TABLE}(image_id, embedding) VALUES (?1, ?2)"), params![id, blob])?;
    }
    Ok(rows.len())
}

/// `vectors.prune_orphans`: delete vectors whose image no longer exists.
pub fn prune_orphans(tx: &Transaction) -> Result<usize> {
    tx.execute(&format!("DELETE FROM {VECTOR_TABLE} WHERE image_id NOT IN (SELECT id FROM images)"), [])
}

/// One `find_similar_models` result: `(similarity, model_id, title, archive name, thumbnail_path)`.
pub type Similar = (f64, i64, String, String, Option<String>);

/// `find_similar_models`: the best image per model among the `limit` nearest
/// images with similarity >= `threshold`, most similar first; plus whether all
/// `limit` nearest images cleared the threshold (more may exist).
pub fn find_similar_models(conn: &Connection, query: &[u8], threshold: f64, limit: usize) -> Result<(Vec<Similar>, bool)> {
    let k = limit.min(MAX_KNN) as i64;
    let maxd = 1.0 - threshold;
    let mut st = conn.prepare(&format!(
        "WITH hits AS MATERIALIZED (\n    SELECT image_id, distance FROM {VECTOR_TABLE}\n    \
         WHERE embedding MATCH ?1 AND k = ?2\n)\n\
         SELECT hits.distance, i.model_id, i.thumbnail_path, m.title, {}\nFROM hits\n\
         LEFT JOIN images i ON i.id = hits.image_id\nLEFT JOIN models m ON m.id = i.model_id\n",
        super::schema_sql::archive_path_sql("m")
    ))?;
    type Hit = (f64, Option<i64>, Option<String>, Option<String>, Option<String>);
    let rows: Vec<Hit> = st
        .query_map(params![query, k], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?
        .collect::<Result<_>>()?;
    let capped = rows.len() as i64 == k && rows.iter().map(|r| r.0).fold(f64::MIN, f64::max) <= maxd;

    let mut order: Vec<i64> = Vec::new();
    let mut best: HashMap<i64, Similar> = HashMap::new();
    for (distance, model_id, thumb, title, archive_path) in rows {
        let Some(mid) = model_id else { continue };
        if distance > maxd {
            continue;
        }
        let sim = 1.0 - distance;
        if best.get(&mid).is_none_or(|b| sim > b.0) {
            let name = archive_path.as_deref().filter(|p| !p.is_empty()).map(|p| WinPath::new(p).name().to_string());
            if !best.contains_key(&mid) {
                order.push(mid);
            }
            best.insert(mid, (sim, mid, title.unwrap_or_default(), name.unwrap_or_default(), thumb));
        }
    }
    let mut out: Vec<Similar> = order.into_iter().map(|m| best.remove(&m).unwrap()).collect();
    out.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal)); // stable, as Python's sort
    Ok((out, capped))
}

/// `load_model_vectors`: `(model_ids, matrix)` for the vectors of present
/// (not missing) models, row-aligned.
pub fn load_model_vectors(conn: &Connection, dim: usize) -> Result<(Vec<i64>, Vec<f32>)> {
    let (ids, mat) = read_all(conn, dim)?;
    let mut st = conn.prepare(
        "SELECT i.id, i.model_id FROM images i JOIN models m ON m.id = i.model_id WHERE m.is_missing IS NOT 1",
    )?;
    let model_of: HashMap<i64, i64> = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    let mut out_ids = Vec::with_capacity(ids.len());
    let mut out_mat = Vec::with_capacity(mat.len());
    for (row, id) in ids.iter().enumerate() {
        if let Some(&mid) = model_of.get(id) {
            out_ids.push(mid);
            out_mat.extend_from_slice(&mat[row * dim..(row + 1) * dim]);
        }
    }
    Ok((out_ids, out_mat))
}
