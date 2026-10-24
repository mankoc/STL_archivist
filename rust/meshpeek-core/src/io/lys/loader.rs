//! Turn a `.lys` archive into a Scene: models with their Lychee placement, supports, build plate.

use std::collections::HashMap;
use std::path::Path;

use glam::{DMat3, DMat4, DVec3, DVec4};
use rmpv::Value;

use super::container::Archive;
use super::raft::{self, Footprint, Foundation, Kind, Placement, RaftFoot};
use super::value::{self, get, ids, index, path, str_of, vec3};
use super::{codec, mesh_bin, supports};
use crate::error::{LoadError, Result};
use crate::io::{Data, map_file};
use crate::mesh::Mesh;
use crate::scene::{ObjectKind, Plate, Scene, SceneObject};

/// Object -> world for a mesh centred on its origin (as Lychee stores it).
///
/// Rotation is Euler XYZ in degrees about the mesh origin, applied about X, then Y, then Z (`R = Rz * Ry * Rx`;
/// checked against a file rotated about all three axes, where the support tips only fit this order). `position` x/y place that origin;
/// `position.z` is the height of the object's lowest point after rotation (the support lift).
/// `center` is the part's centre in its original file and is not used for placement.
pub fn object_matrix(obj: &Value, vertices: &[[f32; 3]]) -> DMat4 {
    let r = vec3(get(obj, "rotation"), 0.0) * (std::f64::consts::PI / 180.0);
    let linear = DMat3::from_rotation_z(r.z)
        * DMat3::from_rotation_y(r.y)
        * DMat3::from_rotation_x(r.x)
        * DMat3::from_diagonal(vec3(get(obj, "scale"), 1.0));
    let mut translation = vec3(get(obj, "position"), 0.0);
    let row_z = linear.row(2);
    let lowest = vertices
        .iter()
        .map(|v| row_z.dot(DVec3::new(v[0] as f64, v[1] as f64, v[2] as f64)))
        .fold(f64::INFINITY, f64::min);
    if lowest.is_finite() {
        translation.z -= lowest;
    }
    DMat4::from_cols(
        linear.x_axis.extend(0.0),
        linear.y_axis.extend(0.0),
        linear.z_axis.extend(0.0),
        DVec4::from((translation, 1.0)),
    )
}

/// `Ok(mesh)` or `Err(reason it was skipped)`.
fn object_mesh(archive: &mut Archive, obj: &Value) -> std::result::Result<Mesh, String> {
    let props = get(obj, "properties");
    let hash = props.and_then(|p| str_of(p, "hash")).unwrap_or("");
    let kind = str_of(obj, "type").unwrap_or("");
    if kind == "libraryGeometry" || hash.ends_with(".lyd") {
        return Err("Lychee Library model (.lyd), not supported".into());
    }
    if kind == "libraryFile" {
        // Geometry lives outside the archive, e.g. Lychee's bundled benchy.stl.
        let file = props.and_then(|p| str_of(p, "file")).unwrap_or("");
        if file.is_empty() || !Path::new(file).exists() {
            return Err(format!("external file not found: {file}"));
        }
        let mut mesh = crate::io::load_mesh(Path::new(file)).map_err(|e| e.to_string())?;
        if let Some((lo, hi)) = mesh.bounds() {
            let centre: [f32; 3] = std::array::from_fn(|i| (lo[i] + hi[i]) / 2.0); // centre it like embedded meshes
            mesh.vertices.iter_mut().for_each(|v| (0..3).for_each(|i| v[i] -= centre[i]));
        }
        return Ok(mesh);
    }
    let member = format!("{hash}.bin");
    if !archive.contains(&member) {
        return Err(format!("mesh {member} missing from archive"));
    }
    let raw = archive.read(&member).map_err(|e| e.to_string())?;
    mesh_bin::parse(&raw).map_err(|e| e.to_string())
}

/// The raft of one object (`None` when its `foundation` is off or the raft is empty).
fn object_raft(object: Option<&Value>, mesh: &Mesh, feet: Option<Vec<RaftFoot>>, scene_raft: bool) -> Option<Mesh> {
    let foundation = Foundation::of(object, scene_raft);
    if !foundation.enabled {
        return None;
    }
    // The raft is built in the object's frame without its yaw, then turned by the yaw about its position.
    let rotation = vec3(object.and_then(|o| get(o, "rotation")), 0.0) * (std::f64::consts::PI / 180.0);
    let scale = vec3(object.and_then(|o| get(o, "scale")), 1.0);
    let to_local = DMat4::from_mat3(
        DMat3::from_rotation_y(rotation.y) * DMat3::from_rotation_x(rotation.x) * DMat3::from_diagonal(scale),
    );
    let position = vec3(object.and_then(|o| get(o, "position")), 0.0);
    let at = Placement { position: glam::DVec2::new(position.x, position.y), yaw: rotation.z };
    let cell = match foundation.kind {
        Kind::Vectoriel => Some((foundation.precision / 5.0).max(0.1)),
        Kind::Pixelate | Kind::PixelateRound => Some(foundation.precision),
        _ => None,
    };
    let feet = feet.unwrap_or_default();
    // with `baseSupport` Lychee paints a disc around every plate support's foot into the silhouette
    let step = (foundation.precision / 5.0).max(0.1);
    let discs: Vec<(glam::DVec2, f64)> = if foundation.base_support {
        feet.iter()
            .map(|f| {
                let from_object = glam::DVec2::new(f.position.x, f.position.y) - at.position;
                (glam::DMat2::from_angle(-at.yaw) * from_object, (f.reach / step).ceil() * step)
            })
            .collect()
    } else {
        Vec::new()
    };
    let footprint = if foundation.kind == Kind::Triangle { None } else { Footprint::of(mesh, &to_local, cell, &discs) };
    let raft = raft::build(&foundation, &feet, footprint.as_ref(), at);
    (raft.n_faces() > 0).then_some(raft)
}

