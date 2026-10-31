//! The browser's read queries: table ids and rows, filter-pane values and counts,
//! lookup usage counts, model summaries. A port of `app/services/library_query.py`
//! (`table_ids`, `page_rows`, the predicates of `conditions`), of
//! `app/services/filter_tree.py` and of `lookups.py` / `model_summary.py`.
//!
//! The predicates reproduce SQLAlchemy's SQL, quirks included, because the
//! results must match the Python services exactly (golden-tested):
//!
//! * an exclusion is `NOT IN` / `NOT LIKE` / `NOT (...)`, so a row whose column
//!   is NULL is excluded too (excluding a creator hides models without one);
//! * creator and source compare `lower(column)` (ASCII-only in SQLite) with
//!   values lowered the Unicode way, as Python's `str.lower` does;
//! * `LIKE` patterns are not escaped (`%` and `_` in a folder name are wildcards).
//!
//! Folder and file-type values use `pathlib.WindowsPath` semantics ([`WinPath`]).

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

use rusqlite::types::Value;
use rusqlite::{Connection, params_from_iter};

use super::Result;
use crate::winpath::WinPath;

/// Filter-pane sections, in display order (`FACETS`).
pub const FACETS: &[&str] = &[
    "library", "creator", "source", "tag", "path", "date", "sculptor", "collection", "subcollection", "category",
    "release", "scale", "presupports", "warning", "filetype",
];

/// Keys accepted by the has-value / no-value group filters (`GROUPS`).
pub const GROUPS: &[&str] = &[
    "creator", "tag", "source", "date", "sculptor", "collection", "subcollection", "category",
    "release", "scale", "presupports", "warning", "path",
];

/// Sections `filter_tree` can be asked to show (`Show`).
pub const SHOW: &[&str] = &[
    "library", "author", "tags", "source", "date", "sculptor", "collection", "subcollection", "category", "release",
    "scale", "presupports", "path",
];

/// Facets compared against a lookup FK column (`library`: a model's library,
/// the group of its folder; see [`column_of`]).
const ID_FACETS: &[(&str, &str)] = &[
    ("library", LIBRARY),
    ("sculptor", "sculptor_id"),
    ("collection", "collection_id"),
    ("subcollection", "subcollection_id"),
    ("category", "category_id"),
    ("release", "release_id"),
];

/// `models_view` columns the table can sort by (`SORT_COLUMNS`).
pub const SORT_COLUMNS: &[&str] = &[
    "title", "creator", "source", "sculptor_name", "collection_name", "subcollection_name", "sequence",
    "release_name", "archive_path", "presupports", "created_at", "archive_size", "compression_rate",
    "category_name", "library_name",
];

/// One selected value: text, a lookup id, or a `(year, month)` date.
#[derive(Debug, Clone, PartialEq)]
pub enum FilterValue {
    Text(String),
    Int(i64),
    Date(i64, Option<i64>),
}

/// `FilterSpec`: include/exclude values per facet, has-value / no-value groups,
/// and an optional id restriction. Empty value lists must already be dropped.
#[derive(Debug, Clone, Default)]
pub struct FilterSpec {
    pub include: BTreeMap<String, Vec<FilterValue>>,
    pub exclude: BTreeMap<String, Vec<FilterValue>>,
    pub has_value: BTreeSet<String>,
    pub no_value: BTreeSet<String>,
    pub ids: Option<Vec<i64>>,
}

impl FilterSpec {
    pub fn facet_active(&self, facet: &str) -> bool {
        self.include.contains_key(facet) || self.exclude.contains_key(facet)
    }

    pub fn is_empty(&self) -> bool {
        self.include.is_empty()
            && self.exclude.is_empty()
            && self.has_value.is_empty()
            && self.no_value.is_empty()
            && self.ids.is_none()
    }

    fn texts(values: &[FilterValue]) -> impl Iterator<Item = &str> {
        values.iter().filter_map(|v| if let FilterValue::Text(s) = v { Some(s.as_str()) } else { None })
    }

    fn pruned_paths(&self) -> (Vec<String>, Vec<String>) {
        let get = |m: &BTreeMap<String, Vec<FilterValue>>| {
            prune_ancestor_paths(&Self::texts(m.get("path").map(Vec::as_slice).unwrap_or(&[])).collect::<Vec<_>>())
        };
        (get(&self.include), get(&self.exclude))
    }
}

/// `prune_ancestor_paths`: drop paths whose ancestor is already listed.
pub fn prune_ancestor_paths(paths: &[&str]) -> Vec<String> {
    let mut normalized: Vec<String> = paths.iter().map(|p| p.replace('\\', "/").trim_end_matches('/').to_string()).collect();
    normalized.sort();
    let mut out: Vec<String> = Vec::new();
    for p in normalized {
        if !out.iter().any(|kept| p.starts_with(&format!("{kept}/"))) {
            out.push(p);
        }
    }
    out
}

/// A SQL fragment with its parameters.
#[derive(Debug, Clone, Default)]
struct Sql {
    text: String,
    params: Vec<Value>,
}

impl Sql {
    fn new(text: impl Into<String>) -> Self {
        Sql { text: text.into(), params: Vec::new() }
    }

    fn with(text: impl Into<String>, params: Vec<Value>) -> Self {
        Sql { text: text.into(), params }
    }

    /// `a AND b AND ...` (empty: none).
    fn and(parts: Vec<Sql>) -> Option<Sql> {
        if parts.is_empty() {
            return None;
        }
        let mut out = Sql::default();
        let texts: Vec<String> = parts.iter().map(|p| p.text.clone()).collect();
        out.text = texts.join(" AND ");
        for p in parts {
            out.params.extend(p.params);
        }
        Some(out)
    }
}

fn placeholders(n: usize) -> String {
    vec!["?"; n].join(", ")
}

fn text_values<'a>(values: impl Iterator<Item = &'a str>) -> Vec<Value> {
    values.map(|s| Value::Text(s.to_string())).collect()
}

fn int_values(values: &[FilterValue]) -> Vec<Value> {
    values
        .iter()
        .filter_map(|v| match v {
            FilterValue::Int(i) => Some(Value::Integer(*i)),
            _ => None,
        })
        .collect()
}

const TAGGED: &str = "SELECT model_tags.model_id FROM model_tags JOIN tags ON model_tags.tag_id = tags.id";

