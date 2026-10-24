//! Archive catalogs: member names, CRCs and sizes, read without decompressing.
//!
//! The member list feeds the archive fingerprint (`archive_hash`), so it must
//! match what the Python readers produce **exactly** (compatibility rule 1 in
//! `docs/rust-migration-plan.md`):
//!
//! * ZIP: a port of CPython 3.14's `zipfile` catalog reader (`zip.rs`).
//! * 7z: `sevenz-rust2`, with `py7zr`'s name rule (`\` becomes `/`).
//! * RAR: not handled here yet; the Python `rarfile` reader stays in use.
//!
//! Whenever a catalog looks unusual, these readers return an error instead of
//! guessing, and the caller falls back to the Python reader. They must also
//! fail wherever the Python reader fails, so the two never disagree about an
//! archive that only one of them can read.

mod rar;
mod sevenz;
mod zip;

use std::fmt;
use std::path::Path;

use crate::fingerprint::{self, Member};

/// One catalog entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArchiveMember {
    pub name: String,
    pub crc: u32,
    /// Uncompressed size; `None` when the catalog does not record it.
    pub size: Option<u64>,
}

#[derive(Debug)]
pub enum ArchiveError {
    Io(std::io::Error),
    /// Corrupt, truncated or not an archive of the expected type.
    Format(String),
    /// A valid archive using something this reader does not handle (yet).
    Unsupported(String),
}

impl fmt::Display for ArchiveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ArchiveError::Io(e) => write!(f, "I/O error: {e}"),
            ArchiveError::Format(m) => write!(f, "bad archive: {m}"),
            ArchiveError::Unsupported(m) => write!(f, "unsupported: {m}"),
        }
    }
}

impl std::error::Error for ArchiveError {}

impl From<std::io::Error> for ArchiveError {
    fn from(e: std::io::Error) -> Self {
        ArchiveError::Io(e)
    }
}

pub(crate) fn format_err(msg: impl Into<String>) -> ArchiveError {
    ArchiveError::Format(msg.into())
}

fn extension(path: &Path) -> String {
    path.extension()
        .and_then(|e| e.to_str())
        .map(str::to_ascii_lowercase)
        .unwrap_or_default()
}

/// Catalog of the archive at `path`, chosen by extension (`.zip`, `.7z`).
pub fn list_members(path: &Path) -> Result<Vec<ArchiveMember>, ArchiveError> {
    match extension(path).as_str() {
        "zip" => zip::list_members(path),
        "7z" => sevenz::list_members(path),
        "rar" => rar::list_members(path),
        other => Err(ArchiveError::Unsupported(format!("archive type .{other}"))),
    }
}

/// Decompressed bytes of the named members, as `(name, data)`.
///
/// Names that are missing, empty, or that the Python reader would skip
/// (bad CRC and the like, per member for ZIP) are left out; anything this
/// reader does not handle exactly fails the whole call instead.
pub fn read_members(path: &Path, names: &[String]) -> Result<Vec<(String, Vec<u8>)>, ArchiveError> {
    read_members_with(path, names, "")
}

/// [`read_members`] with the UnRAR tool `.rar` members need (`""`: none, so
/// compressed RAR members are unsupported).
pub fn read_members_with(path: &Path, names: &[String], unrar: &str) -> Result<Vec<(String, Vec<u8>)>, ArchiveError> {
    match extension(path).as_str() {
        "zip" => zip::read_members(path, names),
        "7z" => sevenz::read_members(path, names),
        "rar" => rar::read_members(path, names, unrar),
        other => Err(ArchiveError::Unsupported(format!("archive type .{other}"))),
    }
}

/// Archive fingerprint (`archive_hash`) computed from the catalog.
pub fn fingerprint(path: &Path) -> Result<String, ArchiveError> {
    Ok(fingerprint_of(&list_members(path)?))
}

/// Archive fingerprint (`archive_hash`) of a catalog [`list_members`] read.
pub fn fingerprint_of(members: &[ArchiveMember]) -> String {
    let mut members: Vec<Member> = members.iter().map(|m| Member { name: m.name.clone(), crc: m.crc }).collect();
    fingerprint::archive_fingerprint(&mut members)
}
