//! User accounts, sessions and per-user settings in `<data_dir>/server.db`.
//!
//! Kept out of the library database so the library's schema (rule 6) stays
//! what the desktop app expects. Passwords are Argon2id hashes; a session
//! token is 32 random bytes (hex), stored only as its SHA-256.

use std::time::{SystemTime, UNIX_EPOCH};

use argon2::password_hash::rand_core::{OsRng, RngCore};
use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::Argon2;
use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use utoipa::ToSchema;

pub const DB_FILE: &str = "server.db";

/// A session lasts this long after its last use.
pub const SESSION_IDLE_SECS: i64 = 30 * 24 * 3600;
/// `last_used` is refreshed at most this often.
const TOUCH_SECS: i64 = 60;

pub const MIN_PASSWORD: usize = 8;
const MAX_NAME: usize = 64;
const MAX_SETTING_BYTES: usize = 64 * 1024;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash BLOB PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    last_used INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
PRAGMA user_version = 1;
";

/// What a user may do. Ordered: each role includes the ones before it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, ToSchema)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    /// Read the library.
    Viewer,
    /// Also edit models and lookups, run crawls and maintenance.
    Editor,
    /// Also manage users and server settings.
    Admin,
}

impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Role::Viewer => "viewer",
            Role::Editor => "editor",
            Role::Admin => "admin",
        }
    }

    fn parse(s: &str) -> Role {
        match s {
            "admin" => Role::Admin,
            "editor" => Role::Editor,
            _ => Role::Viewer,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, ToSchema)]
