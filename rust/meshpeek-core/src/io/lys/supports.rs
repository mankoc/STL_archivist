//! Support geometry from Lychee's parametric support records, ported from Lychee's own `SupportTip.ts`,
//! `SupportBase.ts`, `SupportMiddle.ts` and `CapsuleBufferGeometry.ts` (see `docs/formats/LYS_FILE_FORMAT.md`, section 9.3).
//!
//! A support is a chain of **capsules** (the hull of two spheres whose centres are the chain's points, an end
//! optionally cut flat):
//!
//! * the *tip*: from the contact point on the model to the tip join, `settings.tip.length` along the tip normal;
//! * the *middle*: a straight capsule from the base join to the tip join;
//! * the *base*: a vertical column from the plate up to the base join (plate supports), with a ball at the join,
//!   a foot pad and a join cone; or, for a branch (`isBaseTip`), a base cone from its start point (on the model or on
//!   a parent support) to the base join.
//!
//! "Mini" supports and braces use short cones (`PERCENT_LENGTH_BRACING` of the straight length, at most
//! `MAX_LENGTH_BRACING`) and thin radii. Coordinates (`tip`, `base` of branches) are in the object's space and are
//! placed with its matrix; plate `base` points are at plate height with x/y turned by the object's Z rotation.
//! Rafts are built in `raft.rs`; with a raft the plate supports lose their foot pad and keep only their join cone.

use std::collections::HashMap;
use std::f64::consts::PI;

use glam::{DMat4, DVec3};
use rmpv::Value;

use super::raft::{self, Foundation, RaftFoot};
use super::value::{get, ids, index, number, str_of, vec3};
use crate::mesh::Mesh;

/// Default facets around each support column.
pub const SIDES: u32 = 12;

/// Lychee's `Support.MAX_LENGTH_BRACING` and `PERCENT_LENGTH_BRACING`.
const MAX_LENGTH_BRACING: f64 = 2.0;
const PERCENT_LENGTH_BRACING: f64 = 0.33;

/// `v` normalised, or `fallback` when it is (nearly) zero.
fn unit(v: DVec3, fallback: DVec3) -> DVec3 {
    let length = v.length();
    if length > 1e-9 { v / length } else { fallback }
}

/// A position as a mesh vertex.
fn to_f32(p: DVec3) -> [f32; 3] {
    [p.x as f32, p.y as f32, p.z as f32]
}

// --- primitives ---------------------------------------------------------------------------------------------

/// Lychee's capsule: the hull of a sphere of radius `r_a` centred on `a` and one of radius `r_b` centred on `b`.
/// An end can be cut flat at its centre (`a_flat`, `b_flat`). `sides` facets around, `rings_a` / `rings_b` rings
/// on the caps.
#[allow(clippy::too_many_arguments)]
pub fn capsule(
    a: DVec3,
    b: DVec3,
    r_a: f64,
    r_b: f64,
    a_flat: bool,
    b_flat: bool,
    sides: usize,
    rings_a: usize,
    rings_b: usize,
) -> Mesh {
    capsule_twisted(a, b, r_a, r_b, a_flat, b_flat, sides, rings_a, rings_b, 0.0)
}

