//! Libraries: named, relocatable collections of archives. A library is a root
//! folder (where it is on this machine) plus a permanent `uuid`; each model
//! belongs to one library and stores its archive's [`RelPath`] below the root.
//! Moving a library is a change of its root; nothing else is rewritten.
//!
//! Libraries may sit inside one another: an archive belongs to the **deepest**
//! library above it ([`Roots::locate`]); creating an inner library adopts the
//! records below its root, removing it gives them back to the library around it.

use std::path::Path;

use rusqlite::{Connection, OptionalExtension, Result, Transaction, params};

use super::edit::utc_now_text;
use crate::relpath::{self, RelPath, path_key};
use crate::winpath::WinPath;

#[derive(Debug, Clone, PartialEq)]
pub struct Library {
    pub id: i64,
    pub uuid: String,
    pub name: String,
    pub root: String,
    pub pattern: Option<String>,
    pub monitored: bool,
    pub last_crawled_at: Option<String>,
    pub archive_count: Option<i64>,
    pub new_count: Option<i64>,
    pub updated_count: Option<i64>,
    /// `migration` for libraries the path migration made for archives outside
    /// every crawled folder; `None` otherwise.
    pub origin: Option<String>,
    /// Records in it.
    pub models: i64,
}

const COLUMNS: &str = "l.id, l.uuid, l.name, l.root, l.pattern, l.monitored, l.last_crawled_at, l.archive_count, \
     l.new_count, l.updated_count, l.origin, (SELECT count(*) FROM models m WHERE m.library_id = l.id)";

fn from_row(r: &rusqlite::Row) -> Result<Library> {
    Ok(Library {
        id: r.get(0)?,
        uuid: r.get(1)?,
        name: r.get(2)?,
        root: r.get(3)?,
        pattern: r.get(4)?,
        monitored: r.get::<_, i64>(5)? != 0,
        last_crawled_at: r.get(6)?,
        archive_count: r.get(7)?,
        new_count: r.get(8)?,
        updated_count: r.get(9)?,
        origin: r.get(10)?,
        models: r.get(11)?,
    })
}

/// Every library, by name.
pub fn list(conn: &Connection) -> Result<Vec<Library>> {
    let mut st = conn.prepare(&format!("SELECT {COLUMNS} FROM libraries l ORDER BY l.name COLLATE NOCASE"))?;
    st.query_map([], from_row)?.collect()
}

pub fn get(conn: &Connection, id: i64) -> Result<Option<Library>> {
    conn.query_row(&format!("SELECT {COLUMNS} FROM libraries l WHERE l.id = ?1"), [id], from_row).optional()
}

/// The library whose root is `root` (compared as this system does).
pub fn by_root(conn: &Connection, root: &str) -> Result<Option<Library>> {
    let key = path_key(root);
    Ok(list(conn)?.into_iter().find(|l| path_key(&l.root) == key))
}

/// `root` without trailing separators (a drive's or the file system's root keeps one).
pub fn clean_root(root: &str) -> String {
    let t = root.trim();
    let trimmed = t.trim_end_matches(['/', '\\']);
    if trimmed.is_empty() {
        return t.chars().next().map(String::from).unwrap_or_default();
    }
    if trimmed.len() == 2 && trimmed.ends_with(':') {
        return format!("{trimmed}{}", crate::winpath::SEP);
    }
    trimmed.to_string()
}

/// Whether `path` is `root` or below it.
pub fn contains(root: &str, path: &str) -> bool {
    path_key(root) == path_key(path) || RelPath::from_abs(root, path).is_some()
}

// ── roots ────────────────────────────────────────────────────────────────────

/// The libraries' roots, for going between absolute and relative paths.
#[derive(Debug, Clone, Default)]
pub struct Roots {
    /// `(id, root)`, deepest roots first.
    libs: Vec<(i64, String)>,
}

impl Roots {
    pub fn load(conn: &Connection) -> Result<Roots> {
        let mut st = conn.prepare("SELECT id, root FROM libraries")?;
        let libs = st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<Vec<(i64, String)>>>()?;
        Ok(Roots::new(libs))
    }

    pub fn new(mut libs: Vec<(i64, String)>) -> Roots {
        libs.sort_by_key(|(_, r)| std::cmp::Reverse(path_key(r).len()));
        Roots { libs }
    }

    pub fn root(&self, id: i64) -> Option<&str> {
        self.libs.iter().find(|(i, _)| *i == id).map(|(_, r)| r.as_str())
    }

    /// The absolute path of `rel` in library `id`.
    pub fn resolve(&self, id: i64, rel: &str) -> Option<String> {
        self.root(id).map(|root| relpath::join(root, rel))
    }

    /// The deepest library holding `abs`, and the path relative to it.
    pub fn locate(&self, abs: &str) -> Option<(i64, RelPath)> {
        self.libs.iter().find_map(|(id, root)| RelPath::from_abs(root, abs).map(|r| (*id, r)))
    }

    /// The deepest library holding the folder `folder` (its root included),
    /// and the folder relative to it (`""` for the root).
    pub fn locate_folder(&self, folder: &str) -> Option<(i64, String)> {
        let key = path_key(folder);
        self.libs.iter().find_map(|(id, root)| {
            if path_key(root) == key {
                Some((*id, String::new()))
            } else {
                RelPath::from_abs(root, folder).map(|r| (*id, r.as_str().to_string()))
            }
        })
    }

    /// The library whose root is `root`.
    pub fn locate_root(&self, root: &str) -> Option<i64> {
        let key = path_key(root);
        self.libs.iter().find(|(_, r)| path_key(r) == key).map(|(i, _)| *i)
    }

