//! Sync with another server, pull or push, always started here: the remotes
//! this server knows (`sync_remotes` in `server.db`: URL, a session token on
//! the remote, path rules) and a blocking client for the remote's API. A sync
//! mirrors one library onto the other through the import (`import_library`,
//! on the receiving side); only the thumbnails the receiver lacks travel
//! (`backup::write_sync`). The job bodies are in `jobs`.
//!
//! The token is the remote's session for one of its administrators, made by
//! signing in there; the password is never kept, and the token never leaves
//! this server.

use std::cmp::Ordering;
use std::collections::HashSet;
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

use rusqlite::{Connection, OptionalExtension, params};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use stl_core::control::Control;
use utoipa::ToSchema;

/// The first version that syncs: older remotes are refused.
pub const MIN_VERSION: &str = "2026.44.5";
/// The header `/api/sync/thumbnails` answers with: the server's `Library::instance`.
pub const INSTANCE_HEADER: &str = "x-stl-instance";

/// A server's random id for one run (see [`INSTANCE_HEADER`]).
pub fn new_instance() -> String {
    use rand_core::RngCore;
    let mut bytes = [0u8; 16];
    rand_core::OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS sync_remotes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    user TEXT NOT NULL,
    token TEXT NOT NULL,
    rules TEXT NOT NULL DEFAULT '[]',
    backup_first INTEGER NOT NULL DEFAULT 1,
    last_sync_at INTEGER,
    last_direction TEXT,
    last_result TEXT
);
";

pub fn ensure_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA)
}

/// Which way a sync goes: `pull` makes this library a copy of the remote's,
/// `push` makes the remote's a copy of this one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, ToSchema)]
#[serde(rename_all = "snake_case")]
pub enum Direction {
    Pull,
    Push,
}

impl Direction {
    pub fn as_str(self) -> &'static str {
        match self {
            Direction::Pull => "pull",
            Direction::Push => "push",
        }
    }
}

/// A folder here and the same folder on the remote: a path-start rule, used
/// both ways (`D:\Minis` here is `/library` there).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct PathRule {
    pub local: String,
    pub remote: String,
}

/// A path-start rule as the import applies it: roots below `from` go below `to`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, ToSchema)]
#[serde(deny_unknown_fields)]
pub struct RootRule {
    pub from: String,
    pub to: String,
}

impl PathRule {
    /// The rule for the import on the receiving side.
    pub fn toward(&self, direction: Direction) -> RootRule {
        match direction {
            Direction::Pull => RootRule { from: self.remote.clone(), to: self.local.clone() },
            Direction::Push => RootRule { from: self.local.clone(), to: self.remote.clone() },
        }
    }
}

/// The root `rules` give `root` (the longest matching `from` wins).
pub fn apply_rules(rules: &[RootRule], root: &str) -> Option<String> {
    rules
        .iter()
        .filter_map(|r| stl_core::storage::libraries::rebase(root, &r.from, &r.to).map(|t| (r.from.trim().len(), t)))
        .max_by_key(|(len, _)| *len)
        .map(|(_, t)| t)
}

/// A remote as stored.
#[derive(Debug, Clone)]
pub struct Stored {
    pub id: i64,
    pub name: String,
    pub url: String,
    pub user: String,
    pub token: String,
    pub rules: Vec<PathRule>,
    pub backup_first: bool,
    pub last_sync_at: Option<i64>,
    pub last_direction: Option<String>,
    pub last_result: Option<String>,
}

/// A remote as the API shows it (without its token).
#[derive(Debug, Serialize, ToSchema)]
pub struct RemoteInfo {
    pub id: i64,
    pub name: String,
    pub url: String,
    /// The remote's administrator this server signed in as.
    pub user: String,
    pub rules: Vec<PathRule>,
    /// The receiving side backs its library up before a sync replaces it.
    pub backup_first: bool,
    /// Unix seconds.
    pub last_sync_at: Option<i64>,
    pub last_direction: Option<String>,
    pub last_result: Option<String>,
}

