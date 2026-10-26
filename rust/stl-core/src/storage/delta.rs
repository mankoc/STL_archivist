//! Smart sync: a library database as block fingerprints, and the delta that
//! turns one database into another (only the blocks that differ).
//!
//! Every ordinary table's rows are grouped by rowid into blocks of [`BLOCK`];
//! a block's fingerprint hashes its rows (rowid and every value). The
//! receiving side sends its fingerprints ([`State`]); the sending side packs
//! the rows of each block that differs, or that only one side has
//! ([`write_delta`]); the receiving side replaces those blocks in a copy of
//! its database ([`apply_delta`]) and checks that the result fingerprints as
//! the sender's did. After a sync both sides hold the same rows under the same
//! rowids (folder roots aside), so a later delta is small.
//!
//! Virtual tables (`image_vec`) are left out: their shadow tables are ordinary
//! tables and travel like the others.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use rusqlite::types::ValueRef;
use rusqlite::{Connection, params};
use xxhash_rust::xxh3::Xxh3;

/// Rows per block (by rowid).
pub const BLOCK: i64 = 128;
const MAGIC: &[u8; 6] = b"STLDB1";

/// A database's fingerprints: the schema's, and each table's blocks.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct State {
    pub schema: u64,
    /// Table → block number (`rowid / BLOCK`) → fingerprint.
    pub tables: BTreeMap<String, BTreeMap<i64, u64>>,
}

#[derive(Debug)]
pub enum DeltaError {
    Db(rusqlite::Error),
    /// The result is not the sender's database (the receiving side changed
    /// since it sent its fingerprints): sync the whole library instead.
    Diverged(String),
    Invalid(String),
}

