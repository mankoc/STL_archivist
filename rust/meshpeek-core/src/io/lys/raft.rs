//! Rafts ("foundations") of `.lys` objects, ported from Lychee's `foundation/*.ts` (see `docs/formats/LYS_FILE_FORMAT.md`).
//!
//! A raft is switched on per object (`foundation.enabled`) and has a `type`: `cylinder`, `cube`, `rectangle`, `grid`,
//! `triangle`, `vectoriel`, `vectorielWall`, `pixelate` or `pixelateRound`. All of them are built in the object's own
//! frame without its yaw (the Z rotation) and then turned by the yaw about the object's position. The footprint
//! types use the object's silhouette on the plate; `triangle` uses the supports' feet. Wherever Lychee draws a
//! draft, the **top** of the raft is wider than the bottom, by `size * angle / 100`.
//!
//! Approximations: the silhouette of `vectorielWall` is the convex hull of the object's projection (Lychee takes a
//! concave hull with a concavity so large that it is practically convex); `vectoriel` and the pixel types raster
//! the silhouette into cells.

use std::collections::BTreeSet;
use std::f64::consts::PI;

use glam::{DMat4, DVec2, DVec3};
use rmpv::Value;

use super::value::{get, number, str_of};
use crate::mesh::Mesh;

/// Shape of a raft, from `foundation.type`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// A disc round the footprint.
    Cylinder,
    /// A square round the footprint.
    Cube,
    /// A box over the footprint's bounding box.
    Rectangle,
    /// A lattice of bars over the footprint's bounding box.
    Grid,
    /// Cylinders under the supports joined by thin bars along a Delaunay triangulation ("line triangle").
    Triangle,
    /// The footprint's outline extruded.
    Vectoriel,
    /// A dish: a floor over the footprint's outline with a rim wall.
    VectorielWall,
    /// A small diamond per footprint cell.
    Pixelate,
    /// A small cylinder per footprint cell.
    PixelateRound,
}

/// An object's raft settings.
#[derive(Clone, Debug)]
pub struct Foundation {
    /// The object has a raft.
    pub enabled: bool,
    /// Its shape.
    pub kind: Kind,
    /// Thickness in mm.
    pub size: f64,
    /// Percent, 100 = the natural size of the shape.
    pub scale: f64,
    /// Draft: the top is wider than the bottom by `size * angle / 100`.
    pub angle: f64,
    /// `vectorielWall`: width of the rim wall's top in mm.
    pub wall: f64,
    /// `vectorielWall`: height of the rim wall above the floor in mm.
    pub extra_wall: f64,
    /// `rectangle` and `grid`: percent of the object's footprint.
    pub width: f64,
    /// `rectangle` and `grid`: percent of the object's footprint in y.
    pub height: f64,
    /// Grid size in mm of the raster types.
    pub precision: f64,
    /// `baseSupport`: the supports' foot pads are replaced by the raft.
    pub base_support: bool,
}

impl Foundation {
    /// Read `object.foundation`; `default_enabled` (the scene's raft setting) applies when the object has none.
    pub fn of(object: Option<&Value>, default_enabled: bool) -> Self {
        let f = object.and_then(|o| get(o, "foundation"));
        let kind = match f.and_then(|f| str_of(f, "type")) {
            Some("cylinder") => Kind::Cylinder,
            Some("cube") => Kind::Cube,
            Some("rectangle") => Kind::Rectangle,
            Some("grid") => Kind::Grid,
            Some("vectoriel") => Kind::Vectoriel,
            Some("vectorielWall") => Kind::VectorielWall,
            Some("pixelate") => Kind::Pixelate,
            Some("pixelateRound") => Kind::PixelateRound,
            _ => Kind::Triangle,
        };
        // Lychee's defaults per type (`config/defaultFoundation.ts`); the file's values win.
        let (scale, angle, size, wall) = match kind {
            Kind::Cylinder | Kind::Cube | Kind::Rectangle => (100.0, 100.0, 0.5, 1.0),
            Kind::Grid => (100.0, 0.0, 1.5, 1.0),
            Kind::Vectoriel => (200.0, 100.0, 0.5, 1.0),
            Kind::VectorielWall => (100.0, 200.0, 1.0, 1.0),
            Kind::Pixelate | Kind::PixelateRound => (200.0, 100.0, 0.5, 1.0),
            Kind::Triangle => (100.0, 0.0, 0.5, 1.0),
        };
        let value = |key: &str, default: f64| f.map_or(default, |f| number(f, key, default));
        let enabled = f.and_then(|f| get(f, "enabled")).and_then(Value::as_bool).unwrap_or(default_enabled);
        Self {
            enabled: enabled && f.is_none_or(|f| get(f, "visible").and_then(Value::as_bool) != Some(false)),
            kind,
            size: value("size", size).clamp(0.05, 10.0),
            scale: value("scale", scale).max(1.0),
            angle: value("angle", angle).max(0.0),
            wall: value("wall", wall).clamp(0.0, 10.0),
            extra_wall: value("extraWall", 1.0).clamp(0.0, 10.0),
            width: value("width", 100.0).max(1.0),
            height: value("height", 100.0).max(1.0),
            precision: value("precision", 1.0).max(0.05),
            base_support: f.and_then(|f| get(f, "baseSupport")).and_then(Value::as_bool).unwrap_or(true),
        }
    }