impl From<Stored> for RemoteInfo {
    fn from(s: Stored) -> Self {
        RemoteInfo {
            id: s.id,
            name: s.name,
            url: s.url,
            user: s.user,
            rules: s.rules,
            backup_first: s.backup_first,
            last_sync_at: s.last_sync_at,
            last_direction: s.last_direction,
            last_result: s.last_result,
        }
    }
}

const COLUMNS: &str = "id, name, url, user, token, rules, backup_first, last_sync_at, last_direction, last_result";

fn from_row(r: &rusqlite::Row) -> rusqlite::Result<Stored> {
    let rules: String = r.get(5)?;
    Ok(Stored {
        id: r.get(0)?,
        name: r.get(1)?,
        url: r.get(2)?,
        user: r.get(3)?,
        token: r.get(4)?,
        rules: serde_json::from_str(&rules).unwrap_or_default(),
        backup_first: r.get::<_, i64>(6)? != 0,
        last_sync_at: r.get(7)?,
        last_direction: r.get(8)?,
        last_result: r.get(9)?,
    })
}

pub fn list(conn: &Connection) -> rusqlite::Result<Vec<Stored>> {
    let mut st = conn.prepare(&format!("SELECT {COLUMNS} FROM sync_remotes ORDER BY name COLLATE NOCASE"))?;
    st.query_map([], from_row)?.collect()
}

pub fn get(conn: &Connection, id: i64) -> rusqlite::Result<Option<Stored>> {
    conn.query_row(&format!("SELECT {COLUMNS} FROM sync_remotes WHERE id = ?1"), [id], from_row).optional()
}

pub fn insert(conn: &Connection, name: &str, url: &str, user: &str, token: &str, rules: &[PathRule]) -> rusqlite::Result<i64> {
    conn.execute(
        "INSERT INTO sync_remotes (name, url, user, token, rules) VALUES (?1, ?2, ?3, ?4, ?5)",
        params![name, url, user, token, serde_json::to_string(rules).expect("rules serialise")],
    )?;
    Ok(conn.last_insert_rowid())
}

pub fn update(conn: &Connection, id: i64, name: Option<&str>, rules: Option<&[PathRule]>, backup_first: Option<bool>) -> rusqlite::Result<()> {
    if let Some(n) = name {
        conn.execute("UPDATE sync_remotes SET name = ?2 WHERE id = ?1", params![id, n])?;
    }
    if let Some(r) = rules {
        conn.execute("UPDATE sync_remotes SET rules = ?2 WHERE id = ?1", params![id, serde_json::to_string(r).expect("rules serialise")])?;
    }
    if let Some(b) = backup_first {
        conn.execute("UPDATE sync_remotes SET backup_first = ?2 WHERE id = ?1", params![id, b])?;
    }
    Ok(())
}

pub fn set_token(conn: &Connection, id: i64, user: &str, token: &str) -> rusqlite::Result<()> {
    conn.execute("UPDATE sync_remotes SET user = ?2, token = ?3 WHERE id = ?1", params![id, user, token])?;
    Ok(())
}

pub fn delete(conn: &Connection, id: i64) -> rusqlite::Result<bool> {
    Ok(conn.execute("DELETE FROM sync_remotes WHERE id = ?1", [id])? > 0)
}

/// Note how the last sync with remote `id` went.
pub fn record(conn: &Connection, id: i64, direction: Direction, result: &str) -> rusqlite::Result<()> {
    conn.execute(
        "UPDATE sync_remotes SET last_sync_at = ?2, last_direction = ?3, last_result = ?4 WHERE id = ?1",
        params![id, crate::accounts::now(), direction.as_str(), result],
    )?;
    Ok(())
}

/// `url` without trailing `/`, when it is an `http(s)://` address.
pub fn clean_url(url: &str) -> Result<String, String> {
    let u = url.trim().trim_end_matches('/');
    let rest = u.strip_prefix("http://").or_else(|| u.strip_prefix("https://")).ok_or("the address must start with http:// or https://")?;
    if rest.is_empty() || rest.contains(char::is_whitespace) {
        return Err(format!("{url:?} is not a server address"));
    }
    Ok(u.to_string())
}