/// `conditions(spec, cols, skip=..., path_ids=...)` over table `t` (`models` or `models_view`).
fn conditions(spec: &FilterSpec, t: &str, skip: Option<&str>, path_table: Option<&str>) -> Vec<Sql> {
    let (inc, exc) = (&spec.include, &spec.exclude);
    let want = |facet: &str| Some(facet) != skip && spec.facet_active(facet);
    let mut out = Vec::new();

    if let Some(ids) = &spec.ids {
        let json = serde_json_ids(ids);
        out.push(Sql::with(format!("{t}.id IN (SELECT value FROM json_each(?))"), vec![Value::Text(json)]));
    }

    for facet in ["creator", "source"] {
        if want(facet) {
            for (values, neg) in [(inc.get(facet), ""), (exc.get(facet), "NOT ")] {
                if let Some(vs) = values {
                    let lowered: Vec<String> = FilterSpec::texts(vs).map(str::to_lowercase).collect();
                    out.push(Sql::with(
                        format!("lower({t}.{facet}) {neg}IN ({})", placeholders(lowered.len())),
                        lowered.into_iter().map(Value::Text).collect(),
                    ));
                }
            }
        }
    }

    if want("tag") {
        for (values, neg) in [(inc.get("tag"), ""), (exc.get("tag"), "NOT ")] {
            if let Some(vs) = values {
                let p = text_values(FilterSpec::texts(vs));
                out.push(Sql::with(
                    format!("{t}.id {neg}IN ({TAGGED} WHERE tags.name IN ({}))", placeholders(p.len())),
                    p,
                ));
            }
        }
    }

    if want("path") {
        if let Some(pt) = path_table {
            out.push(Sql::new(format!("{t}.id IN (SELECT {pt}.id FROM {pt})")));
        } else {
            let (p_inc, p_exc) = spec.pruned_paths();
            if let Some(s) = path_matches(t, &p_inc) {
                out.push(s);
            }
            if let Some(s) = path_matches(t, &p_exc) {
                out.push(Sql::with(format!("NOT ({})", s.text), s.params));
            }
        }
    }

    if want("date") {
        for (values, neg) in [(inc.get("date"), false), (exc.get("date"), true)] {
            if let Some(vs) = values {
                let mut parts = Vec::new();
                let mut params = Vec::new();
                for v in vs {
                    if let FilterValue::Date(y, m) = v {
                        match m {
                            None => {
                                parts.push(format!("{t}.release_year = ?"));
                                params.push(Value::Integer(*y));
                            }
                            Some(m) => {
                                parts.push(format!("{t}.release_year = ? AND {t}.release_month = ?"));
                                params.push(Value::Integer(*y));
                                params.push(Value::Integer(*m));
                            }
                        }
                    }
                }
                let joined = parts.iter().map(|p| format!("({p})")).collect::<Vec<_>>().join(" OR ");
                let text = if neg { format!("NOT ({joined})") } else { format!("({joined})") };
                out.push(Sql::with(text, params));
            }
        }
    }

    for &(facet, column) in ID_FACETS {
        if want(facet) {
            for (values, neg) in [(inc.get(facet), ""), (exc.get(facet), "NOT ")] {
                if let Some(vs) = values {
                    let p = int_values(vs);
                    out.push(Sql::with(format!("{} {neg}IN ({})", column_of(t, column), placeholders(p.len())), p));
                }
            }
        }
    }

    if want("scale") {
        for (values, neg) in [(inc.get("scale"), ""), (exc.get("scale"), "NOT ")] {
            if let Some(vs) = values {
                let p = int_values(vs);
                out.push(Sql::with(
                    format!(
                        "{t}.id {neg}IN (SELECT model_scales.model_id FROM model_scales \
                         WHERE model_scales.scale_id IN ({}))",
                        placeholders(p.len())
                    ),
                    p,
                ));
            }
        }
    }

    if want("presupports") {
        for (values, neg) in [(inc.get("presupports"), ""), (exc.get("presupports"), "NOT ")] {
            if let Some(vs) = values {
                let p = text_values(FilterSpec::texts(vs));
                out.push(Sql::with(format!("{t}.presupports {neg}IN ({})", placeholders(p.len())), p));
            }
        }
    }

    if want("warning") {
        if let Some(vs) = inc.get("warning") {
            let p = text_values(FilterSpec::texts(vs));
            let parts = vec![format!("{t}.warning LIKE '%' || ? || '%'"); p.len()];
            out.push(Sql::with(format!("({})", parts.join(" OR ")), p));
        }
        if let Some(vs) = exc.get("warning") {
            let p = text_values(FilterSpec::texts(vs));
            let parts = vec![format!("{t}.warning NOT LIKE '%' || ? || '%'"); p.len()];
            out.push(Sql::with(format!("({})", parts.join(" AND ")), p));
        }
    }

    if want("filetype") {
        if let Some(vs) = inc.get("filetype") {
            let p: Vec<Value> = FilterSpec::texts(vs).map(|e| Value::Text(format!("%{e}"))).collect();
            let parts = vec![format!("lower({t}.rel_path) LIKE ?"); p.len()];
            out.push(Sql::with(format!("({})", parts.join(" OR ")), p));
        }
        if let Some(vs) = exc.get("filetype") {
            let p: Vec<Value> = FilterSpec::texts(vs).map(|e| Value::Text(format!("%{e}"))).collect();
            let parts = vec![format!("lower({t}.rel_path) NOT LIKE ?"); p.len()];
            out.push(Sql::with(format!("({})", parts.join(" AND ")), p));
        }
    }

    for key in &spec.has_value {
        out.push(Sql::new(group_has_value(key, t)));
    }
    for key in &spec.no_value {
        out.push(Sql::new(format!("NOT ({})", group_has_value(key, t))));
    }
    out
}

/// The "column" of a model's library: the library (`library_groups`) of its
/// folder (`models.library_id`).
const LIBRARY: &str = "library_group";

/// SQL for the facet column `col` of table `t`.
fn column_of(t: &str, col: &str) -> String {
    if col == LIBRARY { format!("(SELECT group_id FROM libraries WHERE libraries.id = {t}.library_id)") } else { format!("{t}.{col}") }
}

/// SQL for a model's absolute archive path in table `t` (`models_view` has it
/// as a column).
fn archive_path_of(t: &str) -> String {
    if t == "models_view" { "models_view.archive_path".into() } else { super::schema_sql::archive_path_sql(t) }
}

/// The folders (in `/` form, the folder tree's spelling) of the models of
/// `scope` (a subquery of model ids, or all), with each folder's model count.
const FOLDER_COUNTS: &str = "SELECT posix_join(l.root, g.rel_folder), g.c FROM \
     (SELECT library_id, rel_folder, count(*) AS c FROM models WHERE {scope} GROUP BY library_id, rel_folder) g \
     JOIN libraries l ON l.id = g.library_id";

fn folder_counts_sql(scope: Option<&str>) -> String {
    FOLDER_COUNTS.replace("{scope}", scope.unwrap_or("1"))
}

fn serde_json_ids(ids: &[i64]) -> String {
    let parts: Vec<String> = ids.iter().map(i64::to_string).collect();
    format!("[{}]", parts.join(","))
}

fn path_matches(t: &str, paths: &[String]) -> Option<Sql> {
    if paths.is_empty() {
        return None;
    }
    let params: Vec<Value> =
        paths.iter().map(|p| Value::Text(format!("{}/%", p.trim_end_matches('/')))).collect();
    let parts = vec![format!("replace({}, '\\', '/') LIKE ?", archive_path_of(t)); params.len()];
    Some(Sql::with(format!("({})", parts.join(" OR ")), params))
}

