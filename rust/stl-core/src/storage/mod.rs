//! The SQLite library database: connections and schema migrations.
//!
//! The schema is owned here (compatibility rule 4): `db.init_db` calls
//! [`migrate`], which brings any older library up to date in this order:
//!
//! 1. renames from the old schema (`categories` → `collections`, ...);
//! 2. `images.dhash` rebuilt with TEXT affinity on databases that declared it INTEGER;
//! 3. missing tables created from [`schema_sql`] (the source of truth since Phase 4h);
//! 4. the sqlite-vec table `image_vec` (and moving legacy `images.embedding` blobs);
//! 5. missing columns added, extra indexes created, `ANALYZE`;
//! 6. the legacy single `models.scale_id` copied into `model_scales`;
//! 7. `models_view` recreated.
//!
//! The DDL text is the one SQLAlchemy used to emit, so libraries created by
//! older versions and by this code hold the same `sqlite_master` SQL.

pub mod admin;
pub mod browser;
pub mod crawl;
pub mod duplicates;
pub mod edit;
pub mod jobs;
pub mod library;
pub mod maintenance;
pub mod model_edit;
pub mod schema_sql;
pub mod sidecar;
pub mod stats;
pub mod vectors;
pub mod views;

use std::collections::HashSet;
use std::path::Path;
use std::sync::Once;

use rusqlite::{Connection, OptionalExtension, Transaction, params};

pub type Result<T> = rusqlite::Result<T>;

/// Register sqlite-vec for every connection this process opens (once).
fn register_sqlite_vec() {
    static ONCE: Once = Once::new();
    ONCE.call_once(|| {
        // SAFETY: sqlite3_vec_init has the extension entry-point signature that
        // sqlite3_auto_extension expects; the transmute only erases its arguments.
        unsafe {
            #[allow(clippy::missing_transmute_annotations)]
            rusqlite::ffi::sqlite3_auto_extension(Some(std::mem::transmute(
                sqlite_vec::sqlite3_vec_init as *const (),
            )));
        }
    });
}

/// Open the database with the pragmas `db.init_db` sets on every connection.
pub fn open(path: &Path) -> Result<Connection> {
    register_sqlite_vec();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let conn = Connection::open(path)?;
    conn.pragma_update(None, "journal_mode", "WAL")?;
    conn.execute_batch(
        "PRAGMA foreign_keys=ON;
         PRAGMA synchronous=NORMAL;
         PRAGMA cache_size=-65536;
         PRAGMA temp_store=MEMORY;
         PRAGMA mmap_size=2147483648;
         PRAGMA busy_timeout=5000;
         PRAGMA wal_autocheckpoint=1000;",
    )?;
    add_vector_delete_trigger(&conn)?;
    Ok(conn)
}

/// The TEMP trigger `vectors.on_connect` adds: an image delete removes its
/// `image_vec` row (a virtual table has no foreign keys). Skipped until both
/// tables exist; deliberately not persistent (see `vectors.py`).
pub fn add_vector_delete_trigger(conn: &Connection) -> Result<()> {
    let n: i64 = conn.query_row(
        "SELECT count(*) FROM sqlite_master WHERE name IN ('images', ?1)",
        [schema_sql::VECTOR_TABLE],
        |r| r.get(0),
    )?;
    if n == 2 {
        conn.execute_batch(schema_sql::VECTOR_DELETE_TRIGGER)?;
    }
    Ok(())
}

/// What [`migrate`] changed (for the log).
#[derive(Debug, Default, Clone)]
pub struct MigrateReport {
    pub renamed: Vec<String>,
    pub tables_created: Vec<String>,
    pub columns_added: Vec<String>,
    /// `Some(n)` when `images.dhash` was rebuilt; n damaged values cleared.
    pub dhash_rebuilt: Option<i64>,
    /// `Some(n)` when legacy `images.embedding` blobs were moved into `image_vec`.
    pub embeddings_moved: Option<usize>,
    /// The embedding model changed, so the stored vectors were dropped.
    pub vectors_reset: bool,
    pub scales_copied: Option<usize>,
}