    /// Roots of the libraries strictly inside library `id`.
    pub fn inner(&self, id: i64) -> Vec<(i64, String)> {
        let Some(root) = self.root(id) else { return Vec::new() };
        self.libs.iter().filter(|(i, r)| *i != id && RelPath::from_abs(root, r).is_some()).cloned().collect()
    }
}

// ── changes ──────────────────────────────────────────────────────────────────

fn new_uuid() -> String {
    uuid::Uuid::new_v4().to_string()
}

/// `base`, or `base (2)`, `base (3)`… so that no other library has the name.
fn unique_name(conn: &Connection, base: &str, except: Option<i64>) -> Result<String> {
    let taken = |n: &str| -> Result<bool> {
        conn.query_row("SELECT EXISTS (SELECT 1 FROM libraries WHERE name = ?1 COLLATE NOCASE AND id IS NOT ?2)", params![n, except], |r| r.get(0))
    };
    let base = if base.trim().is_empty() { "Library" } else { base.trim() };
    if !taken(base)? {
        return Ok(base.to_string());
    }
    for i in 2.. {
        let n = format!("{base} ({i})");
        if !taken(&n)? {
            return Ok(n);
        }
    }
    unreachable!()
}

/// A library's default name: its root's last folder (or the root itself).
pub fn default_name(root: &str) -> String {
    let name = WinPath::new(root).name().to_string();
    if name.is_empty() { root.trim_end_matches(['/', '\\']).to_string() } else { name }
}

/// Move the records below `inner_root` from library `from` (whose root is
/// `from_root`) into library `to`. Returns how many moved.
fn adopt(tx: &Transaction, from: i64, from_root: &str, to: i64, inner_root: &str) -> Result<usize> {
    let mut st = tx.prepare("SELECT id, rel_path FROM models WHERE library_id = ?1")?;
    let rows: Vec<(i64, String)> = st.query_map([from], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    let mut up = tx.prepare("UPDATE models SET library_id = ?2, rel_path = ?3, rel_folder = ?4 WHERE id = ?1")?;
    let mut moved = 0;
    for (id, rel) in rows {
        if let Some(r) = RelPath::from_abs(inner_root, &relpath::join(from_root, &rel)) {
            up.execute(params![id, to, r.as_str(), r.folder()])?;
            moved += 1;
        }
    }
    Ok(moved)
}

/// Create a library at `root` (or return the one already there). Records of
/// libraries around it that lie below `root` move into it.
pub fn create(tx: &Transaction, root: &str, name: Option<&str>, pattern: Option<&str>, origin: Option<&str>) -> Result<i64> {
    let root = clean_root(root);
    if let Some(l) = by_root(tx, &root)? {
        return Ok(l.id);
    }
    let name = unique_name(tx, name.unwrap_or(&default_name(&root)), None)?;
    tx.execute(
        "INSERT INTO libraries (uuid, name, root, pattern, monitored, origin, created_at) VALUES (?1, ?2, ?3, ?4, 0, ?5, ?6)",
        params![new_uuid(), name, root, pattern, origin, utc_now_text()],
    )?;
    let id = tx.last_insert_rowid();
    for (outer, outer_root) in Roots::load(tx)?.libs.iter().filter(|(i, r)| *i != id && RelPath::from_abs(r, &root).is_some()) {
        adopt(tx, *outer, outer_root, id, &root)?;
    }
    Ok(id)
}

/// The library holding `abs`, creating one at `fallback_root` (default: the
/// path's folder) when none does; and the path relative to it.
pub fn locate_or_create(tx: &Transaction, abs: &str, fallback_root: Option<&str>) -> Result<(i64, RelPath)> {
    if let Some(found) = Roots::load(tx)?.locate(abs) {
        return Ok(found);
    }
    let folder = WinPath::new(abs).parent().as_posix();
    let root = fallback_root.filter(|r| RelPath::from_abs(r, abs).is_some()).map(str::to_string).unwrap_or_else(|| crate::winpath::native(&folder));
    let id = create(tx, &root, None, None, None)?;
    let rel = Roots::load(tx)?.locate(abs).filter(|(i, _)| *i == id).map(|(_, r)| r);
    rel.map(|r| (id, r)).ok_or_else(|| rusqlite::Error::InvalidParameterName(format!("{abs} is not a usable archive path")))
}

pub fn rename(tx: &Transaction, id: i64, name: &str) -> Result<String> {
    let name = unique_name(tx, name, Some(id))?;
    tx.execute("UPDATE libraries SET name = ?2 WHERE id = ?1", params![id, name])?;
    Ok(name)
}

pub fn set_pattern(tx: &Transaction, id: i64, pattern: Option<&str>) -> Result<bool> {
    Ok(tx.execute("UPDATE libraries SET pattern = ?2 WHERE id = ?1", params![id, pattern.filter(|p| !p.trim().is_empty())])? > 0)
}

pub fn set_monitored(tx: &Transaction, id: i64, monitored: bool) -> Result<bool> {
    Ok(tx.execute("UPDATE libraries SET monitored = ?2 WHERE id = ?1", params![id, monitored as i64])? > 0)
}

/// After a crawl: its counts and pattern.
pub fn record_crawl(tx: &Transaction, id: i64, pattern: Option<&str>, archive_count: i64, new_count: i64, updated_count: i64) -> Result<()> {
    tx.execute(
        "UPDATE libraries SET pattern = coalesce(?2, pattern), last_crawled_at = ?3, archive_count = ?4, new_count = ?5, \
         updated_count = ?6 WHERE id = ?1",
        params![id, pattern.filter(|p| !p.trim().is_empty()), utc_now_text(), archive_count, new_count, updated_count],
    )?;
    Ok(())
}

/// What removing a library did.
#[derive(Debug, Default, PartialEq)]
pub struct Removed {
    /// The library around it that took its records.
    pub moved_to: Option<i64>,
    pub moved: usize,
    pub deleted: usize,
    /// Thumbnail refs of deleted images (for the thumbnail store).
    pub refs: Vec<String>,
}

/// Remove a library: its records go to the library around it, or are deleted
/// when there is none (the archive files are not touched).
pub fn remove(tx: &Transaction, id: i64) -> Result<Removed> {
    let Some(lib) = get(tx, id)? else { return Ok(Removed::default()) };
    let roots = Roots::load(tx)?;
    let outer = roots.libs.iter().filter(|(i, _)| *i != id).find(|(_, r)| RelPath::from_abs(r, &lib.root).is_some()).cloned();
    let mut out = Removed::default();
    let mut st = tx.prepare("SELECT id, rel_path FROM models WHERE library_id = ?1")?;
    let rows: Vec<(i64, String)> = st.query_map([id], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    for (mid, rel) in rows {
        let target = outer.as_ref().and_then(|(o, oroot)| RelPath::from_abs(oroot, &relpath::join(&lib.root, &rel)).map(|r| (*o, r)));
        let free = |o: i64, r: &RelPath| -> Result<bool> {
            tx.query_row("SELECT NOT EXISTS (SELECT 1 FROM models WHERE library_id = ?1 AND rel_path = ?2)", params![o, r.as_str()], |x| x.get(0))
        };
        match target {
            Some((o, r)) if free(o, &r)? => {
                tx.execute("UPDATE models SET library_id = ?2, rel_path = ?3, rel_folder = ?4 WHERE id = ?1", params![mid, o, r.as_str(), r.folder()])?;
                out.moved_to = Some(o);
                out.moved += 1;
            }
            _ => {
                out.refs.extend(super::edit::delete_model(tx, mid)?);
                out.deleted += 1;
            }
        }
    }
    tx.execute("DELETE FROM libraries WHERE id = ?1", [id])?;
    Ok(out)
}

// ── relocation ───────────────────────────────────────────────────────────────

/// What moving a library to a new root finds there.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct RelocateCheck {
    pub total: usize,
    pub found: usize,
    /// Found only with another letter case (`(model id, the spelling on disk)`).
    pub case_only: Vec<(i64, RelPath)>,
    pub missing: usize,
    /// A few missing relative paths, to show.
    pub missing_examples: Vec<String>,
    /// Libraries inside this one, moved along: `(id, new root)`.
    pub inner: Vec<(i64, String)>,
}

/// `rel` under `root` with the letter case it has on disk, when it exists
/// only with another case.
fn case_insensitive_match(root: &Path, rel: &str) -> Option<RelPath> {
    let mut dir = root.to_path_buf();
    let mut parts = Vec::new();
    for want in rel.split('/') {
        let exact = dir.join(want);
        let name = if exact.exists() {
            want.to_string()
        } else {
            let lower = want.to_lowercase();
            std::fs::read_dir(&dir).ok()?.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).find(|n| n.to_lowercase() == lower)?
        };
        dir = dir.join(&name);
        parts.push(name);
    }
    RelPath::parse(&parts.join("/")).ok()
}