pub struct User {
    pub id: i64,
    pub name: String,
    pub role: Role,
    pub disabled: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub enum AccountError {
    Invalid(String),
    Conflict(String),
    NotFound,
    Db(String),
}

impl From<rusqlite::Error> for AccountError {
    fn from(e: rusqlite::Error) -> Self {
        AccountError::Db(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, AccountError>;

pub fn now() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

/// Create the tables if missing.
pub fn ensure_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA)
}

fn user_from_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<User> {
    Ok(User { id: r.get(0)?, name: r.get(1)?, role: Role::parse(&r.get::<_, String>(2)?), disabled: r.get(3)? })
}

const USER_COLS: &str = "id, name, role, disabled";

fn clean_name(name: &str) -> Result<String> {
    let name = name.trim();
    if name.is_empty() || name.chars().count() > MAX_NAME || name.chars().any(char::is_control) {
        return Err(AccountError::Invalid(format!("a user name has 1 to {MAX_NAME} printable characters")));
    }
    Ok(name.to_string())
}

fn check_password(password: &str) -> Result<()> {
    if password.chars().count() < MIN_PASSWORD {
        return Err(AccountError::Invalid(format!("a password has at least {MIN_PASSWORD} characters")));
    }
    Ok(())
}

pub fn hash_password(password: &str) -> Result<String> {
    let salt = SaltString::generate(&mut OsRng);
    Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map(|h| h.to_string())
        .map_err(|e| AccountError::Db(format!("password hashing: {e}")))
}

fn verify_password(password: &str, hash: &str) -> bool {
    PasswordHash::new(hash).is_ok_and(|h| Argon2::default().verify_password(password.as_bytes(), &h).is_ok())
}

pub fn user_count(conn: &Connection) -> Result<i64> {
    Ok(conn.query_row("SELECT count(*) FROM users", [], |r| r.get(0))?)
}

pub fn list_users(conn: &Connection) -> Result<Vec<User>> {
    let mut st = conn.prepare(&format!("SELECT {USER_COLS} FROM users ORDER BY name COLLATE NOCASE"))?;
    Ok(st.query_map([], user_from_row)?.collect::<rusqlite::Result<_>>()?)
}

pub fn get_user(conn: &Connection, id: i64) -> Result<User> {
    conn.query_row(&format!("SELECT {USER_COLS} FROM users WHERE id = ?1"), [id], user_from_row)
        .optional()?
        .ok_or(AccountError::NotFound)
}

pub fn create_user(conn: &Connection, name: &str, password: &str, role: Role) -> Result<User> {
    let name = clean_name(name)?;
    check_password(password)?;
    let hash = hash_password(password)?;
    let taken: bool = conn.query_row("SELECT count(*) > 0 FROM users WHERE name = ?1", [&name], |r| r.get(0))?;
    if taken {
        return Err(AccountError::Conflict(format!("the name {name:?} is taken")));
    }
    conn.execute(
        "INSERT INTO users (name, password_hash, role, created_at) VALUES (?1, ?2, ?3, ?4)",
        params![name, hash, role.as_str(), now()],
    )?;
    get_user(conn, conn.last_insert_rowid())
}

fn active_admins_other_than(conn: &Connection, id: i64) -> Result<i64> {
    Ok(conn.query_row(
        "SELECT count(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?1",
        [id],
        |r| r.get(0),
    )?)
}

/// Change a user's role, enabled state or password. The last active admin can
/// be neither demoted nor disabled.
pub fn update_user(conn: &Connection, id: i64, role: Option<Role>, disabled: Option<bool>, password: Option<&str>) -> Result<User> {
    let user = get_user(conn, id)?;
    let loses_admin = user.role == Role::Admin
        && !user.disabled
        && (role.is_some_and(|r| r != Role::Admin) || disabled == Some(true));
    if loses_admin && active_admins_other_than(conn, id)? == 0 {
        return Err(AccountError::Conflict("the last administrator must stay an enabled administrator".into()));
    }
    if let Some(p) = password {
        check_password(p)?;
        conn.execute("UPDATE users SET password_hash = ?2 WHERE id = ?1", params![id, hash_password(p)?])?;
        conn.execute("DELETE FROM sessions WHERE user_id = ?1", [id])?;
    }
    if let Some(r) = role {
        conn.execute("UPDATE users SET role = ?2 WHERE id = ?1", params![id, r.as_str()])?;
    }
    if let Some(d) = disabled {
        conn.execute("UPDATE users SET disabled = ?2 WHERE id = ?1", params![id, d])?;
        if d {
            conn.execute("DELETE FROM sessions WHERE user_id = ?1", [id])?;
        }
    }
    get_user(conn, id)
}

pub fn delete_user(conn: &Connection, id: i64) -> Result<()> {
    let user = get_user(conn, id)?;
    if user.role == Role::Admin && !user.disabled && active_admins_other_than(conn, id)? == 0 {
        return Err(AccountError::Conflict("the last administrator cannot be deleted".into()));
    }
    conn.execute("DELETE FROM users WHERE id = ?1", [id])?;
    Ok(())
}

/// Check a user's own password change: the current password must match.
pub fn change_own_password(conn: &Connection, id: i64, current: &str, new: &str) -> Result<()> {
    let hash: String = conn.query_row("SELECT password_hash FROM users WHERE id = ?1", [id], |r| r.get(0))?;
    if !verify_password(current, &hash) {
        return Err(AccountError::Invalid("the current password is wrong".into()));
    }
    update_user(conn, id, None, None, Some(new)).map(|_| ())
}

/// The enabled user with these credentials, if any. Runs a hash check even for
/// unknown names, so timing does not reveal which names exist.
pub fn authenticate(conn: &Connection, name: &str, password: &str) -> Result<Option<User>> {
    let row: Option<(User, String)> = conn
        .query_row(
            &format!("SELECT {USER_COLS}, password_hash FROM users WHERE name = ?1"),
            [name.trim()],
            |r| Ok((user_from_row(r)?, r.get(4)?)),
        )
        .optional()?;
    match row {
        Some((user, hash)) => Ok((verify_password(password, &hash) && !user.disabled).then_some(user)),
        None => {
            let _ = verify_password(password, dummy_hash());
            Ok(None)
        }
    }
}

/// A hash of a random string with the real parameters, for constant-time refusals.
fn dummy_hash() -> &'static str {
    static HASH: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    HASH.get_or_init(|| {
        let mut bytes = [0u8; 16];
        OsRng.fill_bytes(&mut bytes);
        hash_password(&format!("{bytes:?}")).unwrap_or_default()
    })
}

fn token_hash(token: &str) -> Vec<u8> {
    Sha256::digest(token.as_bytes()).to_vec()
}

/// Start a session; returns the token (only its hash is stored).
pub fn create_session(conn: &Connection, user_id: i64) -> Result<String> {
    let mut bytes = [0u8; 32];
    OsRng.fill_bytes(&mut bytes);
    let token: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    let t = now();
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_used) VALUES (?1, ?2, ?3, ?3)",
        params![token_hash(&token), user_id, t],
    )?;
    Ok(token)
}

