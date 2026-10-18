//! Library backups: the desktop's Backup / Restore (`backup_restore_dialog.py`)
//! layout, one `.7z` holding `stlLibrary.db`, `thumbnails.lmdb/` and
//! `settings.json` at its root, so either side restores the other's backups.
//!
//! Writing takes consistent snapshots of the open library (`VACUUM INTO` for the
//! database, a compacting LMDB copy for the thumbnails), so it works while the
//! library is in use. Restoring replaces those files and must run with the
//! library closed (the caller holds the library lock).

use std::fs::{self, File};
use std::io::{self, BufReader};
use std::path::{Path, PathBuf};

use rusqlite::Connection;
use sevenz_rust2::{ArchiveEntry, ArchiveWriter, EncoderMethod};

use crate::control::Control;
use crate::thumbstore::ThumbStore;

pub const DB_FILE: &str = "stlLibrary.db";
pub const THUMB_DIR: &str = "thumbnails.lmdb";
pub const SETTINGS_FILE: &str = "settings.json";
const LMDB_DATA: &str = "data.mdb";

#[derive(Debug)]
pub enum BackupError {
    Io(io::Error),
    Db(rusqlite::Error),
    Store(String),
    Archive(String),
    /// Not a library backup (no database inside).
    Invalid(String),
    Stopped,
}

impl std::fmt::Display for BackupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            BackupError::Io(e) => write!(f, "I/O error: {e}"),
            BackupError::Db(e) => write!(f, "database: {e}"),
            BackupError::Store(e) => write!(f, "thumbnail store: {e}"),
            BackupError::Archive(e) => write!(f, "archive: {e}"),
            BackupError::Invalid(e) => write!(f, "not a library backup: {e}"),
            BackupError::Stopped => write!(f, "stopped"),
        }
    }
}

impl std::error::Error for BackupError {}

impl From<io::Error> for BackupError {
    fn from(e: io::Error) -> Self {
        BackupError::Io(e)
    }
}

impl From<rusqlite::Error> for BackupError {
    fn from(e: rusqlite::Error) -> Self {
        BackupError::Db(e)
    }
}

impl From<sevenz_rust2::Error> for BackupError {
    fn from(e: sevenz_rust2::Error) -> Self {
        BackupError::Archive(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, BackupError>;

/// Removes its path (file or folder) when dropped: temporary snapshots.
struct Scratch(PathBuf);

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = if self.0.is_dir() { fs::remove_dir_all(&self.0) } else { fs::remove_file(&self.0) };
    }
}

fn push_file(w: &mut ArchiveWriter<File>, name: &str, path: &Path) -> Result<()> {
    let entry = ArchiveEntry::from_path(path, name.to_string());
    w.push_archive_entry(entry, Some(BufReader::new(File::open(path)?)))?;
    Ok(())
}

/// Write a backup of the open library to `dest` (`.7z`). `settings` is the
/// library's `settings.json`, when there is one. Progress counts the three
/// steps (database, thumbnails, archive). Returns the backup's size in bytes.
pub fn write(conn: &Connection, store: &ThumbStore, settings: Option<&Path>, dest: &Path, ctl: &Control) -> Result<u64> {
    let dir = dest.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
    fs::create_dir_all(dir)?;
    let stem = dest.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "backup".into());
    ctl.set_total(3);

    let db = Scratch(dir.join(format!(".{stem}.db.tmp")));
    let _ = fs::remove_file(&db.0);
    conn.execute("VACUUM INTO ?1", [db.0.to_string_lossy()])?;
    ctl.advance(1);
    if ctl.is_stopped() {
        return Err(BackupError::Stopped);
    }

    let lmdb = Scratch(dir.join(format!(".{stem}.lmdb.tmp")));
    let _ = fs::remove_file(&lmdb.0);
    store.copy_compacted(&lmdb.0).map_err(|e| BackupError::Store(e.to_string()))?;
    ctl.advance(1);
    if ctl.is_stopped() {
        return Err(BackupError::Stopped);
    }

    let tmp = Scratch(dir.join(format!(".{stem}.tmp")));
    {
        let mut w = ArchiveWriter::create(&tmp.0)?;
        push_file(&mut w, DB_FILE, &db.0)?;
        if let Some(s) = settings.filter(|s| s.is_file()) {
            push_file(&mut w, SETTINGS_FILE, s)?;
        }
        // Thumbnails are JPEGs: compressing them again only costs time.
        w.set_content_methods(vec![EncoderMethod::COPY.into()]);
        push_file(&mut w, &format!("{THUMB_DIR}/{LMDB_DATA}"), &lmdb.0)?;
        w.finish()?;
    }
    fs::rename(&tmp.0, dest)?;
    std::mem::forget(tmp);
    ctl.advance(1);
    Ok(fs::metadata(dest)?.len())
}

/// The entries a restore takes from a backup; anything else is ignored.
fn wanted(name: &str) -> bool {
    let name = name.replace('\\', "/");
    if name.starts_with('/') || name.contains(':') || name.split('/').any(|c| c == "..") {
        return false;
    }
    name == DB_FILE || name == SETTINGS_FILE || name.starts_with(&format!("{THUMB_DIR}/"))
}

/// What a restore did.
#[derive(Debug)]
pub struct Restored {
    /// Where the replaced files were moved (`<data_dir>/pre-restore-<stamp>`);
    /// `None` when there was nothing to replace.
    pub previous: Option<PathBuf>,
    pub settings: bool,
    pub thumbnails: bool,
}

