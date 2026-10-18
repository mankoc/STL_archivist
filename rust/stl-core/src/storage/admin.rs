//! Lookup-table administration for the editor dialogs: a port of
//! `app/services/lookup_admin.py` (tags, categories, collections,
//! subcollections, scales, creator names and creator profiles).
//!
//! What the ORM did as a side effect is done explicitly here:
//!
//! * deleting a category, collection or subcollection nulls the models'
//!   reference first (the ORM's dependency UPDATE, which also sets
//!   `updated_at`); a collection takes its subcollections with it;
//! * deleting a tag or scale only removes its link rows;
//! * bulk creator updates (`query.update`) set `updated_at`.

use rusqlite::{Connection, OptionalExtension, Transaction, params};

use super::Result;
use super::edit::utc_now_text;

/// `(table, models column or link table)` per lookup kind.
fn table(kind: &str) -> Result<&'static str> {
    Ok(match kind {
        "tag" => "tags",
        "category" => "categories",
        "collection" => "collections",
        "subcollection" => "subcollections",
        "scale" => "scales",
        other => return Err(rusqlite::Error::InvalidParameterName(format!("unknown lookup kind: {other}"))),
    })
}

/// One editor row: `(id, name, models, collection_id, collection_name)`; the
/// last two only for subcollections.
pub type AdminRow = (i64, String, i64, Option<i64>, Option<String>);

/// `list_lookups`.
pub fn list(conn: &Connection, kind: &str) -> Result<Vec<AdminRow>> {
    let t = table(kind)?;
    let counts = super::library::usage_counts(conn, kind)?;
    if kind == "subcollection" {
        let mut st = conn.prepare(
            "SELECT subcollections.id, subcollections.name, collections.id, collections.name FROM subcollections \
             JOIN collections ON subcollections.collection_id = collections.id \
             ORDER BY collections.name, subcollections.name",
        )?;
        return st
            .query_map([], |r| {
                let id: i64 = r.get(0)?;
                Ok((id, r.get(1)?, counts.get(&id).copied().unwrap_or(0), Some(r.get(2)?), Some(r.get(3)?)))
            })?
            .collect();
    }
    let mut st = conn.prepare(&format!("SELECT id, name FROM {t} ORDER BY name"))?;
    st.query_map([], |r| {
        let id: i64 = r.get(0)?;
        Ok((id, r.get(1)?, counts.get(&id).copied().unwrap_or(0), None, None))
    })?
    .collect()
}

/// `tags_for_merge_scan`: `(id, name, models)` for every tag, by id.
pub fn tags_for_scan(conn: &Connection) -> Result<Vec<(i64, String, i64)>> {
    let mut st = conn.prepare(
        "SELECT tags.id, tags.name, count(model_tags.model_id) FROM tags \
         LEFT OUTER JOIN model_tags ON model_tags.tag_id = tags.id GROUP BY tags.id, tags.name ORDER BY tags.id",
    )?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect()
}

fn id_by_name(tx: &Connection, t: &str, name: &str) -> Result<Option<i64>> {
    tx.query_row(&format!("SELECT id FROM {t} WHERE name = ?1 ORDER BY id LIMIT 1"), [name], |r| r.get(0)).optional()
}

/// `add`: `false` when the name exists (or the parent collection does not).
pub fn add(tx: &Transaction, kind: &str, name: &str, collection_name: Option<&str>) -> Result<bool> {
    let t = table(kind)?;
    if kind == "subcollection" {
        let Some(col) = id_by_name(tx, "collections", collection_name.unwrap_or_default())? else { return Ok(false) };
        let taken: Option<i64> = tx
            .query_row(
                "SELECT id FROM subcollections WHERE name = ?1 AND collection_id = ?2 LIMIT 1",
                params![name, col],
                |r| r.get(0),
            )
            .optional()?;
        if taken.is_some() {
            return Ok(false);
        }
        tx.execute("INSERT INTO subcollections (collection_id, name) VALUES (?1, ?2)", params![col, name])?;
        return Ok(true);
    }
    if id_by_name(tx, t, name)?.is_some() {
        return Ok(false);
    }
    tx.execute(&format!("INSERT INTO {t} (name) VALUES (?1)"), [name])?;
    Ok(true)
}

/// `name_of`: `(name, collection_id)`.
pub fn name_of(conn: &Connection, kind: &str, id: i64) -> Result<Option<(String, Option<i64>)>> {
    let t = table(kind)?;
    if kind == "subcollection" {
        return conn
            .query_row("SELECT name, collection_id FROM subcollections WHERE id = ?1", [id], |r| {
                Ok((r.get(0)?, Some(r.get(1)?)))
            })
            .optional();
    }
    conn.query_row(&format!("SELECT name FROM {t} WHERE id = ?1"), [id], |r| Ok((r.get(0)?, None))).optional()
}

