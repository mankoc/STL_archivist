//! Library backups: the desktop's Backup / Restore (`backup_restore_dialog.py`)
//! layout, one `.7z` holding `models.db`, `thumbnails.lmdb/` and
//! `settings.json` at its root, so either side restores the other's backups.
//!
//! Writing takes consistent snapshots of the open library (`VACUUM INTO` for the
//! database, a compacting LMDB copy for the thumbnails), so it works while the
//! library is in use. Restoring replaces those files and must run with the
//! library closed (the caller holds the library lock).

use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{self, BufReader};
use std::path::{Path, PathBuf};

use rusqlite::Connection;
use sevenz_rust2::{ArchiveEntry, ArchiveWriter, EncoderMethod};

use crate::control::Control;
use crate::thumbstore::ThumbStore;

/// The model database in the data directory (and in backups). Before 2026.43.10 it was
/// `stlLibrary.db`; older data directories are not read (their libraries are recreated).
pub const DB_FILE: &str = "models.db";
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
    write_with(conn, settings, dest, ctl, |path| store.copy_compacted(path).map_err(|e| BackupError::Store(e.to_string())))
}

/// A sync's package (`.7z`, the backup layout): like [`write`], but its
/// thumbnail store holds every image's reference and only the blobs not in
/// `have` (the receiver has those; the import completes the store from its
/// own). Not a backup to keep. Returns its size and `(blobs sent, blobs skipped)`.
pub fn write_sync(
    conn: &Connection,
    store: &ThumbStore,
    settings: Option<&Path>,
    dest: &Path,
    have: &HashSet<Vec<u8>>,
    ctl: &Control,
) -> Result<(u64, (u64, u64))> {
    let mut counts = (0, 0);
    let size = write_with(conn, settings, dest, ctl, |path| {
        let dir = Scratch(path.with_extension("store"));
        let _ = fs::remove_dir_all(&dir.0);
        let partial = ThumbStore::open(&dir.0).map_err(|e| BackupError::Store(e.to_string()))?;
        let copied = store.copy_partial(&partial, have, ctl);
        partial.close();
        counts = copied.map_err(|e| if ctl.is_stopped() { BackupError::Stopped } else { BackupError::Store(e.to_string()) })?;
        fs::rename(dir.0.join(LMDB_DATA), path)?;
        Ok(())
    })?;
    Ok((size, counts))
}

/// [`write`] with the thumbnail store's file written by `thumbnails` (to the path given).
fn write_with(conn: &Connection, settings: Option<&Path>, dest: &Path, ctl: &Control, thumbnails: impl FnOnce(&Path) -> Result<()>) -> Result<u64> {
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
    thumbnails(&lmdb.0)?;
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

/// Whether `src` is a library backup: a `.7z` with a database inside (its
/// header is read, nothing is unpacked).
pub fn check_package(src: &Path) -> Result<()> {
    let archive = sevenz_rust2::Archive::open(src)?;
    if archive.files.iter().any(|f| f.name() == DB_FILE) {
        Ok(())
    } else {
        Err(BackupError::Invalid(format!("it holds no {DB_FILE}")))
    }
}

/// What [`extract`] takes from a backup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Parts {
    /// The database, the settings and the thumbnails.
    All,
    /// The database and the settings.
    Database,
    /// The thumbnail store.
    Thumbnails,
}

impl Parts {
    fn takes(self, name: &str) -> bool {
        let thumbnail = name.replace('\\', "/").starts_with(&format!("{THUMB_DIR}/"));
        wanted(name)
            && match self {
                Parts::All => true,
                Parts::Database => !thumbnail,
                Parts::Thumbnails => thumbnail,
            }
    }
}

/// Extract `parts` of a backup (`.7z`) into the folder `dir`, created when
/// needed. A database taken out must open and hold a `models` table.
pub fn extract(src: &Path, dir: &Path, parts: Parts) -> Result<()> {
    fs::create_dir_all(dir)?;
    sevenz_rust2::decompress_file_with_extract_fn(src, dir, |entry, reader, dest| {
        if parts.takes(entry.name()) {
            sevenz_rust2::default_entry_extract_fn(entry, reader, dest)
        } else {
            io::copy(reader, &mut io::sink()).map_err(sevenz_rust2::Error::from)?;
            Ok(true)
        }
    })?;
    if parts != Parts::Thumbnails {
        let db = dir.join(DB_FILE);
        if !db.is_file() {
            return Err(BackupError::Invalid(format!("{} holds no {DB_FILE}", src.display())));
        }
        check_database(&db)?;
    }
    Ok(())
}

/// Pages [`replace_live`] copies per step.
const PAGES_PER_STEP: std::ffi::c_int = 1024;

