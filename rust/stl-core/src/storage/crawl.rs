//! The crawl's database phases: a port of `CrawlerWorker`'s Phase 1 lookup,
//! Phase 2 diff (with early move matching), Phase 4 upsert and the post-crawl
//! reconciliation of missing records (`app/crawler/crawler.py`).
//!
//! The Python side still computes what needs Python semantics or libraries:
//! metadata, rule results, path-pattern matches and their parsed dates and
//! numbers, thumbnails (precomputed JPEGs and dHashes) and CLIP vectors. This
//! module applies them in the order the ORM would, so ids and values match:
//! SQLAlchemy flushes a mapper's INSERTs before its DELETEs, and SQLite reuses
//! the highest rowid, so image rows are inserted, then deleted, in that order.

#![allow(clippy::type_complexity)] // row tuples mirror the Python records

use std::collections::hash_map::Entry;
use std::collections::{HashMap, HashSet};
use std::path::Path;

use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension, Transaction, params};

use super::edit::{lookup, py_strip, relocate, subcollection, tag, utc_now_text};
use super::vectors;
use crate::thumbstore::ThumbStore;
use crate::winpath::WinPath;

pub const WARNING_UNREADABLE: &str = "Unreadable archive";
pub const WARNING_LOW_COMPRESSION: &str = "Archive with low compression rate";

type Result<T> = rusqlite::Result<T>;

fn json_ids(ids: &[i64]) -> String {
    format!("[{}]", ids.iter().map(i64::to_string).collect::<Vec<_>>().join(","))
}

fn json_strs(v: &[String]) -> String {
    serde_json::to_string(v).unwrap_or_else(|_| "[]".into())
}

/// `(size, mtime_ns)` of a file, as `int(st.st_size)`, `int(st.st_mtime_ns)`.
pub fn stat(path: &str) -> Option<(i64, i64)> {
    let md = std::fs::metadata(path).ok()?;
    let mtime = md.modified().ok()?;
    let ns = match mtime.duration_since(std::time::UNIX_EPOCH) {
        Ok(d) => d.as_nanos() as i64,
        Err(e) => -(e.duration().as_nanos() as i64),
    };
    Some((md.len() as i64, ns))
}

// ── Phase 1 ──────────────────────────────────────────────────────────────────

