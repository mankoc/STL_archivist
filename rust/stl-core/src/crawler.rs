//! The crawl pipeline.
//!
//! Phases per batch of 50 archives: 1 hash (parallel, reusing stored hashes when
//! size and mtime match), 2 diff with early moves, 3 extract and precompute
//! thumbnails (parallel), 4 upsert; then sidecars, move reconciliation, missing
//! records, the crawled-folder record and lookup pruning. The database phases
//! are `storage::crawl`.
//!
//! What needs the server's optional libraries (PDF previews, CLIP vectors) goes
//! to the [`Host`]; without them the crawl continues without that piece.

use std::collections::{HashMap, HashSet};
use std::path::Path;

use rayon::prelude::*;

use crate::archive;
use crate::metadata::{self, Metadata};
use crate::path_pattern;
use crate::rules::{self, Checks, MemberPath, PresupportPatterns};
use crate::storage::{self, crawl as db, writes, sidecar};
use crate::thumbstore::ThumbStore;
use crate::winpath::WinPath;

const BATCH_SIZE: usize = 50;
const MAX_CONSECUTIVE_FAILURES: usize = 50;
pub(crate) const SIZE_LIMIT_IMAGE: u64 = 50 * 1024 * 1024;
const SIZE_LIMIT_META: u64 = 1024 * 1024;

/// The crawl settings (`CrawlerConfig` plus the source folder and pattern).
#[derive(Debug, Clone, Default)]
pub struct CrawlConfig {
    pub source_dir: String,
    /// The pattern given for this crawl of a part of a library folder, to keep
    /// for `source_dir` (`""`: forget the one kept).
    pub keep_pattern: Option<String>,
    /// Crawl `source_dir` as a part of the library folder at this root: its
    /// records stay that folder's (pattern and metadata read from its root),
    /// and `source_dir` is not made a library folder.
    pub library_root: Option<String>,
    pub pattern: String,
    pub recurse: bool,
    pub force_rescan: bool,
    /// An archive of the stored size is unchanged whatever its modification time.
    pub size_only: bool,
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
    pub defer_missing_deletion: bool,
    pub upsert: db::UpsertConfig,
}

impl CrawlConfig {
    /// The library folder's root: paths are read relative to it.
    fn root(&self) -> &str {
        self.library_root.as_deref().unwrap_or(&self.source_dir)
    }
}

#[derive(Debug, Default)]
pub struct Extracted {
    pub contents: Vec<(String, Vec<u8>)>,
    pub names: Vec<String>,
    pub stl_info: Vec<(String, Option<i64>)>,
    pub uncompressed: Option<i64>,
    /// Why `contents` is empty though the catalog was read (no UnRAR, an
    /// encrypted member, a compression method the readers lack).
    pub unread: Option<String>,
}

/// What the crawl reports, in order.
#[derive(Debug, Clone)]
pub enum Event {
    Log(String),
    Phase(String),
    Warning(String),
    Progress(usize, usize),
    /// Archive path, `"new"` or `"updated"`.
    Result(String, String),
    Deleted(String),
}

pub trait Host: Sync {
    /// A thumbnail of a PDF's first page; `None` without a PDF library.
    fn pdf_thumbnail(&self, pdf: &[u8]) -> Option<Vec<u8>>;
    /// CLIP vectors (float32 blobs) of JPEGs; `None` when none can be computed.
    fn embed(&self, jpegs: &[Vec<u8>]) -> Option<Vec<Option<Vec<u8>>>>;
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
    /// Records matched to an archive that moved.
    pub moved: usize,
    /// Records deleted, and records flagged missing (kept).
    pub deleted: usize,
    pub marked: usize,
    pub new_paths: Vec<String>,
    /// The folder listing cannot be trusted (a folder could not be read, or a
    /// library with records came back empty): what it lacks is not gone.
    pub listing_incomplete: bool,
}

// ── listing ──────────────────────────────────────────────────────────────────

fn suffix_lower(name: &str) -> String {
    WinPath::new(name).suffix().to_lowercase()
}

fn is_archive_suffix(name: &str) -> bool {
    rules::ARCHIVE_SUFFIXES.contains(&suffix_lower(name).as_str())
}

/// Windows `sorted(paths)`: by the lowercased path components.
fn sort_key(p: &str) -> Vec<String> {
    p.to_lowercase().split(['\\', '/']).map(str::to_string).collect()
}

fn sort_paths(v: &mut [String]) {
    v.sort_by_cached_key(|p| sort_key(p));
}

/// `iter_archives` (`rglob("*")`, no symlinked folders followed) or the
/// folder's own archive files, sorted, without `METADATA.7z`. Only files count:
/// a folder named like an archive (`Dragon.7z/`) is entered, not listed.
/// Folders in `skip` (the roots of libraries inside this one) are not entered.
pub fn list_archives(root: &str, recurse: bool, skip: &[String]) -> Vec<String> {
    list_archives_stat(root, recurse, skip).files.into_iter().map(|(p, _)| p).collect()
}

