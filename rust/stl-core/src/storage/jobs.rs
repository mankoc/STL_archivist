//! Archive jobs outside the crawl: the browser's Generate Thumbnails and Rescan,
//! and Update Folders' cross-folder reconciliation and deferred-deletion clean-up.

#![allow(clippy::type_complexity)] // row tuples

use std::collections::{HashMap, HashSet};

use rusqlite::{Connection, OptionalExtension, Transaction, params};

use super::writes::{lookup, merge_into_missing, delete_model, utc_now_text};
use crate::thumbstore::ThumbStore;
use crate::winpath::WinPath;

type Result<T> = rusqlite::Result<T>;

/// `(archive_path, archive_size, archive_mtime_ns, archive_hash)` of a model.
pub fn rescan_state(conn: &Connection, model_id: i64) -> Result<Option<(Option<String>, Option<i64>, Option<i64>, String)>> {
    conn.query_row(
        &format!("SELECT {}, archive_size, archive_mtime_ns, archive_hash FROM models WHERE id = ?1", super::schema_sql::archive_path_sql("models")),
        [model_id],
        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
    )
    .optional()
}

/// Generate Thumbnails for one model: drop its images, then store `thumbs`
/// (`(member name, JPEG or None for a failed conversion)`, each keeping the place
/// of the old image with its name) and set the warning. Returns the number stored
/// and the old images' store keys, for [`super::crawl::drop_unused_thumbs`] after
/// the commit; `None` without the model.
pub fn replace_thumbnails(
    tx: &Transaction,
    store: Option<&ThumbStore>,
    model_id: i64,
    thumbs: &[(String, Option<Vec<u8>>)],
    warning: Option<&str>,
) -> Result<Option<(usize, Vec<u64>)>> {
    if tx.query_row("SELECT 1 FROM models WHERE id = ?1", [model_id], |_| Ok(())).optional()?.is_none() {
        return Ok(None);
    }
    let mut drops = Vec::new();
    let positions = super::crawl::remove_images(tx, model_id, &mut drops)?;
    let mut count = 0;
    if let Some(s) = store {
        let mut blobs: Vec<(u64, &[u8])> = Vec::new();
        for (name, jpeg) in thumbs {
            let Some(j) = jpeg.as_ref().filter(|j| !j.is_empty()) else { continue };
            tx.execute(
                "INSERT INTO images (model_id, filename, position) VALUES (?1, ?2, ?3)",
                params![model_id, name, positions.get(name)],
            )?;
            blobs.push((tx.last_insert_rowid() as u64, j.as_slice()));
        }
        // One store commit for the model.
        let stored = s.put_many(&blobs).is_ok();
        for (id, _) in &blobs {
            let id = *id as i64;
            if stored {
                tx.execute("UPDATE images SET thumbnail_path = ?2 WHERE id = ?1", params![id, id.to_string()])?;
                count += 1;
            } else {
                tx.execute("DELETE FROM images WHERE id = ?1", [id])?;
            }
        }
    }
    tx.execute(
        "UPDATE models SET warning = ?2, updated_at = ?3 WHERE id = ?1 AND warning IS NOT ?2",
        params![model_id, warning, utc_now_text()],
    )?;
    Ok(Some((count, drops)))
}

/// What the browser's Rescan writes for one model.
#[derive(Debug, Clone, Default)]
pub struct Rescan {
    pub archive_hash: String,
    pub archive_size: Option<i64>,
    pub archive_mtime_ns: Option<i64>,
    /// Set only when given.
    pub compression_rate: Option<f64>,
    /// `None` keeps the current value (the metadata had none).
    pub title: Option<String>,
    pub creator: Option<String>,
    pub source: Option<String>,
    pub release: Option<(i64, Option<i64>)>,
    pub url: Option<String>,
    pub description: Option<String>,
    pub presupports: String,
    pub warning: Option<String>,
    /// Empty keeps the current scales.
    pub scales: Vec<String>,
    /// The STL file list to store.
    pub stl_files: Vec<String>,
    /// Every member as the archive's catalog lists it (`archive_files`);
    /// `None` when the native readers cannot list it (the stored list stays).
    pub files: Option<Vec<crate::archive::ArchiveMember>>,
}

