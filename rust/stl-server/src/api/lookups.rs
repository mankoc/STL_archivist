//! The lookup editors: tags, categories, collections, subcollections and
//! scales; creator (author) names and creator profiles. Reading needs a
//! session, changes need the editor role.

use std::collections::BTreeMap;

use axum::Json;
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use serde::{Deserialize, Serialize};
use stl_core::storage::admin;
use stl_core::strings::Likeness;
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

pub const KINDS: &[&str] = &["tag", "category", "collection", "subcollection", "scale"];

fn kind_ok(kind: &str) -> Result<(), ApiError> {
    if KINDS.contains(&kind) { Ok(()) } else { Err(ApiError::not_found(format!("no lookup kind {kind:?}"))) }
}

#[derive(Serialize, ToSchema)]
pub struct LookupRow {
    pub id: i64,
    pub name: String,
    /// Models using it.
    pub models: i64,
    /// Subcollections only.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub collection_id: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub collection: Option<String>,
}

/// Every row of a lookup table by name (subcollections by collection, then name).
#[utoipa::path(get, path = "/api/lookups/{kind}", tag = "lookups",
    params(("kind" = String, Path, description = "tag, category, collection, subcollection or scale")),
    responses((status = 200, body = Vec<LookupRow>), (status = 404, body = crate::error::ErrorBody)))]
pub async fn list(State(lib): State<AppState>, _me: CurrentUser, Path(kind): Path<String>) -> Result<Json<Vec<LookupRow>>, ApiError> {
    kind_ok(&kind)?;
    let rows = lib.run(move |lib| Ok(lib.db.with(|c| admin::list(c, &kind))?)).await?;
    Ok(Json(
        rows.into_iter()
            .map(|(id, name, models, collection_id, collection)| LookupRow { id, name, models, collection_id, collection })
            .collect(),
    ))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct NewLookup {
    pub name: String,
    /// The parent collection's name (subcollections only).
    #[serde(default)]
    pub collection: Option<String>,
}

/// Add a row. 409 when the name exists (within the collection, for a
/// subcollection) or the parent collection does not.
#[utoipa::path(post, path = "/api/lookups/{kind}", tag = "lookups", request_body = NewLookup,
    params(("kind" = String, Path, description = "Lookup kind")),
    responses((status = 201), (status = 409, body = crate::error::ErrorBody)))]
pub async fn add(
    State(lib): State<AppState>,
    me: CurrentUser,
    Path(kind): Path<String>,
    Json(n): Json<NewLookup>,
) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    kind_ok(&kind)?;
    let name = n.name.trim().to_string();
    if name.is_empty() {
        return Err(ApiError::bad_request("a name is needed"));
    }
    if kind == "subcollection" && n.collection.is_none() {
        return Err(ApiError::bad_request("a subcollection needs its collection"));
    }
    let created = lib.run(move |lib| Ok(lib.db.with_tx(|tx| admin::add(tx, &kind, &name, n.collection.as_deref()))?)).await?;
    if created { Ok(StatusCode::CREATED) } else { Err(ApiError::new(StatusCode::CONFLICT, "the name exists, or the collection does not")) }
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct Rename {
    pub name: String,
}

/// Rename a row; 409 when the new name is taken.
#[utoipa::path(patch, path = "/api/lookups/{kind}/{id}", tag = "lookups", request_body = Rename,
    params(("kind" = String, Path, description = "Lookup kind"), ("id" = i64, Path, description = "Row id")),
    responses((status = 204), (status = 404, body = crate::error::ErrorBody), (status = 409, body = crate::error::ErrorBody)))]
pub async fn rename(
    State(lib): State<AppState>,
    me: CurrentUser,
    Path((kind, id)): Path<(String, i64)>,
    Json(r): Json<Rename>,
) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    kind_ok(&kind)?;
    let name = r.name.trim().to_string();
    if name.is_empty() {
        return Err(ApiError::bad_request("a name is needed"));
    }
    let outcome = lib
        .run(move |lib| {
            Ok(lib.db.with_tx(|tx| -> rusqlite::Result<_> {
                if admin::name_of(tx, &kind, id)?.is_none() {
                    return Ok(None);
                }
                let paths = admin::archive_paths(tx, &kind, id)?;
                Ok(Some(admin::rename(tx, &kind, id, &name)?.then_some(paths)))
            })?)
        })
        .await?;
    match outcome {
        None => Err(ApiError::not_found(format!("no {id}"))),
        Some(Some(paths)) => {
            lib.schedule_sidecars(paths);
            Ok(StatusCode::NO_CONTENT)
        }
        Some(None) => Err(ApiError::new(StatusCode::CONFLICT, "the name is taken")),
    }
}