// ── versions ─────────────────────────────────────────────────────────────────

/// `a` against `b` as versions (`2026.44.5`), number by number.
pub fn version_cmp(a: &str, b: &str) -> Ordering {
    let parts = |v: &str| v.split('.').map(|p| p.trim().parse::<u64>().unwrap_or(0)).collect::<Vec<_>>();
    parts(a).cmp(&parts(b))
}

/// Whether a sync with remote `name` (running `remote`) can go `direction`:
/// the remote syncs at all, and the receiving side is not older than the
/// sending one (its database must understand the other's).
pub fn check_versions(direction: Direction, name: &str, remote: &str) -> Result<(), String> {
    let here = stl_core::VERSION;
    if version_cmp(remote, MIN_VERSION) == Ordering::Less {
        return Err(format!("{name} runs {remote}, which cannot sync: update it to {MIN_VERSION} or later"));
    }
    match direction {
        Direction::Pull if version_cmp(here, remote) == Ordering::Less => {
            Err(format!("{name} runs a newer version ({remote}) than this server ({here}): update this server first"))
        }
        Direction::Push if version_cmp(remote, here) == Ordering::Less => {
            Err(format!("{name} runs an older version ({remote}) than this server ({here}): update {name} first"))
        }
        _ => Ok(()),
    }
}

// ── the receiver's thumbnails ────────────────────────────────────────────────

/// Blob keys on the wire: a length byte, then the key, for each.
pub fn encode_keys(keys: &[Vec<u8>]) -> Vec<u8> {
    let mut out = Vec::with_capacity(keys.len() * 18);
    for k in keys {
        out.push(k.len() as u8);
        out.extend_from_slice(k);
    }
    out
}

pub fn decode_keys(mut bytes: &[u8]) -> Result<HashSet<Vec<u8>>, String> {
    let mut out = HashSet::new();
    while let Some((&n, rest)) = bytes.split_first() {
        let n = n as usize;
        if rest.len() < n || !(n == 16 || n == 17) {
            return Err("not a list of thumbnail keys".into());
        }
        out.insert(rest[..n].to_vec());
        bytes = &rest[n..];
    }
    Ok(out)
}

fn have_dir(data_dir: &Path) -> PathBuf {
    data_dir.join("sync")
}

/// What a failed import of a delta says (`storage::delta::DeltaError::Diverged`):
/// the sync then sends the whole library.
pub const DIVERGED: &str = "do not fit this library";

/// What a receiving server sends for a pull: its thumbnail keys (`have`) or
/// its database's fingerprints (`blocks`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Upload {
    Have,
    Blocks,
}

impl Upload {
    fn prefix(self) -> &'static str {
        match self {
            Upload::Have => "have",
            Upload::Blocks => "blocks",
        }
    }
}

/// Keep a receiver's key set (`POST /api/sync/thumbnails`) for a `sync_export`
/// job; sets older than a day go. Returns its id.
pub fn store_have(data_dir: &Path, bytes: &[u8]) -> Result<String, String> {
    decode_keys(bytes)?;
    store_sent(data_dir, Upload::Have, bytes)
}

/// Keep a receiver's database fingerprints (`POST /api/sync/blocks`). Returns their id.
pub fn store_blocks(data_dir: &Path, bytes: &[u8]) -> Result<String, String> {
    stl_core::storage::delta::decode(bytes)?;
    store_sent(data_dir, Upload::Blocks, bytes)
}