/// What [`list_archives_stat`] found.
#[derive(Debug, Default)]
pub struct Listing {
    /// Each archive with its `(size, mtime_ns)`.
    pub files: Vec<(String, Option<(i64, i64)>)>,
    /// Folders that could not be read (in full): whether their archives
    /// still exist is unknown, so no record below them may be judged gone.
    pub unreadable: Vec<String>,
}

/// [`list_archives`] with each archive's `(size, mtime_ns)` as the directory
/// listing gives it (on Windows it comes with the listing, without opening or
/// asking about each file; `None` when it could not be had).
pub fn list_archives_stat(root: &str, recurse: bool, skip: &[String]) -> Listing {
    let skip: Vec<String> = skip.iter().map(|s| crate::relpath::path_key(s)).collect();
    let mut out = Vec::new();
    let mut unreadable = Vec::new();
    let join = crate::winpath::join;
    let mut stack = vec![root.to_string()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else {
            unreadable.push(dir);
            continue;
        };
        let mut complete = true;
        for e in rd {
            let Ok(e) = e else {
                complete = false;
                continue;
            };
            let name = e.file_name().to_string_lossy().into_owned();
            let path = join(&dir, &name);
            let ft = e.file_type().ok();
            let is_dir = ft.is_some_and(|t| t.is_dir());
            let archive_name = is_archive_suffix(&name) && name != sidecar::METADATA_FILENAME;
            if recurse {
                // A link to a file counts (its target's size and time).
                let md = match ft {
                    Some(t) if t.is_file() && archive_name => e.metadata().ok(),
                    Some(t) if t.is_symlink() && archive_name => std::fs::metadata(&path).ok().filter(|m| m.is_file()),
                    _ => None,
                };
                if let Some(md) = md {
                    out.push((path.clone(), db::stat_of(&md)));
                }
                if is_dir && !skip.contains(&crate::relpath::path_key(&path)) {
                    stack.push(path);
                }
            } else if ft.is_some_and(|t| t.is_file()) && archive_name {
                let stat = e.metadata().ok().and_then(|md| db::stat_of(&md));
                out.push((path, stat));
            }
        }
        if !complete {
            unreadable.push(dir);
        }
        if !recurse {
            break;
        }
    }
    out.sort_by_cached_key(|(p, _)| sort_key(p));
    Listing { files: out, unreadable }
}

// ── phase 1 ──────────────────────────────────────────────────────────────────

pub(crate) fn fallback_fingerprint(path: &str) -> String {
    match db::stat(path) {
        Some((size, mtime)) => format!("fallback:{size}:{mtime}"),
        None => "fallback:0:0".into(),
    }
}

/// The fingerprint, and the catalog it came from when the native readers
/// read it (stored as the record's file list).
fn hash_one(path: &str) -> (String, Option<Vec<archive::ArchiveMember>>) {
    match archive::list_members(Path::new(path)) {
        Ok(members) => (archive::fingerprint_of(&members), Some(members)),
        Err(_) => (fallback_fingerprint(path), None),
    }
}

/// An archive's file list, as `store_files` takes it.
type FileList = (String, Vec<archive::ArchiveMember>);

/// Phase 1's fingerprints, the file lists it read, and the archives taken from sidecars.
type Phase1 = (Vec<String>, Vec<FileList>, Vec<String>);

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

const STL_EXTS: [&str; 7] = [".stl", ".obj", ".lys", ".lyt", ".chitubox", ".ctp", ".ags"];

/// The archive's catalog and its `.url` and `info.json` members, with its first
/// `max_images` images and PDFs (`None`: every one; in a solid archive, reading
/// stops after the last member wanted). `None` where the catalog cannot be read.
pub(crate) fn extract_native(path: &str, unrar: &str, max_images: Option<usize>) -> Option<Extracted> {
    let members = archive::list_members(Path::new(path)).ok()?;
    Some(extract_listed(path, &members, unrar, max_images))
}

/// [`extract_native`] of an archive whose catalog is at hand.
fn extract_listed(path: &str, members: &[archive::ArchiveMember], unrar: &str, max_images: Option<usize>) -> Extracted {
    let p = Path::new(path);
    let names: Vec<String> = members.iter().map(|m| m.name.clone()).collect();
    let uncompressed = Some(members.iter().filter_map(|m| m.size).sum::<u64>() as i64);
    let stl_info = stl_dict(members.iter().map(|m| (m.name.clone(), m.size.map(|s| s as i64))));
    let size_by_name: HashMap<&str, Option<u64>> = members.iter().map(|m| (m.name.as_str(), m.size)).collect();
    let mut images = 0;
    let to_read: Vec<String> = names
        .iter()
        .filter(|n| is_wanted(n))
        .filter(|n| size_by_name.get(n.as_str()).copied().flatten().is_none_or(|s| s <= size_limit(n)))
        .filter(|n| {
            images += usize::from(is_image_or_pdf(n));
            !is_image_or_pdf(n) || max_images.is_none_or(|max| images <= max)
        })
        .cloned()
        .collect();
    // Members that cannot be read cost the thumbnails, not the catalog read above.
    let (read, unread) = if to_read.is_empty() {
        (Vec::new(), None)
    } else {
        match archive::read_members_with(p, &to_read, unrar) {
            Ok(read) => (read, None),
            Err(e) => (Vec::new(), Some(e.to_string())),
        }
    };
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
    Extracted { contents, names, stl_info, uncompressed, unread }
}

