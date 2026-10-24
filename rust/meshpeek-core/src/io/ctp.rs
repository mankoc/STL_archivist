//! CHITUBOX `.ctp` projects: the models of a CHITUBOX scene with their supports, in the newer layout with 64-bit
//! offsets (magic `0xAB231253`; the older `.chitubox` projects are read by `chitubox.rs`). Worked out from a sample
//! file; `docs/formats/CTP_NOTES.md` describes it.
//!
//! In short, all little-endian:
//!
//! - header: magic, object count, the offset of the object table (u64 at 24) and of the preview;
//! - object table: 216 bytes per object: its id, X/Y position on the plate, half its height, its lift above the
//!   plate, and the length and offset of its name, the offset of its mesh, and the offset and count of its
//!   support records;
//! - mesh: magic `0xAB231255`, then the offset and count of its vertices (3 × f32, centred on the origin, the
//!   object's rotation already applied) and of its triangle indices (u32);
//! - support records (40 bytes, magic `0xAB231256`): the object id, a part count and the offset of the parts;
//! - support part (48 bytes): its type, a style word, end points A and B and a radius at each, in the model's frame;
//!   the types are those of `.chitubox` projects (tips, columns, foot cones, contact balls, joints, link rods).
//!
//! Unlike `.chitubox` projects, the supports carry no triangles: their shapes are built from the parts.

use std::path::Path;

use glam::{DMat4, DVec3};
use rayon::prelude::*;

use crate::error::{LoadError, Result};
use crate::io::lys::supports::{capsule, sphere};
use crate::mesh::Mesh;
use crate::scene::{ObjectKind, Scene, SceneObject};

/// Magic numbers of the file, an object record, a mesh and a support record.
const MAGIC: u32 = 0xAB23_1253;
const OBJECT_MAGIC: u32 = 0xAB23_1254;
const MESH_MAGIC: u32 = 0xAB23_1255;
const SUPPORT_MAGIC: u32 = 0xAB23_1256;
/// Sizes of an object record, a support record and a support part.
const OBJECT: usize = 216;
const SUPPORT: usize = 40;
const PART: usize = 48;
/// Part types (`CHITUBOX_FILE_FORMAT.md`, 6.5).
const TOP_TIP: u32 = 1;
const BOTTOM_TIP: u32 = 2;
const COLUMN: u32 = 3;
const FOOT_CONE: u32 = 4;
const FOOT_PAD: u32 = 5;
const CONTACT_BALL: u32 = 7;
const JOINT: u32 = 9;
const LINK_ROD: u32 = 12;

/// A little-endian u32, u64 and f32 at `at`, if the data is long enough.
fn word(data: &[u8], at: usize) -> Option<u32> {
    data.get(at..at.checked_add(4)?).map(|b| u32::from_le_bytes(b.try_into().unwrap()))
}

fn long(data: &[u8], at: usize) -> Option<usize> {
    data.get(at..at.checked_add(8)?).and_then(|b| usize::try_from(u64::from_le_bytes(b.try_into().unwrap())).ok())
}

fn float(data: &[u8], at: usize) -> Option<f64> {
    word(data, at).map(|w| f32::from_bits(w) as f64)
}

/// A point of three f32 at `at`.
fn point(data: &[u8], at: usize) -> Option<DVec3> {
    Some(DVec3::new(float(data, at)?, float(data, at + 4)?, float(data, at + 8)?))
}

/// One support part: its type, end points A and B and the radius at each.
struct Part {
    kind: u32,
    a: DVec3,
    b: DVec3,
    size_a: f64,
    size_b: f64,
}

/// One object of the project, as stored.
struct Object {
    name: String,
    /// Centre on the plate (x, y) and the height of the mesh's origin above the plate.
    position: DVec3,
    lift: f64,
    mesh: Mesh,
    parts: Vec<Part>,
}