/// `{archive_path: (archive_hash, archive_size, archive_mtime_ns)}` of the given paths.
pub fn existing(conn: &Connection, paths: &[String]) -> Result<Vec<(String, String, Option<i64>, Option<i64>)>> {
    let mut st = conn.prepare(
        "SELECT archive_path, archive_hash, archive_size, archive_mtime_ns FROM models \
         WHERE archive_path IN (SELECT value FROM json_each(?1))",
    )?;
    st.query_map([json_strs(paths)], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?.collect()
}

// ── Phase 2 ──────────────────────────────────────────────────────────────────

#[derive(Debug, Default)]
pub struct Diff {
    pub to_extract: Vec<String>,
    pub stl_backfill: Vec<String>,
    /// `archive_path → model id`, including early-moved records.
    pub existing: Vec<(String, i64)>,
    /// Paths whose hash is a fallback fingerprint (unreadable archive).
    pub unreadable: Vec<String>,
    /// `(hash, model id, new path)` of the missing records relocated here.
    pub moved: Vec<(String, i64, String)>,
    /// Move-candidate hashes used up (relocated or not).
    pub consumed: Vec<String>,
}

fn append_warning(current: Option<String>, text: &str) -> Option<String> {
    match current {
        Some(w) if !w.is_empty() => Some(if w.contains(text) { w } else { format!("{w}\n{text}") }),
        _ => Some(text.to_string()),
    }
}

fn set_stat(tx: &Transaction, id: i64, path: &str) -> Result<()> {
    if let Some((size, mtime)) = stat(path) {
        tx.execute(
            "UPDATE models SET archive_size = ?2, archive_mtime_ns = ?3, updated_at = ?4 \
             WHERE id = ?1 AND (archive_size IS NOT ?2 OR archive_mtime_ns IS NOT ?3)",
            params![id, size, mtime, utc_now_text()],
        )?;
    }
    Ok(())
}

/// Phase 2 for one batch: `batch` is `(path, hash)` in crawl order;
/// `candidates` maps content hashes to missing record ids (`moves.index_by_hash`).
pub fn diff(
    tx: &Transaction,
    batch: &[(String, String)],
    force: bool,
    store_stl: bool,
    candidates: &HashMap<String, i64>,
) -> Result<Diff> {
    let paths: Vec<String> = batch.iter().map(|(p, _)| p.clone()).collect();
    let mut st = tx.prepare(
        "SELECT archive_path, id, archive_hash, warning, stl_list_hash, \
         EXISTS (SELECT 1 FROM stl_files f WHERE f.model_id = models.id) \
         FROM models WHERE archive_path IN (SELECT value FROM json_each(?1))",
    )?;
    type Ex = (i64, String, Option<String>, Option<String>, bool);
    let mut existing: HashMap<String, Ex> = st
        .query_map([json_strs(&paths)], |r| Ok((r.get(0)?, (r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?))))?
        .collect::<Result<_>>()?;
    drop(st);
    let mut new_hash_counts: HashMap<&str, usize> = HashMap::new();
    for (p, h) in batch {
        if !existing.contains_key(p) {
            *new_hash_counts.entry(h.as_str()).or_default() += 1;
        }
    }
    let mut out = Diff::default();
    let mut used: HashSet<&str> = HashSet::new();
    for (p, h) in batch {
        if h.starts_with("fallback:") {
            out.unreadable.push(p.clone());
            if let Some(ex) = existing.get_mut(p) {
                let w = append_warning(ex.2.clone(), WARNING_UNREADABLE);
                if w != ex.2 {
                    tx.execute("UPDATE models SET warning = ?2, updated_at = ?3 WHERE id = ?1", params![ex.0, w, utc_now_text()])?;
                    ex.2 = w;
                }
            }
        }
        let Some(ex) = existing.get(p).cloned() else {
            if new_hash_counts.get(h.as_str()) == Some(&1)
                && !used.contains(h.as_str())
                && let Some(&id) = candidates.get(h)
            {
                used.insert(h);
                out.consumed.push(h.clone());
                let old: Option<Option<i64>> =
                    tx.query_row("SELECT archive_size FROM models WHERE id = ?1", [id], |r| r.get(0)).optional()?;
                if let Some(old_size) = old {
                    let (size, mtime) = match stat(p) {
                        Some((s, m)) => (Some(s), Some(m)),
                        None => (old_size, None),
                    };
                    relocate(tx, id, p, h, size, mtime, None)?;
                    out.moved.push((h.clone(), id, p.clone()));
                    out.existing.push((p.clone(), id));
                    existing.insert(p.clone(), (id, h.clone(), None, None, true));
                    continue;
                }
            }
            out.to_extract.push(p.clone());
            continue;
        };
        let (id, ex_hash, _warning, stl_list_hash, has_stl) = ex;
        out.existing.push((p.clone(), id));
        if force {
            out.to_extract.push(p.clone());
        } else if ex_hash.is_empty() {
            tx.execute("UPDATE models SET archive_hash = ?2, updated_at = ?3 WHERE id = ?1", params![id, h, utc_now_text()])?;
            set_stat(tx, id, p)?;
        } else if &ex_hash != h {
            out.to_extract.push(p.clone());
            set_stat(tx, id, p)?;
        } else if store_stl && !has_stl && stl_list_hash.as_deref() != Some(h.as_str()) {
            out.stl_backfill.push(p.clone());
        }
    }
    Ok(out)
}

// ── Phase 4 ──────────────────────────────────────────────────────────────────

/// What the crawl configuration contributes to an upsert.
#[derive(Debug, Clone, Default)]
pub struct UpsertConfig {
    pub store_stl: bool,
    pub warn_low_compression: bool,
    pub low_compression_threshold: f64,
    pub force_creator: Option<String>,
    pub force_source: Option<String>,
    pub force_sculptor: Option<String>,
    pub force_category: Option<String>,
    pub force_collection: Option<String>,
    pub force_release: Option<String>,
    pub force_scale: Option<String>,
    /// `parse_release_date(force_date)` when `force_date` is set.
    pub force_date: Option<(Option<i64>, Option<i64>)>,
    pub embed_dim: usize,
}

/// A path-pattern match with Python's parses of its date and number.
#[derive(Debug, Clone, Default)]
pub struct Extracted {
    pub fields: Vec<(String, String)>,
    /// `parse_release_date(fields["release_date"])`.
    pub release_date: Option<(Option<i64>, Option<i64>)>,
    /// `int(fields["sequence"])`, `None` where it raises.
    pub sequence: Option<i64>,
}

/// One extracted image: name, thumbnail JPEG, formatted dHash, CLIP vector blob.
pub type Thumb = (String, Option<Vec<u8>>, Option<String>, Option<Vec<u8>>);

/// What a new or changed archive contributes (Python computes it).
#[derive(Debug, Clone, Default)]
pub struct Changed {
    pub title: Option<String>,
    pub creator: Option<String>,
    pub source: Option<String>,
    pub release_year: Option<i64>,
    pub release_month: Option<i64>,
    pub description: Option<String>,
    pub release: Option<String>,
    pub tags: Vec<String>,
    pub presupports: String,
    pub warnings: Option<String>,
    pub scales: Vec<String>,
    pub uncompressed_size: Option<i64>,
    /// The thumbnail candidates (`image_names[:max_thumbnails]`), in order.
    pub thumbs: Vec<Thumb>,
    /// Images from the archive's folders: name (the file path), JPEG, dHash.
    pub folder_images: Vec<(String, Option<Vec<u8>>, Option<String>)>,
    pub stl_info: Option<Vec<(String, Option<i64>)>>,
}

#[derive(Debug, Clone)]
pub struct UpsertInput {
    pub path: String,
    pub hash: String,
    pub existing: Option<i64>,
    pub extracted: Option<Extracted>,
    /// `None`: unchanged, only the pattern is applied.
    pub changed: Option<Changed>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Upserted {
    pub thumbnails: usize,
    pub folder_images: usize,
}

/// The model columns an upsert reads and writes.
#[derive(Debug, Clone, Default, PartialEq)]
struct ModelRow {
    archive_hash: String,
    archive_folder: Option<String>,
    archive_size: Option<i64>,
    archive_mtime_ns: Option<i64>,
    compression_rate: Option<f64>,
    presupports: Option<String>,
    warning: Option<String>,
    title: Option<String>,
    creator: Option<String>,
    source: Option<String>,
    url: Option<String>,
    release_year: Option<i64>,
    release_month: Option<i64>,
    sequence: Option<i64>,
    description: Option<String>,
    stl_list_hash: Option<String>,
    sculptor_id: Option<i64>,
    collection_id: Option<i64>,
    subcollection_id: Option<i64>,
    category_id: Option<i64>,
    release_id: Option<i64>,
}

const ROW_COLUMNS: &str = "archive_hash, archive_folder, archive_size, archive_mtime_ns, compression_rate, presupports, \
     warning, title, creator, source, url, release_year, release_month, sequence, description, stl_list_hash, \
     sculptor_id, collection_id, subcollection_id, category_id, release_id";

fn load(tx: &Transaction, id: i64) -> Result<ModelRow> {
    tx.query_row(&format!("SELECT {ROW_COLUMNS} FROM models WHERE id = ?1"), [id], |r| {
        Ok(ModelRow {
            archive_hash: r.get(0)?,
            archive_folder: r.get(1)?,
            archive_size: r.get(2)?,
            archive_mtime_ns: r.get(3)?,
            compression_rate: r.get(4)?,
            presupports: r.get(5)?,
            warning: r.get(6)?,
            title: r.get(7)?,
            creator: r.get(8)?,
            source: r.get(9)?,
            url: r.get(10)?,
            release_year: r.get(11)?,
            release_month: r.get(12)?,
            sequence: r.get(13)?,
            description: r.get(14)?,
            stl_list_hash: r.get(15)?,
            sculptor_id: r.get(16)?,
            collection_id: r.get(17)?,
            subcollection_id: r.get(18)?,
            category_id: r.get(19)?,
            release_id: r.get(20)?,
        })
    })
}

fn row_values(r: &ModelRow) -> Vec<Value> {
    let t = |v: &Option<String>| v.clone().map(Value::Text).unwrap_or(Value::Null);
    let i = |v: &Option<i64>| v.map(Value::Integer).unwrap_or(Value::Null);
    vec![
        Value::Text(r.archive_hash.clone()),
        t(&r.archive_folder),
        i(&r.archive_size),
        i(&r.archive_mtime_ns),
        r.compression_rate.map(Value::Real).unwrap_or(Value::Null),
        t(&r.presupports),
        t(&r.warning),
        t(&r.title),
        t(&r.creator),
        t(&r.source),
        t(&r.url),
        i(&r.release_year),
        i(&r.release_month),
        i(&r.sequence),
        t(&r.description),
        t(&r.stl_list_hash),
        i(&r.sculptor_id),
        i(&r.collection_id),
        i(&r.subcollection_id),
        i(&r.category_id),
        i(&r.release_id),
    ]
}

/// Write the columns that differ, with `updated_at` only when any does.
fn store_model(tx: &Transaction, id: i64, old: &ModelRow, new: &ModelRow) -> Result<()> {
    let names: Vec<&str> = ROW_COLUMNS.split(',').map(str::trim).collect();
    let (ov, nv) = (row_values(old), row_values(new));
    let mut sets = Vec::new();
    let mut vals = Vec::new();
    for ((name, o), n) in names.iter().zip(ov).zip(nv) {
        if o != n {
            sets.push(format!("{name} = ?"));
            vals.push(n);
        }
    }
    if sets.is_empty() {
        return Ok(());
    }
    sets.push("updated_at = ?".into());
    vals.push(Value::Text(utc_now_text()));
    vals.push(Value::Integer(id));
    tx.execute(&format!("UPDATE models SET {} WHERE id = ?", sets.join(", ")), rusqlite::params_from_iter(vals))?;
    Ok(())
}

fn insert(tx: &Transaction, path: &str, r: &ModelRow) -> Result<i64> {
    let now = utc_now_text();
    let mut vals = row_values(r);
    vals.push(Value::Text(path.to_string()));
    vals.push(Value::Text(now.clone()));
    vals.push(Value::Text(now));
    let n = vals.len();
    let marks: Vec<String> = (1..=n).map(|k| format!("?{k}")).collect();
    tx.execute(
        &format!(
            "INSERT INTO models ({ROW_COLUMNS}, archive_path, created_at, updated_at, is_missing, to_print) \
             VALUES ({}, 0, 0)",
            marks.join(", ")
        ),
        rusqlite::params_from_iter(vals),
    )?;
    Ok(tx.last_insert_rowid())
}

fn falsy_id(v: Option<i64>) -> bool {
    v.is_none_or(|i| i == 0)
}

fn falsy_text(v: &Option<String>) -> bool {
    v.as_deref().is_none_or(str::is_empty)
}

/// `apply_extracted_to_model(..., overwrite=False)` after a flush (the crawler
/// flushes first, so foreign keys read as assigned).
fn apply_pattern(tx: &Transaction, row: &mut ModelRow, ex: &Extracted) -> Result<()> {
    for (attr, value) in &ex.fields {
        if value.is_empty() {
            continue;
        }
        match attr.as_str() {
            "source" if falsy_text(&row.source) => row.source = Some(value.clone()),
            "creator" if falsy_text(&row.creator) => row.creator = Some(value.clone()),
            "title" if falsy_text(&row.title) => row.title = Some(value.clone()),
            "url" if falsy_text(&row.url) => row.url = Some(value.clone()),
            "release_date" if falsy_id(row.release_year) => {
                if let Some((Some(y), m)) = ex.release_date {
                    row.release_year = Some(y);
                    row.release_month = m;
                }
            }
            "sculptor_name" if falsy_id(row.sculptor_id) => row.sculptor_id = Some(lookup(tx, "sculptors", value)?),
            "collection_name" if falsy_id(row.collection_id) => {
                row.collection_id = Some(lookup(tx, "collections", value)?)
            }
            "subcollection_name" if falsy_id(row.subcollection_id) => match row.collection_id {
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
            "sequence" if falsy_id(row.sequence) => {
                if let Some(n) = ex.sequence {
                    row.sequence = Some(n);
                }
            }
            "release_name" if falsy_id(row.release_id) => row.release_id = Some(lookup(tx, "releases", value)?),
            _ => {}
        }
    }
    Ok(())
}

/// Python's `round(x, 4)`: correctly rounded, ties to even on the exact value.
fn round4(x: f64) -> f64 {
    format!("{x:.4}").parse().unwrap_or(x)
}

fn link_all(tx: &Transaction, table: &str, col: &str, model_id: i64, ids: &[i64]) -> Result<()> {
    tx.execute(&format!("DELETE FROM {table} WHERE model_id = ?1"), [model_id])?;
    for id in ids {
        tx.execute(&format!("INSERT INTO {table} (model_id, {col}) VALUES (?1, ?2)"), [model_id, *id])?;
    }
    Ok(())
}

/// Image ids from stored thumbnail references (`delete_thumbnails`).
fn ref_ids(refs: &[String]) -> Vec<u64> {
    refs.iter()
        .filter_map(|r| {
            if !r.is_empty() && r.bytes().all(|b| b.is_ascii_digit()) {
                r.parse().ok()
            } else {
                WinPath::new(r).stem().parse().ok()
            }
        })
        .collect()
}

/// Phase 4 for one archive (`_upsert_archive`), inside the caller's savepoint.
pub fn upsert(
    tx: &Transaction,
    store: Option<&ThumbStore>,
    cfg: &UpsertConfig,
    input: &UpsertInput,
) -> Result<Upserted> {
    let Some(ch) = &input.changed else {
        // Unchanged: re-apply the pattern without overwriting.
        if let (Some(id), Some(ex)) = (input.existing, &input.extracted) {
            let old = load(tx, id)?;
            let mut row = old.clone();
            apply_pattern(tx, &mut row, ex)?;
            store_model(tx, id, &old, &row)?;
        }
        return Ok(Upserted::default());
    };
    let old = match input.existing {
        Some(id) => Some(load(tx, id)?),
        None => None,
    };
    let hash_changed = old.as_ref().is_some_and(|o| o.archive_hash != input.hash);
    let mut row = old.clone().unwrap_or_default();
    row.archive_hash = input.hash.clone();
    row.archive_folder = Some(WinPath::new(&input.path).parent().as_posix());
    if let Some((size, mtime)) = stat(&input.path) {
        row.archive_size = Some(size);
        row.archive_mtime_ns = Some(mtime);
    }
    if let (Some(unc), Some(size)) = (ch.uncompressed_size, row.archive_size)
        && unc > 0
    {
        row.compression_rate = Some(round4(size as f64 / unc as f64));
    }
    // _detect_rules
    row.presupports = Some(ch.presupports.clone());
    row.warning = ch.warnings.clone().filter(|w| !w.is_empty());
    let mut scales: Option<Vec<i64>> = None;
    if !ch.scales.is_empty() {
        scales = Some(ch.scales.iter().map(|s| lookup(tx, "scales", s)).collect::<Result<_>>()?);
    }
    if cfg.warn_low_compression && row.compression_rate.is_some_and(|r| r >= cfg.low_compression_threshold) {
        row.warning = append_warning(row.warning.take(), WARNING_LOW_COMPRESSION);
    }
    if input.hash.starts_with("fallback:") {
        row.warning = append_warning(row.warning.take(), WARNING_UNREADABLE);
    }
    // _assign_metadata
    row.title = ch.title.clone();
    row.creator = ch.creator.clone();
    row.source = ch.source.clone();
    row.release_year = ch.release_year;
    row.release_month = ch.release_month;
    row.description = ch.description.clone();
    if let Some(rel) = ch.release.as_deref().filter(|r| !r.is_empty()) {
        row.release_id = Some(lookup(tx, "releases", rel)?);
    }
    // Forced values
    let set = |v: &Option<String>| v.as_deref().filter(|s| !s.is_empty()).map(str::to_string);
    if let Some(v) = set(&cfg.force_creator) {
        row.creator = Some(v);
    }
    if let Some(v) = set(&cfg.force_source) {
        row.source = Some(v);
    }
    if let Some(v) = set(&cfg.force_sculptor) {
        row.sculptor_id = Some(lookup(tx, "sculptors", &v)?);
    }
    if let Some(v) = set(&cfg.force_category) {
        row.category_id = Some(lookup(tx, "categories", &v)?);
    }
    if let Some(v) = set(&cfg.force_collection) {
        row.collection_id = Some(lookup(tx, "collections", &v)?);
    }
    if let Some(v) = set(&cfg.force_release) {
        row.release_id = Some(lookup(tx, "releases", &v)?);
    }
    if let Some(v) = set(&cfg.force_scale) {
        scales = Some(vec![lookup(tx, "scales", &v)?]);
    }
    if let Some((Some(y), m)) = cfg.force_date
        && y != 0
    {
        row.release_year = Some(y);
        row.release_month = m;
    }
    if let Some(ex) = &input.extracted {
        apply_pattern(tx, &mut row, ex)?;
    }
    let mut tag_ids = Vec::new();
    for t in &ch.tags {
        if !py_strip(t).is_empty() {
            tag_ids.push(tag(tx, t)?);
        }
    }
    if cfg.store_stl && ch.stl_info.is_some() {
        row.stl_list_hash = Some(input.hash.clone());
    }

    let model_id = match (input.existing, &old) {
        (Some(id), Some(o)) => {
            store_model(tx, id, o, &row)?;
            id
        }
        _ => insert(tx, &input.path, &row)?,
    };
    if let Some(ids) = &scales {
        link_all(tx, "model_scales", "scale_id", model_id, ids)?;
    }
    link_all(tx, "model_tags", "tag_id", model_id, &tag_ids)?;

    let mut out = Upserted::default();
    if input.existing.is_none() || hash_changed {
        let mut pending: Vec<i64> = Vec::new(); // image deletes the next flush runs
        let flush = |pending: &mut Vec<i64>| -> Result<()> {
            for id in pending.drain(..) {
                tx.execute("DELETE FROM images WHERE id = ?1", [id])?;
            }
            Ok(())
        };
        if hash_changed {
            let mut st = tx.prepare("SELECT id, thumbnail_path FROM images WHERE model_id = ?1 ORDER BY id")?;
            let old_imgs: Vec<(i64, Option<String>)> =
                st.query_map([model_id], |r| Ok((r.get(0)?, r.get(1)?)))?.collect::<Result<_>>()?;
            let refs: Vec<String> = old_imgs.iter().filter_map(|(_, t)| t.clone()).filter(|t| !t.is_empty()).collect();
            if let Some(s) = store {
                let _ = s.delete_many(&ref_ids(&refs));
            }
            pending.extend(old_imgs.iter().map(|(id, _)| *id));
        }
        let mut ids = Vec::with_capacity(ch.thumbs.len());
        for (name, ..) in &ch.thumbs {
            tx.execute("INSERT INTO images (model_id, filename) VALUES (?1, ?2)", params![model_id, name])?;
            ids.push(tx.last_insert_rowid());
        }
        flush(&mut pending)?;
        let mut resolved: Vec<(i64, &Vec<u8>, &Option<String>, &Option<Vec<u8>>)> = Vec::new();
        for (id, (_, jpeg, dhash, vec)) in ids.iter().zip(&ch.thumbs) {
            match jpeg.as_ref().filter(|j| !j.is_empty()) {
                Some(j) => resolved.push((*id, j, dhash, vec)),
                None => pending.push(*id),
            }
        }
        if let Some(s) = store {
            let items: Vec<(u64, &[u8])> = resolved.iter().map(|(id, j, ..)| (*id as u64, j.as_slice())).collect();
            let _ = s.put_many(&items);
        }
        for (id, _, dhash, _) in &resolved {
            tx.execute("UPDATE images SET thumbnail_path = ?2, dhash = ?3 WHERE id = ?1", params![id, id.to_string(), dhash])?;
        }
        let vecs: Vec<(i64, Vec<u8>)> =
            resolved.iter().filter_map(|(id, _, _, v)| v.as_ref().map(|v| (*id, v.clone()))).collect();
        if !vecs.is_empty() {
            vectors::store(tx, cfg.embed_dim, &vecs)?;
        }
        out.thumbnails = resolved.len();
        for (name, jpeg, dhash) in &ch.folder_images {
            tx.execute("INSERT INTO images (model_id, filename) VALUES (?1, ?2)", params![model_id, name])?;
            let id = tx.last_insert_rowid();
            flush(&mut pending)?;
            let stored = match (jpeg.as_ref().filter(|j| !j.is_empty()), store) {
                (Some(j), Some(s)) => s.put_many(&[(id as u64, j.as_slice())]).is_ok(),
                _ => false,
            };
            if stored {
                tx.execute("UPDATE images SET thumbnail_path = ?2, dhash = ?3 WHERE id = ?1", params![id, id.to_string(), dhash])?;
                out.folder_images += 1;
            } else {
                pending.push(id);
            }
        }
        flush(&mut pending)?;
    }
    if cfg.store_stl
        && let Some(info) = &ch.stl_info
    {
        tx.execute("DELETE FROM stl_files WHERE model_id = ?1", [model_id])?;
        for (name, size) in info {
            tx.execute(
                "INSERT INTO stl_files (model_id, filename, file_size, archive_hash) VALUES (?1, ?2, ?3, ?4)",
                params![model_id, name, size, input.hash],
            )?;
        }
    }
    Ok(out)
}

// ── Post-crawl ───────────────────────────────────────────────────────────────

/// A missing record: `(id, archive_path, archive_hash or "", file name, archive_size)`.
pub type Missing = (i64, String, String, String, Option<i64>);

/// `_collect_missing`: records whose archive is gone, plus those already flagged.
pub fn collect_missing(conn: &Connection) -> Result<Vec<Missing>> {
    use rayon::prelude::*;
    let mut st = conn.prepare("SELECT id, archive_path, archive_hash, archive_size, is_missing FROM models")?;
    let rows: Vec<(i64, Option<String>, Option<String>, Option<i64>, bool)> =
        st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get::<_, Option<bool>>(4)?.unwrap_or(false))))?
            .collect::<Result<_>>()?;
    let absent: HashSet<i64> = rows
        .par_iter()
        .filter(|(_, p, _, _, missing)| !missing && p.as_deref().is_some_and(|p| !p.is_empty()))
        .filter(|(_, p, ..)| !Path::new(p.as_deref().unwrap_or_default()).exists())
        .map(|(id, ..)| *id)
        .collect();
    Ok(rows
        .into_iter()
        .filter(|(id, p, _, _, missing)| p.as_deref().is_some_and(|p| !p.is_empty()) && (*missing || absent.contains(id)))
        .map(|(id, p, h, size, _)| {
            let p = p.unwrap_or_default();
            let name = WinPath::new(&p).name().to_string();
            (id, p, h.unwrap_or_default(), name, size)
        })
        .collect())
}

