//! The model viewer's side of an archive: which members MeshPeek can show
//! (`meshpeek_core`, vendored from MeshPeek), and one of them loaded as a scene
//! for the web client's viewer (`meshpeek_core::wire`).

use std::fmt;
use std::path::Path;

use meshpeek_core::{LoadError, LoadOptions, SUPPORTED_EXTENSIONS};

use crate::archive::{self, ArchiveError, ArchiveMember};

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
    /// The member is not a valid model file.
    Model(LoadError),
}

impl fmt::Display for ViewError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ViewError::Archive(e) => write!(f, "{e}"),
            ViewError::Missing(name) => write!(f, "{name} is not in the archive"),
            ViewError::NotViewable(name) => write!(f, "{name} is not a model file the viewer shows"),
            ViewError::Model(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for ViewError {}

/// The archive member `name` loaded (MeshPeek's default options) and encoded
/// for the viewer. `unrar` is the UnRAR tool compressed `.rar` members need.
pub fn load_member(path: &Path, name: &str, unrar: &str) -> Result<Vec<u8>, ViewError> {
    if !is_viewable(name) {
        return Err(ViewError::NotViewable(name.to_string()));
    }
    let data = archive::read_members_with(path, &[name.to_string()], unrar)
        .map_err(ViewError::Archive)?
        .into_iter()
        .find(|(n, _)| n == name)
        .map(|(_, data)| data)
        .ok_or_else(|| ViewError::Missing(name.to_string()))?;
    let scene = meshpeek_core::io::load_data(Path::new(name), meshpeek_core::io::Data::Owned(data), &LoadOptions::default())
        .map_err(ViewError::Model)?;
    Ok(meshpeek_core::wire::encode(&scene))
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
    fn a_member_loads_as_a_scene() {
        let dir = tempfile::tempdir().unwrap();
        let path = archive(dir.path());
        let scene = meshpeek_core::wire::decode(&load_member(&path, "Parts/b.STL", "").unwrap()).unwrap();
        assert_eq!(scene.objects.len(), 1);
        assert_eq!(scene.objects[0].mesh.n_faces(), 1);
        assert!(matches!(load_member(&path, "missing.stl", ""), Err(ViewError::Missing(_))));
        assert!(matches!(load_member(&path, "readme.txt", ""), Err(ViewError::NotViewable(_))));
        assert!(matches!(load_member(&path, "bad.stl", ""), Err(ViewError::Model(_))));
    }
}