/// The enabled user owning a live session; refreshes its `last_used`.
pub fn session_user(conn: &Connection, token: &str) -> Result<Option<User>> {
    let hash = token_hash(token);
    let t = now();
    let row: Option<(User, i64)> = conn
        .query_row(
            "SELECT u.id, u.name, u.role, u.disabled, s.last_used FROM sessions s \
             JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?1",
            [&hash],
            |r| Ok((user_from_row(r)?, r.get(4)?)),
        )
        .optional()?;
    let Some((user, last_used)) = row else { return Ok(None) };
    if t - last_used > SESSION_IDLE_SECS || user.disabled {
        conn.execute("DELETE FROM sessions WHERE token_hash = ?1", [&hash])?;
        return Ok(None);
    }
    if t - last_used > TOUCH_SECS {
        conn.execute("UPDATE sessions SET last_used = ?2 WHERE token_hash = ?1", params![hash, t])?;
    }
    Ok(Some(user))
}

pub fn end_session(conn: &Connection, token: &str) -> Result<()> {
    conn.execute("DELETE FROM sessions WHERE token_hash = ?1", [token_hash(token)])?;
    Ok(())
}

fn check_key(key: &str) -> Result<()> {
    let ok = !key.is_empty()
        && key.len() <= MAX_NAME
        && key.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b));
    if ok { Ok(()) } else { Err(AccountError::Invalid("a setting key has 1 to 64 of a-z, A-Z, 0-9, '.', '_', '-'".into())) }
}

