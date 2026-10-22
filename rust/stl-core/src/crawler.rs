//! The crawl pipeline: a port of `CrawlerWorker.run` (`app/crawler/crawler.py`).
//!
//! Phases per batch of 50 archives, as in Python: 1 hash (parallel, reusing
//! stored hashes when size and mtime match), 2 diff with early moves, 3 extract
//! and precompute thumbnails (parallel), 4 upsert; then sidecars, move
//! reconciliation, missing records, the crawled-folder record and lookup
//! pruning. The database phases are `storage::crawl`.
//!
//! What this crate cannot do exactly as the Python app does goes to the
//! [`Host`] as a [`Request`]: archives the native readers refuse, images only
//! Pillow decodes, PDF pages (PyMuPDF), CLIP vectors, metadata or rules the
//! native ports leave to Python, sidecars the native
//! port refuses. The Python host answers them on its own thread; a host
//! without Python answers `Response::None` and the crawl continues without
//! that piece (a later native implementation can answer instead).

use std::collections::{HashMap, HashSet};
use std::path::Path;

use rayon::prelude::*;

use crate::archive;
use crate::metadata::{self, Metadata};
use crate::path_pattern;
use crate::rules::{self, Checks, MemberPath, PresupportPatterns};
use crate::storage::{self, crawl as db, edit, sidecar};
use crate::thumbstore::ThumbStore;
use crate::winpath::WinPath;

const BATCH_SIZE: usize = 50;
const MAX_CONSECUTIVE_FAILURES: usize = 50;
const SIZE_LIMIT_IMAGE: u64 = 50 * 1024 * 1024;
const SIZE_LIMIT_META: u64 = 1024 * 1024;

/// The crawl settings (`CrawlerConfig` plus the source folder and pattern).
#[derive(Debug, Clone, Default)]
pub struct CrawlConfig {
    pub source_dir: String,
    pub pattern: String,
    pub recurse: bool,
    pub force_rescan: bool,
    pub store_stl: bool,
    pub max_thumbnails: usize,
    pub max_thumb_size: u32,
    pub max_workers: usize,
    /// The UnRAR executable, resolved (`""`: none).
    pub unrar: String,
    pub sidecars: bool,
    pub sidecar_thumbnails: bool,
    pub warn: Checks,
    pub presupport_patterns: Vec<String>,
    pub embed: bool,
    pub log_unchanged: bool,
    pub defer_missing_deletion: bool,
    pub upsert: db::UpsertConfig,
}

/// Work the host does for the crawl (see the module docs).
#[derive(Debug)]
pub enum Request {
    /// `archive_hash(path)`.
    Hash(String),
    /// `extract_archive_contents(path)` with names, STL sizes, uncompressed total.
    Extract(String),
    /// `list_stl_members(path)`.
    ListStl(String),
    /// `make_thumbnail` (Pillow) of an image, or of a PDF's first page.
    Thumbnail { data: Vec<u8>, pdf: bool },
    /// CLIP vectors (float32 blobs) of JPEGs.
    Embed(Vec<Vec<u8>>),
    /// `extract_metadata(path, contents (.url / info.json), source_dir)`.
    Metadata { path: String, files: Vec<(String, Vec<u8>)> },
    /// `detect_presupport`, `detect_warnings`, `detect_scale([name] + names)`.
    Rules { archive_name: String, names: Vec<String> },
    SidecarImport(String),
    SidecarExport(String),
}

#[derive(Debug, Default)]
pub struct Extracted {
    pub contents: Vec<(String, Vec<u8>)>,
    pub names: Vec<String>,
    pub stl_info: Vec<(String, Option<i64>)>,
    pub uncompressed: Option<i64>,
}

#[derive(Debug)]
pub enum Response {
    None,
    Hash(String),
    Extracted(Extracted),
    Stl(Vec<(String, Option<i64>)>),
    Thumbnail(Option<Vec<u8>>),
    Vectors(Vec<Option<Vec<u8>>>),
    Metadata(Metadata),
    Rules { presupports: String, warnings: String, scales: Vec<String> },
    Imported(usize),
}

/// What the crawl reports, in order (the Python worker's signals).
#[derive(Debug, Clone)]
pub enum Event {
    Log(String),
    Phase(String),
    Warning(String),
    Progress(usize, usize),
    Counts(usize, usize, usize),
    /// Archive path, `"new"` or `"updated"`.
    Result(String, String),
    Moved(String, String),
    Deleted(String),
}

pub trait Host: Sync {
    fn call(&self, request: Request) -> Response;
    fn emit(&self, event: Event);
    fn should_stop(&self) -> bool;
}

#[derive(Debug)]
pub enum CrawlError {
    Db(rusqlite::Error),
    Io(std::io::Error),
}

