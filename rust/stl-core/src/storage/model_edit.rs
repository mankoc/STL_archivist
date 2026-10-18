//! Model editing for the editor dialog, the AI tagger and the mass editor: a
//! port of `app/services/model_edit.py` (with `path_pattern.apply_extracted_to_model`).
//!
//! The mass editor's find & replace is reproduced for the cases where Rust and
//! Python's `re` agree exactly: a literal find text (case-insensitive only when
//! it is ASCII) and a replacement without backslashes (Python expands `\1`,
//! `\n`, `\g<0>`, ...). Anything else, and tag lists that would link one tag
//! twice (an IntegrityError in Python), returns [`EditError::Unsupported`]
//! before writing, and the caller uses Python.

use std::collections::{BTreeSet, HashMap};

use rusqlite::{Connection, OptionalExtension, Transaction, params};

use super::Result;
use super::edit::{
    EditError, Row, assign_scales_ext, assign_tags_ext, clean, load_row, lookup, py_int, py_strip, release_date,
    set_collection, set_subcollection, split_list, store_row, subcollection, tag,
};
use crate::path_pattern::{format_release_date, parse_release_date};
use crate::winpath::WinPath;

type EResult<T> = std::result::Result<T, EditError>;

fn sorted_distinct_texts(conn: &Connection, sql: &str) -> Result<Vec<String>> {
    let mut st = conn.prepare(sql)?;
    let mut v: Vec<String> =
        st.query_map([], |r| r.get::<_, Option<String>>(0))?.filter_map(|r| r.transpose()).collect::<Result<_>>()?;
    v.retain(|s| !s.is_empty());
    v.sort();
    Ok(v)
}

fn names(conn: &Connection, table: &str) -> Result<Vec<String>> {
    let mut v = sorted_distinct_texts(conn, &format!("SELECT name FROM {table}"))?;
    v.sort();
    Ok(v)
}

fn dates(conn: &Connection, scope: Option<&str>) -> Result<Vec<String>> {
    let mut sql = String::from(
        "SELECT DISTINCT release_year, release_month FROM models WHERE release_year IS NOT NULL",
    );
    if let Some(ids) = scope {
        sql.push_str(&format!(" AND id IN (SELECT value FROM json_each('{ids}'))"));
    }
    let mut st = conn.prepare(&sql)?;
    let set: BTreeSet<String> = st
        .query_map([], |r| Ok(format_release_date(r.get(0)?, r.get(1)?)))?
        .collect::<Result<_>>()?;
    Ok(set.into_iter().collect())
}

/// `editor_choices`.
pub fn editor_choices(conn: &Connection) -> Result<Vec<(&'static str, Vec<String>)>> {
    let mut tags_st = conn.prepare("SELECT name FROM tags ORDER BY name")?;
    let tags: Vec<String> = tags_st.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    Ok(vec![
        ("titles", sorted_distinct_texts(conn, "SELECT DISTINCT title FROM models")?),
        ("creators", sorted_distinct_texts(conn, "SELECT DISTINCT creator FROM models")?),
        ("sources", sorted_distinct_texts(conn, "SELECT DISTINCT source FROM models")?),
        ("dates", dates(conn, None)?),
        ("tags", tags),
        ("releases", names(conn, "releases")?),
        ("sculptors", names(conn, "sculptors")?),
        ("collections", names(conn, "collections")?),
        ("subcollections", all_names(conn, "subcollections")?),
        ("categories", names(conn, "categories")?),
        ("scales", names(conn, "scales")?),
    ])
}

/// Every name of a table, duplicates kept (subcollection names repeat across collections), sorted.
fn all_names(conn: &Connection, table: &str) -> Result<Vec<String>> {
    let mut st = conn.prepare(&format!("SELECT name FROM {table}"))?;
    let mut v: Vec<String> = st.query_map([], |r| r.get(0))?.collect::<Result<_>>()?;
    v.sort();
    Ok(v)
}