impl std::fmt::Display for DeltaError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DeltaError::Db(e) => write!(f, "database: {e}"),
            DeltaError::Diverged(t) => write!(f, "the changes do not fit this library (table {t})"),
            DeltaError::Invalid(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for DeltaError {}

impl From<rusqlite::Error> for DeltaError {
    fn from(e: rusqlite::Error) -> Self {
        DeltaError::Db(e)
    }
}

pub type Result<T> = std::result::Result<T, DeltaError>;

fn quote(name: &str) -> String {
    format!("\"{}\"", name.replace('"', "\"\""))
}

/// The tables a delta covers (`(name, sql)`), by name: ordinary tables, not
/// SQLite's own nor virtual ones.
fn tables(conn: &Connection, schema: &str) -> Result<Vec<(String, String)>> {
    let mut st = conn.prepare(&format!(
        "SELECT name, sql FROM {schema}.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' \
         AND sql NOT LIKE 'CREATE VIRTUAL TABLE%' ORDER BY name"
    ))?;
    Ok(st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<rusqlite::Result<_>>()?)
}

fn feed(h: &mut Xxh3, v: ValueRef) {
    match v {
        ValueRef::Null => h.update(&[0]),
        ValueRef::Integer(i) => {
            h.update(&[1]);
            h.update(&i.to_le_bytes());
        }
        ValueRef::Real(f) => {
            h.update(&[2]);
            h.update(&f.to_bits().to_le_bytes());
        }
        ValueRef::Text(t) => {
            h.update(&[3]);
            h.update(&(t.len() as u64).to_le_bytes());
            h.update(t);
        }
        ValueRef::Blob(b) => {
            h.update(&[4]);
            h.update(&(b.len() as u64).to_le_bytes());
            h.update(b);
        }
    }
}

/// The fingerprints of `conn`'s main database (call it inside a transaction
/// for a consistent picture). Tables without rowids cannot be fingerprinted.
pub fn state(conn: &Connection) -> Result<State> {
    let mut out = State::default();
    let mut schema = Xxh3::new();
    for (name, sql) in tables(conn, "main")? {
        schema.update(name.as_bytes());
        schema.update(&[0]);
        schema.update(sql.as_bytes());
        schema.update(&[0]);
        let mut st = conn
            .prepare(&format!("SELECT rowid, * FROM main.{} ORDER BY rowid", quote(&name)))
            .map_err(|_| DeltaError::Invalid(format!("table {name} has no rowids")))?;
        let n = st.column_count();
        let mut blocks = BTreeMap::new();
        let mut current: Option<(i64, Xxh3)> = None;
        let mut rows = st.query([])?;
        while let Some(row) = rows.next()? {
            let rowid: i64 = row.get(0)?;
            let block = rowid.div_euclid(BLOCK);
            if current.as_ref().is_none_or(|(b, _)| *b != block) {
                if let Some((b, h)) = current.take() {
                    blocks.insert(b, h.digest());
                }
                current = Some((block, Xxh3::new()));
            }
            let h = &mut current.as_mut().expect("set above").1;
            h.update(&rowid.to_le_bytes());
            for i in 1..n {
                feed(h, row.get_ref(i)?);
            }
        }
        if let Some((b, h)) = current {
            blocks.insert(b, h.digest());
        }
        out.tables.insert(name, blocks);
    }
    out.schema = schema.digest();
    Ok(out)
}

/// Fingerprints on the wire.
pub fn encode(s: &State) -> Vec<u8> {
    let mut out = MAGIC.to_vec();
    out.extend_from_slice(&s.schema.to_le_bytes());
    out.extend_from_slice(&(s.tables.len() as u32).to_le_bytes());
    for (name, blocks) in &s.tables {
        out.extend_from_slice(&(name.len() as u32).to_le_bytes());
        out.extend_from_slice(name.as_bytes());
        out.extend_from_slice(&(blocks.len() as u64).to_le_bytes());
        for (b, h) in blocks {
            out.extend_from_slice(&b.to_le_bytes());
            out.extend_from_slice(&h.to_le_bytes());
        }
    }
    out
}

pub fn decode(bytes: &[u8]) -> std::result::Result<State, String> {
    let bad = || "not a list of library fingerprints".to_string();
    let mut at = 0usize;
    let mut take = |n: usize| -> std::result::Result<&[u8], String> {
        let s = bytes.get(at..at + n).ok_or_else(bad)?;
        at += n;
        Ok(s)
    };
    if take(MAGIC.len())? != MAGIC {
        return Err(bad());
    }
    let u64_of = |s: &[u8]| u64::from_le_bytes(s.try_into().expect("8 bytes"));
    let schema = u64_of(take(8)?);
    let count = u32::from_le_bytes(take(4)?.try_into().expect("4 bytes"));
    let mut tables = BTreeMap::new();
    for _ in 0..count {
        let len = u32::from_le_bytes(take(4)?.try_into().expect("4 bytes")) as usize;
        let name = String::from_utf8(take(len)?.to_vec()).map_err(|_| bad())?;
        let n = u64_of(take(8)?);
        let mut blocks = BTreeMap::new();
        for _ in 0..n {
            let b = u64_of(take(8)?) as i64;
            blocks.insert(b, u64_of(take(8)?));
        }
        tables.insert(name, blocks);
    }
    Ok(State { schema, tables })
}

/// The blocks that differ between `mine` and `theirs`, by table.
fn differing(mine: &State, theirs: &State) -> BTreeMap<String, BTreeSet<i64>> {
    let empty = BTreeMap::new();
    let names: BTreeSet<&String> = mine.tables.keys().chain(theirs.tables.keys()).collect();
    let mut out = BTreeMap::new();
    for name in names {
        let (a, b) = (mine.tables.get(name).unwrap_or(&empty), theirs.tables.get(name).unwrap_or(&empty));
        let diff: BTreeSet<i64> = a.keys().chain(b.keys()).filter(|k| a.get(k) != b.get(k)).copied().collect();
        if !diff.is_empty() {
            out.insert(name.clone(), diff);
        }
    }
    out
}

/// What a delta holds.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct Built {
    /// Rows in it (the rows of the blocks that differ).
    pub rows: u64,
    /// Rows in the sending database.
    pub total: u64,
    /// Blocks it replaces.
    pub blocks: u64,
}