fn group_has_value(key: &str, t: &str) -> String {
    let non_empty = |col: &str| format!("({t}.{col} IS NOT NULL AND {t}.{col} != '')");
    match key {
        "tag" => format!("{t}.id IN (SELECT DISTINCT model_tags.model_id FROM model_tags)"),
        "scale" => format!("{t}.id IN (SELECT DISTINCT model_scales.model_id FROM model_scales)"),
        "date" => format!("{t}.release_year IS NOT NULL"),
        "path" => non_empty("rel_path"),
        k => match ID_FACETS.iter().find(|(f, _)| *f == k) {
            Some((_, col)) => format!("{} IS NOT NULL", column_of(t, col)),
            None => non_empty(k), // creator, source, presupports, warning
        },
    }
}

fn search_condition(text: &str) -> Sql {
    let pat = format!("%{text}%");
    let cols = ["title", "creator", "sculptor_name", "collection_name", "archive_path"];
    let mut parts: Vec<String> = cols.iter().map(|c| format!("lower(models_view.{c}) LIKE lower(?)")).collect();
    parts.push(format!("models_view.id IN ({TAGGED} WHERE lower(tags.name) LIKE lower(?))"));
    let n = parts.len();
    Sql::with(format!("({})", parts.join(" OR ")), vec![Value::Text(pat); n])
}

/// `table_ids`: the ids the browser table shows, in display order (`None`: unordered).
pub fn table_ids(conn: &Connection, spec: &FilterSpec, search: &str, sort: Option<(&str, bool)>) -> Result<Vec<i64>> {
    let mut parts = conditions(spec, "models_view", None, None);
    if !search.is_empty() {
        parts.push(search_condition(search));
    }
    let mut sql = String::from("SELECT models_view.id FROM models_view");
    let mut params = Vec::new();
    if let Some(w) = Sql::and(parts) {
        sql.push_str(&format!(" WHERE {}", w.text));
        params = w.params;
    }
    if let Some((column, descending)) = sort {
        if !SORT_COLUMNS.contains(&column) {
            return Err(rusqlite::Error::InvalidParameterName(format!("not a sortable column: {column}")));
        }
        let dir = if descending { "DESC" } else { "ASC" };
        sql.push_str(&format!(" ORDER BY models_view.{column} {dir} NULLS LAST, models_view.id"));
    }
    let mut st = conn.prepare(&sql)?;
    st.query_map(params_from_iter(params), |r| r.get(0))?.collect()
}

/// A display row; see `page_rows`.
#[derive(Debug, Clone)]
pub struct PageRow {
    pub id: i64,
    pub title: Option<String>,
    pub creator: Option<String>,
    pub source: Option<String>,
    pub release_year: Option<i64>,
    pub release_month: Option<i64>,
    pub archive_path: Option<String>,
    pub presupports: Option<String>,
    pub warning: Option<String>,
    pub archive_size: Option<i64>,
    pub compression_rate: Option<f64>,
    pub sequence: Option<i64>,
    pub created_at: Option<String>,
    pub sculptor_name: Option<String>,
    pub collection_name: Option<String>,
    pub subcollection_name: Option<String>,
    pub category_name: Option<String>,
    pub release_name: Option<String>,
    pub scales: String,
    pub tags: String,
    pub to_print: Option<bool>,
    pub url: Option<String>,
    pub description: Option<String>,
    pub updated_at: Option<String>,
    pub archive_folder: Option<String>,
    pub archive_hash: Option<String>,
    pub is_missing: Option<bool>,
    /// Images catalogued for the model.
    pub image_count: i64,
    pub library_id: i64,
    pub library_name: String,
    /// The archive's path relative to its library (`/` form).
    pub rel_path: String,
}

/// `page_rows`: rows for `ids` in that order (missing ids skipped).
pub fn page_rows(conn: &Connection, ids: &[i64]) -> Result<Vec<PageRow>> {
    let json = Value::Text(serde_json_ids(ids));
    let mut st = conn.prepare(
        "SELECT v.id, v.title, v.creator, v.source, v.release_year, v.release_month, v.archive_path, v.presupports, \
         v.warning, v.archive_size, v.compression_rate, v.sequence, v.created_at, v.sculptor_name, v.collection_name, \
         v.subcollection_name, v.category_name, v.release_name, v.to_print, \
         m.url, m.description, m.updated_at, \
         posix_join((SELECT root FROM libraries WHERE libraries.id = v.library_id), v.rel_folder), m.archive_hash, m.is_missing, \
         (SELECT count(*) FROM images i WHERE i.model_id = v.id), v.library_id, v.library_name, v.rel_path \
         FROM models_view v JOIN models m ON m.id = v.id WHERE v.id IN (SELECT value FROM json_each(?1))",
    )?;
    let mut by_id: HashMap<i64, PageRow> = st
        .query_map([&json], |r| {
            Ok(PageRow {
                id: r.get(0)?,
                title: r.get(1)?,
                creator: r.get(2)?,
                source: r.get(3)?,
                release_year: r.get(4)?,
                release_month: r.get(5)?,
                archive_path: r.get(6)?,
                presupports: r.get(7)?,
                warning: r.get(8)?,
                archive_size: r.get(9)?,
                compression_rate: r.get(10)?,
                sequence: r.get(11)?,
                created_at: r.get(12)?,
                sculptor_name: r.get(13)?,
                collection_name: r.get(14)?,
                subcollection_name: r.get(15)?,
                category_name: r.get(16)?,
                release_name: r.get(17)?,
                scales: String::new(),
                tags: String::new(),
                to_print: r.get(18)?,
                url: r.get(19)?,
                description: r.get(20)?,
                updated_at: r.get(21)?,
                archive_folder: r.get(22)?,
                archive_hash: r.get(23)?,
                is_missing: r.get(24)?,
                image_count: r.get(25)?,
                library_id: r.get(26)?,
                library_name: r.get(27)?,
                rel_path: r.get(28)?,
            })
        })?
        .map(|r| r.map(|row| (row.id, row)))
        .collect::<Result<_>>()?;
    for (join, fk, lookup, is_tags) in
        [("model_tags", "tag_id", "tags", true), ("model_scales", "scale_id", "scales", false)]
    {
        let mut st = conn.prepare(&format!(
            "SELECT {join}.model_id, {lookup}.name FROM {join} JOIN {lookup} ON {join}.{fk} = {lookup}.id \
             WHERE {join}.model_id IN (SELECT value FROM json_each(?1)) ORDER BY {join}.model_id, {lookup}.id"
        ))?;
        let mut rows = st.query([&json])?;
        while let Some(r) = rows.next()? {
            let (mid, name): (i64, String) = (r.get(0)?, r.get(1)?);
            if let Some(row) = by_id.get_mut(&mid) {
                let s = if is_tags { &mut row.tags } else { &mut row.scales };
                if !s.is_empty() {
                    s.push_str(", ");
                }
                s.push_str(&name);
            }
        }
    }
    Ok(ids.iter().filter_map(|id| by_id.remove(id)).collect())
}