    /// How far the supports' base cones are raised: Lychee sits them on the raft for the footprint-following
    /// types and the triangle raft, and leaves them at the plate for the plain shapes.
    pub fn base_lift(&self) -> f64 {
        match self.kind {
            Kind::Triangle | Kind::Vectoriel | Kind::VectorielWall | Kind::Pixelate | Kind::PixelateRound => {
                (self.size - 0.001).max(0.0)
            }
            Kind::Cylinder | Kind::Cube | Kind::Rectangle | Kind::Grid => 0.0,
        }
    }
}

/// The foot of a plate support, as the `triangle` raft needs it.
#[derive(Clone, Copy, Debug)]
pub struct RaftFoot {
    /// On the plate, in the object's yaw-turned frame, world units (z = plate).
    pub position: DVec3,
    /// Diameter of the cylinder under the support: its join cone plus half its join diameter, rounded up.
    pub node_diameter: f64,
    /// The support's `base.joinDiameter`, the width of the bars that start at this foot.
    pub join_diameter: f64,
    /// Radius Lychee paints around the foot into the footprint of the silhouette rafts (`baseSupport`).
    pub reach: f64,
}

/// The object's silhouette on the plate, in the object's frame without its yaw.
#[derive(Clone, Debug, Default)]
pub struct Footprint {
    /// Lower corner of the bounding box.
    pub min: DVec2,
    /// Upper corner of the bounding box.
    pub max: DVec2,
    /// Convex hull, counter-clockwise.
    pub hull: Vec<DVec2>,
    /// Filled cells (origin `min`, `cell` mm wide) when requested.
    pub cells: Vec<(usize, usize)>,
    /// Width of a cell in mm (0 when not rastered).
    pub cell: f64,
}

impl Footprint {
    /// Footprint of `mesh` seen through `to_local` (object space to the yaw-less frame). With `cell`, the
    /// silhouette is also rastered into cells of that size.
    /// `discs` (centre in the same frame, radius) are painted into it as well: Lychee does that for the plate
    /// supports' feet when the foundation has `baseSupport`.
    pub fn of(mesh: &Mesh, to_local: &DMat4, cell: Option<f64>, discs: &[(DVec2, f64)]) -> Option<Self> {
        let points: Vec<DVec2> = mesh
            .vertices
            .iter()
            .map(|v| {
                let p = to_local.transform_point3(DVec3::new(v[0] as f64, v[1] as f64, v[2] as f64));
                DVec2::new(p.x, p.y)
            })
            .collect();
        let first = *points.first()?;
        let rim = |&(c, r): &(DVec2, f64)| (0..24).map(move |k| c + DVec2::from_angle(2.0 * PI * k as f64 / 24.0) * r);
        let outline: Vec<DVec2> = points.iter().copied().chain(discs.iter().flat_map(rim)).collect();
        let (min, max) = outline.iter().fold((first, first), |(lo, hi), p| (lo.min(*p), hi.max(*p)));
        let mut footprint = Self { min, max, hull: convex_hull(&outline), cells: Vec::new(), cell: 0.0 };
        if let Some(cell) = cell {
            footprint.cell = cell;
            footprint.cells = raster(mesh, &points, min, max, cell, discs);
        }
        Some(footprint)
    }

    /// Width and depth of the bounding box.
    fn size(&self) -> DVec2 {
        self.max - self.min
    }

    /// Centre of the bounding box.
    fn centre(&self) -> DVec2 {
        (self.min + self.max) / 2.0
    }
}