fn store_sent(data_dir: &Path, kind: Upload, bytes: &[u8]) -> Result<String, String> {
    let dir = have_dir(data_dir);
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    if let Ok(rd) = std::fs::read_dir(&dir) {
        let day = Duration::from_secs(24 * 3600);
        for e in rd.flatten() {
            if e.metadata().ok().and_then(|m| m.modified().ok()).and_then(|t| t.elapsed().ok()).is_some_and(|age| age > day) {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
    let id = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos()).to_string();
    std::fs::write(dir.join(format!("{}-{id}.bin", kind.prefix())), bytes).map_err(|e| e.to_string())?;
    Ok(id)
}

/// What a receiver sent, stored under `id`.
pub fn sent_path(data_dir: &Path, kind: Upload, id: &str) -> Result<PathBuf, String> {
    if id.is_empty() || !id.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("{id:?} is not a list from another server"));
    }
    let path = have_dir(data_dir).join(format!("{}-{id}.bin", kind.prefix()));
    if path.is_file() { Ok(path) } else { Err(format!("no list {id} from another server (kept for a day)")) }
}

// ── the remote's API ─────────────────────────────────────────────────────────

/// Signed in at a remote: its token, role and version.
pub struct SignedIn {
    pub token: String,
    pub version: String,
}

/// A blocking client for a remote server's API.
pub struct Remote {
    name: String,
    base: String,
    token: Option<String>,
    agent: ureq::Agent,
    /// This server's `Library::instance`: the remote must not answer with it.
    own: Option<String>,
}

/// Counts what is read into `ctl` (an upload's progress).
struct Counting<'a, R> {
    inner: R,
    ctl: &'a Control,
}

impl<R: Read> Read for Counting<'_, R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.ctl.is_stopped() {
            return Err(io::Error::other("stopped"));
        }
        let n = self.inner.read(buf)?;
        self.ctl.advance(n as u64);
        Ok(n)
    }
}

type Resp = ureq::http::Response<ureq::Body>;

impl Remote {
    pub fn new(name: &str, url: &str, token: Option<&str>) -> Remote {
        let agent = ureq::Agent::config_builder()
            .http_status_as_error(false)
            .timeout_connect(Some(Duration::from_secs(30)))
            // A remote job's status, a package's headers: never minutes.
            .timeout_recv_response(Some(Duration::from_secs(300)))
            .user_agent(concat!("StlArchivist/", env!("CARGO_PKG_VERSION"), " sync"))
            .build()
            .into();
        Remote { name: name.to_string(), base: url.trim_end_matches('/').to_string(), token: token.map(str::to_string), agent, own: None }
    }

    /// Refuse a remote that turns out to be this server (`instance`): a sync
    /// with itself would wait for its own job forever.
    pub fn not_this_server(mut self, instance: &str) -> Remote {
        self.own = Some(instance.to_string());
        self
    }

    fn check_not_self(&self, resp: &Resp) -> Result<(), String> {
        let theirs = resp.headers().get(INSTANCE_HEADER).and_then(|v| v.to_str().ok());
        if theirs.is_some() && theirs == self.own.as_deref() {
            return Err(format!("{} ({}) is this server itself", self.name, self.base));
        }
        Ok(())
    }

    fn url(&self, path: &str) -> String {
        format!("{}{path}", self.base)
    }

    fn auth(&self) -> String {
        format!("Bearer {}", self.token.as_deref().unwrap_or(""))
    }

    fn unreachable(&self, e: ureq::Error) -> String {
        format!("{} ({}) cannot be reached: {e}", self.name, self.base)
    }

    /// The response when it is a success, else the remote's message.
    fn ok(&self, mut resp: Resp) -> Result<Resp, String> {
        let status = resp.status();
        if status.is_success() {
            return Ok(resp);
        }
        if status.as_u16() == 401 {
            return Err(format!("the sign-in on {} expired or was revoked: sign in to it again", self.name));
        }
        let text = resp.body_mut().read_to_string().unwrap_or_default();
        let message = serde_json::from_str::<Value>(&text).ok().and_then(|v| v["error"].as_str().map(str::to_string)).unwrap_or(text);
        Err(format!("{}: {} ({status})", self.name, message.trim()))
    }

    fn json_of(&self, mut resp: Resp) -> Result<Value, String> {
        let text = resp.body_mut().with_config().limit(256 << 20).read_to_string().map_err(|e| format!("{}: {e}", self.name))?;
        if text.trim().is_empty() {
            return Ok(Value::Null);
        }
        serde_json::from_str(&text).map_err(|e| format!("{}: unexpected answer ({e})", self.name))
    }