/// Rescan for one model; `Ok(false)` without the model. A file listed twice
/// is an error, before anything is written.
pub fn apply_rescan(tx: &Transaction, model_id: i64, r: &Rescan) -> std::result::Result<bool, String> {
    let db = |e: rusqlite::Error| e.to_string();
    let mut seen = HashSet::new();
    if r.stl_files.iter().any(|f| !seen.insert(f)) {
        return Err("UNIQUE constraint failed: stl_files.model_id, stl_files.filename".into());
    }
    type Cur = (Option<f64>, Option<String>, Option<String>, Option<String>, Option<i64>, Option<i64>, Option<String>, Option<String>);
    let cur: Option<Cur> = tx
        .query_row(
            "SELECT compression_rate, title, creator, source, release_year, release_month, url, description \
             FROM models WHERE id = ?1",
            [model_id],
            |q| Ok((q.get(0)?, q.get(1)?, q.get(2)?, q.get(3)?, q.get(4)?, q.get(5)?, q.get(6)?, q.get(7)?)),
        )
        .optional()
        .map_err(db)?;
    let Some((rate, title, creator, source, year, month, url, description)) = cur else { return Ok(false) };
    let rate = r.compression_rate.or(rate);
    let (year, month) = match r.release {
        Some((y, m)) => (Some(y), m),
        None => (year, month),
    };
    let pick = |new: &Option<String>, old: Option<String>| new.clone().filter(|s| !s.is_empty()).or(old);
    // Images beside the archive count as its images.
    let warning = if super::maintenance::has_outside_images(tx, model_id).map_err(db)? {
        super::maintenance::without_warning(r.warning.as_deref(), crate::rules::WARNING_NO_IMAGES)
    } else {
        r.warning.clone()
    };
    tx.execute(
        "UPDATE models SET archive_hash = ?2, archive_size = ?3, archive_mtime_ns = ?4, compression_rate = ?5, \
         title = ?6, creator = ?7, source = ?8, release_year = ?9, release_month = ?10, url = ?11, description = ?12, \
         presupports = ?13, warning = ?14, updated_at = ?15 WHERE id = ?1 AND (archive_hash IS NOT ?2 OR \
         archive_size IS NOT ?3 OR archive_mtime_ns IS NOT ?4 OR compression_rate IS NOT ?5 OR title IS NOT ?6 OR \
         creator IS NOT ?7 OR source IS NOT ?8 OR release_year IS NOT ?9 OR release_month IS NOT ?10 OR \
         url IS NOT ?11 OR description IS NOT ?12 OR presupports IS NOT ?13 OR warning IS NOT ?14)",
        params![
            model_id,
            r.archive_hash,
            r.archive_size,
            r.archive_mtime_ns,
            rate,
            pick(&r.title, title),
            pick(&r.creator, creator),
            pick(&r.source, source),
            year,
            month,
            pick(&r.url, url),
            pick(&r.description, description),
            r.presupports,
            warning.as_deref().filter(|w| !w.is_empty()),
            utc_now_text()
        ],
    )
    .map_err(db)?;
    if !r.scales.is_empty() {
        let ids = r.scales.iter().map(|s| lookup(tx, "scales", s)).collect::<Result<Vec<_>>>().map_err(db)?;
        tx.execute("DELETE FROM model_scales WHERE model_id = ?1", [model_id]).map_err(db)?;
        for id in ids {
            tx.execute("INSERT INTO model_scales (model_id, scale_id) VALUES (?1, ?2)", [model_id, id]).map_err(db)?;
        }
    }
    if let Some(files) = &r.files {
        tx.execute("DELETE FROM archive_files WHERE model_id = ?1", [model_id]).map_err(db)?;
        for m in files {
            tx.execute(
                "INSERT INTO archive_files (model_id, name, size, crc) VALUES (?1, ?2, ?3, ?4)",
                params![model_id, m.name, m.size.map(|s| s as i64), m.crc as i64],
            )
            .map_err(db)?;
        }
    }
    tx.execute("DELETE FROM stl_files WHERE model_id = ?1", [model_id]).map_err(db)?;
    for f in &r.stl_files {
        tx.execute(
            "INSERT INTO stl_files (model_id, filename, file_size, archive_hash) VALUES (?1, ?2, NULL, ?3)",
            params![model_id, f, r.archive_hash],
        )
        .map_err(db)?;
    }
    Ok(true)
}

