//! The library lock (compatibility rule 7): one process at a time opens a
//! library's data directory. The server and the desktop app both take it.
//!
//! `library.lock` holds an exclusive OS file lock for the owner's lifetime; the
//! OS releases it when the process ends, so a crash never leaves the library
//! locked. Windows locks are mandatory (a locked file cannot be read), so the
//! owner's description goes into a second file, `library.owner`, for the
//! message the next process shows.

use std::fs::{File, OpenOptions, TryLockError};
use std::io::Write;
use std::path::{Path, PathBuf};

pub const LOCK_FILE: &str = "library.lock";
pub const OWNER_FILE: &str = "library.owner";

#[derive(Debug)]
pub enum LockError {
    /// Another process holds the lock; its description (may be empty).
    Held(String),
    Io(std::io::Error),
}

impl std::fmt::Display for LockError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            LockError::Held(owner) if owner.is_empty() => write!(f, "the library is open in another process"),
            LockError::Held(owner) => write!(f, "the library is open in another process: {owner}"),
            LockError::Io(e) => write!(f, "cannot lock the library: {e}"),
        }
    }
}

impl std::error::Error for LockError {}

/// Held for as long as the value lives.
#[derive(Debug)]
pub struct LibraryLock {
    _file: File,
    dir: PathBuf,
}

impl LibraryLock {
    /// Lock `data_dir` for this process, describing it as `owner` (for example
    /// `"stl-server 0.1.0, pid 1234"`).
    pub fn acquire(data_dir: &Path, owner: &str) -> Result<Self, LockError> {
        std::fs::create_dir_all(data_dir).map_err(LockError::Io)?;
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(data_dir.join(LOCK_FILE))
            .map_err(LockError::Io)?;
        match file.try_lock() {
            Ok(()) => {}
            Err(TryLockError::WouldBlock) => return Err(LockError::Held(Self::owner(data_dir).unwrap_or_default())),
            Err(TryLockError::Error(e)) => return Err(LockError::Io(e)),
        }
        // Best effort: the description only improves the other side's message.
        if let Ok(mut f) = File::create(data_dir.join(OWNER_FILE)) {
            let _ = f.write_all(owner.as_bytes());
        }
        Ok(LibraryLock { _file: file, dir: data_dir.to_path_buf() })
    }

    /// The description the current (or last) owner left, if any.
    pub fn owner(data_dir: &Path) -> Option<String> {
        std::fs::read_to_string(data_dir.join(OWNER_FILE)).ok().map(|s| s.trim().to_string())
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }
}

impl Drop for LibraryLock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(self.dir.join(OWNER_FILE));
        // The file lock goes with the handle.
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn second_owner_is_refused_until_release() {
        let dir = tempfile::tempdir().unwrap();
        let first = LibraryLock::acquire(dir.path(), "first owner").unwrap();
        match LibraryLock::acquire(dir.path(), "second") {
            Err(LockError::Held(owner)) => assert_eq!(owner, "first owner"),
            other => panic!("expected Held, got {other:?}"),
        }
        drop(first);
        let again = LibraryLock::acquire(dir.path(), "second").unwrap();
        assert_eq!(LibraryLock::owner(again.dir()).as_deref(), Some("second"));
    }
}