/// Cells (column, row) of a grid of `cell` mm over `min..max` that the projected triangles cover.
fn raster(
    mesh: &Mesh,
    points: &[DVec2],
    min: DVec2,
    max: DVec2,
    cell: f64,
    discs: &[(DVec2, f64)],
) -> Vec<(usize, usize)> {
    let columns = (((max.x - min.x) / cell).ceil() as usize).clamp(1, 4000);
    let rows = (((max.y - min.y) / cell).ceil() as usize).clamp(1, 4000);
    let mut filled = vec![false; columns * rows];
    for face in &mesh.faces {
        let [a, b, c] = face.map(|i| points[i as usize]);
        let (lo, hi) = (a.min(b).min(c), a.max(b).max(c));
        let first = (((lo - min) / cell).floor().max(DVec2::ZERO)).as_uvec2();
        let last = (((hi - min) / cell).floor()).as_uvec2().min(glam::UVec2::new(columns as u32 - 1, rows as u32 - 1));
        let area = (b - a).perp_dot(c - a);
        for j in first.y..=last.y {
            for i in first.x..=last.x {
                let p = min + DVec2::new(i as f64 + 0.5, j as f64 + 0.5) * cell;
                if area.abs() > 1e-12 {
                    let (u, v) = ((p - a).perp_dot(c - a) / area, (b - a).perp_dot(p - a) / area);
                    if u >= 0.0 && v >= 0.0 && u + v <= 1.0 {
                        filled[j as usize * columns + i as usize] = true;
                    }
                } else if (lo.x..=hi.x).contains(&p.x) && (lo.y..=hi.y).contains(&p.y) {
                    filled[j as usize * columns + i as usize] = true;
                }
            }
        }
        // a triangle smaller than a cell still marks the cell holding its centre
        let centre = (a + b + c) / 3.0 - min;
        let (i, j) = ((centre.x / cell) as usize, (centre.y / cell) as usize);
        if i < columns && j < rows {
            filled[j * columns + i] = true;
        }
    }
    for j in 0..rows {
        for i in 0..columns {
            let p = min + DVec2::new(i as f64 + 0.5, j as f64 + 0.5) * cell;
            if discs.iter().any(|&(c, r)| (p - c).length_squared() <= r * r) {
                filled[j * columns + i] = true;
            }
        }
    }
    (0..rows).flat_map(|j| (0..columns).map(move |i| (i, j))).filter(|&(i, j)| filled[j * columns + i]).collect()
}

/// Convex hull (Andrew's monotone chain), counter-clockwise, without repeated points.
pub fn convex_hull(points: &[DVec2]) -> Vec<DVec2> {
    let mut sorted: Vec<DVec2> = points.to_vec();
    sorted.sort_by(|a, b| a.x.total_cmp(&b.x).then(a.y.total_cmp(&b.y)));
    sorted.dedup();
    if sorted.len() < 3 {
        return sorted;
    }
    let turn = |o: DVec2, a: DVec2, b: DVec2| (a - o).perp_dot(b - o);
    let mut hull: Vec<DVec2> = Vec::new();
    for &p in &sorted {
        while hull.len() >= 2 && turn(hull[hull.len() - 2], hull[hull.len() - 1], p) <= 0.0 {
            hull.pop();
        }
        hull.push(p);
    }
    let lower = hull.len() + 1;
    for &p in sorted.iter().rev().skip(1) {
        while hull.len() >= lower && turn(hull[hull.len() - 2], hull[hull.len() - 1], p) <= 0.0 {
            hull.pop();
        }
        hull.push(p);
    }
    hull.pop();
    hull
}

// --- building -----------------------------------------------------------------------------------------------

/// Where the raft is built: the object's position on the plate and its yaw (radians).
#[derive(Clone, Copy, Debug)]
pub struct Placement {
    /// The object's position on the plate.
    pub position: DVec2,
    /// The object's Z rotation in radians.
    pub yaw: f64,
}

/// The raft mesh for `foundation`, in world coordinates. `feet` are the plate supports' feet (used by the
/// `triangle` type), `footprint` the object's silhouette (used by the others).
pub fn build(foundation: &Foundation, feet: &[RaftFoot], footprint: Option<&Footprint>, at: Placement) -> Mesh {
    let mut mesh = match foundation.kind {
        Kind::Triangle => triangle(foundation, feet),
        _ => match footprint {
            Some(footprint) => footprint_raft(foundation, footprint),
            None => Mesh::default(),
        },
    };
    if foundation.kind != Kind::Triangle {
        // built in the object's frame without yaw: turn it into the world
        let (sin, cos) = at.yaw.sin_cos();
        for v in &mut mesh.vertices {
            let (x, y) = (v[0] as f64, v[1] as f64);
            v[0] = (x * cos - y * sin + at.position.x) as f32;
            v[1] = (x * sin + y * cos + at.position.y) as f32;
        }
    }
    mesh
}

