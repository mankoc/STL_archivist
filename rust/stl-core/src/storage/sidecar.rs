//! `METADATA.7z` sidecars: a port of `app/crawler/folder_metadata.py`
//! (`export_folder_metadata`, `import_folder_metadata`).
//!
//! The export writes the same `metadata.json` (`json.dumps(..., indent=2)`
//! layout, dict order, `repr(float)`) and thumbnail members. The import refuses
//! ([`SidecarError::Unsupported`]) any sidecar where Python would raise, store a
//! non-text value, or read the JSON differently, before writing anything, and
//! the caller uses Python.

use std::collections::{HashMap, HashSet};
use std::io::Cursor;
use std::path::{Path, PathBuf};

use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension, Transaction, params};
use serde_json::Value as Json;

use super::edit::{lookup, py_strip, subcollection, tag, utc_now_text};
use super::libraries::{self, Roots};
use crate::pyjson::{J, dumps_indent2, utc_now_isoformat};
use crate::thumbstore::ThumbStore;
use crate::winpath::WinPath;

pub const METADATA_FILENAME: &str = "METADATA.7z";
const JSON_MEMBER: &str = "metadata.json";
const THUMBS_DIR: &str = "thumbnails";
const SCHEMA_VERSION: i64 = 1;

#[derive(Debug)]
pub enum SidecarError {
    Db(rusqlite::Error),
    /// Input the port leaves to Python (nothing was written).
    Unsupported(String),
    /// Writing the sidecar file failed.
    Io(String),
    Store(String),
}

impl std::fmt::Display for SidecarError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SidecarError::Db(e) => write!(f, "database: {e}"),
            SidecarError::Unsupported(m) => write!(f, "unsupported: {m}"),
            SidecarError::Io(m) => write!(f, "{m}"),
            SidecarError::Store(m) => write!(f, "thumbnail store: {m}"),
        }
    }
}

impl From<rusqlite::Error> for SidecarError {
    fn from(e: rusqlite::Error) -> Self {
        SidecarError::Db(e)
    }
}

type SResult<T> = std::result::Result<T, SidecarError>;

fn unsupported<T>(m: impl Into<String>) -> SResult<T> {
    Err(SidecarError::Unsupported(m.into()))
}

fn value_json(v: Value) -> SResult<J> {
    Ok(match v {
        Value::Null => J::Null,
        Value::Integer(i) => J::Int(i),
        Value::Real(f) => J::Float(f),
        Value::Text(s) => J::Str(s),
        Value::Blob(_) => return unsupported("a blob value in a model column"),
    })
}

/// `str(Path(folder))` lowercased, for Windows path equality.
fn normcase(p: &str) -> String {
    WinPath::new(p).as_posix().to_lowercase()
}

use crate::winpath::join;

struct Snapshot {
    data: J,
    /// `(member name, image id)` of the thumbnails to embed.
    thumbs: Vec<(String, i64)>,
}

fn names(conn: &Connection, sql: &str, id: i64) -> SResult<Vec<J>> {
    let mut st = conn.prepare_cached(sql)?;
    let v: Vec<String> = st.query_map([id], |r| r.get(0))?.collect::<rusqlite::Result<_>>()?;
    Ok(v.into_iter().map(J::Str).collect())
}