#[derive(Serialize, ToSchema)]
pub struct DeleteInfo {
    pub name: String,
    /// Models that lose the value.
    pub models: i64,
    /// Subcollections deleted with a collection.
    pub subcollections: i64,
}

/// What deleting a row would affect.
#[utoipa::path(get, path = "/api/lookups/{kind}/{id}", tag = "lookups",
    params(("kind" = String, Path, description = "Lookup kind"), ("id" = i64, Path, description = "Row id")),
    responses((status = 200, body = DeleteInfo), (status = 404, body = crate::error::ErrorBody)))]
pub async fn info(State(lib): State<AppState>, _me: CurrentUser, Path((kind, id)): Path<(String, i64)>) -> Result<Json<DeleteInfo>, ApiError> {
    kind_ok(&kind)?;
    let i = lib.run(move |lib| Ok(lib.db.with(|c| admin::delete_info(c, &kind, id))?)).await?;
    let (name, models, subcollections) = i.ok_or_else(|| ApiError::not_found(format!("no {id}")))?;
    Ok(Json(DeleteInfo { name, models, subcollections }))
}

/// Delete a row. Models lose the value; a collection takes its subcollections.
#[utoipa::path(delete, path = "/api/lookups/{kind}/{id}", tag = "lookups",
    params(("kind" = String, Path, description = "Lookup kind"), ("id" = i64, Path, description = "Row id")),
    responses((status = 204), (status = 404, body = crate::error::ErrorBody)))]
pub async fn delete(State(lib): State<AppState>, me: CurrentUser, Path((kind, id)): Path<(String, i64)>) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    kind_ok(&kind)?;
    let paths = lib
        .run(move |lib| {
            Ok(lib.db.with_tx(|tx| -> rusqlite::Result<_> {
                if admin::name_of(tx, &kind, id)?.is_none() {
                    return Ok(None);
                }
                let paths = admin::archive_paths(tx, &kind, id)?;
                admin::delete(tx, &kind, id)?;
                Ok(Some(paths))
            })?)
        })
        .await?;
    let paths = paths.ok_or_else(|| ApiError::not_found(format!("no {id}")))?;
    lib.schedule_sidecars(paths);
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct Merge {
    /// The row that stays.
    pub keep: i64,
    /// The row merged into it and deleted.
    pub remove: i64,
}

/// Merge two rows: models of `remove` get `keep`, then `remove` is deleted.
#[utoipa::path(post, path = "/api/lookups/{kind}/merge", tag = "lookups", request_body = Merge,
    params(("kind" = String, Path, description = "Lookup kind")),
    responses((status = 204), (status = 404, body = crate::error::ErrorBody)))]
pub async fn merge(
    State(lib): State<AppState>,
    me: CurrentUser,
    Path(kind): Path<String>,
    Json(m): Json<Merge>,
) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    kind_ok(&kind)?;
    if m.keep == m.remove {
        return Err(ApiError::bad_request("cannot merge a row into itself"));
    }
    let paths = lib
        .run(move |lib| {
            Ok(lib.db.with_tx(|tx| -> rusqlite::Result<_> {
                if admin::name_of(tx, &kind, m.keep)?.is_none() || admin::name_of(tx, &kind, m.remove)?.is_none() {
                    return Ok(None);
                }
                let paths = admin::archive_paths(tx, &kind, m.remove)?;
                admin::merge(tx, &kind, m.keep, m.remove)?;
                Ok(Some(paths))
            })?)
        })
        .await?;
    let paths = paths.ok_or_else(|| ApiError::not_found("no such row"))?;
    lib.schedule_sidecars(paths);
    Ok(StatusCode::NO_CONTENT)
}

/// Most pairs one search returns.
const MAX_SIMILAR: usize = 2000;

#[derive(Deserialize, utoipa::IntoParams)]
#[serde(deny_unknown_fields)]
pub struct SimilarQuery {
    /// `spelling` (default), `sound` or `words`.
    pub method: Option<String>,
    /// `spelling`: most letters that differ (0 to 5, default 2; case aside).
    /// `words`: least percentage of the shorter name's words shared (1 to 100, default 80).
    pub threshold: Option<u32>,
}

#[derive(Serialize, ToSchema)]
pub struct SimilarName {
    /// The row's id (0 for a creator, who has a name only).
    pub id: i64,
    pub name: String,
    /// Models using it.
    pub models: i64,
}

#[derive(Serialize, ToSchema)]
pub struct SimilarPair {
    pub a: SimilarName,
    pub b: SimilarName,
    /// The distance, the Soundex code or the percentage of words shared.
    pub score: String,
}

#[derive(Serialize, ToSchema)]
pub struct SimilarNames {
    /// The closest first.
    pub pairs: Vec<SimilarPair>,
    /// More pairs exist than are returned.
    pub truncated: bool,
}

