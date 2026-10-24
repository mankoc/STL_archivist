//! A mesh living on the GPU: positions, optional vertex colours and 32-bit indices, split into chunks when
//! it exceeds the adapter's buffer size limit.

use glam::Vec3;
use meshpeek_core::Mesh;
use wgpu::util::DeviceExt;

/// Part of a mesh small enough for one set of buffers, drawn with one call.
pub(crate) struct Chunk {
    /// Positions, 3 x f32 per vertex.
    pub vertices: wgpu::Buffer,
    /// RGBA8 per vertex, when the mesh has colours.
    pub colors: Option<wgpu::Buffer>,
    /// Triangle indices, u32.
    pub indices: wgpu::Buffer,
    /// Number of indices (three per triangle).
    pub index_count: u32,
}

/// GPU copy of a mesh. Creating it only needs the device, so it can be built on a loading thread; the
/// CPU-side mesh can be dropped afterwards.
pub struct GpuMesh {
    pub(crate) chunks: Vec<Chunk>,
    /// Object-space bounds.
    pub bounds: Option<(Vec3, Vec3)>,
    /// Number of triangles.
    pub triangles: usize,
    /// The mesh has per-vertex colours (drawn with them instead of the object's colour).
    pub has_colors: bool,
}

impl GpuMesh {
    /// Upload `mesh`; `max_buffer` is the largest buffer the device allows (`device.limits().max_buffer_size`).
    pub fn upload(device: &wgpu::Device, mesh: &Mesh, max_buffer: u64) -> Self {
        let bounds = mesh.bounds().map(|(lo, hi)| (Vec3::from_array(lo), Vec3::from_array(hi)));
        let max_elements = (max_buffer / 12).max(3) as usize; // 12 bytes per vertex and per triangle
        let chunks = if mesh.vertices.len() <= max_elements && mesh.faces.len() <= max_elements {
            vec![chunk(device, &mesh.vertices, mesh.colors.as_deref(), &mesh.faces)]
        } else {
            split(device, mesh, max_elements)
        };
        Self { chunks, bounds, triangles: mesh.n_faces(), has_colors: mesh.colors.is_some() }
    }

    /// Bytes this mesh occupies on the GPU.
    pub fn gpu_bytes(&self) -> u64 {
        self.chunks
            .iter()
            .map(|c| c.vertices.size() + c.indices.size() + c.colors.as_ref().map_or(0, |b| b.size()))
            .sum()
    }
}

/// Upload one chunk; colours are widened to RGBA because vertex formats have no 3-byte type.
fn chunk(device: &wgpu::Device, vertices: &[[f32; 3]], colors: Option<&[[u8; 3]]>, faces: &[[u32; 3]]) -> Chunk {
    let init = |label, contents: &[u8], usage| {
        device.create_buffer_init(&wgpu::util::BufferInitDescriptor { label: Some(label), contents, usage })
    };
    let colors = colors.map(|c| {
        let rgba: Vec<[u8; 4]> = c.iter().map(|&[r, g, b]| [r, g, b, 255]).collect(); // GPUs read 4-byte colours
        init("mesh colours", bytemuck::cast_slice(&rgba), wgpu::BufferUsages::VERTEX)
    });
    Chunk {
        vertices: init("mesh vertices", bytemuck::cast_slice(vertices), wgpu::BufferUsages::VERTEX),
        colors,
        indices: init("mesh indices", bytemuck::cast_slice(faces), wgpu::BufferUsages::INDEX),
        index_count: (faces.len() * 3) as u32,
    }
}

/// Split into chunks, each with its own compact vertex set. A chunk of N triangles uses at most 3N vertices,
/// so N = max_elements / 3 keeps both its index and its vertex buffer within the limit.
fn split(device: &wgpu::Device, mesh: &Mesh, max_elements: usize) -> Vec<Chunk> {
    let mut remap = vec![u32::MAX; mesh.vertices.len()];
    let mut chunks = Vec::new();
    for faces in mesh.faces.chunks((max_elements / 3).max(1)) {
        let mut vertices = Vec::new();
        let mut colors = mesh.colors.as_ref().map(|_| Vec::new());
        let mut used = Vec::new();
        let local: Vec<[u32; 3]> = faces
            .iter()
            .map(|f| {
                f.map(|i| {
                    let slot = &mut remap[i as usize];
                    if *slot == u32::MAX {
                        *slot = vertices.len() as u32;
                        vertices.push(mesh.vertices[i as usize]);
                        if let (Some(out), Some(all)) = (colors.as_mut(), mesh.colors.as_ref()) {
                            out.push(all[i as usize]);
                        }
                        used.push(i);
                    }
                    *slot
                })
            })
            .collect();
        chunks.push(chunk(device, &vertices, colors.as_deref(), &local));
        used.iter().for_each(|&i| remap[i as usize] = u32::MAX);
    }
    chunks
}