/// Check moving library `id` to `new_root`: the root must be a folder outside
/// every other library (inner libraries move along) and not around one.
pub fn check_relocate(conn: &Connection, id: i64, new_root: &str, ctl: Option<&crate::control::Control>) -> std::result::Result<RelocateCheck, String> {
    let lib = get(conn, id).map_err(|e| e.to_string())?.ok_or("no such library")?;
    let new_root = clean_root(new_root);
    if !Path::new(&new_root).is_dir() {
        return Err(format!("{new_root} is not a folder on the server"));
    }
    let roots = Roots::load(conn).map_err(|e| e.to_string())?;
    let inner = roots.inner(id);
    for (other, root) in roots.libs.iter().filter(|(i, _)| *i != id && !inner.iter().any(|(j, _)| j == i)) {
        if contains(root, &new_root) || contains(&new_root, root) {
            let name = get(conn, *other).ok().flatten().map(|l| l.name).unwrap_or_default();
            return Err(format!("{new_root} overlaps the library {name} ({root})"));
        }
    }
    let mut out = RelocateCheck {
        inner: inner.iter().filter_map(|(i, r)| RelPath::from_abs(&lib.root, r).map(|rel| (*i, rel.resolve(&new_root)))).collect(),
        ..Default::default()
    };
    let mut st = conn.prepare("SELECT id, rel_path FROM models WHERE library_id = ?1 ORDER BY id").map_err(|e| e.to_string())?;
    let rows: Vec<(i64, String)> =
        st.query_map([id], |r| Ok((r.get(0)?, r.get(1)?))).map_err(|e| e.to_string())?.collect::<Result<_>>().map_err(|e| e.to_string())?;
    out.total = rows.len();
    if let Some(c) = ctl {
        c.set_total(rows.len() as u64);
    }
    for (mid, rel) in rows {
        if ctl.is_some_and(|c| c.is_stopped()) {
            return Err("stopped".into());
        }
        if Path::new(&relpath::join(&new_root, &rel)).exists() {
            out.found += 1;
        } else if let Some(m) = case_insensitive_match(Path::new(&new_root), &rel) {
            out.case_only.push((mid, m));
        } else {
            out.missing += 1;
            if out.missing_examples.len() < 20 {
                out.missing_examples.push(rel);
            }
        }
        if let Some(c) = ctl {
            c.advance(1);
        }
    }
    Ok(out)
}