/// `subcollection_names`.
pub fn subcollection_names(conn: &Connection, collection_name: &str) -> Result<Vec<String>> {
    let name = py_strip(collection_name);
    let col: Option<i64> = if name.is_empty() {
        None
    } else {
        conn.query_row("SELECT id FROM collections WHERE name = ?1 ORDER BY id LIMIT 1", [name], |r| r.get(0))
            .optional()?
    };
    let (sql, p): (&str, Vec<i64>) = match col {
        Some(c) => ("SELECT name FROM subcollections WHERE collection_id = ?1 ORDER BY name, id", vec![c]),
        None => ("SELECT name FROM subcollections ORDER BY name, id", vec![]),
    };
    let mut st = conn.prepare(sql)?;
    st.query_map(rusqlite::params_from_iter(p), |r| r.get(0))?.collect()
}

fn lookup_name(conn: &Connection, table: &str, id: Option<i64>) -> Result<Option<String>> {
    match id {
        None => Ok(None),
        Some(i) => conn.query_row(&format!("SELECT name FROM {table} WHERE id = ?1"), [i], |r| r.get(0)).optional(),
    }
}

fn linked(conn: &Connection, model_id: i64, tags: bool) -> Result<Vec<String>> {
    let sql = if tags {
        "SELECT tags.name FROM model_tags JOIN tags ON tags.id = model_tags.tag_id WHERE model_tags.model_id = ?1 \
         ORDER BY tags.id"
    } else {
        "SELECT scales.name FROM model_scales JOIN scales ON scales.id = model_scales.scale_id \
         WHERE model_scales.model_id = ?1 ORDER BY scales.id"
    };
    let mut st = conn.prepare(sql)?;
    st.query_map([model_id], |r| r.get(0))?.collect()
}

/// The editor's view of a model (see `model_for_editor`).
pub struct EditorModel {
    pub row: Row,
    pub archive_path: Option<String>,
    pub release: Option<String>,
    pub sculptor: Option<String>,
    pub collection: Option<String>,
    pub subcollection: Option<String>,
    pub category: Option<String>,
    pub scales: Vec<String>,
    pub tags: Vec<String>,
    pub images: Vec<(i64, bool)>,
}

/// `model_for_editor`.
pub fn model_for_editor(tx: &Connection, model_id: i64) -> Result<Option<EditorModel>> {
    let Some((row, archive_path)) = load_row(tx, model_id)? else { return Ok(None) };
    let mut st = tx.prepare("SELECT id, thumbnail_path FROM images WHERE model_id = ?1 ORDER BY id")?;
    let images = st
        .query_map([model_id], |r| Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.is_some_and(|t| !t.is_empty()))))?
        .collect::<Result<_>>()?;
    Ok(Some(EditorModel {
        release: lookup_name(tx, "releases", row.release_id)?,
        sculptor: lookup_name(tx, "sculptors", row.sculptor_id)?,
        collection: lookup_name(tx, "collections", row.collection_id)?,
        subcollection: lookup_name(tx, "subcollections", row.subcollection_id)?,
        category: lookup_name(tx, "categories", row.category_id)?,
        scales: linked(tx, model_id, false)?,
        tags: linked(tx, model_id, true)?,
        images,
        row,
        archive_path,
    }))
}

/// The editor dialog's form (`save_model_editor`).
#[derive(Debug, Default, Clone)]
pub struct EditorForm {
    pub title: String,
    pub creator: String,
    pub source: String,
    pub date: String,
    pub presupports: String,
    pub url: String,
    pub description: String,
    pub sequence: i64,
    pub release: String,
    pub sculptor: String,
    pub collection: String,
    pub subcollection: String,
    pub category: String,
    pub scales: Vec<String>,
    pub tags: Vec<String>,
}

fn opt_stripped(s: &str) -> Option<String> {
    let t = py_strip(s);
    (!t.is_empty()).then(|| t.to_string())
}

fn set_named(tx: &Transaction, table: &str, name: &str) -> Result<Option<i64>> {
    clean(Some(name)).map(|n| lookup(tx, table, &n)).transpose()
}