    pub fn get_json(&self, path: &str) -> Result<Value, String> {
        let resp = self.agent.get(self.url(path)).header("Authorization", self.auth()).call().map_err(|e| self.unreachable(e))?;
        self.json_of(self.ok(resp)?)
    }

    pub fn post_json(&self, path: &str, body: &Value) -> Result<Value, String> {
        let resp = self
            .agent
            .post(self.url(path))
            .header("Authorization", self.auth())
            .header("Content-Type", "application/json")
            .send(body.to_string())
            .map_err(|e| self.unreachable(e))?;
        self.json_of(self.ok(resp)?)
    }

    pub fn delete(&self, path: &str) -> Result<(), String> {
        let resp = self.agent.delete(self.url(path)).header("Authorization", self.auth()).call().map_err(|e| self.unreachable(e))?;
        self.ok(resp).map(|_| ())
    }

    /// Sign in as `user`; the account must be an administrator there.
    pub fn sign_in(&self, user: &str, password: &str) -> Result<SignedIn, String> {
        let resp = self
            .agent
            .post(self.url("/api/auth/login"))
            .header("Content-Type", "application/json")
            .send(json!({"name": user, "password": password}).to_string())
            .map_err(|e| self.unreachable(e))?;
        if resp.status().as_u16() == 401 {
            return Err(format!("{} refused the name or password", self.name));
        }
        let session = self.json_of(self.ok(resp)?)?;
        if session["user"]["role"] != "admin" {
            return Err(format!("{user} is not an administrator on {}", self.name));
        }
        let token = session["token"].as_str().ok_or_else(|| format!("{}: no session in the answer", self.name))?.to_string();
        let status = Remote::new(&self.name, &self.base, Some(&token)).status()?;
        Ok(SignedIn { token, version: status.0 })
    }

    /// The remote's version, once its sign-in is checked to be an administrator's.
    pub fn status(&self) -> Result<(String,), String> {
        let v = self.get_json("/api/auth/status")?;
        let version = v["version"].as_str().unwrap_or_default().to_string();
        match v["user"]["role"].as_str() {
            Some("admin") => Ok((version,)),
            Some(_) => Err(format!("the account this server uses on {} is no longer an administrator", self.name)),
            None => Err(format!("the sign-in on {} expired or was revoked: sign in to it again", self.name)),
        }
    }

    /// The remote's thumbnail blob keys (`GET /api/sync/thumbnails`).
    pub fn blob_keys(&self) -> Result<HashSet<Vec<u8>>, String> {
        let resp = self.agent.get(self.url("/api/sync/thumbnails")).header("Authorization", self.auth()).call().map_err(|e| self.unreachable(e))?;
        self.check_not_self(&resp)?;
        let bytes = self.ok(resp)?.body_mut().with_config().limit(u64::MAX).read_to_vec().map_err(|e| format!("{}: {e}", self.name))?;
        decode_keys(&bytes)
    }

    /// The remote's database fingerprints (`GET /api/sync/blocks`); `None` when
    /// it cannot take a smart sync (an older version, or a database it cannot
    /// fingerprint).
    pub fn blocks(&self) -> Result<Option<stl_core::storage::delta::State>, String> {
        let resp = self.agent.get(self.url("/api/sync/blocks")).header("Authorization", self.auth()).call().map_err(|e| self.unreachable(e))?;
        if matches!(resp.status().as_u16(), 404 | 405 | 409) {
            return Ok(None);
        }
        let bytes = self.ok(resp)?.body_mut().with_config().limit(u64::MAX).read_to_vec().map_err(|e| format!("{}: {e}", self.name))?;
        stl_core::storage::delta::decode(&bytes).map(Some)
    }