/// `usage_counts(kind)`: `{lookup id: models referencing it}`.
pub fn usage_counts(conn: &Connection, kind: &str) -> Result<HashMap<i64, i64>> {
    let (table, col) = usage_column(kind)?;
    let mut st = conn.prepare(&format!(
        "SELECT {table}.{col}, count(*) FROM {table} WHERE {table}.{col} IS NOT NULL GROUP BY {table}.{col}"
    ))?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

fn usage_column(kind: &str) -> Result<(&'static str, &'static str)> {
    Ok(match kind {
        "tag" => ("model_tags", "tag_id"),
        "scale" => ("model_scales", "scale_id"),
        "category" => ("models", "category_id"),
        "collection" => ("models", "collection_id"),
        "subcollection" => ("models", "subcollection_id"),
        "sculptor" => ("models", "sculptor_id"),
        "release" => ("models", "release_id"),
        other => return Err(rusqlite::Error::InvalidParameterName(format!("unknown lookup kind: {other}"))),
    })
}

/// A model summary column value.
pub type SummaryValue = Value;

/// `model_summary`: `(column, value)` pairs, or `None` when the model is gone.
pub fn model_summary(conn: &Connection, model_id: i64) -> Result<Option<Vec<(String, SummaryValue)>>> {
    let mut st = conn.prepare(
        "SELECT v.id, v.title, v.creator, v.source, v.sculptor_name, v.collection_name, \
         v.subcollection_name, v.category_name, v.release_name, v.release_year, \
         v.release_month, v.archive_path, v.archive_size, v.is_missing, \
         (SELECT group_concat(t.name, ', ') FROM model_tags mt \
            JOIN tags t ON t.id = mt.tag_id WHERE mt.model_id = v.id) AS tags, \
         (SELECT count(*) FROM images i WHERE i.model_id = v.id) AS image_count \
         FROM models_view v WHERE v.id = ?1",
    )?;
    let names: Vec<String> = st.column_names().iter().map(|s| s.to_string()).collect();
    let mut rows = st.query([model_id])?;
    let Some(r) = rows.next()? else { return Ok(None) };
    let mut out = Vec::with_capacity(names.len());
    for (i, n) in names.into_iter().enumerate() {
        out.push((n, r.get::<_, Value>(i)?));
    }
    Ok(Some(out))
}

// ── Filter pane ──────────────────────────────────────────────────────────────

/// Count/value keys: `(kind, value)` as the Python payload uses them.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Key {
    Text(String, String),
    Int(String, i64),
    Month(i64, Option<i64>),
    Year(i64),
    Group(String),
}

/// A collection with its subcollections: `(name, id, [(sub name, sub id)])`.
pub type Collection = (String, i64, Vec<(String, i64)>);

/// What `filter_tree_data` returns.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct FilterTree {
    pub counts: HashMap<Key, i64>,
    pub path_counts: Option<HashMap<String, i64>>,
    pub scoped_counts: HashMap<Key, i64>,
    pub sc_path_counts: Option<HashMap<String, i64>>,
    pub creators: Vec<String>,
    pub tags: Vec<String>,
    pub sources: Vec<String>,
    pub dates: Vec<(i64, Option<i64>)>,
    pub libraries: Vec<(String, i64)>,
    pub sculptors: Vec<(String, i64)>,
    pub collections: Vec<(String, i64)>,
    pub sub_by_col: Vec<Collection>,
    pub categories: Vec<(String, i64)>,
    pub releases: Vec<(String, i64)>,
    pub scales: Vec<(String, i64)>,
    pub presupports: Vec<String>,
    pub filetypes: Vec<String>,
    pub sorted_dirs: Vec<String>,
    pub scoped_dirs: Option<Vec<String>>,
    pub new_dirs: Option<Vec<String>>,
    pub scoped: bool,
}

/// Which sections the tree shows (`show` flags).
#[derive(Debug, Clone, Default)]
pub struct Show(pub HashSet<String>);

impl Show {
    fn get(&self, k: &str) -> bool {
        self.0.contains(k)
    }
}

const WARNINGS_ALL: &[&str] = &[
    "Models with presupported stl",
    "Archive without images",
    "Nested archives",
    "Archive without printable files",
    "Unreadable archive",
];
const PATH_TEMP: &str = "_native_filter_path_ids";

fn rows_i64_text(conn: &Connection, sql: &str, params: &[Value]) -> Result<Vec<(Value, i64)>> {
    let mut st = conn.prepare(sql)?;
    st.query_map(params_from_iter(params.iter()), |r| Ok((r.get::<_, Value>(0)?, r.get::<_, i64>(1)?)))?.collect()
}

fn key_for(kind: &str, v: Value) -> Option<Key> {
    match v {
        Value::Text(s) => Some(Key::Text(kind.into(), s)),
        Value::Integer(i) => Some(Key::Int(kind.into(), i)),
        _ => None,
    }
}

/// Python's `str.rstrip("_id")`: strip any trailing `_`, `i`, `d`.
fn rstrip_id(kind: &str) -> &str {
    kind.trim_end_matches(['_', 'i', 'd'])
}

fn add_ancestors(out: &mut HashMap<String, i64>, direct: &HashMap<String, i64>) {
    for (folder, &c) in direct {
        *out.entry(folder.clone()).or_default() += c;
        for a in WinPath::new(folder).ancestors() {
            *out.entry(a).or_default() += c;
        }
    }
}

/// `filter_tree_data(spec, show, show_counts)`: every section in turn on `conn`.
pub fn filter_tree(conn: &Connection, spec: Option<&FilterSpec>, show: &Show, show_counts: bool) -> Result<FilterTree> {
    let mut ft = FilterTree::default();
    let spec = spec.filter(|s| !s.is_empty());
    let scopes = match spec {
        Some(spec) => {
            ft.scoped = true;
            scopes_of(conn, spec)?
        }
        None => Scopes::default(),
    };
    for section in SECTIONS {
        section(conn, &scopes, show, show_counts, &mut ft)?;
    }
    if show_counts && spec.is_some() {
        scoped_counts(conn, &scopes, &mut ft)?;
    }
    Ok(ft)
}

/// The unfiltered tree with its sections read at once, each on a connection of
/// `pool`: on a hard disk the reads of several indexes queue together instead of
/// one after the other. The same tree as [`filter_tree`] without a filter.
pub fn filter_tree_parallel(pool: &super::pool::Pool, show: &Show, show_counts: bool) -> Result<FilterTree> {
    use rayon::prelude::*;
    let none = Scopes::default();
    let parts = SECTIONS
        .par_iter()
        .map(|section| {
            let conn = pool.get()?;
            let mut ft = FilterTree::default();
            section(&conn, &none, show, show_counts, &mut ft)?;
            Ok(ft)
        })
        .collect::<Result<Vec<_>>>()?;
    let mut ft = FilterTree::default();
    for part in parts {
        ft.absorb(part);
    }
    Ok(ft)
}

