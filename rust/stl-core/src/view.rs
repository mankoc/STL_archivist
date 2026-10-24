//! The model viewer's side of an archive: which members MeshPeek can show
//! (`meshpeek_core`, vendored from MeshPeek), and some of them loaded as scenes
//! for the web client's viewer (`meshpeek_core::wire`), several in one pass
//! over the archive and sent as one bundle.

use std::collections::HashMap;
use std::fmt;
use std::path::Path;

use meshpeek_core::{LoadError, LoadOptions, SUPPORTED_EXTENSIONS};

use crate::archive::{self, ArchiveError, ArchiveMember};

/// The largest member the viewer loads (its uncompressed size): a loaded
/// model takes a few times its size in memory.
pub const MAX_MEMBER_BYTES: u64 = 1 << 30;
/// The most member bytes one [`load_members`] call reads (they are all in
/// memory at once).
pub const MAX_BATCH_BYTES: u64 = 1 << 30;
/// The most members one [`load_members`] call loads.
pub const MAX_BATCH_FILES: usize = 16;

/// The member is a model file the viewer can show (by extension).
pub fn is_viewable(name: &str) -> bool {
    let ext = Path::new(name).extension().and_then(|e| e.to_str()).map(str::to_ascii_lowercase).unwrap_or_default();
    SUPPORTED_EXTENSIONS.contains(&ext.as_str())
}

/// The viewable members of the archive at `path`, without regard to case by
/// name (MeshPeek's order).
pub fn viewable_members(path: &Path) -> Result<Vec<ArchiveMember>, ArchiveError> {
    let mut members: Vec<ArchiveMember> = archive::list_members(path)?.into_iter().filter(|m| is_viewable(&m.name)).collect();
    members.sort_by_key(|m| m.name.to_lowercase());
    Ok(members)
}

#[derive(Debug)]
pub enum ViewError {
    /// The archive could not be read.
    Archive(ArchiveError),
    /// No such member (or one the archive readers skip).
    Missing(String),
    /// Not a model file the viewer shows.
    NotViewable(String),
    /// The member is larger than [`MAX_MEMBER_BYTES`].
    TooLarge { name: String, size: u64 },
    /// The members asked for together are more than [`MAX_BATCH_BYTES`] or
    /// [`MAX_BATCH_FILES`].
    BatchTooLarge { files: usize, size: u64 },
    /// The member is not a valid model file.
    Model(LoadError),
}

fn mb(bytes: u64) -> String {
    format!("{:.0} MB", bytes as f64 / (1u64 << 20) as f64)
}

