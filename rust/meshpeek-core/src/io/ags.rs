//! `.ags` support projects: one model with its supports, as a JSON file written by a .NET program. Worked out from
//! sample files; `docs/formats/AGS_NOTES.md` describes it.
//!
//! In short:
//!
//! - `ModifiedMeshDataBase64Gz`: the model, gzipped and in Base64: the vertex count (i32), the vertices (3 × f32), the
//!   index count, the triangle indices (i32), the normal count and the normals. Without it the project is refused:
//!   `ModelPath` is never opened, because a server loads these files from other people's archives;
//! - `ModelTransform`: the model's matrix (`M11`…`M44`, row vectors), into a world with Y up and the plate at Y = 0;
//! - `Supports`: in that world, each with `ControlPoints` from the tip to the foot and a radius for each point.
//!
//! The supports carry no triangles, so their shapes are built here. The raft is not stored either: it is rebuilt
//! from the feet, which stand on a grid.

use std::collections::BTreeSet;
use std::io::Read;
use std::path::Path;

use base64::Engine;
use glam::{DMat4, DVec3};
use rayon::prelude::*;
use serde_json::Value;

use crate::error::{LoadError, Result};
use crate::io::lys::supports::{capsule, frustum, sphere};
use crate::io::{Y_UP_TO_Z_UP, stem};
use crate::mesh::Mesh;
use crate::scene::{ObjectKind, Scene, SceneObject};

/// The lowest raft thickness taken as "on the plate" when looking for feet (they end half a raft above the plate).
const MIN_FOOT_HEIGHT: f64 = 0.3;

/// One support, in this viewer's Z-up world.
struct Support {
    points: Vec<DVec3>,
    radii: Vec<f64>,
    /// A pillar inside a hollow model: it has neither a tip nor a foot.
    column: bool,
    tip_sphere: bool,
}

/// What the project says about all supports.
struct Style {
    tip_sphere: f64,
    /// Radius of the foot cones compared with the support (1 without reinforcement).
    foot: f64,
    raft_thickness: f64,
    /// Distance between the grid lines the feet stand on, if they are snapped to a grid.
    grid: Option<f64>,
}

/// Parse an `.ags` file; `path` names the file in messages and labels the objects.
pub fn load_bytes(data: &[u8], path: &Path, support_sides: u32) -> Result<Scene> {
    let bad = |what: String| LoadError::format(format!("{}: {what}", path.display()));
    let project: Value = serde_json::from_slice(data).map_err(|e| bad(format!("not a JSON project ({e})")))?;
    let supports_field = project.get("Supports").and_then(Value::as_array);
    let model_path = project.get("ModelPath").and_then(Value::as_str).unwrap_or_default();
    let packed = project.get("ModifiedMeshDataBase64Gz").and_then(Value::as_str).unwrap_or_default();
    if supports_field.is_none() && packed.is_empty() {
        return Err(bad("not a support project (no model and no supports)".into()));
    }
    if packed.is_empty() {
        return Err(bad("the model is not stored in the project".into()));
    }
    let mesh = unpack_mesh(packed).map_err(bad)?;

    let number = |key: &str, default: f64| project.get(key).and_then(Value::as_f64).unwrap_or(default);
    let flag = |key: &str, default: bool| project.get(key).and_then(Value::as_bool).unwrap_or(default);
    let style = Style {
        tip_sphere: if flag("UseTipSphere", true) { number("TipSphereMultiplier", 1.0) } else { 0.0 },
        foot: if flag("ReinforcementEnabled", false) {
            number("ReinforcementBaseRadiusPercent", 100.0) / 100.0
        } else {
            1.0
        },
        raft_thickness: number("RaftThickness", 0.0),
        grid: flag("EnableSnapToGrid", false).then(|| number("GridSize", 0.0)).filter(|g| *g > 0.0),
    };
    let supports: Vec<Support> = supports_field.into_iter().flatten().filter_map(read_support).collect();
    let support_mesh = build_supports(&supports, &style, support_sides.max(3) as usize);

    let name = if model_path.is_empty() { stem(path) } else { stem(Path::new(model_path)) };
    let mut model = SceneObject::new(name, mesh, path);
    model.transform = Y_UP_TO_Z_UP * matrix(project.get("ModelTransform"));
    let mut info = vec![("Supports".to_string(), supports.len().to_string())];
    if flag("IsHollowed", false) {
        let wall = number("HollowThickness", 0.0);
        info.push(("Hollowed".to_string(), format!("{wall:.2} mm wall (not shown)")));
    }
    if let Some(holes) = project.get("Holes").and_then(Value::as_array).filter(|h| !h.is_empty()) {
        info.push(("Holes".to_string(), holes.len().to_string()));
    }
    if !model_path.is_empty() {
        info.push(("Model file".to_string(), model_path.to_string()));
    }
    model.info = info;

    let mut scene = Scene::default();
    scene.objects.push(model);
    if !support_mesh.faces.is_empty() {
        let mut child = SceneObject::new("Supports", support_mesh, path);
        child.kind = ObjectKind::Support;
        child.parent = Some(0);
        scene.objects.push(child);
    }
    Ok(scene)
}

