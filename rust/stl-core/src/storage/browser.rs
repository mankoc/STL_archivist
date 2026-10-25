//! Browser panel and sort-dialog data: a port of `app/services/browser_data.py`.

use rusqlite::types::Value;
use rusqlite::{Connection, Transaction, params};

use super::Result;
use super::edit::{delete_model, utc_now_text};

/// `(id, thumbnail_path)` of a model's images, in id order.
pub fn model_images(conn: &Connection, model_id: i64) -> Result<Vec<(i64, Option<String>)>> {
    let mut st = conn.prepare("SELECT id, thumbnail_path FROM images WHERE model_id = ?1 ORDER BY id")?;
    st.query_map([model_id], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

/// Insert image records without thumbnails; returns their ids.
pub fn add_images(tx: &Transaction, model_id: i64, filenames: &[String]) -> Result<Vec<i64>> {
    let mut st = tx.prepare("INSERT INTO images (model_id, filename) VALUES (?1, ?2)")?;
    filenames
        .iter()
        .map(|f| {
            st.execute(params![model_id, f])?;
            Ok(tx.last_insert_rowid())
        })
        .collect()
}

pub fn set_image_thumbnails(tx: &Transaction, items: &[(i64, String)]) -> Result<()> {
    let mut st = tx.prepare("UPDATE images SET thumbnail_path = ?2 WHERE id = ?1")?;
    for (id, path) in items {
        st.execute(params![id, path])?;
    }
    Ok(())
}

/// `moves.delete_model` for each existing id; returns all thumbnail references.
pub fn delete_models(tx: &Transaction, ids: &[i64]) -> Result<Vec<String>> {
    let mut refs = Vec::new();
    for &id in ids {
        refs.extend(delete_model(tx, id)?);
    }
    Ok(refs)
}

/// The fields the sort dialog's path pattern uses.
pub struct SortSource {
    pub id: i64,
    pub archive_path: Option<String>,
    pub title: Option<String>,
    pub source: Option<String>,
    pub creator: Option<String>,
    pub release_year: Value,
    pub release_month: Value,
    pub url: Option<String>,
    pub sequence: Value,
    pub sculptor: Option<String>,
    pub collection: Option<String>,
    pub subcollection: Option<String>,
    pub release: Option<String>,
}

pub fn sort_sources(conn: &Connection, ids: &[i64]) -> Result<Vec<SortSource>> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let mut st = conn.prepare(&format!(
        "SELECT m.id, {}, m.title, m.source, m.creator, m.release_year, m.release_month, m.url, \
         m.sequence, scu.name, col.name, sub.name, rel.name FROM models m \
         LEFT JOIN sculptors scu ON m.sculptor_id = scu.id \
         LEFT JOIN collections col ON m.collection_id = col.id \
         LEFT JOIN subcollections sub ON m.subcollection_id = sub.id \
         LEFT JOIN releases rel ON m.release_id = rel.id \
         WHERE m.id IN (SELECT value FROM json_each(?1)) ORDER BY m.id",
        super::schema_sql::archive_path_sql("m")
    ))?;
    st.query_map([json], |r| {
        Ok(SortSource {
            id: r.get(0)?,
            archive_path: r.get(1)?,
            title: r.get(2)?,
            source: r.get(3)?,
            creator: r.get(4)?,
            release_year: r.get(5)?,
            release_month: r.get(6)?,
            url: r.get(7)?,
            sequence: r.get(8)?,
            sculptor: r.get(9)?,
            collection: r.get(10)?,
            subcollection: r.get(11)?,
            release: r.get(12)?,
        })
    })?
    .collect()
}

/// Point models at their moved archives (absolute paths); a path outside every
/// library goes into a library at `fallback_root` (or at its folder).
/// `updated_at` changes only for rows that really change.
pub fn set_archive_paths(tx: &Transaction, items: &[(i64, String)], fallback_root: Option<&str>) -> Result<()> {
    let now = utc_now_text();
    for (id, path) in items {
        let (library, rel) = super::libraries::locate_or_create(tx, path, fallback_root)?;
        tx.execute(
            "UPDATE models SET library_id = ?2, rel_path = ?3, rel_folder = ?4, updated_at = ?5 \
             WHERE id = ?1 AND (library_id IS NOT ?2 OR rel_path IS NOT ?3)",
            params![id, library, rel.as_str(), rel.folder(), now],
        )?;
    }
    Ok(())
}

/// `(id, archive_path, title, creator, archive_hash)` of existing models, in id order
/// (archive deletion and its trash recovery sidecar).
pub type DeletionRow = (i64, Option<String>, Option<String>, Option<String>, Option<String>);

pub fn deletion_rows(conn: &Connection, ids: &[i64]) -> Result<Vec<DeletionRow>> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let mut st = conn.prepare(&format!(
        "SELECT id, {}, title, creator, archive_hash FROM models \
         WHERE id IN (SELECT value FROM json_each(?1)) ORDER BY id",
        super::schema_sql::archive_path_sql("models")
    ))?;
    st.query_map([json], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?.collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, migrate, open};

    #[test]
    fn browser_writes_and_stats() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let mut conn = open(&path).unwrap();
        conn.execute_batch(
            "INSERT INTO sculptors (name) VALUES ('Bob'), ('Alice');
             INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'F', 'F:');
             INSERT INTO models (title, source, library_id, rel_path, archive_hash, sculptor_id, is_missing, to_print)
             VALUES ('A', 'patreon', 1, 'a.7z', 'h1', 1, 0, 0), ('B', NULL, 1, 'b.7z', 'h2', 1, 0, 0),
                    ('C', 'patreon', 1, 'c.7z', 'h3', 2, 0, 0);",
        )
        .unwrap();
        let tx = conn.transaction().unwrap();
        let ids = add_images(&tx, 1, &["a.png".into(), "b.png".into()]).unwrap();
        set_image_thumbnails(&tx, &[(ids[0], ids[0].to_string())]).unwrap();
        set_archive_paths(&tx, &[(2, "G:/s/b.7z".into())], None).unwrap();
        assert_eq!(delete_models(&tx, &[3, 99]).unwrap(), Vec::<String>::new());
        tx.commit().unwrap();
        assert_eq!(model_images(&conn, 1).unwrap(), [(ids[0], Some(ids[0].to_string())), (ids[1], None)]);
        let sql = format!("SELECT {} FROM models WHERE id = 2", crate::storage::schema_sql::archive_folder_sql("models"));
        let folder: String = conn.query_row(&sql, [], |r| r.get(0)).unwrap();
        assert_eq!(folder, "G:/s", "moved into a library made at its folder");
        let (rows, total) = crate::storage::stats::column(&conn, "sculptor", None).unwrap();
        assert_eq!(total, 2);
        assert_eq!(rows, [(Value::Text("Bob".into()), 2)]);
        let st = crate::storage::stats::library(&conn).unwrap();
        assert!(st.scalars.contains(&("total_file_size", Value::Integer(0))));
    }
}