/// Move library `id` to `new_root` after [`check_relocate`] (its result is
/// passed back in): the new root, the roots of the libraries inside it, and
/// with `fix_case` the spelling of paths found only with another case.
pub fn relocate(tx: &Transaction, id: i64, new_root: &str, check: &RelocateCheck, fix_case: bool) -> Result<()> {
    tx.execute("UPDATE libraries SET root = ?2 WHERE id = ?1", params![id, clean_root(new_root)])?;
    for (inner, root) in &check.inner {
        tx.execute("UPDATE libraries SET root = ?2 WHERE id = ?1", params![inner, root])?;
    }
    if fix_case {
        let mut up = tx.prepare("UPDATE OR IGNORE models SET rel_path = ?2, rel_folder = ?3 WHERE id = ?1")?;
        for (mid, rel) in &check.case_only {
            up.execute(params![mid, rel.as_str(), rel.folder()])?;
        }
    }
    Ok(())
}

// ── the one-way migration from absolute paths ───────────────────────────────

/// What the migration from absolute paths did.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct PathsMigration {
    /// Libraries made from crawled folders.
    pub libraries: usize,
    /// Libraries made for archives outside every crawled folder.
    pub extra_libraries: usize,
    pub models: usize,
    /// Records of an archive catalogued twice (the same folder crawled under
    /// two spellings, `F:/STL` and `F:\STL`) merged into one.
    pub duplicates_merged: usize,
    /// Thumbnail refs of the merged-away records' images (for the thumbnail store).
    pub thumbnail_refs: Vec<String>,
}

/// Columns a merged-away duplicate fills in on the record that is kept, where
/// the kept one has none.
const FILLED: [&str; 15] = [
    "title", "creator", "source", "release_year", "release_month", "url", "description", "sequence", "sculptor_id",
    "collection_id", "subcollection_id", "category_id", "release_id", "presupports", "warning",
];

/// Of records cataloguing the same archive, the one to keep: present before
/// missing, then the most recently changed, then the oldest.
fn kept_of(tx: &Transaction, ids: &[i64], cols: &[String]) -> Result<i64> {
    let has = |c: &str| cols.iter().any(|x| x == c);
    let missing = if has("is_missing") { "coalesce(is_missing, 0)" } else { "0" };
    let updated = if has("updated_at") { "coalesce(updated_at, '')" } else { "''" };
    let list = ids.iter().map(i64::to_string).collect::<Vec<_>>().join(",");
    tx.query_row(&format!("SELECT id FROM models WHERE id IN ({list}) ORDER BY {missing}, {updated} DESC, id LIMIT 1"), [], |r| r.get(0))
}

/// Merge each `(keep, drop)` record pair: `keep` gets `drop`'s values where
/// it has none, and its tags and scales; `drop` is deleted with its images.
/// Set-based (one statement per column or table, whatever the number of
/// pairs). Returns the deleted images' thumbnail refs.
fn merge_duplicates(tx: &Transaction, pairs: &[(i64, i64)], cols: &[String], tables: &[String]) -> Result<Vec<String>> {
    if pairs.is_empty() {
        return Ok(Vec::new());
    }
    let has_table = |t: &str| tables.iter().any(|x| x == t);
    tx.execute_batch("CREATE TEMP TABLE _dups (keep INTEGER NOT NULL, drop_id INTEGER PRIMARY KEY); CREATE INDEX _dups_keep ON _dups (keep);")?;
    {
        let mut ins = tx.prepare("INSERT INTO _dups VALUES (?1, ?2)")?;
        for (keep, drop) in pairs {
            ins.execute([keep, drop])?;
        }
    }
    for c in FILLED.iter().filter(|c| cols.iter().any(|x| x == *c)) {
        tx.execute_batch(&format!(
            "UPDATE models SET {c} = (SELECT d.{c} FROM _dups x JOIN models d ON d.id = x.drop_id \
               WHERE x.keep = models.id AND d.{c} IS NOT NULL AND d.{c} != '' ORDER BY d.id LIMIT 1) \
             WHERE id IN (SELECT keep FROM _dups) AND ({c} IS NULL OR {c} = '') \
               AND EXISTS (SELECT 1 FROM _dups x JOIN models d ON d.id = x.drop_id \
                 WHERE x.keep = models.id AND d.{c} IS NOT NULL AND d.{c} != '')"
        ))?;
    }
    if cols.iter().any(|x| x == "to_print") {
        tx.execute_batch("UPDATE models SET to_print = 1 WHERE id IN (SELECT x.keep FROM _dups x JOIN models d ON d.id = x.drop_id WHERE d.to_print)")?;
    }
    for (table, col) in [("model_tags", "tag_id"), ("model_scales", "scale_id")] {
        if has_table(table) {
            tx.execute_batch(&format!(
                "INSERT OR IGNORE INTO {table} (model_id, {col}) SELECT x.keep, t.{col} FROM _dups x JOIN {table} t ON t.model_id = x.drop_id"
            ))?;
        }
    }
    let mut refs = Vec::new();
    if has_table("images") {
        let mut st = tx.prepare(
            "SELECT thumbnail_path FROM images WHERE model_id IN (SELECT drop_id FROM _dups) AND coalesce(thumbnail_path, '') != ''",
        )?;
        refs = st.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
        if has_table(super::schema_sql::VECTOR_TABLE) {
            tx.execute_batch(&format!(
                "DELETE FROM {} WHERE image_id IN (SELECT id FROM images WHERE model_id IN (SELECT drop_id FROM _dups))",
                super::schema_sql::VECTOR_TABLE
            ))?;
        }
    }
    // Foreign keys are off while `models` is rebuilt: delete the children here.
    for table in ["model_tags", "model_scales", "stl_files", "images"] {
        if has_table(table) {
            tx.execute_batch(&format!("DELETE FROM {table} WHERE model_id IN (SELECT drop_id FROM _dups)"))?;
        }
    }
    tx.execute_batch("DELETE FROM models WHERE id IN (SELECT drop_id FROM _dups); DROP TABLE _dups;")?;
    Ok(refs)
}