/// Load a `.ctp` project whose bytes are `data`; `path` names it in errors and in the objects. Round support parts
/// get `support_sides` facets around.
pub fn load_bytes(data: &[u8], path: &Path, support_sides: u32) -> Result<Scene> {
    let bad = |what: String| LoadError::format(format!("{}: {what}", path.display()));
    if word(data, 0) != Some(MAGIC) {
        return Err(bad("not a CHITUBOX .ctp project (unknown file signature)".into()));
    }
    let count = word(data, 4).unwrap_or(0) as usize;
    let table = long(data, 24).unwrap_or(0);
    if count == 0 || table.saturating_add(count.saturating_mul(OBJECT)) > data.len() {
        return Err(bad("the object table is missing or cut short".into()));
    }
    let read: Vec<std::result::Result<Object, String>> =
        (0..count).into_par_iter().map(|i| read_object(data, table + i * OBJECT)).collect();
    let sides = support_sides.max(3) as usize;
    let mut scene = Scene::default();
    for (i, object) in read.into_iter().enumerate() {
        let object = match object {
            Ok(object) => object,
            Err(reason) => {
                scene.skipped.push(format!("object {}: {reason}", i + 1));
                continue;
            }
        };
        let transform = DMat4::from_translation(object.position);
        let contacts = object.parts.iter().filter(|p| matches!(p.kind, TOP_TIP | BOTTOM_TIP)).count();
        let mut info = vec![("Lift".to_string(), format!("{:.2} mm", object.lift))];
        if !object.parts.is_empty() {
            info.push(("Contact points".to_string(), contacts.to_string()));
        }
        let supports = support_mesh(&object.parts, sides);
        let mut model = SceneObject::new(object.name, object.mesh, path);
        model.transform = transform;
        model.info = info.clone();
        let parent = scene.objects.len();
        scene.objects.push(model);
        if !supports.faces.is_empty() {
            let mut child = SceneObject::new("Supports", supports, path);
            child.kind = ObjectKind::Support;
            child.transform = transform;
            child.parent = Some(parent);
            child.info = info;
            scene.objects.push(child);
        }
    }
    if scene.objects.is_empty() {
        return Err(bad("no readable objects".into()));
    }
    Ok(scene)
}

/// Read the object whose record starts at `at`: its name, placement, mesh and support parts.
fn read_object(data: &[u8], at: usize) -> std::result::Result<Object, String> {
    let short = || "its record is cut short".to_string();
    if word(data, at) != Some(OBJECT_MAGIC) {
        return Err("its record is not valid".into());
    }
    let (x, y) = (float(data, at + 8).ok_or_else(short)?, float(data, at + 12).ok_or_else(short)?);
    let (half_height, lift) = (float(data, at + 16).ok_or_else(short)?, float(data, at + 20).ok_or_else(short)?);
    if ![x, y, half_height, lift].iter().all(|v| v.is_finite()) {
        return Err("its position is not a number".into());
    }
    let name_length = word(data, at + 176).ok_or_else(short)? as usize;
    let name_at = long(data, at + 184).ok_or_else(short)?;
    let name = data
        .get(name_at..name_at.saturating_add(name_length))
        .map(|text| String::from_utf8_lossy(text).trim_matches(char::from(0)).trim().to_string())
        .filter(|text| !text.is_empty())
        .unwrap_or_else(|| format!("Object {:#x}", word(data, at + 4).unwrap_or(0)));
    let mesh = read_mesh(data, long(data, at + 192).ok_or_else(short)?)?;
    let parts = read_parts(data, long(data, at + 200).ok_or_else(short)?, long(data, at + 208).ok_or_else(short)?)?;
    Ok(Object { name, position: DVec3::new(x, y, half_height + lift), lift, mesh, parts })
}

/// The mesh block at `at`: its vertices and triangles.
fn read_mesh(data: &[u8], at: usize) -> std::result::Result<Mesh, String> {
    if word(data, at) != Some(MESH_MAGIC) {
        return Err("its mesh is missing".into());
    }
    let short = || "its mesh lies outside the file".to_string();
    let (vertices_at, vertex_count) = (long(data, at + 8).ok_or_else(short)?, long(data, at + 16).ok_or_else(short)?);
    let (indices_at, index_count) = (long(data, at + 24).ok_or_else(short)?, long(data, at + 32).ok_or_else(short)?);
    let vertex_bytes =
        data.get(vertices_at..vertices_at.saturating_add(vertex_count.saturating_mul(12))).ok_or_else(short)?;
    let index_bytes =
        data.get(indices_at..indices_at.saturating_add(index_count.saturating_mul(4))).ok_or_else(short)?;
    if index_count % 3 != 0 {
        return Err("its triangle list is not whole triangles".into());
    }
    let float = |b: &[u8]| f32::from_le_bytes(b.try_into().unwrap());
    let vertices: Vec<[f32; 3]> =
        vertex_bytes.par_chunks_exact(12).map(|c| [float(&c[0..4]), float(&c[4..8]), float(&c[8..12])]).collect();
    let index = |b: &[u8]| u32::from_le_bytes(b.try_into().unwrap());
    let faces: Vec<[u32; 3]> =
        index_bytes.par_chunks_exact(12).map(|c| [index(&c[0..4]), index(&c[4..8]), index(&c[8..12])]).collect();
    if faces.par_iter().flatten().any(|&v| v as usize >= vertices.len()) {
        return Err("its mesh refers to vertices it does not have".into());
    }
    Ok(Mesh::new(vertices, faces))
}

