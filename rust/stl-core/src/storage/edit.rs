//! Writes to the library: lookup get-or-create, browser cell edits, moved
//! archives, model deletes and pruning unused lookups. A port of
//! `app/database/helpers.py`, `app/services/editing.py` and `app/services/moves.py`,
//! working on model ids inside a caller's transaction instead of on ORM objects.
//!
//! ORM behaviour reproduced:
//!
//! * `updated_at` (SQLAlchemy `onupdate`) changes only when a column of the
//!   model row really changes, not for tag/scale edits or a same-value write;
//! * deleting a model relies on the schema's `ON DELETE CASCADE` (images, STL
//!   files, tag and scale links) and on the TEMP trigger [`super::open`] adds
//!   for `image_vec`, which together leave what the ORM cascade leaves;
//! * names are stripped with Python's `str.strip()` and tags lowercased with
//!   `str.lower()` semantics.

use rusqlite::types::Value;
use rusqlite::{OptionalExtension, Transaction, params};

use super::Result;

/// Python's `str.strip()` (Unicode whitespace, plus the separators it counts).
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}

/// `editing.clean`: stripped, or `None` when empty.
pub fn clean(value: Option<&str>) -> Option<String> {
    let s = py_strip(value.unwrap_or(""));
    (!s.is_empty()).then(|| s.to_string())
}

/// `editing.split_list`: comma-separated items, stripped, empties dropped.
pub fn split_list(text: Option<&str>) -> Vec<String> {
    text.unwrap_or("").split(',').map(py_strip).filter(|s| !s.is_empty()).map(str::to_string).collect()
}

/// `datetime.now(timezone.utc)` as SQLAlchemy stores a `DateTime` in SQLite.
pub fn utc_now_text() -> String {
    let d = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let secs = d.as_secs() as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // Days since 1970-01-01 to a civil date (H. Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02} {:02}:{:02}:{:02}.{:06}",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60,
        d.subsec_micros()
    )
}

#[derive(Debug)]
pub enum EditError {
    Db(rusqlite::Error),
    /// Input this port does not handle.
    Unsupported(String),
    UnknownField(String),
    /// Invalid user input (a find pattern or replacement template).
    Invalid(String),
}

impl From<rusqlite::Error> for EditError {
    fn from(e: rusqlite::Error) -> Self {
        EditError::Db(e)
    }
}

impl std::fmt::Display for EditError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EditError::Db(e) => write!(f, "{e}"),
            EditError::Unsupported(m) => write!(f, "unsupported: {m}"),
            EditError::Invalid(m) => write!(f, "{m}"),
            EditError::UnknownField(m) => write!(f, "unknown field {m}"),
        }
    }
}

impl std::error::Error for EditError {}

// ── get-or-create (helpers.py) ───────────────────────────────────────────────

fn get_or_create(tx: &Transaction, table: &str, name: &str) -> Result<i64> {
    if let Some(id) =
        tx.query_row(&format!("SELECT id FROM {table} WHERE name = ?1 LIMIT 1"), [name], |r| r.get(0)).optional()?
    {
        return Ok(id);
    }
    tx.execute(&format!("INSERT INTO {table} (name) VALUES (?1)"), [name])?;
    Ok(tx.last_insert_rowid())
}

/// `get_or_create_tag`: names stripped and lowercased.
pub fn tag(tx: &Transaction, name: &str) -> Result<i64> {
    get_or_create(tx, "tags", &py_strip(name).to_lowercase())
}

/// Exact-name lookups: `"sculptors"`, `"collections"`, `"categories"`, `"releases"`, `"scales"`.
pub fn lookup(tx: &Transaction, table: &str, name: &str) -> Result<i64> {
    debug_assert!(["sculptors", "collections", "categories", "releases", "scales"].contains(&table));
    get_or_create(tx, table, name)
}

/// `get_or_create_subcollection`: unique per parent collection.
pub fn subcollection(tx: &Transaction, name: &str, collection_id: i64) -> Result<i64> {
    if let Some(id) = tx
        .query_row(
            "SELECT id FROM subcollections WHERE name = ?1 AND collection_id = ?2 LIMIT 1",
            params![name, collection_id],
            |r| r.get(0),
        )
        .optional()?
    {
        return Ok(id);
    }
    tx.execute("INSERT INTO subcollections (collection_id, name) VALUES (?1, ?2)", params![collection_id, name])?;
    Ok(tx.last_insert_rowid())
}