/// An archive's `.url` and `info.json` members (name, bytes; each at most 1 MB),
/// or `None` when it cannot be read.
pub fn link_files(path: &str, unrar: &str) -> Option<Vec<(String, Vec<u8>)>> {
    let p = Path::new(path);
    let wanted: Vec<String> = archive::list_members(p)
        .ok()?
        .into_iter()
        .filter(|m| (metadata::is_url_file(&m.name) || metadata::is_info_json(&m.name)) && m.size.is_none_or(|s| s <= SIZE_LIMIT_META))
        .map(|m| m.name)
        .collect();
    if wanted.is_empty() {
        return Some(Vec::new());
    }
    let read = archive::read_members_with(p, &wanted, unrar).ok()?;
    Some(read.into_iter().filter(|(_, b)| b.len() as u64 <= SIZE_LIMIT_META).collect())
}

/// The STL file list (printable files: name, size) of an archive's catalog.
pub fn stl_list(members: &[archive::ArchiveMember]) -> Vec<(String, Option<i64>)> {
    stl_dict(members.iter().map(|m| (m.name.clone(), m.size.map(|s| s as i64))))
}

/// `{name: size}` for the printable members: a repeated name keeps its first
/// position and its last size.
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

/// A thumbnail of an image, or through the host of a PDF.
fn thumbnail(raw: &[u8], size: u32, pdf: bool, host: &dyn Host) -> Option<Vec<u8>> {
    if pdf { host.pdf_thumbnail(raw) } else { crate::thumbnail::make_thumbnail(raw, size, true).ok() }
}

struct Prepared {
    extracted: Extracted,
    /// `(name, jpeg, dhash)` for each thumbnail candidate.
    precomp: Vec<(String, Option<Vec<u8>>, Option<String>)>,
}

fn prepare(path: &str, members: Option<&Vec<archive::ArchiveMember>>, cfg: &CrawlConfig, host: &dyn Host) -> Prepared {
    let max = Some(cfg.max_thumbnails);
    let mut extracted = match members {
        Some(members) => extract_listed(path, members, &cfg.unrar, max),
        None => extract_native(path, &cfg.unrar, max).unwrap_or_default(),
    };
    if let Some(why) = &extracted.unread {
        host.emit(Event::Warning(format!("{}: images not read ({why}); its file list is kept", WinPath::new(path).name())));
    }
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
    // A batch waits for its last archive: only the small files are needed from here on.
    extracted.contents.retain(|(name, _)| !is_image_or_pdf(name));
    Prepared { extracted, precomp }
}

// ── phase 4 inputs ───────────────────────────────────────────────────────────

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

/// What `pattern` reads from the archive's path below `root`.
pub fn match_pattern(pattern: &str, path: &str, root: &str) -> Option<db::Extracted> {
    if pattern.is_empty() {
        return None;
    }
    let fields = path_pattern::parse_path(pattern, &rel_path(path, root));
    if fields.is_empty() {
        return None;
    }
    let get = |k: &str| fields.iter().find(|(f, _)| f == k).map(|(_, v)| v.clone());
    let sequence = get("sequence").filter(|v| !v.is_empty()).and_then(|v| crate::pyunicode::py_int(writes::py_strip(&v)));
    let release_date = get("release_date").filter(|v| !v.is_empty()).map(|v| path_pattern::parse_release_date(&v));
    Some(db::Extracted { fields, release_date, sequence })
}

/// Presupports, warnings and scales from an archive's member names.
fn rules_for(patterns: &PresupportPatterns, checks: Checks, archive_name: &str, names: &[String]) -> (String, Option<String>, Vec<String>) {
    let warnings = rules::detect_warnings(names, checks, patterns).unwrap_or_default();
    let mut all = vec![archive_name.to_string()];
    all.extend(names.iter().cloned());
    (rules::detect_presupport(names).to_string(), Some(warnings).filter(|w| !w.is_empty()), rules::detect_scale(&all))
}

/// An image beside an archive: its path, thumbnail and dHash.
type FolderImage = (String, Option<Vec<u8>>, Option<String>);

