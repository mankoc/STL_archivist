//! Browser panel and sort-dialog data: a port of `app/services/browser_data.py`.

use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension, Transaction, params};

use super::Result;
use super::edit::{archive_folder_for, delete_model, utc_now_text};

fn names(conn: &Connection, sql: &str) -> Result<Vec<String>> {
    let mut st = conn.prepare(sql)?;
    let v: Vec<Option<String>> = st.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    Ok(v.into_iter().flatten().collect())
}

/// `lookup_cache` lists: sorted names of a lookup kind (`source`: distinct
/// non-empty model sources). Unknown kinds give none.
pub fn lookup_names(conn: &Connection, kind: &str) -> Result<Vec<String>> {
    let table = match kind {
        "source" => {
            let mut v = names(conn, "SELECT DISTINCT source FROM models")?;
            v.retain(|s| !s.is_empty());
            v.sort();
            return Ok(v);
        }
        "sculptor" => "sculptors",
        "collection" => "collections",
        "subcollection" => "subcollections",
        "category" => "categories",
        "release" => "releases",
        "scale" => "scales",
        _ => return Ok(Vec::new()),
    };
    let mut v = names(conn, &format!("SELECT name FROM {table}"))?;
    v.sort();
    Ok(v)
}

pub fn tag_names(conn: &Connection) -> Result<Vec<String>> {
    names(conn, "SELECT name FROM tags ORDER BY name")
}

/// A creator profile's display fields.
pub type CreatorLinks = [Option<String>; 5];

/// `(url, description)` if the model exists, and the creator profile if one exists.
pub type Panel = (Option<(Option<String>, Option<String>)>, Option<CreatorLinks>);

/// The info panel's extra fields: the model's `(url, description)` and the
/// creator profile `(full_name, patreon, artstation, gumroad, myminifactory)`.
pub fn model_panel(
    conn: &Connection,
    model_id: i64,
    creator: Option<&str>,
) -> Result<Panel> {
    let m = conn
        .query_row("SELECT url, description FROM models WHERE id = ?1", [model_id], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .optional()?;
    let c = match creator {
        Some(name) if !name.is_empty() => conn
            .query_row(
                "SELECT full_name, patreon_url, artstation_url, gumroad_url, myminifactory_url \
                 FROM creators WHERE name = ?1",
                [name],
                |r| Ok([r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?]),
            )
            .optional()?,
        _ => None,
    };
    Ok((m, c))
}

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

/// Create a tag unless one with that exact name exists; returns whether it was created.
pub fn add_tag(tx: &Transaction, name: &str) -> Result<bool> {
    let exists = tx.query_row("SELECT 1 FROM tags WHERE name = ?1", [name], |_| Ok(())).optional()?.is_some();
    if !exists {
        tx.execute("INSERT INTO tags (name) VALUES (?1)", [name])?;
    }
    Ok(!exists)
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
    let mut st = conn.prepare(
        "SELECT m.id, m.archive_path, m.title, m.source, m.creator, m.release_year, m.release_month, m.url, \
         m.sequence, scu.name, col.name, sub.name, rel.name FROM models m \
         LEFT JOIN sculptors scu ON m.sculptor_id = scu.id \
         LEFT JOIN collections col ON m.collection_id = col.id \
         LEFT JOIN subcollections sub ON m.subcollection_id = sub.id \
         LEFT JOIN releases rel ON m.release_id = rel.id \
         WHERE m.id IN (SELECT value FROM json_each(?1)) ORDER BY m.id",
    )?;
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

/// Point models at their moved archives (`archive_path` and `archive_folder`);
/// `updated_at` changes only for rows that really change.
pub fn set_archive_paths(tx: &Transaction, items: &[(i64, String)]) -> Result<()> {
    let now = utc_now_text();
    let mut st = tx.prepare(
        "UPDATE models SET archive_path = ?2, archive_folder = ?3, updated_at = ?4 \
         WHERE id = ?1 AND (archive_path IS NOT ?2 OR archive_folder IS NOT ?3)",
    )?;
    for (id, path) in items {
        st.execute(params![id, path, archive_folder_for(path), now])?;
    }
    Ok(())
}

/// `(id, archive_path, title, creator, archive_hash)` of existing models, in id order
/// (archive deletion and its trash recovery sidecar).
pub type DeletionRow = (i64, Option<String>, Option<String>, Option<String>, Option<String>);

pub fn deletion_rows(conn: &Connection, ids: &[i64]) -> Result<Vec<DeletionRow>> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let mut st = conn.prepare(
        "SELECT id, archive_path, title, creator, archive_hash FROM models \
         WHERE id IN (SELECT value FROM json_each(?1)) ORDER BY id",
    )?;
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
             INSERT INTO models (title, source, archive_path, archive_hash, sculptor_id, is_missing, to_print)
             VALUES ('A', 'patreon', 'F:/a.7z', 'h1', 1, 0, 0), ('B', NULL, 'F:/b.7z', 'h2', 1, 0, 0),
                    ('C', 'patreon', 'F:/c.7z', 'h3', 2, 0, 0);",
        )
        .unwrap();
        assert_eq!(lookup_names(&conn, "sculptor").unwrap(), ["Alice", "Bob"]);
        assert_eq!(lookup_names(&conn, "source").unwrap(), ["patreon"]);
        let tx = conn.transaction().unwrap();
        assert!(add_tag(&tx, "elf").unwrap());
        assert!(!add_tag(&tx, "elf").unwrap());
        let ids = add_images(&tx, 1, &["a.png".into(), "b.png".into()]).unwrap();
        set_image_thumbnails(&tx, &[(ids[0], ids[0].to_string())]).unwrap();
        set_archive_paths(&tx, &[(2, r"G:\s\b.7z".into())]).unwrap();
        assert_eq!(delete_models(&tx, &[3, 99]).unwrap(), Vec::<String>::new());
        tx.commit().unwrap();
        assert_eq!(model_images(&conn, 1).unwrap(), [(ids[0], Some(ids[0].to_string())), (ids[1], None)]);
        let folder: String = conn.query_row("SELECT archive_folder FROM models WHERE id = 2", [], |r| r.get(0)).unwrap();
        assert_eq!(folder, "G:/s");
        let (rows, total) = crate::storage::stats::column(&conn, "sculptor", None).unwrap();
        assert_eq!(total, 2);
        assert_eq!(rows, [(Value::Text("Bob".into()), 2)]);
        let st = crate::storage::stats::library(&conn).unwrap();
        assert!(st.scalars.contains(&("total_file_size", Value::Integer(0))));
    }
}