/// `save_model_editor`: returns the archive path (`None` when the model is gone).
pub fn save_model_editor(tx: &Transaction, model_id: i64, f: &EditorForm) -> EResult<Option<Option<String>>> {
    let Some((old, archive_path)) = load_row(tx, model_id)? else { return Ok(None) };
    let mut new = old.clone();
    new.title = opt_stripped(&f.title);
    new.creator = opt_stripped(&f.creator);
    new.source = opt_stripped(&f.source);
    (new.release_year, new.release_month) = release_date(py_strip(&f.date))?;
    assign_scales_ext(tx, model_id, &f.scales, true)?;
    new.presupports = opt_stripped(&f.presupports);
    new.url = opt_stripped(&f.url);
    new.description = opt_stripped(&f.description);
    new.sequence = (f.sequence > 0).then_some(f.sequence);
    new.release_id = set_named(tx, "releases", &f.release)?;
    new.sculptor_id = set_named(tx, "sculptors", &f.sculptor)?;
    set_collection(tx, &mut new, clean(Some(&f.collection)).as_deref())?;
    if new.collection_id.is_some() {
        set_subcollection(tx, &mut new, clean(Some(&f.subcollection)).as_deref())?;
    } else {
        new.subcollection_id = None;
    }
    new.category_id = set_named(tx, "categories", &f.category)?;
    assign_tags_ext(tx, model_id, &f.tags, true)?;
    store_row(tx, model_id, &old, &new)?;
    Ok(Some(archive_path))
}

/// What `ai_model_inputs` returns.
pub struct AiInputs {
    pub archive_path: String,
    pub title: Option<String>,
    pub meta: Vec<(String, String)>,
    pub tags: Vec<String>,
    pub images: Vec<(i64, Option<String>)>,
    pub files: Vec<String>,
}