impl std::fmt::Display for CrawlError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CrawlError::Db(e) => write!(f, "database: {e}"),
            CrawlError::Io(e) => write!(f, "{e}"),
        }
    }
}

impl From<rusqlite::Error> for CrawlError {
    fn from(e: rusqlite::Error) -> Self {
        CrawlError::Db(e)
    }
}

impl From<std::io::Error> for CrawlError {
    fn from(e: std::io::Error) -> Self {
        CrawlError::Io(e)
    }
}

/// Run statistics.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Summary {
    pub total: usize,
    pub new: usize,
    pub updated: usize,
    pub unchanged: usize,
    pub new_paths: Vec<String>,
}

// ── listing ──────────────────────────────────────────────────────────────────

fn suffix_lower(name: &str) -> String {
    WinPath::new(name).suffix().to_lowercase()
}

fn is_archive_suffix(name: &str) -> bool {
    rules::ARCHIVE_SUFFIXES.contains(&suffix_lower(name).as_str())
}

/// Windows `sorted(paths)`: by the lowercased path components.
fn sort_paths(v: &mut [String]) {
    v.sort_by_cached_key(|p| p.to_lowercase().split(['\\', '/']).map(str::to_string).collect::<Vec<_>>());
}

/// `iter_archives` (`rglob("*")`: files and folders, no symlinked folders
/// followed) or the folder's own archive files, sorted, without `METADATA.7z`.
pub fn list_archives(root: &str, recurse: bool) -> Vec<String> {
    let mut out = Vec::new();
    let join = crate::winpath::join;
    let mut stack = vec![root.to_string()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            let path = join(&dir, &name);
            let ft = e.file_type().ok();
            let is_dir = ft.is_some_and(|t| t.is_dir());
            if recurse {
                if is_archive_suffix(&name) && name != sidecar::METADATA_FILENAME {
                    out.push(path.clone());
                }
                if is_dir {
                    stack.push(path);
                }
            } else if ft.is_some_and(|t| t.is_file()) && is_archive_suffix(&name) && name != sidecar::METADATA_FILENAME {
                out.push(path);
            }
        }
        if !recurse {
            break;
        }
    }
    sort_paths(&mut out);
    out
}

// ── phase 1 ──────────────────────────────────────────────────────────────────

pub(crate) fn fallback_fingerprint(path: &str) -> String {
    match db::stat(path) {
        Some((size, mtime)) => format!("fallback:{size}:{mtime}"),
        None => "fallback:0:0".into(),
    }
}

fn hash_one(path: &str, host: &dyn Host) -> String {
    match archive::fingerprint(Path::new(path)) {
        Ok(h) => h,
        Err(_) => match host.call(Request::Hash(path.to_string())) {
            Response::Hash(h) => h,
            _ => fallback_fingerprint(path),
        },
    }
}

// ── phase 3 ──────────────────────────────────────────────────────────────────

fn is_wanted(name: &str) -> bool {
    let p = MemberPath::new(&name.replace('\\', "/"));
    let suffix = p.suffix().to_lowercase();
    rules::IMAGE_SUFFIXES.contains(&suffix.as_str())
        || suffix == ".pdf"
        || suffix == ".url"
        || (p.name.is_ascii() && p.name.eq_ignore_ascii_case("info.json"))
}

fn is_image_or_pdf(name: &str) -> bool {
    let suffix = MemberPath::new(&name.replace('\\', "/")).suffix().to_lowercase();
    rules::IMAGE_SUFFIXES.contains(&suffix.as_str()) || suffix == ".pdf"
}

fn size_limit(name: &str) -> u64 {
    if is_image_or_pdf(name) { SIZE_LIMIT_IMAGE } else { SIZE_LIMIT_META }
}

const STL_EXTS: [&str; 6] = [".stl", ".obj", ".lys", ".lyt", ".chitubox", ".ctp"];

/// `extract_archive_contents` natively, or `None` where the native readers refuse.
pub(crate) fn extract_native(path: &str, unrar: &str) -> Option<Extracted> {
    let p = Path::new(path);
    let members = archive::list_members(p).ok()?;
    let names: Vec<String> = members.iter().map(|m| m.name.clone()).collect();
    let uncompressed = Some(members.iter().filter_map(|m| m.size).sum::<u64>() as i64);
    let stl_info = stl_dict(members.iter().map(|m| (m.name.clone(), m.size.map(|s| s as i64))));
    let size_by_name: HashMap<&str, Option<u64>> = members.iter().map(|m| (m.name.as_str(), m.size)).collect();
    let to_read: Vec<String> = names
        .iter()
        .filter(|n| is_wanted(n))
        .filter(|n| size_by_name.get(n.as_str()).copied().flatten().is_none_or(|s| s <= size_limit(n)))
        .cloned()
        .collect();
    let read = if to_read.is_empty() { Vec::new() } else { archive::read_members_with(p, &to_read, unrar).ok()? };
    // A dict built from the pairs: first position, last value.
    let mut order: Vec<String> = Vec::new();
    let mut data: HashMap<String, Vec<u8>> = HashMap::new();
    for (name, bytes) in read {
        if !data.contains_key(&name) {
            order.push(name.clone());
        }
        data.insert(name, bytes);
    }
    let contents = order
        .into_iter()
        .filter_map(|n| {
            let b = data.remove(&n)?;
            (b.len() as u64 <= size_limit(&n)).then_some((n, b))
        })
        .collect();
    Some(Extracted { contents, names, stl_info, uncompressed })
}