/// [`capsule`] with the corners turned by `twist` radians about the axis (Lychee turns 4-sided ones by 45 degrees).
#[allow(clippy::too_many_arguments)]
fn capsule_twisted(
    a: DVec3,
    b: DVec3,
    r_a: f64,
    r_b: f64,
    a_flat: bool,
    b_flat: bool,
    sides: usize,
    rings_a: usize,
    rings_b: usize,
    twist: f64,
) -> Mesh {
    let height = (b - a).length().max(1e-4);
    let axis = unit(b - a, DVec3::NEG_Z);
    let helper = if axis.z.abs() < 0.9 { DVec3::Z } else { DVec3::X };
    let u = axis.cross(helper).normalize();
    let v = axis.cross(u);
    let ratio = (r_b - r_a) / height;
    let alpha = if ratio.abs() <= 1.0 { ratio.acos() } else { 0.0 };
    let (sin_alpha, cos_alpha) = alpha.sin_cos();
    let half = height / 2.0;
    let (rings_a, rings_b) = (if a_flat { 1 } else { rings_a.max(1) }, if b_flat { 1 } else { rings_b.max(1) });

    // rows of (distance from the middle towards `a`, ring radius), top (a) to bottom (b)
    let mut rows: Vec<(f64, f64)> = Vec::new();
    for i in 0..=rings_a {
        let angle = PI / 2.0 - alpha * (i as f64 / rings_a as f64);
        rows.push((if a_flat { half } else { half + angle.sin() * r_a }, angle.cos() * r_a));
    }
    let cone_height = height + cos_alpha * r_a - cos_alpha * r_b;
    let cone_y = if a_flat || b_flat { half - cone_height } else { half + cos_alpha * r_a - cone_height };
    rows.push((cone_y, sin_alpha * r_b));
    for i in 1..=rings_b {
        let angle = PI / 2.0 - alpha - (PI - alpha) * (i as f64 / rings_b as f64);
        rows.push((if b_flat { -half } else { -half + angle.sin() * r_b }, angle.cos() * r_b));
    }

    let middle = (a + b) / 2.0;
    let mut mesh = Mesh::default();
    for &(y, radius) in &rows {
        for k in 0..sides {
            let theta = 2.0 * PI * k as f64 / sides as f64 + twist;
            let p = middle + axis * -y + (u * theta.cos() + v * theta.sin()) * radius;
            mesh.vertices.push(to_f32(p));
        }
    }
    let n = sides as u32;
    for row in 0..rows.len() as u32 - 1 {
        for k in 0..n {
            let next = (k + 1) % n;
            let (p00, p01) = (row * n + k, row * n + next);
            let (p10, p11) = (p00 + n, p01 + n);
            for tri in [[p00, p01, p10], [p01, p11, p10]] {
                let c = tri.map(|i| DVec3::from_array(mesh.vertices[i as usize].map(f64::from)));
                if (c[1] - c[0]).cross(c[2] - c[0]).length_squared() > 1e-18 {
                    mesh.faces.push(tri);
                }
            }
        }
    }
    mesh
}

/// The 12 corners of Lychee's cross profile for a "radius" 1, counter-clockwise.
fn cross_profile() -> Vec<glam::DVec2> {
    let t = 1.0 / 3.0;
    [
        (t, -1.0),
        (t, -t),
        (1.0, -t),
        (1.0, t),
        (t, t),
        (t, 1.0),
        (-t, 1.0),
        (-t, t),
        (-1.0, t),
        (-1.0, -t),
        (-t, -t),
        (-t, -1.0),
    ]
    .iter()
    .map(|&(x, y)| glam::DVec2::new(x, y))
    .collect()
}

/// Closed prism along `p0 -> p1`: `profile` (counter-clockwise, star-shaped about its origin) scaled by `s0` at `p0`
/// and `s1` at `p1`, with flat caps. Lychee's extruded `cross` and `cube` shapes.
fn prism(p0: DVec3, p1: DVec3, profile: &[glam::DVec2], s0: f64, s1: f64) -> Mesh {
    let axis = unit(p1 - p0, DVec3::NEG_Z);
    let helper = if axis.z.abs() < 0.9 { DVec3::Z } else { DVec3::X };
    let u = axis.cross(helper).normalize();
    let v = axis.cross(u);
    let n = profile.len() as u32;
    let at = |p: DVec3, q: glam::DVec2, s: f64| to_f32(p + (u * q.x + v * q.y) * s);
    let mut mesh = Mesh::default();
    mesh.vertices.extend(profile.iter().map(|&q| at(p0, q, s0)));
    mesh.vertices.extend(profile.iter().map(|&q| at(p1, q, s1)));
    mesh.vertices.push(to_f32(p0));
    mesh.vertices.push(to_f32(p1));
    for i in 0..n {
        let j = (i + 1) % n;
        mesh.faces.extend([[i, j, n + j], [i, n + j, n + i]]);
        mesh.faces.push([2 * n, j, i]);
        mesh.faces.push([2 * n + 1, n + i, n + j]);
    }
    mesh
}

/// A regular polygon with `n` corners at `radius`, the first at `start`, counter-clockwise.
fn polygon(n: usize, radius: f64, start: f64) -> Vec<glam::DVec2> {
    (0..n)
        .map(|k| {
            let a = start + 2.0 * PI * k as f64 / n as f64;
            glam::DVec2::new(a.cos(), a.sin()) * radius
        })
        .collect()
}

