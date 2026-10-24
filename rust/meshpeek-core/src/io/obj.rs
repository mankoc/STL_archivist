//! Wavefront OBJ reader: geometry only (`v` and `f`), parsed in parallel over line chunks.
//!
//! Everything else (materials, normals, texture coordinates, lines, groups) is ignored, so files that trip
//! stricter readers, e.g. Blender's `usemtl` with an empty name, still load.

use std::path::Path;

use rayon::prelude::*;

use crate::error::{LoadError, Result};
use crate::io::{fan, line_chunks, map_file};
use crate::mesh::Mesh;

/// Load an OBJ file.
pub fn load(path: &Path) -> Result<Mesh> {
    load_bytes(&map_file(path)?, path)
}

/// Parse file contents already in memory; `path` only names the file in error messages.
pub fn load_bytes(data: &[u8], path: &Path) -> Result<Mesh> {
    parse(data).map_err(|e| LoadError::format(format!("{}: {e}", path.display())))
}

/// What one chunk of lines contributed: its vertices and its faces, with indices not yet resolved.
#[derive(Default)]
struct Chunk {
    vertices: Vec<[f32; 3]>,
    colors: Vec<[u8; 3]>,
    all_colored: bool,
    /// Corner indices as written in the file (1-based, or negative = relative to the vertices so far).
    corners: Vec<i64>,
    /// Per face: (corner count, vertices seen earlier in this chunk) for resolving negative indices.
    faces: Vec<(u32, u32)>,
}

/// Parse OBJ text: `v` lines (with optional `r g b` in 0..1) and `f` lines (polygons are fan-triangulated).
/// Vertex colours are kept only when every vertex has them.
pub fn parse(data: &[u8]) -> std::result::Result<Mesh, String> {
    let chunks: Vec<Chunk> = line_chunks(data, rayon::current_num_threads() * 4)
        .into_par_iter()
        .map(parse_chunk)
        .collect::<std::result::Result<_, _>>()?;

    let mut first_vertex = Vec::with_capacity(chunks.len()); // global index of each chunk's first vertex
    let mut total = 0u32;
    for chunk in &chunks {
        first_vertex.push(total);
        total += chunk.vertices.len() as u32;
    }

    let triangles: Vec<Vec<[u32; 3]>> = chunks
        .par_iter()
        .zip(&first_vertex)
        .map(|(chunk, &base)| resolve(chunk, base, total))
        .collect::<std::result::Result<_, _>>()?;
    let faces: Vec<[u32; 3]> = triangles.concat();
    if faces.is_empty() {
        return Err("no triangles found".into());
    }

    let colored = chunks.iter().all(|c| c.all_colored);
    let mut mesh = Mesh::default();
    mesh.vertices.reserve_exact(total as usize);
    for chunk in &chunks {
        mesh.vertices.extend_from_slice(&chunk.vertices);
    }
    if colored {
        mesh.colors = Some(chunks.iter().flat_map(|c| c.colors.iter().copied()).collect());
    }
    mesh.faces = faces;
    Ok(mesh)
}

/// Collect the vertices and raw face corners of one slice of lines.
fn parse_chunk(text: &[u8]) -> std::result::Result<Chunk, String> {
    let mut chunk = Chunk { all_colored: true, ..Chunk::default() };
    for line in text.split(|&b| b == b'\n') {
        if line.len() < 2 || !(line[1] == b' ' || line[1] == b'\t') {
            continue;
        }
        match line[0] {
            b'v' => {
                let mut values = [0f64; 6];
                let mut n = 0;
                for token in fields(&line[2..]) {
                    if n == 6 {
                        break;
                    }
                    values[n] = fast_float2::parse(token).map_err(|_| format!("bad vertex line {:?}", lossy(line)))?;
                    n += 1;
                }
                if n < 3 {
                    return Err(format!("vertex with fewer than 3 coordinates: {:?}", lossy(line)));
                }
                chunk.vertices.push([values[0] as f32, values[1] as f32, values[2] as f32]);
                if n == 6 {
                    chunk.colors.push([0, 1, 2].map(|i| (values[3 + i] * 255.0 + 0.5).clamp(0.0, 255.0) as u8));
                } else {
                    chunk.all_colored = false;
                }
            }
            b'f' => {
                let start = chunk.corners.len();
                for token in fields(&line[2..]) {
                    chunk.corners.push(leading_int(token).ok_or_else(|| format!("bad face line {:?}", lossy(line)))?);
                }
                chunk.faces.push(((chunk.corners.len() - start) as u32, chunk.vertices.len() as u32));
            }
            _ => {}
        }
    }
    Ok(chunk)
}