    /// Hand the remote this server's database fingerprints; their id there, or
    /// `None` when it cannot make a smart sync (an older version).
    pub fn send_blocks(&self, state: &stl_core::storage::delta::State) -> Result<Option<String>, String> {
        let resp = self
            .agent
            .post(self.url("/api/sync/blocks"))
            .header("Authorization", self.auth())
            .header("Content-Type", "application/octet-stream")
            .send(&stl_core::storage::delta::encode(state)[..])
            .map_err(|e| self.unreachable(e))?;
        if matches!(resp.status().as_u16(), 404 | 405) {
            return Ok(None);
        }
        let v = self.json_of(self.ok(resp)?)?;
        Ok(v["id"].as_str().map(str::to_string))
    }

    /// Hand the remote this server's thumbnail keys; returns their id there.
    pub fn send_keys(&self, keys: &[Vec<u8>]) -> Result<String, String> {
        let resp = self
            .agent
            .post(self.url("/api/sync/thumbnails"))
            .header("Authorization", self.auth())
            .header("Content-Type", "application/octet-stream")
            .send(&encode_keys(keys)[..])
            .map_err(|e| self.unreachable(e))?;
        self.check_not_self(&resp)?;
        let v = self.json_of(self.ok(resp)?)?;
        v["id"].as_str().map(str::to_string).ok_or_else(|| format!("{}: no id in the answer", self.name))
    }

    /// Download `path` into `dest` (removed again when it fails); progress in bytes.
    pub fn download(&self, path: &str, dest: &Path, ctl: &Control) -> Result<u64, String> {
        let got = (|| {
            let resp = self.agent.get(self.url(path)).header("Authorization", self.auth()).call().map_err(|e| self.unreachable(e))?;
            let mut resp = self.ok(resp)?;
            let len = resp.headers().get("content-length").and_then(|v| v.to_str().ok()).and_then(|v| v.parse().ok()).unwrap_or(0);
            ctl.set_total(len);
            if let Some(folder) = dest.parent() {
                std::fs::create_dir_all(folder).map_err(|e| e.to_string())?;
            }
            let mut out = std::fs::File::create(dest).map_err(|e| e.to_string())?;
            let mut reader = Counting { inner: resp.body_mut().with_config().limit(u64::MAX).reader(), ctl };
            io::copy(&mut reader, &mut out).map_err(|e| format!("downloading from {}: {e}", self.name))
        })();
        if got.is_err() {
            let _ = std::fs::remove_file(dest);
        }
        got
    }

    /// Upload the file `src` as the body of a POST to `path`; progress in bytes.
    pub fn upload(&self, path: &str, src: &Path, ctl: &Control) -> Result<Value, String> {
        let file = std::fs::File::open(src).map_err(|e| e.to_string())?;
        let len = file.metadata().map_err(|e| e.to_string())?.len();
        ctl.set_total(len);
        let mut reader = Counting { inner: file, ctl };
        let resp = self
            .agent
            .post(self.url(path))
            .header("Authorization", self.auth())
            .header("Content-Type", "application/octet-stream")
            .header("Content-Length", len.to_string())
            .send(ureq::SendBody::from_reader(&mut reader))
            .map_err(|e| self.unreachable(e))?;
        self.json_of(self.ok(resp)?)
    }

    /// Queue a job on the remote and wait for it, copying its new log lines
    /// to `log`; cancels it when `stopped` says so. Returns its detail.
    pub fn run_job(&self, request: &Value, log: &dyn Fn(String), stopped: &dyn Fn() -> bool) -> Result<Value, String> {
        let job = self.post_json("/api/jobs", request)?;
        let id = job["id"].as_u64().ok_or_else(|| format!("{}: no job id in the answer", self.name))?;
        let mut last: Option<String> = None;
        loop {
            std::thread::sleep(Duration::from_millis(1000));
            if stopped() {
                let _ = self.delete(&format!("/api/jobs/{id}"));
                return Err("stopped".into());
            }
            let detail = self.get_json(&format!("/api/jobs/{id}"))?;
            let lines: Vec<&str> = detail["log"].as_array().map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
            let start = last.as_deref().and_then(|l| lines.iter().rposition(|x| *x == l)).map_or(0, |i| i + 1);
            for line in &lines[start..] {
                log(format!("{}: {line}", self.name));
            }
            if let Some(l) = lines.last() {
                last = Some(l.to_string());
            }
            match detail["job"]["state"].as_str() {
                Some("done") => {
                    if let Some(m) = detail["job"]["message"].as_str() {
                        log(format!("{}: {m}", self.name));
                    }
                    return Ok(detail);
                }
                Some("failed" | "cancelled") => {
                    return Err(format!("{}: {}", self.name, detail["job"]["message"].as_str().unwrap_or("the job failed")));
                }
                _ => {}
            }
        }
    }
}