/// `_reconcile_moved_files` matching and merging: returns `(matched (id, old
/// path, new path), skipped ambiguous file names, thumbnail refs)`.
pub fn reconcile(
    tx: &Transaction,
    missing: &[Missing],
    new_paths: &[String],
) -> Result<(Vec<(i64, String, String)>, Vec<String>, Vec<String>)> {
    let mut st = tx.prepare(
        "SELECT archive_path, id, archive_hash, archive_size FROM models \
         WHERE archive_path IN (SELECT value FROM json_each(?1)) ORDER BY id",
    )?;
    let news: Vec<(String, i64, Option<String>, Option<i64>)> =
        st.query_map([json_strs(new_paths)], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?.collect::<Result<_>>()?;
    drop(st);
    let new_ids: HashMap<String, i64> = news.iter().map(|(p, id, ..)| (p.clone(), *id)).collect();
    let mut hash_to_path: HashMap<String, String> = HashMap::new();
    let mut hash_collision: HashSet<String> = HashSet::new();
    let mut ns_to_path: HashMap<(String, i64), String> = HashMap::new();
    let mut ns_collision: HashSet<(String, i64)> = HashSet::new();
    for (path, _, h, size) in &news {
        if let Some(h) = h.as_ref().filter(|h| !h.is_empty()) {
            if hash_to_path.contains_key(h) {
                hash_collision.insert(h.clone());
            } else {
                hash_to_path.insert(h.clone(), path.clone());
            }
        }
        if let Some(size) = size {
            let key = (WinPath::new(path).name().to_string(), *size);
            match ns_to_path.entry(key) {
                Entry::Occupied(e) => {
                    ns_collision.insert(e.key().clone());
                }
                Entry::Vacant(v) => {
                    v.insert(path.clone());
                }
            }
        }
    }
    let mut missing_collision: HashSet<&str> = HashSet::new();
    let mut seen: HashSet<&str> = HashSet::new();
    for (_, _, h, _, _) in missing {
        if !h.is_empty() && !seen.insert(h.as_str()) {
            missing_collision.insert(h.as_str());
        }
    }
    let mut used: HashSet<String> = HashSet::new();
    let (mut matched, mut skipped, mut refs) = (Vec::new(), Vec::new(), Vec::new());
    for (id, old_path, h, name, size) in missing {
        let mut target: Option<String> = None;
        if !h.is_empty() && !hash_collision.contains(h) && !missing_collision.contains(h.as_str())
            && let Some(c) = hash_to_path.get(h)
            && !used.contains(c)
        {
            target = Some(c.clone());
        }
        if target.is_none()
            && !name.is_empty()
            && let Some(size) = size
        {
            let key = (name.clone(), *size);
            if !ns_collision.contains(&key) {
                if let Some(c) = ns_to_path.get(&key).filter(|c| !used.contains(*c)) {
                    target = Some(c.clone());
                }
            } else {
                skipped.push(name.clone());
            }
        }
        let Some(new_path) = target else { continue };
        let exists = tx.query_row("SELECT 1 FROM models WHERE id = ?1", [id], |_| Ok(())).optional()?.is_some();
        let Some(&new_id) = new_ids.get(&new_path) else { continue };
        if !exists {
            continue;
        }
        refs.extend(super::edit::merge_into_missing(tx, *id, new_id)?);
        used.insert(new_path.clone());
        matched.push((*id, old_path.clone(), new_path));
    }
    Ok((matched, skipped, refs))
}

/// `_mark_records_missing`: in-scope records are deleted (or, with `defer`,
/// flagged); others are flagged missing. Returns `(deleted paths, marked
/// paths, deferred count, thumbnail refs)`.
pub fn mark_missing(
    tx: &Transaction,
    missing: &[Missing],
    source_dir: &str,
    defer: bool,
) -> Result<(Vec<String>, Vec<String>, usize, Vec<String>)> {
    let source = WinPath::new(source_dir).as_posix().to_lowercase();
    let in_scope = |p: &str| {
        let mut cur = WinPath::new(p);
        loop {
            if cur.as_posix().to_lowercase() == source {
                return true;
            }
            let parent = cur.parent();
            if parent == cur {
                return false;
            }
            cur = parent;
        }
    };
    let (mut deleted, mut marked, mut deferred, mut refs) = (Vec::new(), Vec::new(), 0, Vec::new());
    for (id, path, ..) in missing {
        let flag: Option<bool> = tx
            .query_row("SELECT is_missing FROM models WHERE id = ?1", [id], |r| r.get::<_, Option<bool>>(0))
            .optional()?
            .map(|f| f.unwrap_or(false));
        let Some(already) = flag else { continue };
        if in_scope(path) {
            if defer {
                if !already {
                    tx.execute("UPDATE models SET is_missing = 1, updated_at = ?2 WHERE id = ?1", params![id, utc_now_text()])?;
                }
                deferred += 1;
            } else {
                refs.extend(super::edit::delete_model(tx, *id)?);
                deleted.push(path.clone());
            }
        } else if !already {
            tx.execute("UPDATE models SET is_missing = 1, updated_at = ?2 WHERE id = ?1", params![id, utc_now_text()])?;
            marked.push(path.clone());
        }
    }
    Ok((deleted, marked, deferred, refs))
}

/// `_record_crawled_folder`.
pub fn record_folder(
    tx: &Transaction,
    path: &str,
    pattern: Option<&str>,
    crawled_at: &str,
    archive_count: i64,
    new_count: i64,
    updated_count: i64,
) -> Result<()> {
    let id: Option<i64> =
        tx.query_row("SELECT id FROM crawled_folders WHERE path = ?1 ORDER BY id LIMIT 1", [path], |r| r.get(0)).optional()?;
    let id = match id {
        Some(id) => id,
        None => {
            tx.execute("INSERT INTO crawled_folders (path) VALUES (?1)", [path])?;
            tx.last_insert_rowid()
        }
    };
    tx.execute(
        "UPDATE crawled_folders SET pattern = ?2, last_crawled_at = ?3, archive_count = ?4, new_count = ?5, \
         updated_count = ?6 WHERE id = ?1",
        params![id, pattern, crawled_at, archive_count, new_count, updated_count],
    )?;
    Ok(())
}

/// `ids` that still exist (helper for the savepoint loop).
pub fn existing_ids(conn: &Connection, ids: &[i64]) -> Result<HashSet<i64>> {
    let mut st = conn.prepare("SELECT id FROM models WHERE id IN (SELECT value FROM json_each(?1))")?;
    st.query_map([json_ids(ids)], |r| r.get(0))?.collect()
}

/// Phase 4 for a batch: each archive in its own savepoint, as the Python
/// worker's `begin_nested()`. Stops after `max_failures` consecutive failures
/// (counting `failures_before` from earlier batches); the remaining archives
/// get no result. Returns per archive `Ok(counts)` or the error text.
pub fn upsert_batch(
    tx: &Transaction,
    store: Option<&ThumbStore>,
    cfg: &UpsertConfig,
    inputs: &[UpsertInput],
    failures_before: usize,
    max_failures: usize,
) -> Result<Vec<std::result::Result<Upserted, String>>> {
    let mut out = Vec::with_capacity(inputs.len());
    let mut failures = failures_before;
    for input in inputs {
        tx.execute_batch("SAVEPOINT crawl_upsert")?;
        match upsert(tx, store, cfg, input) {
            Ok(u) => {
                tx.execute_batch("RELEASE crawl_upsert")?;
                failures = 0;
                out.push(Ok(u));
            }
            Err(e) => {
                tx.execute_batch("ROLLBACK TO crawl_upsert; RELEASE crawl_upsert")?;
                failures += 1;
                out.push(Err(e.to_string()));
                if failures >= max_failures {
                    break;
                }
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_round_and_warnings() {
        assert_eq!(round4(0.12345), 0.1235); // 0.12345 is just above the tie in binary
        assert_eq!(round4(2.0 / 3.0), 0.6667);
        assert_eq!(append_warning(None, "x"), Some("x".into()));
        assert_eq!(append_warning(Some("a".into()), "x"), Some("a\nx".into()));
        assert_eq!(append_warning(Some("a\nx".into()), "x"), Some("a\nx".into()));
        assert_eq!(ref_ids(&["12".into(), "0000/7.jpg".into(), "bad".into()]), vec![12, 7]);
    }
}
