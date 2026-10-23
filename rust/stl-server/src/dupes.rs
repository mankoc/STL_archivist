//! The duplicate finders, as job bodies (the desktop's Find Duplicates and
//! Find Duplicate Archives dialogs): results are JSON for the job's `result`.

use std::collections::{BTreeMap, HashMap};
use std::path::Path;

use serde::Serialize;
use stl_core::control::Control;
use utoipa::ToSchema;
use stl_core::storage::{duplicates, schema_sql, vectors, views};

use crate::Library;

/// Most result rows kept (`image_duplicates.MAX_RESULTS`).
pub const MAX_RESULTS: usize = 100_000;
const DHASH_BATCH: usize = 1024;

/// What a finder or maintenance job found (`JobDetail.result`).
#[derive(Debug, Clone, Serialize, ToSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum JobResult {
    Content { pairs: Vec<ContentRow> },
    Images(ImageResult),
    Metadata { groups: Vec<MetadataGroup> },
    /// Records of a maintenance list (Verify, duplicate entries, moved, found).
    Records { rows: Vec<crate::maint::RecordRow> },
    /// The AI tagger's suggestion for one model (`ai_suggest`).
    AiSuggestion(Box<crate::ai::AiSuggestion>),
    /// What moving a library to a new root found (`relocate_library`).
    Relocation(Relocation),
}

/// The check (and move) of a library to a new root.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct Relocation {
    pub library: i64,
    pub root: String,
    pub total: usize,
    pub found: usize,
    /// Found only with another letter case.
    pub case_only: usize,
    pub missing: usize,
    /// Some relative paths not found.
    pub missing_examples: Vec<String>,
    /// Libraries inside it, moved along.
    pub inner: usize,
    /// The root was changed (else only checked).
    pub applied: bool,
}