/// Replace the content of the open database `live` with the database file
/// `staged` (SQLite's online backup), for every connection at once: the others
/// see the old library until the copy is complete, then the new one; stopping
/// rolls it back. Both must use the same page size. Progress counts pages.
pub fn replace_live(live: &mut Connection, staged: &Path, ctl: &Control) -> Result<()> {
    let src = Connection::open_with_flags(staged, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let page_size = |c: &Connection| c.query_row("PRAGMA page_size", [], |r| r.get::<_, i64>(0));
    let (from, to) = (page_size(&src)?, page_size(live)?);
    if from != to {
        return Err(BackupError::Invalid(format!("its database pages are {from} bytes, this library's {to}")));
    }
    {
        let backup = rusqlite::backup::Backup::new(&src, live)?;
        let mut copied = 0;
        loop {
            let step = backup.step(PAGES_PER_STEP)?;
            let p = backup.progress();
            if copied == 0 {
                ctl.set_total(p.pagecount.max(0) as u64);
            }
            let now = (p.pagecount - p.remaining).max(0) as u64;
            ctl.advance(now.saturating_sub(copied));
            copied = copied.max(now);
            match step {
                rusqlite::backup::StepResult::Done => break,
                rusqlite::backup::StepResult::More => {}
                // Busy or locked: another connection is writing; try again.
                _ => std::thread::sleep(std::time::Duration::from_millis(50)),
            }
            if ctl.is_stopped() {
                return Err(BackupError::Stopped);
            }
        }
    }
    // The whole database went through the write-ahead log.
    live.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |_| Ok(()))?;
    Ok(())
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
    extract(src, &staging.0, Parts::All)?;

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
        assert!(wanted("models.db"));
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
    fn parts_extract_separately() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("live.db")).unwrap();
        conn.execute_batch("CREATE TABLE models (id INTEGER PRIMARY KEY)").unwrap();
        let store = ThumbStore::open(&dir.path().join("thumbs")).unwrap();
        store.put_many(&[(1, b"jpeg".as_slice())]).unwrap();
        let src = dir.path().join("b.7z");
        write(&conn, &store, None, &src, &Control::new()).unwrap();

        check_package(&src).unwrap();
        let staged = dir.path().join("staged");
        extract(&src, &staged, Parts::Database).unwrap();
        assert!(staged.join(DB_FILE).is_file() && !staged.join(THUMB_DIR).exists());
        extract(&src, &staged, Parts::Thumbnails).unwrap();
        assert!(staged.join(THUMB_DIR).join(LMDB_DATA).is_file());
    }

    #[test]
    fn a_sync_package_sends_only_missing_thumbnails() {
        let dir = tempfile::tempdir().unwrap();
        let conn = Connection::open(dir.path().join("live.db")).unwrap();
        conn.execute_batch("CREATE TABLE models (id INTEGER PRIMARY KEY)").unwrap();
        let store = ThumbStore::open(&dir.path().join("thumbs")).unwrap();
        store.put_many(&[(1, b"known".as_slice()), (2, b"new".as_slice())]).unwrap();
        let receiver = ThumbStore::open(&dir.path().join("receiver")).unwrap();
        receiver.put_many(&[(7, b"known".as_slice())]).unwrap();
        let have: HashSet<Vec<u8>> = receiver.blob_keys().unwrap().into_iter().collect();

        let src = dir.path().join("sync.7z");
        let (size, counts) = write_sync(&conn, &store, None, &src, &have, &Control::new()).unwrap();
        assert!(size > 0);
        assert_eq!(counts, (1, 1), "one blob sent, one skipped");
        let leftovers: Vec<_> = fs::read_dir(dir.path()).unwrap().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        assert!(!leftovers.iter().any(|n| n.ends_with(".tmp") || n.ends_with(".store")), "{leftovers:?}");

        let staged = dir.path().join("staged");
        extract(&src, &staged, Parts::All).unwrap();
        let partial = ThumbStore::open(&staged.join(THUMB_DIR)).unwrap();
        assert_eq!(partial.fill_missing_blobs(&receiver).unwrap(), (1, 0));
        assert_eq!(partial.get_many(&[1, 2]).unwrap(), vec![(1, b"known".to_vec()), (2, b"new".to_vec())]);
    }

    #[test]
    fn a_staged_database_replaces_the_open_one() {
        let dir = tempfile::tempdir().unwrap();
        let live_path = dir.path().join("live.db");
        let mut live = Connection::open(&live_path).unwrap();
        live.execute_batch("PRAGMA journal_mode = WAL; CREATE TABLE models (title TEXT); INSERT INTO models VALUES ('old');").unwrap();
        let other = Connection::open(&live_path).unwrap();
        let staged = dir.path().join("staged.db");
        Connection::open(&staged)
            .unwrap()
            .execute_batch("CREATE TABLE models (title TEXT); CREATE TABLE extra (x); INSERT INTO models VALUES ('new'), ('newer');")
            .unwrap();

        let ctl = Control::new();
        replace_live(&mut live, &staged, &ctl).unwrap();
        let titles: Vec<String> =
            other.prepare("SELECT title FROM models ORDER BY title").unwrap().query_map([], |r| r.get(0)).unwrap().map(|r| r.unwrap()).collect();
        assert_eq!(titles, ["new", "newer"], "another connection sees the new content");
        let (done, total) = ctl.progress();
        assert!(total > 0 && done == total);

        let odd = dir.path().join("odd.db");
        Connection::open(&odd).unwrap().execute_batch("PRAGMA page_size = 8192; CREATE TABLE models (title TEXT); VACUUM;").unwrap();
        assert!(matches!(replace_live(&mut live, &odd, &ctl), Err(BackupError::Invalid(_))));
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
        assert!(matches!(check_package(&src), Err(BackupError::Invalid(_))));
        assert!(check_package(&other).is_err(), "not a .7z at all");
    }
}