fn tables(conn: &Connection) -> Result<HashSet<String>> {
    let mut st = conn.prepare("SELECT name FROM sqlite_master WHERE type='table'")?;
    st.query_map([], |r| r.get(0))?.collect()
}

/// `(name, upper-cased declared type)` of each column.
fn columns(conn: &Connection, table: &str) -> Result<Vec<(String, String)>> {
    let mut st = conn.prepare(&format!("PRAGMA table_info(\"{table}\")"))?;
    st.query_map([], |r| Ok((r.get::<_, String>(1)?, r.get::<_, Option<String>>(2)?.unwrap_or_default().to_uppercase())))?
        .collect()
}

fn column_names(conn: &Connection, table: &str) -> Result<HashSet<String>> {
    Ok(columns(conn, table)?.into_iter().map(|(n, _)| n).collect())
}

/// The embedding store [`migrate`] prepares (`vectors.ensure_schema`).
#[derive(Debug, Clone)]
pub struct Vectors {
    /// Model the stored vectors must come from; others are discarded.
    pub model_id: String,
    pub dim: i64,
    /// `false` skips the step entirely, as Python does when it cannot load
    /// sqlite-vec: legacy `images.embedding` data then stays where it is.
    pub enabled: bool,
}

impl Default for Vectors {
    fn default() -> Self {
        Vectors { model_id: schema_sql::EMBED_MODEL_ID.into(), dim: schema_sql::EMBED_DIM, enabled: true }
    }
}

/// Bring the database at `path` to the current schema; see the module docs.
pub fn migrate(path: &Path, vectors: &Vectors) -> Result<MigrateReport> {
    let mut conn = open(path)?;
    let mut report = MigrateReport::default();

    let tx = conn.transaction()?;
    rename_old_schema(&tx, &mut report)?;
    tx.commit()?;

    let tx = conn.transaction()?;
    report.dhash_rebuilt = fix_dhash_affinity(&tx)?;
    tx.commit()?;

    let tx = conn.transaction()?;
    create_missing_tables(&tx, &mut report)?;
    tx.commit()?;

    if vectors.enabled {
        let tx = conn.transaction()?;
        ensure_vector_schema(&tx, vectors, &mut report)?;
        tx.commit()?;
    }
    if report.embeddings_moved.is_some() {
        conn.execute_batch("VACUUM")?; // give the dropped blob columns' space back
    }

    let tx = conn.transaction()?;
    add_missing_columns(&tx, &mut report)?;
    tx.commit()?;

    let tx = conn.transaction()?;
    for ddl in schema_sql::ENSURE_INDEXES {
        tx.execute_batch(ddl)?;
    }
    let _ = tx.execute_batch("ANALYZE"); // as Python: a failed ANALYZE is ignored
    // The bundled SQLite is built with STAT4 and Python's is not; drop the extra
    // statistics table so the file is the same whichever side migrated.
    tx.execute_batch("DROP TABLE IF EXISTS sqlite_stat4")?;
    tx.commit()?;

    let tx = conn.transaction()?;
    if column_names(&tx, "models")?.contains("scale_id") {
        report.scales_copied = Some(tx.execute(
            "INSERT OR IGNORE INTO model_scales (model_id, scale_id) \
             SELECT id, scale_id FROM models WHERE scale_id IS NOT NULL",
            [],
        )?);
    }
    tx.commit()?;

    let tx = conn.transaction()?;
    tx.execute_batch("DROP VIEW IF EXISTS models_view")?;
    tx.execute_batch(&strip_cr(schema_sql::MODELS_VIEW))?;
    tx.commit()?;

    conn.close().map_err(|(_, e)| e)?;
    Ok(report)
}

/// The generated DDL must reach SQLite with `\n` line ends, as Python sends it.
fn strip_cr(sql: &str) -> String {
    sql.replace('\r', "")
}