/// Write to `dest` (a new SQLite file) the delta from `theirs` to `conn`'s
/// main database: the rows of every block that differs, the blocks to
/// replace, and `conn`'s fingerprints to check the result against. `None`
/// when the schemas differ (a delta cannot bridge them). Call it inside a
/// transaction for a consistent picture.
pub fn write_delta(conn: &Connection, theirs: &State, dest: &Path) -> Result<Option<Built>> {
    let mine = state(conn)?;
    if mine.schema != theirs.schema {
        return Ok(None);
    }
    let diff = differing(&mine, theirs);
    let out = Connection::open(dest)?;
    out.execute_batch(
        "PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF; BEGIN;
         CREATE TABLE _blocks (t TEXT NOT NULL, b INTEGER NOT NULL);
         CREATE TABLE _target (t TEXT NOT NULL, b INTEGER NOT NULL, h INTEGER NOT NULL);
         CREATE TABLE _meta (schema INTEGER NOT NULL);",
    )?;
    out.execute("INSERT INTO _meta (schema) VALUES (?1)", [mine.schema as i64])?;
    {
        let mut target = out.prepare("INSERT INTO _target (t, b, h) VALUES (?1, ?2, ?3)")?;
        for (name, blocks) in &mine.tables {
            for (b, h) in blocks {
                target.execute(params![name, b, *h as i64])?;
            }
        }
    }
    let mut built = Built::default();
    for (name, _) in tables(conn, "main")? {
        built.total += conn.query_row(&format!("SELECT count(*) FROM main.{}", quote(&name)), [], |r| r.get::<_, i64>(0))? as u64;
    }
    for (name, blocks) in &diff {
        let mut mark = out.prepare("INSERT INTO _blocks (t, b) VALUES (?1, ?2)")?;
        for b in blocks {
            mark.execute(params![name, b])?;
        }
        built.blocks += blocks.len() as u64;
        if !mine.tables.contains_key(name) {
            continue; // a table only they have: its blocks empty, nothing to send
        }
        let cols = columns(conn, "main", name)?;
        let typed: Vec<String> = cols.iter().map(|c| quote(c)).collect();
        out.execute_batch(&format!("CREATE TABLE {} (\"__rid\" INTEGER PRIMARY KEY, {})", quote(name), typed.join(", ")))?;
        let mut insert = out.prepare(&format!(
            "INSERT INTO {} (\"__rid\", {}) VALUES ({})",
            quote(name),
            typed.join(", "),
            (0..=cols.len()).map(|i| format!("?{}", i + 1)).collect::<Vec<_>>().join(", ")
        ))?;
        let mut read = conn.prepare(&format!("SELECT rowid, {} FROM main.{} WHERE rowid BETWEEN ?1 AND ?2 ORDER BY rowid", typed.join(", "), quote(name)))?;
        for b in blocks {
            let mut rows = read.query(params![b * BLOCK, b * BLOCK + BLOCK - 1])?;
            while let Some(row) = rows.next()? {
                let values: Vec<rusqlite::types::Value> = (0..=cols.len()).map(|i| row.get(i)).collect::<rusqlite::Result<_>>()?;
                insert.execute(rusqlite::params_from_iter(values))?;
                built.rows += 1;
            }
        }
    }
    out.execute_batch("COMMIT")?;
    Ok(Some(built))
}