/// `ai_model_inputs`.
pub fn ai_model_inputs(tx: &Connection, model_id: i64) -> Result<Option<AiInputs>> {
    let Some((row, archive_path)) = load_row(tx, model_id)? else { return Ok(None) };
    let mut meta = Vec::new();
    let mut add = |label: &str, value: Option<String>| {
        if let Some(v) = value.filter(|v| !v.is_empty()) {
            meta.push((label.to_string(), py_strip(&v).to_string()));
        }
    };
    add("Title", row.title.clone());
    add("Creator", row.creator.clone());
    add("Sculptor", lookup_name(tx, "sculptors", row.sculptor_id)?);
    add("Category", lookup_name(tx, "categories", row.category_id)?);
    add("Collection", lookup_name(tx, "collections", row.collection_id)?);
    add("Subcollection", lookup_name(tx, "subcollections", row.subcollection_id)?);
    let scales = linked(tx, model_id, false)?;
    add("Scale", (!scales.is_empty()).then(|| scales.join(", ")));
    add("Source", row.source.clone());
    add("Year", row.release_year.map(|y| y.to_string()));
    add("Description", row.description.clone());
    let mut st = tx.prepare("SELECT id, filename FROM images WHERE model_id = ?1 ORDER BY id")?;
    let images = st.query_map([model_id], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
    let mut st = tx.prepare("SELECT filename FROM stl_files WHERE model_id = ?1 ORDER BY filename")?;
    let files: Vec<String> = st
        .query_map([model_id], |r| r.get::<_, Option<String>>(0))?
        .filter_map(|r| r.transpose())
        .collect::<Result<Vec<_>>>()?
        .into_iter()
        .filter(|f| !f.is_empty())
        .collect();
    Ok(Some(AiInputs {
        archive_path: archive_path.unwrap_or_default(),
        title: row.title,
        meta,
        tags: linked(tx, model_id, true)?,
        images,
        files,
    }))
}

/// The AI tagger's suggestions (`ai_commit`); empty strings mean "no value".
#[derive(Debug, Default, Clone)]
pub struct AiFields {
    pub title: String,
    pub creator: String,
    pub description: String,
    pub source: String,
    pub sculptor: String,
    pub collection: String,
    pub subcollection: String,
    pub scale: String,
    pub year: Option<i64>,
}

fn empty(v: &Option<String>) -> bool {
    v.as_deref().is_none_or(str::is_empty)
}

/// `ai_commit`: `false` when the model is gone.
pub fn ai_commit(tx: &Transaction, model_id: i64, f: &AiFields, tags: &[String], overwrite: bool) -> EResult<bool> {
    let Some((old, _)) = load_row(tx, model_id)? else { return Ok(false) };
    let mut new = old.clone();
    if !f.title.is_empty() && (overwrite || empty(&new.title)) {
        new.title = Some(f.title.clone());
    }
    if !f.creator.is_empty() && (overwrite || empty(&new.creator)) {
        new.creator = Some(f.creator.clone());
    }
    if f.year.is_some() && (overwrite || new.release_year.is_none()) {
        new.release_year = f.year;
    }
    if !f.description.is_empty() && (overwrite || empty(&new.description)) {
        new.description = Some(f.description.clone());
    }
    if !f.source.is_empty() && (overwrite || empty(&new.source)) {
        new.source = Some(f.source.clone());
    }
    if !f.sculptor.is_empty() && (overwrite || new.sculptor_id.is_none()) {
        new.sculptor_id = set_named(tx, "sculptors", &f.sculptor)?;
    }
    if !f.collection.is_empty() && (overwrite || new.collection_id.is_none()) {
        set_collection(tx, &mut new, clean(Some(&f.collection)).as_deref())?;
    }
    if !f.subcollection.is_empty() && new.collection_id.is_some() && (overwrite || new.subcollection_id.is_none()) {
        set_subcollection(tx, &mut new, clean(Some(&f.subcollection)).as_deref())?;
    }
    if !f.scale.is_empty() {
        let has_scales: bool =
            tx.query_row("SELECT 1 FROM model_scales WHERE model_id = ?1", [model_id], |_| Ok(())).optional()?.is_some();
        if overwrite || !has_scales {
            assign_scales_ext(tx, model_id, &split_list(Some(&f.scale)), overwrite)?;
        }
    }
    assign_tags_ext(tx, model_id, tags, false)?;
    store_row(tx, model_id, &old, &new)?;
    Ok(true)
}

/// `mass_choices`.
pub fn mass_choices(conn: &Connection, ids: &[i64]) -> Result<Vec<(&'static str, Vec<String>)>> {
    let large = ids.len() > 999;
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let scope = (!large).then_some(json.as_str());
    let tags: Vec<String> = if large {
        names(conn, "tags")?
    } else {
        let mut v = sorted_distinct_texts(
            conn,
            &format!(
                "SELECT DISTINCT tags.name FROM tags JOIN model_tags ON tags.id = model_tags.tag_id \
                 WHERE model_tags.model_id IN (SELECT value FROM json_each('{json}'))"
            ),
        )?;
        v.sort();
        v
    };
    let used = |col: &str| -> Result<Vec<i64>> {
        let mut sql = format!("SELECT DISTINCT {col} FROM models WHERE {col} IS NOT NULL");
        if let Some(j) = scope {
            sql.push_str(&format!(" AND id IN (SELECT value FROM json_each('{j}'))"));
        }
        let mut st = conn.prepare(&sql)?;
        st.query_map([], |r| r.get(0))?.collect()
    };
    let scoped_names = |table: &str, col: &str| -> Result<Vec<String>> {
        let ids = used(col)?;
        if ids.is_empty() {
            return all_names(conn, table);
        }
        let j = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
        let mut v = {
            let mut st = conn.prepare(&format!("SELECT name FROM {table} WHERE id IN (SELECT value FROM json_each(?1))"))?;
            st.query_map([j], |r| r.get::<_, String>(0))?.collect::<Result<Vec<_>>>()?
        };
        v.retain(|s| !s.is_empty());
        v.sort();
        Ok(v)
    };
    Ok(vec![
        ("sources", sorted_distinct_texts(conn, "SELECT DISTINCT source FROM models")?),
        ("creators", sorted_distinct_texts(conn, "SELECT DISTINCT creator FROM models")?),
        ("titles", sorted_distinct_texts(conn, "SELECT DISTINCT title FROM models")?),
        ("dates", dates(conn, scope)?),
        ("tags", tags),
        ("releases", scoped_names("releases", "release_id")?),
        ("collections", scoped_names("collections", "collection_id")?),
        ("subcollections", scoped_names("subcollections", "subcollection_id")?),
        ("categories", names(conn, "categories")?),
        ("scales", names(conn, "scales")?),
        ("sculptors", names(conn, "sculptors")?),
    ])
}

/// `archive_paths`: `{model_id: archive_path}` for the models that exist.
pub fn archive_paths(conn: &Connection, ids: &[i64]) -> Result<HashMap<i64, String>> {
    let json = format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","));
    let mut st =
        conn.prepare("SELECT id, archive_path FROM models WHERE id IN (SELECT value FROM json_each(?1))")?;
    st.query_map([json], |r| Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default())))?.collect()
}