/// `_migrate_schema`: one-time renames, each applied only if still needed.
fn rename_old_schema(tx: &Transaction, report: &mut MigrateReport) -> Result<()> {
    let mut t = tables(tx)?;
    let run = |sql: &str, report: &mut MigrateReport| -> Result<()> {
        tx.execute_batch(sql)?;
        report.renamed.push(sql.to_string());
        Ok(())
    };
    if t.contains("categories") && !t.contains("collections") {
        run("ALTER TABLE categories RENAME TO collections", report)?;
        t.remove("categories");
        t.insert("collections".into());
    }
    if t.contains("subcategories") && !t.contains("subcollections") {
        run("ALTER TABLE subcategories RENAME TO subcollections", report)?;
        t.remove("subcategories");
        t.insert("subcollections".into());
    }
    if t.contains("subcollections") {
        let cols = column_names(tx, "subcollections")?;
        if cols.contains("category_id") && !cols.contains("collection_id") {
            run("ALTER TABLE subcollections RENAME COLUMN category_id TO collection_id", report)?;
        }
    }
    if t.contains("models") {
        for (old, new) in [("author", "creator"), ("category_id", "collection_id"), ("subcategory_id", "subcollection_id")] {
            let cols = column_names(tx, "models")?;
            if cols.contains(old) && !cols.contains(new) {
                run(&format!("ALTER TABLE models RENAME COLUMN {old} TO {new}"), report)?;
            }
        }
        tx.execute(
            "UPDATE models SET warning = REPLACE(warning, 'Archive inside archive', 'Nested archives') \
             WHERE warning LIKE '%Archive inside archive%'",
            [],
        )?;
    }
    Ok(())
}

/// `_fix_dhash_affinity`: `Some(damaged values cleared)` when the column was rebuilt.
fn fix_dhash_affinity(tx: &Transaction) -> Result<Option<i64>> {
    let decl = columns(tx, "images")?.into_iter().find(|(n, _)| n == "dhash").map(|(_, d)| d);
    let Some(decl) = decl else { return Ok(None) };
    if ["CHAR", "CLOB", "TEXT"].iter().any(|k| decl.contains(k)) {
        return Ok(None);
    }
    let damaged: i64 =
        tx.query_row("SELECT count(*) FROM images WHERE typeof(dhash) IN ('integer', 'real')", [], |r| r.get(0))?;
    tx.execute_batch(
        "DROP INDEX IF EXISTS ix_images_dhash;
         ALTER TABLE images RENAME COLUMN dhash TO dhash_old;
         ALTER TABLE images ADD COLUMN dhash VARCHAR(32);
         UPDATE images SET dhash = dhash_old WHERE typeof(dhash_old) = 'text';
         ALTER TABLE images DROP COLUMN dhash_old;",
    )?;
    Ok(Some(damaged))
}

/// `create_all`: each missing table with the indexes created alongside it.
fn create_missing_tables(tx: &Transaction, report: &mut MigrateReport) -> Result<()> {
    let existing = tables(tx)?;
    for t in schema_sql::TABLES {
        if existing.contains(t.name) {
            continue;
        }
        tx.execute_batch(&strip_cr(t.create))?;
        for ix in t.indexes {
            tx.execute_batch(&strip_cr(ix))?;
        }
        report.tables_created.push(t.name.to_string());
    }
    Ok(())
}