/// Restore a backup (`.7z`, from this module or the desktop app) into
/// `data_dir`. The library must be closed. The database, thumbnails and
/// settings it replaces are moved to `pre-restore-<stamp>`, not deleted;
/// items the backup lacks are left as they are. `stamp` names that folder.
pub fn restore(src: &Path, data_dir: &Path, stamp: &str) -> Result<Restored> {
    let staging = Scratch(data_dir.join(format!(".restore-{stamp}")));
    let _ = fs::remove_dir_all(&staging.0);
    fs::create_dir_all(&staging.0)?;
    sevenz_rust2::decompress_file_with_extract_fn(src, &staging.0, |entry, reader, dest| {
        if wanted(entry.name()) {
            sevenz_rust2::default_entry_extract_fn(entry, reader, dest)
        } else {
            io::copy(reader, &mut io::sink()).map_err(sevenz_rust2::Error::from)?;
            Ok(true)
        }
    })?;
    let new_db = staging.0.join(DB_FILE);
    if !new_db.is_file() {
        return Err(BackupError::Invalid(format!("{} holds no {DB_FILE}", src.display())));
    }
    check_database(&new_db)?;

    let previous = data_dir.join(format!("pre-restore-{stamp}"));
    fs::create_dir_all(&previous)?;
    let replace = |name: &str| -> Result<bool> {
        let new = staging.0.join(name);
        if !new.exists() {
            return Ok(false);
        }
        let old = data_dir.join(name);
        if old.exists() {
            fs::rename(&old, previous.join(name))?;
        }
        fs::rename(&new, &old)?;
        Ok(true)
    };
    // The write-ahead log and shared memory belong to the replaced database.
    for suffix in ["-wal", "-shm"] {
        let side = data_dir.join(format!("{DB_FILE}{suffix}"));
        if side.exists() {
            fs::rename(&side, previous.join(format!("{DB_FILE}{suffix}")))?;
        }
    }
    replace(DB_FILE)?;
    let thumbnails = replace(THUMB_DIR)?;
    let settings = replace(SETTINGS_FILE)?;
    let kept = fs::read_dir(&previous)?.next().is_some();
    if !kept {
        fs::remove_dir(&previous)?;
    }
    Ok(Restored { previous: kept.then_some(previous), settings, thumbnails })
}

/// A restored database must at least open and hold a `models` table.
fn check_database(path: &Path) -> Result<()> {
    let conn = Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let ok: i64 = conn.query_row("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'models'", [], |r| r.get(0))?;
    if ok == 0 {
        return Err(BackupError::Invalid("the database has no models table".into()));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_library_entries_are_restored() {
        assert!(wanted("stlLibrary.db"));
        assert!(wanted("settings.json"));
        assert!(wanted("thumbnails.lmdb/data.mdb"));
        assert!(wanted("thumbnails.lmdb\\lock.mdb"));
        assert!(!wanted("thumbnails.lmdb/../../evil"));
        assert!(!wanted("server.db"));
        assert!(!wanted("C:/Windows/x"));
        assert!(!wanted("/etc/passwd"));
    }

    #[test]
    fn backup_and_restore_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let data = dir.path().join("data");
        fs::create_dir_all(&data).unwrap();
        let db_path = data.join(DB_FILE);
        {
            let conn = Connection::open(&db_path).unwrap();
            conn.execute_batch("CREATE TABLE models (id INTEGER PRIMARY KEY, title TEXT); INSERT INTO models (title) VALUES ('Dragon');")
                .unwrap();
        }
        fs::write(data.join(SETTINGS_FILE), r#"{"max_workers": 3}"#).unwrap();
        let store = ThumbStore::open(&data.join(THUMB_DIR)).unwrap();
        store.put_many(&[(7, b"jpeg bytes".as_slice())]).unwrap();

        let dest = dir.path().join("backups").join("b.7z");
        let conn = Connection::open(&db_path).unwrap();
        let size = write(&conn, &store, Some(&data.join(SETTINGS_FILE)), &dest, &Control::new()).unwrap();
        assert!(size > 0 && dest.is_file());
        let leftovers: Vec<_> = fs::read_dir(dest.parent().unwrap()).unwrap().flatten().map(|e| e.file_name()).collect();
        assert_eq!(leftovers.len(), 1, "temporary snapshots are removed: {leftovers:?}");

        // Change the library, then restore the backup over it.
        conn.execute("UPDATE models SET title = 'Changed'", []).unwrap();
        drop(conn);
        store.close();
        fs::write(data.join(SETTINGS_FILE), "{}").unwrap();
        let r = restore(&dest, &data, "1").unwrap();
        assert!(r.settings && r.thumbnails);
        let conn = Connection::open(&db_path).unwrap();
        let title: String = conn.query_row("SELECT title FROM models", [], |r| r.get(0)).unwrap();
        assert_eq!(title, "Dragon");
        assert_eq!(fs::read_to_string(data.join(SETTINGS_FILE)).unwrap(), r#"{"max_workers": 3}"#);
        let store = ThumbStore::open(&data.join(THUMB_DIR)).unwrap();
        assert_eq!(store.get_many(&[7]).unwrap(), vec![(7, b"jpeg bytes".to_vec())]);
        assert!(r.previous.unwrap().join(DB_FILE).is_file(), "the replaced database is kept");
        assert!(!data.join(".restore-1").exists());
    }

    #[test]
    fn a_backup_without_a_database_is_refused() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("x.7z");
        let other = dir.path().join("readme.txt");
        fs::write(&other, "hello").unwrap();
        let mut w = ArchiveWriter::create(&src).unwrap();
        push_file(&mut w, "readme.txt", &other).unwrap();
        w.finish().unwrap();
        let data = dir.path().join("data");
        fs::create_dir_all(&data).unwrap();
        assert!(matches!(restore(&src, &data, "2"), Err(BackupError::Invalid(_))));
    }
}