/// Every raft type except `triangle`, built from the footprint in the object's yaw-less frame.
fn footprint_raft(f: &Foundation, fp: &Footprint) -> Mesh {
    let (size, scale) = (f.size, f.scale / 100.0);
    let draft = f.size * f.angle / 100.0;
    let (sizes, centre) = (fp.size(), fp.centre());
    match f.kind {
        Kind::Cylinder => {
            let radius = sizes.length() * 0.51 * scale;
            loft(&circle(centre, radius, 30, 0.0), 0.0, &circle(centre, radius + draft, 30, 0.0), size)
        }
        Kind::Cube => {
            let radius = sizes.x.max(sizes.y) * 0.75 * scale;
            // a 4-sided cylinder turned by 45 degrees: a square whose corners lie on the axes' diagonals
            let square = |r: f64| circle(centre, r, 4, PI / 4.0);
            loft(&square(radius), 0.0, &square(radius + draft), size)
        }
        Kind::Rectangle => {
            let (w, h) = (sizes.x * f.width / 100.0 * scale, sizes.y * f.height / 100.0 * scale);
            let rect = |grow: f64| {
                let (hw, hh) = (w / 2.0 + grow, h / 2.0 + grow);
                vec![
                    centre + DVec2::new(-hw, -hh),
                    centre + DVec2::new(hw, -hh),
                    centre + DVec2::new(hw, hh),
                    centre + DVec2::new(-hw, hh),
                ]
            };
            loft(&rect(0.0), 0.0, &rect(draft), size)
        }
        Kind::Grid => grid(f, sizes, centre),
        Kind::VectorielWall => wall(f, &fp.hull),
        Kind::Vectoriel => runs(fp, f.size),
        Kind::Pixelate | Kind::PixelateRound => pixels(f, fp),
        Kind::Triangle => Mesh::default(),
    }
}

/// Regular polygon (counter-clockwise) with `n` corners; the first corner at angle `start` from +x.
pub fn circle(centre: DVec2, radius: f64, n: usize, start: f64) -> Vec<DVec2> {
    (0..n)
        .map(|k| {
            let a = start + 2.0 * PI * k as f64 / n as f64;
            centre + DVec2::new(a.cos(), a.sin()) * radius
        })
        .collect()
}

/// Solid between two polygons with the same number of counter-clockwise corners, at heights `z0` and `z1`,
/// with both caps.
pub fn loft(bottom: &[DVec2], z0: f64, top: &[DVec2], z1: f64) -> Mesh {
    let n = bottom.len() as u32;
    let mut mesh = Mesh::default();
    mesh.vertices.extend(bottom.iter().map(|p| [p.x as f32, p.y as f32, z0 as f32]));
    mesh.vertices.extend(top.iter().map(|p| [p.x as f32, p.y as f32, z1 as f32]));
    let centre = |poly: &[DVec2]| poly.iter().sum::<DVec2>() / poly.len() as f64;
    let (cb, ct) = (centre(bottom), centre(top));
    mesh.vertices.push([cb.x as f32, cb.y as f32, z0 as f32]);
    mesh.vertices.push([ct.x as f32, ct.y as f32, z1 as f32]);
    for i in 0..n {
        let j = (i + 1) % n;
        mesh.faces.extend([[i, j, n + j], [i, n + j, n + i]]); // side
        mesh.faces.push([2 * n, j, i]); // bottom cap, facing down
        mesh.faces.push([2 * n + 1, n + i, n + j]); // top cap, facing up
    }
    mesh
}

/// Moves each corner of a convex counter-clockwise polygon outwards by `distance`.
fn offset_convex(poly: &[DVec2], distance: f64) -> Vec<DVec2> {
    let n = poly.len();
    (0..n)
        .map(|i| {
            let (prev, next) = (poly[(i + n - 1) % n], poly[(i + 1) % n]);
            let outward = |a: DVec2, b: DVec2| {
                let e = (b - a).normalize_or_zero();
                DVec2::new(e.y, -e.x)
            };
            let (n1, n2) = (outward(prev, poly[i]), outward(poly[i], next));
            let bisector = (n1 + n2).normalize_or_zero();
            let cos = bisector.dot(n1).max(0.2);
            poly[i] + bisector * (distance / cos)
        })
        .collect()
}