// ── cell edits (editing.py) ──────────────────────────────────────────────────

/// `editing.CELL_FIELDS`.
pub const CELL_FIELDS: &[&str] = &[
    "title", "creator", "source", "date", "tags", "sculptor", "collection", "subcollection", "category", "sequence",
    "release", "scale", "presupports", "to_print",
];

/// The editable model columns, as loaded and as edited.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub title: Option<String>,
    pub creator: Option<String>,
    pub source: Option<String>,
    pub release_year: Option<i64>,
    pub release_month: Option<i64>,
    pub sequence: Option<i64>,
    pub presupports: Option<String>,
    pub to_print: bool,
    pub sculptor_id: Option<i64>,
    pub collection_id: Option<i64>,
    pub subcollection_id: Option<i64>,
    pub category_id: Option<i64>,
    pub release_id: Option<i64>,
    pub url: Option<String>,
    pub description: Option<String>,
}

/// The model's editable columns and its archive path.
pub(crate) fn load_row(tx: &rusqlite::Connection, model_id: i64) -> Result<Option<(Row, Option<String>)>> {
    tx.query_row(
        "SELECT title, creator, source, release_year, release_month, sequence, presupports, to_print, \
         sculptor_id, collection_id, subcollection_id, category_id, release_id, archive_path, url, description \
         FROM models WHERE id = ?1",
        [model_id],
        |r| {
            Ok((
                Row {
                    title: r.get(0)?,
                    creator: r.get(1)?,
                    source: r.get(2)?,
                    release_year: r.get(3)?,
                    release_month: r.get(4)?,
                    sequence: r.get(5)?,
                    presupports: r.get(6)?,
                    to_print: r.get::<_, Option<bool>>(7)?.unwrap_or(false),
                    sculptor_id: r.get(8)?,
                    collection_id: r.get(9)?,
                    subcollection_id: r.get(10)?,
                    category_id: r.get(11)?,
                    release_id: r.get(12)?,
                    url: r.get(14)?,
                    description: r.get(15)?,
                },
                r.get(13)?,
            ))
        },
    )
    .optional()
}

/// Write the columns that differ (and `updated_at`, only if any does).
pub(crate) fn store_row(tx: &Transaction, model_id: i64, old: &Row, new: &Row) -> Result<()> {
    let mut sets: Vec<&str> = Vec::new();
    let mut vals: Vec<Value> = Vec::new();
    macro_rules! diff {
        ($f:ident, $col:literal, $conv:expr) => {
            if old.$f != new.$f {
                sets.push(concat!($col, " = ?"));
                vals.push($conv(new.$f.clone()));
            }
        };
    }
    let text = |v: Option<String>| v.map(Value::Text).unwrap_or(Value::Null);
    let int = |v: Option<i64>| v.map(Value::Integer).unwrap_or(Value::Null);
    diff!(title, "title", text);
    diff!(creator, "creator", text);
    diff!(source, "source", text);
    diff!(release_year, "release_year", int);
    diff!(release_month, "release_month", int);
    diff!(sequence, "sequence", int);
    diff!(presupports, "presupports", text);
    diff!(to_print, "to_print", |b: bool| Value::Integer(b as i64));
    diff!(sculptor_id, "sculptor_id", int);
    diff!(collection_id, "collection_id", int);
    diff!(subcollection_id, "subcollection_id", int);
    diff!(category_id, "category_id", int);
    diff!(release_id, "release_id", int);
    diff!(url, "url", text);
    diff!(description, "description", text);
    if sets.is_empty() {
        return Ok(());
    }
    sets.push("updated_at = ?");
    vals.push(Value::Text(utc_now_text()));
    vals.push(Value::Integer(model_id));
    tx.execute(&format!("UPDATE models SET {} WHERE id = ?", sets.join(", ")), rusqlite::params_from_iter(vals))?;
    Ok(())
}

/// `parse_release_date` for typed input.
pub(crate) fn release_date(value: &str) -> std::result::Result<(Option<i64>, Option<i64>), EditError> {
    Ok(crate::path_pattern::parse_release_date(value))
}

