//! The browser filter (`FilterSpec`) as JSON, and the filter pane's data.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;

use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};
use stl_core::storage::library::{self, FilterValue, Key};
use utoipa::ToSchema;

use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

/// A selected filter value: text, a lookup id, or a release date.
#[derive(Debug, Clone, Serialize, Deserialize, ToSchema)]
#[serde(untagged)]
pub enum FilterValueDto {
    Id(i64),
    Text(String),
    Date(DateDto),
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, ToSchema, PartialEq, Eq)]
pub struct DateDto {
    pub year: i64,
    /// Absent (or null) for the whole year.
    #[serde(default)]
    pub month: Option<i64>,
}

/// Include/exclude values per facet (`creator`, `tag`, `path`, `date`,
/// `sculptor`, …), has-value / no-value groups and an optional id list.
/// Lookup facets take ids, `date` takes `{year, month}`, the others text.
#[derive(Debug, Clone, Default, Serialize, Deserialize, ToSchema)]
#[serde(default, deny_unknown_fields)]
pub struct FilterSpecDto {
    pub include: BTreeMap<String, Vec<FilterValueDto>>,
    pub exclude: BTreeMap<String, Vec<FilterValueDto>>,
    pub has_value: Vec<String>,
    pub no_value: Vec<String>,
    pub ids: Option<Vec<i64>>,
}

impl FilterSpecDto {
    pub fn to_spec(&self) -> Result<library::FilterSpec, ApiError> {
        let mut spec = library::FilterSpec::default();
        for (src, dst) in [(&self.include, &mut spec.include), (&self.exclude, &mut spec.exclude)] {
            for (facet, values) in src {
                if !library::FACETS.contains(&facet.as_str()) {
                    return Err(ApiError::bad_request(format!("unknown facet {facet:?}")));
                }
                if values.is_empty() {
                    continue;
                }
                let vals = values
                    .iter()
                    .map(|v| match v {
                        FilterValueDto::Id(i) => FilterValue::Int(*i),
                        FilterValueDto::Text(s) => FilterValue::Text(s.clone()),
                        FilterValueDto::Date(d) => FilterValue::Date(d.year, d.month),
                    })
                    .collect();
                dst.insert(facet.clone(), vals);
            }
        }
        for (src, dst) in [(&self.has_value, &mut spec.has_value), (&self.no_value, &mut spec.no_value)] {
            for g in src {
                if !library::GROUPS.contains(&g.as_str()) {
                    return Err(ApiError::bad_request(format!("unknown group {g:?}")));
                }
                dst.insert(g.clone());
            }
        }
        spec.ids = self.ids.clone();
        Ok(spec)
    }
}

#[derive(Debug, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct FilterTreeRequest {
    /// The current filter; the counts of each section are scoped by the others.
    #[serde(default)]
    pub filter: Option<FilterSpecDto>,
    /// Sections to fill: `author`, `tags`, `source`, `date`, `sculptor`,
    /// `collection`, `subcollection`, `category`, `release`, `scale`,
    /// `presupports`, `path`.
    #[serde(default)]
    pub show: Vec<String>,
    /// Compute per-value counts.
    #[serde(default = "yes")]
    pub counts: bool,
}