/// `rename`: `false` when the new name is taken (within the collection, for a subcollection).
pub fn rename(tx: &Transaction, kind: &str, id: i64, new_name: &str) -> Result<bool> {
    let t = table(kind)?;
    let Some((_, col)) = name_of(tx, kind, id)? else { return Ok(false) };
    let taken: Option<i64> = if kind == "subcollection" {
        tx.query_row(
            "SELECT id FROM subcollections WHERE name = ?1 AND collection_id = ?2 LIMIT 1",
            params![new_name, col],
            |r| r.get(0),
        )
        .optional()?
    } else {
        id_by_name(tx, t, new_name)?
    };
    if taken.is_some() {
        return Ok(false);
    }
    tx.execute(&format!("UPDATE {t} SET name = ?1 WHERE id = ?2"), params![new_name, id])?;
    Ok(true)
}

/// `delete_info`: `(name, models using it, subcollections)`.
pub fn delete_info(conn: &Connection, kind: &str, id: i64) -> Result<Option<(String, i64, i64)>> {
    let Some((name, _)) = name_of(conn, kind, id)? else { return Ok(None) };
    let used = super::library::usage_counts(conn, kind)?.get(&id).copied().unwrap_or(0);
    let subs = if kind == "collection" {
        conn.query_row("SELECT count(*) FROM subcollections WHERE collection_id = ?1", [id], |r| r.get(0))?
    } else {
        0
    };
    Ok(Some((name, used, subs)))
}

/// Null a model column for the rows matching `where_sql`, setting `updated_at`.
fn null_ref(tx: &Transaction, column: &str, where_sql: &str, param: i64) -> Result<()> {
    tx.execute(
        &format!("UPDATE models SET {column} = NULL, updated_at = ?2 WHERE {where_sql}"),
        params![param, utc_now_text()],
    )?;
    Ok(())
}

/// `delete`.
pub fn delete(tx: &Transaction, kind: &str, id: i64) -> Result<()> {
    match kind {
        "tag" => {
            tx.execute("DELETE FROM model_tags WHERE tag_id = ?1", [id])?;
            tx.execute("DELETE FROM tags WHERE id = ?1", [id])?;
        }
        "scale" => {
            tx.execute("DELETE FROM model_scales WHERE scale_id = ?1", [id])?;
            tx.execute("DELETE FROM scales WHERE id = ?1", [id])?;
        }
        "category" => {
            null_ref(tx, "category_id", "category_id = ?1", id)?;
            tx.execute("DELETE FROM categories WHERE id = ?1", [id])?;
        }
        "subcollection" => {
            null_ref(tx, "subcollection_id", "subcollection_id = ?1", id)?;
            tx.execute("DELETE FROM subcollections WHERE id = ?1", [id])?;
        }
        "collection" => delete_collection(tx, id)?,
        other => return Err(rusqlite::Error::InvalidParameterName(format!("unknown lookup kind: {other}"))),
    }
    Ok(())
}

fn delete_collection(tx: &Transaction, id: i64) -> Result<()> {
    null_ref(tx, "subcollection_id", "subcollection_id IN (SELECT id FROM subcollections WHERE collection_id = ?1)", id)?;
    null_ref(tx, "collection_id", "collection_id = ?1", id)?;
    tx.execute("DELETE FROM subcollections WHERE collection_id = ?1", [id])?;
    tx.execute("DELETE FROM collections WHERE id = ?1", [id])?;
    Ok(())
}

/// `merge`: the models of `delete_id` move to `keep_id`, then `delete_id` goes.
pub fn merge(tx: &Transaction, kind: &str, keep_id: i64, delete_id: i64) -> Result<()> {
    let now = utc_now_text();
    match kind {
        "category" => {
            tx.execute(
                "UPDATE models SET category_id = ?1, updated_at = ?3 WHERE category_id = ?2",
                params![keep_id, delete_id, now],
            )?;
            tx.execute("DELETE FROM categories WHERE id = ?1", [delete_id])?;
        }
        "subcollection" => {
            tx.execute(
                "UPDATE models SET subcollection_id = ?1, updated_at = ?3 WHERE subcollection_id = ?2",
                params![keep_id, delete_id, now],
            )?;
            tx.execute("DELETE FROM subcollections WHERE id = ?1", [delete_id])?;
        }
        "collection" => {
            tx.execute(
                "UPDATE models SET collection_id = ?1, subcollection_id = NULL, updated_at = ?3 WHERE collection_id = ?2",
                params![keep_id, delete_id, now],
            )?;
            delete_collection(tx, delete_id)?;
        }
        other => return Err(rusqlite::Error::InvalidParameterName(format!("merge is not offered for {other}"))),
    }
    Ok(())
}

const UNUSED_TAGS: &str = "NOT EXISTS (SELECT 1 FROM model_tags WHERE model_tags.tag_id = tags.id)";

pub fn unused_tag_count(conn: &Connection) -> Result<i64> {
    conn.query_row(&format!("SELECT count(*) FROM tags WHERE {UNUSED_TAGS}"), [], |r| r.get(0))
}

