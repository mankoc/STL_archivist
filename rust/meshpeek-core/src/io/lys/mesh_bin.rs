//! Embedded object meshes: `<hash>.bin` (and `<hash>_hollowing.bin`).
//!
//! Layout (verified on sample files): five u32 (version = 2, header_len, n_indices, n_coords, reserved), then
//! `n_indices` u32 and `n_coords` f32. Data always starts at byte 20 even though header_len reads 12.

use rayon::prelude::*;

use crate::error::{LoadError, Result};
use crate::mesh::Mesh;

/// Bytes before the index data.
pub const HEADER_SIZE: usize = 20;

/// Parse a mesh member, checking its size against the header and its indices against the vertex count.
pub fn parse(raw: &[u8]) -> Result<Mesh> {
    if raw.len() < HEADER_SIZE {
        return Err(LoadError::format("mesh member too short"));
    }
    let word = |i: usize| u32::from_le_bytes(raw[i * 4..i * 4 + 4].try_into().unwrap()) as usize;
    let (version, n_indices, n_coords) = (word(0), word(2), word(3));
    if version != 2 {
        return Err(LoadError::format(format!("unsupported mesh version {version}")));
    }
    if raw.len() != HEADER_SIZE + 4 * (n_indices + n_coords) || n_indices % 3 != 0 || n_coords % 3 != 0 {
        return Err(LoadError::format("mesh member size does not match its header"));
    }
    // three u32 (a triangle) or three f32 (a vertex) per 12 bytes, read in parallel
    let triple = |t: &[u8]| [0, 1, 2].map(|k| u32::from_le_bytes(t[k * 4..k * 4 + 4].try_into().unwrap()));
    let coords = HEADER_SIZE + 4 * n_indices;
    let faces: Vec<[u32; 3]> = raw[HEADER_SIZE..coords].par_chunks_exact(12).map(triple).collect();
    let vertices: Vec<[f32; 3]> = raw[coords..].par_chunks_exact(12).map(|v| triple(v).map(f32::from_bits)).collect();
    if faces.par_iter().any(|f| f.iter().any(|&i| i as usize >= vertices.len())) {
        return Err(LoadError::format("mesh member refers to a vertex that does not exist"));
    }
    Ok(Mesh::new(vertices, faces))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_wrong_size() {
        let mut raw: Vec<u8> = [2u32, 12, 3, 9, 0].iter().flat_map(|w| w.to_le_bytes()).collect();
        raw.extend([0u32, 1, 2].iter().flat_map(|w| w.to_le_bytes()));
        assert!(parse(&raw).is_err());
    }

    #[test]
    fn parses_one_triangle() {
        let mut raw: Vec<u8> = [2u32, 12, 3, 9, 0].iter().flat_map(|w| w.to_le_bytes()).collect();
        raw.extend([0u32, 1, 2].iter().flat_map(|w| w.to_le_bytes()));
        raw.extend([0f32, 0., 0., 1., 0., 0., 0., 1., 0.].iter().flat_map(|w| w.to_le_bytes()));
        let mesh = parse(&raw).unwrap();
        assert_eq!(mesh.faces, vec![[0, 1, 2]]);
        assert_eq!(mesh.vertices[2], [0., 1., 0.]);
    }
}