fn yes() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct NamedId {
    pub id: i64,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct CollectionNode {
    pub id: i64,
    pub name: String,
    pub subcollections: Vec<NamedId>,
}

/// Counts per kind, then per value. Kinds: the facet names (`creator`, `tag`,
/// `sculptor`, …; lookup ids as decimal strings), `date_month` (`"2024-03"`,
/// or `"2024"` for a year without months), `date_year` (`"2024"`) and `group`
/// (models with a value, per group name).
pub type Counts = BTreeMap<String, BTreeMap<String, i64>>;

#[derive(Debug, Clone, Serialize, ToSchema)]
pub struct FilterTree {
    pub counts: Counts,
    pub scoped_counts: Counts,
    /// Models per folder, ancestors included.
    pub path_counts: Option<BTreeMap<String, i64>>,
    pub scoped_path_counts: Option<BTreeMap<String, i64>>,
    pub creators: Vec<String>,
    pub tags: Vec<String>,
    pub sources: Vec<String>,
    pub dates: Vec<DateDto>,
    pub libraries: Vec<NamedId>,
    pub sculptors: Vec<NamedId>,
    pub collections: Vec<NamedId>,
    pub subcollections: Vec<CollectionNode>,
    pub categories: Vec<NamedId>,
    pub releases: Vec<NamedId>,
    pub scales: Vec<NamedId>,
    pub presupports: Vec<String>,
    pub filetypes: Vec<String>,
    pub folders: Vec<String>,
    pub scoped_folders: Option<Vec<String>>,
    pub new_folders: Option<Vec<String>>,
    /// Whether the counts are scoped by a filter.
    pub scoped: bool,
}

/// The facet name for a core count kind (`sculptor_id` → `sculptor`).
fn facet(kind: &str) -> String {
    kind.strip_suffix("_id").unwrap_or(kind).to_string()
}

fn counts(src: &HashMap<Key, i64>) -> Counts {
    let mut out = Counts::new();
    for (k, &n) in src {
        let (kind, value) = match k {
            Key::Text(kind, s) => (facet(kind), s.clone()),
            Key::Int(kind, i) => (facet(kind), i.to_string()),
            Key::Month(y, Some(m)) => ("date_month".into(), format!("{y:04}-{m:02}")),
            Key::Month(y, None) => ("date_month".into(), format!("{y:04}")),
            Key::Year(y) => ("date_year".into(), format!("{y:04}")),
            Key::Group(g) => ("group".into(), facet(g)),
        };
        out.entry(kind).or_default().insert(value, n);
    }
    out
}

fn named(v: Vec<(String, i64)>) -> Vec<NamedId> {
    v.into_iter().map(|(name, id)| NamedId { id, name }).collect()
}

fn sorted(m: Option<HashMap<String, i64>>) -> Option<BTreeMap<String, i64>> {
    m.map(|m| m.into_iter().collect())
}

impl From<library::FilterTree> for FilterTree {
    fn from(t: library::FilterTree) -> Self {
        FilterTree {
            counts: counts(&t.counts),
            scoped_counts: counts(&t.scoped_counts),
            path_counts: sorted(t.path_counts),
            scoped_path_counts: sorted(t.sc_path_counts),
            creators: t.creators,
            tags: t.tags,
            sources: t.sources,
            dates: t.dates.into_iter().map(|(year, month)| DateDto { year, month }).collect(),
            libraries: named(t.libraries),
            sculptors: named(t.sculptors),
            collections: named(t.collections),
            subcollections: t
                .sub_by_col
                .into_iter()
                .map(|(name, id, subs)| CollectionNode { id, name, subcollections: named(subs) })
                .collect(),
            categories: named(t.categories),
            releases: named(t.releases),
            scales: named(t.scales),
            presupports: t.presupports,
            filetypes: t.filetypes,
            folders: t.sorted_dirs,
            scoped_folders: t.scoped_dirs,
            new_folders: t.new_dirs,
            scoped: t.scoped,
        }
    }
}

/// Every value list and count the filter pane shows.
#[utoipa::path(post, path = "/api/filter-tree", tag = "library", request_body = FilterTreeRequest,
    responses((status = 200, body = FilterTree), (status = 400, body = crate::error::ErrorBody)))]
pub async fn tree(State(lib): State<AppState>, _me: CurrentUser, Json(req): Json<FilterTreeRequest>) -> Result<Json<FilterTree>, ApiError> {
    let spec = req.filter.as_ref().map(FilterSpecDto::to_spec).transpose()?;
    for s in &req.show {
        if !library::SHOW.contains(&s.as_str()) {
            return Err(ApiError::bad_request(format!("unknown section {s:?}")));
        }
    }
    let show = library::Show(req.show.iter().cloned().collect());
    if spec.as_ref().is_none_or(|s| s.is_empty()) {
        let tree = lib.run(move |lib| Ok(unfiltered_tree(lib, show, req.counts)?)).await?;
        return Ok(Json((*tree).clone()));
    }
    let tree = lib.run(move |lib| Ok(lib.db.with(|c| library::filter_tree(c, spec.as_ref(), &show, req.counts))?)).await?;
    Ok(Json(tree.into()))
}

/// The unfiltered tree, kept until the library changes: it reads most of the
/// database, which on a hard disk takes seconds after the server starts.
#[derive(Default)]
pub struct TreeCache(std::sync::Mutex<Option<CachedTree>>);

struct CachedTree {
    generation: u64,
    show: BTreeSet<String>,
    counts: bool,
    tree: Arc<FilterTree>,
}

/// [`TreeCache`]'s answer, computed when missing or stale. Held while computing,
/// so a request arriving meanwhile (the start-up warm-up and the first page) waits
/// for it instead of reading the disk a second time.
pub fn unfiltered_tree(lib: &crate::Library, show: library::Show, counts: bool) -> rusqlite::Result<Arc<FilterTree>> {
    let mut slot = lib.tree_cache.0.lock().unwrap_or_else(|e| e.into_inner());
    let generation = lib.generation.load(std::sync::atomic::Ordering::SeqCst);
    let key: BTreeSet<String> = show.0.iter().cloned().collect();
    if let Some(c) = slot.as_ref().filter(|c| c.generation == generation && c.show == key && c.counts == counts) {
        return Ok(Arc::clone(&c.tree));
    }
    let tree: Arc<FilterTree> = Arc::new(library::filter_tree_parallel(&lib.db, &show, counts)?.into());
    // Computed from what the database held at `generation` (a change since makes it stale).
    *slot = Some(CachedTree { generation, show: key, counts, tree: Arc::clone(&tree) });
    Ok(tree)
}

/// Fill [`TreeCache`] with what the web client's filter pane asks for first.
pub fn warm_tree(lib: &crate::Library) {
    let show = library::Show(library::SHOW.iter().map(|s| s.to_string()).collect());
    if let Err(e) = unfiltered_tree(lib, show, true) {
        tracing::warn!("filter pane warm-up: {e}");
    }
}