/// Load a `.lys` scene: its models, their supports (with `support_sides` facets round) and rafts, and the
/// build plate.
pub fn load(file: &Path, support_sides: u32) -> Result<Scene> {
    load_data(file, Data::Mapped(map_file(file)?), support_sides)
}

/// Like [`load`] for contents already in memory; `file` only labels the objects.
pub fn load_data(file: &Path, data: Data, support_sides: u32) -> Result<Scene> {
    let mut archive = Archive::open(data)?;
    if !archive.contains("scene.bin") {
        return Err(LoadError::format("scene.bin missing from archive"));
    }
    let ciphered = !archive.is_zip();
    let state = codec::decode(&archive.read("scene.bin")?, ciphered)?;

    let objects = path(&state, "objects.present");
    let by_id = index(objects.and_then(|o| get(o, "byId")));
    let mut scene = Scene::default();
    let mut placed: HashMap<&str, DMat4> = HashMap::new(); // object id -> transform, for loaded objects
    let mut models: Vec<(&str, SceneObject)> = Vec::new();
    for oid in ids(objects.and_then(|o| get(o, "allIds"))) {
        let Some(&obj) = by_id.get(oid) else { continue };
        let name = str_of(obj, "name").filter(|n| !n.is_empty()).unwrap_or(oid).to_string();
        match object_mesh(&mut archive, obj) {
            Err(reason) => scene.skipped.push(format!("{name}: {reason}")),
            Ok(mesh) => {
                let transform = object_matrix(obj, &mesh.vertices);
                placed.insert(oid, transform);
                let mut object = SceneObject::new(name, mesh, file);
                object.transform = transform;
                object.visible = get(obj, "visible").and_then(Value::as_bool).unwrap_or(true);
                models.push((oid, object));
            }
        }
    }

    let support_state = path(&state, "supports.present");
    let raft = path(&state, "settings.batch.msla.raft.enabled").and_then(Value::as_bool).unwrap_or(false);
    let (mut support_meshes, mut feet) = supports::build(support_state, &placed, &by_id, support_sides, raft);
    for (oid, model) in models {
        let mut support_mesh = support_meshes.remove(oid);
        if let Some(raft_mesh) = object_raft(by_id.get(oid).copied(), &model.mesh, feet.remove(oid), raft) {
            support_mesh = Some(Mesh::merge(support_mesh.into_iter().chain([raft_mesh])));
        }
        scene.objects.push(model);
        if let Some(mesh) = support_mesh {
            let mut support = SceneObject::new("Supports", mesh, file);
            support.kind = ObjectKind::Support;
            support.parent = Some(scene.objects.len() - 1);
            scene.objects.push(support);
        }
    }

    let plates = path(&state, "plates.present.byId").and_then(Value::as_map);
    for (_, plate) in plates.into_iter().flatten() {
        let visible = get(plate, "visible").and_then(Value::as_bool).unwrap_or(true);
        if visible && !value::truthy(plate, "isVirtual") {
            scene.plates.push(Plate {
                size: vec3(get(plate, "size"), 0.0).to_array(),
                position: vec3(get(plate, "position"), 0.0).to_array(),
            });
        }
    }
    Ok(scene)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(pairs: Vec<(&str, Value)>) -> Value {
        Value::Map(pairs.into_iter().map(|(k, v)| (Value::from(k), v)).collect())
    }

    fn xyz(x: f64, y: f64, z: f64) -> Value {
        map(vec![("x", x.into()), ("y", y.into()), ("z", z.into())])
    }

    #[test]
    fn object_matrix_places_lowest_point_at_position_z() {
        let vertices = [[-1.0f32, -1.0, -1.0], [1.0, 1.0, 1.0]];
        let obj =
            map(vec![("position", xyz(10., 20., 5.)), ("rotation", xyz(0., 90., 0.)), ("center", xyz(99., 99., 99.))]);
        let m = object_matrix(&obj, &vertices);
        let world: Vec<DVec3> =
            vertices.iter().map(|v| m.transform_point3(DVec3::new(v[0] as f64, v[1] as f64, v[2] as f64))).collect();
        let lowest = world.iter().map(|p| p.z).fold(f64::INFINITY, f64::min);
        assert!((lowest - 5.0).abs() < 1e-9); // lift height
        let mean = (world[0] + world[1]) / 2.0;
        assert!((mean.x - 10.0).abs() < 1e-6 && (mean.y - 20.0).abs() < 1e-6); // `center` is ignored
    }
}