/// The images directly in folder `dir`, in order, in parallel.
fn images_in(dir: &str, pool: &rayon::ThreadPool, cfg: &CrawlConfig, host: &dyn Host) -> Vec<FolderImage> {
    let mut files: Vec<String> = std::fs::read_dir(dir)
        .map(|rd| {
            rd.flatten()
                .filter(|e| e.file_type().is_ok_and(|t| t.is_file()))
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .filter(|n| rules::IMAGE_SUFFIXES.contains(&suffix_lower(n).as_str()))
                .map(|n| crate::winpath::join(dir, &n))
                .collect()
        })
        .unwrap_or_default();
    sort_paths(&mut files);
    pool.install(|| {
        files
            .into_par_iter()
            .filter_map(|f| {
                let raw = std::fs::read(&f).ok()?;
                let jpeg = thumbnail(&raw, cfg.max_thumb_size, false, host);
                let dh = jpeg.as_deref().and_then(dhash_text);
                Some((f, jpeg, dh))
            })
            .collect()
    })
}

fn metadata_for(path: &str, contents: &[(String, Vec<u8>)], cfg: &CrawlConfig) -> Metadata {
    let files: Vec<(String, Vec<u8>)> = contents
        .iter()
        .filter(|(n, _)| n.to_lowercase().ends_with(".url") || n.to_lowercase().ends_with("info.json"))
        .cloned()
        .collect();
    metadata::extract(path, &files, Some(cfg.root()))
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
    /// The presupport patterns, compiled once, and the checks they run under.
    patterns: PresupportPatterns,
    checks: Checks,
    candidates: HashMap<String, i64>,
    /// Hashes shared by several missing records: which one moved is unknown.
    ambiguous: HashSet<String>,
    /// Records outside the crawled folder, and those of them found gone.
    outside: db::Outside,
    gone_outside: Vec<db::Missing>,
    names: HashMap<i64, String>, // missing id -> file name
    early_moved: HashSet<i64>,
    moved_new_paths: HashSet<String>,
    existing_hashes: HashMap<String, String>,
    /// Roots of the libraries inside the crawled one (not entered).
    inner_roots: Vec<String>,
    /// `(size, mtime_ns)` of the archives, from the folder listing.
    listed: HashMap<String, (i64, i64)>,
    /// The archives (`relpath::path_key`s) whose records this crawl imported from sidecars.
    from_sidecar: HashSet<String>,
    /// Records of archives not found are flagged, whatever `defer_missing_deletion` says.
    keep_records: bool,
    /// The patterns of the library folder's sub-folders, ahead of `cfg.pattern`.
    folder_patterns: storage::libraries::FolderPatterns,
    /// The images of the current archive's folders: archives come in path order,
    /// so its neighbours share them, and each is decoded once.
    folder_cache: HashMap<String, Vec<FolderImage>>,
}

fn tx(conn: &mut rusqlite::Connection) -> rusqlite::Result<rusqlite::Transaction<'_>> {
    conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
}

