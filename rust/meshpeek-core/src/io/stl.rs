//! STL reader. Binary files are read straight from the memory map in parallel; shared corners are merged.

use std::path::Path;

use rayon::prelude::*;

use crate::error::{LoadError, Result};
use crate::io::map_file;
use crate::mesh::Mesh;

const HEADER: usize = 84; // 80-byte comment + u32 triangle count
const RECORD: usize = 50; // normal, 3 corners (12 floats), u16 attribute

/// Load an STL file (binary or ASCII).
pub fn load(path: &Path) -> Result<Mesh> {
    load_bytes(&map_file(path)?, path)
}

/// Parse file contents already in memory; `path` only names the file in error messages.
pub fn load_bytes(data: &[u8], path: &Path) -> Result<Mesh> {
    let corners = if is_binary(data) { binary_corners(data) } else { ascii_corners(data, path)? };
    if corners.is_empty() {
        return Err(LoadError::format(format!("{}: no triangles found", path.display())));
    }
    Ok(Mesh::from_corners(&corners))
}

/// The 80-byte header comment of a binary STL (or the name after `solid` in an ASCII one), if it says anything.
pub fn comment(data: &[u8]) -> Option<String> {
    let text = String::from_utf8_lossy(&data[..data.len().min(80)]);
    let text: String = text.chars().filter(|c| !c.is_control()).collect();
    let text = text.trim().trim_start_matches("solid").trim().to_string();
    (!text.is_empty()).then_some(text)
}

/// Size check rather than the "solid" prefix: many binary STLs start with "solid" too.
fn is_binary(data: &[u8]) -> bool {
    data.len() >= HEADER && {
        let n = u32::from_le_bytes(data[80..84].try_into().unwrap()) as usize;
        HEADER + n * RECORD == data.len()
    }
}

/// The three corners of every triangle of a binary STL (normals and attributes are ignored).
fn binary_corners(data: &[u8]) -> Vec<[f32; 3]> {
    let f = |b: &[u8], i: usize| f32::from_le_bytes(b[i..i + 4].try_into().unwrap());
    data[HEADER..]
        .par_chunks_exact(RECORD)
        .flat_map_iter(|r| (0..3).map(move |c| [f(r, 12 + c * 12), f(r, 16 + c * 12), f(r, 20 + c * 12)]))
        .collect()
}

/// The corners of every `vertex` line of an ASCII STL.
fn ascii_corners(data: &[u8], path: &Path) -> Result<Vec<[f32; 3]>> {
    let text = std::str::from_utf8(data)
        .map_err(|_| LoadError::format(format!("{}: neither binary STL nor ASCII text", path.display())))?;
    let mut tokens = text.split_ascii_whitespace();
    let mut corners = Vec::new();
    while let Some(token) = tokens.next() {
        if token.eq_ignore_ascii_case("vertex") {
            let mut c = [0f32; 3];
            for value in &mut c {
                let t = tokens.next().unwrap_or("");
                *value = fast_float2::parse::<f64, _>(t)
                    .map_err(|_| LoadError::format(format!("{}: bad vertex value {t:?}", path.display())))?
                    as f32;
            }
            corners.push(c);
        }
    }
    if corners.len() % 3 != 0 {
        return Err(LoadError::format(format!("{}: vertex count is not a multiple of 3", path.display())));
    }
    Ok(corners)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub fn write_binary(path: &Path, corners: &[[f32; 3]]) {
        let mut bytes = b"solid looks-like-ascii".to_vec(); // binary file whose header starts with "solid"
        bytes.resize(80, b' ');
        bytes.extend((corners.len() as u32 / 3).to_le_bytes());
        for tri in corners.chunks(3) {
            bytes.extend([0u8; 12]);
            tri.iter().flatten().for_each(|v| bytes.extend(v.to_le_bytes()));
            bytes.extend([0u8; 2]);
        }
        std::fs::write(path, bytes).unwrap();
    }

    const TRI: [[f32; 3]; 3] = [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]];

    #[test]
    fn binary_with_solid_header() {
        let dir = std::env::temp_dir().join("meshpeek-stl-bin");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("t.stl");
        let shifted = TRI.map(|v| v.map(|x| x + 2.0));
        write_binary(&path, &[TRI, shifted].concat());
        let mesh = load(&path).unwrap();
        assert_eq!(mesh.n_faces(), 2);
        assert_eq!(mesh.faces[1].map(|i| mesh.vertices[i as usize]), shifted);
    }

    #[test]
    fn ascii() {
        let dir = std::env::temp_dir().join("meshpeek-stl-ascii");
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("t.stl");
        let facet: String = TRI.iter().map(|v| format!("vertex {} {} {}\n", v[0], v[1], v[2])).collect();
        std::fs::write(
            &path,
            format!("solid t\nfacet normal 0 0 1\nouter loop\n{facet}endloop\nendfacet\nendsolid t\n"),
        )
        .unwrap();
        assert_eq!(load(&path).unwrap().n_faces(), 1);
    }
}