impl FilterTree {
    /// Take what a section filled in.
    fn absorb(&mut self, part: FilterTree) {
        fn take<T>(into: &mut Option<T>, v: Option<T>) {
            if v.is_some() {
                *into = v;
            }
        }
        fn take_vec<T>(into: &mut Vec<T>, v: Vec<T>) {
            if !v.is_empty() {
                *into = v;
            }
        }
        // Every field named: a new one cannot be forgotten here.
        let FilterTree {
            counts,
            path_counts,
            scoped_counts,
            sc_path_counts,
            creators,
            tags,
            sources,
            dates,
            libraries,
            sculptors,
            collections,
            sub_by_col,
            categories,
            releases,
            scales,
            presupports,
            filetypes,
            sorted_dirs,
            scoped_dirs,
            new_dirs,
            scoped,
        } = part;
        self.counts.extend(counts);
        self.scoped_counts.extend(scoped_counts);
        take(&mut self.path_counts, path_counts);
        take(&mut self.sc_path_counts, sc_path_counts);
        take(&mut self.scoped_dirs, scoped_dirs);
        take(&mut self.new_dirs, new_dirs);
        take_vec(&mut self.creators, creators);
        take_vec(&mut self.tags, tags);
        take_vec(&mut self.sources, sources);
        take_vec(&mut self.dates, dates);
        take_vec(&mut self.libraries, libraries);
        take_vec(&mut self.sculptors, sculptors);
        take_vec(&mut self.collections, collections);
        take_vec(&mut self.sub_by_col, sub_by_col);
        take_vec(&mut self.categories, categories);
        take_vec(&mut self.releases, releases);
        take_vec(&mut self.scales, scales);
        take_vec(&mut self.presupports, presupports);
        take_vec(&mut self.filetypes, filetypes);
        take_vec(&mut self.sorted_dirs, sorted_dirs);
        self.scoped |= scoped;
    }
}

/// One part of the tree: its counts (when asked) and its value list (when shown),
/// so each index is read once.
type Section = fn(&Connection, &Scopes, &Show, bool, &mut FilterTree) -> Result<()>;

const SECTIONS: &[Section] = &[
    library_section,
    creator_section,
    source_section,
    sculptor_section,
    collection_section,
    subcollection_section,
    category_section,
    release_section,
    presupports_section,
    date_section,
    warning_section,
    filetype_section,
    path_section,
    tag_section,
    scale_section,
];

/// Per section, the SELECT of ids matching every other filter (`facet_scopes`).
fn scopes_of(conn: &Connection, spec: &FilterSpec) -> Result<Scopes> {
    let mut scopes = Scopes::default();
    let mut path_table = None;
    if spec.facet_active("path") {
        let (p_inc, p_exc) = spec.pruned_paths();
        conn.execute_batch(&format!("CREATE TEMP TABLE IF NOT EXISTS {PATH_TEMP} (id INTEGER PRIMARY KEY)"))?;
        conn.execute(&format!("DELETE FROM {PATH_TEMP}"), [])?;
        let mut parts = Vec::new();
        if let Some(s) = path_matches("models", &p_inc) {
            parts.push(s);
        }
        if let Some(s) = path_matches("models", &p_exc) {
            parts.push(Sql::with(format!("NOT ({})", s.text), s.params));
        }
        let mut sql = format!("INSERT INTO {PATH_TEMP} (id) SELECT models.id FROM models");
        let mut params = Vec::new();
        if let Some(w) = Sql::and(parts) {
            sql.push_str(&format!(" WHERE {}", w.text));
            params = w.params;
        }
        conn.execute(&sql, params_from_iter(params))?;
        path_table = Some(PATH_TEMP);
    }
    for &facet in FACETS {
        let conds = conditions(spec, "models", Some(facet), path_table);
        scopes
            .0
            .insert(facet, Sql::and(conds).map(|w| Sql::with(format!("SELECT models.id FROM models WHERE {}", w.text), w.params)));
    }
    Ok(scopes)
}

/// Models per value of a `models` column, and the group's total.
fn column_counts(conn: &Connection, col: &str, kind: &str, ft: &mut FilterTree) -> Result<()> {
    let mut total = 0;
    let col = column_of("models", col);
    for (v, c) in rows_i64_text(conn, &format!("SELECT {col}, count(models.id) FROM models WHERE {col} IS NOT NULL GROUP BY {col}"), &[])? {
        if let Some(k) = key_for(kind, v) {
            ft.counts.insert(k, c);
        }
        total += c;
    }
    ft.counts.insert(Key::Group(kind.into()), total);
    Ok(())
}

fn library_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, LIBRARY, "library_id", ft)?;
    }
    if show.get("library") {
        ft.libraries = lookup_list(conn, "library_groups", LIBRARY, scopes.get("library"))?;
    }
    Ok(())
}

fn creator_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "creator", "creator", ft)?;
    }
    if show.get("author") {
        ft.creators = distinct_texts(conn, "creator", scopes.get("creator"))?;
    }
    Ok(())
}

fn source_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "source", "source", ft)?;
    }
    if show.get("source") {
        ft.sources = distinct_texts(conn, "source", scopes.get("source"))?;
    }
    Ok(())
}

fn sculptor_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "sculptor_id", "sculptor_id", ft)?;
    }
    if show.get("sculptor") {
        ft.sculptors = lookup_list(conn, "sculptors", "sculptor_id", scopes.get("sculptor"))?;
    }
    Ok(())
}

fn collection_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "collection_id", "collection_id", ft)?;
    }
    if show.get("collection") {
        ft.collections = lookup_list(conn, "collections", "collection_id", scopes.get("collection"))?;
    }
    Ok(())
}

fn subcollection_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "subcollection_id", "subcollection_id", ft)?;
    }
    if !show.get("subcollection") {
        return Ok(());
    }
    let scoped_ids: Option<HashSet<i64>> = match scopes.get("subcollection") {
        None => None,
        Some(sc) => Some(
            column_ints(
                conn,
                &format!(
                    "SELECT DISTINCT models.subcollection_id FROM models WHERE models.id IN ({}) \
                     AND models.subcollection_id IS NOT NULL",
                    sc.text
                ),
                &sc.params,
            )?
            .into_iter()
            .collect(),
        ),
    };
    let mut st = conn.prepare(
        "SELECT collections.name, collections.id, subcollections.name, subcollections.id FROM collections \
         JOIN subcollections ON subcollections.collection_id = collections.id \
         ORDER BY collections.name, subcollections.name",
    )?;
    let mut order: Vec<i64> = Vec::new();
    let mut by_col: HashMap<i64, (String, Vec<(String, i64)>)> = HashMap::new();
    let mut rows = st.query([])?;
    while let Some(r) = rows.next()? {
        let (cn, cid, sn, sid): (String, i64, String, i64) = (r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?);
        if scoped_ids.as_ref().is_some_and(|s| !s.contains(&sid)) {
            continue;
        }
        by_col.entry(cid).or_insert_with(|| {
            order.push(cid);
            (cn, Vec::new())
        });
        by_col.get_mut(&cid).unwrap().1.push((sn, sid));
    }
    ft.sub_by_col = order
        .into_iter()
        .map(|cid| {
            let (name, subs) = by_col.remove(&cid).unwrap();
            (name, cid, subs)
        })
        .collect();
    Ok(())
}

