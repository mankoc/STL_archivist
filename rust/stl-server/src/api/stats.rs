//! Statistics and Column Distribution (the desktop's two statistics dialogs).

use std::collections::BTreeMap;

use axum::Json;
use axum::extract::{Query, State};
use rusqlite::types::Value;
use serde::{Deserialize, Serialize};
use stl_core::storage::stats;
use utoipa::ToSchema;

use crate::auth::CurrentUser;
use crate::{ApiError, AppState};

#[derive(Serialize, ToSchema)]
pub struct RankEntry {
    /// The value (a name, a year, …); null for "none".
    #[schema(value_type = Object)]
    pub value: serde_json::Value,
    pub count: i64,
}

#[derive(Serialize, ToSchema)]
pub struct LibraryStats {
    /// `total_models`, `total_images`, `total_archive_size`, `presupported`, …
    pub scalars: BTreeMap<String, f64>,
    /// `by_source`, `by_year`, `top_sculptors`, … each most frequent first.
    pub rankings: BTreeMap<String, Vec<RankEntry>>,
}

fn json(v: Value) -> serde_json::Value {
    match v {
        Value::Null | Value::Blob(_) => serde_json::Value::Null,
        Value::Integer(i) => i.into(),
        Value::Real(f) => serde_json::Number::from_f64(f).map_or(serde_json::Value::Null, Into::into),
        Value::Text(s) => s.into(),
    }
}

fn number(v: &Value) -> f64 {
    match v {
        Value::Integer(i) => *i as f64,
        Value::Real(f) => *f,
        _ => 0.0,
    }
}

/// Counts, sums and rankings for the Statistics view.
#[utoipa::path(get, path = "/api/stats/library", tag = "statistics", responses((status = 200, body = LibraryStats)))]
pub async fn library(State(lib): State<AppState>, _me: CurrentUser) -> Result<Json<LibraryStats>, ApiError> {
    let s = lib.run(|lib| Ok(lib.db.with(stats::library)?)).await?;
    Ok(Json(LibraryStats {
        scalars: s.scalars.iter().map(|(k, v)| (k.to_string(), number(v))).collect(),
        rankings: s
            .rankings
            .into_iter()
            .map(|(k, rows)| (k.to_string(), rows.into_iter().map(|(v, count)| RankEntry { value: json(v), count }).collect()))
            .collect(),
    }))
}

pub const COLUMN_KEYS: &[&str] = &[
    "category", "collection", "creator", "presupports", "release", "release_month", "release_year", "scale", "sculptor",
    "source", "subcollection", "tag",
];

const MONTHS: [&str; 12] = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
];

#[derive(Deserialize, utoipa::IntoParams)]
pub struct ColumnQuery {
    /// One of `category`, `collection`, `creator`, `presupports`, `release`,
    /// `release_month`, `release_year`, `scale`, `sculptor`, `source`,
    /// `subcollection`, `tag`.
    pub key: String,
    /// Most frequent values to return (all when absent).
    pub top: Option<i64>,
}

#[derive(Serialize, ToSchema)]
pub struct ColumnRow {
    pub label: String,
    pub count: i64,
}

#[derive(Serialize, ToSchema)]
pub struct ColumnStats {
    pub rows: Vec<ColumnRow>,
    /// Models in the library.
    pub total: i64,
}

/// The label the desktop shows for a value of a direct column.
fn label(key: &str, v: Value) -> String {
    let null = match key {
        "source" | "creator" => "(unknown)",
        "presupports" => "(not set)",
        _ => "(none)",
    };
    let text = match v {
        Value::Null => return null.to_string(),
        Value::Integer(i) => i.to_string(),
        Value::Real(f) => f.to_string(),
        Value::Text(s) => s,
        Value::Blob(_) => return null.to_string(),
    };
    if key == "release_month"
        && let Ok(m) = text.parse::<usize>()
        && (1..=12).contains(&m)
    {
        return MONTHS[m - 1].to_string();
    }
    text
}

/// How the models spread over one column's values, most frequent first.
#[utoipa::path(get, path = "/api/stats/column", tag = "statistics", params(ColumnQuery),
    responses((status = 200, body = ColumnStats), (status = 400, body = crate::error::ErrorBody)))]
pub async fn column(State(lib): State<AppState>, _me: CurrentUser, Query(q): Query<ColumnQuery>) -> Result<Json<ColumnStats>, ApiError> {
    if !COLUMN_KEYS.contains(&q.key.as_str()) {
        return Err(ApiError::bad_request(format!("unknown column {:?}", q.key)));
    }
    let key = q.key.clone();
    let (raw, total) = lib.run(move |lib| Ok(lib.db.with(|c| stats::column(c, &key, q.top))?)).await?;
    let rows = raw.into_iter().map(|(v, count)| ColumnRow { label: label(&q.key, v), count }).collect();
    Ok(Json(ColumnStats { rows, total }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_as_the_desktop() {
        assert_eq!(label("source", Value::Null), "(unknown)");
        assert_eq!(label("presupports", Value::Null), "(not set)");
        assert_eq!(label("release_year", Value::Integer(2024)), "2024");
        assert_eq!(label("release_month", Value::Integer(3)), "March");
        assert_eq!(label("sculptor", Value::Text("Alice".into())), "Alice");
    }
}