/// The model stored in the project.
fn unpack_mesh(packed: &str) -> std::result::Result<Mesh, String> {
    let gzipped = base64::engine::general_purpose::STANDARD
        .decode(packed)
        .map_err(|e| format!("the stored model is not valid Base64 ({e})"))?;
    let mut raw = Vec::new();
    flate2::read::GzDecoder::new(&gzipped[..])
        .read_to_end(&mut raw)
        .map_err(|e| format!("the stored model cannot be unpacked ({e})"))?;
    parse_mesh(&raw)
}

/// Vertices and triangles of an unpacked model (the normals after them are worked out again by the renderer).
fn parse_mesh(raw: &[u8]) -> std::result::Result<Mesh, String> {
    let short = || "the stored model is cut short".to_string();
    let count = |at: usize| -> std::result::Result<usize, String> {
        let bytes = raw.get(at..at.checked_add(4).ok_or_else(short)?).ok_or_else(short)?;
        Ok(u32::from_le_bytes(bytes.try_into().unwrap()) as usize)
    };
    let triple = |t: &[u8]| [0, 1, 2].map(|k| u32::from_le_bytes(t[k * 4..k * 4 + 4].try_into().unwrap()));
    let vertex_count = count(0)?;
    let indices_at = vertex_count.checked_mul(12).and_then(|n| n.checked_add(8)).ok_or_else(short)?;
    let index_count = count(indices_at - 4)?;
    let end = index_count.checked_mul(4).and_then(|n| n.checked_add(indices_at)).ok_or_else(short)?;
    if end > raw.len() || index_count % 3 != 0 {
        return Err(short());
    }
    let vertices: Vec<[f32; 3]> =
        raw[4..indices_at - 4].par_chunks_exact(12).map(|v| triple(v).map(f32::from_bits)).collect();
    let faces: Vec<[u32; 3]> = raw[indices_at..end].par_chunks_exact(12).map(triple).collect();
    if faces.par_iter().any(|f| f.iter().any(|&i| i as usize >= vertices.len())) {
        return Err("the stored model refers to a vertex that does not exist".into());
    }
    if faces.is_empty() {
        return Err("the stored model has no triangles".into());
    }
    Ok(Mesh::new(vertices, faces))
}

/// `ModelTransform`. Its rows are for row vectors, so they are the columns of this viewer's matrices.
fn matrix(value: Option<&Value>) -> DMat4 {
    let Some(value) = value else { return DMat4::IDENTITY };
    let mut cells = [0.0; 16];
    for (i, cell) in cells.iter_mut().enumerate() {
        let key = format!("M{}{}", i / 4 + 1, i % 4 + 1);
        match value.get(&key).and_then(Value::as_f64) {
            Some(number) if number.is_finite() => *cell = number,
            _ => return DMat4::IDENTITY,
        }
    }
    DMat4::from_cols_array(&cells)
}

/// A support record, its points turned to Z-up (`None` for one that cannot be drawn).
fn read_support(record: &Value) -> Option<Support> {
    let points: Vec<DVec3> = record.get("ControlPoints")?.as_array()?.iter().map(point).collect::<Option<_>>()?;
    let radii: Vec<f64> = record.get("Radii")?.as_array()?.iter().map(Value::as_f64).collect::<Option<_>>()?;
    let flag = |key: &str| record.get(key).and_then(Value::as_bool).unwrap_or(false);
    let usable = points.len() >= 2 && !radii.is_empty() && radii.iter().all(|r| r.is_finite());
    // "UseTIpSphere" is how the files spell it
    usable.then(|| Support { points, radii, column: flag("IsColumn"), tip_sphere: flag("UseTIpSphere") })
}

/// An `{X, Y, Z}` of the Y-up world, in this viewer's Z-up world.
fn point(value: &Value) -> Option<DVec3> {
    let axis = |key: &str| value.get(key)?.as_f64().filter(|v| v.is_finite());
    Some(DVec3::new(axis("X")?, -axis("Z")?, axis("Y")?))
}

impl Support {
    /// The radius at point `index` (a support can list fewer radii than points).
    fn radius(&self, index: usize) -> f64 {
        self.radii[index.min(self.radii.len() - 1)].abs().max(1e-3)
    }

