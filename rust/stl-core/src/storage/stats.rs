//! Library statistics: a port of `app/services/library_stats.py` (the
//! Statistics and Column Distribution dialogs). Values are returned raw; the
//! Python service applies the labels.

use rusqlite::Connection;
use rusqlite::types::Value;

use super::Result;

/// `(value, count)` ranking rows.
pub type Ranking = Vec<(Value, i64)>;

pub struct LibraryStats {
    /// `(key, value)` for the scalar statistics, in a fixed order.
    pub scalars: Vec<(&'static str, Value)>,
    /// `(key, rows)` for the rankings.
    pub rankings: Vec<(&'static str, Ranking)>,
}

fn scalar(conn: &Connection, sql: &str) -> Result<Value> {
    let v: Value = conn.query_row(sql, [], |r| r.get(0))?;
    // `scalar() or 0`: NULL (an empty SUM) and 0 both become 0.
    Ok(match v {
        Value::Null => Value::Integer(0),
        Value::Real(0.0) => Value::Integer(0),
        v => v,
    })
}

fn ranking(conn: &Connection, sql: &str, limit: Option<i64>) -> Result<Ranking> {
    let sql = match limit {
        Some(n) => format!("{sql} LIMIT {n}"),
        None => sql.to_owned(),
    };
    let mut st = conn.prepare(&sql)?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

pub fn library(conn: &Connection) -> Result<LibraryStats> {
    let scalars_sql: [(&str, &str); 14] = [
        ("total_models", "SELECT count(id) FROM models"),
        ("total_images", "SELECT count(id) FROM images"),
        ("total_sculptors", "SELECT count(id) FROM sculptors"),
        ("total_collections", "SELECT count(id) FROM collections"),
        ("total_categories", "SELECT count(id) FROM categories"),
        ("total_releases", "SELECT count(id) FROM releases"),
        ("total_scales", "SELECT count(id) FROM scales"),
        ("total_tags", "SELECT count(id) FROM tags"),
        ("total_archive_size", "SELECT sum(archive_size) FROM models"),
        ("total_file_size", "SELECT sum(file_size) FROM stl_files"),
        (
            "presupported",
            "SELECT count(id) FROM models WHERE presupports IS NOT NULL AND presupports NOT IN ('', 'No')",
        ),
        ("with_warnings", "SELECT count(id) FROM models WHERE warning IS NOT NULL"),
        (
            "no_images",
            "SELECT count(m.id) FROM models m LEFT OUTER JOIN images i ON m.id = i.model_id WHERE i.id IS NULL",
        ),
        ("with_url", "SELECT count(id) FROM models WHERE url IS NOT NULL AND url != ''"),
    ];
    let scalars = scalars_sql.iter().map(|(k, sql)| Ok((*k, scalar(conn, sql)?))).collect::<Result<_>>()?;
    let rankings = vec![
        (
            "by_source",
            ranking(conn, "SELECT source, count(id) AS n FROM models GROUP BY source ORDER BY n DESC, source", None)?,
        ),
        (
            "by_year",
            ranking(
                conn,
                "SELECT release_year, count(id) AS n FROM models WHERE release_year IS NOT NULL \
                 GROUP BY release_year ORDER BY n DESC, release_year",
                Some(10),
            )?,
        ),
        ("top_sculptors", joined(conn, "sculptors", "sculptor_id", Some(15))?),
        ("top_tags", linked(conn, "tags", "model_tags", "tag_id", Some(15))?),
        ("top_collections", joined(conn, "collections", "collection_id", Some(10))?),
    ];
    Ok(LibraryStats { scalars, rankings })
}

fn joined(conn: &Connection, table: &str, fk: &str, limit: Option<i64>) -> Result<Ranking> {
    ranking(
        conn,
        &format!(
            "SELECT l.name, count(m.id) AS n FROM {table} l JOIN models m ON m.{fk} = l.id \
             GROUP BY l.id ORDER BY n DESC, l.id"
        ),
        limit,
    )
}

fn linked(conn: &Connection, table: &str, link: &str, fk: &str, limit: Option<i64>) -> Result<Ranking> {
    ranking(
        conn,
        &format!(
            "SELECT l.name, count(k.model_id) AS n FROM {table} l JOIN {link} k ON k.{fk} = l.id \
             GROUP BY l.id ORDER BY n DESC, l.id"
        ),
        limit,
    )
}

/// The Column Distribution rows for `key` (unknown keys give none) and the model count.
pub fn column(conn: &Connection, key: &str, top_n: Option<i64>) -> Result<(Ranking, i64)> {
    let total: i64 = conn.query_row("SELECT count(id) FROM models", [], |r| r.get(0))?;
    let direct = |col: &str, skip_null: bool| {
        let filter = if skip_null { format!(" WHERE {col} IS NOT NULL") } else { String::new() };
        ranking(
            conn,
            &format!("SELECT {col}, count(id) AS n FROM models{filter} GROUP BY {col} ORDER BY n DESC, {col}"),
            top_n,
        )
    };
    let rows = match key {
        "source" | "presupports" | "creator" => direct(key, false)?,
        "release_year" | "release_month" => direct(key, true)?,
        "sculptor" => joined(conn, "sculptors", "sculptor_id", top_n)?,
        "collection" => joined(conn, "collections", "collection_id", top_n)?,
        "subcollection" => joined(conn, "subcollections", "subcollection_id", top_n)?,
        "category" => joined(conn, "categories", "category_id", top_n)?,
        "release" => joined(conn, "releases", "release_id", top_n)?,
        "scale" => linked(conn, "scales", "model_scales", "scale_id", top_n)?,
        "tag" => linked(conn, "tags", "model_tags", "tag_id", top_n)?,
        _ => Vec::new(),
    };
    Ok((rows, total))
}