/// A user's settings: key → JSON value.
pub fn settings(conn: &Connection, user_id: i64) -> Result<serde_json::Map<String, serde_json::Value>> {
    let mut st = conn.prepare("SELECT key, value FROM user_settings WHERE user_id = ?1 ORDER BY key")?;
    let rows = st.query_map([user_id], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
    let mut out = serde_json::Map::new();
    for row in rows {
        let (k, v) = row?;
        out.insert(k, serde_json::from_str(&v).unwrap_or(serde_json::Value::Null));
    }
    Ok(out)
}

pub fn set_setting(conn: &Connection, user_id: i64, key: &str, value: &serde_json::Value) -> Result<()> {
    check_key(key)?;
    let text = value.to_string();
    if text.len() > MAX_SETTING_BYTES {
        return Err(AccountError::Invalid(format!("a setting holds at most {MAX_SETTING_BYTES} bytes")));
    }
    conn.execute(
        "INSERT INTO user_settings (user_id, key, value) VALUES (?1, ?2, ?3) \
         ON CONFLICT (user_id, key) DO UPDATE SET value = excluded.value",
        params![user_id, key, text],
    )?;
    Ok(())
}

pub fn delete_setting(conn: &Connection, user_id: i64, key: &str) -> Result<()> {
    check_key(key)?;
    conn.execute("DELETE FROM user_settings WHERE user_id = ?1 AND key = ?2", params![user_id, key])?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch("PRAGMA foreign_keys = ON").unwrap();
        ensure_schema(&c).unwrap();
        c
    }

    #[test]
    fn dummy_hash_parses() {
        assert!(PasswordHash::new(dummy_hash()).is_ok());
    }

    #[test]
    fn users_and_authentication() {
        let c = db();
        let admin = create_user(&c, " Alice ", "correct horse", Role::Admin).unwrap();
        assert_eq!(admin.name, "Alice");
        assert_eq!(create_user(&c, "alice", "whatever1", Role::Viewer).unwrap_err(), AccountError::Conflict("the name \"alice\" is taken".into()));
        assert!(matches!(create_user(&c, "bob", "short", Role::Viewer), Err(AccountError::Invalid(_))));
        assert!(matches!(create_user(&c, "", "long enough", Role::Viewer), Err(AccountError::Invalid(_))));
        assert_eq!(authenticate(&c, "ALICE", "correct horse").unwrap().map(|u| u.id), Some(admin.id));
        assert_eq!(authenticate(&c, "alice", "wrong").unwrap(), None);
        assert_eq!(authenticate(&c, "nobody", "correct horse").unwrap(), None);
    }

    #[test]
    fn last_admin_is_protected() {
        let c = db();
        let a = create_user(&c, "a", "password1", Role::Admin).unwrap();
        assert!(matches!(update_user(&c, a.id, Some(Role::Editor), None, None), Err(AccountError::Conflict(_))));
        assert!(matches!(update_user(&c, a.id, None, Some(true), None), Err(AccountError::Conflict(_))));
        assert!(matches!(delete_user(&c, a.id), Err(AccountError::Conflict(_))));
        let b = create_user(&c, "b", "password2", Role::Admin).unwrap();
        assert_eq!(update_user(&c, a.id, Some(Role::Viewer), None, None).unwrap().role, Role::Viewer);
        assert!(matches!(delete_user(&c, b.id), Err(AccountError::Conflict(_))));
        delete_user(&c, a.id).unwrap();
        assert_eq!(list_users(&c).unwrap(), vec![b]);
    }

    #[test]
    fn sessions_expire_and_end_with_password_changes() {
        let c = db();
        let u = create_user(&c, "u", "password1", Role::Editor).unwrap();
        let t = create_session(&c, u.id).unwrap();
        assert_eq!(t.len(), 64);
        assert_eq!(session_user(&c, &t).unwrap(), Some(u.clone()));
        assert_eq!(session_user(&c, "nope").unwrap(), None);
        // A session idle for too long is gone.
        c.execute("UPDATE sessions SET last_used = last_used - ?1", [SESSION_IDLE_SECS + 1]).unwrap();
        assert_eq!(session_user(&c, &t).unwrap(), None);
        let t = create_session(&c, u.id).unwrap();
        assert!(matches!(change_own_password(&c, u.id, "wrong", "password2"), Err(AccountError::Invalid(_))));
        change_own_password(&c, u.id, "password1", "password2").unwrap();
        assert_eq!(session_user(&c, &t).unwrap(), None, "a password change ends sessions");
        let t = create_session(&c, u.id).unwrap();
        update_user(&c, u.id, None, Some(true), None).unwrap();
        assert_eq!(session_user(&c, &t).unwrap(), None, "disabling ends sessions");
        assert_eq!(authenticate(&c, "u", "password2").unwrap(), None, "disabled users cannot log in");
    }

    #[test]
    fn settings_round_trip() {
        let c = db();
        let u = create_user(&c, "u", "password1", Role::Viewer).unwrap();
        set_setting(&c, u.id, "table.columns", &serde_json::json!(["title", "creator"])).unwrap();
        set_setting(&c, u.id, "theme", &serde_json::json!("dark")).unwrap();
        set_setting(&c, u.id, "theme", &serde_json::json!("light")).unwrap();
        let s = settings(&c, u.id).unwrap();
        assert_eq!(s["theme"], "light");
        assert_eq!(s["table.columns"][1], "creator");
        delete_setting(&c, u.id, "theme").unwrap();
        assert!(!settings(&c, u.id).unwrap().contains_key("theme"));
        assert!(matches!(set_setting(&c, u.id, "bad key", &serde_json::json!(1)), Err(AccountError::Invalid(_))));
        let big = serde_json::json!("x".repeat(MAX_SETTING_BYTES));
        assert!(matches!(set_setting(&c, u.id, "big", &big), Err(AccountError::Invalid(_))));
    }
}