/// `vectorielWall`: a floor `size` thick over the (convex) silhouette, with a rim wall `wall` wide that leans
/// outwards, from the silhouette's outline at the plate to `size * angle / 100` beyond it at `size + extraWall`.
fn wall(f: &Foundation, hull: &[DVec2]) -> Mesh {
    let mut mesh = Mesh::default();
    if hull.len() < 3 {
        return mesh;
    }
    let depth = f.size + f.extra_wall;
    let lean = f.size * f.angle / 100.0;
    let layers: [(f64, Vec<DVec2>); 4] = [
        (0.0, hull.to_vec()),
        (depth, offset_convex(hull, lean)),
        (depth, offset_convex(hull, lean - f.wall)),
        (f.size, hull.to_vec()),
    ];
    let n = hull.len() as u32;
    for (z, poly) in &layers {
        mesh.vertices.extend(poly.iter().map(|p| [p.x as f32, p.y as f32, *z as f32]));
    }
    let centre = hull.iter().sum::<DVec2>() / hull.len() as f64;
    mesh.vertices.push([centre.x as f32, centre.y as f32, 0.0]); // 4n: under the floor
    mesh.vertices.push([centre.x as f32, centre.y as f32, f.size as f32]); // 4n + 1: floor top
    for i in 0..n {
        let j = (i + 1) % n;
        mesh.faces.push([4 * n, j, i]); // bottom, facing down
        mesh.faces.push([4 * n + 1, 3 * n + i, 3 * n + j]); // floor top, facing up
        let quad = |from: u32, to: u32, mesh: &mut Mesh| {
            // a ring of quads between layer `from` and `to`, wound so that it faces outwards or up
            let (a, b, c, d) = (from * n + i, from * n + j, to * n + j, to * n + i);
            mesh.faces.extend([[a, b, c], [a, c, d]]);
        };
        quad(0, 1, &mut mesh); // outer face
        // top of the wall, facing up: layer 2 is inside layer 1
        mesh.faces.extend([[2 * n + i, n + j, n + i], [2 * n + i, 2 * n + j, n + j]]);
        // inner face, facing inwards: from the inner top edge down to the floor
        let (a, b, c, d) = (2 * n + i, 2 * n + j, 3 * n + j, 3 * n + i);
        mesh.faces.extend([[a, c, b], [a, d, c]]);
    }
    mesh
}

/// Raster types: the silhouette as runs of cells per row, each a box `height` thick (`vectoriel`).
fn runs(fp: &Footprint, height: f64) -> Mesh {
    let mut meshes = Vec::new();
    let mut i = 0;
    while i < fp.cells.len() {
        let (start, row) = fp.cells[i];
        let mut end = start;
        while i + 1 < fp.cells.len() && fp.cells[i + 1] == (end + 1, row) {
            end += 1;
            i += 1;
        }
        i += 1;
        let lo = fp.min + DVec2::new(start as f64, row as f64) * fp.cell;
        let hi = fp.min + DVec2::new(end as f64 + 1.0, row as f64 + 1.0) * fp.cell;
        let rect = [lo, DVec2::new(hi.x, lo.y), hi, DVec2::new(lo.x, hi.y)];
        meshes.push(loft(&rect, 0.0, &rect, height));
    }
    Mesh::merge(meshes)
}

/// `pixelate` / `pixelateRound`: a small cylinder (12 sides) or diamond (4 sides) per filled cell.
fn pixels(f: &Foundation, fp: &Footprint) -> Mesh {
    let round = f.kind == Kind::PixelateRound;
    let (divisor, n, start) = if round { (99.0, 12, 0.0) } else { (100.0, 4, -PI / 4.0) };
    let radius = fp.cell * f.scale / divisor;
    let top = radius + f.size * f.angle / divisor;
    let meshes: Vec<Mesh> = fp
        .cells
        .iter()
        .map(|&(i, j)| {
            let centre = fp.min + DVec2::new(i as f64 + 0.5, j as f64 + 0.5) * fp.cell;
            loft(&circle(centre, radius, n, start), 0.0, &circle(centre, top, n, start), f.size)
        })
        .collect();
    Mesh::merge(meshes)
}

