//! Find Duplicate Archives (content): the STL file-list overlap scan of
//! `app/services/content_duplicates.py` and the database reads of
//! `app/services/image_duplicates.py`.

use std::collections::{HashMap, HashSet};

use rusqlite::Connection;
use rusqlite::types::Value;

use super::Result;
use crate::control::Control;

/// `(image id, model id, dhash)` of thumbnailed images of present models, by image id.
pub fn present_images(conn: &Connection) -> Result<Vec<(i64, i64, Option<String>)>> {
    let mut st = conn.prepare(
        "SELECT i.id, i.model_id, i.dhash FROM images i JOIN models m ON m.id = i.model_id \
         WHERE i.thumbnail_path IS NOT NULL AND m.is_missing IS NOT 1 ORDER BY i.id",
    )?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect()
}

pub type ModelInfo = (i64, Option<String>, Option<String>, Value);

/// `(id, title, archive_path, archive_size)` of every model.
pub fn model_info(conn: &Connection) -> Result<Vec<ModelInfo>> {
    let mut st = conn.prepare(&format!("SELECT id, title, {}, archive_size FROM models", super::schema_sql::archive_path_sql("models")))?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?.collect()
}

/// `(root, pattern)` of the libraries (the folders crawls cover), by root.
pub fn crawled_folders(conn: &Connection) -> Result<Vec<(String, Option<String>)>> {
    let mut st = conn.prepare("SELECT root, pattern FROM libraries WHERE root != '' ORDER BY root")?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

/// Every model's non-empty `archive_path` (sidecar folder lists).
pub fn all_archive_paths(conn: &Connection) -> Result<Vec<String>> {
    let mut st = conn.prepare(&format!("SELECT {} FROM models", super::schema_sql::archive_path_sql("models")))?;
    let v: Vec<Option<String>> = st.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    Ok(v.into_iter().flatten().filter(|p| !p.is_empty()).collect())
}

/// One archive pair whose STL file lists overlap.
#[derive(Debug, Clone, PartialEq)]
pub struct ContentPair {
    pub id_a: i64,
    pub id_b: i64,
    pub overlap: i64,
    pub total_a: i64,
    pub total_b: i64,
    pub sim: f64,
    pub title_a: Option<String>,
    pub path_a: Option<String>,
    pub title_b: Option<String>,
    pub path_b: Option<String>,
}

/// Archive pairs sharing `(file name, size)` entries with `overlap / max(total_a,
/// total_b) >= threshold`, most similar first (ties by ids). `None` when stopped.
pub fn stl_content_pairs(conn: &Connection, threshold: f64, ctl: &Control) -> Result<Option<Vec<ContentPair>>> {
    let mut st = conn.prepare(
        "SELECT f.model_id, f.filename, f.file_size FROM stl_files f JOIN models m ON m.id = f.model_id \
         WHERE f.file_size IS NOT NULL AND m.is_missing IS NOT 1",
    )?;
    let mut model_files: HashMap<i64, HashSet<(String, i64)>> = HashMap::new();
    let rows = st.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?)))?;
    for row in rows {
        let (mid, name, size) = row?;
        model_files.entry(mid).or_default().insert((name, size));
    }
    if ctl.is_stopped() {
        return Ok(None);
    }
    let mut file_models: HashMap<&(String, i64), Vec<i64>> = HashMap::new();
    for (mid, files) in &model_files {
        for key in files {
            file_models.entry(key).or_default().push(*mid);
        }
    }
    ctl.set_total(file_models.len() as u64);
    let mut overlap: HashMap<(i64, i64), i64> = HashMap::new();
    for models in file_models.values_mut() {
        ctl.advance(1);
        if models.len() < 2 {
            continue;
        }
        if ctl.is_stopped() {
            return Ok(None);
        }
        models.sort_unstable();
        for i in 0..models.len() {
            for j in i + 1..models.len() {
                *overlap.entry((models[i], models[j])).or_default() += 1;
            }
        }
    }
    let mut pairs: Vec<(i64, i64, i64, i64, i64, f64)> = overlap
        .into_iter()
        .filter_map(|((a, b), n)| {
            let (ta, tb) = (model_files[&a].len() as i64, model_files[&b].len() as i64);
            let sim = n as f64 / ta.max(tb) as f64;
            (sim >= threshold).then_some((a, b, n, ta, tb, sim))
        })
        .collect();
    pairs.sort_by(|x, y| y.5.total_cmp(&x.5).then(x.0.cmp(&y.0)).then(x.1.cmp(&y.1)));
    if pairs.is_empty() {
        return Ok(Some(Vec::new()));
    }
    let info: HashMap<i64, (Option<String>, Option<String>)> =
        model_info(conn)?.into_iter().map(|(id, t, p, _)| (id, (t, p))).collect();
    Ok(Some(
        pairs
            .into_iter()
            .filter_map(|(a, b, n, ta, tb, sim)| {
                let (title_a, path_a) = info.get(&a)?.clone();
                let (title_b, path_b) = info.get(&b)?.clone();
                Some(ContentPair { id_a: a, id_b: b, overlap: n, total_a: ta, total_b: tb, sim, title_a, path_a, title_b, path_b })
            })
            .collect(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, migrate, open};

    #[test]
    fn content_pairs() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let conn = open(&path).unwrap();
        conn.execute_batch(
            "INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'L', '/l');
             INSERT INTO models (title, library_id, rel_path, archive_hash, is_missing, to_print)
             VALUES ('A', 1, 'a', '1', 0, 0), ('B', 1, 'b', '2', 0, 0), ('C', 1, 'c', '3', 1, 0);
             INSERT INTO stl_files (model_id, filename, file_size) VALUES
               (1, 'x.stl', 1), (1, 'y.stl', 2), (2, 'x.stl', 1), (2, 'y.stl', 3), (3, 'x.stl', 1);",
        )
        .unwrap();
        let pairs = stl_content_pairs(&conn, 0.5, &Control::new()).unwrap().unwrap();
        assert_eq!(pairs.len(), 1);
        assert_eq!((pairs[0].id_a, pairs[0].id_b, pairs[0].overlap, pairs[0].sim), (1, 2, 1, 0.5));
        assert!(stl_content_pairs(&conn, 0.6, &Control::new()).unwrap().unwrap().is_empty());
        assert_eq!(present_images(&conn).unwrap().len(), 0);
    }
}