/// The parts of the `count` support records at `at`.
fn read_parts(data: &[u8], at: usize, count: usize) -> std::result::Result<Vec<Part>, String> {
    let mut parts = Vec::new();
    for record in 0..count {
        let r = at + record * SUPPORT;
        if word(data, r) != Some(SUPPORT_MAGIC) {
            return Err(format!("support record {} is not valid", record + 1));
        }
        let short = || format!("support record {} lies outside the file", record + 1);
        let (n, first) = (long(data, r + 24).ok_or_else(short)?, long(data, r + 32).ok_or_else(short)?);
        for k in 0..n {
            let p = first.saturating_add(k.saturating_mul(PART));
            let part = (|| {
                Some(Part {
                    kind: word(data, p)?,
                    a: point(data, p + 8)?,
                    b: point(data, p + 20)?,
                    size_a: float(data, p + 32)?,
                    size_b: float(data, p + 36)?,
                })
            })();
            parts.push(part.ok_or_else(short)?);
        }
    }
    Ok(parts)
}

/// The supports' surface, built from their parts (`CHITUBOX_FILE_FORMAT.md`, 6.5): tips, columns and foot cones
/// are frustums from A to B, contact balls and joints spheres halfway between them, link rods cylinders with a
/// joint at each end, foot pads square frustums.
fn support_mesh(parts: &[Part], sides: usize) -> Mesh {
    let rings = (sides / 2).max(2);
    let meshes: Vec<Mesh> = parts
        .par_iter()
        .filter(|p| [p.a, p.b].iter().all(|v| v.is_finite()) && p.size_a.is_finite() && p.size_b.is_finite())
        .flat_map_iter(|p| {
            let (a, b, ra, rb) = (p.a, p.b, p.size_a.abs().max(1e-3), p.size_b.abs().max(1e-3));
            let middle = (a + b) / 2.0;
            match p.kind {
                TOP_TIP | BOTTOM_TIP | COLUMN | FOOT_CONE => vec![capsule(a, b, ra, rb, true, true, sides, 1, 1)],
                CONTACT_BALL | JOINT => vec![sphere(middle, ra, sides.max(2 * rings))],
                LINK_ROD => vec![
                    capsule(a, b, ra, ra, true, true, sides, 1, 1),
                    sphere(a, ra * 1.04, sides),
                    sphere(b, ra * 1.04, sides),
                ],
                FOOT_PAD => vec![square_pad(a, b, rb, ra)],
                _ => Vec::new(),
            }
        })
        .collect();
    Mesh::merge(meshes)
}