impl JobResult {
    /// Pairs or groups found.
    pub fn len(&self) -> usize {
        match self {
            JobResult::Content { pairs } => pairs.len(),
            JobResult::Images(r) => r.pairs.len(),
            JobResult::Metadata { groups } => groups.len(),
            JobResult::Records { rows } => rows.len(),
            JobResult::AiSuggestion(_) => 1,
            JobResult::Relocation(r) => r.total,
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

/// Two archives sharing STL files (name and size).
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct ContentRow {
    pub id_a: i64,
    pub title_a: Option<String>,
    pub path_a: Option<String>,
    pub total_a: i64,
    pub id_b: i64,
    pub title_b: Option<String>,
    pub path_b: Option<String>,
    pub total_b: i64,
    /// Files in both.
    pub overlap: i64,
    /// `overlap` over the larger list.
    pub similarity: f64,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct ImageResult {
    /// `dhash` or `clip`.
    pub method: String,
    /// Images compared.
    pub images: usize,
    /// Distinct images (identical ones collapsed).
    pub items: usize,
    pub near_pairs: usize,
    /// Distinct images left out as shared by too many models.
    pub shared_skipped: usize,
    /// More pairs matched than are kept.
    pub truncated: bool,
    pub pairs: Vec<ImagePair>,
}

/// Two archives with matching images.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct ImagePair {
    pub id_a: i64,
    pub title_a: String,
    pub path_a: String,
    pub size_a: Option<i64>,
    /// Images of the model compared.
    pub imgs_a: i64,
    pub id_b: i64,
    pub title_b: String,
    pub path_b: String,
    pub size_b: Option<i64>,
    pub imgs_b: i64,
    /// Matching image pairs.
    pub pairs: i64,
    /// The best match: differing bits (`dhash`) or cosine similarity (`clip`).
    pub best: f64,
}

/// Models with equal or similar values in one column.
#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct MetadataGroup {
    /// The column's label.
    pub column: String,
    /// `Exact`, `Levenshtein: n` or `Soundex: code`.
    pub similarity: String,
    pub models: Vec<MetadataModel>,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct MetadataModel {
    pub id: i64,
    pub title: String,
    pub value: String,
}

fn db(e: rusqlite::Error) -> String {
    format!("database: {e}")
}

fn parse_dhash(s: &str) -> Option<u128> {
    s.strip_prefix("2:").filter(|h| h.len() == 32).and_then(|h| u128::from_str_radix(h, 16).ok())
}

fn stem(path: &str) -> String {
    Path::new(path).file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()
}

/// `(model id, hash)` per thumbnailed image of a present model: stored current
/// hashes, else computed from the thumbnail (not saved), as `load_dhash_items`.
fn dhash_items(lib: &Library, log: &dyn Fn(String)) -> Result<Vec<(i64, u128)>, String> {
    let rows = lib.db.with(duplicates::present_images).map_err(db)?;
    let mut good = Vec::new();
    let mut todo = Vec::new();
    for (image, model, hash) in rows {
        match hash.as_deref().and_then(parse_dhash) {
            Some(h) => good.push((model, h)),
            None => todo.push((image, model)),
        }
    }
    if !todo.is_empty() {
        log(format!("Computing {} missing perceptual hash(es)…", todo.len()));
        for chunk in todo.chunks(DHASH_BATCH) {
            let ids: Vec<u64> = chunk.iter().map(|&(i, _)| i as u64).collect();
            let thumbs: HashMap<u64, Vec<u8>> = lib.thumbs.get_many(&ids).map_err(|e| e.to_string())?.into_iter().collect();
            let have: Vec<(i64, &[u8])> = chunk.iter().filter_map(|&(i, m)| thumbs.get(&(i as u64)).map(|t| (m, t.as_slice()))).collect();
            let hashes = stl_core::dhash::dhash_many(&have.iter().map(|&(_, t)| t).collect::<Vec<_>>());
            good.extend(have.iter().zip(hashes).filter_map(|(&(m, _), h)| h.map(|h| (m, h))));
        }
    }
    Ok(good)
}

/// Model pairs as result rows, best first (the desktop's `_results`).
fn rows(lib: &Library, pairs: Vec<stl_core::image_pairs::ModelPair>, img_model: &[i64], higher: bool) -> Result<(Vec<ImagePair>, bool), String> {
    let mut images: HashMap<i64, i64> = HashMap::new();
    for &m in img_model {
        *images.entry(m).or_default() += 1;
    }
    let info: HashMap<i64, (String, String, Option<i64>)> = lib
        .db
        .with(duplicates::model_info)
        .map_err(db)?
        .into_iter()
        .map(|(id, title, path, size)| {
            let path = path.unwrap_or_default();
            let title = title.filter(|t| !t.is_empty()).unwrap_or_else(|| stem(&path));
            let size = match size {
                rusqlite::types::Value::Integer(i) => Some(i),
                _ => None,
            };
            (id, (title, path, size))
        })
        .collect();
    let mut pairs = pairs;
    pairs.sort_by(|x, y| {
        let by_best = if higher { y.3.total_cmp(&x.3) } else { x.3.total_cmp(&y.3) };
        by_best.then(y.2.cmp(&x.2))
    });
    let truncated = pairs.len() > MAX_RESULTS;
    pairs.truncate(MAX_RESULTS);
    let out = pairs
        .into_iter()
        .filter_map(|(a, b, n, best)| {
            let (ia, ib) = (info.get(&a)?, info.get(&b)?);
            Some(ImagePair {
                id_a: a,
                title_a: ia.0.clone(),
                path_a: ia.1.clone(),
                size_a: ia.2,
                imgs_a: images.get(&a).copied().unwrap_or(0),
                id_b: b,
                title_b: ib.0.clone(),
                path_b: ib.1.clone(),
                size_b: ib.2,
                imgs_b: images.get(&b).copied().unwrap_or(0),
                pairs: n,
                best: if higher { best.min(1.0) } else { best },
            })
        })
        .collect();
    Ok((out, truncated))
}

/// Find Duplicate Archives → Images by perceptual hash.
pub fn image_dhash(lib: &Library, ctl: &Control, log: &dyn Fn(String), max_dist: u32, max_models: usize) -> Result<JobResult, String> {
    log("Loading image hashes…".into());
    let items = dhash_items(lib, log)?;
    let mut index: HashMap<u128, usize> = HashMap::new();
    let mut hashes = Vec::new();
    let img_item: Vec<usize> = items
        .iter()
        .map(|&(_, h)| {
            *index.entry(h).or_insert_with(|| {
                hashes.push(h);
                hashes.len() - 1
            })
        })
        .collect();
    let img_model: Vec<i64> = items.iter().map(|&(m, _)| m).collect();
    log(format!("Comparing {} distinct image(s) ({} images)…", hashes.len(), items.len()));
    let hi: Vec<u64> = hashes.iter().map(|h| (h >> 64) as u64).collect();
    let lo: Vec<u64> = hashes.iter().map(|&h| h as u64).collect();
    let near = stl_core::hamming::near_pairs_128(&hi, &lo, max_dist, ctl).map_err(|_| "stopped".to_string())?;
    let edges: Vec<(usize, usize, f64)> =
        (0..near.len()).map(|k| (near.i[k] as usize, near.j[k] as usize, f64::from(near.score[k]))).collect();
    log("Grouping matches by archive…".into());
    let (pairs, skipped) = stl_core::image_pairs::model_pairs(hashes.len(), &img_item, &img_model, &edges, 0.0, false, max_models);
    let (out, truncated) = rows(lib, pairs, &img_model, false)?;
    Ok(JobResult::Images(ImageResult {
        method: "dhash".into(),
        images: items.len(),
        items: hashes.len(),
        near_pairs: edges.len(),
        shared_skipped: skipped,
        truncated,
        pairs: out,
    }))
}

/// Find Duplicate Archives → Images by AI embedding.
pub fn image_clip(lib: &Library, ctl: &Control, log: &dyn Fn(String), threshold: f32, max_models: usize) -> Result<JobResult, String> {
    log("Loading image embeddings…".into());
    let dim = schema_sql::EMBED_DIM as usize;
    let (img_model, mat) = lib.db.with(|c| vectors::load_model_vectors(c, dim)).map_err(db)?;
    let mut index: HashMap<Vec<u32>, usize> = HashMap::new();
    let mut vecs: Vec<f32> = Vec::new();
    let img_item: Vec<usize> = mat
        .chunks(dim)
        .map(|v| {
            let key: Vec<u32> = v.iter().map(|x| x.to_bits()).collect();
            *index.entry(key).or_insert_with(|| {
                let mut v = v.to_vec();
                stl_core::clip::normalize(&mut v);
                vecs.extend(v);
                vecs.len() / dim - 1
            })
        })
        .collect();
    let n_items = vecs.len() / dim;
    log(format!("Comparing {n_items} distinct image(s) ({} images)…", img_model.len()));
    let near = stl_core::embedding::near_pairs_cosine(&vecs, dim, threshold, ctl).map_err(|_| "stopped".to_string())?;
    let edges: Vec<(usize, usize, f64)> =
        (0..near.len()).map(|k| (near.i[k] as usize, near.j[k] as usize, f64::from(near.score[k]))).collect();
    log("Grouping matches by archive…".into());
    let (pairs, skipped) = stl_core::image_pairs::model_pairs(n_items, &img_item, &img_model, &edges, 1.0, true, max_models);
    let (out, truncated) = rows(lib, pairs, &img_model, true)?;
    Ok(JobResult::Images(ImageResult {
        method: "clip".into(),
        images: img_model.len(),
        items: n_items,
        near_pairs: edges.len(),
        shared_skipped: skipped,
        truncated,
        pairs: out,
    }))
}

/// Find Duplicate Archives → STL files: archive pairs sharing a fraction of files.
pub fn content(lib: &Library, ctl: &Control, threshold: f64) -> Result<JobResult, String> {
    let found = lib.db.with(|c| duplicates::stl_content_pairs(c, threshold, ctl)).map_err(db)?;
    let Some(pairs) = found else { return Err("stopped".into()) };
    let pairs = pairs
        .into_iter()
        .map(|p| ContentRow {
            id_a: p.id_a,
            title_a: p.title_a,
            path_a: p.path_a,
            total_a: p.total_a,
            id_b: p.id_b,
            title_b: p.title_b,
            path_b: p.path_b,
            total_b: p.total_b,
            overlap: p.overlap,
            similarity: p.sim,
        })
        .collect();
    Ok(JobResult::Content { pairs })
}

/// Columns of Find Duplicates (metadata): key → (label, index in `DuplicateRow::texts`, or scales).
pub const COLUMNS: &[(&str, &str, Option<usize>)] = &[
    ("title", "Title", Some(0)),
    ("creator", "Creator", Some(1)),
    ("source", "Source", Some(2)),
    ("sculptor", "Sculptor", Some(3)),
    ("collection", "Collection", Some(4)),
    ("subcollection", "Subcollection", Some(5)),
    ("category", "Category", Some(6)),
    ("release", "Release", Some(7)),
    ("scale", "Scale", None),
];

/// Find Duplicates (metadata): models with identical, near (Levenshtein) or
/// phonetically similar (Soundex) values, as the desktop's search worker.
pub fn metadata(lib: &Library, ctl: &Control, columns: &[String], method: &str, max_distance: u32) -> Result<JobResult, String> {
    let rows = lib.db.with(views::duplicate_rows).map_err(db)?;
    ctl.set_total(columns.len() as u64);
    let mut groups = Vec::new();
    for key in columns {
        if ctl.is_stopped() {
            break;
        }
        let &(_, label, index) = COLUMNS.iter().find(|(k, ..)| k == key).ok_or(format!("unknown column {key:?}"))?;
        let value = |r: &views::DuplicateRow| -> String {
            match index {
                Some(i) => r.texts[i].clone().unwrap_or_default(),
                None => {
                    let mut s = r.scales.clone();
                    s.sort();
                    s.join(", ")
                }
            }
        };
        let model = |r: &views::DuplicateRow, v: &str| MetadataModel { id: r.id, title: r.texts[0].clone().unwrap_or_default(), value: v.to_string() };
        let group = |similarity: String, models: Vec<MetadataModel>| MetadataGroup { column: label.to_string(), similarity, models };
        let vals: Vec<(&views::DuplicateRow, String)> =
            rows.iter().map(|r| (r, value(r).trim().to_string())).filter(|(_, v)| !v.is_empty()).collect();
        match method {
            "exact" => {
                let mut buckets: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
                for (k, (_, v)) in vals.iter().enumerate() {
                    buckets.entry(v.as_str()).or_default().push(k);
                }
                let mut found: Vec<&Vec<usize>> = buckets.values().filter(|b| b.len() > 1).collect();
                found.sort_by_key(|b| b[0]);
                for b in found {
                    groups.push(group("Exact".into(), b.iter().map(|&k| model(vals[k].0, &vals[k].1)).collect()));
                }
            }
            "levenshtein" => {
                let mut rows_of: HashMap<String, Vec<usize>> = HashMap::new();
                let mut keys: Vec<String> = Vec::new();
                for (k, (_, v)) in vals.iter().enumerate() {
                    let lower = v.to_lowercase();
                    rows_of.entry(lower.clone()).or_insert_with(|| {
                        keys.push(lower.clone());
                        Vec::new()
                    });
                    rows_of.get_mut(&lower).expect("inserted").push(k);
                }
                let mut hits = Vec::new();
                for (a, b, d) in stl_core::strings::levenshtein_pairs(&keys, max_distance) {
                    if d == 0 {
                        continue;
                    }
                    for &x in &rows_of[&keys[a as usize]] {
                        for &y in &rows_of[&keys[b as usize]] {
                            hits.push((x.min(y), x.max(y), d));
                        }
                    }
                }
                hits.sort_unstable();
                for (x, y, d) in hits {
                    groups.push(group(format!("Levenshtein: {d}"), vec![model(vals[x].0, &vals[x].1), model(vals[y].0, &vals[y].1)]));
                }
            }
            "soundex" => {
                let mut buckets: Vec<(String, Vec<usize>)> = Vec::new();
                let mut index: HashMap<String, usize> = HashMap::new();
                for (k, (_, v)) in vals.iter().enumerate() {
                    let code = stl_core::strings::soundex(v);
                    if code == "0000" {
                        continue;
                    }
                    let i = *index.entry(code.clone()).or_insert_with(|| {
                        buckets.push((code, Vec::new()));
                        buckets.len() - 1
                    });
                    buckets[i].1.push(k);
                }
                for (code, b) in buckets {
                    let distinct: std::collections::HashSet<&str> = b.iter().map(|&k| vals[k].1.as_str()).collect();
                    if b.len() < 2 || distinct.len() == 1 {
                        continue; // all identical: exact matching shows those
                    }
                    groups.push(group(format!("Soundex: {code}"), b.iter().map(|&k| model(vals[k].0, &vals[k].1)).collect()));
                }
            }
            other => return Err(format!("unknown method {other:?} (exact, levenshtein or soundex)")),
        }
        ctl.advance(1);
    }
    Ok(JobResult::Metadata { groups })
}