/// Whether `models` still has absolute paths (a database from before libraries).
pub fn needs_migration(conn: &Connection) -> Result<bool> {
    let mut st = conn.prepare("SELECT 1 FROM pragma_table_info('models') WHERE name = 'archive_path'")?;
    st.exists([])
}

/// Turn `crawled_folders` into libraries and `models.archive_path` into
/// `library_id` + `rel_path`. Foreign keys must be off (the caller's
/// connection); `models` is rebuilt.
pub fn migrate(tx: &Transaction, models_ddl: &str, models_indexes: &[&str]) -> Result<Option<PathsMigration>> {
    if !needs_migration(tx)? {
        return Ok(None);
    }
    let mut report = PathsMigration::default();
    // 1. Crawled folders → libraries.
    let has_folders = tx.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'crawled_folders'")?.exists([])?;
    if has_folders {
        let cols: Vec<String> =
            tx.prepare("SELECT name FROM pragma_table_info('crawled_folders')")?.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
        let col = |c: &str| if cols.iter().any(|x| x == c) { c.to_string() } else { "NULL".to_string() };
        let sql = format!(
            "SELECT path, {}, {}, {}, {}, {}, {} FROM crawled_folders ORDER BY id",
            col("pattern"),
            col("monitored"),
            col("last_crawled_at"),
            col("archive_count"),
            col("new_count"),
            col("updated_count")
        );
        type Folder = (String, Option<String>, Option<i64>, Option<String>, Option<i64>, Option<i64>, Option<i64>);
        let folders: Vec<Folder> = tx
            .prepare(&sql)?
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?)))?
            .collect::<Result<_>>()?;
        for (path, pattern, monitored, last, count, new, updated) in folders {
            let root = clean_root(&path);
            if root.is_empty() || Roots::load(tx)?.locate_root(&root).is_some() {
                continue;
            }
            let name = unique_name(tx, &default_name(&root), None)?;
            tx.execute(
                "INSERT INTO libraries (uuid, name, root, pattern, monitored, last_crawled_at, archive_count, new_count, updated_count, created_at) \
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![new_uuid(), name, root, pattern, monitored.unwrap_or(0), last, count, new, updated, utc_now_text()],
            )?;
            report.libraries += 1;
        }
    }
    // 2. Each model to the deepest library above it; the rest grouped by folder.
    let models: Vec<(i64, Option<String>)> =
        tx.prepare("SELECT id, archive_path FROM models ORDER BY id")?.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    report.models = models.len();
    let mut roots = Roots::load(tx)?;
    let usable = |p: &Option<String>| p.as_deref().filter(|p| !p.trim().is_empty()).map(str::to_string);
    let orphan_folders: Vec<String> = {
        let mut f: Vec<String> = models
            .iter()
            .filter_map(|(_, p)| usable(p))
            .filter(|p| roots.locate(p).or_else(|| sanitized(&roots, p)).is_none())
            .map(|p| crate::winpath::native(&WinPath::new(&p).parent().as_posix()))
            .collect();
        f.sort_by_key(|x| path_key(x).len());
        f.dedup_by(|a, b| path_key(a) == path_key(b));
        f
    };
    let mut extra_roots: Vec<String> = Vec::new();
    for folder in orphan_folders {
        if !extra_roots.iter().any(|r| contains(r, &folder)) {
            extra_roots.push(folder);
        }
    }
    for root in &extra_roots {
        let name = unique_name(tx, &default_name(root), None)?;
        tx.execute(
            "INSERT INTO libraries (uuid, name, root, monitored, origin, created_at) VALUES (?1, ?2, ?3, 0, 'migration', ?4)",
            params![new_uuid(), name, clean_root(root), utc_now_text()],
        )?;
        report.extra_libraries += 1;
    }
    roots = Roots::load(tx)?;
    let old_cols: Vec<String> = tx.prepare("SELECT name FROM pragma_table_info('models')")?.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    let tables: Vec<String> =
        tx.prepare("SELECT name FROM sqlite_master WHERE type = 'table'")?.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    tx.execute_batch("CREATE TEMP TABLE _paths (id INTEGER PRIMARY KEY, library_id INTEGER, rel_path TEXT, rel_folder TEXT)")?;
    {
        let mut placed: Vec<(i64, i64, RelPath)> = Vec::with_capacity(models.len());
        let mut unplaced: Option<i64> = None;
        for (id, path) in &models {
            let located = usable(path).and_then(|p| roots.locate(&p).or_else(|| sanitized(&roots, &p)));
            let (lib, rel) = match located {
                Some(found) => found,
                None => {
                    // No usable path: kept, missing, in a library of their own.
                    let lib = match unplaced {
                        Some(l) => l,
                        None => {
                            let name = unique_name(tx, "Archives without a path", None)?;
                            tx.execute(
                                "INSERT INTO libraries (uuid, name, root, monitored, origin, created_at) VALUES (?1, ?2, '', 0, 'migration', ?3)",
                                params![new_uuid(), name, utc_now_text()],
                            )?;
                            report.extra_libraries += 1;
                            *unplaced.insert(tx.last_insert_rowid())
                        }
                    };
                    tx.execute("UPDATE models SET is_missing = 1 WHERE id = ?1", [id])?;
                    (lib, RelPath::parse(&format!("model-{id}")).expect("a plain name"))
                }
            };
            placed.push((*id, lib, rel));
        }
        // 2b. Records of the same archive (one folder crawled under two
        // spellings) become one record.
        let key = |lib: i64, rel: &RelPath| {
            (lib, if crate::relpath::CASE_INSENSITIVE { rel.as_str().to_lowercase() } else { rel.as_str().to_string() })
        };
        let mut groups: std::collections::HashMap<(i64, String), Vec<i64>> = std::collections::HashMap::new();
        for (id, lib, rel) in &placed {
            groups.entry(key(*lib, rel)).or_default().push(*id);
        }
        let mut dropped: std::collections::HashSet<i64> = std::collections::HashSet::new();
        let mut dup_groups: Vec<&Vec<i64>> = groups.values().filter(|v| v.len() > 1).collect();
        dup_groups.sort();
        let mut pairs: Vec<(i64, i64)> = Vec::new();
        for ids in dup_groups {
            let keep = kept_of(tx, ids, &old_cols)?;
            for &d in ids.iter().filter(|&&d| d != keep) {
                pairs.push((keep, d));
                dropped.insert(d);
            }
        }
        report.duplicates_merged = pairs.len();
        report.thumbnail_refs = merge_duplicates(tx, &pairs, &old_cols, &tables)?;
        let mut ins = tx.prepare("INSERT INTO _paths VALUES (?1, ?2, ?3, ?4)")?;
        for (id, lib, rel) in placed.iter().filter(|(id, ..)| !dropped.contains(id)) {
            ins.execute(params![id, lib, rel.as_str(), rel.folder()])?;
        }
    }
    // 3. Rebuild `models` with the new columns.
    tx.execute_batch("DROP VIEW IF EXISTS models_view")?;
    tx.execute_batch(&models_ddl.replace("CREATE TABLE models (", "CREATE TABLE models_new ("))?;
    let new_cols: Vec<String> =
        tx.prepare("SELECT name FROM pragma_table_info('models_new')")?.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    let copied: Vec<&String> = new_cols.iter().filter(|c| old_cols.contains(c) && !matches!(c.as_str(), "library_id" | "rel_path" | "rel_folder")).collect();
    let list = copied.iter().map(|c| format!("\"{c}\"")).collect::<Vec<_>>().join(", ");
    let from = copied.iter().map(|c| format!("m.\"{c}\"")).collect::<Vec<_>>().join(", ");
    tx.execute_batch(&format!(
        "INSERT INTO models_new ({list}, library_id, rel_path, rel_folder) \
         SELECT {from}, p.library_id, p.rel_path, p.rel_folder FROM models m JOIN _paths p ON p.id = m.id;
         DROP TABLE models;
         ALTER TABLE models_new RENAME TO models;
         DROP TABLE _paths;"
    ))?;
    for ix in models_indexes {
        tx.execute_batch(ix)?;
    }
    if has_folders {
        tx.execute_batch("DROP TABLE crawled_folders")?;
    }
    Ok(Some(report))
}