fn snapshot(conn: &Connection, folder: &str, include_thumbnails: bool) -> SResult<Option<Snapshot>> {
    // The folder's archives: in the deepest library holding it, at its relative folder.
    let Some((library, rel_folder)) = Roots::load(conn)?.locate_folder(folder) else { return Ok(None) };
    let mut st = conn.prepare(&format!(
        "SELECT m.id, {}, m.archive_hash, m.title, m.creator, m.source, m.release_year, m.release_month, \
         m.url, m.description, m.sequence, m.presupports, m.warning, m.compression_rate, \
         scu.name, col.name, sub.name, cat.name, rel.name, m.archive_size FROM models m \
         LEFT JOIN sculptors scu ON m.sculptor_id = scu.id \
         LEFT JOIN collections col ON m.collection_id = col.id \
         LEFT JOIN subcollections sub ON m.subcollection_id = sub.id \
         LEFT JOIN categories cat ON m.category_id = cat.id \
         LEFT JOIN releases rel ON m.release_id = rel.id \
         WHERE m.library_id = ?1 AND m.rel_folder = ?2 ORDER BY m.id",
        super::schema_sql::archive_path_sql("m")
    ))?;
    let want = normcase(folder);
    let rows: Vec<Vec<Value>> = st
        .query_map(params![library, rel_folder], |r| (0..20).map(|k| r.get::<_, Value>(k)).collect())?
        .collect::<rusqlite::Result<_>>()?;
    let mut entries = Vec::new();
    let mut thumbs = Vec::new();
    for row in rows {
        let mut row = row.into_iter();
        let mut next = || row.next().unwrap_or(Value::Null);
        let id = match next() {
            Value::Integer(i) => i,
            _ => return unsupported("a model without an integer id"),
        };
        let path = match next() {
            Value::Text(s) => s,
            _ => return unsupported("a model without a text archive_path"),
        };
        if normcase(&WinPath::new(&path).parent().as_posix()) != want {
            continue;
        }
        let m_idx = entries.len();
        let hash = match next() {
            Value::Text(s) => s,
            Value::Null => String::new(),
            other => match value_json(other)? {
                J::Str(s) => s,
                _ => return unsupported("a non-text archive_hash"),
            },
        };
        let mut fields: Vec<(String, J)> = vec![
            ("archive_name".into(), J::Str(WinPath::new(&path).name().to_string())),
            ("archive_hash".into(), J::Str(hash)),
        ];
        for key in [
            "title", "creator", "source", "release_year", "release_month", "url", "description", "sequence",
            "presupports", "warning", "compression_rate",
        ] {
            fields.push((key.into(), value_json(next())?));
        }
        for key in ["sculptor", "collection", "subcollection", "category", "release"] {
            fields.push((key.into(), value_json(next())?));
        }
        // The archive's size, so a library reading the sidecar elsewhere can trust it
        // without opening the archive while the size still matches.
        fields.push(("archive_size".into(), value_json(next())?));
        fields.push((
            "scales".into(),
            J::Arr(names(
                conn,
                "SELECT s.name FROM model_scales k JOIN scales s ON s.id = k.scale_id WHERE k.model_id = ?1 ORDER BY s.id",
                id,
            )?),
        ));
        fields.push((
            "tags".into(),
            J::Arr(names(
                conn,
                "SELECT t.name FROM model_tags k JOIN tags t ON t.id = k.tag_id WHERE k.model_id = ?1 ORDER BY t.id",
                id,
            )?),
        ));
        let mut images = Vec::new();
        if include_thumbnails {
            let mut st = conn.prepare_cached("SELECT id, filename, thumbnail_path FROM images WHERE model_id = ?1 ORDER BY id")?;
            let imgs: Vec<(i64, Option<String>, Option<String>)> =
                st.query_map([id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<_>>()?;
            for (i_idx, (image_id, filename, thumb)) in imgs.into_iter().enumerate() {
                if thumb.is_none_or(|t| t.is_empty()) {
                    continue;
                }
                let member = format!("{THUMBS_DIR}/{m_idx}_{i_idx}.jpg");
                images.push(J::Obj(vec![
                    ("filename".into(), J::Str(filename.unwrap_or_default())),
                    ("thumbnail".into(), J::Str(member.clone())),
                ]));
                thumbs.push((member, image_id));
            }
        }
        fields.push(("images".into(), J::Arr(images)));
        let mut st = conn.prepare_cached(
            "SELECT filename, file_size, archive_hash FROM stl_files WHERE model_id = ?1 ORDER BY filename",
        )?;
        let files: Vec<(Value, Value, Value)> =
            st.query_map([id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<rusqlite::Result<_>>()?;
        let files = files
            .into_iter()
            .map(|(f, s, h)| {
                Ok(J::Obj(vec![
                    ("filename".into(), value_json(f)?),
                    ("file_size".into(), value_json(s)?),
                    ("archive_hash".into(), value_json(h)?),
                ]))
            })
            .collect::<SResult<Vec<_>>>()?;
        fields.push(("stl_files".into(), J::Arr(files)));
        entries.push(J::Obj(fields));
    }
    if entries.is_empty() {
        return Ok(None);
    }
    // The pattern of the library the folder is the root of (none for other folders).
    let pattern: Option<Value> = match Roots::load(conn)?.locate_root(folder) {
        Some(id) => conn.query_row("SELECT pattern FROM libraries WHERE id = ?1", [id], |r| r.get(0)).optional()?,
        None => None,
    };
    let data = J::Obj(vec![
        ("version".into(), J::Int(SCHEMA_VERSION)),
        ("generated_at".into(), J::Str(utc_now_isoformat())),
        ("models".into(), J::Arr(entries)),
        ("pattern".into(), value_json(pattern.unwrap_or(Value::Null))?),
    ]);
    Ok(Some(Snapshot { data, thumbs }))
}

/// `export_folder_metadata`: write `folder/METADATA.7z` for the archives directly
/// in `folder` (`folder` as `str(Path)`). `Ok(false)`: no records. A write
/// failure is [`SidecarError::Io`] (Python logs it and returns `False`).
pub fn export(conn: &Connection, store: Option<&ThumbStore>, folder: &str, include_thumbnails: bool) -> SResult<bool> {
    if include_thumbnails && store.is_none() {
        return unsupported("thumbnails requested without a native thumbnail store");
    }
    let Some(snap) = snapshot(conn, folder, include_thumbnails)? else { return Ok(false) };
    let json = dumps_indent2(&snap.data).into_bytes();
    let blobs: HashMap<i64, Vec<u8>> = match store {
        Some(s) if !snap.thumbs.is_empty() => {
            let ids: Vec<u64> = snap.thumbs.iter().map(|(_, id)| *id as u64).collect();
            s.get_many(&ids)
                .map_err(|e| SidecarError::Store(e.to_string()))?
                .into_iter()
                .map(|(id, b)| (id as i64, b))
                .collect()
        }
        _ => HashMap::new(),
    };
    let dest = PathBuf::from(join(folder, METADATA_FILENAME));
    let tmp = PathBuf::from(format!("{}.tmp", dest.display()));
    let written = (|| -> Result<(), String> {
        let mut w = sevenz_rust2::ArchiveWriter::create(&tmp).map_err(|e| e.to_string())?;
        w.push_archive_entry(sevenz_rust2::ArchiveEntry::new_file(JSON_MEMBER), Some(Cursor::new(json)))
            .map_err(|e| e.to_string())?;
        // JPEG thumbnails do not compress: store them, as LZMA2 only costs time.
        w.set_content_methods(vec![sevenz_rust2::EncoderMethod::COPY.into()]);
        for (member, id) in &snap.thumbs {
            if let Some(b) = blobs.get(id).filter(|b| !b.is_empty()) {
                w.push_archive_entry(sevenz_rust2::ArchiveEntry::new_file(member), Some(Cursor::new(b.as_slice())))
                    .map_err(|e| e.to_string())?;
            }
        }
        w.finish().map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, &dest).map_err(|e| e.to_string())
    })();
    if let Err(e) = written {
        let _ = std::fs::remove_file(&tmp);
        return Err(SidecarError::Io(format!("Failed to write {}: {e}", dest.display())));
    }
    Ok(true)
}

// ── import ───────────────────────────────────────────────────────────────────

/// Python truthiness of a JSON value.
fn truthy(v: &Json) -> bool {
    match v {
        Json::Null => false,
        Json::Bool(b) => *b,
        Json::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Json::String(s) => !s.is_empty(),
        Json::Array(a) => !a.is_empty(),
        Json::Object(o) => !o.is_empty(),
    }
}

fn opt_text(v: Option<&Json>, what: &str) -> SResult<Option<String>> {
    match v {
        None | Some(Json::Null) => Ok(None),
        Some(Json::String(s)) => Ok(Some(s.clone())),
        Some(_) => unsupported(format!("{what} is not text")),
    }
}

fn opt_int(v: Option<&Json>, what: &str) -> SResult<Option<i64>> {
    match v {
        None | Some(Json::Null) => Ok(None),
        Some(Json::Number(n)) if n.is_i64() => Ok(n.as_i64()),
        Some(_) => unsupported(format!("{what} is not an integer")),
    }
}

/// `a or b` over two optional JSON values, as text.
fn or_text(a: Option<&Json>, b: Option<&Json>, what: &str) -> SResult<Option<String>> {
    match a {
        Some(v) if truthy(v) => opt_text(Some(v), what),
        _ => opt_text(b, what),
    }
}

fn list<'a>(v: Option<&'a Json>, what: &str) -> SResult<&'a [Json]> {
    match v {
        None => Ok(&[]),
        Some(Json::Array(a)) => Ok(a),
        Some(_) => unsupported(format!("{what} is not a list")),
    }
}

/// Everything one model of the sidecar needs, checked before any write.
struct Planned {
    archive_path: String,
    columns: [Option<String>; 7], // title creator source url description presupports warning
    archive_hash: String,
    /// The archive's size when the sidecar was written (none in older sidecars).
    archive_size: Option<i64>,
    release_year: Option<i64>,
    release_month: Option<i64>,
    sequence: Option<i64>,
    compression_rate: Option<Json>,
    sculptor: Option<String>,
    collection: Option<String>,
    subcollection: Option<String>,
    category: Option<String>,
    release: Option<String>,
    scales: Vec<String>,
    tags: Vec<String>,
    images: Vec<(Option<String>, Vec<u8>)>,
    stl_files: Vec<(String, Option<i64>, Option<String>)>,
}

fn plan_model(m: &serde_json::Map<String, Json>, path: String, thumbs: &HashMap<String, Vec<u8>>) -> SResult<Planned> {
    let text = |k: &str| opt_text(m.get(k), k);
    let archive_hash = match m.get("archive_hash") {
        None => String::new(),
        Some(Json::String(s)) => s.clone(),
        Some(_) => return unsupported("archive_hash is not text"),
    };
    let compression_rate = match m.get("compression_rate") {
        None | Some(Json::Null) => None,
        Some(v @ Json::Number(_)) => Some(v.clone()),
        Some(_) => return unsupported("compression_rate is not a number"),
    };
    let truthy_text = |k: &str| -> SResult<Option<String>> {
        match m.get(k) {
            Some(v) if truthy(v) => opt_text(Some(v), k),
            _ => Ok(None),
        }
    };
    let collection = or_text(m.get("collection"), m.get("category"), "collection")?.filter(|s| !s.is_empty());
    let subcollection = or_text(m.get("subcollection"), m.get("subcategory"), "subcollection")?.filter(|s| !s.is_empty());
    let category = if m.contains_key("collection") { truthy_text("category")? } else { None };
    let scales: Vec<String> = match m.get("scales") {
        None | Some(Json::Null) => match m.get("scale") {
            Some(v) if truthy(v) => vec![opt_text(Some(v), "scale")?.unwrap_or_default()],
            _ => Vec::new(),
        },
        Some(Json::Array(a)) => a
            .iter()
            .filter(|v| truthy(v))
            .map(|v| opt_text(Some(v), "scale").map(Option::unwrap_or_default))
            .collect::<SResult<_>>()?,
        Some(v) if !truthy(v) => Vec::new(),
        Some(_) => return unsupported("scales is not a list"),
    };
    let tags: Vec<String> = match m.get("tags") {
        None => Vec::new(),
        Some(Json::Array(a)) => a
            .iter()
            .map(|v| match v {
                Json::String(s) => Ok(s.clone()),
                _ => unsupported("a tag is not text"),
            })
            .collect::<SResult<Vec<_>>>()?
            .into_iter()
            .filter(|t| !py_strip(t).is_empty())
            .collect(),
        Some(_) => return unsupported("tags is not a list"),
    };
    let mut images = Vec::new();
    for img in list(m.get("images"), "images")? {
        let Json::Object(img) = img else { return unsupported("an image entry is not an object") };
        let key = match img.get("thumbnail") {
            None => String::new(),
            Some(Json::String(s)) => s.clone(),
            Some(Json::Array(_)) | Some(Json::Object(_)) => return unsupported("an unhashable thumbnail key"),
            Some(_) => continue, // never a key of the thumbnail map
        };
        let Some(raw) = thumbs.get(&key).filter(|b| !b.is_empty()) else { continue };
        let filename = match img.get("filename") {
            None => Some(String::new()),
            v => opt_text(v, "image filename")?,
        };
        images.push((filename, raw.clone()));
    }
    let mut stl_files = Vec::new();
    let mut seen = HashSet::new();
    for sf in list(m.get("stl_files"), "stl_files")? {
        let Json::Object(sf) = sf else { return unsupported("an stl_files entry is not an object") };
        let Some(fname) = sf.get("filename").filter(|v| truthy(v)) else { continue };
        let Json::String(fname) = fname else { return unsupported("an STL file name is not text") };
        if !seen.insert(fname.clone()) {
            return unsupported("an STL file listed twice");
        }
        stl_files.push((fname.clone(), opt_int(sf.get("file_size"), "file_size")?, opt_text(sf.get("archive_hash"), "stl archive_hash")?));
    }
    Ok(Planned {
        archive_path: path,
        columns: [
            text("title")?,
            or_text(m.get("creator"), m.get("author"), "creator")?,
            text("source")?,
            text("url")?,
            text("description")?,
            text("presupports")?,
            text("warning")?,
        ],
        archive_hash,
        archive_size: opt_int(m.get("archive_size"), "archive_size")?,
        release_year: opt_int(m.get("release_year"), "release_year")?,
        release_month: opt_int(m.get("release_month"), "release_month")?,
        sequence: opt_int(m.get("sequence"), "sequence")?,
        compression_rate,
        sculptor: truthy_text("sculptor")?,
        collection,
        subcollection,
        category,
        release: truthy_text("release")?,
        scales,
        tags,
        images,
        stl_files,
    })
}

/// The `metadata.json` and thumbnails of a sidecar, as `_read_metadata_7z` extracts them.
fn read_sidecar(path: &Path) -> SResult<(Json, HashMap<String, Vec<u8>>)> {
    let members = crate::archive::list_members(path).map_err(|e| SidecarError::Unsupported(format!("reading {}: {e}", path.display())))?;
    let names: Vec<String> = members.iter().filter(|m| !m.name.ends_with('/')).map(|m| m.name.clone()).collect();
    for n in &names {
        let lower = n.to_lowercase();
        let canonical = n == JSON_MEMBER
            || (n.starts_with("thumbnails/") && !n[THUMBS_DIR.len() + 1..].contains(['/', '\\']) && n.len() > THUMBS_DIR.len() + 1);
        if !canonical && (lower == JSON_MEMBER || lower.starts_with(THUMBS_DIR) || n.contains('\\')) {
            return unsupported(format!("member name {n:?} differs from what this port extracts"));
        }
    }
    let data = crate::archive::read_members(path, &names).map_err(|e| SidecarError::Unsupported(format!("reading {}: {e}", path.display())))?;
    let mut json = None;
    let mut thumbs = HashMap::new();
    for (name, bytes) in data {
        if name == JSON_MEMBER {
            json = Some(bytes);
        } else if let Some(rest) = name.strip_prefix("thumbnails/") {
            thumbs.insert(format!("{THUMBS_DIR}/{rest}"), bytes);
        }
    }
    let Some(json) = json else { return unsupported("metadata.json not found") };
    let text = std::str::from_utf8(json.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&json))
        .map_err(|_| SidecarError::Unsupported("metadata.json is not UTF-8".into()))?;
    let value = serde_json::from_str(text).map_err(|e| SidecarError::Unsupported(format!("metadata.json: {e}")))?;
    Ok((value, thumbs))
}