fn category_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "category_id", "category_id", ft)?;
    }
    if show.get("category") {
        ft.categories = lookup_list(conn, "categories", "category_id", scopes.get("category"))?;
    }
    Ok(())
}

fn release_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "release_id", "release_id", ft)?;
    }
    if show.get("release") {
        ft.releases = lookup_list(conn, "releases", "release_id", scopes.get("release"))?;
    }
    Ok(())
}

fn presupports_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        column_counts(conn, "presupports", "presupports", ft)?;
    }
    if show.get("presupports") {
        ft.presupports = distinct_texts(conn, "presupports", scopes.get("presupports"))?;
    }
    Ok(())
}

fn date_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        let (year_cnt, date_total) = date_counts(conn, None, &mut ft.counts)?;
        for (y, c) in year_cnt {
            ft.counts.insert(Key::Year(y), c);
        }
        ft.counts.insert(Key::Group("date".into()), date_total);
    }
    if show.get("date") {
        let mut sql =
            String::from("SELECT DISTINCT models.release_year, models.release_month FROM models WHERE models.release_year IS NOT NULL");
        let mut params = Vec::new();
        if let Some(sc) = scopes.get("date") {
            sql.push_str(&format!(" AND models.id IN ({})", sc.text));
            params = sc.params.clone();
        }
        sql.push_str(" ORDER BY models.release_year DESC, models.release_month");
        let mut st = conn.prepare(&sql)?;
        ft.dates = st.query_map(params_from_iter(params), |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    }
    Ok(())
}

fn warning_section(conn: &Connection, _: &Scopes, _: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        let warnings = column_texts(conn, "SELECT models.warning FROM models WHERE models.warning IS NOT NULL", &[])?;
        for wt in WARNINGS_ALL {
            ft.counts.insert(Key::Text("warning".into(), wt.to_string()), warnings.iter().filter(|w| w.contains(wt)).count() as i64);
        }
        ft.counts.insert(Key::Group("warning".into()), warnings.iter().filter(|w| !w.is_empty()).count() as i64);
    }
    Ok(())
}

/// File types: counted over every model, listed in the scope (one read without one).
fn filetype_section(conn: &Connection, scopes: &Scopes, _: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    let scope = scopes.get("filetype");
    let all = if counts || scope.is_none() { Some(filetype_suffixes(conn, None, true)?) } else { None };
    if counts && let Some(suffixes) = &all {
        let mut ft_counts: HashMap<String, i64> = HashMap::new();
        for s in suffixes {
            *ft_counts.entry(s.clone()).or_default() += 1;
        }
        for (s, c) in ft_counts {
            ft.counts.insert(Key::Text("filetype".into(), s), c);
        }
        ft.counts.insert(Key::Group("filetype".into()), suffixes.len() as i64);
    }
    let listed = match (scope, all) {
        (None, Some(all)) => all,
        (sc, _) => filetype_suffixes(conn, sc, true)?,
    };
    let mut fts: Vec<String> = listed.into_iter().filter(|s| !s.is_empty()).collect();
    fts.sort();
    fts.dedup();
    ft.filetypes = fts;
    Ok(())
}

/// Folders: counts with their ancestors, and the folder list (one read without a scope).
fn path_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    let scope = scopes.get("path");
    let direct = if counts || (show.get("path") && scope.is_none()) { Some(direct_folders(conn, None)?) } else { None };
    if counts && let Some(direct) = &direct {
        let mut path_counts = HashMap::new();
        add_ancestors(&mut path_counts, direct);
        ft.counts.insert(Key::Group("path".into()), direct.values().sum());
        ft.path_counts = Some(path_counts);
    }
    if !show.get("path") {
        return Ok(());
    }
    match scope {
        Some(sc) => {
            let scope = format!("models.id IN ({})", sc.text);
            let dirs: BTreeSet<String> =
                rows_i64_text(conn, &folder_counts_sql(Some(&scope)), &sc.params)?.into_iter().filter_map(|(v, _)| text_of(v)).collect();
            ft.scoped_dirs = Some(dirs.into_iter().collect());
        }
        None => {
            let dirs: BTreeSet<String> = direct.unwrap_or_default().into_keys().collect();
            let sorted: Vec<String> = dirs.into_iter().collect();
            ft.new_dirs = Some(sorted.clone());
            ft.sorted_dirs = sorted;
        }
    }
    Ok(())
}

fn tag_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        for (v, c) in rows_i64_text(
            conn,
            "SELECT tags.name, count(model_tags.model_id) FROM tags JOIN model_tags ON tags.id = model_tags.tag_id GROUP BY tags.name",
            &[],
        )? {
            if let Some(k) = key_for("tag", v) {
                ft.counts.insert(k, c);
            }
        }
        ft.counts.insert(Key::Group("tag".into()), scalar(conn, "SELECT count(DISTINCT model_tags.model_id) FROM model_tags", &[])?);
    }
    if show.get("tags") {
        ft.tags = match scopes.get("tag") {
            None => column_texts(conn, "SELECT tags.name FROM tags ORDER BY tags.name", &[])?,
            Some(sc) => scoped_tag_names(conn, sc)?,
        };
    }
    Ok(())
}