impl fmt::Display for ViewError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ViewError::Archive(e) => write!(f, "{e}"),
            ViewError::Missing(name) => write!(f, "{name} is not in the archive"),
            ViewError::NotViewable(name) => write!(f, "{name} is not a model file the viewer shows"),
            ViewError::TooLarge { name, size } => {
                write!(f, "{name} is {}, more than the viewer loads ({})", mb(*size), mb(MAX_MEMBER_BYTES))
            }
            ViewError::BatchTooLarge { files, size } => write!(
                f,
                "{files} files of {} at once are more than the viewer loads (at most {MAX_BATCH_FILES} files, {})",
                mb(*size),
                mb(MAX_BATCH_BYTES)
            ),
            ViewError::Model(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for ViewError {}

/// Each member asked for, with its encoded scene or why it has none.
pub type LoadedMembers = Vec<(String, Result<Vec<u8>, ViewError>)>;

/// The archive members `names` loaded (MeshPeek's default options) and
/// encoded for the viewer, in the order asked, each a scene or why it is not
/// one. The archive's catalog is read once and the members are extracted in
/// one pass (a solid 7z block is decompressed once). Fails as a whole when the
/// archive cannot be read or the batch is too large. `unrar` is the UnRAR tool
/// compressed `.rar` members need.
pub fn load_members(path: &Path, names: &[String], unrar: &str) -> Result<LoadedMembers, ViewError> {
    let catalog: HashMap<String, Option<u64>> = archive::list_members(path).map_err(ViewError::Archive)?.into_iter().map(|m| (m.name, m.size)).collect();
    let checked: Vec<(String, Result<(), ViewError>)> = names
        .iter()
        .map(|name| {
            let check = if !is_viewable(name) {
                Err(ViewError::NotViewable(name.clone()))
            } else {
                match catalog.get(name) {
                    None => Err(ViewError::Missing(name.clone())),
                    Some(Some(size)) if *size > MAX_MEMBER_BYTES => Err(ViewError::TooLarge { name: name.clone(), size: *size }),
                    Some(_) => Ok(()),
                }
            };
            (name.clone(), check)
        })
        .collect();
    let wanted: Vec<String> = checked.iter().filter(|(_, c)| c.is_ok()).map(|(n, _)| n.clone()).collect();
    let size: u64 = wanted.iter().filter_map(|n| catalog.get(n).copied().flatten()).sum();
    if wanted.len() > MAX_BATCH_FILES || size > MAX_BATCH_BYTES {
        return Err(ViewError::BatchTooLarge { files: wanted.len(), size });
    }
    let mut data: HashMap<String, Vec<u8>> = if wanted.is_empty() {
        HashMap::new()
    } else {
        archive::read_members_with(path, &wanted, unrar).map_err(ViewError::Archive)?.into_iter().collect()
    };
    Ok(checked
        .into_iter()
        .map(|(name, check)| {
            let scene = check.and_then(|()| {
                // Taken out of the map, so each member's bytes are freed once it is loaded.
                let bytes = data.remove(&name).ok_or_else(|| ViewError::Missing(name.clone()))?;
                let scene = meshpeek_core::io::load_data(Path::new(&name), meshpeek_core::io::Data::Owned(bytes), &LoadOptions::default())
                    .map_err(ViewError::Model)?;
                Ok(meshpeek_core::wire::encode(&scene))
            });
            (name, scene)
        })
        .collect())
}

const BUNDLE_MAGIC: &[u8; 4] = b"MPKB";

/// A file of a bundle: its name, and its scene or the message why it has none.
pub type BundleFile = (String, Result<Vec<u8>, String>);

/// Scenes (or why a file has none) as one response body, little-endian:
///
/// ```text
/// "MPKB" u32:files { u32:len, utf-8 name, u8:ok (1: a scene, 0: an error message), u32:len, bytes }
/// ```
pub fn encode_bundle(files: &[BundleFile]) -> Vec<u8> {
    let size: usize = files.iter().map(|(n, r)| 13 + n.len() + r.as_ref().map_or_else(String::len, Vec::len)).sum();
    let mut out = Vec::with_capacity(size + 8);
    out.extend_from_slice(BUNDLE_MAGIC);
    out.extend_from_slice(&(files.len() as u32).to_le_bytes());
    for (name, result) in files {
        out.extend_from_slice(&(name.len() as u32).to_le_bytes());
        out.extend_from_slice(name.as_bytes());
        let (ok, bytes) = match result {
            Ok(scene) => (1u8, scene.as_slice()),
            Err(message) => (0u8, message.as_bytes()),
        };
        out.push(ok);
        out.extend_from_slice(&(bytes.len() as u32).to_le_bytes());
        out.extend_from_slice(bytes);
    }
    out
}

/// What [`encode_bundle`] wrote (`None` for anything else).
pub fn decode_bundle(data: &[u8]) -> Option<Vec<BundleFile>> {
    let mut at = 0usize;
    let mut take = |n: usize| -> Option<&[u8]> {
        let bytes = data.get(at..at.checked_add(n)?)?;
        at += n;
        Some(bytes)
    };
    let u32_of = |b: &[u8]| u32::from_le_bytes([b[0], b[1], b[2], b[3]]) as usize;
    if take(4)? != BUNDLE_MAGIC {
        return None;
    }
    let count = u32_of(take(4)?);
    let mut files = Vec::new();
    for _ in 0..count {
        let n = u32_of(take(4)?);
        let name = String::from_utf8(take(n)?.to_vec()).ok()?;
        let ok = take(1)?[0] == 1;
        let n = u32_of(take(4)?);
        let bytes = take(n)?.to_vec();
        files.push((name, if ok { Ok(bytes) } else { Err(String::from_utf8_lossy(&bytes).into_owned()) }));
    }
    (at == data.len()).then_some(files)
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;

    const TRIANGLE: &str = "solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid t\n";

    fn archive(dir: &Path) -> std::path::PathBuf {
        let path = dir.join("pack.zip");
        let mut zip = zip::ZipWriter::new(std::fs::File::create(&path).unwrap());
        for (name, body) in [("Parts/b.STL", TRIANGLE), ("a.obj", "v 0 0 0\n"), ("readme.txt", "hi"), ("bad.stl", "solid x\n")] {
            zip.start_file(name, zip::write::SimpleFileOptions::default()).unwrap();
            zip.write_all(body.as_bytes()).unwrap();
        }
        zip.finish().unwrap();
        path
    }

    #[test]
    fn model_members_are_listed_by_name() {
        let dir = tempfile::tempdir().unwrap();
        let names: Vec<String> = viewable_members(&archive(dir.path())).unwrap().into_iter().map(|m| m.name).collect();
        assert_eq!(names, ["a.obj", "bad.stl", "Parts/b.STL"]);
    }

    #[test]
    fn members_load_together_each_with_its_own_result() {
        let dir = tempfile::tempdir().unwrap();
        let path = archive(dir.path());
        let names: Vec<String> = ["Parts/b.STL", "missing.stl", "readme.txt", "bad.stl"].map(String::from).to_vec();
        let loaded = load_members(&path, &names, "").unwrap();
        assert_eq!(loaded.iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(), names);
        let scene = meshpeek_core::wire::decode(loaded[0].1.as_ref().unwrap()).unwrap();
        assert_eq!(scene.objects[0].mesh.n_faces(), 1);
        assert!(matches!(loaded[1].1, Err(ViewError::Missing(_))));
        assert!(matches!(loaded[2].1, Err(ViewError::NotViewable(_))));
        assert!(matches!(loaded[3].1, Err(ViewError::Model(_))));
    }

    #[test]
    fn too_many_members_at_once_are_refused() {
        let dir = tempfile::tempdir().unwrap();
        let names = vec!["Parts/b.STL".to_string(); MAX_BATCH_FILES + 1];
        assert!(matches!(load_members(&archive(dir.path()), &names, ""), Err(ViewError::BatchTooLarge { .. })));
    }

    #[test]
    fn a_bundle_survives_the_round_trip() {
        let files = vec![("a/b.stl".to_string(), Ok(vec![1, 2, 3])), ("c.obj".to_string(), Err("bad ✗".to_string()))];
        let bytes = encode_bundle(&files);
        assert_eq!(decode_bundle(&bytes), Some(files));
        assert_eq!(decode_bundle(&bytes[..bytes.len() - 1]), None);
        assert_eq!(decode_bundle(b"MPKS\0\0\0\0"), None);
    }
}