/// `vectors.ensure_schema`: the vec0 table for the current embedding model, and
/// the one-time move of legacy `images.embedding` blobs into it.
fn ensure_vector_schema(tx: &Transaction, v: &Vectors, report: &mut MigrateReport) -> Result<()> {
    use schema_sql::{VECTOR_INFO, VECTOR_TABLE};
    let (model, dim) = (v.model_id.as_str(), v.dim);
    tx.execute_batch(schema_sql::VECTOR_INFO_CREATE)?;
    let stored: Option<(String, i64)> = tx
        .query_row(&format!("SELECT model, dim FROM {VECTOR_INFO}"), [], |r| Ok((r.get(0)?, r.get(1)?)))
        .optional()?;
    let exists: bool = tx
        .query_row("SELECT 1 FROM sqlite_master WHERE name = ?1", [VECTOR_TABLE], |_| Ok(()))
        .optional()?
        .is_some();
    if exists && stored.as_ref().map(|(m, d)| (m.as_str(), *d)) != Some((model, dim)) {
        tx.execute_batch(&format!("DROP TABLE {VECTOR_TABLE}"))?;
        report.vectors_reset = true;
    }
    // `schema_sql::VECTOR_TABLE_CREATE` with this dimension.
    tx.execute_batch(&format!(
        "CREATE VIRTUAL TABLE IF NOT EXISTS {VECTOR_TABLE} USING vec0(image_id INTEGER PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)"
    ))?;
    tx.execute(&format!("DELETE FROM {VECTOR_INFO}"), [])?;
    tx.execute(&format!("INSERT INTO {VECTOR_INFO} VALUES (?1, ?2)"), params![model, dim])?;

    let cols = column_names(tx, "images")?;
    if cols.contains("embedding") {
        let has_model = cols.contains("embedding_model");
        let filter = if has_model { "AND embedding_model = ?2" } else { "" };
        let sql = format!(
            "INSERT INTO {VECTOR_TABLE}(image_id, embedding) SELECT id, embedding FROM images \
             WHERE embedding IS NOT NULL AND length(embedding) = ?1 {filter}"
        );
        let moved = if has_model {
            tx.execute(&sql, params![dim * 4, model])?
        } else {
            tx.execute(&sql, params![dim * 4])?
        };
        tx.execute_batch("ALTER TABLE images DROP COLUMN embedding")?;
        if has_model {
            tx.execute_batch("ALTER TABLE images DROP COLUMN embedding_model")?;
        }
        report.embeddings_moved = Some(moved);
    }
    Ok(())
}

/// `ALTER TABLE ... ADD COLUMN` for each column a table lacks.
fn add_missing_columns(tx: &Transaction, report: &mut MigrateReport) -> Result<()> {
    let existing_tables = tables(tx)?;
    let mut cache: std::collections::HashMap<&str, HashSet<String>> = std::collections::HashMap::new();
    for &(table, column, ddl) in schema_sql::ADD_COLUMNS {
        if !existing_tables.contains(table) {
            continue;
        }
        if !cache.contains_key(table) {
            cache.insert(table, column_names(tx, table)?);
        }
        if cache[table].contains(column) {
            continue;
        }
        tx.execute_batch(ddl)?;
        cache.get_mut(table).unwrap().insert(column.to_string());
        report.columns_added.push(format!("{table}.{column}"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fresh_database_gets_the_whole_schema() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        let report = migrate(&path, &Vectors::default()).unwrap();
        assert!(report.tables_created.contains(&"models".to_string()));
        let conn = open(&path).unwrap();
        let t = tables(&conn).unwrap();
        for name in ["models", "images", "image_vec", "vector_store_info", "model_tags", "filter_cache"] {
            assert!(t.contains(name), "{name}");
        }
        let v: String = conn.query_row("SELECT vec_version()", [], |r| r.get(0)).unwrap();
        assert_eq!(v, "v0.1.9");
        // A second run changes nothing.
        let again = migrate(&path, &Vectors::default()).unwrap();
        assert!(again.tables_created.is_empty() && again.columns_added.is_empty());
    }

    #[test]
    fn vector_ddl_matches_the_generated_one() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate(&path, &Vectors::default()).unwrap();
        let sql: String = open(&path)
            .unwrap()
            .query_row("SELECT sql FROM sqlite_master WHERE name = 'image_vec'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(sql, schema_sql::VECTOR_TABLE_CREATE.replace(" IF NOT EXISTS", ""));
    }

    #[test]
    fn integer_dhash_column_is_rebuilt() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        {
            let c = Connection::open(&path).unwrap();
            c.execute_batch(
                "CREATE TABLE images (id INTEGER PRIMARY KEY, model_id INTEGER NOT NULL, filename VARCHAR(512), dhash INTEGER);
                 INSERT INTO images VALUES (1, 1, 'a', 'abcd'), (2, 1, 'b', '0000123');",
            )
            .unwrap();
        }
        let report = migrate(&path, &Vectors::default()).unwrap();
        assert_eq!(report.dhash_rebuilt, Some(1));
        let c = open(&path).unwrap();
        let rows: Vec<Option<String>> =
            c.prepare("SELECT dhash FROM images ORDER BY id").unwrap().query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
        assert_eq!(rows, vec![Some("abcd".into()), None]);
    }
}