    /// Where it stands on the plate, if it does.
    fn foot(&self, style: &Style) -> Option<DVec3> {
        let last = *self.points.last()?;
        (!self.column && last.z <= style.raft_thickness.max(MIN_FOOT_HEIGHT)).then_some(last)
    }

    /// Its shape: a capsule for each stretch between two points, a sphere on the tip, a wider end on the plate.
    fn pieces(&self, style: &Style, sides: usize) -> Vec<Mesh> {
        let rings = (sides / 2).max(2);
        let last = self.points.len() - 1;
        let on_plate = self.foot(style).is_some();
        let mut pieces = Vec::with_capacity(last + 1);
        for (i, ends) in self.points.windows(2).enumerate() {
            let at_foot = on_plate && i + 1 == last;
            let radius = self.radius(i + 1) * if at_foot { style.foot } else { 1.0 };
            pieces.push(capsule(ends[0], ends[1], self.radius(i), radius, false, at_foot, sides, rings, rings));
        }
        if self.tip_sphere && !self.column && style.tip_sphere > 0.0 {
            pieces.push(sphere(self.points[0], self.radius(0) * style.tip_sphere, sides));
        }
        pieces
    }
}

/// All supports and the raft under them as one mesh.
fn build_supports(supports: &[Support], style: &Style, sides: usize) -> Mesh {
    let mut pieces: Vec<Mesh> = supports.par_iter().flat_map_iter(|s| s.pieces(style, sides)).collect();
    pieces.extend(raft(supports, style, sides));
    Mesh::merge(pieces)
}