/// `{name: size}` for the printable members, as a Python dict builds it:
/// a repeated name keeps its first position and its last size.
fn stl_dict(members: impl Iterator<Item = (String, Option<i64>)>) -> Vec<(String, Option<i64>)> {
    let mut out: Vec<(String, Option<i64>)> = Vec::new();
    let mut index: HashMap<String, usize> = HashMap::new();
    for (name, size) in members {
        if !STL_EXTS.contains(&MemberPath::new(&name.replace('\\', "/")).suffix().to_lowercase().as_str()) {
            continue;
        }
        match index.get(&name) {
            Some(&k) => out[k].1 = size,
            None => {
                index.insert(name.clone(), out.len());
                out.push((name, size));
            }
        }
    }
    out
}

fn dhash_text(jpeg: &[u8]) -> Option<String> {
    crate::dhash::dhash(jpeg).ok().map(|h| format!("2:{h:032x}"))
}

/// `make_thumbnail(raw, size)`: native, else the host's Pillow (or PDF render).
fn thumbnail(raw: &[u8], size: u32, pdf: bool, host: &dyn Host) -> Option<Vec<u8>> {
    if !pdf && let Ok(j) = crate::thumbnail::make_thumbnail(raw, size, true) {
        return Some(j);
    }
    match host.call(Request::Thumbnail { data: raw.to_vec(), pdf }) {
        Response::Thumbnail(t) => t,
        _ => None,
    }
}

struct Prepared {
    extracted: Extracted,
    /// `(name, jpeg, dhash)` for each thumbnail candidate.
    precomp: Vec<(String, Option<Vec<u8>>, Option<String>)>,
}

fn prepare(path: &str, cfg: &CrawlConfig, host: &dyn Host) -> Prepared {
    let extracted = extract_native(path, &cfg.unrar).unwrap_or_else(|| match host.call(Request::Extract(path.to_string())) {
        Response::Extracted(x) => x,
        _ => Extracted::default(),
    });
    let candidates: Vec<&(String, Vec<u8>)> =
        extracted.contents.iter().filter(|(n, _)| is_image_or_pdf(n)).take(cfg.max_thumbnails).collect();
    let precomp = candidates
        .into_iter()
        .map(|(name, raw)| {
            if raw.is_empty() {
                return (name.clone(), None, None);
            }
            let pdf = MemberPath::new(&name.replace('\\', "/")).suffix().to_lowercase() == ".pdf";
            let jpeg = thumbnail(raw, cfg.max_thumb_size, pdf, host);
            let dh = jpeg.as_deref().and_then(dhash_text);
            (name.clone(), jpeg, dh)
        })
        .collect();
    Prepared { extracted, precomp }
}

// ── phase 4 inputs ───────────────────────────────────────────────────────────

fn parse_date(s: &str, _host: &dyn Host) -> (Option<i64>, Option<i64>) {
    path_pattern::parse_release_date(s)
}

/// Python's `int(s)` (it strips surrounding whitespace itself).
fn parse_int(s: &str, _host: &dyn Host) -> Option<i64> {
    crate::pyunicode::py_int(crate::storage::edit::py_strip(s))
}

/// `"/" + str(path.relative_to(source)).replace("\\", "/")`, or the path itself.
fn rel_path(path: &str, source: &str) -> String {
    match metadata::win_relative_parts(path, source) {
        Some(parts) => format!("/{}", parts.join("/")),
        None => path.to_string(),
    }
}

/// An archive's path inside the crawled folder (`Studio/2020/Dragon.zip`), for the log.
fn rel_name(path: &str, source: &str) -> String {
    match metadata::win_relative_parts(path, source) {
        Some(parts) if !parts.is_empty() => parts.join("/"),
        _ => WinPath::new(path).name().to_string(),
    }
}

