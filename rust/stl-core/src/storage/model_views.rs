//! Read-only model views for the viewer dialogs: a port of
//! `app/services/model_views.py`.

use super::schema_sql::archive_path_sql;
use std::collections::HashMap;

use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension};

use super::Result;

fn id_json(ids: &[i64]) -> String {
    format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","))
}

pub type StlFileRow = (String, Option<i64>, Option<String>);

pub fn stl_files(conn: &Connection, model_id: i64) -> Result<Vec<StlFileRow>> {
    let mut st =
        conn.prepare("SELECT filename, file_size, archive_hash FROM stl_files WHERE model_id = ?1 ORDER BY filename")?;
    st.query_map([model_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect()
}

/// `(name, size, crc)` of every member of a model's archive, as the crawl
/// stored them (`archive_files`), by name.
pub fn archive_files(conn: &Connection, model_id: i64) -> Result<Vec<(String, Option<i64>, i64)>> {
    let mut st = conn.prepare("SELECT name, size, crc FROM archive_files WHERE model_id = ?1 ORDER BY name, rowid")?;
    st.query_map([model_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect()
}

/// Where one image's original is: an archive member, or a file beside the
/// archive given by its full path.
pub fn image_source(conn: &Connection, image_id: i64) -> Result<Option<ImageSource>> {
    conn.query_row(
        &format!(
            "SELECT {}, l.root, i.filename FROM images i JOIN models m ON m.id = i.model_id \
             LEFT JOIN libraries l ON l.id = m.library_id WHERE i.id = ?1",
            archive_path_sql("m")
        ),
        [image_id],
        |r| Ok(ImageSource { archive: r.get(0)?, root: r.get(1)?, name: r.get(2)? }),
    )
    .optional()
}

/// Where an image's original is ([`image_source`]).
#[derive(Debug, Clone, PartialEq)]
pub struct ImageSource {
    /// The model's archive (absolute).
    pub archive: Option<String>,
    /// The root of the model's library folder.
    pub root: Option<String>,
    /// The image's name: a member of the archive, or the absolute path of an image
    /// beside it (one found in its folders by a crawl).
    pub name: Option<String>,
}

/// `(archive_path, warning)` of one model (Warning Details).
pub fn path_warning(conn: &Connection, model_id: i64) -> Result<Option<(Option<String>, Option<String>)>> {
    conn.query_row(&format!("SELECT {}, warning FROM models WHERE id = ?1", archive_path_sql("models")), [model_id], |r| {
        Ok((r.get(0)?, r.get(1)?))
    })
    .optional()
}

/// Every folder holding catalogued archives (absolute, `/` form), sorted.
pub fn archive_folders(conn: &Connection) -> Result<Vec<String>> {
    let mut st = conn.prepare(
        "SELECT DISTINCT posix_join(l.root, g.rel_folder) AS f FROM (SELECT DISTINCT library_id, rel_folder FROM models) g \
         JOIN libraries l ON l.id = g.library_id ORDER BY f",
    )?;
    let v: Vec<String> = st.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    Ok(v.into_iter().filter(|f| !f.is_empty()).collect())
}

/// `{model_id: [thumbnail_path, ...]}` in image id order (every id present).
fn thumbs(conn: &Connection, ids: &[i64]) -> Result<HashMap<i64, Vec<Option<String>>>> {
    let mut out: HashMap<i64, Vec<Option<String>>> = ids.iter().map(|&i| (i, Vec::new())).collect();
    if ids.is_empty() {
        return Ok(out);
    }
    let mut st = conn.prepare(
        "SELECT model_id, thumbnail_path FROM images \
         WHERE model_id IN (SELECT value FROM json_each(?1)) ORDER BY id",
    )?;
    let rows = st.query_map([id_json(ids)], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, Option<String>>(1)?)))?;
    for row in rows {
        let (mid, path) = row?;
        out.entry(mid).or_default().push(path);
    }
    Ok(out)
}

pub struct FolderModel {
    pub id: i64,
    pub title: Option<String>,
    pub creator: Option<String>,
    pub archive_path: Option<String>,
    pub thumbs: Vec<Option<String>>,
}

/// Archive browser cells for `folder` (and, with `include_subfolders`, every
/// folder under it, matched with `LIKE` as the Python code does), by path.
pub fn folder_models(conn: &Connection, folder: &str, include_subfolders: bool) -> Result<Vec<FolderModel>> {
    let cols = format!(
        "SELECT id, title, creator, {} AS path, {} AS folder FROM models",
        archive_path_sql("models"),
        super::schema_sql::archive_folder_sql("models")
    );
    let order = " ORDER BY path, id";
    let map = |r: &rusqlite::Row| {
        Ok(FolderModel { id: r.get(0)?, title: r.get(1)?, creator: r.get(2)?, archive_path: r.get(3)?, thumbs: Vec::new() })
    };
    let mut rows: Vec<FolderModel> = if include_subfolders {
        let mut prefix = folder.replace('\\', "/");
        if !prefix.ends_with('/') {
            prefix.push('/');
        }
        prefix.push('%');
        let mut st = conn.prepare(&format!("SELECT * FROM ({cols}) WHERE folder = ?1 OR folder LIKE ?2{order}"))?;
        st.query_map([folder, prefix.as_str()], map)?.collect::<Result<_>>()?
    } else {
        let mut st = conn.prepare(&format!("SELECT * FROM ({cols}) WHERE folder = ?1{order}"))?;
        st.query_map([folder], map)?.collect::<Result<_>>()?
    };
    let mut t = thumbs(conn, &rows.iter().map(|r| r.id).collect::<Vec<_>>())?;
    for r in &mut rows {
        r.thumbs = t.remove(&r.id).unwrap_or_default();
    }
    Ok(rows)
}

/// The image grid's fields of one model.
pub struct GridModel {
    pub id: i64,
    /// `(field, value)` in `model_views._GRID_FIELDS` order.
    pub fields: Vec<(&'static str, Value)>,
    pub tags: Vec<String>,
    pub scales: Vec<String>,
    pub thumbs: Vec<Option<String>>,
}

const GRID_FIELDS: [&str; 10] = [
    "title",
    "creator",
    "source",
    "release_year",
    "release_month",
    "archive_path",
    "presupports",
    "warning",
    "description",
    "url",
];

fn names_by_model(conn: &Connection, sql: &str, ids: &[i64]) -> Result<HashMap<i64, Vec<String>>> {
    let mut out: HashMap<i64, Vec<String>> = HashMap::new();
    let mut st = conn.prepare(sql)?;
    let rows = st.query_map([id_json(ids)], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?;
    for row in rows {
        let (mid, name) = row?;
        out.entry(mid).or_default().push(name);
    }
    Ok(out)
}

fn tags_of(conn: &Connection, ids: &[i64]) -> Result<HashMap<i64, Vec<String>>> {
    names_by_model(
        conn,
        "SELECT k.model_id, t.name FROM model_tags k JOIN tags t ON t.id = k.tag_id \
         WHERE k.model_id IN (SELECT value FROM json_each(?1)) ORDER BY t.id",
        ids,
    )
}

fn scales_of(conn: &Connection, ids: &[i64]) -> Result<HashMap<i64, Vec<String>>> {
    names_by_model(
        conn,
        "SELECT k.model_id, s.name FROM model_scales k JOIN scales s ON s.id = k.scale_id \
         WHERE k.model_id IN (SELECT value FROM json_each(?1)) ORDER BY s.id",
        ids,
    )
}

pub fn grid_models(conn: &Connection, ids: &[i64]) -> Result<Vec<GridModel>> {
    let cols: Vec<String> =
        GRID_FIELDS.iter().map(|f| if *f == "archive_path" { archive_path_sql("models") } else { f.to_string() }).collect();
    let mut st = conn.prepare(&format!(
        "SELECT id, {} FROM models WHERE id IN (SELECT value FROM json_each(?1)) ORDER BY id",
        cols.join(", ")
    ))?;
    let mut rows: Vec<GridModel> = st
        .query_map([id_json(ids)], |r| {
            let fields =
                GRID_FIELDS.iter().enumerate().map(|(k, f)| Ok((*f, r.get(k + 1)?))).collect::<Result<_>>()?;
            Ok(GridModel { id: r.get(0)?, fields, tags: Vec::new(), scales: Vec::new(), thumbs: Vec::new() })
        })?
        .collect::<Result<_>>()?;
    let found: Vec<i64> = rows.iter().map(|r| r.id).collect();
    let (mut t, mut s, mut th) = (tags_of(conn, &found)?, scales_of(conn, &found)?, thumbs(conn, &found)?);
    for r in &mut rows {
        r.tags = t.remove(&r.id).unwrap_or_default();
        r.scales = s.remove(&r.id).unwrap_or_default();
        r.thumbs = th.remove(&r.id).unwrap_or_default();
    }
    Ok(rows)
}

/// Find Duplicate Archives' metadata of one model.
pub struct DuplicateRow {
    pub id: i64,
    /// `title, creator, source, sculptor, collection, subcollection, category, release`.
    pub texts: [Option<String>; 8],
    pub scales: Vec<String>,
}

pub fn duplicate_rows(conn: &Connection) -> Result<Vec<DuplicateRow>> {
    let mut st = conn.prepare(
        "SELECT m.id, m.title, m.creator, m.source, scu.name, col.name, sub.name, cat.name, rel.name \
         FROM models m \
         LEFT JOIN sculptors scu ON m.sculptor_id = scu.id \
         LEFT JOIN collections col ON m.collection_id = col.id \
         LEFT JOIN subcollections sub ON m.subcollection_id = sub.id \
         LEFT JOIN categories cat ON m.category_id = cat.id \
         LEFT JOIN releases rel ON m.release_id = rel.id ORDER BY m.id",
    )?;
    let mut rows: Vec<DuplicateRow> = st
        .query_map([], |r| {
            Ok(DuplicateRow {
                id: r.get(0)?,
                texts: [r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?, r.get(7)?, r.get(8)?],
                scales: Vec::new(),
            })
        })?
        .collect::<Result<_>>()?;
    let mut scales: HashMap<i64, Vec<String>> = HashMap::new();
    let mut st = conn.prepare(
        "SELECT k.model_id, s.name FROM model_scales k JOIN scales s ON s.id = k.scale_id ORDER BY s.id",
    )?;
    for row in st.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))? {
        let (mid, name) = row?;
        scales.entry(mid).or_default().push(name);
    }
    for r in &mut rows {
        r.scales = scales.remove(&r.id).unwrap_or_default();
    }
    Ok(rows)
}

/// Image id, model id, perceptual hash.
pub type HashRow = (i64, i64, String);

/// Every image's hash, for comparing: what a search shows comes from
/// [`image_details`], for the few that match.
pub fn image_hashes(conn: &Connection) -> Result<Vec<HashRow>> {
    let mut st = conn.prepare("SELECT id, model_id, dhash FROM images WHERE dhash IS NOT NULL ORDER BY id")?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect()
}

/// Thumbnail, title and archive path, by image id.
pub type ImageDetail = (Option<String>, Option<String>, Option<String>);

pub fn image_details(conn: &Connection, ids: &[i64]) -> Result<HashMap<i64, ImageDetail>> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let mut st = conn.prepare(&format!(
        "SELECT i.id, i.thumbnail_path, m.title, {} FROM images i JOIN models m ON i.model_id = m.id \
         WHERE i.id IN (SELECT value FROM json_each(?1))",
        archive_path_sql("m")
    ))?;
    st.query_map([json], |r| Ok((r.get(0)?, (r.get(1)?, r.get(2)?, r.get(3)?))))?.collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, migrate, open};

    #[test]
    fn viewer_reads() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let conn = open(&path).unwrap();
        conn.execute_batch(
            "INSERT INTO scales (name) VALUES ('75mm'), ('28mm');
             INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'F', 'F:/');
             INSERT INTO models (title, library_id, rel_path, rel_folder, archive_hash, is_missing, to_print)
             VALUES ('A', 1, 'a/x.7z', 'a', 'h1', 0, 0), (NULL, 1, 'a/b/y.7z', 'a/b', 'h2', 0, 0),
                    ('C', 1, 'ab/z.7z', 'ab', 'h3', 0, 0);
             INSERT INTO model_scales (model_id, scale_id) VALUES (1, 2), (1, 1);
             INSERT INTO images (model_id, thumbnail_path, dhash) VALUES (1, '5', '2:ab'), (1, NULL, NULL);
             INSERT INTO stl_files (model_id, filename) VALUES (1, 'b.stl'), (1, 'a.stl');",
        )
        .unwrap();
        assert_eq!(archive_folders(&conn).unwrap(), ["F:/a", "F:/a/b", "F:/ab"]);
        let sub: Vec<i64> = folder_models(&conn, "F:/a", true).unwrap().iter().map(|m| m.id).collect();
        assert_eq!(sub, [2, 1]);
        assert_eq!(folder_models(&conn, "F:/a", false).unwrap()[0].thumbs, [Some("5".into()), None]);
        let grid = grid_models(&conn, &[1, 9]).unwrap();
        assert_eq!(grid.len(), 1);
        assert_eq!(grid[0].scales, ["75mm", "28mm"]);
        assert_eq!(stl_files(&conn, 1).unwrap()[0].0, "a.stl");
        assert_eq!(duplicate_rows(&conn).unwrap()[0].scales, ["75mm", "28mm"]);
        let hashes = image_hashes(&conn).unwrap();
        assert_eq!(hashes, [(1, 1, "2:ab".to_string())]);
        let details = image_details(&conn, &[1, 9]).unwrap();
        assert_eq!(details.len(), 1);
        let (thumb, title, path) = &details[&1];
        assert_eq!((thumb.as_deref(), title.as_deref()), (Some("5"), Some("A")));
        assert!(path.as_deref().is_some_and(|p| p.ends_with("x.7z")));
    }

    /// Each of these is the start of a longer index, which serves the same searches.
    #[test]
    fn redundant_indexes_go() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let conn = open(&path).unwrap();
        conn.execute_batch("CREATE INDEX ix_models_creator ON models (creator)").unwrap();
        drop(conn);
        migrate(&path, &Vectors::default()).unwrap();
        let conn = open(&path).unwrap();
        for name in ["ix_models_release_year", "ix_models_creator", "ix_models_sculptor_id", "ix_models_collection_id"] {
            let found: i64 = conn.query_row("SELECT count(*) FROM sqlite_master WHERE name = ?1", [name], |r| r.get(0)).unwrap();
            assert_eq!(found, 0, "{name}");
        }
        for (column, index) in [
            ("release_year", "ix_models_release_date"),
            ("creator", "ix_models_creator_source"),
            ("sculptor_id", "ix_models_sculptor_collection"),
            ("collection_id", "ix_models_collection_subcollection"),
        ] {
            let sql = format!("EXPLAIN QUERY PLAN SELECT id FROM models WHERE {column} = 1");
            let plan: String = conn.query_row(&sql, [], |r| r.get(3)).unwrap();
            assert!(plan.contains(index), "{plan}");
        }
    }
}