// ── mass save ────────────────────────────────────────────────────────────────

/// The mass editor's find & replace.
#[derive(Debug, Clone)]
pub struct Find {
    pub find: String,
    pub replace: String,
    pub case: bool,
    pub regex: bool,
    pub fields: Vec<String>,
}

/// The mass editor's plan (`mass_save`).
#[derive(Debug, Clone, Default)]
pub struct MassPlan {
    pub model_ids: Vec<i64>,
    pub path_results: HashMap<i64, Vec<(String, String)>>,
    pub overwrite: bool,
    /// `(field, template)`; `None` clears.
    pub manual: Vec<(String, Option<String>)>,
    pub clearing: BTreeSet<String>,
    pub scale: String,
    pub presupports: Option<String>,
    pub release: String,
    pub collection: String,
    pub subcollection: String,
    pub category: String,
    pub sculptor: String,
    pub tags: String,
    pub find: Option<Find>,
    pub trim: bool,
}

/// `resolve_tokens`: `{token}` -> `ctx[token]`, unknown tokens unchanged.
pub fn resolve_tokens(value: &str, ctx: &[(String, String)]) -> String {
    use std::sync::LazyLock;
    static TOKEN: LazyLock<regex::Regex> = LazyLock::new(|| regex::Regex::new(r"\{(\w+)\}").unwrap());
    TOKEN
        .replace_all(value, |c: &regex::Captures| {
            // Later entries win, as ctx.update() does.
            ctx.iter().rev().find(|(k, _)| k == &c[1]).map(|(_, v)| v.clone()).unwrap_or_else(|| c[0].to_string())
        })
        .into_owned()
}

/// A literal find & replace with Python `re` results, or `None` when this
/// port cannot promise them (see the module docs).
struct Replacer {
    find: String,
    case: bool,
    replace: String,
    rx: Option<regex::Regex>,
}

impl Replacer {
    fn new(f: &Find) -> EResult<Self> {
        if f.regex {
            return Err(EditError::Unsupported("regex find & replace".into()));
        }
        if f.replace.contains('\\') {
            return Err(EditError::Unsupported("replacement with backslashes".into()));
        }
        if !f.case && !f.find.is_ascii() {
            return Err(EditError::Unsupported("case-insensitive non-ASCII find".into()));
        }
        let rx = if f.case {
            None
        } else {
            Some(regex::RegexBuilder::new(&regex::escape(&f.find)).case_insensitive(true).build().unwrap())
        };
        Ok(Replacer { find: f.find.clone(), case: f.case, replace: f.replace.clone(), rx })
    }

    fn sub(&self, s: &str) -> String {
        if self.case {
            if self.find.is_empty() {
                // re.sub with an empty pattern inserts at every position.
                let mut out = String::from(self.replace.as_str());
                for ch in s.chars() {
                    out.push(ch);
                    out.push_str(&self.replace);
                }
                return out;
            }
            s.replace(&self.find, &self.replace)
        } else {
            self.rx.as_ref().unwrap().replace_all(s, regex::NoExpand(&self.replace)).into_owned()
        }
    }
}