fn plural(n: usize, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

/// One log line per catalogued archive: position, what happened, which archive,
/// and what was stored.
fn result_line(idx: usize, total: usize, status: &str, name: &str, thumbnails: usize, folder_images: usize) -> String {
    let mut stored = plural(thumbnails, "thumbnail", "thumbnails");
    if folder_images > 0 {
        stored.push_str(&format!(", {}", plural(folder_images, "folder image", "folder images")));
    }
    format!("[{idx}/{total}] {status}: {name} ({stored})")
}

fn pattern_match(cfg: &CrawlConfig, path: &str, host: &dyn Host) -> Option<db::Extracted> {
    if cfg.pattern.is_empty() {
        return None;
    }
    let fields = path_pattern::parse_path(&cfg.pattern, &rel_path(path, &cfg.source_dir));
    if fields.is_empty() {
        return None;
    }
    let get = |k: &str| fields.iter().find(|(f, _)| f == k).map(|(_, v)| v.clone());
    let sequence = get("sequence").filter(|v| !v.is_empty()).and_then(|v| parse_int(&v, host));
    let release_date = get("release_date").filter(|v| !v.is_empty()).map(|v| parse_date(&v, host));
    Some(db::Extracted { fields, release_date, sequence })
}

fn rules_for(cfg: &CrawlConfig, archive_name: &str, names: &[String], host: &dyn Host) -> (String, Option<String>, Vec<String>) {
    let native = (|| {
        let defaults: Vec<String> = rules::DEFAULT_PRESUPPORT_PATTERNS.iter().map(|s| s.to_string()).collect();
        let pats = if cfg.presupport_patterns == defaults {
            PresupportPatterns::new::<String>(None).ok()?
        } else {
            PresupportPatterns::new(Some(&cfg.presupport_patterns)).ok()?
        };
        let warnings = rules::detect_warnings(names, cfg.warn, &pats).ok()?;
        let mut all = vec![archive_name.to_string()];
        all.extend(names.iter().cloned());
        Some((rules::detect_presupport(names).to_string(), warnings, rules::detect_scale(&all)))
    })();
    let (presupports, warnings, scales) = match native {
        Some(r) => r,
        None => match host.call(Request::Rules { archive_name: archive_name.to_string(), names: names.to_vec() }) {
            Response::Rules { presupports, warnings, scales } => (presupports, warnings, scales),
            _ => ("No".to_string(), String::new(), Vec::new()),
        },
    };
    (presupports, Some(warnings).filter(|w| !w.is_empty()), scales)
}

fn folder_images(path: &str, cfg: &CrawlConfig, host: &dyn Host) -> Vec<(String, Option<Vec<u8>>, Option<String>)> {
    let mut out = Vec::new();
    let source = WinPath::new(&cfg.source_dir).as_posix().to_lowercase();
    let mut folder = WinPath::new(path).parent();
    loop {
        let posix = folder.as_posix();
        if metadata::win_relative_parts(&posix, &cfg.source_dir).is_none() {
            break;
        }
        let dir = crate::winpath::native(&posix);
        let mut files: Vec<String> = std::fs::read_dir(&dir)
            .map(|rd| {
                rd.flatten()
                    .filter(|e| e.file_type().is_ok_and(|t| t.is_file()))
                    .map(|e| e.file_name().to_string_lossy().into_owned())
                    .filter(|n| rules::IMAGE_SUFFIXES.contains(&suffix_lower(n).as_str()))
                    .map(|n| crate::winpath::join(&dir, &n))
                    .collect()
            })
            .unwrap_or_default();
        sort_paths(&mut files);
        for f in files {
            let Ok(raw) = std::fs::read(&f) else { continue };
            let jpeg = thumbnail(&raw, cfg.max_thumb_size, false, host);
            let dh = jpeg.as_deref().and_then(dhash_text);
            out.push((f, jpeg, dh));
        }
        if posix.to_lowercase() == source {
            break;
        }
        let parent = folder.parent();
        if parent == folder {
            break;
        }
        folder = parent;
    }
    out
}

fn metadata_for(path: &str, contents: &[(String, Vec<u8>)], cfg: &CrawlConfig, host: &dyn Host) -> Metadata {
    let files: Vec<(String, Vec<u8>)> = contents
        .iter()
        .filter(|(n, _)| n.to_lowercase().ends_with(".url") || n.to_lowercase().ends_with("info.json"))
        .cloned()
        .collect();
    match metadata::extract(path, &files, Some(&cfg.source_dir)) {
        Ok(m) => m,
        Err(_) => match host.call(Request::Metadata { path: path.to_string(), files }) {
            Response::Metadata(m) => m,
            _ => Metadata::default(),
        },
    }
}

// ── the run ──────────────────────────────────────────────────────────────────

struct Run<'a> {
    conn: rusqlite::Connection,
    store: Option<&'a ThumbStore>,
    cfg: &'a CrawlConfig,
    host: &'a dyn Host,
    pool: rayon::ThreadPool,
    summary: Summary,
    failures: usize,
    candidates: HashMap<String, i64>,
    names: HashMap<i64, (String, String)>, // missing id -> (path, file name)
    early_moved: HashSet<i64>,
    moved_new_paths: HashSet<String>,
    existing_hashes: HashMap<String, String>,
}

