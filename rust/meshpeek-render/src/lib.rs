//! MeshPeek renderer: uploads core meshes to the GPU and draws them with wgpu into an offscreen texture that
//! the UI shows. No UI code lives here.

// MeshPeek's code as it is upstream, which lints with an older clippy (see ../meshpeek-core/README.md).
#![allow(clippy::collapsible_if)]

pub mod camera;
pub mod gpu_mesh;
pub mod renderer;

pub use camera::Camera;
pub use gpu_mesh::GpuMesh;
pub use renderer::{Material, ObjectId, Palette, Renderer, Style};
pub use wgpu;

/// Colour format of the image handed to the UI (gamma space, as egui expects for native textures).
pub const COLOR_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8Unorm;

/// The eight corners of the axis-aligned box `low..high` (bit 0 picks x, bit 1 y, bit 2 z).
pub(crate) fn box_corners(low: glam::DVec3, high: glam::DVec3) -> [glam::DVec3; 8] {
    std::array::from_fn(|i| {
        glam::DVec3::new(
            if i & 1 == 0 { low.x } else { high.x },
            if i & 2 == 0 { low.y } else { high.y },
            if i & 4 == 0 { low.z } else { high.z },
        )
    })
}