/// `path_pattern.apply_extracted_to_model` on a row.
fn apply_extracted(tx: &Transaction, row: &mut Row, extracted: &[(String, String)], overwrite: bool) -> EResult<()> {
    let should = |current_empty: bool| overwrite || current_empty;
    for (attr, value) in extracted {
        if value.is_empty() {
            continue;
        }
        match attr.as_str() {
            "source" if should(empty(&row.source)) => row.source = Some(value.clone()),
            "creator" if should(empty(&row.creator)) => row.creator = Some(value.clone()),
            "title" if should(empty(&row.title)) => row.title = Some(value.clone()),
            "url" if should(empty(&row.url)) => row.url = Some(value.clone()),
            "release_date" if should(row.release_year.is_none_or(|y| y == 0)) => {
                if value.chars().any(|c| c.is_numeric() && !c.is_ascii_digit()) {
                    return Err(EditError::Unsupported("date with non-ASCII digits".into()));
                }
                let (y, m) = parse_release_date(value);
                if y.is_some() {
                    row.release_year = y;
                    row.release_month = m;
                }
            }
            "sculptor_name" if should(row.sculptor_id.is_none_or(|i| i == 0)) => {
                row.sculptor_id = Some(lookup(tx, "sculptors", value)?)
            }
            "collection_name" if should(row.collection_id.is_none_or(|i| i == 0)) => {
                row.collection_id = Some(lookup(tx, "collections", value)?)
            }
            "subcollection_name" if should(row.subcollection_id.is_none_or(|i| i == 0)) => match row.collection_id {
                Some(col) => row.subcollection_id = Some(subcollection(tx, value, col)?),
                None => {
                    let sc: Option<i64> = tx
                        .query_row("SELECT id FROM subcollections WHERE name = ?1 ORDER BY id LIMIT 1", [value], |r| r.get(0))
                        .optional()?;
                    if sc.is_some() {
                        row.subcollection_id = sc;
                    }
                }
            },
            "sequence" if should(row.sequence.is_none_or(|s| s == 0)) => {
                if let Some(n) = py_int(value)? {
                    row.sequence = Some(n);
                }
            }
            "release_name" if should(row.release_id.is_none_or(|i| i == 0)) => {
                row.release_id = Some(lookup(tx, "releases", value)?)
            }
            _ => {}
        }
    }
    Ok(())
}

fn set_text_field(row: &mut Row, field: &str, value: Option<String>) {
    match field {
        "title" => row.title = value,
        "creator" => row.creator = value,
        "source" => row.source = value,
        "url" => row.url = value,
        "description" => row.description = value,
        _ => {}
    }
}

fn text_field(row: &Row, field: &str) -> Option<String> {
    match field {
        "title" => row.title.clone(),
        "creator" => row.creator.clone(),
        "source" => row.source.clone(),
        "url" => row.url.clone(),
        "description" => row.description.clone(),
        _ => None,
    }
}

/// Fields the mass editor trims.
const TRIMMABLE: [&str; 5] = ["title", "creator", "source", "url", "description"];