/// The point end of a support's tip (or of a branch's base): a cone-ended capsule (`cone`), a 4-sided one (`cube`),
/// a capsule of constant radius (`cylinder`) or an extruded cross (`cross`, with a ball at the join).
/// `point_end` and `join_end` are the end centres; `ball` adds the join sphere of the cross type.
#[allow(clippy::too_many_arguments)]
fn terminal(
    kind: &str,
    point_end: DVec3,
    join_end: DVec3,
    point_radius: f64,
    radius: f64,
    point_flat: bool,
    join_flat: bool,
    sides: usize,
    rings_point: usize,
    rings_join: usize,
    ball: bool,
) -> Vec<Mesh> {
    match kind {
        "cube" => vec![capsule_twisted(
            point_end,
            join_end,
            point_radius,
            radius,
            point_flat,
            join_flat,
            4,
            rings_point,
            rings_join,
            -PI / 4.0,
        )],
        "cylinder" => vec![capsule(
            point_end,
            join_end,
            point_radius,
            point_radius,
            point_flat,
            join_flat,
            sides,
            rings_point,
            rings_join,
        )],
        "cross" => {
            let mut parts = vec![prism(join_end, point_end, &cross_profile(), radius, point_radius)];
            if ball {
                parts.push(sphere(join_end, radius, sides));
            }
            parts
        }
        _ => vec![capsule(
            point_end,
            join_end,
            point_radius,
            radius,
            point_flat,
            join_flat,
            sides,
            rings_point,
            rings_join,
        )],
    }
}

/// A sphere (a capsule with both centres together).
pub(crate) fn sphere(centre: DVec3, radius: f64, sides: usize) -> Mesh {
    capsule(centre, centre + DVec3::Z * 1e-4, radius, radius, false, false, sides, sides / 2, sides / 2)
}

/// Pad or cone standing on height `z` (plate-parallel): `bottom` radius at `z`, `top` radius at `z + height`.
fn frustum(centre: DVec3, bottom: f64, top: f64, height: f64, sides: usize, start: f64) -> Mesh {
    let flat = |r: f64| raft::circle(glam::DVec2::new(centre.x, centre.y), r.max(1e-4), sides, start);
    raft::loft(&flat(bottom), centre.z, &flat(top), centre.z + height.max(1e-4))
}

// --- support data -------------------------------------------------------------------------------------------

/// The numbers of one support's `settings` that the geometry uses.
#[derive(Clone, Debug, Default)]
struct Settings {
    base_diameter: f64,
    base_length: f64,
    base_join_length: f64,
    base_join_diameter: f64,
    base_join_cone: f64,
    base_angle: f64,
    base_type: String,
    base_tip_diameter: f64,
    base_tip_point_diameter: f64,
    base_tip_length: f64,
    base_tip_angle: f64,
    base_tip_penetration: f64,
    base_tip_type: String,
    tip_diameter: f64,
    tip_point_diameter: f64,
    tip_length: f64,
    tip_angle: f64,
    tip_penetration: f64,
    tip_type: String,
    tip_break_point: f64,
    base_tip_break_point: f64,
    mid_type: String,
}

impl Settings {
    /// Read a support record's `settings`, with Lychee's defaults for missing values.
    fn of(support: &Value) -> Self {
        let settings = get(support, "settings");
        let part = |name: &str| settings.and_then(|s| get(s, name));
        let n = |part: Option<&Value>, key: &str, default: f64| part.map_or(default, |p| number(p, key, default));
        let kind =
            |part: Option<&Value>, default: &str| part.and_then(|p| str_of(p, "type")).unwrap_or(default).to_string();
        let (base, base_tip, tip, mid) = (part("base"), part("baseTip"), part("tip"), part("mid"));
        Self {
            base_diameter: n(base, "diameter", 6.0),
            base_length: n(base, "length", 0.5),
            base_join_length: n(base, "joinLength", 0.0),
            base_join_diameter: n(base, "joinDiameter", 1.0),
            base_join_cone: n(base, "joinCone", 0.0),
            base_angle: n(base, "angle", 0.0),
            base_type: kind(base, "cylinder"),
            base_tip_diameter: n(base_tip, "diameter", 1.0),
            base_tip_point_diameter: n(base_tip, "pointDiameter", 0.4),
            base_tip_length: n(base_tip, "length", 1.0),
            base_tip_angle: n(base_tip, "angle", 100.0),
            base_tip_penetration: n(base_tip, "penetration", 0.0),
            base_tip_type: kind(base_tip, "cone"),
            tip_diameter: n(tip, "diameter", 1.0),
            tip_point_diameter: n(tip, "pointDiameter", 0.4),
            tip_length: n(tip, "length", 3.0),
            tip_angle: n(tip, "angle", 100.0),
            tip_penetration: n(tip, "penetration", 0.0),
            tip_type: kind(tip, "cone"),
            tip_break_point: n(tip, "breakPoint", 0.0),
            base_tip_break_point: n(base_tip, "breakPoint", 0.0),
            mid_type: kind(mid, "cylinder"),
        }
    }
}