/// `grid`: two sets of bars with a peaked profile, `wall` wide and `size` tall, every `2 * wall`.
fn grid(f: &Foundation, sizes: DVec2, centre: DVec2) -> Mesh {
    let wall = if f.wall == 0.0 { 0.1 } else { f.wall };
    let height = f.size;
    let (w, h) = (sizes.x * f.width / 100.0 * f.scale / 100.0, sizes.y * f.height / 100.0 * f.scale / 100.0);
    let (w, h) = (w - w % wall + wall, h - h % wall + wall);
    let mut meshes = Vec::new();
    let bar = |along_x: bool, offset: f64| {
        let length = if along_x { w + wall } else { h + wall };
        let profile = [
            (-wall / 2.0, 0.0),
            (wall / 2.0, 0.0),
            (wall / 2.0, height - wall / 2.0),
            (0.0, height),
            (-wall / 2.0, height - wall / 2.0),
        ];
        let to_world = |t: f64, across: f64, z: f64| {
            let (x, y) = if along_x { (t, offset + across) } else { (offset + across, t) };
            [(centre.x + x) as f32, (centre.y + y) as f32, z as f32]
        };
        let mut mesh = Mesh::default();
        for t in [-length / 2.0, length / 2.0] {
            mesh.vertices.extend(profile.iter().map(|&(across, z)| to_world(t, across, z)));
        }
        let m = profile.len() as u32;
        for i in 0..m {
            let j = (i + 1) % m;
            mesh.faces.extend([[i, j, m + j], [i, m + j, m + i]]);
        }
        for i in 1..m - 1 {
            mesh.faces.extend([[0, i, i + 1], [m, m + i + 1, m + i]]);
        }
        // the winding of the sides depends on the axis: make every face point away from the bar's middle
        let corners: Vec<DVec3> = mesh.vertices.iter().map(|v| DVec3::from_array(v.map(f64::from))).collect();
        let middle = corners.iter().sum::<DVec3>() / corners.len() as f64;
        for face in &mut mesh.faces {
            let v = face.map(|k| corners[k as usize]);
            let outward = (v[0] + v[1] + v[2]) / 3.0 - middle;
            if (v[1] - v[0]).cross(v[2] - v[0]).dot(outward) < 0.0 {
                face.swap(1, 2);
            }
        }
        mesh
    };
    let mut y = h / 2.0 - wall / 2.0;
    for _ in 0..((h / (wall * 2.0)).ceil() as usize + 1).min(2000) {
        meshes.push(bar(true, y));
        y -= wall * 2.0;
    }
    let mut x = w / 2.0 - wall / 2.0;
    for _ in 0..((w / (wall * 2.0)).ceil() as usize + 1).min(2000) {
        meshes.push(bar(false, x));
        x -= wall * 2.0;
    }
    Mesh::merge(meshes)
}

/// `triangle`: a cylinder (12 sides, `size` tall) under every support foot and a thin bar along every edge of the
/// Delaunay triangulation of the feet. Built straight in world coordinates.
fn triangle(f: &Foundation, feet: &[RaftFoot]) -> Mesh {
    let height = f.size;
    let scale = f.scale / 100.0;
    let points: Vec<DVec2> = feet.iter().map(|p| DVec2::new(p.position.x, p.position.y)).collect();
    let mut meshes = Vec::new();
    for foot in feet {
        let radius = foot.node_diameter * scale / 2.0;
        let ring = circle(DVec2::new(foot.position.x, foot.position.y), radius, 12, 0.0);
        meshes.push(loft(&ring, foot.position.z, &ring, foot.position.z + height + 0.01));
    }
    for (a, b) in delaunay(&points).0 {
        let width = feet[a].join_diameter.min(feet[b].join_diameter);
        let towards = (points[a] - points[b]).normalize_or_zero();
        let length = points[a].distance(points[b]) - 0.4;
        if length <= 0.0 {
            continue;
        }
        let middle = (points[a] + points[b]) / 2.0 + towards * 0.2;
        let side = DVec2::new(-towards.y, towards.x) * (width / 2.0);
        let half = towards * (length / 2.0);
        let rect = [middle - half - side, middle + half - side, middle + half + side, middle - half + side];
        meshes.push(loft(&rect, feet[a].position.z, &rect, feet[a].position.z + height));
    }
    Mesh::merge(meshes)
}