/// The raft: a pad under each foot and, between feet on neighbouring grid points, a bar (diagonal where no straight
/// route joins them). The projects do not store it, so this shows where it runs rather than its exact shape.
fn raft(supports: &[Support], style: &Style, sides: usize) -> Vec<Mesh> {
    let thickness = style.raft_thickness;
    if thickness <= 0.0 {
        return Vec::new();
    }
    // feet by hundredths of a millimetre: several supports can share one
    let feet: BTreeSet<(i64, i64)> = supports
        .iter()
        .filter_map(|s| s.foot(style))
        .map(|f| ((f.x * 100.0).round() as i64, (f.y * 100.0).round() as i64))
        .collect();
    let at = |foot: (i64, i64), z: f64| DVec3::new(foot.0 as f64 / 100.0, foot.1 as f64 / 100.0, z);
    let radius = supports.iter().find(|s| s.foot(style).is_some()).map_or(0.0, |s| s.radius(usize::MAX) * style.foot);
    let mut pieces: Vec<Mesh> =
        feet.iter().map(|&foot| frustum(at(foot, 0.0), radius, radius, thickness, sides * 2, 0.0)).collect();
    if let Some(grid) = style.grid {
        let step = (grid * 100.0).round() as i64;
        for &foot in &feet {
            let (right, up) = ((foot.0 + step, foot.1), (foot.0, foot.1 + step));
            let (up_right, up_left) = ((foot.0 + step, foot.1 + step), (foot.0 - step, foot.1 + step));
            let open = |a: (i64, i64), b: (i64, i64)| !feet.contains(&a) && !feet.contains(&b);
            let diagonals = [(up_right, open(right, up)), (up_left, open((foot.0 - step, foot.1), up))];
            for (next, wanted) in [(right, true), (up, true), diagonals[0], diagonals[1]] {
                if wanted && feet.contains(&next) {
                    let (a, b) = (at(foot, thickness / 2.0), at(next, thickness / 2.0));
                    pieces.push(capsule(a, b, thickness / 2.0, thickness / 2.0, true, true, sides, 1, 1));
                }
            }
        }
    }
    pieces
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;

    /// A one-triangle model as a project stores it.
    fn packed_triangle() -> String {
        let mut raw: Vec<u8> = 3u32.to_le_bytes().to_vec();
        [0f32, 0., 0., 2., 0., 0., 0., 2., 0.].iter().for_each(|v| raw.extend(v.to_le_bytes()));
        raw.extend(3u32.to_le_bytes());
        [0u32, 1, 2].iter().for_each(|i| raw.extend(i.to_le_bytes()));
        raw.extend(3u32.to_le_bytes());
        raw.extend([0u8; 36]); // the normals
        let mut gzip = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
        gzip.write_all(&raw).unwrap();
        base64::engine::general_purpose::STANDARD.encode(gzip.finish().unwrap())
    }

    fn project(extra: &str) -> Vec<u8> {
        let transform: Vec<String> = (0..16)
            .map(|i| {
                let value = match i {
                    0 | 5 | 10 | 15 => 1.0,
                    13 => 5.0, // M42: 5 up in the Y-up world
                    _ => 0.0,
                };
                format!("\"M{}{}\": {value}", i / 4 + 1, i % 4 + 1)
            })
            .collect();
        format!(
            r#"{{"ModelPath": "Part.stl", "ModifiedMeshDataBase64Gz": "{}", "ModelTransform": {{{}}}, {extra}
               "RaftThickness": 0.5, "GridSize": 4, "EnableSnapToGrid": true, "ReinforcementEnabled": true,
               "ReinforcementBaseRadiusPercent": 150, "TipSphereMultiplier": 1.13, "UseTipSphere": true}}"#,
            packed_triangle(),
            transform.join(", ")
        )
        .into_bytes()
    }

    const SUPPORTS: &str = r#""Supports": [
        {"ControlPoints": [{"X": 0, "Y": 5, "Z": 0}, {"X": 0, "Y": 1.25, "Z": 0}, {"X": 0, "Y": 0.25, "Z": 0}],
         "Radii": [0.2, 0.5, 0.5], "UseTIpSphere": true, "IsColumn": false},
        {"ControlPoints": [{"X": 4, "Y": 5, "Z": 0}, {"X": 4, "Y": 0.25, "Z": 0}],
         "Radii": [0.2, 0.5], "UseTIpSphere": true, "IsColumn": false},
        {"ControlPoints": [{"X": 1, "Y": 9, "Z": 1}, {"X": 1, "Y": 7, "Z": 1}],
         "Radii": [0.5, 0.5], "UseTIpSphere": true, "IsColumn": true}],"#;

    #[test]
    fn model_is_placed_and_supports_stand_on_the_plate_with_z_up() {
        let scene = load_bytes(&project(SUPPORTS), Path::new("p.ags"), 8).unwrap();
        assert_eq!(scene.objects.len(), 2);
        let model = &scene.objects[0];
        assert_eq!((model.name.as_str(), model.mesh.n_faces()), ("Part", 1));
        // a vertex at Y = 2 in the model is 7 above the plate: Y up becomes Z up
        let top = model.transform.transform_point3(DVec3::new(0.0, 2.0, 0.0));
        assert!(top.distance(DVec3::new(0.0, 0.0, 7.0)) < 1e-9);
        let supports = &scene.objects[1];
        assert_eq!((supports.kind, supports.parent), (ObjectKind::Support, Some(0)));
        let (lo, hi) = supports.mesh.bounds().unwrap();
        assert!(lo[2].abs() < 1e-4, "the raft lies on the plate, got {}", lo[2]);
        assert!(hi[2] > 9.0, "the column is there, got {}", hi[2]);
    }

    #[test]
    fn feet_get_pads_and_neighbours_a_bar() {
        let scene = load_bytes(&project(SUPPORTS), Path::new("p.ags"), 8).unwrap();
        let supports: Vec<Support> = vec![
            Support { points: vec![DVec3::Z, DVec3::Z * 0.25], radii: vec![0.5], column: false, tip_sphere: false },
            Support {
                points: vec![DVec3::new(4.0, 0.0, 1.0), DVec3::new(4.0, 0.0, 0.25)],
                radii: vec![0.5],
                column: false,
                tip_sphere: false,
            },
            Support {
                points: vec![DVec3::new(9.0, 0.0, 1.0), DVec3::new(9.0, 0.0, 0.25)],
                radii: vec![0.5],
                column: false,
                tip_sphere: false,
            },
        ];
        let style = Style { tip_sphere: 0.0, foot: 1.5, raft_thickness: 0.5, grid: Some(4.0) };
        assert_eq!(raft(&supports, &style, 8).len(), 3 + 1); // three pads, one bar: the last foot is off the grid
        assert!(scene.objects[1].mesh.n_faces() > 0);
        let at = |x: f64, y: f64| Support {
            points: vec![DVec3::new(x, y, 1.0), DVec3::new(x, y, 0.25)],
            radii: vec![0.5],
            column: false,
            tip_sphere: false,
        };
        assert_eq!(raft(&[at(0.0, 0.0), at(4.0, 4.0)], &style, 8).len(), 2 + 1); // a diagonal joins them
        // with a foot at the corner they are joined the straight way: no diagonal
        assert_eq!(raft(&[at(0.0, 0.0), at(4.0, 0.0), at(4.0, 4.0)], &style, 8).len(), 3 + 2);
    }

    #[test]
    fn broken_projects_are_reported() {
        assert!(load_bytes(b"{}", Path::new("p.ags"), 8).unwrap_err().to_string().contains("not a support project"));
        assert!(load_bytes(b"not json", Path::new("p.ags"), 8).is_err());
        let external = br#"{"Supports": [], "ModelPath": "../secret.stl"}"#;
        assert!(load_bytes(external, Path::new("p.ags"), 8).unwrap_err().to_string().contains("not stored in the project"));
        assert!(parse_mesh(&[255, 255, 255, 255]).unwrap_err().contains("cut short"));
    }
}