/// One support with its positions worked out (Lychee's `Support`, `SupportTip` and `SupportBase` state).
struct Prepared<'a> {
    tip_object: &'a str,
    s: Settings,
    mini: bool,
    branch: bool,
    parent_base: Option<&'a str>,
    parent_tip: Option<&'a str>,
    /// Lychee's `isBracing`: between two supports.
    bracing: bool,
    tip_point: DVec3,
    tip_normal: DVec3,
    tip_join: DVec3,
    /// Plate supports: the foot on the plate. Branches: where the branch starts.
    base_point: DVec3,
    base_join: DVec3,
    /// Direction the base cone of a branch points in.
    base_orientation: DVec3,
    length_join: f64,
    /// Straight distance from base to tip.
    total_length: f64,
    /// Id of the object a plate support stands on.
    base_object: &'a str,
}

/// `Support.smallestDiameterToBraceOn`: the diameter of a support at height `z`, for a brace to match.
fn smallest_diameter(parent: &Prepared, z: Option<f64>) -> f64 {
    let tip = parent.s.tip_diameter;
    let base = if parent.branch { parent.s.base_tip_diameter } else { parent.s.base_join_diameter };
    if let Some(z) = z {
        if z < parent.base_join.z {
            return base;
        } else if z > parent.tip_point.z {
            return tip;
        }
        let rise = parent.tip_point.z - parent.base_join.z;
        if rise.abs() > 1e-9 {
            return base + (tip - base) / rise * (z - parent.base_join.z);
        }
    }
    base.min(tip)
}