// ── the preview ──────────────────────────────────────────────────────────────

/// One outermost folder of the sending side, and where a sync puts it.
#[derive(Debug, Serialize, ToSchema)]
pub struct PreviewFolder {
    pub uuid: String,
    pub name: String,
    /// The library it is a folder of.
    pub library: String,
    /// Its root on the sending side.
    pub root: String,
    /// Its root on the receiving side (a rule's, else the same when that exists
    /// there); `None`: no rule maps it, and its records would show as missing.
    pub target: Option<String>,
    /// `target` is a folder on the receiving side.
    pub exists: bool,
    /// Records in it and the folders inside it.
    pub models: i64,
}

/// What a sync would do, before it runs.
#[derive(Debug, Serialize, ToSchema)]
pub struct SyncPreview {
    pub direction: Direction,
    pub local_version: String,
    pub remote_version: String,
    /// Why the sync would be refused (versions), if it would.
    pub problem: Option<String>,
    pub folders: Vec<PreviewFolder>,
}

/// A sending side's folder, as the preview needs it.
pub struct Sent {
    pub uuid: String,
    pub name: String,
    pub library: String,
    pub root: String,
    pub models: i64,
}

/// The outermost of `folders` (the others move with them), their records counted in.
pub fn outermost(folders: Vec<Sent>) -> Vec<Sent> {
    use stl_core::storage::libraries::foreign_rel;
    let inside = |outer: &Sent, f: &Sent| outer.uuid != f.uuid && foreign_rel(&outer.root, &f.root).is_some();
    let mut out: Vec<Sent> = Vec::new();
    for f in &folders {
        if folders.iter().any(|o| inside(o, f)) {
            continue;
        }
        let models = folders.iter().filter(|g| g.uuid == f.uuid || inside(f, g)).map(|g| g.models).sum();
        out.push(Sent { uuid: f.uuid.clone(), name: f.name.clone(), library: f.library.clone(), root: f.root.clone(), models });
    }
    out
}

/// The folders of a remote's `/api/libraries` answer.
pub fn remote_folders(libraries: &Value) -> Vec<Sent> {
    let mut out = Vec::new();
    for lib in libraries.as_array().into_iter().flatten() {
        for f in lib["folders"].as_array().into_iter().flatten() {
            out.push(Sent {
                uuid: f["uuid"].as_str().unwrap_or_default().to_string(),
                name: f["name"].as_str().unwrap_or_default().to_string(),
                library: lib["name"].as_str().unwrap_or_default().to_string(),
                root: f["root"].as_str().unwrap_or_default().to_string(),
                models: f["models"].as_i64().unwrap_or(0),
            });
        }
    }
    out
}

