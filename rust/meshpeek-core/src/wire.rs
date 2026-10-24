//! A loaded [`Scene`] as compact bytes, for a server to send and a web viewer to draw (StlArchivist addition,
//! not in MeshPeek).
//!
//! Little-endian throughout:
//!
//! ```text
//! "MPKS" u32:version(1)
//! u32:plates   { f32 x6: size xyz, position xyz }
//! u32:skipped  { u32:len, utf-8 }
//! u32:objects  { u32:len, utf-8 name, u8:kind (0 model, 1 support), u8:visible, u8:colours, u8:0,
//!                f32 x16: transform (column-major), u32:vertices, u32:faces,
//!                f32 x3 per vertex, u32 x3 per face, [u8 x3 per vertex, zero-padded to 4 bytes] }
//! ```
//!
//! Names, sources and the per-object info are not sent; decoded objects have neither parent nor source.

use glam::DMat4;

use crate::error::{LoadError, Result};
use crate::mesh::Mesh;
use crate::scene::{ObjectKind, Plate, Scene, SceneObject};

const MAGIC: &[u8; 4] = b"MPKS";
const VERSION: u32 = 1;

/// The scene as bytes (see the module documentation).
pub fn encode(scene: &Scene) -> Vec<u8> {
    let size: usize = scene.objects.iter().map(|o| 100 + o.name.len() + o.mesh.vertices.len() * 15 + o.mesh.faces.len() * 12).sum();
    let mut out = Vec::with_capacity(size + 64);
    out.extend_from_slice(MAGIC);
    put_u32(&mut out, VERSION);
    put_u32(&mut out, scene.plates.len() as u32);
    for plate in &scene.plates {
        for v in plate.size.iter().chain(&plate.position) {
            put_f32(&mut out, *v as f32);
        }
    }
    put_u32(&mut out, scene.skipped.len() as u32);
    for text in &scene.skipped {
        put_str(&mut out, text);
    }
    put_u32(&mut out, scene.objects.len() as u32);
    for object in &scene.objects {
        put_str(&mut out, &object.name);
        let mesh = &object.mesh;
        let colors = mesh.colors.as_ref().filter(|c| c.len() == mesh.vertices.len());
        out.extend_from_slice(&[(object.kind == ObjectKind::Support) as u8, object.visible as u8, colors.is_some() as u8, 0]);
        for v in object.transform.to_cols_array() {
            put_f32(&mut out, v as f32);
        }
        put_u32(&mut out, mesh.vertices.len() as u32);
        put_u32(&mut out, mesh.faces.len() as u32);
        for v in mesh.vertices.iter().flatten() {
            put_f32(&mut out, *v);
        }
        for i in mesh.faces.iter().flatten() {
            put_u32(&mut out, *i);
        }
        if let Some(colors) = colors {
            out.extend(colors.iter().flatten());
            out.resize(out.len() + (colors.len() * 3).next_multiple_of(4) - colors.len() * 3, 0);
        }
    }
    out
}

/// The scene [`encode`] wrote; fails on anything else (wrong magic or version, truncated, bad indices).
pub fn decode(data: &[u8]) -> Result<Scene> {
    let mut r = Reader { data, at: 0 };
    if r.take(4)? != MAGIC {
        return Err(LoadError::format("not a MeshPeek scene"));
    }
    let version = r.u32()?;
    if version != VERSION {
        return Err(LoadError::format(format!("unsupported scene version {version}")));
    }
    let mut scene = Scene::default();
    for _ in 0..r.u32()? {
        let v: Vec<f64> = (0..6).map(|_| r.f32().map(f64::from)).collect::<Result<_>>()?;
        scene.plates.push(Plate { size: [v[0], v[1], v[2]], position: [v[3], v[4], v[5]] });
    }
    for _ in 0..r.u32()? {
        scene.skipped.push(r.string()?);
    }
    for _ in 0..r.u32()? {
        let name = r.string()?;
        let flags = r.take(4)?;
        let kind = if flags[0] == 1 { ObjectKind::Support } else { ObjectKind::Model };
        let (visible, has_colors) = (flags[1] != 0, flags[2] != 0);
        let matrix: Vec<f64> = (0..16).map(|_| r.f32().map(f64::from)).collect::<Result<_>>()?;
        let transform = DMat4::from_cols_slice(&matrix);
        let (n_vertices, n_faces) = (r.u32()? as usize, r.u32()? as usize);
        let vertices = r.take(n_vertices.checked_mul(12).ok_or_else(truncated)?)?;
        let vertices: Vec<[f32; 3]> = vertices.as_chunks::<12>().0.iter().map(|c| std::array::from_fn(|k| f32_at(c, k * 4))).collect();
        let faces = r.take(n_faces.checked_mul(12).ok_or_else(truncated)?)?;
        let faces: Vec<[u32; 3]> = faces.as_chunks::<12>().0.iter().map(|c| std::array::from_fn(|k| u32_at(c, k * 4))).collect();
        if faces.iter().flatten().any(|&i| i as usize >= n_vertices) {
            return Err(LoadError::format(format!("{name}: a face uses a vertex that does not exist")));
        }
        let colors = if has_colors {
            let bytes = r.take((n_vertices * 3).next_multiple_of(4))?;
            Some(bytes.as_chunks::<3>().0[..n_vertices].to_vec())
        } else {
            None
        };
        let mesh = Mesh { vertices, faces, colors };
        scene.objects.push(SceneObject { kind, visible, transform, ..SceneObject::new(name, mesh, "") });
    }
    if r.at != data.len() {
        return Err(LoadError::format("unexpected bytes after the scene"));
    }
    Ok(scene)
}