fn columns(conn: &Connection, schema: &str, table: &str) -> Result<Vec<String>> {
    let mut st = conn.prepare(&format!("SELECT name FROM {schema}.pragma_table_info(?1) ORDER BY cid"))?;
    Ok(st.query_map([table], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?)
}

/// The column that is the rowid (`INTEGER PRIMARY KEY`), if any.
fn rowid_alias(conn: &Connection, table: &str) -> Result<Option<String>> {
    let mut st = conn.prepare("SELECT name, type, pk FROM main.pragma_table_info(?1)")?;
    let pk: Vec<(String, String)> =
        st.query_map([table], |r| Ok((r.get(0)?, r.get(1)?, r.get::<_, i64>(2)?)))?.filter_map(|r| r.ok()).filter(|(_, _, pk)| *pk > 0).map(|(n, t, _)| (n, t)).collect();
    Ok(match pk.as_slice() {
        [(name, ty)] if ty.eq_ignore_ascii_case("INTEGER") => Some(name.clone()),
        _ => None,
    })
}

/// Apply the delta `delta` to the database file `base` (a copy: nothing else
/// may have it open), then check that it now fingerprints as the sender's.
/// Triggers and foreign keys do not act meanwhile: whole blocks are replaced
/// as they are.
pub fn apply_delta(base: &Path, delta: &Path) -> Result<Built> {
    let mut conn = Connection::open(base)?;
    conn.execute_batch("PRAGMA foreign_keys = OFF")?;
    conn.execute("ATTACH DATABASE ?1 AS d", [delta.to_string_lossy()])?;
    let has_meta: i64 = conn.query_row("SELECT count(*) FROM d.sqlite_master WHERE name IN ('_blocks', '_target', '_meta')", [], |r| r.get(0))?;
    if has_meta != 3 {
        return Err(DeltaError::Invalid("not a library delta".into()));
    }
    let mut target: BTreeMap<String, BTreeMap<i64, u64>> = BTreeMap::new();
    {
        let mut st = conn.prepare("SELECT t, b, h FROM d._target")?;
        let mut rows = st.query([])?;
        while let Some(r) = rows.next()? {
            target.entry(r.get(0)?).or_default().insert(r.get(1)?, r.get::<_, i64>(2)? as u64);
        }
    }
    let mut blocks: BTreeMap<String, Vec<i64>> = BTreeMap::new();
    {
        let mut st = conn.prepare("SELECT t, b FROM d._blocks ORDER BY t, b")?;
        let mut rows = st.query([])?;
        while let Some(r) = rows.next()? {
            blocks.entry(r.get(0)?).or_default().push(r.get(1)?);
        }
    }
    let triggers: Vec<(String, String)> = conn
        .prepare("SELECT name, sql FROM main.sqlite_master WHERE type = 'trigger' AND sql IS NOT NULL")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<rusqlite::Result<_>>()?;
    let mut built = Built::default();
    let known: BTreeSet<String> = tables(&conn, "main")?.into_iter().map(|(n, _)| n).collect();
    let tx = conn.transaction()?;
    for (name, _) in &triggers {
        tx.execute_batch(&format!("DROP TRIGGER main.{}", quote(name)))?;
    }
    for (name, list) in &blocks {
        if !known.contains(name) {
            return Err(DeltaError::Diverged(name.clone()));
        }
        let mut del = tx.prepare(&format!("DELETE FROM main.{} WHERE rowid BETWEEN ?1 AND ?2", quote(name)))?;
        for b in list {
            del.execute(params![b * BLOCK, b * BLOCK + BLOCK - 1])?;
        }
        built.blocks += list.len() as u64;
        let sent: i64 = tx.query_row("SELECT count(*) FROM d.sqlite_master WHERE type = 'table' AND name = ?1", [name], |r| r.get(0))?;
        if sent == 0 {
            continue;
        }
        let cols: Vec<String> = columns(&tx, "d", name)?.into_iter().filter(|c| c != "__rid").collect();
        let typed: Vec<String> = cols.iter().map(|c| quote(c)).collect();
        let (into, from) = match rowid_alias(&tx, name)? {
            Some(_) => (typed.join(", "), typed.join(", ")),
            None => (format!("rowid, {}", typed.join(", ")), format!("\"__rid\", {}", typed.join(", "))),
        };
        built.rows += tx.execute(&format!("INSERT INTO main.{} ({into}) SELECT {from} FROM d.{} ORDER BY \"__rid\"", quote(name), quote(name)), [])? as u64;
    }
    for (_, sql) in &triggers {
        tx.execute_batch(sql)?;
    }
    tx.commit()?;
    conn.execute_batch("DETACH DATABASE d")?;
    let now = state(&conn)?;
    for (name, blocks) in target.iter().filter(|(_, b)| !b.is_empty()) {
        if now.tables.get(name) != Some(blocks) {
            return Err(DeltaError::Diverged(name.clone()));
        }
    }
    if let Some((name, _)) = now.tables.iter().find(|(n, b)| !b.is_empty() && !target.contains_key(*n)) {
        return Err(DeltaError::Diverged(name.clone()));
    }
    Ok(built)
}

#[cfg(test)]
mod tests {
    use super::*;

    const SCHEMA: &str = "
        CREATE TABLE models (id INTEGER PRIMARY KEY, title TEXT UNIQUE, size REAL, data BLOB);
        CREATE TABLE images (id INTEGER NOT NULL, model_id INTEGER NOT NULL REFERENCES models (id) ON DELETE CASCADE,
                             filename TEXT, PRIMARY KEY (id));
        CREATE TABLE model_tags (model_id INTEGER NOT NULL, tag TEXT NOT NULL);
        CREATE TABLE log (n INTEGER);
        CREATE TRIGGER count_tags AFTER INSERT ON model_tags BEGIN INSERT INTO log (n) VALUES (new.model_id); END;";

    fn db(dir: &Path, name: &str) -> (std::path::PathBuf, Connection) {
        let path = dir.join(name);
        let c = Connection::open(&path).unwrap();
        c.execute_batch(&format!("PRAGMA foreign_keys = ON; {SCHEMA}")).unwrap();
        (path, c)
    }

    fn fill(c: &Connection, models: i64) {
        let tx = c.unchecked_transaction().unwrap();
        for i in 1..=models {
            tx.execute("INSERT INTO models (id, title, size, data) VALUES (?1, ?2, ?3, ?4)", params![i, format!("Model {i}"), i as f64 / 2.0, vec![i as u8; 3]]).unwrap();
            tx.execute("INSERT INTO images (id, model_id, filename) VALUES (?1, ?1, ?2)", params![i, format!("{i}.jpg")]).unwrap();
            tx.execute("INSERT INTO model_tags (model_id, tag) VALUES (?1, 'dragon')", [i]).unwrap();
        }
        tx.commit().unwrap();
    }

    fn rows(c: &Connection) -> Vec<String> {
        let mut out = Vec::new();
        for t in ["models", "images", "model_tags", "log"] {
            let mut st = c.prepare(&format!("SELECT rowid, * FROM {t} ORDER BY rowid")).unwrap();
            let n = st.column_count();
            let mut q = st.query([]).unwrap();
            while let Some(r) = q.next().unwrap() {
                out.push(format!("{t}:{:?}", (0..n).map(|i| format!("{:?}", r.get_ref(i).unwrap())).collect::<Vec<_>>()));
            }
        }
        out
    }

    #[test]
    fn a_delta_turns_one_database_into_the_other() {
        let dir = tempfile::tempdir().unwrap();
        let (_, a) = db(dir.path(), "a.db");
        let (b_path, b) = db(dir.path(), "b.db");
        fill(&a, 1000);
        fill(&b, 1000);
        assert_eq!(state(&a).unwrap(), state(&b).unwrap(), "same rows, same fingerprints");
        drop(b);

        // The sender changes: an edit, two titles swapped (unique), a model
        // deleted with its image (cascade), new rows at the end.
        a.execute("UPDATE models SET title = 'Dragon' WHERE id = 5", []).unwrap();
        a.execute_batch(
            "UPDATE models SET title = 'tmp' WHERE id = 700; UPDATE models SET title = 'Model 700' WHERE id = 900;
             UPDATE models SET title = 'Model 900' WHERE id = 700;",
        )
        .unwrap();
        a.execute("DELETE FROM models WHERE id = 300", []).unwrap();
        fill_from(&a, 1001, 1010);
        let theirs = state(&Connection::open(&b_path).unwrap()).unwrap();
        let delta = dir.path().join("delta.db");
        let built = write_delta(&a, &theirs, &delta).unwrap().unwrap();
        assert!(built.rows < built.total / 3, "{built:?}");

        let applied = apply_delta(&b_path, &delta).unwrap();
        assert_eq!(applied.blocks, built.blocks);
        let b = Connection::open(&b_path).unwrap();
        assert_eq!(rows(&b), rows(&a), "every row as the sender has it");
        assert_eq!(state(&b).unwrap(), state(&a).unwrap());
        let trigger: i64 = b.query_row("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'", [], |r| r.get(0)).unwrap();
        assert_eq!(trigger, 1, "the trigger is back");
    }

    fn fill_from(c: &Connection, from: i64, to: i64) {
        for i in from..=to {
            c.execute("INSERT INTO models (id, title) VALUES (?1, ?2)", params![i, format!("Model {i}")]).unwrap();
            c.execute("INSERT INTO model_tags (model_id, tag) VALUES (?1, 'new')", [i]).unwrap();
        }
    }

    #[test]
    fn a_changed_receiver_or_schema_is_caught() {
        let dir = tempfile::tempdir().unwrap();
        let (_, a) = db(dir.path(), "a.db");
        let (b_path, b) = db(dir.path(), "b.db");
        fill(&a, 300);
        fill(&b, 300);
        a.execute("UPDATE models SET title = 'Sent' WHERE id = 1", []).unwrap();
        let theirs = state(&b).unwrap();
        // The receiver changes after sending its fingerprints, in a block the delta does not touch.
        b.execute("UPDATE models SET title = 'Late edit' WHERE id = 250", []).unwrap();
        drop(b);
        let delta = dir.path().join("delta.db");
        write_delta(&a, &theirs, &delta).unwrap().unwrap();
        assert!(matches!(apply_delta(&b_path, &delta), Err(DeltaError::Diverged(t)) if t == "models"));

        let (_, c) = db(dir.path(), "c.db");
        c.execute_batch("CREATE TABLE extra (x)").unwrap();
        assert!(write_delta(&a, &state(&c).unwrap(), &dir.path().join("d2.db")).unwrap().is_none(), "schemas differ");
    }

    #[test]
    fn fingerprints_travel() {
        let dir = tempfile::tempdir().unwrap();
        let (_, a) = db(dir.path(), "a.db");
        fill(&a, 500);
        let s = state(&a).unwrap();
        assert_eq!(decode(&encode(&s)).unwrap(), s);
        assert!(decode(b"STLDB1 short").is_err());
        assert!(decode(b"nothing").is_err());
    }
}