/// A foot pad: a square frustum, its sides along X and Y, from `top` (half diagonal `top_size`) down to `bottom`
/// on the plate (half diagonal `bottom_size`).
fn square_pad(top: DVec3, bottom: DVec3, top_size: f64, bottom_size: f64) -> Mesh {
    let corners = |centre: DVec3, half_diagonal: f64| {
        let h = half_diagonal / std::f64::consts::SQRT_2;
        [(-h, -h), (h, -h), (h, h), (-h, h)].map(|(x, y)| (centre + DVec3::new(x, y, 0.0)).as_vec3().to_array())
    };
    let mut vertices = corners(bottom, bottom_size).to_vec();
    vertices.extend(corners(top, top_size));
    let mut faces = vec![[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7]];
    for i in 0..4u32 {
        let j = (i + 1) % 4;
        faces.extend([[i, j, j + 4], [i, j + 4, i + 4]]);
    }
    Mesh::new(vertices, faces)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A project of one object: a triangle mesh and two support records (a column and a joint, a top tip).
    fn project() -> Vec<u8> {
        let mut data = vec![0u8; 0x60];
        let put32 = |data: &mut Vec<u8>, at: usize, v: u32| data[at..at + 4].copy_from_slice(&v.to_le_bytes());
        let put64 = |data: &mut Vec<u8>, at: usize, v: u64| data[at..at + 8].copy_from_slice(&v.to_le_bytes());
        let putf = |data: &mut Vec<u8>, at: usize, v: f32| data[at..at + 4].copy_from_slice(&v.to_le_bytes());
        put32(&mut data, 0, MAGIC);
        put32(&mut data, 4, 1);
        let table = data.len();
        put64(&mut data, 24, table as u64);
        data.resize(table + OBJECT, 0);
        let name = data.len();
        data.extend(b"Thing.stl");
        let mesh = data.len();
        data.resize(mesh + 40, 0);
        let vertices = data.len();
        for v in [[-1.0f32, -1.0, -2.0], [1.0, -1.0, -2.0], [0.0, 1.0, 2.0]] {
            v.iter().for_each(|c| data.extend(c.to_le_bytes()));
        }
        let indices = data.len();
        [0u32, 1, 2].iter().for_each(|i| data.extend(i.to_le_bytes()));
        put32(&mut data, mesh, MESH_MAGIC);
        put64(&mut data, mesh + 8, vertices as u64);
        put64(&mut data, mesh + 16, 3);
        put64(&mut data, mesh + 24, indices as u64);
        put64(&mut data, mesh + 32, 3);
        let records = data.len();
        data.resize(records + 2 * SUPPORT, 0);
        let parts = data.len();
        // record 1: a column from z -2 down to the plate (z -7) and a joint; record 2: a top tip
        let part_list = [
            (COLUMN, [0.0f32, 0.0, -2.0], [0.0, 0.0, -7.0], 0.4, 0.4),
            (JOINT, [0.0, 0.0, -1.9], [0.0, 0.0, -2.1], 0.42, 0.42),
            (TOP_TIP, [0.0, 0.0, -1.0], [0.0, 0.0, -2.0], 0.2, 0.4),
        ];
        for (kind, a, b, ra, rb) in part_list {
            let p = data.len();
            data.resize(p + PART, 0);
            put32(&mut data, p, kind);
            for k in 0..3 {
                putf(&mut data, p + 8 + 4 * k, a[k]);
                putf(&mut data, p + 20 + 4 * k, b[k]);
            }
            putf(&mut data, p + 32, ra);
            putf(&mut data, p + 36, rb);
        }
        for (r, (n, first)) in [(2u64, parts), (1, parts + 2 * PART)].into_iter().enumerate() {
            let at = records + r * SUPPORT;
            put32(&mut data, at, SUPPORT_MAGIC);
            put64(&mut data, at + 24, n);
            put64(&mut data, at + 32, first as u64);
        }
        put32(&mut data, table, OBJECT_MAGIC);
        put32(&mut data, table + 4, 0x1234);
        putf(&mut data, table + 8, 10.0);
        putf(&mut data, table + 12, -5.0);
        putf(&mut data, table + 16, 2.0); // half the mesh's height
        putf(&mut data, table + 20, 5.0); // lift
        put32(&mut data, table + 176, 9);
        put64(&mut data, table + 184, name as u64);
        put64(&mut data, table + 192, mesh as u64);
        put64(&mut data, table + 200, records as u64);
        put64(&mut data, table + 208, 2);
        data
    }

    #[test]
    fn objects_stand_on_their_lift_and_supports_reach_the_plate() {
        let scene = load_bytes(&project(), Path::new("t.ctp"), 12).unwrap();
        assert_eq!(scene.objects.len(), 2);
        let (model, supports) = (&scene.objects[0], &scene.objects[1]);
        assert_eq!(model.name, "Thing.stl");
        assert_eq!(model.mesh.faces.len(), 1);
        // the model's bottom (z -2) stands 5 mm above the plate, at (10, -5)
        assert_eq!(model.transform.transform_point3(DVec3::new(0.0, 0.0, -2.0)), DVec3::new(10.0, -5.0, 5.0));
        assert_eq!((supports.kind, supports.parent), (ObjectKind::Support, Some(0)));
        let (lo, _) = supports.mesh.bounds().unwrap();
        assert!((supports.transform.transform_point3(DVec3::from_array(lo.map(f64::from))).z).abs() < 1e-6);
        assert!(model.info.contains(&("Contact points".to_string(), "1".to_string())));
    }

    #[test]
    fn other_files_are_refused() {
        assert!(load_bytes(b"not a project", Path::new("t.ctp"), 12).is_err());
        let mut broken = project();
        broken[0x60 + 192..0x60 + 200].copy_from_slice(&u64::MAX.to_le_bytes()); // the mesh offset
        let error = load_bytes(&broken, Path::new("t.ctp"), 12).unwrap_err().to_string();
        assert!(error.contains("no readable objects"), "{error}");
    }
}