fn tx(conn: &mut rusqlite::Connection) -> rusqlite::Result<rusqlite::Transaction<'_>> {
    conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
}

impl Run<'_> {
    fn log(&self, s: impl Into<String>) {
        self.host.emit(Event::Log(s.into()));
    }

    fn phase1(&mut self, batch: &[String]) -> Result<Vec<String>, CrawlError> {
        let stats: Vec<Option<(i64, i64)>> = self.pool.install(|| batch.par_iter().map(|p| db::stat(p)).collect());
        let existing: HashMap<String, (String, Option<i64>, Option<i64>)> =
            db::existing(&self.conn, batch)?.into_iter().map(|(p, h, s, m)| (p, (h, s, m))).collect();
        for (p, (h, ..)) in &existing {
            self.existing_hashes.insert(p.clone(), h.clone());
        }
        let host = self.host;
        let force = self.cfg.force_rescan;
        let hashes: Vec<String> = self.pool.install(|| {
            batch
                .par_iter()
                .zip(stats.par_iter())
                .map(|(p, st)| {
                    if !force
                        && let (Some((h, Some(size), Some(mtime))), Some((fs, fm))) = (existing.get(p), st)
                        && size == fs
                        && mtime == fm
                    {
                        return h.clone();
                    }
                    hash_one(p, host)
                })
                .collect()
        });
        Ok(hashes)
    }

    fn batch(&mut self, batch: &[String], start: usize, modified: &mut HashSet<String>) -> Result<bool, CrawlError> {
        let hashes = self.phase1(batch)?;
        if start == 0 {
            self.host.emit(Event::Phase("Checking database for changes…".into()));
        }
        let pairs: Vec<(String, String)> = batch.iter().cloned().zip(hashes.iter().cloned()).collect();
        let diff = {
            let t = tx(&mut self.conn)?;
            let d = db::diff(&t, &pairs, self.cfg.force_rescan, self.cfg.store_stl, &self.candidates)?;
            t.commit()?;
            d
        };
        for p in &diff.unreadable {
            self.host
                .emit(Event::Warning(format!("WARNING: Cannot open archive (possibly corrupted): {}", WinPath::new(p).name())));
        }
        for h in &diff.consumed {
            let Some(id) = self.candidates.remove(h) else { continue };
            if let Some((_, _, new_path)) = diff.moved.iter().find(|(mh, _, _)| mh == h) {
                self.early_moved.insert(id);
                self.moved_new_paths.insert(new_path.clone());
                let (old_path, name) = self.names.get(&id).cloned().unwrap_or_default();
                self.log(format!("Moved: {name} → {new_path}"));
                self.host.emit(Event::Moved(old_path, new_path.clone()));
            }
        }
        if !diff.to_extract.is_empty() && start == 0 {
            self.host.emit(Event::Phase("Extracting new/changed archives…".into()));
        }
        let (cfg, host) = (self.cfg, self.host);
        let prepared: HashMap<String, Prepared> =
            self.pool.install(|| diff.to_extract.par_iter().map(|p| (p.clone(), prepare(p, cfg, host))).collect());
        let backfill: HashMap<String, Vec<(String, Option<i64>)>> = self.pool.install(|| {
            diff.stl_backfill
                .par_iter()
                .map(|p| {
                    let stl = match archive::list_members(Path::new(p)) {
                        Ok(m) => stl_dict(m.into_iter().map(|m| (m.name, m.size.map(|s| s as i64)))),
                        Err(_) => match host.call(Request::ListStl(p.clone())) {
                            Response::Stl(v) => v,
                            _ => Vec::new(),
                        },
                    };
                    (p.clone(), stl)
                })
                .collect()
        });
        let _ = backfill; // listed as in Python; the upsert does not store it (as in Python)
        let existing: HashMap<String, i64> = diff.existing.into_iter().collect();
        let total = self.summary.total;
        let mut inputs = Vec::new();
        for (p, h) in pairs.iter() {
            let prep = prepared.get(p);
            let existing_id = existing.get(p).copied();
            let changed = prep.map(|prep| self.changed(p, h, existing_id, prep));
            inputs.push(db::UpsertInput {
                path: p.clone(),
                hash: h.clone(),
                existing: existing_id,
                extracted: pattern_match(cfg, p, host),
                changed,
            });
            if host.should_stop() {
                break;
            }
        }
        let results = {
            let t = tx(&mut self.conn)?;
            let r = db::upsert_batch(&t, self.store, &cfg.upsert, &inputs, self.failures, MAX_CONSECUTIVE_FAILURES)?;
            t.commit()?;
            r
        };
        for (k, (input, result)) in inputs.iter().zip(results).enumerate() {
            let idx = start + k + 1;
            match result {
                Err(e) => {
                    self.log(format!("[{idx}/{total}] ERROR {}: {e}", rel_name(&input.path, &cfg.source_dir)));
                    self.failures += 1;
                    if self.failures >= MAX_CONSECUTIVE_FAILURES {
                        self.log(format!("Crawl aborted: {} consecutive upsert failures — last error: {e}", self.failures));
                        return Ok(true);
                    }
                }
                Ok(u) => {
                    self.failures = 0;
                    if input.changed.is_some() {
                        let status = if input.existing.is_none() {
                            self.summary.new += 1;
                            self.summary.new_paths.push(input.path.clone());
                            self.host.emit(Event::Result(input.path.clone(), "new".into()));
                            "new"
                        } else {
                            self.summary.updated += 1;
                            self.host.emit(Event::Result(input.path.clone(), "updated".into()));
                            "updated"
                        };
                        self.log(result_line(idx, total, status, &rel_name(&input.path, &cfg.source_dir), u.thumbnails, u.folder_images));
                        modified.insert(WinPath::new(&input.path).parent().as_posix());
                    } else {
                        self.summary.unchanged += 1;
                        if cfg.log_unchanged {
                            self.log(format!("[{idx}/{total}] unchanged: {}", rel_name(&input.path, &cfg.source_dir)));
                        }
                        if self.moved_new_paths.contains(&input.path) {
                            modified.insert(WinPath::new(&input.path).parent().as_posix());
                        }
                    }
                }
            }
            self.host.emit(Event::Progress(idx, total));
        }
        self.host.emit(Event::Counts(self.summary.new, self.summary.updated, self.summary.unchanged));
        Ok(host.should_stop())
    }

    fn changed(&self, path: &str, hash: &str, existing_id: Option<i64>, prep: &Prepared) -> db::Changed {
        let (cfg, host) = (self.cfg, self.host);
        let x = &prep.extracted;
        let meta = metadata_for(path, &x.contents, cfg, host);
        let archive_name = WinPath::new(path).name().to_string();
        let (presupports, warnings, scales) = rules_for(cfg, &archive_name, &x.names, host);
        let (year, month) = parse_date(meta.release_date.as_deref().unwrap_or(""), host);
        let images = x.contents.iter().filter(|(n, _)| is_image_or_pdf(n)).count();
        self.log(format!("  {} relevant file(s) extracted ({images} image(s))", x.contents.len()));
        let mut thumbs: Vec<db::Thumb> =
            prep.precomp.iter().map(|(n, j, d)| (n.clone(), j.clone(), if j.is_some() { d.clone() } else { None }, None)).collect();
        if cfg.embed {
            let resolved: Vec<usize> = (0..thumbs.len()).filter(|&i| thumbs[i].1.is_some()).collect();
            let jpegs = resolved.iter().map(|&i| thumbs[i].1.clone().unwrap_or_default()).collect();
            if let Response::Vectors(v) = host.call(Request::Embed(jpegs)) {
                for (i, vec) in resolved.into_iter().zip(v) {
                    thumbs[i].3 = vec;
                }
            }
        }
        let fresh = existing_id.is_none() || self.existing_hashes.get(path).map(String::as_str) != Some(hash);
        db::Changed {
            title: meta.title,
            creator: meta.creator,
            source: meta.distributor,
            release_year: year,
            release_month: month,
            description: meta.description,
            release: meta.release,
            tags: meta.tags,
            presupports,
            warnings,
            scales,
            uncompressed_size: x.uncompressed,
            thumbs,
            folder_images: if fresh { folder_images(path, cfg, host) } else { Vec::new() },
            stl_info: if cfg.store_stl { Some(x.stl_info.clone()) } else { None },
        }
    }
}