/// `names`' similar pairs, added to `out` (at most [`MAX_SIMILAR`] and one).
fn similar_in(rows: &[(i64, String, i64)], how: Likeness, out: &mut Vec<SimilarPair>) {
    let names: Vec<String> = rows.iter().map(|r| r.1.clone()).collect();
    let name = |k: u32| {
        let (id, name, models) = &rows[k as usize];
        SimilarName { id: *id, name: name.clone(), models: *models }
    };
    out.extend(stl_core::strings::similar_pairs(&names, how, MAX_SIMILAR).into_iter().map(|(i, j, score)| SimilarPair { a: name(i), b: name(j), score }));
}

/// Pairs of near-duplicate names of a lookup, or of the creators (kind
/// `creator`), to merge. Subcollections are compared within their collection.
#[utoipa::path(get, path = "/api/lookups/{kind}/similar", tag = "lookups",
    params(("kind" = String, Path, description = "A lookup kind, or creator"), SimilarQuery),
    responses((status = 200, body = SimilarNames), (status = 400, body = crate::error::ErrorBody), (status = 404, body = crate::error::ErrorBody)))]
pub async fn similar(State(lib): State<AppState>, _me: CurrentUser, Path(kind): Path<String>, Query(q): Query<SimilarQuery>) -> Result<Json<SimilarNames>, ApiError> {
    if kind != "creator" {
        kind_ok(&kind)?;
    }
    let how = match q.method.as_deref().unwrap_or("spelling") {
        "spelling" => Likeness::Spelling(q.threshold.unwrap_or(2).min(5)),
        "sound" => Likeness::Sound,
        "words" => Likeness::Words(q.threshold.unwrap_or(80).clamp(1, 100)),
        other => return Err(ApiError::bad_request(format!("unknown method {other:?} (spelling, sound or words)"))),
    };
    let mut pairs = lib
        .run(move |lib| {
            let mut pairs = Vec::new();
            if kind == "creator" {
                let rows: Vec<(i64, String, i64)> = lib.db.with(admin::author_counts)?.into_iter().map(|(name, models)| (0, name, models)).collect();
                similar_in(&rows, how, &mut pairs);
            } else {
                let mut groups: BTreeMap<Option<i64>, Vec<(i64, String, i64)>> = BTreeMap::new();
                for (id, name, models, collection, _) in lib.db.with(|c| admin::list(c, &kind))? {
                    groups.entry(collection).or_default().push((id, name, models));
                }
                for rows in groups.values() {
                    similar_in(rows, how, &mut pairs);
                }
            }
            Ok::<_, ApiError>(pairs)
        })
        .await?;
    let truncated = pairs.len() > MAX_SIMILAR;
    pairs.truncate(MAX_SIMILAR);
    Ok(Json(SimilarNames { pairs, truncated }))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct TagMerge {
    pub ids: Vec<i64>,
    /// The resulting tag's name (an existing tag, matched case-insensitively, or a new one).
    pub target: String,
}

/// Merge tags into one (Tag editor → Merge).
#[utoipa::path(post, path = "/api/tags/merge", tag = "lookups", request_body = TagMerge, responses((status = 204)))]
pub async fn merge_tags(State(lib): State<AppState>, me: CurrentUser, Json(m): Json<TagMerge>) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    if m.target.trim().is_empty() {
        return Err(ApiError::bad_request("a target name is needed"));
    }
    let paths = lib
        .run(move |lib| {
            Ok(lib.db.with_tx(|tx| -> rusqlite::Result<_> {
                let mut paths = Vec::new();
                for &id in &m.ids {
                    paths.extend(admin::archive_paths(tx, "tag", id)?);
                }
                admin::merge_tags(tx, &m.ids, &m.target)?;
                Ok(paths)
            })?)
        })
        .await?;
    lib.schedule_sidecars(paths);
    Ok(StatusCode::NO_CONTENT)
}

#[derive(Serialize, ToSchema)]
pub struct Count {
    pub count: i64,
}