fn put_u32(out: &mut Vec<u8>, v: u32) {
    out.extend_from_slice(&v.to_le_bytes());
}

fn put_f32(out: &mut Vec<u8>, v: f32) {
    out.extend_from_slice(&v.to_le_bytes());
}

fn put_str(out: &mut Vec<u8>, s: &str) {
    put_u32(out, s.len() as u32);
    out.extend_from_slice(s.as_bytes());
}

fn u32_at(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
}

fn f32_at(bytes: &[u8], at: usize) -> f32 {
    f32::from_bits(u32_at(bytes, at))
}

fn truncated() -> LoadError {
    LoadError::format("the scene data is truncated")
}

/// Reads the encoding front to back.
struct Reader<'a> {
    data: &'a [u8],
    at: usize,
}

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        let end = self.at.checked_add(n).filter(|&end| end <= self.data.len()).ok_or_else(truncated)?;
        let bytes = &self.data[self.at..end];
        self.at = end;
        Ok(bytes)
    }

    fn u32(&mut self) -> Result<u32> {
        Ok(u32_at(self.take(4)?, 0))
    }

    fn f32(&mut self) -> Result<f32> {
        Ok(f32_at(self.take(4)?, 0))
    }

    fn string(&mut self) -> Result<String> {
        let n = self.u32()? as usize;
        String::from_utf8(self.take(n)?.to_vec()).map_err(|_| LoadError::format("a name is not UTF-8"))
    }
}

#[cfg(test)]
mod tests {
    use glam::DVec3;

    use super::*;

    fn scene() -> Scene {
        let tri = Mesh::new(vec![[0.; 3], [1., 0., 0.], [0., 1., 0.]], vec![[0, 1, 2]]);
        let mut colored = tri.clone();
        colored.colors = Some(vec![[1, 2, 3], [4, 5, 6], [7, 8, 9]]);
        let mut support = SceneObject::new("Supports", tri.clone(), "a.lys");
        support.kind = ObjectKind::Support;
        support.visible = false;
        support.transform = DMat4::from_translation(DVec3::new(1.0, 2.0, 3.0));
        Scene {
            objects: vec![SceneObject::new("Dragon ✓", colored, "a.lys"), support],
            plates: vec![Plate { size: [120.0, 68.0, 150.0], position: [0.0, 0.0, 0.0] }],
            skipped: vec!["broken: no faces".into()],
        }
    }

    #[test]
    fn a_scene_survives_the_round_trip() {
        let original = scene();
        let decoded = decode(&encode(&original)).unwrap();
        assert_eq!(decoded.plates, original.plates);
        assert_eq!(decoded.skipped, original.skipped);
        assert_eq!(decoded.objects.len(), 2);
        for (a, b) in decoded.objects.iter().zip(&original.objects) {
            assert_eq!((&a.name, &a.mesh, a.kind, a.visible, a.transform), (&b.name, &b.mesh, b.kind, b.visible, b.transform));
        }
    }

    #[test]
    fn damaged_data_is_refused() {
        let bytes = encode(&scene());
        assert!(decode(&bytes[..bytes.len() - 1]).is_err());
        assert!(decode(&[bytes.as_slice(), &[0]].concat()).is_err());
        assert!(decode(b"PK\x03\x04").is_err());
        let mut bad_index = encode(&Scene { objects: vec![scene().objects.remove(1)], ..Scene::default() });
        let faces_at = bad_index.len() - 12;
        bad_index[faces_at..faces_at + 4].copy_from_slice(&7u32.to_le_bytes());
        assert!(decode(&bad_index).is_err());
    }
}