/// `CrawlerWorker.run` for the database at `db_path`.
pub fn crawl(db_path: &Path, store: Option<&ThumbStore>, cfg: &CrawlConfig, host: &dyn Host) -> Result<Summary, CrawlError> {
    let conn = storage::open(db_path)?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(cfg.max_workers.max(1))
        .build()
        .map_err(|e| CrawlError::Io(std::io::Error::other(e.to_string())))?;
    let mut run = Run {
        conn,
        store,
        cfg,
        host,
        pool,
        summary: Summary::default(),
        failures: 0,
        candidates: HashMap::new(),
        names: HashMap::new(),
        early_moved: HashSet::new(),
        moved_new_paths: HashSet::new(),
        existing_hashes: HashMap::new(),
    };
    host.emit(Event::Phase("Scanning for deleted files…".into()));
    let missing = db::collect_missing(&run.conn)?;
    if !missing.is_empty() {
        run.log(format!("Found {} missing archive(s) — will check for moved files after crawl.", missing.len()));
    }
    // moves.index_by_hash
    let mut shared = HashSet::new();
    for (id, path, h, name, _) in &missing {
        run.names.insert(*id, (path.clone(), name.clone()));
        if h.is_empty() || h.starts_with("fallback:") {
            continue;
        }
        if run.candidates.contains_key(h) {
            shared.insert(h.clone());
        } else {
            run.candidates.insert(h.clone(), *id);
        }
    }
    for h in shared {
        run.candidates.remove(&h);
    }

    host.emit(Event::Phase("Retrieving file list…".into()));
    let archives = list_archives(&cfg.source_dir, cfg.recurse);
    let total = archives.len();
    run.summary.total = total;
    let msg = format!("Found {total} archives in {}", cfg.source_dir);
    run.log(msg.clone());
    host.emit(Event::Phase(msg));

    if cfg.sidecars
        && let Some(st) = store
    {
        let mut seen = HashSet::new();
        for p in &archives {
            if host.should_stop() {
                break;
            }
            let folder = crate::winpath::native(&WinPath::new(p).parent().as_posix());
            if !seen.insert(folder.clone()) || !Path::new(&folder).join(sidecar::METADATA_FILENAME).exists() {
                continue;
            }
            let imported = {
                let t = tx(&mut run.conn)?;
                match sidecar::import(&t, st, &folder) {
                    Ok(v) => {
                        t.commit()?;
                        v.len()
                    }
                    Err(_) => {
                        drop(t);
                        match host.call(Request::SidecarImport(folder.clone())) {
                            Response::Imported(n) => n,
                            _ => 0,
                        }
                    }
                }
            };
            if imported > 0 {
                run.log(format!("Imported {imported} record(s) from METADATA.7z in {}", WinPath::new(&folder).name()));
            }
        }
    }

    if total > 0 {
        host.emit(Event::Phase(format!("Computing hashes for {total} archive(s)…")));
    }
    let mut modified: HashSet<String> = HashSet::new();
    for start in (0..total).step_by(BATCH_SIZE) {
        if host.should_stop() {
            break;
        }
        let batch = &archives[start..(start + BATCH_SIZE).min(total)];
        if run.batch(batch, start, &mut modified)? || host.should_stop() {
            break;
        }
    }

    if !modified.is_empty() && cfg.sidecars && store.is_some() {
        run.log(format!("Updating METADATA.7z in {} folder(s)…", modified.len()));
        let mut folders: Vec<String> = modified.into_iter().map(|f| crate::winpath::native(&f)).collect();
        sort_paths(&mut folders);
        for folder in folders {
            if host.should_stop() {
                break;
            }
            match sidecar::export(&run.conn, store, &folder, cfg.sidecar_thumbnails) {
                Ok(_) | Err(sidecar::SidecarError::Io(_)) => {}
                Err(_) => {
                    host.call(Request::SidecarExport(folder.clone()));
                }
            }
            run.log(format!("  Updated METADATA.7z in {}", WinPath::new(&folder).name()));
        }
    }

    if !run.early_moved.is_empty() {
        run.log(format!("Matched {} moved file(s) by content, without re-extracting.", run.early_moved.len()));
    }
    let missing: Vec<db::Missing> = missing.into_iter().filter(|m| !run.early_moved.contains(&m.0)).collect();
    if !missing.is_empty() {
        host.emit(Event::Phase(format!("Finding moved files ({} candidate(s))…", missing.len())));
        reconcile(&mut run, missing)?;
    }

    {
        let now = edit::utc_now_text();
        let t = tx(&mut run.conn)?;
        db::record_folder(
            &t,
            &cfg.source_dir,
            Some(cfg.pattern.as_str()).filter(|p| !p.is_empty()),
            &now,
            total as i64,
            run.summary.new as i64,
            run.summary.updated as i64,
        )?;
        t.commit()?;
    }
    {
        let t = tx(&mut run.conn)?;
        let pruned = edit::prune_unused_lookups(&t)?;
        t.commit()?;
        if !pruned.is_empty() {
            let order = ["subcollections", "collections", "sculptors", "categories", "releases", "scales"];
            let summary: Vec<String> = order
                .iter()
                .filter_map(|k| pruned.iter().find(|(n, _)| n == k).map(|(n, c)| format!("{c} {n}")))
                .collect();
            run.log(format!("Pruned unused lookups: {}", summary.join(", ")));
        }
    }
    run.log("Crawl complete.");
    Ok(run.summary)
}