/// Python's `int(value)` for a stripped string (sign, decimal digits of any
/// script, single underscores between digits); `Ok(None)` where `int()` raises.
pub(crate) fn py_int(value: &str) -> std::result::Result<Option<i64>, EditError> {
    Ok(crate::pyunicode::py_int(value))
}

/// Names of the model's links (`tags` or `scales`), lowercased.
fn linked_names_lower(tx: &Transaction, model_id: i64, tags: bool) -> Result<std::collections::HashSet<String>> {
    let sql = if tags {
        "SELECT tags.name FROM model_tags JOIN tags ON tags.id = model_tags.tag_id WHERE model_tags.model_id = ?1"
    } else {
        "SELECT scales.name FROM model_scales JOIN scales ON scales.id = model_scales.scale_id \
         WHERE model_scales.model_id = ?1"
    };
    let mut st = tx.prepare(sql)?;
    let names: Vec<String> = st.query_map([model_id], |r| r.get(0))?.collect::<Result<_>>()?;
    Ok(names.into_iter().map(|n| n.to_lowercase()).collect())
}

/// `editing.assign_tags`: names stripped, lowercased, de-duplicated; with
/// `replace` the model's tags are cleared first, otherwise they are kept.
pub(crate) fn assign_tags_ext(tx: &Transaction, model_id: i64, names: &[String], replace: bool) -> Result<()> {
    if replace {
        tx.execute("DELETE FROM model_tags WHERE model_id = ?1", [model_id])?;
    }
    let mut seen = linked_names_lower(tx, model_id, true)?;
    for raw in names {
        let name = py_strip(raw).to_lowercase();
        if name.is_empty() || !seen.insert(name.clone()) {
            continue;
        }
        let id = tag(tx, &name)?;
        tx.execute("INSERT INTO model_tags (model_id, tag_id) VALUES (?1, ?2)", params![model_id, id])?;
    }
    Ok(())
}

/// `editing.assign_scales`: de-duplicated case-insensitively, rows keep the
/// casing they were first created with; `replace` as for tags.
pub(crate) fn assign_scales_ext(tx: &Transaction, model_id: i64, names: &[String], replace: bool) -> Result<()> {
    if replace {
        tx.execute("DELETE FROM model_scales WHERE model_id = ?1", [model_id])?;
    }
    let mut seen = linked_names_lower(tx, model_id, false)?;
    for raw in names {
        let name = py_strip(raw);
        if name.is_empty() || !seen.insert(name.to_lowercase()) {
            continue;
        }
        let id = lookup(tx, "scales", name)?;
        tx.execute("INSERT INTO model_scales (model_id, scale_id) VALUES (?1, ?2)", params![model_id, id])?;
    }
    Ok(())
}

/// `apply_cell_edit` on one model. Returns `false` when the model does not exist.
pub fn apply_cell_edit(tx: &Transaction, model_id: i64, field: &str, text: Option<&str>) -> std::result::Result<bool, EditError> {
    if !CELL_FIELDS.contains(&field) {
        return Err(EditError::UnknownField(field.into()));
    }
    let Some((old, _)) = load_row(tx, model_id)? else { return Ok(false) };
    let mut new = old.clone();
    let value = clean(text);
    match field {
        "title" => new.title = value,
        "creator" => new.creator = value,
        "source" => new.source = value,
        "date" => (new.release_year, new.release_month) = release_date(value.as_deref().unwrap_or(""))?,
        "tags" => assign_tags_ext(tx, model_id, &split_list(value.as_deref()), true)?,
        "scale" => assign_scales_ext(tx, model_id, &split_list(value.as_deref()), true)?,
        "sculptor" => new.sculptor_id = value.map(|n| lookup(tx, "sculptors", &n)).transpose()?,
        "category" => new.category_id = value.map(|n| lookup(tx, "categories", &n)).transpose()?,
        "release" => new.release_id = value.map(|n| lookup(tx, "releases", &n)).transpose()?,
        "collection" => set_collection(tx, &mut new, value.as_deref())?,
        "subcollection" => set_subcollection(tx, &mut new, value.as_deref())?,
        "sequence" => match value {
            None => new.sequence = None,
            Some(v) => {
                if let Some(n) = py_int(&v)? {
                    new.sequence = Some(n);
                }
            }
        },
        "presupports" => new.presupports = value,
        "to_print" => new.to_print = value.as_deref() == Some("1"),
        _ => unreachable!(),
    }
    store_row(tx, model_id, &old, &new)?;
    Ok(true)
}