fn scale_section(conn: &Connection, scopes: &Scopes, show: &Show, counts: bool, ft: &mut FilterTree) -> Result<()> {
    if counts {
        for (v, c) in rows_i64_text(
            conn,
            "SELECT model_scales.scale_id, count(model_scales.model_id) FROM model_scales GROUP BY model_scales.scale_id",
            &[],
        )? {
            if let Some(k) = key_for("scale_id", v) {
                ft.counts.insert(k, c);
            }
        }
        ft.counts
            .insert(Key::Group("scale".into()), scalar(conn, "SELECT count(DISTINCT model_scales.model_id) FROM model_scales", &[])?);
    }
    if show.get("scale") {
        let mut sql = String::from("SELECT scales.name, scales.id FROM scales");
        let mut params = Vec::new();
        if let Some(sc) = scopes.get("scale") {
            sql.push_str(&format!(
                " WHERE scales.id IN (SELECT DISTINCT model_scales.scale_id FROM model_scales \
                 WHERE model_scales.model_id IN ({}))",
                sc.text
            ));
            params = sc.params.clone();
        }
        sql.push_str(" ORDER BY scales.name");
        let mut st = conn.prepare(&sql)?;
        ft.scales = st.query_map(params_from_iter(params), |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    }
    Ok(())
}

fn scoped_tag_names(conn: &Connection, sc: &Sql) -> Result<Vec<String>> {
    column_texts(
        conn,
        &format!(
            "SELECT name FROM (SELECT DISTINCT tags.id, tags.name FROM tags JOIN model_tags \
             ON tags.id = model_tags.tag_id WHERE model_tags.model_id IN ({})) ORDER BY name",
            sc.text
        ),
        &sc.params,
    )
}

/// Per section, the SELECT of ids matching every other filter (`None`: unscoped).
#[derive(Default)]
struct Scopes(HashMap<&'static str, Option<Sql>>);

impl Scopes {
    fn get(&self, section: &str) -> Option<&Sql> {
        self.0.get(section).and_then(Option::as_ref)
    }
}

fn in_scope(col: &str, sc: &Sql) -> Sql {
    Sql::with(format!("{col} IN ({})", sc.text), sc.params.clone())
}

fn scoped_counts(conn: &Connection, scopes: &Scopes, ft: &mut FilterTree) -> Result<()> {
    let scope = |section: &str| scopes.get(section);
    for (section, col, kind) in [
        ("library", LIBRARY, "library_id"),
        ("creator", "creator", "creator"),
        ("source", "source", "source"),
        ("sculptor", "sculptor_id", "sculptor_id"),
        ("collection", "collection_id", "collection_id"),
        ("subcollection", "subcollection_id", "subcollection_id"),
        ("category", "category_id", "category_id"),
        ("release", "release_id", "release_id"),
        ("presupports", "presupports", "presupports"),
    ] {
        if let Some(sc) = scope(section) {
            let cond = in_scope("models.id", sc);
            let rows = rows_i64_text(
                conn,
                &format!(
                    "SELECT {col}, count(models.id) FROM models WHERE {} AND {col} IS NOT NULL \
                     GROUP BY {col}",
                    cond.text,
                    col = column_of("models", col)
                ),
                &cond.params,
            )?;
            let mut total = 0;
            for (v, c) in rows {
                if let Some(k) = key_for(kind, v) {
                    ft.scoped_counts.insert(k, c);
                }
                total += c;
            }
            ft.scoped_counts.insert(Key::Group(rstrip_id(kind).into()), total);
        }
    }
    if let Some(sc) = scope("scale") {
        let cond = in_scope("model_scales.model_id", sc);
        for (v, c) in rows_i64_text(
            conn,
            &format!(
                "SELECT model_scales.scale_id, count(model_scales.model_id) FROM model_scales WHERE {} \
                 GROUP BY model_scales.scale_id",
                cond.text
            ),
            &cond.params,
        )? {
            if let Some(k) = key_for("scale_id", v) {
                ft.scoped_counts.insert(k, c);
            }
        }
        ft.scoped_counts.insert(
            Key::Group("scale".into()),
            scalar(conn, &format!("SELECT count(DISTINCT model_scales.model_id) FROM model_scales WHERE {}", cond.text), &cond.params)?,
        );
    }
    if let Some(sc) = scope("tag") {
        let cond = in_scope("model_tags.model_id", sc);
        for (v, c) in rows_i64_text(
            conn,
            &format!(
                "SELECT tags.name, count(model_tags.model_id) FROM tags JOIN model_tags ON tags.id = model_tags.tag_id \
                 WHERE {} GROUP BY tags.name",
                cond.text
            ),
            &cond.params,
        )? {
            if let Some(k) = key_for("tag", v) {
                ft.scoped_counts.insert(k, c);
            }
        }
        ft.scoped_counts.insert(
            Key::Group("tag".into()),
            scalar(conn, &format!("SELECT count(DISTINCT model_tags.model_id) FROM model_tags WHERE {}", cond.text), &cond.params)?,
        );
    }
    if let Some(sc) = scope("date") {
        let (years, total) = date_counts(conn, Some(sc), &mut ft.scoped_counts)?;
        for (y, c) in years {
            ft.scoped_counts.insert(Key::Year(y), c);
        }
        ft.scoped_counts.insert(Key::Group("date".into()), total);
    }
    if let Some(sc) = scope("warning") {
        let cond = in_scope("models.id", sc);
        let warnings = column_texts(
            conn,
            &format!("SELECT models.warning FROM models WHERE {} AND models.warning IS NOT NULL", cond.text),
            &cond.params,
        )?;
        for wt in &WARNINGS_ALL[..4] {
            ft.scoped_counts
                .insert(Key::Text("warning".into(), wt.to_string()), warnings.iter().filter(|w| w.contains(wt)).count() as i64);
        }
        ft.scoped_counts.insert(Key::Group("warning".into()), warnings.iter().filter(|w| !w.is_empty()).count() as i64);
    }
    if let Some(sc) = scope("filetype") {
        let suffixes = filetype_suffixes(conn, Some(sc), true)?;
        let mut counts: HashMap<String, i64> = HashMap::new();
        for s in &suffixes {
            *counts.entry(s.clone()).or_default() += 1;
        }
        for (s, c) in counts {
            ft.scoped_counts.insert(Key::Text("filetype".into(), s), c);
        }
        ft.scoped_counts.insert(Key::Group("filetype".into()), suffixes.len() as i64);
    }
    if let Some(sc) = scope("path") {
        let direct = direct_folders(conn, Some(sc))?;
        let mut counts = HashMap::new();
        add_ancestors(&mut counts, &direct);
        ft.scoped_counts.insert(Key::Group("path".into()), direct.values().sum());
        ft.sc_path_counts = Some(counts);
    }
    Ok(())
}

/// Month counts into `counts`; returns per-year totals and the overall total.
fn date_counts(conn: &Connection, sc: Option<&Sql>, counts: &mut HashMap<Key, i64>) -> Result<(BTreeMap<i64, i64>, i64)> {
    let mut sql = String::from("SELECT models.release_year, models.release_month, count(models.id) FROM models WHERE ");
    let mut params = Vec::new();
    if let Some(sc) = sc {
        sql.push_str(&format!("models.id IN ({}) AND ", sc.text));
        params = sc.params.clone();
    }
    sql.push_str("models.release_year IS NOT NULL GROUP BY models.release_year, models.release_month");
    let mut st = conn.prepare(&sql)?;
    let rows: Vec<(i64, Option<i64>, i64)> =
        st.query_map(params_from_iter(params), |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<Result<_>>()?;
    let mut years = BTreeMap::new();
    let mut total = 0;
    for (y, m, c) in rows {
        counts.insert(Key::Month(y, m), c);
        *years.entry(y).or_default() += c;
        total += c;
    }
    Ok((years, total))
}

/// `Path(ap).suffix.lower()` of every non-empty archive path (in scope).
fn filetype_suffixes(conn: &Connection, sc: Option<&Sql>, _all: bool) -> Result<Vec<String>> {
    let mut sql = String::from("SELECT models.rel_path FROM models WHERE 1");
    let mut params = Vec::new();
    if let Some(sc) = sc {
        sql.push_str(&format!(" AND models.id IN ({})", sc.text));
        params = sc.params.clone();
    }
    Ok(column_texts(conn, &sql, &params)?
        .into_iter()
        .filter(|ap| !ap.is_empty())
        .map(|ap| WinPath::new(&ap).suffix().to_lowercase())
        .collect())
}

/// Models per folder (absolute, in `/` form).
fn direct_folders(conn: &Connection, sc: Option<&Sql>) -> Result<HashMap<String, i64>> {
    let (scope, params) = match sc {
        Some(sc) => (Some(format!("models.id IN ({})", sc.text)), sc.params.clone()),
        None => (None, Vec::new()),
    };
    let mut direct: HashMap<String, i64> = HashMap::new();
    for (v, c) in rows_i64_text(conn, &folder_counts_sql(scope.as_deref()), &params)? {
        if let Some(f) = text_of(v) {
            *direct.entry(f).or_default() += c;
        }
    }
    Ok(direct)
}

fn text_of(v: Value) -> Option<String> {
    match v {
        Value::Text(t) => Some(t),
        _ => None,
    }
}

fn distinct_texts(conn: &Connection, col: &str, sc: Option<&Sql>) -> Result<Vec<String>> {
    let mut sql = format!("SELECT DISTINCT models.{col} FROM models");
    let mut params = Vec::new();
    let null_filter = col == "presupports";
    let mut wheres = Vec::new();
    if null_filter {
        wheres.push(format!("models.{col} IS NOT NULL"));
    }
    if let Some(sc) = sc {
        wheres.push(format!("models.id IN ({})", sc.text));
        params = sc.params.clone();
    }
    if !wheres.is_empty() {
        sql.push_str(&format!(" WHERE {}", wheres.join(" AND ")));
    }
    sql.push_str(&format!(" ORDER BY models.{col}"));
    Ok(column_opt_texts_p(conn, &sql, &params)?.into_iter().flatten().filter(|s| !s.is_empty()).collect())
}

fn lookup_list(conn: &Connection, table: &str, fk: &str, sc: Option<&Sql>) -> Result<Vec<(String, i64)>> {
    let mut sql = format!("SELECT {table}.name, {table}.id FROM {table}");
    let mut params = Vec::new();
    if let Some(sc) = sc {
        sql.push_str(&format!(
            " WHERE {table}.id IN (SELECT DISTINCT {fk} FROM models WHERE models.id IN ({}) \
             AND {fk} IS NOT NULL)",
            sc.text,
            fk = column_of("models", fk)
        ));
        params = sc.params.clone();
    }
    sql.push_str(&format!(" ORDER BY {table}.name"));
    let mut st = conn.prepare(&sql)?;
    st.query_map(params_from_iter(params), |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

fn scalar(conn: &Connection, sql: &str, params: &[Value]) -> Result<i64> {
    conn.query_row(sql, params_from_iter(params.iter()), |r| r.get::<_, Option<i64>>(0)).map(|v| v.unwrap_or(0))
}

fn column_texts(conn: &Connection, sql: &str, params: &[Value]) -> Result<Vec<String>> {
    let mut st = conn.prepare(sql)?;
    st.query_map(params_from_iter(params.iter()), |r| r.get(0))?.collect()
}

fn column_opt_texts_p(conn: &Connection, sql: &str, params: &[Value]) -> Result<Vec<Option<String>>> {
    let mut st = conn.prepare(sql)?;
    st.query_map(params_from_iter(params.iter()), |r| r.get(0))?.collect()
}

fn column_ints(conn: &Connection, sql: &str, params: &[Value]) -> Result<Vec<i64>> {
    let mut st = conn.prepare(sql)?;
    st.query_map(params_from_iter(params.iter()), |r| r.get(0))?.collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parallel_tree_is_the_sequential_one() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("models.db");
        crate::storage::migrate(&path, &crate::storage::Vectors::default()).unwrap();
        let pool = crate::storage::pool::Pool::new(&path);
        pool.with_tx(|tx| {
            let lib = crate::storage::libraries::create(tx, "F:/lib", crate::storage::libraries::Target::Around, None, None)?;
            tx.execute_batch(
                "INSERT INTO sculptors (name) VALUES ('Alice'), ('Bob');
                 INSERT INTO tags (name) VALUES ('dragon'), ('knight');
                 INSERT INTO scales (name) VALUES ('32mm');",
            )?;
            for (i, (title, creator, rel, sculptor, warning, year)) in [
                ("Red", "Ann", "A/red.7z", Some(1), Some("Nested archives"), Some(2024)),
                ("Blue", "Ann", "A/sub/blue.zip", Some(1), None, Some(2023)),
                ("Knight", "Ben", "B/knight.rar", Some(2), Some("Archive without images\nNested archives"), None),
                ("Loose", "", "loose.7z", None, None, None),
            ]
            .into_iter()
            .enumerate()
            {
                let folder = rel.rsplit_once('/').map_or("", |(f, _)| f);
                tx.execute(
                    "INSERT INTO models (title, creator, library_id, rel_path, rel_folder, archive_hash, sculptor_id, warning, \
                     release_year, release_month, presupports, is_missing, to_print) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?1, ?6, ?7, ?8, 3, ?9, 0, 0)",
                    rusqlite::params![title, creator, lib, rel, folder, sculptor, warning, year, (i % 2 == 0).then_some("Yes")],
                )?;
                let id = tx.last_insert_rowid();
                tx.execute("INSERT INTO model_tags (model_id, tag_id) VALUES (?1, ?2)", [id, 1 + (i as i64 % 2)])?;
                if i < 2 {
                    tx.execute("INSERT INTO model_scales (model_id, scale_id) VALUES (?1, 1)", [id])?;
                }
            }
            Ok::<_, rusqlite::Error>(())
        })
        .unwrap();
        let all = Show(SHOW.iter().map(|s| s.to_string()).collect());
        for (show, counts) in [(&all, true), (&all, false), (&Show::default(), true)] {
            let sequential = pool.with(|c| filter_tree(c, None, show, counts)).unwrap();
            assert_eq!(filter_tree_parallel(&pool, show, counts).unwrap(), sequential, "counts: {counts}");
        }
        let tree = filter_tree_parallel(&pool, &all, true).unwrap();
        assert_eq!(tree.counts[&Key::Text("warning".into(), "Nested archives".into())], 2);
        assert!(tree.creators.contains(&"Ann".to_string()) && tree.creators.contains(&"Ben".to_string()), "{:?}", tree.creators);
        assert_eq!(tree.path_counts.as_ref().unwrap()["F:/lib/A"], 2);
    }

    #[test]
    fn prune_and_rstrip() {
        assert_eq!(prune_ancestor_paths(&["D:/a/b", "D:\\a", "D:/ab/"]), vec!["D:/a", "D:/ab"]);
        assert_eq!(rstrip_id("sculptor_id"), "sculptor");
        assert_eq!(rstrip_id("creator"), "creator");
    }
}
