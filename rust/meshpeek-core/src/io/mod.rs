//! Loading any supported file into a [`Scene`].

pub mod chitubox;
pub mod ctb;
pub mod ctp;
pub mod lys;
pub mod obj;
pub mod ply;
pub mod stl;
pub mod threemf;

use std::fs::File;
use std::path::Path;

use glam::{DMat4, DVec4};
use memmap2::Mmap;

use crate::error::{LoadError, Result};
use crate::mesh::Mesh;
use crate::scene::{Scene, SceneObject};

/// Lower-case extensions (without the dot) that [`load`] understands.
pub const SUPPORTED_EXTENSIONS: &[&str] = &["stl", "obj", "ply", "3mf", "lys", "chitubox", "ctp", "ctb"];

/// Rotation of +90 degrees about X: a Y-up model (x, y, z) becomes Z-up (x, -z, y).
pub const Y_UP_TO_Z_UP: DMat4 = DMat4::from_cols(
    DVec4::new(1.0, 0.0, 0.0, 0.0),
    DVec4::new(0.0, 0.0, 1.0, 0.0),
    DVec4::new(0.0, -1.0, 0.0, 0.0),
    DVec4::new(0.0, 0.0, 0.0, 1.0),
);

/// Options that change what a load produces (changing them needs a reload).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LoadOptions {
    /// Facets around `.lys` support columns and their joint spheres.
    pub support_sides: u32,
    /// OBJ files use Y as up (Blender, Maya, ZBrush): rotate them to this viewer's Z-up.
    pub obj_y_up: bool,
}

impl Default for LoadOptions {
    fn default() -> Self {
        Self { support_sides: 12, obj_y_up: true }
    }
}

/// Lower-case extension of `path`, without the dot.
pub fn extension(path: &Path) -> String {
    path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default()
}

/// File contents: memory-mapped from disk or owned (unpacked from an archive).
pub enum Data {
    /// A file mapped read-only from disk.
    Mapped(Mmap),
    /// Bytes held in memory.
    Owned(Vec<u8>),
}

impl std::ops::Deref for Data {
    type Target = [u8];
    fn deref(&self) -> &[u8] {
        match self {
            Self::Mapped(map) => map,
            Self::Owned(bytes) => bytes,
        }
    }
}

impl AsRef<[u8]> for Data {
    fn as_ref(&self) -> &[u8] {
        self
    }
}

/// Parse a single-mesh file (STL, OBJ, PLY) from memory; `name` picks the format and names the file in errors.
pub fn parse_mesh(name: &Path, data: &[u8]) -> Result<Mesh> {
    match extension(name).as_str() {
        "stl" => stl::load_bytes(data, name),
        "obj" => obj::load_bytes(data, name),
        "ply" => ply::load_bytes(data, name),
        other => Err(LoadError::Unsupported(format!(".{other}"))),
    }
}

/// Load a single-mesh file (STL, OBJ, PLY).
pub fn load_mesh(path: &Path) -> Result<Mesh> {
    parse_mesh(path, &map_file(path)?)
}

/// Load any supported file. Single meshes keep their file coordinates, apart from the OBJ up-axis fix.
pub fn load(path: &Path, options: &LoadOptions) -> Result<Scene> {
    load_data(path, Data::Mapped(map_file(path)?), options)
}

/// Like [`load`] for a file whose contents are already in memory (e.g. unpacked from an archive);
/// `name` is only used to pick the format and to label the result.
pub fn load_data(name: &Path, data: Data, options: &LoadOptions) -> Result<Scene> {
    let ext = extension(name);
    let size = data.len() as u64;
    let mut scene = if ext == "lys" {
        lys::load_data(name, data, options.support_sides)?
    } else if ext == "chitubox" {
        chitubox::load_bytes(&data, name)?
    } else if ext == "3mf" {
        threemf::load_bytes(&data, name)?
    } else if ext == "ctp" {
        ctp::load_bytes(&data, name, options.support_sides)?
    } else if ext == "ctb" {
        ctb::load_bytes(&data, name)?
    } else {
        let mut object = SceneObject::new(stem(name), parse_mesh(name, &data)?, name);
        if ext == "obj" && options.obj_y_up {
            object.transform = Y_UP_TO_Z_UP;
        }
        if ext == "stl" {
            if let Some(comment) = stl::comment(&data) {
                object.info.push(("Comment".into(), comment));
            }
        }
        Scene { objects: vec![object], ..Scene::default() }
    };
    let format = match ext.as_str() {
        "lys" => "Lychee scene (.lys)".to_string(),
        "chitubox" => "CHITUBOX project (.chitubox)".to_string(),
        "ctp" => "CHITUBOX project (.ctp)".to_string(),
        "ctb" => "CHITUBOX print file (.ctb)".to_string(),
        other => format!("{} file", other.to_uppercase()),
    };
    let size = format_size(size);
    for object in &mut scene.objects {
        object.info.insert(0, ("Format".into(), format.clone()));
        object.info.insert(1, ("File size".into(), size.clone()));
    }
    Ok(scene)
}

/// File name of `path` without directory and extension, used as the default object name.
pub(crate) fn stem(path: &Path) -> String {
    path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Memory-map a file for reading: the OS pages it in on demand instead of us copying it.
pub(crate) fn map_file(path: &Path) -> Result<Mmap> {
    let file = File::open(path).map_err(|e| LoadError::io(path, e))?;
    if file.metadata().map_err(|e| LoadError::io(path, e))?.len() == 0 {
        return Err(LoadError::format(format!("{}: file is empty", path.display())));
    }
    // SAFETY: the map is read-only; if another program truncates the file while we read it the process
    // can fault, the usual trade-off of mapped I/O, accepted here for speed and memory.
    unsafe { Mmap::map(&file) }.map_err(|e| LoadError::io(path, e))
}

/// Human-readable file size (bytes, KB or MB).
fn format_size(bytes: u64) -> String {
    match bytes {
        b if b >= 1 << 20 => format!("{:.1} MB", b as f64 / (1u64 << 20) as f64),
        b if b >= 1 << 10 => format!("{:.1} KB", b as f64 / 1024.0),
        b => format!("{b} bytes"),
    }
}

/// Split `data` into about `parts` slices (at least 64 KB each) that end on line boundaries, for parsing
/// text formats in parallel.
pub(crate) fn line_chunks(data: &[u8], parts: usize) -> Vec<&[u8]> {
    let target = (data.len() / parts.max(1)).max(1 << 16);
    let mut chunks = Vec::new();
    let mut start = 0;
    while start < data.len() {
        let mut end = (start + target).min(data.len());
        if end < data.len() {
            end = memchr::memchr(b'\n', &data[end..]).map_or(data.len(), |i| end + i + 1);
        }
        chunks.push(&data[start..end]);
        start = end;
    }
    chunks
}

/// Fan-triangulate a polygon given by its corner indices.
pub(crate) fn fan(corners: &[u32], out: &mut Vec<[u32; 3]>) {
    for k in 1..corners.len().saturating_sub(1) {
        out.push([corners[0], corners[k], corners[k + 1]]);
    }
}