/// Turn a chunk's raw face corners into triangles of global vertex indices; `base` is the global index of the
/// chunk's first vertex and `total` the number of vertices in the file.
fn resolve(chunk: &Chunk, base: u32, total: u32) -> std::result::Result<Vec<[u32; 3]>, String> {
    let mut out = Vec::with_capacity(chunk.faces.len());
    let mut polygon = Vec::with_capacity(8);
    let mut corners = chunk.corners.iter();
    for &(size, seen) in &chunk.faces {
        polygon.clear();
        for &raw in corners.by_ref().take(size as usize) {
            let index = if raw < 0 { (base + seen) as i64 + raw } else { raw - 1 };
            if index < 0 || index >= total as i64 {
                return Err("face refers to a vertex that does not exist".into());
            }
            polygon.push(index as u32);
        }
        fan(&polygon, &mut out);
    }
    Ok(out)
}

/// Whitespace-separated tokens of a line.
fn fields(text: &[u8]) -> impl Iterator<Item = &[u8]> {
    text.split(|b| b.is_ascii_whitespace()).filter(|t| !t.is_empty())
}

/// The vertex index of a face corner (`v`, `v/vt`, `v//vn` or `v/vt/vn`).
fn leading_int(token: &[u8]) -> Option<i64> {
    let end = token.iter().position(|&b| b == b'/').unwrap_or(token.len());
    std::str::from_utf8(&token[..end]).ok()?.parse().ok()
}

/// A line as text for error messages.
fn lossy(line: &[u8]) -> String {
    String::from_utf8_lossy(line).trim_end().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blender_style_with_empty_material_and_corner_formats() {
        let text = "# Blender\nmtllib b.mtl\no thing\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nvt 0 0\nvn 0 0 1\ns 0\nusemtl \n\
                    f 1/1/1 2/1/1 3/1/1\n\
                    f 1//1 3//1 4//1\n\
                    f\t-4/1  -2/1 -1/1 \n\
                    l 1 2\n";
        let mesh = parse(text.as_bytes()).unwrap();
        assert_eq!(mesh.faces, vec![[0, 1, 2], [0, 2, 3], [0, 2, 3]]);
    }

    #[test]
    fn polygon_fan_and_vertex_colors() {
        let text =
            "v 0 0 0 1 0 0\nv 1 0 0 0 1 0\nv 1 1 0 0 0 1\nv 0.5 1.5 0 1 1 1\nv 0 1 0 0 0 0\nf 1 2 3 4 5\nf 1 2 3\n";
        let mesh = parse(text.as_bytes()).unwrap();
        assert_eq!(mesh.faces, vec![[0, 1, 2], [0, 2, 3], [0, 3, 4], [0, 1, 2]]);
        assert_eq!(mesh.colors.unwrap()[..2], [[255, 0, 0], [0, 255, 0]]);
    }

    #[test]
    fn quad_is_triangulated() {
        let mesh = parse(b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n").unwrap();
        assert_eq!(mesh.n_faces(), 2);
    }

    #[test]
    fn negative_indices_are_relative_to_vertices_so_far() {
        let mesh = parse(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf -3 -2 -1\nv 5 5 5\n").unwrap();
        assert_eq!(mesh.faces, vec![[0, 1, 2]]);
    }

    #[test]
    fn bad_index_is_reported() {
        let err = parse(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n").unwrap_err();
        assert!(err.contains("does not exist"));
    }
}