/// `mass_save`: every selected model in one transaction.
pub fn mass_save(tx: &Transaction, plan: &MassPlan) -> EResult<()> {
    let replacer = plan.find.as_ref().map(Replacer::new).transpose()?;
    let clearing = |f: &str| plan.clearing.contains(f);
    let apply_scale = clearing("scale") || !plan.scale.is_empty();
    let apply_presupports = clearing("presupports") || plan.presupports.is_some();
    let presupports_value = if clearing("presupports") { None } else { plan.presupports.clone() };
    let apply_tags = clearing("tags") || !py_strip(&plan.tags).is_empty();
    for &mid in &plan.model_ids {
        let Some((old, archive_path)) = load_row(tx, mid)? else { continue };
        let mut row = old.clone();
        let extracted = plan.path_results.get(&mid).map(Vec::as_slice).unwrap_or(&[]);
        apply_extracted(tx, &mut row, extracted, plan.overwrite)?;

        // token_context
        let ap = archive_path.clone().unwrap_or_default();
        let wp = WinPath::new(&ap);
        let folder = if ap.is_empty() { String::new() } else { wp.parent().name().to_string() };
        let filename = if ap.is_empty() { String::new() } else { wp.stem().to_string() };
        let mut ctx: Vec<(String, String)> = vec![
            ("creator".into(), row.creator.clone().unwrap_or_default()),
            ("source".into(), row.source.clone().unwrap_or_default()),
            ("title".into(), row.title.clone().unwrap_or_default()),
            ("date".into(), format_release_date(row.release_year, row.release_month)),
            ("path".into(), ap.clone()),
            ("folder".into(), folder),
            ("filename".into(), filename),
        ];
        ctx.extend(extracted.iter().cloned());

        for (attr, value) in &plan.manual {
            match value {
                None if attr == "release_date" => {
                    row.release_year = None;
                    row.release_month = None;
                }
                None => set_text_field(&mut row, attr, None),
                Some(t) => {
                    let resolved = resolve_tokens(t, &ctx);
                    if attr == "release_date" {
                        (row.release_year, row.release_month) = release_date(&resolved)?;
                    } else {
                        set_text_field(&mut row, attr, (!resolved.is_empty()).then_some(resolved));
                    }
                }
            }
        }

        if clearing("release") {
            row.release_id = None;
        } else if !plan.release.is_empty() {
            let name = resolve_tokens(&plan.release, &ctx);
            if !name.is_empty() {
                row.release_id = Some(lookup(tx, "releases", &name)?);
            }
        }
        if clearing("collection") {
            row.collection_id = None;
            row.subcollection_id = None;
        } else if !plan.collection.is_empty() {
            let col = lookup(tx, "collections", &plan.collection)?;
            row.collection_id = Some(col);
            if clearing("subcollection") {
                row.subcollection_id = None;
            } else if !plan.subcollection.is_empty() {
                row.subcollection_id = Some(subcollection(tx, &plan.subcollection, col)?);
            }
        }
        if clearing("category") {
            row.category_id = None;
        } else if !plan.category.is_empty() {
            row.category_id = Some(lookup(tx, "categories", &plan.category)?);
        }
        if clearing("sculptor") {
            row.sculptor_id = None;
        } else if !plan.sculptor.is_empty() {
            row.sculptor_id = Some(lookup(tx, "sculptors", &plan.sculptor)?);
        }

        if apply_scale {
            tx.execute("DELETE FROM model_scales WHERE model_id = ?1", [mid])?;
            if !clearing("scale") || !plan.scale.is_empty() {
                let mut seen = std::collections::HashSet::new();
                for name in plan.scale.split(',').map(py_strip).filter(|s| !s.is_empty()) {
                    if !seen.insert(name.to_lowercase()) {
                        continue;
                    }
                    let id = lookup(tx, "scales", name)?;
                    tx.execute("INSERT INTO model_scales (model_id, scale_id) VALUES (?1, ?2)", params![mid, id])?;
                }
            }
        }
        if apply_presupports {
            row.presupports = presupports_value.clone().filter(|v| !v.is_empty());
        }
        if apply_tags {
            tx.execute("DELETE FROM model_tags WHERE model_id = ?1", [mid])?;
            if !(clearing("tags") && py_strip(&plan.tags).is_empty()) {
                let resolved = resolve_tokens(&plan.tags, &ctx);
                let names: Vec<&str> = resolved.split(',').map(py_strip).filter(|s| !s.is_empty()).collect();
                let mut seen = std::collections::HashSet::new();
                for name in names {
                    if !seen.insert(py_strip(name).to_lowercase()) {
                        // Python appends the same tag twice and fails on the unique key.
                        return Err(EditError::Unsupported("tag listed twice".into()));
                    }
                    let id = tag(tx, name)?;
                    tx.execute("INSERT INTO model_tags (model_id, tag_id) VALUES (?1, ?2)", params![mid, id])?;
                }
            }
        }

        if let (Some(r), Some(f)) = (&replacer, &plan.find) {
            for field in f.fields.iter().filter(|x| *x != "collection" && *x != "subcollection") {
                let Some(old_val) = text_field(&row, field).filter(|v| !v.is_empty()) else { continue };
                let new_val = r.sub(&old_val);
                if new_val != old_val {
                    set_text_field(&mut row, field, (!new_val.is_empty()).then_some(new_val));
                }
            }
            if f.fields.iter().any(|x| x == "collection")
                && let Some(name) = super_name(tx, "collections", row.collection_id)?
            {
                let new_name = r.sub(&name);
                if !new_name.is_empty() && new_name != name {
                    row.collection_id = Some(lookup(tx, "collections", &new_name)?);
                }
            }
            if f.fields.iter().any(|x| x == "subcollection")
                && let Some(name) = super_name(tx, "subcollections", row.subcollection_id)?
            {
                let new_name = r.sub(&name);
                if !new_name.is_empty()
                    && new_name != name
                    && let Some(parent) = row.collection_id
                {
                    row.subcollection_id = Some(subcollection(tx, &new_name, parent)?);
                }
            }
        }

        if plan.trim {
            for attr in TRIMMABLE {
                if let Some(v) = text_field(&row, attr) {
                    let t = py_strip(&v);
                    if t != v {
                        set_text_field(&mut row, attr, (!t.is_empty()).then(|| t.to_string()));
                    }
                }
            }
        }
        store_row(tx, mid, &old, &row)?;
    }
    Ok(())
}

fn super_name(tx: &Transaction, table: &str, id: Option<i64>) -> Result<Option<String>> {
    lookup_name(tx, table, id)
}
