//! MeshPeek core: file formats, meshes and scenes. No GPU or UI code lives here.
//!
//! Entry point: [`io::load`] turns any supported file into a [`scene::Scene`]; [`wire`] sends one to a web viewer.
//! File loading needs the `io` feature (on by default).

// MeshPeek's code as it is upstream, which lints with an older clippy (see README.md).
#![allow(clippy::chunks_exact_to_as_chunks, clippy::collapsible_if, clippy::manual_is_multiple_of)]

pub mod error;
#[cfg(feature = "io")]
pub mod io;
pub mod mesh;
pub mod scene;
pub mod wire;

pub use error::{LoadError, Result};
#[cfg(feature = "io")]
pub use io::{LoadOptions, SUPPORTED_EXTENSIONS, load, load_mesh};
pub use mesh::Mesh;
pub use scene::{ObjectKind, Plate, Scene, SceneObject};