pub fn delete_unused_tags(tx: &Transaction) -> Result<usize> {
    tx.execute(&format!("DELETE FROM tags WHERE {UNUSED_TAGS}"), [])
}

/// `merge_tags`: re-tag the models of `tag_ids` with the tag named `target`
/// (SQL `lower(name)` against `target` lowered the Unicode way, as Python
/// compared; created with `target` as given when missing) and delete them.
pub fn merge_tags(tx: &Transaction, tag_ids: &[i64], target: &str) -> Result<()> {
    let keep: i64 = match tx
        .query_row("SELECT id FROM tags WHERE lower(name) = ?1 ORDER BY id LIMIT 1", [target.to_lowercase()], |r| r.get(0))
        .optional()?
    {
        Some(id) => id,
        None => {
            tx.execute("INSERT INTO tags (name) VALUES (?1)", [target])?;
            tx.last_insert_rowid()
        }
    };
    for &tid in tag_ids {
        if tid == keep {
            continue;
        }
        let exists: Option<i64> = tx.query_row("SELECT id FROM tags WHERE id = ?1", [tid], |r| r.get(0)).optional()?;
        if exists.is_none() {
            continue;
        }
        tx.execute(
            "INSERT INTO model_tags (model_id, tag_id) SELECT model_id, ?1 FROM model_tags WHERE tag_id = ?2 \
             AND model_id NOT IN (SELECT model_id FROM model_tags WHERE tag_id = ?1)",
            params![keep, tid],
        )?;
        tx.execute("DELETE FROM model_tags WHERE tag_id = ?1", [tid])?;
        tx.execute("DELETE FROM tags WHERE id = ?1", [tid])?;
    }
    Ok(())
}

// ── creators ─────────────────────────────────────────────────────────────────

pub fn author_counts(conn: &Connection) -> Result<Vec<(String, i64)>> {
    let mut st = conn.prepare(
        "SELECT creator, count(id) FROM models WHERE creator IS NOT NULL GROUP BY creator ORDER BY creator",
    )?;
    st.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?.collect()
}

pub fn author_model_count(conn: &Connection, name: &str) -> Result<i64> {
    conn.query_row("SELECT count(*) FROM models WHERE creator = ?1", [name], |r| r.get(0))
}

fn profile_exists(tx: &Connection, name: &str) -> Result<bool> {
    Ok(tx.query_row("SELECT 1 FROM creators WHERE name = ?1", [name], |_| Ok(())).optional()?.is_some())
}

pub fn rename_author(tx: &Transaction, old_name: &str, new_name: &str) -> Result<()> {
    tx.execute(
        "UPDATE models SET creator = ?1, updated_at = ?3 WHERE creator = ?2",
        params![new_name, old_name, utc_now_text()],
    )?;
    if profile_exists(tx, old_name)? {
        if profile_exists(tx, new_name)? {
            tx.execute("DELETE FROM creators WHERE name = ?1", [old_name])?;
        } else {
            tx.execute("UPDATE creators SET name = ?1 WHERE name = ?2", params![new_name, old_name])?;
        }
    }
    Ok(())
}

pub fn delete_author(tx: &Transaction, name: &str) -> Result<()> {
    tx.execute("UPDATE models SET creator = NULL, updated_at = ?2 WHERE creator = ?1", params![name, utc_now_text()])?;
    tx.execute("DELETE FROM creators WHERE name = ?1", [name])?;
    Ok(())
}

pub fn merge_authors(tx: &Transaction, keep_name: &str, delete_name: &str) -> Result<()> {
    tx.execute(
        "UPDATE models SET creator = ?1, updated_at = ?3 WHERE creator = ?2",
        params![keep_name, delete_name, utc_now_text()],
    )?;
    tx.execute("DELETE FROM creators WHERE name = ?1", [delete_name])?;
    Ok(())
}

/// Profile columns, in `lookup_admin.PROFILE_FIELDS` order.
pub const PROFILE_FIELDS: [&str; 5] = ["full_name", "patreon_url", "artstation_url", "gumroad_url", "myminifactory_url"];

pub fn creator_profile(conn: &Connection, name: &str) -> Result<Option<[Option<String>; 5]>> {
    conn.query_row(
        &format!("SELECT {} FROM creators WHERE name = ?1", PROFILE_FIELDS.join(", ")),
        [name],
        |r| Ok([r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?]),
    )
    .optional()
}

pub fn save_creator_profile(tx: &Transaction, name: &str, values: &[Option<String>; 5]) -> Result<()> {
    if !profile_exists(tx, name)? {
        tx.execute("INSERT INTO creators (name) VALUES (?1)", [name])?;
    }
    tx.execute(
        &format!(
            "UPDATE creators SET {} WHERE name = ?6",
            PROFILE_FIELDS.iter().enumerate().map(|(i, f)| format!("{f} = ?{}", i + 1)).collect::<Vec<_>>().join(", ")
        ),
        params![values[0], values[1], values[2], values[3], values[4], name],
    )?;
    Ok(())
}