/// Edges (index pairs) and triangles of the Delaunay triangulation of `points`, by incremental insertion
/// (Bowyer-Watson). Collinear points have no triangles and so no edges.
pub fn delaunay(points: &[DVec2]) -> (Vec<(usize, usize)>, Vec<[usize; 3]>) {
    let n = points.len();
    if n < 3 {
        return (Vec::new(), Vec::new());
    }
    let (lo, hi) = points.iter().fold((points[0], points[0]), |(lo, hi), p| (lo.min(*p), hi.max(*p)));
    let span = (hi - lo).max_element().max(1.0);
    let mid = (lo + hi) / 2.0;
    let mut pts = points.to_vec();
    pts.extend([
        mid + DVec2::new(-20.0 * span, -span),
        mid + DVec2::new(0.0, 20.0 * span),
        mid + DVec2::new(20.0 * span, -span),
    ]);
    let in_circle = |t: &[usize; 3], p: DVec2| {
        let (a, b, c) = (pts[t[0]] - p, pts[t[1]] - p, pts[t[2]] - p);
        let det = a.length_squared() * b.perp_dot(c) - b.length_squared() * a.perp_dot(c)
            + c.length_squared() * a.perp_dot(b);
        let ccw = (pts[t[1]] - pts[t[0]]).perp_dot(pts[t[2]] - pts[t[0]]) > 0.0;
        if ccw { det > 1e-12 } else { det < -1e-12 }
    };
    let mut triangles: Vec<[usize; 3]> = vec![[n, n + 1, n + 2]];
    for (i, &point) in points.iter().enumerate() {
        let (bad, keep): (Vec<_>, Vec<_>) = triangles.into_iter().partition(|t| in_circle(t, point));
        let mut boundary: Vec<(usize, usize)> = Vec::new(); // edges used by exactly one removed triangle
        for t in &bad {
            for k in 0..3 {
                let edge = (t[k].min(t[(k + 1) % 3]), t[k].max(t[(k + 1) % 3]));
                match boundary.iter().position(|&e| e == edge) {
                    Some(at) => {
                        boundary.swap_remove(at);
                    }
                    None => boundary.push(edge),
                }
            }
        }
        triangles = keep;
        triangles.extend(boundary.into_iter().map(|(a, b)| [a, b, i]));
    }
    let real: Vec<[usize; 3]> = triangles.into_iter().filter(|t| t.iter().all(|&v| v < n)).collect();
    let mut edges = BTreeSet::new();
    for t in &real {
        for k in 0..3 {
            edges.insert((t[k].min(t[(k + 1) % 3]), t[k].max(t[(k + 1) % 3])));
        }
    }
    (edges.into_iter().collect(), real)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn foundation(kind: Kind) -> Foundation {
        Foundation {
            enabled: true,
            kind,
            size: 1.0,
            scale: 100.0,
            angle: 200.0,
            wall: 1.0,
            extra_wall: 1.0,
            width: 100.0,
            height: 100.0,
            precision: 1.0,
            base_support: true,
        }
    }

    fn square_footprint() -> Footprint {
        let hull = vec![DVec2::new(0.0, 0.0), DVec2::new(10.0, 0.0), DVec2::new(10.0, 6.0), DVec2::new(0.0, 6.0)];
        Footprint { min: DVec2::ZERO, max: DVec2::new(10.0, 6.0), hull, cells: Vec::new(), cell: 0.0 }
    }

    fn at() -> Placement {
        Placement { position: DVec2::ZERO, yaw: 0.0 }
    }

    #[test]
    fn angle_widens_the_top_by_size_times_angle_percent() {
        // the cylinder raft: radius from the bounding box diagonal, 2 mm wider at the top for size 1, angle 200
        let mesh = build(&foundation(Kind::Cylinder), &[], Some(&square_footprint()), at());
        let radius = DVec2::new(10.0, 6.0).length() * 0.51;
        let (lo, hi) = mesh.bounds().unwrap();
        assert!((hi[2] - 1.0).abs() < 1e-6 && lo[2].abs() < 1e-6);
        assert!(((hi[0] - lo[0]) as f64 / 2.0 - (radius + 2.0)).abs() < 0.2, "{lo:?} {hi:?}");
        let bottom: Vec<_> = mesh.vertices.iter().filter(|v| v[2] < 0.5).collect();
        let widest = bottom.iter().map(|v| (v[0] - 5.0).abs()).fold(0.0f32, f32::max) as f64;
        assert!((widest - radius).abs() < 0.2, "the bottom is the narrow end: {widest} vs {radius}");
    }

    #[test]
    fn shape_wall_has_a_floor_and_a_leaning_rim() {
        let mesh = build(&foundation(Kind::VectorielWall), &[], Some(&square_footprint()), at());
        let (lo, hi) = mesh.bounds().unwrap();
        assert!((hi[2] - 2.0).abs() < 1e-6, "rim reaches size + extraWall: {}", hi[2]);
        assert!(lo[0] < -1.9 && hi[0] > 11.9, "the rim top leans out by size * angle / 100 = 2: {lo:?} {hi:?}");
        assert!(mesh.vertices.iter().filter(|v| v[2] < 1e-6).all(|v| v[0] >= -1e-3 && v[0] <= 10.0 + 1e-3));
    }

    #[test]
    fn triangle_raft_has_a_cylinder_per_foot_and_a_bar_per_edge() {
        let feet: Vec<RaftFoot> = [(0.0, 0.0), (6.0, 0.0), (3.0, 5.0)]
            .iter()
            .map(|&(x, y)| RaftFoot {
                position: DVec3::new(x, y, 0.0),
                node_diameter: 3.0,
                join_diameter: 1.0,
                reach: 1.5,
            })
            .collect();
        let mesh = build(&foundation(Kind::Triangle), &feet, None, at());
        let cylinder_faces = 3 * (4 * 12); // 12 sides, each with two side and two cap triangles
        let bar_faces = 3 * (4 * 4); // three edges, each a box: 4 sides
        assert_eq!(mesh.n_faces(), cylinder_faces + bar_faces);
        // the bars are `joinDiameter` wide: nothing wider than the cylinders
        let (lo, hi) = mesh.bounds().unwrap();
        assert!(lo[0] > -1.51 && hi[0] < 7.51);
    }

    #[test]
    fn delaunay_of_a_square_with_a_centre_has_no_diagonals() {
        let pts: Vec<DVec2> = [(0.0, 0.0), (6.0, 0.0), (6.0, 6.0), (0.0, 6.0), (3.0, 3.0)]
            .iter()
            .map(|&(x, y)| DVec2::new(x, y))
            .collect();
        let (edges, triangles) = delaunay(&pts);
        assert_eq!((edges.len(), triangles.len()), (8, 4));
        assert!(!edges.contains(&(0, 2)) && !edges.contains(&(1, 3)));
        let row: Vec<DVec2> = [0.0, 6.0, 12.0].iter().map(|&x| DVec2::new(x, 0.0)).collect();
        assert!(delaunay(&row).0.is_empty()); // collinear: no triangles
    }

    #[test]
    fn every_footprint_type_builds_a_raft_of_the_right_height() {
        // a 10 x 6 mm footprint, rastered into 1 mm cells
        let mut footprint = square_footprint();
        footprint.cell = 1.0;
        footprint.cells = (0..6).flat_map(|j| (0..10).map(move |i| (i, j))).collect();
        for (kind, height) in [
            (Kind::Cylinder, 1.0),
            (Kind::Cube, 1.0),
            (Kind::Rectangle, 1.0),
            (Kind::Vectoriel, 1.0),
            (Kind::VectorielWall, 2.0),
            (Kind::Pixelate, 1.0),
            (Kind::PixelateRound, 1.0),
        ] {
            let mesh = build(&foundation(kind), &[], Some(&footprint), at());
            let (lo, hi) = mesh.bounds().unwrap_or_else(|| panic!("{kind:?} is empty"));
            assert!((hi[2] as f64 - height).abs() < 1e-5 && lo[2].abs() < 1e-5, "{kind:?}: {lo:?} {hi:?}");
        }
        // the grid's bars are `size` tall too, and cover the footprint
        let mut f = foundation(Kind::Grid);
        f.size = 1.5;
        let mesh = build(&f, &[], Some(&footprint), at());
        let (lo, hi) = mesh.bounds().unwrap();
        assert!((hi[2] - 1.5).abs() < 1e-5 && lo[0] <= 0.0 && hi[0] >= 10.0, "{lo:?} {hi:?}");
    }

    #[test]
    fn convex_hull_drops_inner_points() {
        let pts: Vec<DVec2> = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (2.0, 2.0), (1.0, 3.0)]
            .iter()
            .map(|&(x, y)| DVec2::new(x, y))
            .collect();
        assert_eq!(convex_hull(&pts).len(), 4);
    }

    #[test]
    fn foundation_reads_type_defaults_and_the_file() {
        let object = |text: &[(&str, Value)]| {
            let foundation = Value::Map(text.iter().map(|(k, v)| (Value::from(*k), v.clone())).collect::<Vec<_>>());
            Value::Map(vec![(Value::from("foundation"), foundation)])
        };
        let wall = object(&[("enabled", Value::Boolean(true)), ("type", Value::from("vectorielWall"))]);
        let f = Foundation::of(Some(&wall), false);
        assert!(f.enabled && f.kind == Kind::VectorielWall && f.angle == 200.0 && f.size == 1.0);
        let cylinder = object(&[
            ("enabled", Value::Boolean(true)),
            ("type", Value::from("cylinder")),
            ("angle", Value::from(50.0)),
            ("size", Value::from(0.8)),
        ]);
        let f = Foundation::of(Some(&cylinder), false);
        assert!(f.kind == Kind::Cylinder && f.angle == 50.0 && (f.size - 0.8).abs() < 1e-9);
        let off = object(&[("enabled", Value::Boolean(false)), ("type", Value::from("triangle"))]);
        assert!(!Foundation::of(Some(&off), true).enabled); // the object wins over the scene setting
        assert!(Foundation::of(None, true).enabled && Foundation::of(None, true).kind == Kind::Triangle);
        assert!(!Foundation::of(None, false).enabled);
    }
}