/// World-space support meshes per model (tip object id -> all visible supports touching that object), and the
/// plate feet of each, for the rafts.
///
/// `transforms` holds the placed objects' matrices; `objects` their Lychee records (position, rotation, raft).
pub fn build(
    supports: Option<&Value>,
    transforms: &HashMap<&str, DMat4>,
    objects: &HashMap<&str, &Value>,
    sides: u32,
    raft_default: bool,
) -> (HashMap<String, Mesh>, HashMap<String, Vec<RaftFoot>>) {
    let Some(supports) = supports else { return (HashMap::new(), HashMap::new()) };
    let by_id = index(get(supports, "byId"));
    let sides = sides.max(3) as usize;

    // pass 1: positions of every support (parents are looked up by the second pass)
    let mut prepared: HashMap<&str, Prepared> = HashMap::new();
    let mut order: Vec<&str> = Vec::new();
    for sid in ids(get(supports, "allIds")) {
        let Some(&s) = by_id.get(sid) else { continue };
        let Some(tip_object) = str_of(s, "objectIdTip") else { continue };
        let Some(tip_matrix) = transforms.get(tip_object) else { continue };
        if get(s, "isVisible").and_then(Value::as_bool) == Some(false) {
            continue;
        }
        let base_object = str_of(s, "objectIdBase").filter(|id| transforms.contains_key(id)).unwrap_or(tip_object);
        let settings = Settings::of(s);
        let flag = |key: &str| get(s, key).and_then(Value::as_bool).unwrap_or(false);
        let id_of = |key: &str| str_of(s, key).filter(|v| !v.is_empty());
        let (branch, mini) = (flag("isBaseTip"), flag("mini"));
        let (parent_base, parent_tip) = (id_of("parentBaseId"), id_of("parentTipId"));
        let bracing = matches!((parent_base, parent_tip), (Some(a), Some(b)) if a != b);

        let tip_point = tip_matrix.transform_point3(vec3(get(s, "tip"), 0.0));
        let tip_normal0 = unit(tip_matrix.transform_vector3(vec3(get(s, "tipNormal"), 0.0)), -DVec3::Z);
        let base_matrix = transforms[base_object];
        let (base_point, base_normal0) = if branch {
            (
                base_matrix.transform_point3(vec3(get(s, "base"), 0.0)),
                unit(base_matrix.transform_vector3(vec3(get(s, "baseNormal"), 0.0)), DVec3::Z),
            )
        } else {
            // The support grid is laid out in the object's yaw-turned frame: x/y are turned by the Z rotation.
            let record = objects.get(base_object).copied();
            let position = record.map_or(DVec3::ZERO, |o| vec3(get(o, "position"), 0.0));
            let yaw = record.map_or(0.0, |o| vec3(get(o, "rotation"), 0.0).z.to_radians());
            let base = vec3(get(s, "base"), 0.0);
            let (sin, cos) = yaw.sin_cos();
            (
                DVec3::new(base.x * cos - base.y * sin, base.x * sin + base.y * cos, base.z)
                    + position * DVec3::new(1.0, 1.0, 0.0),
                DVec3::Z,
            )
        };
        // A brace runs straight from one support to the other whatever normals are stored (Lychee recomputes them).
        let (tip_normal0, base_normal0) = if bracing {
            let along = unit(base_point - tip_point, DVec3::Z);
            (along, -along)
        } else {
            (tip_normal0, base_normal0)
        };
        let total_length = tip_point.distance(base_point);
        let short = (total_length * PERCENT_LENGTH_BRACING).min(MAX_LENGTH_BRACING);

        let (tip_normal, tip_join, base_orientation, length_join, base_join);
        if mini && branch {
            // a mini branch points straight at its tip; the tip points back at the base join
            base_orientation = unit(tip_point - base_point, DVec3::Z);
            length_join = short;
            base_join = base_point + base_orientation * length_join;
            tip_normal = unit(base_join - tip_point, DVec3::Z);
            tip_join = tip_point + tip_normal * short;
        } else {
            let mut normal = if bracing {
                tip_normal0
            } else {
                let lean = (100.0 - settings.tip_angle) / 100.0;
                unit(tip_normal0 + (-DVec3::Z - tip_normal0) * lean, tip_normal0)
            };
            let length = if bracing || mini { short } else { settings.tip_length };
            let mut join = tip_point + normal * length;
            if join.z < settings.tip_diameter {
                // the join must not dip into the plate
                let radius = settings.tip_diameter / 2.0 + 0.1;
                if join.z < radius {
                    join.z = radius;
                    normal = unit(join - tip_point, normal);
                }
            }
            tip_normal = normal;
            tip_join = join;
            if branch {
                let mut orientation = base_normal0;
                if parent_base.is_none() {
                    let to_tip = unit(tip_point - base_point, base_normal0);
                    let lean = (100.0 - settings.base_tip_angle) / 100.0;
                    orientation = unit(base_normal0 + (to_tip - base_normal0) * lean, base_normal0);
                }
                base_orientation = orientation;
                length_join = if bracing { short } else { settings.base_tip_length };
                base_join = base_point + orientation * length_join;
            } else {
                base_orientation = DVec3::Z;
                length_join = tip_join.z.min(settings.base_join_length).max(settings.base_join_diameter / 2.0);
                base_join = base_point + DVec3::Z * length_join;
            }
        }
        order.push(sid);
        prepared.insert(
            sid,
            Prepared {
                tip_object,
                s: settings,
                mini,
                branch,
                parent_base,
                parent_tip,
                bracing,
                tip_point,
                tip_normal,
                tip_join,
                base_point,
                base_join,
                base_orientation,
                length_join,
                total_length,
                base_object,
            },
        );
    }

    // pass 2: geometry
    let mut pieces: HashMap<String, Vec<Mesh>> = HashMap::new();
    let mut feet: HashMap<String, Vec<RaftFoot>> = HashMap::new();
    for sid in order {
        let p = &prepared[sid];
        let parent_diameter = |parent: Option<&str>, z: f64| {
            parent.and_then(|id| prepared.get(id)).map(|parent| smallest_diameter(parent, Some(z)))
        };
        let list = pieces.entry(p.tip_object.to_string()).or_default();
        let s = &p.s;
        let rings = sides / 2;

        // A brace between two supports is drawn by Lychee (when not hovered) as one straight capsule from its base
        // along the base normal, as long as the straight distance, narrowing from the diameter of the support it
        // starts on to that of the support it ends on.
        if p.bracing {
            let length = p.total_length;
            let mut radius = 0.01_f64.max((length * 1.75).min(s.base_tip_diameter)) / 2.0;
            let mut point_radius = 0.01_f64.max(s.base_tip_point_diameter / 2.0);
            if !p.mini {
                if let Some(diameter) = parent_diameter(p.parent_base, p.base_point.z) {
                    point_radius = radius.min(diameter / 2.0);
                }
            }
            radius = point_radius;
            if let Some(diameter) = parent_diameter(p.parent_tip, p.tip_point.z) {
                radius = radius.min(diameter / 2.0);
            }
            radius = 0.01_f64.max((length * 1.75).min(radius * 2.0)) / 2.0;
            point_radius = 0.01_f64.max((length * 1.75).min(point_radius * 2.0)) / 2.0;
            list.extend(terminal(
                &s.base_tip_type,
                p.base_point,
                p.base_point + p.base_orientation * length,
                point_radius,
                radius,
                true,
                true,
                sides,
                rings,
                2,
                false,
            ));
            continue;
        }

        // tip
        let mut penetration = s.tip_penetration;
        let mut radius = s.tip_diameter / 2.0;
        let mut point_radius = s.tip_point_diameter / 2.0;
        if p.mini {
            radius = point_radius;
        }
        if let Some(diameter) = parent_diameter(p.parent_tip, p.tip_point.z) {
            radius = radius.min(diameter / 2.0);
            point_radius = radius;
            penetration = 0.0;
        }
        if p.mini {
            radius -= (radius - s.base_tip_point_diameter / 2.0) * PERCENT_LENGTH_BRACING;
        }
        let tip_length = if p.bracing || p.mini {
            (p.total_length * PERCENT_LENGTH_BRACING).min(MAX_LENGTH_BRACING) + penetration
        } else {
            p.tip_point.distance(p.tip_join) + penetration
        };
        if !p.mini {
            radius = 0.01_f64.max((tip_length * 1.75).min(radius * 2.0)) / 2.0;
            point_radius = 0.01_f64.max((tip_length * 1.75).min(point_radius * 2.0)) / 2.0;
        }
        let tip_point_end = if s.tip_type == "cube" { p.tip_point } else { p.tip_point - p.tip_normal * penetration };
        list.extend(terminal(
            &s.tip_type,
            tip_point_end,
            p.tip_join,
            point_radius,
            radius,
            p.parent_tip.is_some(),
            false,
            sides,
            rings,
            if p.bracing || p.mini { 2 } else { rings },
            !p.bracing,
        ));
        if s.tip_break_point > 0.0 && !p.mini && p.parent_tip.is_none() {
            list.push(sphere(p.tip_point, 0.01_f64.max(tip_length.min(s.tip_break_point)) / 2.0, sides));
        }

        // base
        let base_radius;
        if p.branch {
            let mut base_penetration = if p.parent_base.is_some() { 0.0 } else { s.base_tip_penetration };
            let length = if p.parent_base.is_some() { p.length_join } else { p.length_join + base_penetration };
            let mut r = 0.01_f64.max((length * 1.75).min(s.base_tip_diameter)) / 2.0;
            let mut pr = 0.01_f64.max(s.base_tip_point_diameter / 2.0);
            if p.parent_base.is_some() {
                base_penetration = 0.0;
                if !p.mini {
                    if let Some(diameter) = parent_diameter(p.parent_base, p.base_point.z) {
                        pr = r.min(diameter / 2.0);
                    }
                }
                r = pr;
            }
            if p.mini {
                r = pr;
                r -= (r - s.tip_point_diameter / 2.0) * PERCENT_LENGTH_BRACING;
            } else {
                r = 0.01_f64.max((length * 1.75).min(r * 2.0)) / 2.0;
                pr = 0.01_f64.max((length * 1.75).min(pr * 2.0)) / 2.0;
            }
            base_radius = r;
            let base_point_end = if s.base_tip_type == "cube" {
                p.base_point
            } else {
                p.base_point - p.base_orientation * base_penetration
            };
            list.extend(terminal(
                &s.base_tip_type,
                base_point_end,
                p.base_join,
                pr,
                r,
                p.parent_base.is_some(),
                p.bracing,
                sides,
                rings,
                if p.bracing || p.mini { 2 } else { rings },
                !p.mini && !p.bracing,
            ));
            if s.base_tip_break_point > 0.0 && !p.mini && p.parent_base.is_none() {
                list.push(sphere(p.base_point, 0.01_f64.max(length.min(s.base_tip_break_point)) / 2.0, sides));
            }
        } else {
            let column = if p.mini { s.base_tip_point_diameter / 2.0 } else { s.base_join_diameter / 2.0 };
            base_radius = column;
            let foot = DVec3::new(p.base_point.x, p.base_point.y, 0.0);
            // the column from the plate to the join, flat at the plate, with a ball at the join
            let (from, to) = (foot + DVec3::Z * 0.01, foot + DVec3::Z * (p.length_join + 0.01));
            match s.mid_type.as_str() {
                "cube" => list.push(capsule_twisted(from, to, column, column, true, false, 4, 1, 2, -PI / 4.0)),
                "cross" => list.push(prism(from, to, &cross_profile(), column, column)),
                _ => list.push(capsule(from, to, column, column, true, false, sides, 1, rings)),
            }
            list.push(sphere(foot + DVec3::Z * p.length_join, column, sides));
            // foot pad and join cone, or just the cone on a raft
            let object = objects.get(p.base_object).copied();
            let raft = Foundation::of(object, raft_default);
            let on_raft = raft.enabled && raft.base_support;
            if !on_raft {
                list.push(pad(foot, s, sides));
            } else {
                let cone = if s.base_join_cone > 0.0 {
                    s.base_join_cone + s.base_join_diameter / 2.0
                } else {
                    s.base_join_diameter / 2.0
                };
                feet.entry(p.tip_object.to_string()).or_default().push(RaftFoot {
                    position: foot,
                    node_diameter: (cone * 2.0).ceil(),
                    join_diameter: s.base_join_diameter,
                    reach: cone,
                });
            }
            if s.base_join_cone > 0.0 {
                let join_radius = s.base_join_diameter / 2.0;
                let height =
                    s.base_join_cone.min(s.base_diameter / 2.0 - join_radius).min(p.tip_join.z - join_radius).max(0.0);
                let offset = if on_raft { raft.base_lift() } else { s.base_length - 0.001 };
                list.push(frustum(foot + DVec3::Z * offset, height + join_radius, join_radius, height, sides * 2, -PI));
            }
        }

        // middle: a straight capsule from the base join to the tip join
        let mut length = p.base_join.distance(p.tip_join);
        if p.branch && p.mini {
            length += 0.01;
        }
        let direction = unit(p.tip_join - p.base_join, DVec3::Z);
        let top_flat = p.branch && s.base_tip_type != "cylinder";
        let bottom_flat = s.tip_type != "cylinder";
        let (from, to) = (p.base_join, p.base_join + direction * length);
        match s.mid_type.as_str() {
            "cube" => list.push(prism(from, to, &polygon(4, 1.0, PI / 4.0), base_radius, radius)),
            "cross" => list.push(prism(from, to, &cross_profile(), base_radius, radius)),
            _ => list.push(capsule(from, to, base_radius, radius, top_flat, bottom_flat, sides, rings, rings)),
        }
    }

    let meshes = pieces.into_iter().map(|(object, pieces)| (object, Mesh::merge(pieces))).collect();
    (meshes, feet)
}

