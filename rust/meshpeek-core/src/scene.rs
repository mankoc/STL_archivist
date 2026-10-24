//! What a loaded file turns into: objects (models and their supports) and build plates.

use std::path::PathBuf;

use glam::DMat4;

use crate::mesh::Mesh;

/// What an object is, which decides its default colour and material.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObjectKind {
    /// A printed model.
    Model,
    /// Support structure (and raft) generated for a model.
    Support,
}

/// One mesh of a scene with its placement and description.
#[derive(Debug, Clone)]
pub struct SceneObject {
    /// Name shown in the object list.
    pub name: String,
    /// Geometry in object space.
    pub mesh: Mesh,
    /// Object -> world.
    pub transform: DMat4,
    /// Model or support.
    pub kind: ObjectKind,
    /// Shown at start (a file can store hidden objects).
    pub visible: bool,
    /// The file the object came from.
    pub source: PathBuf,
    /// Index into [`Scene::objects`] of the object this one belongs to (e.g. a model's supports).
    pub parent: Option<usize>,
    /// Extra facts about the source file (format, size, comment…) as label/value pairs.
    pub info: Vec<(String, String)>,
}

impl SceneObject {
    /// A visible model at the origin (identity transform), without parent or extra info.
    pub fn new(name: impl Into<String>, mesh: Mesh, source: impl Into<PathBuf>) -> Self {
        Self {
            name: name.into(),
            mesh,
            transform: DMat4::IDENTITY,
            kind: ObjectKind::Model,
            visible: true,
            source: source.into(),
            parent: None,
            info: Vec::new(),
        }
    }
}

/// Build volume: size (x, y, z) in mm, centred on `position` in XY, standing on its Z.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Plate {
    /// Width, depth and height in mm.
    pub size: [f64; 3],
    /// Centre of the floor in world coordinates.
    pub position: [f64; 3],
}

/// Everything loaded from one file.
#[derive(Debug, Clone, Default)]
pub struct Scene {
    /// Models and supports; supports follow the model they belong to.
    pub objects: Vec<SceneObject>,
    /// Build plates the file describes (slicer scenes only).
    pub plates: Vec<Plate>,
    /// `"<name>: <reason>"` for objects that could not be loaded.
    pub skipped: Vec<String>,
}