fn delete_refs(store: Option<&ThumbStore>, refs: &[String]) {
    let ids: Vec<u64> = refs
        .iter()
        .filter_map(|r| if r.bytes().all(|b| b.is_ascii_digit()) && !r.is_empty() { r.parse().ok() } else { WinPath::new(r).stem().parse().ok() })
        .collect();
    if let Some(s) = store
        && !ids.is_empty()
    {
        let _ = s.delete_many(&ids);
    }
}

fn reconcile(run: &mut Run<'_>, missing: Vec<db::Missing>) -> Result<(), CrawlError> {
    let mut matched = HashSet::new();
    if !run.summary.new_paths.is_empty() {
        run.log(format!(
            "Reconciling {} missing record(s) against {} new archive(s)…",
            missing.len(),
            run.summary.new_paths.len()
        ));
        let mut new_paths = run.summary.new_paths.clone();
        new_paths.sort();
        let (moves, skipped, refs) = {
            let t = tx(&mut run.conn)?;
            let r = db::reconcile(&t, &missing, &new_paths)?;
            t.commit()?;
            r
        };
        for name in skipped {
            run.log(format!("  Skipped ambiguous name+size match for {name} — multiple candidates"));
        }
        for (id, old_path, new_path) in moves {
            matched.insert(id);
            let name = run.names.get(&id).map(|(_, n)| n.clone()).unwrap_or_default();
            run.log(format!("Moved: {name} → {new_path}"));
            run.host.emit(Event::Moved(old_path, new_path));
        }
        delete_refs(run.store, &refs);
        if !matched.is_empty() {
            run.log(format!("Matched {} moved file(s).", matched.len()));
        }
    }
    let unmatched: Vec<db::Missing> = missing.into_iter().filter(|m| !matched.contains(&m.0)).collect();
    if unmatched.is_empty() {
        return Ok(());
    }
    let (deleted, marked, _deferred, refs) = {
        let t = tx(&mut run.conn)?;
        let r = db::mark_missing(&t, &unmatched, &run.cfg.source_dir, run.cfg.defer_missing_deletion)?;
        t.commit()?;
        r
    };
    for p in &deleted {
        run.log(format!("Deleted: {p}"));
        run.host.emit(Event::Deleted(p.clone()));
    }
    for p in &marked {
        run.log(format!("Marked missing: {p}"));
        run.host.emit(Event::Deleted(p.clone()));
    }
    delete_refs(run.store, &refs);
    if !deleted.is_empty() {
        run.log(format!("Deleted {} record(s) removed from the crawled folder.", deleted.len()));
    }
    if !marked.is_empty() {
        run.log(format!(
            "Marked {} record(s) as missing (outside crawled folder — use DB Maintenance to remove).",
            marked.len()
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn log_lines_name_the_archive() {
        assert_eq!(rel_name(r"D:\STL\Studio\2020\Dragon.zip", r"D:\STL"), "Studio/2020/Dragon.zip");
        assert_eq!(rel_name(r"E:\other\Dragon.zip", r"D:\STL"), "Dragon.zip");
        assert_eq!(result_line(3, 40, "new", "Studio/Dragon.zip", 4, 1), "[3/40] new: Studio/Dragon.zip (4 thumbnails, 1 folder image)");
        assert_eq!(result_line(4, 40, "updated", "Knight.zip", 1, 0), "[4/40] updated: Knight.zip (1 thumbnail)");
    }

    #[test]
    fn listing_and_ordering() {
        let dir = tempfile::tempdir().unwrap();
        let root = crate::winpath::native(&dir.path().to_string_lossy());
        std::fs::create_dir_all(dir.path().join("B")).unwrap();
        for f in ["a.ZIP", "B/c.7z", "b.rar", "METADATA.7z", "x.txt", "B/METADATA.7z"] {
            std::fs::write(dir.path().join(f), b"x").unwrap();
        }
        let names = |v: Vec<String>| v.iter().map(|p| p[root.len() + 1..].to_string()).collect::<Vec<_>>();
        // Python sorts Windows paths by lowercased components: "b" < "b.rar".
        assert_eq!(names(list_archives(&root, true)), ["a.ZIP", r"B\c.7z", "b.rar"]);
        assert_eq!(names(list_archives(&root, false)), ["a.ZIP", "b.rar"]);
        let stl = stl_dict(
            [("a.stl", Some(1)), ("x.png", Some(2)), ("a.stl", Some(3)), ("b.OBJ", None)]
                .into_iter()
                .map(|(n, s)| (n.to_string(), s)),
        );
        assert_eq!(stl, [("a.stl".to_string(), Some(3)), ("b.OBJ".to_string(), None)]);
        assert_eq!(rel_path(r"F:\STL\a\b.zip", r"f:\stl"), "/a/b.zip");
        assert_eq!(rel_path(r"G:\x.zip", r"f:\stl"), r"G:\x.zip");
    }
}
