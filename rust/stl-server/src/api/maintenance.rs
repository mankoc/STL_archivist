//! Database Maintenance writes on a list of records: merge moved records and
//! remove records (the lists come from the Verify, Find Moved in DB and Find
//! Duplicate Entries jobs).

use std::collections::BTreeMap;

use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};
use stl_core::storage::{maintenance, model_edit};
use utoipa::ToSchema;

use crate::accounts::Role;
use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct MovedRecord {
    /// The missing record, whose metadata is kept.
    pub id: i64,
    /// The archive path of the record crawled at the new location.
    pub path: String,
}

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct MergeMoved {
    pub items: Vec<MovedRecord>,
}

#[derive(Serialize, ToSchema)]
pub struct Merged {
    pub merged: usize,
    /// Items whose records no longer exist.
    pub errors: usize,
}

/// Merge each missing record into the record found at its new location: the
/// metadata of the missing one moves there (Merge Moved).
#[utoipa::path(post, path = "/api/maintenance/merge-moved", tag = "maintenance", request_body = MergeMoved,
    responses((status = 200, body = Merged), (status = 403, body = crate::error::ErrorBody)))]
pub async fn merge_moved(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<MergeMoved>) -> Result<Json<Merged>, ApiError> {
    me.require(Role::Editor)?;
    let items: Vec<(i64, String)> = req.items.into_iter().map(|m| (m.id, m.path)).collect();
    let paths: Vec<String> = items.iter().map(|(_, p)| p.clone()).collect();
    let (merged, errors) = lib
        .run(move |lib| {
            let (merged, errors, refs) = lib.db.with_tx(|tx| maintenance::merge_moved(tx, &items))?;
            let ids: Vec<u64> = refs.iter().filter_map(|r| r.trim().parse().ok()).collect();
            lib.thumbs.delete_many(&ids)?;
            Ok::<_, ApiError>((merged, errors))
        })
        .await?;
    lib.schedule_sidecars(paths);
    Ok(Json(Merged { merged, errors }))
}

#[derive(Debug, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct RemoveRecords {
    pub ids: Vec<i64>,
}

#[derive(Serialize, ToSchema)]
pub struct Removed {
    pub removed: usize,
    /// Lookup rows no model uses any more, deleted with them: `{table: rows}`.
    pub pruned: BTreeMap<String, usize>,
}

/// Remove records from the library and prune lookups left unused (Remove
/// Selected / All Missing). The archive files are not touched.
#[utoipa::path(post, path = "/api/maintenance/remove", tag = "maintenance", request_body = RemoveRecords,
    responses((status = 200, body = Removed), (status = 403, body = crate::error::ErrorBody)))]
pub async fn remove(State(lib): State<AppState>, me: CurrentUser, Json(req): Json<RemoveRecords>) -> Result<Json<Removed>, ApiError> {
    me.require(Role::Editor)?;
    let (paths, pruned) = lib
        .run(move |lib| {
            let paths = lib.db.with(|c| model_edit::archive_paths(c, &req.ids))?;
            let (refs, pruned) = lib.db.with_tx(|tx| maintenance::remove_records(tx, &req.ids))?;
            let ids: Vec<u64> = refs.iter().filter_map(|r| r.trim().parse().ok()).collect();
            lib.thumbs.delete_many(&ids)?;
            Ok::<_, ApiError>((paths.into_values().collect::<Vec<_>>(), pruned))
        })
        .await?;
    let removed = paths.len();
    lib.schedule_sidecars(paths);
    Ok(Json(Removed { removed, pruned: pruned.into_iter().filter(|(_, n)| *n > 0).map(|(t, n)| (t.to_string(), n)).collect() }))
}