impl Run<'_> {
    /// `moves.index_by_hash`: a missing record, as a candidate for what a new archive is.
    fn index_missing(&mut self, (id, _, h, name, _): &db::Missing) {
        self.names.insert(*id, name.clone());
        if h.is_empty() || h.starts_with("fallback:") {
            return;
        }
        if self.candidates.contains_key(h) || self.ambiguous.contains(h) {
            self.candidates.remove(h);
            self.ambiguous.insert(h.clone());
        } else {
            self.candidates.insert(h.clone(), *id);
        }
    }

    /// The pattern for the archive at `path`.
    fn pattern_of(&self, path: &str) -> &str {
        let own = crate::relpath::RelPath::from_abs(self.cfg.root(), path).and_then(|r| self.folder_patterns.below(r.folder()));
        own.unwrap_or(&self.cfg.pattern)
    }

    fn log(&self, s: impl Into<String>) {
        self.host.emit(Event::Log(s.into()));
    }

    /// The batch's fingerprints, the file lists of the archives whose catalog
    /// was read for them (not those whose stored hash was kept), and the
    /// archives taken as their sidecar describes them (not opened).
    fn phase1(&mut self, batch: &[String]) -> Result<Phase1, CrawlError> {
        let stats: Vec<Option<(i64, i64)>> =
            self.pool.install(|| batch.par_iter().map(|p| self.listed.get(p).copied().or_else(|| db::stat(p))).collect());
        let existing: HashMap<String, (String, Option<i64>, Option<i64>)> =
            db::existing(&self.conn, batch)?.into_iter().map(|(p, h, s, m)| (p, (h, s, m))).collect();
        for (p, (h, ..)) in &existing {
            self.existing_hashes.insert(p.clone(), h.clone());
        }
        let force = self.cfg.force_rescan;
        let size_only = self.cfg.size_only;
        let from_sidecar = &self.from_sidecar;
        let hashed: Vec<(String, Option<Vec<archive::ArchiveMember>>, bool)> = self.pool.install(|| {
            batch
                .par_iter()
                .zip(stats.par_iter())
                .map(|(p, st)| {
                    if !force
                        && let (Some((h, size, mtime)), Some(now)) = (existing.get(p), st)
                        && !h.is_empty()
                        && !h.starts_with("fallback:")
                    {
                        // An unchanged archive keeps its fingerprint (one the readers failed on
                        // is read again: they may read it now).
                        if db::unchanged(*size, *mtime, *now, size_only) {
                            return (h.clone(), None, false);
                        }
                        // Just imported from a sidecar: taken as the sidecar describes it, without
                        // opening it, unless the size the sidecar recorded differs (it changed).
                        if from_sidecar.contains(&crate::relpath::path_key(p)) && size.is_none_or(|s| s == now.0) {
                            return (h.clone(), None, true);
                        }
                    }
                    let (h, members) = hash_one(p);
                    (h, members, false)
                })
                .collect()
        });
        let mut hashes = Vec::with_capacity(hashed.len());
        let mut lists = Vec::new();
        let mut trusted = Vec::new();
        for (p, (h, members, from_sidecar)) in batch.iter().zip(hashed) {
            hashes.push(h);
            if let Some(m) = members {
                lists.push((p.clone(), m));
            }
            if from_sidecar {
                trusted.push(p.clone());
            }
        }
        Ok((hashes, lists, trusted))
    }

    /// Store the file lists of the archives whose catalog this batch read. A
    /// crawl opens only new and changed archives: those without a list keep
    /// none until they are read (*Read missing file lists*, Rescan).
    fn store_files(&mut self, lists: Vec<FileList>) -> Result<(), CrawlError> {
        if lists.is_empty() {
            return Ok(());
        }
        let t = tx(&mut self.conn)?;
        db::store_files(&t, &lists)?;
        t.commit()?;
        Ok(())
    }

    fn batch(&mut self, batch: &[String], start: usize, modified: &mut HashSet<String>) -> Result<bool, CrawlError> {
        let (hashes, lists, trusted) = self.phase1(batch)?;
        if start == 0 {
            self.host.emit(Event::Phase("Checking database for changes…".into()));
        }
        let pairs: Vec<(String, String)> = batch.iter().cloned().zip(hashes.iter().cloned()).collect();
        for (p, h) in &pairs {
            if self.existing_hashes.get(p) != Some(h) {
                for m in self.outside.gone_with_hash(h) {
                    self.index_missing(&m);
                    self.gone_outside.push(m);
                }
            }
        }
        let read: HashSet<String> = lists.iter().map(|(p, _)| p.clone()).collect();
        let trusted: HashSet<String> = trusted.into_iter().collect();
        let diff = {
            let t = tx(&mut self.conn)?;
            let d = db::diff(&t, &pairs, self.cfg.force_rescan, &read, &trusted, &self.candidates)?;
            t.commit()?;
            d
        };
        for p in &diff.unreadable {
            self.host
                .emit(Event::Warning(format!("cannot read archive (damaged, or in a format this server does not read): {}", WinPath::new(p).name())));
        }
        for h in &diff.consumed {
            let Some(id) = self.candidates.remove(h) else { continue };
            if let Some((_, _, new_path)) = diff.moved.iter().find(|(mh, _, _)| mh == h) {
                self.early_moved.insert(id);
                self.moved_new_paths.insert(new_path.clone());
                let name = self.names.get(&id).cloned().unwrap_or_default();
                self.log(format!("Moved: {name} → {new_path}"));
            }
        }
        if !diff.to_extract.is_empty() && start == 0 {
            self.host.emit(Event::Phase("Extracting new/changed archives…".into()));
        }
        let (cfg, host) = (self.cfg, self.host);
        let members_of: HashMap<&str, &Vec<archive::ArchiveMember>> = lists.iter().map(|(p, m)| (p.as_str(), m)).collect();
        let prepared: HashMap<String, Prepared> = self
            .pool
            .install(|| diff.to_extract.par_iter().map(|p| (p.clone(), prepare(p, members_of.get(p.as_str()).copied(), cfg, host))).collect());
        // Records without an STL list whose catalog was read anyway get it from that catalog
        // (no archive is opened for it).
        let hash_of: HashMap<&str, &str> = pairs.iter().map(|(p, h)| (p.as_str(), h.as_str())).collect();
        let stl_lists: Vec<db::StlList> = diff
            .stl_backfill
            .iter()
            .filter_map(|p| {
                let m = members_of.get(p.as_str())?;
                Some((p.clone(), hash_of.get(p.as_str()).copied().unwrap_or_default().to_string(), stl_list(m)))
            })
            .collect();
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
                extracted: match_pattern(self.pattern_of(p), p, cfg.root()),
                changed,
            });
            if host.should_stop() {
                break;
            }
        }
        let results = {
            let t = tx(&mut self.conn)?;
            let r = db::upsert_batch(&t, self.store, &cfg.upsert, &inputs, self.failures, MAX_CONSECUTIVE_FAILURES)?;
            db::store_stl_lists(&t, &stl_lists)?;
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
                        if self.moved_new_paths.contains(&input.path) {
                            modified.insert(WinPath::new(&input.path).parent().as_posix());
                        }
                    }
                }
            }
            self.host.emit(Event::Progress(idx, total));
        }
        self.store_files(lists)?;
        Ok(host.should_stop())
    }

    /// The images in the archive's folder and those above it, up to the library's root.
    fn folder_images(&mut self, path: &str) -> Vec<FolderImage> {
        let (cfg, host) = (self.cfg, self.host);
        let mut out = Vec::new();
        let mut chain = HashSet::new();
        let source = WinPath::new(cfg.root()).as_posix().to_lowercase();
        let mut folder = WinPath::new(path).parent();
        loop {
            let posix = folder.as_posix();
            if metadata::win_relative_parts(&posix, cfg.root()).is_none() {
                break;
            }
            let dir = crate::winpath::native(&posix);
            let images = self.folder_cache.entry(dir.clone()).or_insert_with(|| images_in(&dir, &self.pool, cfg, host));
            out.extend(images.iter().cloned());
            chain.insert(dir);
            if posix.to_lowercase() == source {
                break;
            }
            let parent = folder.parent();
            if parent == folder {
                break;
            }
            folder = parent;
        }
        self.folder_cache.retain(|dir, _| chain.contains(dir));
        out
    }

    fn changed(&mut self, path: &str, hash: &str, existing_id: Option<i64>, prep: &Prepared) -> db::Changed {
        let (cfg, host) = (self.cfg, self.host);
        let x = &prep.extracted;
        let meta = metadata_for(path, &x.contents, cfg);
        let archive_name = WinPath::new(path).name().to_string();
        let (presupports, warnings, scales) = rules_for(&self.patterns, self.checks, &archive_name, &x.names);
        let (year, month) = path_pattern::parse_release_date(meta.release_date.as_deref().unwrap_or(""));
        let mut thumbs: Vec<db::Thumb> =
            prep.precomp.iter().map(|(n, j, d)| (n.clone(), j.clone(), if j.is_some() { d.clone() } else { None }, None)).collect();
        if cfg.embed {
            let resolved: Vec<usize> = (0..thumbs.len()).filter(|&i| thumbs[i].1.is_some()).collect();
            let jpegs: Vec<Vec<u8>> = resolved.iter().map(|&i| thumbs[i].1.clone().unwrap_or_default()).collect();
            if let Some(v) = host.embed(&jpegs) {
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
            url: meta.url,
            release: meta.release,
            tags: meta.tags,
            presupports,
            warnings,
            scales,
            uncompressed_size: x.uncompressed,
            thumbs,
            folder_images: if fresh { self.folder_images(path) } else { Vec::new() },
            stl_info: x.stl_info.clone(),
        }
    }
}