/// `editing.set_collection` on a row: a subcollection of another collection is cleared.
pub(crate) fn set_collection(tx: &Transaction, row: &mut Row, name: Option<&str>) -> Result<()> {
    match name {
        None => {
            row.collection_id = None;
            row.subcollection_id = None;
        }
        Some(n) => {
            let col = lookup(tx, "collections", n)?;
            row.collection_id = Some(col);
            if let Some(sub) = row.subcollection_id {
                let parent: Option<i64> =
                    tx.query_row("SELECT collection_id FROM subcollections WHERE id = ?1", [sub], |r| r.get(0)).optional()?;
                if parent != Some(col) {
                    row.subcollection_id = None;
                }
            }
        }
    }
    Ok(())
}

/// `editing.set_subcollection` on a row: created under the row's collection, or,
/// without one, an existing subcollection of that name and its collection.
pub(crate) fn set_subcollection(tx: &Transaction, row: &mut Row, name: Option<&str>) -> Result<()> {
    match name {
        None => row.subcollection_id = None,
        Some(n) => match row.collection_id {
            Some(col) => row.subcollection_id = Some(subcollection(tx, n, col)?),
            None => {
                let found: Option<(i64, i64)> = tx
                    .query_row(
                        "SELECT id, collection_id FROM subcollections WHERE name = ?1 ORDER BY id LIMIT 1",
                        [n],
                        |r| Ok((r.get(0)?, r.get(1)?)),
                    )
                    .optional()?;
                if let Some((sub, col)) = found {
                    row.collection_id = Some(col);
                    row.subcollection_id = Some(sub);
                }
            }
        },
    }
    Ok(())
}

/// A batch of `(model_id, field, text)` edits in order (the browser's
/// `_CellEditRunnable`). Returns the archive paths of the models that exist.
pub fn apply_cell_edits(
    tx: &Transaction,
    edits: &[(i64, String, Option<String>)],
) -> std::result::Result<Vec<String>, EditError> {
    let mut paths = Vec::new();
    for (id, field, text) in edits {
        if apply_cell_edit(tx, *id, field, text.as_deref())?
            && let Some((_, Some(p))) = load_row(tx, *id)?
        {
            paths.push(p);
        }
    }
    Ok(paths)
}

// ── moves.py ─────────────────────────────────────────────────────────────────

/// `archive_folder_for`: `PurePosixPath(path.replace("\\", "/")).parent.as_posix()`.
pub fn archive_folder_for(archive_path: &str) -> String {
    let norm = archive_path.replace('\\', "/");
    let absolute = norm.starts_with('/');
    // PurePosixPath keeps exactly two leading slashes, collapses three or more.
    let anchor = if norm.starts_with("//") && !norm.starts_with("///") {
        "//"
    } else if absolute {
        "/"
    } else {
        ""
    };
    let mut parts: Vec<&str> = norm.split('/').filter(|p| !p.is_empty() && *p != ".").collect();
    parts.pop();
    if parts.is_empty() {
        return if anchor.is_empty() { ".".into() } else { anchor.into() };
    }
    format!("{anchor}{}", parts.join("/"))
}

/// `moves.relocate`: point the model at its archive's new location.
pub fn relocate(
    tx: &Transaction,
    model_id: i64,
    new_path: &str,
    archive_hash: &str,
    size: Option<i64>,
    mtime_ns: Option<i64>,
    compression: Option<f64>,
) -> Result<()> {
    let folder = archive_folder_for(new_path);
    let now = utc_now_text();
    match compression {
        Some(c) => tx.execute(
            "UPDATE models SET archive_path = ?1, archive_hash = ?2, archive_size = ?3, archive_mtime_ns = ?4, \
             archive_folder = ?5, is_missing = 0, compression_rate = ?6, updated_at = ?7 WHERE id = ?8",
            params![new_path, archive_hash, size, mtime_ns, folder, c, now, model_id],
        )?,
        None => tx.execute(
            "UPDATE models SET archive_path = ?1, archive_hash = ?2, archive_size = ?3, archive_mtime_ns = ?4, \
             archive_folder = ?5, is_missing = 0, updated_at = ?6 WHERE id = ?7",
            params![new_path, archive_hash, size, mtime_ns, folder, now, model_id],
        )?,
    };
    Ok(())
}