/// Tags no model uses.
#[utoipa::path(get, path = "/api/tags/unused", tag = "lookups", responses((status = 200, body = Count)))]
pub async fn unused_tags(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<Count>, ApiError> {
    let count = lib.run(|lib| Ok(lib.db.with(admin::unused_tag_count)?)).await?;
    Ok(Json(Count { count }))
}

/// Delete the tags no model uses.
#[utoipa::path(delete, path = "/api/tags/unused", tag = "lookups", responses((status = 200, body = Count)))]
pub async fn delete_unused_tags(State(lib): State<AppState>, me: CurrentUser) -> Result<Json<Count>, ApiError> {
    me.require(Role::Editor)?;
    let n = lib.run(|lib| Ok(lib.db.with_tx(admin::delete_unused_tags)?)).await?;
    Ok(Json(Count { count: n as i64 }))
}

// ── creators ─────────────────────────────────────────────────────────────────

#[derive(Serialize, ToSchema)]
pub struct Author {
    pub name: String,
    pub models: i64,
}

/// Every creator name with its model count.
#[utoipa::path(get, path = "/api/authors", tag = "lookups", responses((status = 200, body = Vec<Author>)))]
pub async fn authors(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<Vec<Author>>, ApiError> {
    let rows = lib.run(|lib| Ok(lib.db.with(admin::author_counts)?)).await?;
    Ok(Json(rows.into_iter().map(|(name, models)| Author { name, models }).collect()))
}

#[derive(Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct AuthorChange {
    /// `rename` (`name` → `to`), `merge` (`name` into `to`, keeping `to`'s
    /// profile) or `delete` (clear `name` from its models).
    pub action: String,
    pub name: String,
    #[serde(default)]
    pub to: Option<String>,
}

/// Rename, merge or delete a creator name across all models.
#[utoipa::path(post, path = "/api/authors", tag = "lookups", request_body = AuthorChange,
    responses((status = 204), (status = 400, body = crate::error::ErrorBody)))]
pub async fn change_author(State(lib): State<AppState>, me: CurrentUser, Json(c): Json<AuthorChange>) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    let to = c.to.as_deref().map(str::trim).filter(|t| !t.is_empty()).map(str::to_string);
    let (action, name) = (c.action, c.name);
    match (action.as_str(), &to) {
        ("rename" | "merge", None) => return Err(ApiError::bad_request(format!("{action} needs `to`"))),
        ("rename" | "merge" | "delete", _) => {}
        _ => return Err(ApiError::bad_request(format!("unknown action {action:?}"))),
    }
    let paths = lib
        .run(move |lib| {
            Ok(lib.db.with_tx(|tx| -> rusqlite::Result<_> {
                let paths = admin::author_archive_paths(tx, &name)?;
                match (action.as_str(), to) {
                    ("rename", Some(to)) => admin::rename_author(tx, &name, &to)?,
                    ("merge", Some(to)) => admin::merge_authors(tx, &to, &name)?,
                    _ => admin::delete_author(tx, &name)?,
                }
                Ok(paths)
            })?)
        })
        .await?;
    lib.schedule_sidecars(paths);
    Ok(StatusCode::NO_CONTENT)
}

/// A creator's profile links; each may be null.
#[derive(Deserialize, Serialize, ToSchema, Default)]
#[serde(default, deny_unknown_fields)]
pub struct Profile {
    pub full_name: Option<String>,
    pub patreon_url: Option<String>,
    pub artstation_url: Option<String>,
    pub gumroad_url: Option<String>,
    pub myminifactory_url: Option<String>,
}

#[derive(Deserialize, utoipa::IntoParams)]
pub struct NameQuery {
    pub name: String,
}

/// A creator's profile (404 when there is none).
#[utoipa::path(get, path = "/api/authors/profile", tag = "lookups", params(NameQuery),
    responses((status = 200, body = Profile), (status = 404, body = crate::error::ErrorBody)))]
pub async fn profile(State(lib): State<AppState>, _me: CurrentUser, Query(q): Query<NameQuery>) -> Result<Json<Profile>, ApiError> {
    let p = lib.run(move |lib| Ok(lib.db.with(|c| admin::creator_profile(c, &q.name))?)).await?;
    let [full_name, patreon_url, artstation_url, gumroad_url, myminifactory_url] =
        p.ok_or_else(|| ApiError::not_found("no profile"))?;
    Ok(Json(Profile { full_name, patreon_url, artstation_url, gumroad_url, myminifactory_url }))
}

/// Create or update a creator's profile; blank values are stored as null.
#[utoipa::path(put, path = "/api/authors/profile", tag = "lookups", params(NameQuery), request_body = Profile,
    responses((status = 204)))]
pub async fn save_profile(
    State(lib): State<AppState>,
    me: CurrentUser,
    Query(q): Query<NameQuery>,
    Json(p): Json<Profile>,
) -> Result<StatusCode, ApiError> {
    me.require(Role::Editor)?;
    let clean = |v: Option<String>| v.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    let values = [clean(p.full_name), clean(p.patreon_url), clean(p.artstation_url), clean(p.gumroad_url), clean(p.myminifactory_url)];
    lib.run(move |lib| Ok(lib.db.with_tx(|tx| admin::save_creator_profile(tx, &q.name, &values))?)).await?;
    Ok(StatusCode::NO_CONTENT)
}