/// Crawl `cfg.source_dir` into the database at `db_path`.
pub fn crawl(db_path: &Path, store: Option<&ThumbStore>, cfg: &CrawlConfig, host: &dyn Host) -> Result<Summary, CrawlError> {
    let conn = storage::open(db_path)?;
    let (patterns, checks) = match PresupportPatterns::new(Some(&cfg.presupport_patterns)) {
        Ok(patterns) => (patterns, cfg.warn),
        Err(e) => {
            host.emit(Event::Warning(format!("{e}: presupport warnings are skipped in this crawl.")));
            let patterns = PresupportPatterns::new::<&str>(None).map_err(|e| CrawlError::Io(std::io::Error::other(e.to_string())))?;
            (patterns, Checks { presupported: false, ..cfg.warn })
        }
    };
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
        patterns,
        checks,
        candidates: HashMap::new(),
        ambiguous: HashSet::new(),
        outside: db::Outside::default(),
        gone_outside: Vec::new(),
        names: HashMap::new(),
        early_moved: HashSet::new(),
        moved_new_paths: HashSet::new(),
        existing_hashes: HashMap::new(),
        inner_roots: Vec::new(),
        listed: HashMap::new(),
        from_sidecar: HashSet::new(),
        keep_records: false,
        folder_patterns: Default::default(),
        folder_cache: HashMap::new(),
    };
    // The crawled folder is a library (made now when it is none yet), or a part of
    // one (`library_root`); the libraries inside it are crawled on their own.
    let library = match &cfg.library_root {
        Some(root) => storage::libraries::Roots::load(&run.conn)?
            .locate_root(root)
            .ok_or_else(|| CrawlError::Io(std::io::Error::other(format!("{root} is not a library folder"))))?,
        None => {
            let t = tx(&mut run.conn)?;
            let id = storage::libraries::create(&t, &cfg.source_dir, storage::libraries::Target::Around, None, None)?;
            t.commit()?;
            id
        }
    };
    run.inner_roots = storage::libraries::Roots::load(&run.conn)?.inner(library).into_iter().map(|(_, r)| r).collect();
    if let Some(pattern) = &cfg.keep_pattern
        && let Some(rel) = crate::relpath::RelPath::from_abs(cfg.root(), &cfg.source_dir)
    {
        let t = tx(&mut run.conn)?;
        storage::libraries::set_folder_pattern(&t, library, rel.as_str(), pattern)?;
        t.commit()?;
    }
    run.folder_patterns = storage::libraries::FolderPatterns::load(&run.conn, library)?;
    // One listing of the folder gives the archives with their sizes and times: the
    // comparisons with the records below happen in memory.
    host.emit(Event::Phase("Retrieving file list…".into()));
    let Listing { files, unreadable } = list_archives_stat(&cfg.source_dir, cfg.recurse, &run.inner_roots);
    for dir in &unreadable {
        host.emit(Event::Warning(format!("cannot read the folder {dir}: its records stay as they are")));
    }
    run.summary.listing_incomplete = !unreadable.is_empty();
    let archives: Vec<String> = files.iter().map(|(p, _)| p.clone()).collect();
    run.listed = files.into_iter().filter_map(|(p, s)| s.map(|s| (p, s))).collect();
    host.emit(Event::Phase("Scanning for deleted files…".into()));
    // A full (recursive) listing of a whole library folder covers every one of its
    // records; for a part of one, records are looked for on disk.
    let keys: HashSet<String> = archives.iter().map(|p| crate::relpath::path_key(p)).collect();
    let whole = cfg.recurse && cfg.library_root.is_none();
    let db::MissingScan { missing, back, outside } =
        db::collect_missing(&run.conn, whole.then_some((library, &keys)), &unreadable, &cfg.source_dir, &run.inner_roots)?;
    run.outside = db::Outside::new(outside);
    if !back.is_empty() {
        let t = tx(&mut run.conn)?;
        storage::maintenance::clear_missing(&t, &back)?;
        t.commit()?;
        run.log(format!("{} archive(s) flagged missing are back on disk.", back.len()));
    }
    // A folder that lost every archive at once is more likely an unmounted share
    // than a deliberate clean-up: flagged records come back by themselves.
    if archives.is_empty() && missing.iter().any(|m| storage::libraries::contains(&cfg.source_dir, &m.1)) {
        run.keep_records = true;
        run.summary.listing_incomplete = true;
        host.emit(Event::Warning(format!(
            "{} holds no archives, but records do: is its drive or share connected? They are flagged missing, not deleted \
             (Remove missing archives deletes them).",
            cfg.source_dir
        )));
    }
    if !missing.is_empty() {
        run.log(format!("{} recorded archive(s) not found on disk — checked for moves after the crawl.", missing.len()));
    }
    for m in &missing {
        run.index_missing(m);
    }

    let total = archives.len();
    run.summary.total = total;
    let msg = format!("Found {total} archives in {}", cfg.source_dir);
    run.log(msg.clone());
    host.emit(Event::Phase(msg));

    if cfg.sidecars
        && let Some(st) = store
    {
        // The folders in crawl order, with their archives.
        let mut folders: Vec<(String, Vec<String>)> = Vec::new();
        let mut at: HashMap<String, usize> = HashMap::new();
        for p in &archives {
            let folder = crate::winpath::native(&WinPath::new(p).parent().as_posix());
            match at.get(&folder) {
                Some(&i) => folders[i].1.push(p.clone()),
                None => {
                    at.insert(folder.clone(), folders.len());
                    folders.push((folder, vec![p.clone()]));
                }
            }
        }
        let root = WinPath::new(cfg.root()).as_posix();
        for (folder, paths) in &folders {
            if host.should_stop() {
                break;
            }
            if !Path::new(folder).join(sidecar::METADATA_FILENAME).exists() {
                continue;
            }
            // Importing only adds the archives without a record, and reading a sidecar
            // decompresses all its thumbnails: a folder whose archives all have one is left
            // alone (but the crawled folder's own sidecar can still give the library its pattern).
            let is_root = WinPath::new(folder).as_posix().trim_end_matches('/').eq_ignore_ascii_case(root.trim_end_matches('/'));
            if !is_root && db::existing(&run.conn, paths)?.len() == paths.len() {
                continue;
            }
            let folder = folder.clone();
            let imported = {
                let read = sidecar::read(&folder);
                let t = tx(&mut run.conn)?;
                match read.and_then(|s| sidecar::import(&t, st, &folder, s)) {
                    Ok(v) => {
                        t.commit()?;
                        run.from_sidecar.extend(v.iter().map(|p| crate::relpath::path_key(p)));
                        v.len()
                    }
                    Err(e) => {
                        drop(t);
                        host.emit(Event::Warning(format!("cannot import METADATA.7z in {folder}: {e}")));
                        0
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
                Ok(_) => run.log(format!("  Updated METADATA.7z in {}", WinPath::new(&folder).name())),
                Err(e) => host.emit(Event::Warning(format!("cannot write METADATA.7z in {folder}: {e}"))),
            }
        }
    }

    if !run.early_moved.is_empty() {
        run.log(format!("Matched {} moved file(s) by content, without re-extracting.", run.early_moved.len()));
    }
    // New archives may also be records from elsewhere that hashes did not tell.
    for path in run.summary.new_paths.clone() {
        if let Some(&(size, _)) = run.listed.get(&path) {
            let gone = run.outside.gone_with_name(WinPath::new(&path).name(), size);
            for m in gone {
                run.names.insert(m.0, m.3.clone());
                run.gone_outside.push(m);
            }
        }
    }
    let gone_outside = std::mem::take(&mut run.gone_outside);
    let missing: Vec<db::Missing> = missing.into_iter().chain(gone_outside).filter(|m| !run.early_moved.contains(&m.0)).collect();
    if !missing.is_empty() {
        host.emit(Event::Phase(format!("Looking for {} archive(s) not found on disk among the new ones…", missing.len())));
        reconcile(&mut run, missing)?;
    }

    if cfg.library_root.is_none() {
        let now = writes::utc_now_text();
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
        let pruned = writes::prune_unused_lookups(&t)?;
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
    run.summary.moved += run.early_moved.len();
    let s = &run.summary;
    let mut done = format!("Crawl complete: {} new, {} updated, {} unchanged", s.new, s.updated, s.unchanged);
    for (n, what) in [(s.moved, "moved"), (s.deleted, "deleted"), (s.marked, "marked missing")] {
        if n > 0 {
            done.push_str(&format!(", {n} {what}"));
        }
    }
    run.log(format!("{done}."));
    Ok(run.summary)
}

fn delete_refs(store: Option<&ThumbStore>, refs: &[String]) {
    let ids = db::ref_ids(refs);
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
        for (id, _, new_path) in moves {
            matched.insert(id);
            let name = run.names.get(&id).cloned().unwrap_or_default();
            run.log(format!("Moved: {name} → {new_path}"));
        }
        delete_refs(run.store, &refs);
        if !matched.is_empty() {
            run.log(format!("Matched {} moved file(s).", matched.len()));
        }
        run.summary.moved += matched.len();
    }
    let unmatched: Vec<db::Missing> = missing.into_iter().filter(|m| !matched.contains(&m.0)).collect();
    if unmatched.is_empty() {
        return Ok(());
    }
    let (deleted, marked, deferred, refs) = {
        let t = tx(&mut run.conn)?;
        let defer = run.cfg.defer_missing_deletion || run.keep_records;
        let r = db::mark_missing(&t, &unmatched, &run.cfg.source_dir, &run.inner_roots, defer)?;
        t.commit()?;
        r
    };
    for p in &deleted {
        run.log(format!("Deleted: {p}"));
        run.host.emit(Event::Deleted(p.clone()));
    }
    for p in &marked {
        run.log(format!("Not found on disk (record kept, marked missing): {p}"));
    }
    (run.summary.deleted, run.summary.marked) = (deleted.len(), marked.len() + deferred);
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
    fn only_the_images_used_are_read() {
        use std::io::Write;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a.zip");
        let mut z = zip::ZipWriter::new(std::fs::File::create(&path).unwrap());
        let o = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
        for name in ["1.jpg", "model.stl", "2.png", "info.json", "3.jpg", "link.url", "4.pdf"] {
            z.start_file(name, o).unwrap();
            z.write_all(b"data").unwrap();
        }
        z.finish().unwrap();
        let read = |max| {
            let x = extract_native(&path.to_string_lossy(), "", max).unwrap();
            assert_eq!(x.names.len(), 7, "the catalog is whole");
            x.contents.into_iter().map(|(n, _)| n).collect::<Vec<_>>()
        };
        assert_eq!(read(Some(2)), ["1.jpg", "2.png", "info.json", "link.url"]);
        assert_eq!(read(Some(0)), ["info.json", "link.url"]);
        assert_eq!(read(None).len(), 6);
    }

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
        std::fs::create_dir_all(dir.path().join("D.7z")).unwrap();
        for f in ["a.ZIP", "B/c.7z", "b.rar", "METADATA.7z", "x.txt", "B/METADATA.7z", "D.7z/e.zip"] {
            std::fs::write(dir.path().join(f), b"x").unwrap();
        }
        let names = |v: Vec<String>| v.iter().map(|p| p[root.len() + 1..].to_string()).collect::<Vec<_>>();
        // Python sorts Windows paths by lowercased components: "b" < "b.rar".
        // The folder `D.7z` is entered, not listed.
        let sep = crate::winpath::SEP;
        assert_eq!(names(list_archives(&root, true, &[])), ["a.ZIP", &format!("B{sep}c.7z"), "b.rar", &format!("D.7z{sep}e.zip")]);
        assert_eq!(names(list_archives(&root, false, &[])), ["a.ZIP", "b.rar"]);
        assert_eq!(names(list_archives(&root, true, &[crate::winpath::join(&root, "B")])), ["a.ZIP", "b.rar", &format!("D.7z{sep}e.zip")], "an inner library");
        // The listing's sizes and times are what asking each file gives.
        for (p, stat) in list_archives_stat(&root, true, &[]).files {
            assert_eq!(stat, db::stat(&p), "{p}");
        }
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
