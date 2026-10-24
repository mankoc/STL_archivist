//! The triangle mesh shared by all loaders and the renderer.

use rayon::prelude::*;

/// Triangle mesh as plain arrays: float32 positions and 32-bit triangle indices.
///
/// `colors` holds per-vertex RGB when the file provides it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Mesh {
    /// Vertex positions.
    pub vertices: Vec<[f32; 3]>,
    /// Triangles as three indices into `vertices`, counter-clockwise seen from outside.
    pub faces: Vec<[u32; 3]>,
    /// Per-vertex colours, as long as `vertices` when present.
    pub colors: Option<Vec<[u8; 3]>>,
}

impl Mesh {
    /// A mesh without colours.
    pub fn new(vertices: Vec<[f32; 3]>, faces: Vec<[u32; 3]>) -> Self {
        Self { vertices, faces, colors: None }
    }

    /// Number of triangles.
    pub fn n_faces(&self) -> usize {
        self.faces.len()
    }

    /// Axis-aligned bounds `(min, max)`; `None` for a mesh without vertices.
    pub fn bounds(&self) -> Option<([f32; 3], [f32; 3])> {
        let first = *self.vertices.first()?;
        Some(self.vertices.iter().fold((first, first), |(mut lo, mut hi), v| {
            for i in 0..3 {
                lo[i] = lo[i].min(v[i]);
                hi[i] = hi[i].max(v[i]);
            }
            (lo, hi)
        }))
    }

    /// Indexed mesh from triangle corner coordinates (three per triangle), merging corners at identical
    /// positions.
    ///
    /// Formats like STL repeat every shared vertex for each triangle (about 6x on a closed mesh); merging
    /// them cuts vertex memory and GPU vertex work by that factor. Matches positions bit-for-bit.
    pub fn from_corners(corners: &[[f32; 3]]) -> Self {
        assert!(corners.len() % 3 == 0, "corner count must be a multiple of 3");
        let key = |c: &[f32; 3]| ((c[0].to_bits() as u64) << 32 | c[1].to_bits() as u64, c[2].to_bits());
        let mut order: Vec<u32> = (0..corners.len() as u32).collect();
        order.par_sort_unstable_by_key(|&i| key(&corners[i as usize]));

        let mut vertices = Vec::new();
        let mut remap = vec![0u32; corners.len()];
        let mut previous = None;
        for &i in &order {
            let k = key(&corners[i as usize]);
            if previous != Some(k) {
                vertices.push(corners[i as usize]);
                previous = Some(k);
            }
            remap[i as usize] = (vertices.len() - 1) as u32;
        }
        let faces = remap.chunks_exact(3).map(|t| [t[0], t[1], t[2]]).collect();
        Self::new(vertices, faces)
    }

    /// Concatenate meshes, offsetting each one's indices. Colours are kept only if every mesh has them.
    pub fn merge(meshes: impl IntoIterator<Item = Mesh>) -> Self {
        let mut out = Mesh::default();
        let mut colors = Some(Vec::new());
        for mesh in meshes {
            let offset = out.vertices.len() as u32;
            out.faces.extend(mesh.faces.iter().map(|f| [f[0] + offset, f[1] + offset, f[2] + offset]));
            colors = match (colors, mesh.colors) {
                (Some(mut all), Some(c)) => {
                    all.extend(c);
                    Some(all)
                }
                _ => None,
            };
            out.vertices.extend(mesh.vertices);
        }
        out.colors = colors.filter(|c| !c.is_empty());
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn from_corners_merges_shared_corners_and_keeps_triangles() {
        let quad = [[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 0., 0.], [1., 1., 0.], [0., 1., 0.]];
        let mesh = Mesh::from_corners(&quad);
        assert_eq!(mesh.vertices.len(), 4);
        let rebuilt: Vec<[f32; 3]> = mesh.faces.iter().flatten().map(|&i| mesh.vertices[i as usize]).collect();
        assert_eq!(rebuilt, quad);
    }

    #[test]
    fn merge_offsets_indices() {
        let tri = Mesh::new(vec![[0.; 3], [1., 0., 0.], [0., 1., 0.]], vec![[0, 1, 2]]);
        let merged = Mesh::merge([tri.clone(), tri]);
        assert_eq!(merged.faces, vec![[0, 1, 2], [3, 4, 5]]);
        assert_eq!(merged.vertices.len(), 6);
    }
}