/// Where each of the sending side's outermost folders goes: by `rules`, else
/// at the same root when `exists` says that is a folder on the receiving side.
pub fn preview_folders(folders: Vec<Sent>, rules: &[RootRule], exists: impl Fn(&[String]) -> Result<Vec<bool>, String>) -> Result<Vec<PreviewFolder>, String> {
    let outer = outermost(folders);
    let mapped: Vec<Option<String>> = outer.iter().map(|f| apply_rules(rules, &f.root)).collect();
    let asked: Vec<String> = outer.iter().zip(&mapped).map(|(f, m)| m.clone().unwrap_or_else(|| f.root.clone())).collect();
    let there = exists(&asked)?;
    Ok(outer
        .into_iter()
        .zip(mapped)
        .zip(asked.into_iter().zip(there))
        .map(|((f, mapped), (asked, exists))| PreviewFolder {
            target: mapped.or_else(|| exists.then_some(asked)),
            exists,
            uuid: f.uuid,
            name: f.name,
            library: f.library,
            root: f.root,
            models: f.models,
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_compare_by_number() {
        assert_eq!(version_cmp("2026.44.10", "2026.44.9"), Ordering::Greater);
        assert_eq!(version_cmp("2026.44.5", "2026.44.5"), Ordering::Equal);
        assert_eq!(version_cmp("2027.1.0", "2026.52.3"), Ordering::Greater);
        assert!(check_versions(Direction::Push, "nas", "2026.40.0").unwrap_err().contains("cannot sync"));
        assert!(check_versions(Direction::Pull, "nas", "2999.1.0").unwrap_err().contains("update this server"));
        assert!(check_versions(Direction::Push, "nas", "2999.1.0").is_ok());
        assert!(check_versions(Direction::Pull, "nas", stl_core::VERSION).is_ok());
    }

    #[test]
    fn keys_travel_and_odd_lists_are_refused() {
        let keys = vec![vec![1; 16], vec![2; 17]];
        let back = decode_keys(&encode_keys(&keys)).unwrap();
        assert_eq!(back, keys.into_iter().collect());
        assert!(decode_keys(&[16, 1, 2]).is_err());
        assert!(decode_keys(&[3, 1, 2, 3]).is_err());
        assert!(decode_keys(&[]).unwrap().is_empty());
    }

    #[test]
    fn rules_map_both_ways() {
        let rule = PathRule { local: r"D:\Minis".into(), remote: "/library".into() };
        assert_eq!(apply_rules(&[rule.toward(Direction::Push)], r"D:\Minis\Studio").as_deref(), Some("/library/Studio"));
        assert_eq!(apply_rules(&[rule.toward(Direction::Pull)], "/library/Studio").as_deref(), Some(r"D:\Minis\Studio"));
        let deeper = RootRule { from: "/library/Studio".into(), to: r"E:\Studio".into() };
        assert_eq!(apply_rules(&[rule.toward(Direction::Pull), deeper], "/library/Studio/x").as_deref(), Some(r"E:\Studio\x"));
        assert_eq!(apply_rules(&[], "/library"), None);
    }

    #[test]
    fn previews_map_the_outermost_folders() {
        let f = |uuid: &str, root: &str, models| Sent { uuid: uuid.into(), name: uuid.into(), library: "L".into(), root: root.into(), models };
        let folders = vec![f("a", "/library", 2), f("b", "/library/Inner", 3), f("c", "/elsewhere", 1)];
        let rules = [RootRule { from: "/library".into(), to: r"D:\Minis".into() }];
        let p = preview_folders(folders, &rules, |asked| Ok(asked.iter().map(|a| a == r"D:\Minis").collect())).unwrap();
        assert_eq!(p.len(), 2);
        assert_eq!((p[0].target.as_deref(), p[0].exists, p[0].models), (Some(r"D:\Minis"), true, 5));
        assert_eq!((p[1].target.as_deref(), p[1].exists), (None, false), "unmapped and not there");
    }

    #[test]
    fn remotes_are_stored_without_showing_their_token() {
        let conn = Connection::open_in_memory().unwrap();
        ensure_schema(&conn).unwrap();
        let rules = [PathRule { local: "D:\\M".into(), remote: "/m".into() }];
        let id = insert(&conn, "nas", "http://nas:8770", "boss", "secret", &rules).unwrap();
        update(&conn, id, None, None, Some(false)).unwrap();
        record(&conn, id, Direction::Push, "pushed").unwrap();
        let s = get(&conn, id).unwrap().unwrap();
        assert_eq!((s.token.as_str(), s.rules.len(), s.backup_first), ("secret", 1, false));
        let shown = serde_json::to_string(&RemoteInfo::from(s)).unwrap();
        assert!(!shown.contains("secret"), "{shown}");
        assert_eq!(clean_url(" https://nas:8770/ ").unwrap(), "https://nas:8770");
        assert!(clean_url("nas:8770").is_err());
    }
}