fn thumbnail_refs(tx: &Transaction, model_id: i64) -> Result<Vec<String>> {
    let mut st = tx.prepare(
        "SELECT thumbnail_path FROM images WHERE model_id = ?1 AND thumbnail_path IS NOT NULL \
         AND thumbnail_path != '' ORDER BY id",
    )?;
    st.query_map([model_id], |r| r.get(0))?.collect()
}

/// `moves.delete_model`: delete the model (images, STL files and links
/// cascade); returns its thumbnail references for deletion after commit.
pub fn delete_model(tx: &Transaction, model_id: i64) -> Result<Vec<String>> {
    let refs = thumbnail_refs(tx, model_id)?;
    tx.execute("DELETE FROM models WHERE id = ?1", [model_id])?;
    Ok(refs)
}

/// `moves.merge_into_missing`: keep `old_id` (its edited metadata) at the
/// location of `new_id`, the shallow record created for the moved archive,
/// which is deleted. Returns `new_id`'s thumbnail references.
pub fn merge_into_missing(tx: &Transaction, old_id: i64, new_id: i64) -> Result<Vec<String>> {
    type Loc = (String, String, Option<i64>, Option<i64>, Option<f64>);
    let loc: Option<Loc> = tx
        .query_row(
            "SELECT archive_path, archive_hash, archive_size, archive_mtime_ns, compression_rate FROM models WHERE id = ?1",
            [new_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)),
        )
        .optional()?;
    let Some((path, hash, size, mtime, compression)) = loc else { return Ok(Vec::new()) };
    let refs = delete_model(tx, new_id)?;
    relocate(tx, old_id, &path, &hash, size, mtime, compression)?;
    Ok(refs)
}

/// `helpers.prune_unused_lookups`: `(table, rows deleted)` for non-zero counts.
pub fn prune_unused_lookups(tx: &Transaction) -> Result<Vec<(&'static str, usize)>> {
    let statements: [(&str, &str); 6] = [
        (
            "subcollections",
            "DELETE FROM subcollections WHERE subcollections.id NOT IN \
             (SELECT models.subcollection_id FROM models WHERE models.subcollection_id IS NOT NULL)",
        ),
        (
            "collections",
            "DELETE FROM collections WHERE collections.id NOT IN \
             (SELECT models.collection_id FROM models WHERE models.collection_id IS NOT NULL) \
             AND collections.id NOT IN (SELECT subcollections.collection_id FROM subcollections)",
        ),
        (
            "sculptors",
            "DELETE FROM sculptors WHERE sculptors.id NOT IN \
             (SELECT models.sculptor_id FROM models WHERE models.sculptor_id IS NOT NULL)",
        ),
        (
            "categories",
            "DELETE FROM categories WHERE categories.id NOT IN \
             (SELECT models.category_id FROM models WHERE models.category_id IS NOT NULL)",
        ),
        (
            "releases",
            "DELETE FROM releases WHERE releases.id NOT IN \
             (SELECT models.release_id FROM models WHERE models.release_id IS NOT NULL)",
        ),
        ("scales", "DELETE FROM scales WHERE scales.id NOT IN (SELECT model_scales.scale_id FROM model_scales)"),
    ];
    let mut out = Vec::new();
    for (label, sql) in statements {
        let n = tx.execute(sql, [])?;
        if n > 0 {
            out.push((label, n));
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn folder_and_int_rules() {
        assert_eq!(archive_folder_for("F:\\STL\\x.7z"), "F:/STL");
        assert_eq!(archive_folder_for("F:/x.7z"), "F:");
        assert_eq!(archive_folder_for("x.7z"), ".");
        assert_eq!(archive_folder_for("//nas/share/x.7z"), "//nas/share");
        assert_eq!(archive_folder_for("/x.7z"), "/");
        assert_eq!(py_int("1_000").unwrap(), Some(1000));
        assert_eq!(py_int("+3").unwrap(), Some(3));
        assert_eq!(py_int("1__0").unwrap(), None);
        assert_eq!(py_int("x").unwrap(), None);
        assert_eq!(py_int("٣").unwrap(), Some(3)); // Unicode digits, as in Python
        assert_eq!(split_list(Some(" a, ,b ,")), vec!["a", "b"]);
        let now = utc_now_text();
        assert_eq!(now.len(), 26, "{now}");
    }
}