/// The foot pad of a plate support: `base.length` thick, `base.diameter` wide at the plate, its top wider by
/// `length * tan(base.angle)` (Lychee's `getBaseFloorRaftGeometry`). `base.type`: `cylinder` (round), `cone` (wider
/// by `2 * length`), `cube` (square), `cube45` (diamond), `octagon` or `cross`.
fn pad(foot: DVec3, s: &Settings, sides: usize) -> Mesh {
    let length = s.base_length.max(0.01);
    let bottom = s.base_diameter;
    let mut top = bottom + length * (s.base_angle / 180.0 * PI).tan() * 2.0;
    if s.base_type == "cone" {
        top = bottom + length * 2.0;
    }
    let top = top.max(0.01);
    // corners of the top outline at "diameter top / 2", then the bottom outline is the same scaled to the bottom
    let corners = match s.base_type.as_str() {
        "cube" => polygon(4, top / 2.0 * std::f64::consts::SQRT_2, PI / 4.0),
        "cube45" => polygon(4, top / 2.0, 0.0),
        "octagon" => polygon(8, top / 2.0, PI / 8.0),
        "cross" => cross_profile().iter().map(|q| *q * (top / 2.0)).collect(),
        _ => polygon(sides * 2, top / 2.0, -PI),
    };
    let ratio = bottom / top;
    let at = |scale: f64| corners.iter().map(|q| glam::DVec2::new(foot.x, foot.y) + *q * scale).collect::<Vec<_>>();
    raft::loft(&at(ratio), foot.z, &at(1.0), foot.z + length)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Signed volume of a closed mesh: positive when the faces point outwards.
    fn volume(mesh: &Mesh) -> f64 {
        mesh.faces
            .iter()
            .map(|f| {
                let v = f.map(|i| DVec3::from_array(mesh.vertices[i as usize].map(f64::from)));
                v[0].dot(v[1].cross(v[2])) / 6.0
            })
            .sum()
    }

    #[test]
    fn capsule_is_closed_outward_and_reaches_a_radius_past_each_centre() {
        let mesh = capsule(DVec3::ZERO, DVec3::new(0.0, 0.0, -10.0), 1.0, 2.0, false, false, 16, 8, 8);
        assert!(volume(&mesh) > 0.0);
        let (lo, hi) = mesh.bounds().unwrap();
        assert!((hi[2] - 1.0).abs() < 0.05, "the top sphere reaches its radius above the centre: {hi:?}");
        assert!((lo[2] + 12.0).abs() < 0.05, "the bottom sphere reaches its radius below the centre: {lo:?}");
        assert!((hi[0] - 2.0).abs() < 0.2, "the thick end is 2 wide: {hi:?}");
    }

    #[test]
    fn flat_ends_stop_at_the_centre() {
        let mesh = capsule(DVec3::ZERO, DVec3::new(0.0, 0.0, -10.0), 1.0, 1.0, true, true, 12, 6, 6);
        let (lo, hi) = mesh.bounds().unwrap();
        assert!(hi[2].abs() < 1e-5 && (lo[2] + 10.0).abs() < 1e-5, "{lo:?} {hi:?}");
        assert!(volume(&mesh) > 0.0);
    }

    #[test]
    fn a_sphere_is_a_sphere() {
        let mesh = sphere(DVec3::new(1.0, 2.0, 3.0), 1.5, 16);
        let (lo, hi) = mesh.bounds().unwrap();
        for k in 0..3 {
            assert!(((hi[k] - lo[k]) as f64 - 3.0).abs() < 0.05, "{lo:?} {hi:?}");
        }
        let expected = 4.0 / 3.0 * PI * 1.5_f64.powi(3);
        assert!((volume(&mesh) - expected).abs() / expected < 0.15);
    }

    #[test]
    fn braces_match_the_diameter_of_the_support_they_rest_on() {
        let settings = Settings { tip_diameter: 1.0, base_join_diameter: 2.0, ..Settings::default() };
        let parent = Prepared {
            tip_object: "o",
            s: settings,
            mini: false,
            branch: false,
            parent_base: None,
            parent_tip: None,
            bracing: false,
            tip_point: DVec3::new(0.0, 0.0, 20.0),
            tip_normal: DVec3::NEG_Z,
            tip_join: DVec3::new(0.0, 0.0, 22.0),
            base_point: DVec3::ZERO,
            base_join: DVec3::new(0.0, 0.0, 10.0),
            base_orientation: DVec3::Z,
            length_join: 10.0,
            total_length: 20.0,
            base_object: "o",
        };
        assert_eq!(smallest_diameter(&parent, Some(5.0)), 2.0); // below the base join: the join diameter
        assert_eq!(smallest_diameter(&parent, Some(25.0)), 1.0); // above the tip: the tip diameter
        assert!((smallest_diameter(&parent, Some(15.0)) - 1.5).abs() < 1e-9); // between: interpolated
        assert_eq!(smallest_diameter(&parent, None), 1.0);
    }

    #[test]
    fn prism_is_closed_and_cross_has_the_right_footprint() {
        let m = prism(DVec3::ZERO, DVec3::new(0.0, 0.0, 4.0), &cross_profile(), 1.0, 1.0);
        let (lo, hi) = m.bounds().unwrap();
        assert!((hi[0] - lo[0] - 2.0).abs() < 1e-5 && (hi[2] - lo[2] - 4.0).abs() < 1e-5);
        assert_eq!(m.n_faces(), 12 * 4);
    }

    #[test]
    fn pad_shapes() {
        let mut s = Settings { base_diameter: 2.0, base_length: 1.0, base_angle: 45.0, ..Settings::default() };
        for (kind, corners) in [("cube", 4), ("cube45", 4), ("octagon", 8), ("cross", 12)] {
            s.base_type = kind.to_string();
            let m = pad(DVec3::ZERO, &s, 6);
            assert!(m.n_faces() >= corners * 2, "{kind}");
        }
    }
}