/// Update Crawled Folders' cross-folder pass: missing records whose hash
/// matches exactly one new archive (and no other missing record) are merged
/// into it. Returns `(old path, new path)` per move and the thumbnail refs.
pub fn cross_folder(tx: &Transaction, new_paths: &[String]) -> Result<(Vec<(String, String)>, Vec<String>)> {
    let mut st = tx.prepare(&format!("SELECT id, {}, archive_hash FROM models WHERE is_missing = 1 ORDER BY id", super::schema_sql::archive_path_sql("models")))?;
    let missing: Vec<(i64, Option<String>, Option<String>)> =
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<Result<_>>()?;
    drop(st);
    if missing.is_empty() {
        return Ok((Vec::new(), Vec::new()));
    }
    let mut hash_to_missing: Vec<(String, i64)> = Vec::new();
    let mut index: HashMap<String, usize> = HashMap::new();
    let mut missing_collision: HashSet<String> = HashSet::new();
    for (id, _, h) in &missing {
        let Some(h) = h.as_ref().filter(|h| !h.is_empty()) else { continue };
        if index.contains_key(h) {
            missing_collision.insert(h.clone());
        } else {
            index.insert(h.clone(), hash_to_missing.len());
            hash_to_missing.push((h.clone(), *id));
        }
    }
    if hash_to_missing.iter().all(|(h, _)| missing_collision.contains(h)) {
        return Ok((Vec::new(), Vec::new()));
    }
    let mut st = tx.prepare(&format!("SELECT j.value->>2, models.id, archive_hash FROM {} ORDER BY models.id", super::crawl::LOCATED))?;
    let news: Vec<(String, i64, Option<String>)> =
        st.query_map([super::crawl::located_json(tx, new_paths)?], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<Result<_>>()?;
    drop(st);
    let new_ids: HashMap<String, i64> = news.iter().map(|(p, id, _)| (p.clone(), *id)).collect();
    let mut hash_to_new: HashMap<String, String> = HashMap::new();
    let mut new_collision: HashSet<String> = HashSet::new();
    for (p, _, h) in &news {
        let Some(h) = h.as_ref().filter(|h| !h.is_empty()) else { continue };
        if hash_to_new.contains_key(h) {
            new_collision.insert(h.clone());
        } else {
            hash_to_new.insert(h.clone(), p.clone());
        }
    }
    let paths: HashMap<i64, String> = missing.iter().map(|(id, p, _)| (*id, p.clone().unwrap_or_default())).collect();
    let (mut moved, mut refs, mut used) = (Vec::new(), Vec::new(), HashSet::new());
    for (h, missing_id) in &hash_to_missing {
        if missing_collision.contains(h) || new_collision.contains(h) {
            continue;
        }
        let Some(new_path) = hash_to_new.get(h) else { continue };
        if used.contains(new_path) {
            continue;
        }
        let Some(&new_id) = new_ids.get(new_path) else { continue };
        if tx.query_row("SELECT 1 FROM models WHERE id = ?1", [missing_id], |_| Ok(())).optional()?.is_none() {
            continue;
        }
        refs.extend(merge_into_missing(tx, *missing_id, new_id)?);
        used.insert(new_path.clone());
        moved.push((paths.get(missing_id).cloned().unwrap_or_default(), new_path.clone()));
    }
    Ok((moved, refs))
}

/// `Path(p).is_relative_to(root)` on Windows (case-insensitive).
fn under(p: &str, root: &str) -> bool {
    let root = WinPath::new(root).as_posix().to_lowercase();
    let mut cur = WinPath::new(p);
    loop {
        if cur.as_posix().to_lowercase() == root {
            return true;
        }
        let parent = cur.parent();
        if parent == cur {
            return false;
        }
        cur = parent;
    }
}

/// Update Crawled Folders' clean-up: delete the missing records under any of
/// `roots`. Returns their paths and the thumbnail refs.
pub fn delete_missing_under(tx: &Transaction, roots: &[String]) -> Result<(Vec<String>, Vec<String>)> {
    let mut st = tx.prepare(&format!("SELECT id, {} FROM models WHERE is_missing = 1 ORDER BY id", super::schema_sql::archive_path_sql("models")))?;
    let rows: Vec<(i64, Option<String>)> = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    drop(st);
    let (mut deleted, mut refs) = (Vec::new(), Vec::new());
    for (id, path) in rows {
        let Some(path) = path.filter(|p| !p.is_empty()) else { continue };
        if roots.iter().any(|r| under(&path, r)) {
            refs.extend(delete_model(tx, id)?);
            deleted.push(path);
        }
    }
    Ok((deleted, refs))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, crawl, maintenance, migrate, model_files, open, schema_sql};

    /// Generate Thumbnails keeps the chosen order by name, a store key the new
    /// A freed image id that another writer gives a new image, thumbnail stored but
    /// row not yet committed, keeps that thumbnail: the cleanup waits for the commit.
    #[test]
    fn a_reused_image_id_keeps_its_new_thumbnail() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let store = ThumbStore::open(&dir.path().join("thumbs")).unwrap();
        let mut writer = open(&path).unwrap();
        writer
            .execute_batch(
                "INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'F', 'F:');
                 INSERT INTO models (title, library_id, rel_path, archive_hash, is_missing, to_print) VALUES ('A', 1, 'a.7z', 'h1', 0, 0);
                 INSERT INTO images (id, model_id, filename) VALUES (6, 1, 'kept.jpg'), (7, 1, 'old.jpg');
                 DELETE FROM images WHERE id = 7;",
            )
            .unwrap();
        // Another writer takes the freed id 7 for a new image and stores its thumbnail, uncommitted.
        let tx = writer.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate).unwrap();
        tx.execute("INSERT INTO images (model_id, filename) VALUES (1, 'new.jpg')", []).unwrap();
        assert_eq!(tx.last_insert_rowid(), 7, "SQLite reuses the freed id");
        store.put_many(&[(7, b"new".as_slice())]).unwrap();
        std::thread::scope(|s| {
            let cleanup = s.spawn(|| crawl::drop_unused_thumbs(&open(&path).unwrap(), &store, &[7]).unwrap());
            std::thread::sleep(std::time::Duration::from_millis(300));
            tx.commit().unwrap();
            cleanup.join().unwrap();
        });
        assert_eq!(store.get_many(&[7]).unwrap().len(), 1, "the new image's thumbnail stays");
    }

    /// images reuse keeps its new thumbnail, and Prune keeps the cover.
    #[test]
    fn regenerated_thumbnails_keep_their_order() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let store = ThumbStore::open(&dir.path().join("thumbs")).unwrap();
        let mut conn = open(&path).unwrap();
        conn.execute_batch(
            "INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'F', 'F:');
             INSERT INTO models (title, library_id, rel_path, archive_hash, is_missing, to_print) VALUES ('A', 1, 'a.7z', 'h1', 0, 0);",
        )
        .unwrap();
        let thumbs: Vec<(String, Option<Vec<u8>>)> = ["a.jpg", "b.jpg", "c.jpg"].map(|n| (n.to_string(), Some(n.as_bytes().to_vec()))).into();
        let ids = |c: &Connection| model_files::model_images(c, 1).unwrap().into_iter().map(|(id, _)| id).collect::<Vec<i64>>();
        let tx = conn.transaction().unwrap();
        assert_eq!(replace_thumbnails(&tx, Some(&store), 1, &thumbs, None).unwrap().unwrap(), (3, vec![]));
        tx.commit().unwrap();
        let first = ids(&conn);

        let tx = conn.transaction().unwrap();
        model_files::order_images(&tx, 1, &[first[2]]).unwrap(); // c.jpg is the cover
        let (_, drops) = replace_thumbnails(&tx, Some(&store), 1, &thumbs, None).unwrap().unwrap();
        tx.commit().unwrap();
        crawl::drop_unused_thumbs(&conn, &store, &drops).unwrap();

        let sql = format!("SELECT filename FROM images WHERE model_id = 1 ORDER BY {}", schema_sql::IMAGE_ORDER);
        let names: Vec<String> = conn.prepare(&sql).unwrap().query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
        assert_eq!(names, ["c.jpg", "a.jpg", "b.jpg"], "the cover stays first");
        let now = ids(&conn);
        let keys: Vec<u64> = now.iter().map(|&i| i as u64).collect();
        assert_eq!(store.get_many(&keys).unwrap().len(), 3, "reused keys keep the new thumbnails");
        assert_eq!(maintenance::excess_images(&conn, &[1], 1).unwrap(), (1, now[1..].to_vec()), "pruning keeps the cover");
    }
}