/// The archive names a sidecar lists (its models' `archive_name`), for cleaning
/// up sidecars whose archives are gone.
pub fn listed_archives(path: &Path) -> SResult<Vec<String>> {
    let (data, _) = read_sidecar(path)?;
    let models = match &data {
        Json::Object(d) => list(d.get("models"), "models")?,
        _ => return unsupported("metadata.json is not an object"),
    };
    Ok(models
        .iter()
        .filter_map(|m| m.get("archive_name").and_then(Json::as_str))
        .filter(|n| !n.is_empty() && !n.contains(['/', '\\', ':']) && *n != "." && *n != "..")
        .map(str::to_string)
        .collect())
}

/// `import_folder_metadata`: insert the sidecar's records whose archive exists
/// and is not in the database yet; returns their archive paths. Thumbnails are
/// written to `store` in one transaction.
pub fn import(tx: &Transaction, store: &ThumbStore, folder: &str) -> SResult<Vec<String>> {
    let metadata_path = PathBuf::from(join(folder, METADATA_FILENAME));
    if !metadata_path.exists() {
        return Ok(Vec::new());
    }
    let (data, thumbs) = read_sidecar(&metadata_path)?;
    let Json::Object(data) = data else { return unsupported("metadata.json is not an object") };
    let version_ok = match data.get("version") {
        Some(Json::Number(n)) => n.as_f64() == Some(1.0),
        Some(Json::Bool(true)) => true,
        _ => false,
    };
    if !version_ok {
        return Ok(Vec::new());
    }
    let pattern = match data.get("pattern") {
        Some(v) if truthy(v) => opt_text(Some(v), "pattern")?,
        _ => None,
    };
    // Plan every model first: refusals happen before any write.
    let mut plans = Vec::new();
    for m in list(data.get("models"), "models")? {
        let Json::Object(m) = m else { return unsupported("a model entry is not an object") };
        let name = match m.get("archive_name") {
            Some(v) if truthy(v) => match v {
                Json::String(s) => s.clone(),
                _ => return unsupported("archive_name is not text"),
            },
            _ => continue,
        };
        if name.contains(['/', '\\', ':']) || name == "." || name == ".." || py_strip(&name) != name {
            return unsupported(format!("archive name {name:?} is not a plain file name"));
        }
        let path = join(folder, &name);
        if !Path::new(&path).exists() {
            continue;
        }
        plans.push(plan_model(m, path, &thumbs)?);
    }
    // An archive listed twice is inserted once: the second finds the first, as in Python.
    // A sidecar at a library's root gives that library its pattern when it has none.
    if let Some(pattern) = &pattern
        && let Some(id) = Roots::load(tx)?.locate_root(folder)
    {
        tx.execute("UPDATE libraries SET pattern = ?1 WHERE id = ?2 AND coalesce(pattern, '') = ''", params![pattern, id])?;
    }

    let mut imported = Vec::new();
    let mut thumb_writes: Vec<(i64, Vec<u8>)> = Vec::new();
    for p in plans {
        let (library, rel) = libraries::locate_or_create(tx, &p.archive_path, Some(folder))?;
        if tx
            .query_row("SELECT id FROM models WHERE library_id = ?1 AND rel_path = ?2 LIMIT 1", params![library, rel.as_str()], |r| {
                r.get::<_, i64>(0)
            })
            .optional()?
            .is_some()
        {
            continue;
        }
        let sculptor = p.sculptor.as_deref().map(|n| lookup(tx, "sculptors", n)).transpose()?;
        let collection = p.collection.as_deref().map(|n| lookup(tx, "collections", n)).transpose()?;
        let sub = match (p.subcollection.as_deref(), collection) {
            (Some(n), Some(cid)) => Some(subcollection(tx, n, cid)?),
            _ => None,
        };
        let category = p.category.as_deref().map(|n| lookup(tx, "categories", n)).transpose()?;
        let release = p.release.as_deref().map(|n| lookup(tx, "releases", n)).transpose()?;
        let mut scale_ids = Vec::new();
        for s in &p.scales {
            let id = lookup(tx, "scales", s)?;
            if scale_ids.contains(&id) {
                return unsupported("a scale linked twice");
            }
            scale_ids.push(id);
        }
        let mut tag_ids = Vec::new();
        for t in &p.tags {
            let id = tag(tx, t)?;
            if tag_ids.contains(&id) {
                return unsupported("a tag linked twice");
            }
            tag_ids.push(id);
        }
        let rate: Value = match &p.compression_rate {
            Some(Json::Number(n)) if n.is_i64() => Value::Integer(n.as_i64().unwrap_or(0)),
            Some(Json::Number(n)) => Value::Real(n.as_f64().unwrap_or(0.0)),
            _ => Value::Null,
        };
        let now = utc_now_text();
        let [title, creator, source, url, description, presupports, warning] = &p.columns;
        tx.execute(
            "INSERT INTO models (title, creator, source, release_year, release_month, url, description, library_id, rel_path, \
             rel_folder, archive_hash, sequence, presupports, warning, compression_rate, is_missing, to_print, \
             created_at, updated_at, sculptor_id, collection_id, subcollection_id, category_id, release_id, archive_size) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, 0, 0, ?16, ?16, ?17, ?18, ?19, ?20, ?21, ?22)",
            params![
                title, creator, source, p.release_year, p.release_month, url, description, library, rel.as_str(),
                rel.folder(), p.archive_hash, p.sequence, presupports, warning, rate, now,
                sculptor, collection, sub, category, release, p.archive_size
            ],
        )?;
        let model_id = tx.last_insert_rowid();
        for id in scale_ids {
            tx.execute("INSERT INTO model_scales (model_id, scale_id) VALUES (?1, ?2)", [model_id, id])?;
        }
        for id in tag_ids {
            tx.execute("INSERT INTO model_tags (model_id, tag_id) VALUES (?1, ?2)", [model_id, id])?;
        }
        for (filename, raw) in p.images {
            tx.execute("INSERT INTO images (model_id, filename) VALUES (?1, ?2)", params![model_id, filename])?;
            thumb_writes.push((tx.last_insert_rowid(), raw));
        }
        for (fname, size, hash) in p.stl_files {
            tx.execute(
                "INSERT INTO stl_files (model_id, filename, file_size, archive_hash) VALUES (?1, ?2, ?3, ?4)",
                params![model_id, fname, size, hash],
            )?;
        }
        imported.push(p.archive_path);
    }
    if !thumb_writes.is_empty() {
        let items: Vec<(u64, &[u8])> = thumb_writes.iter().map(|(id, b)| (*id as u64, b.as_slice())).collect();
        match store.put_many(&items) {
            Ok(()) => {
                for (id, _) in &thumb_writes {
                    tx.execute("UPDATE images SET thumbnail_path = ?1 WHERE id = ?2", params![id.to_string(), id])?;
                }
            }
            Err(_) => {
                for (id, _) in &thumb_writes {
                    tx.execute("DELETE FROM images WHERE id = ?1", [id])?;
                }
            }
        }
    }
    Ok(imported)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, migrate, open};

    #[test]
    fn export_then_import() {
        let dir = tempfile::tempdir().unwrap();
        let folder = dir.path().join("models");
        std::fs::create_dir(&folder).unwrap();
        std::fs::write(folder.join("a.zip"), b"x").unwrap();
        let folder_s = crate::winpath::native(&folder.to_string_lossy());
        let store = ThumbStore::open(&dir.path().join("thumbs")).unwrap();

        let src = dir.path().join("src.db");
        migrate(&src, &Vectors::default()).unwrap();
        let conn = open(&src).unwrap();
        // A library around the folder: the sidecar holds the folder's own archives.
        conn.execute("INSERT INTO libraries (uuid, name, root) VALUES ('u1', 'lib', ?1)", [crate::winpath::native(&dir.path().to_string_lossy())])
            .unwrap();
        conn.execute(
            "INSERT INTO models (title, library_id, rel_path, rel_folder, archive_hash, compression_rate, is_missing, to_print) \
             VALUES ('A', 1, 'models/a.zip', 'models', 'h', 0.5, 0, 0)",
            [],
        )
        .unwrap();
        conn.execute("INSERT INTO images (model_id, filename, thumbnail_path) VALUES (1, 'i.jpg', '1')", []).unwrap();
        store.put_many(&[(1, b"jpeg".as_slice())]).unwrap();
        assert!(export(&conn, Some(&store), &folder_s, true).unwrap());

        let dst = dir.path().join("dst.db");
        migrate(&dst, &Vectors::default()).unwrap();
        let mut conn = open(&dst).unwrap();
        let tx = conn.transaction().unwrap();
        let imported = import(&tx, &store, &folder_s).unwrap();
        tx.commit().unwrap();
        assert_eq!(imported, [crate::winpath::join(&folder_s, "a.zip")]);
        let (root, rel): (String, String) =
            conn.query_row("SELECT l.root, m.rel_path FROM models m JOIN libraries l ON l.id = m.library_id", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!((root, rel.as_str()), (folder_s.clone(), "a.zip"), "outside every library: the folder became one");
        let (title, rate): (String, f64) =
            conn.query_row("SELECT title, compression_rate FROM models", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!((title.as_str(), rate), ("A", 0.5));
        let thumb: String = conn.query_row("SELECT thumbnail_path FROM images", [], |r| r.get(0)).unwrap();
        assert_eq!(store.get_many(&[thumb.parse().unwrap()]).unwrap()[0].1, b"jpeg");
    }
}