/// A path whose parts include `.`, `..` or empty ones, cleaned up and located.
fn sanitized(roots: &Roots, path: &str) -> Option<(i64, RelPath)> {
    let posix = WinPath::new(path).as_posix();
    roots.locate(&crate::winpath::native(&posix))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, migrate as migrate_db, open};

    fn db() -> (tempfile::TempDir, Connection) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lib.db");
        migrate_db(&path, &Vectors::default()).unwrap();
        let conn = open(&path).unwrap();
        (dir, conn)
    }

    fn insert(conn: &Connection, lib: i64, rel: &str) -> i64 {
        let r = RelPath::parse(rel).unwrap();
        conn.execute(
            "INSERT INTO models (title, library_id, rel_path, rel_folder, archive_hash, is_missing, to_print) VALUES (?1, ?2, ?3, ?4, ?3, 0, 0)",
            params![r.name(), lib, r.as_str(), r.folder()],
        )
        .unwrap();
        conn.last_insert_rowid()
    }

    fn of(conn: &Connection, id: i64) -> (i64, String) {
        conn.query_row("SELECT library_id, rel_path FROM models WHERE id = ?1", [id], |r| Ok((r.get(0)?, r.get(1)?))).unwrap()
    }

    #[test]
    fn migrates_absolute_paths_into_libraries() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("old.db");
        {
            // The layout before libraries: absolute paths and crawled folders.
            let c = Connection::open(&path).unwrap();
            c.execute_batch(
                "CREATE TABLE crawled_folders (id INTEGER NOT NULL, path TEXT NOT NULL, pattern TEXT, last_crawled_at DATETIME,
                     archive_count INTEGER, new_count INTEGER, updated_count INTEGER, PRIMARY KEY (id), UNIQUE (path));
                 CREATE TABLE models (id INTEGER NOT NULL, title VARCHAR(512), archive_path TEXT NOT NULL,
                     archive_folder VARCHAR(512), archive_hash VARCHAR(64) NOT NULL, warning TEXT, is_missing BOOLEAN DEFAULT '0' NOT NULL,
                     to_print BOOLEAN DEFAULT '0' NOT NULL, description TEXT, updated_at DATETIME, PRIMARY KEY (id), UNIQUE (archive_path));
                 CREATE TABLE tags (id INTEGER NOT NULL, name VARCHAR(128) NOT NULL, PRIMARY KEY (id), UNIQUE (name));
                 CREATE TABLE model_tags (model_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY (model_id, tag_id),
                     FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE, FOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE);
                 CREATE TABLE images (id INTEGER NOT NULL, model_id INTEGER NOT NULL, filename VARCHAR(512), thumbnail_path VARCHAR(512),
                     PRIMARY KEY (id), FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE);
                 INSERT INTO crawled_folders (path, pattern, archive_count) VALUES ('D:/STL', '{title}', 2), ('D:/STL/Gambody/', NULL, 1);
                 INSERT INTO models (id, title, archive_path, archive_folder, archive_hash, updated_at) VALUES
                     (1, 'A', 'D:/STL/Studio/a.zip', 'D:/STL/Studio', 'h1', '2020-01-01'),
                     (2, 'B', 'D:/STL/Gambody/b.zip', 'D:/STL/Gambody', 'h2', NULL),
                     (3, 'C', 'E:/loose/c.zip', 'E:/loose', 'h3', NULL),
                     (4, 'D', 'E:/loose/sub/d.zip', 'E:/loose/sub', 'h4', NULL),
                     (5, 'E', '', NULL, 'h5', NULL);
                 -- The same archive catalogued twice, under another spelling of its folder;
                 -- this record was changed last, so it is the one kept.
                 INSERT INTO models (id, title, archive_path, archive_folder, archive_hash, description, updated_at) VALUES
                     (6, NULL, 'D:/STL/./Studio/a.zip', 'D:/STL/Studio', 'h1', 'From six', '2024-01-01');
                 INSERT INTO tags (id, name) VALUES (1, 'dragon'), (2, 'red');
                 INSERT INTO model_tags (model_id, tag_id) VALUES (1, 1), (6, 2);
                 INSERT INTO images (model_id, filename, thumbnail_path) VALUES (1, 'a.png', '1'), (4, 'd.png', '2'), (6, 'a.png', '3');",
            )
            .unwrap();
        }
        let report = migrate_db(&path, &Vectors::default()).unwrap();
        assert_eq!(
            report.paths,
            Some(PathsMigration { libraries: 2, extra_libraries: 2, models: 6, duplicates_merged: 1, thumbnail_refs: vec!["1".into()] })
        );
        let conn = open(&path).unwrap();
        let libs = list(&conn).unwrap();
        let by_name = |n: &str| libs.iter().find(|l| l.name == n).unwrap_or_else(|| panic!("{n} in {libs:?}")).clone();
        let (stl, gambody, loose) = (by_name("STL"), by_name("Gambody"), by_name("loose"));
        assert_eq!((stl.root.as_str(), stl.pattern.as_deref(), stl.archive_count), ("D:/STL", Some("{title}"), Some(2)));
        assert_eq!(gambody.root, "D:/STL/Gambody", "trailing separator dropped");
        assert_eq!((loose.root.clone(), loose.origin.as_deref()), (crate::winpath::native("E:/loose"), Some("migration")));
        let rows: Vec<(i64, i64, String, String)> = conn
            .prepare("SELECT id, library_id, rel_path, rel_folder FROM models ORDER BY id")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))
            .unwrap()
            .collect::<Result<_>>()
            .unwrap();
        assert_eq!(rows[0], (2, gambody.id, "b.zip".into(), "".into()), "the deepest library wins");
        assert_eq!(rows[1], (3, loose.id, "c.zip".into(), "".into()));
        assert_eq!(rows[2], (4, loose.id, "sub/d.zip".into(), "sub".into()), "grouped under the outermost folder");
        let unplaced = by_name("Archives without a path");
        assert_eq!(rows[3], (5, unplaced.id, "model-5".into(), "".into()));
        assert_eq!(rows[4], (6, stl.id, "Studio/a.zip".into(), "Studio".into()), "the duplicates became one record");
        let (title, description): (String, String) =
            conn.query_row("SELECT title, description FROM models WHERE id = 6", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!((title.as_str(), description.as_str()), ("A", "From six"), "empty fields filled from the other record");
        let tags: i64 = conn.query_row("SELECT count(*) FROM model_tags WHERE model_id = 6", [], |r| r.get(0)).unwrap();
        assert_eq!(tags, 2, "tags joined");
        let missing: bool = conn.query_row("SELECT is_missing FROM models WHERE id = 5", [], |r| r.get(0)).unwrap();
        assert!(missing);
        let images: i64 = conn.query_row("SELECT count(*) FROM images", [], |r| r.get(0)).unwrap();
        assert_eq!(images, 2, "the rebuild keeps every image but the merged-away record's");
        let folders: bool = conn.prepare("SELECT 1 FROM sqlite_master WHERE name = 'crawled_folders'").unwrap().exists([]).unwrap();
        assert!(!folders);
        // Paths come back absolute through the view; a second migration does nothing.
        let ap: String = conn.query_row("SELECT archive_path FROM models_view WHERE id = 6", [], |r| r.get(0)).unwrap();
        assert_eq!(ap, relpath::join("D:/STL", "Studio/a.zip"));
        assert_eq!(migrate_db(&path, &Vectors::default()).unwrap().paths, None);
    }

    #[test]
    fn locate_picks_the_deepest_root() {
        let roots = Roots::new(vec![(1, "/stl".into()), (2, "/stl/Gambody".into())]);
        assert_eq!(roots.locate("/stl/Gambody/a/x.zip").map(|(i, r)| (i, r.to_string())), Some((2, "a/x.zip".into())));
        assert_eq!(roots.locate("/stl/b.zip").map(|(i, r)| (i, r.to_string())), Some((1, "b.zip".into())));
        assert_eq!(roots.locate("/other/c.zip"), None);
        assert_eq!(roots.inner(1), vec![(2, "/stl/Gambody".into())]);
        assert_eq!(roots.resolve(2, "a/x.zip").unwrap(), relpath::join("/stl/Gambody", "a/x.zip"));
    }

    #[test]
    fn inner_libraries_adopt_and_give_back_records() {
        let (dir, mut conn) = db();
        let root = dir.path().join("stl").to_string_lossy().into_owned();
        let tx = conn.transaction().unwrap();
        let outer = create(&tx, &root, None, Some("{title}"), None).unwrap();
        tx.commit().unwrap();
        let a = insert(&conn, outer, "Gambody/Dragon.zip");
        let b = insert(&conn, outer, "Other/Knight.zip");
        let inner_root = relpath::join(&root, "Gambody");
        let tx = conn.transaction().unwrap();
        let inner = create(&tx, &inner_root, None, None, None).unwrap();
        assert_eq!(create(&tx, &format!("{inner_root}/"), None, None, None).unwrap(), inner, "the same root");
        tx.commit().unwrap();
        assert_eq!(of(&conn, a), (inner, "Dragon.zip".into()), "adopted");
        assert_eq!(of(&conn, b), (outer, "Other/Knight.zip".into()));
        assert_eq!(get(&conn, inner).unwrap().unwrap().name, "Gambody");

        let tx = conn.transaction().unwrap();
        let r = remove(&tx, inner).unwrap();
        tx.commit().unwrap();
        assert_eq!((r.moved_to, r.moved, r.deleted), (Some(outer), 1, 0));
        assert_eq!(of(&conn, a), (outer, "Gambody/Dragon.zip".into()), "given back");

        let tx = conn.transaction().unwrap();
        let r = remove(&tx, outer).unwrap();
        tx.commit().unwrap();
        assert_eq!((r.moved_to, r.deleted), (None, 2), "no library around it: records go");
        assert!(list(&conn).unwrap().is_empty());
    }

    #[test]
    fn names_are_unique() {
        let (_dir, mut conn) = db();
        let tx = conn.transaction().unwrap();
        let a = create(&tx, "/a/STL", None, None, None).unwrap();
        let b = create(&tx, "/b/STL", None, None, None).unwrap();
        assert_eq!(get(&tx, b).unwrap().unwrap().name, "STL (2)");
        assert_eq!(rename(&tx, a, "stl (2)").unwrap(), "stl (2) (2)");
        assert_eq!(rename(&tx, b, "Minis").unwrap(), "Minis");
        tx.commit().unwrap();
        assert_eq!(get(&conn, a).unwrap().unwrap().uuid.len(), 36);
    }

    #[test]
    fn relocation_checks_then_moves_the_root() {
        let (dir, mut conn) = db();
        let old = dir.path().join("old");
        let new = dir.path().join("new");
        std::fs::create_dir_all(new.join("Studio")).unwrap();
        std::fs::write(new.join("Studio").join("Dragon.zip"), b"x").unwrap();
        std::fs::write(new.join("Studio").join("KNIGHT.zip"), b"x").unwrap();
        let (old_s, new_s) = (old.to_string_lossy().into_owned(), new.to_string_lossy().into_owned());
        let tx = conn.transaction().unwrap();
        let lib = create(&tx, &old_s, None, None, None).unwrap();
        let inner = create(&tx, &relpath::join(&old_s, "Studio/Sub"), None, None, None).unwrap();
        tx.commit().unwrap();
        insert(&conn, lib, "Studio/Dragon.zip");
        let knight = insert(&conn, lib, "Studio/Knight.zip");
        insert(&conn, lib, "Studio/Orc.zip");

        let check = check_relocate(&conn, lib, &new_s, None).unwrap();
        assert_eq!((check.total, check.found, check.missing), (3, if cfg!(windows) { 2 } else { 1 }, 1));
        assert_eq!(check.missing_examples, ["Studio/Orc.zip"]);
        if !cfg!(windows) {
            assert_eq!(check.case_only.len(), 1, "KNIGHT.zip on disk");
        }
        assert_eq!(check.inner, vec![(inner, relpath::join(&new_s, "Studio/Sub"))]);
        let tx = conn.transaction().unwrap();
        relocate(&tx, lib, &new_s, &check, true).unwrap();
        tx.commit().unwrap();
        assert_eq!(get(&conn, lib).unwrap().unwrap().root, clean_root(&new_s));
        assert_eq!(get(&conn, inner).unwrap().unwrap().root, relpath::join(&new_s, "Studio/Sub"));
        if !cfg!(windows) {
            assert_eq!(of(&conn, knight).1, "Studio/KNIGHT.zip", "the spelling on disk");
        }
        assert!(check_relocate(&conn, lib, &dir.path().join("nowhere").to_string_lossy(), None).is_err());
        let tx = conn.transaction().unwrap();
        let other = create(&tx, &dir.path().join("other").to_string_lossy(), None, None, None).unwrap();
        tx.commit().unwrap();
        std::fs::create_dir_all(dir.path().join("other").join("in")).unwrap();
        let err = check_relocate(&conn, lib, &dir.path().join("other").join("in").to_string_lossy(), None).unwrap_err();
        assert!(err.contains("overlaps"), "{err} {other}");
    }
}
