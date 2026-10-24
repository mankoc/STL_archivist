//! 3MF files (`.3mf`), the 3D Manufacturing Format: a ZIP package of XML model files.
//!
//! What is read:
//!
//! - the core model: objects made of a mesh or of components (other objects, each with a transform), the build
//!   items that place objects, and the unit;
//! - the Production extension: components and items that live in other model files of the package (Bambu Studio
//!   and OrcaSlicer save every object in its own file);
//! - display colours of the core and Materials extension (`basematerials`, `colorgroup`), per object or per
//!   triangle corner;
//! - what PrusaSlicer, Bambu Studio and OrcaSlicer save next to the model: object names, each part's extruder and
//!   the extruders' (filaments') colours, colours painted on the triangles (multi-material painting), parts that
//!   are not printed (modifiers, negative volumes, support blockers and enforcers), and the printer's build plates.
//!
//! A file that uses a single colour (or none) is shown in MeshPeek's own model colour; one with several colours
//! gets them as vertex colours.

use std::collections::{HashMap, HashSet};
use std::io::{Cursor, Read};
use std::path::Path;

use glam::{DMat4, DVec3};
use memchr::memmem;
use rayon::prelude::*;

use crate::error::{LoadError, Result};
use crate::io::stem;
use crate::mesh::Mesh;
use crate::scene::{ObjectKind, Plate, Scene, SceneObject};

/// The model file of a package without a relationship naming one.
const DEFAULT_MODEL: &str = "3D/3dmodel.model";
/// Relationship type of the package's model (the end of the URI).
const MODEL_RELATIONSHIP: &str = "/3dmodel";
/// Colour of triangles without one, in a file whose other triangles have colours.
const NO_COLOUR: [u8; 3] = [200, 200, 200];
/// Deepest nesting of components followed (deeper ones, or cycles, are cut off).
const MAX_DEPTH: usize = 16;
/// Gap between Bambu Studio's build plates, as a share of a plate's size.
const PLATE_GAP: f64 = 0.2;

type Package<'a> = zip::ZipArchive<Cursor<&'a [u8]>>;

/// Load a `.3mf` file whose bytes are `data`; `path` names it in errors and in the objects.
pub fn load_bytes(data: &[u8], path: &Path) -> Result<Scene> {
    let bad = |what: String| LoadError::format(format!("{}: {what}", path.display()));
    let package = Package::new(Cursor::new(data)).map_err(|e| bad(format!("not a 3MF package ({e})")))?;
    let root = root_model(&package);
    let mut files: HashMap<String, ModelFile> = HashMap::new();
    let first = read(&package, &root).map_err(bad)?;
    files.insert(root.clone(), parse_model(&first).map_err(|e| bad(format!("{root}: {e}")))?);
    drop(first);
    // the model files the Production extension refers to (in parallel), until none is missing
    loop {
        let wanted: HashSet<String> =
            files.values().flat_map(ModelFile::references).filter(|p| !files.contains_key(p)).collect();
        if wanted.is_empty() {
            break;
        }
        let parsed: Vec<(String, std::result::Result<ModelFile, String>)> = wanted
            .into_par_iter()
            .map(|name| {
                let file = read(&package, &name).and_then(|xml| parse_model(&xml)).map_err(|e| format!("{name}: {e}"));
                (name, file)
            })
            .collect();
        for (name, file) in parsed {
            files.insert(name, file.map_err(bad)?);
        }
    }
    let slicer = Slicer::read(&package);
    Ok(build_scene(&files, &root, &slicer, path))
}

/// The package's model file: the target of the root relationship of the 3D model type.
fn root_model(package: &Package) -> String {
    let rels = read(package, "_rels/.rels").unwrap_or_default();
    let target = Tags::new(&rels).filter(|tag| tag.name == b"Relationship" && !tag.end).find_map(|tag| {
        let mut target = None;
        let mut is_model = false;
        for (name, value) in tag.attributes() {
            match name {
                b"Target" => target = Some(text(value)),
                b"Type" => is_model = value.ends_with(MODEL_RELATIONSHIP.as_bytes()),
                _ => {}
            }
        }
        target.filter(|_| is_model)
    });
    part_name(&target.unwrap_or_else(|| DEFAULT_MODEL.to_string()))
}

/// A part name as the ZIP stores it: without the leading `/`.
fn part_name(name: &str) -> String {
    name.trim_start_matches('/').to_string()
}

/// The bytes of the package part `name` (part names compare without case, as the OPC standard wants).
fn read(package: &Package, name: &str) -> std::result::Result<Vec<u8>, String> {
    let mut package = package.clone();
    let stored = match package.index_for_name(name) {
        Some(_) => name.to_string(),
        None => package
            .file_names()
            .flatten() // zip 9: names that are not valid text are errors
            .find(|n| n.eq_ignore_ascii_case(name))
            .map(|n| n.into_owned())
            .ok_or_else(|| format!("{name} is missing from the package"))?,
    };
    let mut file = package.by_name(&stored).map_err(|e| format!("{name}: {e}"))?;
    let mut bytes = Vec::with_capacity(file.size() as usize);
    file.read_to_end(&mut bytes).map_err(|e| format!("{name}: {e}"))?;
    Ok(bytes)
}

// ---------------------------------------------------------------------------------------------------------------
// XML

/// One XML tag: its local name (without namespace prefix), the raw text of its attributes, and whether it is an
/// end tag (`</name>`) or closes itself (`<name/>`).
struct Tag<'a> {
    name: &'a [u8],
    attributes: &'a [u8],
    end: bool,
    empty: bool,
}

impl<'a> Tag<'a> {
    /// The attributes as (local name, raw value) pairs.
    fn attributes(&self) -> impl Iterator<Item = (&'a [u8], &'a [u8])> {
        let text = self.attributes;
        let mut at = 0;
        std::iter::from_fn(move || {
            let eq = at + memchr::memchr(b'=', &text[at..])?;
            let name = text[at..eq].trim_ascii();
            let open = eq + 1 + text[eq + 1..].iter().position(|b| !b.is_ascii_whitespace())?;
            let quote = text[open];
            if quote != b'"' && quote != b'\'' {
                return None;
            }
            let close = open + 1 + memchr::memchr(quote, &text[open + 1..])?;
            at = close + 1;
            Some((local(name), &text[open + 1..close]))
        })
    }

    /// The value of attribute `name`.
    fn get(&self, name: &[u8]) -> Option<&'a [u8]> {
        self.attributes().find(|(n, _)| *n == name).map(|(_, v)| v)
    }
}

/// A name without its namespace prefix.
fn local(name: &[u8]) -> &[u8] {
    name.iter().rposition(|&b| b == b':').map_or(name, |i| &name[i + 1..])
}

/// The tags of an XML document in order; comments, declarations, processing instructions and text are skipped.
struct Tags<'a> {
    xml: &'a [u8],
    at: usize,
}

impl<'a> Tags<'a> {
    fn new(xml: &'a [u8]) -> Self {
        Self { xml, at: 0 }
    }

    /// The text from the current position up to the next tag.
    fn text(&self) -> &'a [u8] {
        let rest = &self.xml[self.at..];
        &rest[..memchr::memchr(b'<', rest).unwrap_or(rest.len())]
    }

    /// Jump past the content of the element just opened, up to its end tag `</name>` (which comes next).
    fn skip_to_end(&mut self, name: &[u8]) -> Option<&'a [u8]> {
        let start = self.at;
        let mut at = start;
        loop {
            let open = at + memmem::find(&self.xml[at..], b"</")?;
            let rest = &self.xml[open + 2..];
            let length = rest.iter().position(|&b| b == b'>' || b.is_ascii_whitespace())?;
            if local(&rest[..length]) == name {
                self.at = open;
                return Some(&self.xml[start..open]);
            }
            at = open + 2;
        }
    }
}

impl<'a> Iterator for Tags<'a> {
    type Item = Tag<'a>;

    fn next(&mut self) -> Option<Tag<'a>> {
        let xml = self.xml;
        loop {
            let start = self.at + memchr::memchr(b'<', &xml[self.at..])?;
            let rest = &xml[start + 1..];
            if rest.starts_with(b"!--") {
                self.at = start + 4 + memmem::find(&rest[3..], b"-->")? + 3;
                continue;
            }
            if rest.starts_with(b"![CDATA[") {
                self.at = start + 9 + memmem::find(&rest[8..], b"]]>")? + 3;
                continue;
            }
            if matches!(rest.first(), Some(b'?' | b'!')) {
                self.at = start + 2 + memchr::memchr(b'>', rest)?;
                continue;
            }
            // the closing '>', outside quoted attribute values
            let mut at = start + 1;
            loop {
                let found = at + memchr::memchr3(b'>', b'"', b'\'', &xml[at..])?;
                if xml[found] == b'>' {
                    at = found;
                    break;
                }
                at = found + 1 + memchr::memchr(xml[found], &xml[found + 1..])? + 1;
            }
            self.at = at + 1;
            let mut body = &xml[start + 1..at];
            let end = body.first() == Some(&b'/');
            if end {
                body = &body[1..];
            }
            let empty = body.last() == Some(&b'/');
            if empty {
                body = &body[..body.len() - 1];
            }
            let length = body.iter().position(|b| b.is_ascii_whitespace()).unwrap_or(body.len());
            return Some(Tag { name: local(&body[..length]), attributes: &body[length..], end, empty });
        }
    }
}

/// Attribute or element text with its character references resolved.
fn text(raw: &[u8]) -> String {
    let raw = String::from_utf8_lossy(raw);
    if !raw.contains('&') {
        return raw.trim().to_string();
    }
    let mut out = String::with_capacity(raw.len());
    let mut rest = &raw[..];
    while let Some(at) = rest.find('&') {
        out.push_str(&rest[..at]);
        let entity = &rest[at + 1..];
        let Some(end) = entity.find(';') else { break };
        let resolved = match &entity[..end] {
            "amp" => Some('&'),
            "lt" => Some('<'),
            "gt" => Some('>'),
            "quot" => Some('"'),
            "apos" => Some('\''),
            code => code
                .strip_prefix("#x")
                .map(|hex| u32::from_str_radix(hex, 16).ok())
                .unwrap_or_else(|| code.strip_prefix('#').and_then(|dec| dec.parse().ok()))
                .and_then(char::from_u32),
        };
        match resolved {
            Some(c) => {
                out.push(c);
                rest = &entity[end + 1..];
            }
            None => {
                out.push('&');
                rest = entity;
            }
        }
    }
    out.push_str(rest);
    out.trim().to_string()
}

/// A whole number.
fn integer(raw: &[u8]) -> Option<u32> {
    let raw = raw.trim_ascii();
    if raw.is_empty() || raw.len() > 10 {
        return None;
    }
    raw.iter()
        .try_fold(0u64, |n, &b| b.is_ascii_digit().then(|| n * 10 + (b - b'0') as u64))
        .and_then(|n| u32::try_from(n).ok())
}

/// A number.
fn float(raw: &[u8]) -> Option<f64> {
    fast_float2::parse(raw.trim_ascii()).ok()
}

/// A 3MF transform: twelve numbers, the 3×3 matrix by rows then the translation (points are row vectors).
fn transform(raw: &[u8]) -> Option<DMat4> {
    let values: Vec<f64> =
        raw.split(|b| b.is_ascii_whitespace()).filter(|s| !s.is_empty()).map(float).collect::<Option<_>>()?;
    let m: [f64; 12] = values.try_into().ok()?;
    Some(DMat4::from_cols_array(&[
        m[0], m[1], m[2], 0.0, m[3], m[4], m[5], 0.0, m[6], m[7], m[8], 0.0, m[9], m[10], m[11], 1.0,
    ]))
}

/// A colour `#RRGGBB` or `#RRGGBBAA` (the alpha is dropped).
fn colour(raw: &[u8]) -> Option<[u8; 3]> {
    let hex = std::str::from_utf8(raw).ok()?.trim().strip_prefix('#')?;
    if hex.len() != 6 && hex.len() != 8 {
        return None;
    }
    let byte = |i: usize| u8::from_str_radix(hex.get(i..i + 2)?, 16).ok();
    Some([byte(0)?, byte(2)?, byte(4)?])
}

// ---------------------------------------------------------------------------------------------------------------
// Model files

/// One model file of the package: the root model, or one the Production extension refers to.
#[derive(Default)]
struct ModelFile {
    /// Millimetres per unit of the file's coordinates, and the unit's name.
    unit: f64,
    unit_name: String,
    objects: HashMap<u32, Object>,
    items: Vec<Item>,
    /// The colours of the property groups (`basematerials`, `colorgroup`) by id.
    groups: HashMap<u32, Vec<[u8; 3]>>,
    /// The model's metadata (`Title`, `Designer`, `Application`…), non-empty values only.
    metadata: Vec<(String, String)>,
}

impl ModelFile {
    /// The other model files this one refers to.
    fn references(&self) -> impl Iterator<Item = String> + '_ {
        let components = self.objects.values().flat_map(|o| match &o.shape {
            Shape::Components(list) => list.iter().filter_map(|c| c.path.clone()).collect::<Vec<_>>(),
            Shape::Mesh(_) => Vec::new(),
        });
        components.chain(self.items.iter().filter_map(|i| i.path.clone()))
    }

    /// The value of metadata `name`.
    fn metadata(&self, name: &str) -> Option<&str> {
        self.metadata.iter().find(|(n, _)| n.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
    }
}

/// An object resource.
struct Object {
    name: Option<String>,
    /// A support structure (types `support` and `solidsupport`).
    support: bool,
    /// The object's own property: group id and index (`pid`, `pindex`).
    property: Option<(u32, u32)>,
    shape: Shape,
}

enum Shape {
    Mesh(Faces),
    Components(Vec<Component>),
}

/// An object's mesh as stored, with what its triangles carry.
#[derive(Default)]
struct Faces {
    vertices: Vec<[f32; 3]>,
    triangles: Vec<[u32; 3]>,
    /// Triangles with properties: (triangle, group id or `u32::MAX` for the object's, index at each corner).
    properties: Vec<(u32, u32, [u32; 3])>,
    /// Painted triangles: (triangle, painting code), in triangle order.
    paint: Vec<(u32, Box<str>)>,
}

/// A reference to an object: in the same model file, or in the model file `path`.
struct Component {
    path: Option<String>,
    object: u32,
    transform: DMat4,
}

/// A build item: an object placed for printing.
struct Item {
    path: Option<String>,
    object: u32,
    transform: DMat4,
}

/// Parse one model file.
fn parse_model(xml: &[u8]) -> std::result::Result<ModelFile, String> {
    let mut file = ModelFile { unit: 1.0, ..ModelFile::default() };
    let mut tags = Tags::new(xml);
    let mut object: Option<(u32, Object)> = None;
    let mut group: Option<(u32, Vec<[u8; 3]>)> = None;
    while let Some(tag) = tags.next() {
        if tag.end {
            match tag.name {
                b"object" => {
                    if let Some((id, done)) = object.take() {
                        file.objects.insert(id, done);
                    }
                }
                b"basematerials" | b"colorgroup" => {
                    if let Some((id, colours)) = group.take() {
                        file.groups.insert(id, colours);
                    }
                }
                _ => {}
            }
            continue;
        }
        match tag.name {
            b"model" => {
                file.unit_name = tag.get(b"unit").map(text).unwrap_or_else(|| "millimeter".into());
                file.unit = match file.unit_name.as_str() {
                    "micron" => 0.001,
                    "centimeter" => 10.0,
                    "inch" => 25.4,
                    "foot" => 304.8,
                    "meter" => 1000.0,
                    _ => 1.0,
                };
            }
            b"metadata" if !tag.empty => {
                if let Some(name) = tag.get(b"name") {
                    let value = text(tags.text());
                    if !value.is_empty() {
                        file.metadata.push((text(name), value));
                    }
                }
            }
            b"object" => {
                let id = tag.get(b"id").and_then(integer).ok_or("an object has no id")?;
                let kind = tag.get(b"type").map(text).unwrap_or_default();
                let property =
                    tag.get(b"pid").and_then(integer).map(|g| (g, tag.get(b"pindex").and_then(integer).unwrap_or(0)));
                let name = tag.get(b"name").map(text).filter(|n| !n.is_empty());
                let shape = Shape::Components(Vec::new());
                let support = kind == "support" || kind == "solidsupport";
                object = Some((id, Object { name, support, property, shape }));
                if tag.empty {
                    let (id, done) = object.take().unwrap();
                    file.objects.insert(id, done);
                }
            }
            b"mesh" => {
                if let Some((_, current)) = &mut object {
                    current.shape = Shape::Mesh(Faces::default());
                }
            }
            b"vertices" if !tag.empty => {
                let section = tags.skip_to_end(b"vertices").ok_or("a vertex list is not closed")?;
                if let Some((_, Object { shape: Shape::Mesh(faces), .. })) = &mut object {
                    faces.vertices = parse_vertices(section)?;
                }
            }
            b"triangles" if !tag.empty => {
                let section = tags.skip_to_end(b"triangles").ok_or("a triangle list is not closed")?;
                if let Some((_, Object { shape: Shape::Mesh(faces), .. })) = &mut object {
                    parse_triangles(section, faces)?;
                }
            }
            b"component" => {
                let id = tag.get(b"objectid").and_then(integer).ok_or("a component names no object")?;
                let component = Component {
                    path: tag.get(b"path").map(|p| part_name(&text(p))),
                    object: id,
                    transform: tag.get(b"transform").and_then(transform).unwrap_or(DMat4::IDENTITY),
                };
                if let Some((_, Object { shape: Shape::Components(list), .. })) = &mut object {
                    list.push(component);
                }
            }
            b"item" => {
                let id = tag.get(b"objectid").and_then(integer).ok_or("a build item names no object")?;
                file.items.push(Item {
                    path: tag.get(b"path").map(|p| part_name(&text(p))),
                    object: id,
                    transform: tag.get(b"transform").and_then(transform).unwrap_or(DMat4::IDENTITY),
                });
            }
            b"basematerials" | b"colorgroup" => {
                let id = tag.get(b"id").and_then(integer).ok_or("a material group has no id")?;
                group = Some((id, Vec::new()));
                if tag.empty {
                    file.groups.insert(id, Vec::new());
                    group = None;
                }
            }
            b"base" | b"color" => {
                if let Some((_, colours)) = &mut group {
                    let value = tag.get(b"displaycolor").or_else(|| tag.get(b"color"));
                    colours.push(value.and_then(colour).unwrap_or(NO_COLOUR));
                }
            }
            _ => {}
        }
    }
    Ok(file)
}

/// Split the content of a vertex or triangle list into about one piece per thread, at tag starts.
fn pieces(section: &[u8]) -> Vec<&[u8]> {
    let parts = rayon::current_num_threads() * 4;
    let target = (section.len() / parts.max(1)).max(1 << 16);
    let mut out = Vec::new();
    let mut start = 0;
    while start < section.len() {
        let mut end = (start + target).min(section.len());
        if end < section.len() {
            end = memchr::memchr(b'<', &section[end..]).map_or(section.len(), |i| end + i);
        }
        out.push(&section[start..end]);
        start = end;
    }
    out
}

/// The `<vertex x= y= z=/>` tags of a vertex list, parsed in parallel.
fn parse_vertices(section: &[u8]) -> std::result::Result<Vec<[f32; 3]>, String> {
    let parsed: Vec<std::result::Result<Vec<[f32; 3]>, String>> = pieces(section)
        .into_par_iter()
        .map(|piece| {
            let mut out = Vec::with_capacity(piece.len() / 48);
            for tag in Tags::new(piece).filter(|t| t.name == b"vertex" && !t.end) {
                let mut p = [f32::NAN; 3];
                for (name, value) in tag.attributes() {
                    let axis = match name {
                        b"x" => 0,
                        b"y" => 1,
                        b"z" => 2,
                        _ => continue,
                    };
                    p[axis] =
                        fast_float2::parse(value.trim_ascii()).map_err(|_| "a vertex coordinate is not a number")?;
                }
                if p.iter().any(|v| v.is_nan()) {
                    return Err("a vertex lacks a coordinate".to_string());
                }
                out.push(p);
            }
            Ok(out)
        })
        .collect();
    let mut vertices = Vec::new();
    for piece in parsed {
        vertices.extend(piece?);
    }
    Ok(vertices)
}

/// The `<triangle v1= v2= v3= .../>` tags of a triangle list, parsed in parallel, with their properties and
/// painting.
fn parse_triangles(section: &[u8], faces: &mut Faces) -> std::result::Result<(), String> {
    type Parsed = (Vec<[u32; 3]>, Vec<(u32, u32, [u32; 3])>, Vec<(u32, Box<str>)>);
    let parsed: Vec<std::result::Result<Parsed, String>> = pieces(section)
        .into_par_iter()
        .map(|piece| {
            let (mut triangles, mut properties, mut paint) =
                (Vec::with_capacity(piece.len() / 40), Vec::new(), Vec::new());
            for tag in Tags::new(piece).filter(|t| t.name == b"triangle" && !t.end) {
                let (mut v, mut p, mut pid) = ([u32::MAX; 3], [u32::MAX; 3], u32::MAX);
                let index = triangles.len() as u32;
                for (name, value) in tag.attributes() {
                    match name {
                        b"v1" | b"v2" | b"v3" => {
                            v[(name[1] - b'1') as usize] = integer(value).ok_or("a triangle corner is not a number")?
                        }
                        b"p1" | b"p2" | b"p3" => p[(name[1] - b'1') as usize] = integer(value).unwrap_or(u32::MAX),
                        b"pid" => pid = integer(value).unwrap_or(u32::MAX),
                        b"mmu_segmentation" | b"paint_color" if !value.is_empty() => {
                            paint.push((index, String::from_utf8_lossy(value).into()));
                        }
                        _ => {}
                    }
                }
                if v.contains(&u32::MAX) {
                    return Err("a triangle lacks a corner".to_string());
                }
                if p[0] != u32::MAX || pid != u32::MAX {
                    let first = if p[0] == u32::MAX { 0 } else { p[0] };
                    let corner = |c: u32| if c == u32::MAX { first } else { c };
                    properties.push((index, pid, [first, corner(p[1]), corner(p[2])]));
                }
                triangles.push(v);
            }
            Ok((triangles, properties, paint))
        })
        .collect();
    for piece in parsed {
        let (triangles, properties, paint) = piece?;
        let offset = faces.triangles.len() as u32;
        faces.triangles.extend(triangles);
        faces.properties.extend(properties.into_iter().map(|(t, g, p)| (t + offset, g, p)));
        faces.paint.extend(paint.into_iter().map(|(t, code)| (t + offset, code)));
    }
    let count = faces.vertices.len() as u32;
    if faces.triangles.iter().flatten().any(|&v| v >= count) {
        return Err("a triangle refers to a vertex that does not exist".into());
    }
    Ok(())
}

// ---------------------------------------------------------------------------------------------------------------
// Slicer data

/// What a slicer (PrusaSlicer, Bambu Studio, OrcaSlicer) saved next to the model.
#[derive(Default)]
struct Slicer {
    /// The program, e.g. `PrusaSlicer 2.9.6`.
    program: Option<String>,
    /// Colours of extruders (filaments) 1, 2…
    palette: Vec<[u8; 3]>,
    /// By the id of the object in the root model.
    objects: HashMap<u32, SlicerObject>,
    plates: Vec<Plate>,
}

/// A slicer's object: its name, extruder and parts.
#[derive(Default)]
struct SlicerObject {
    name: Option<String>,
    /// Extruder of its parts that name none (0: not given, the first).
    extruder: u32,
    /// PrusaSlicer: the parts as ranges of the object's triangles.
    volumes: Vec<Volume>,
    /// Bambu Studio and OrcaSlicer: the parts by the id of their component's object.
    parts: HashMap<u32, Volume>,
}

/// A part of a slicer's object.
#[derive(Default)]
struct Volume {
    /// Its triangles, `first..=last` (PrusaSlicer).
    first: u32,
    last: u32,
    /// 0: the object's.
    extruder: u32,
    /// A printed part (not a modifier, negative volume, support blocker or enforcer).
    printed: bool,
}

impl Slicer {
    fn read(package: &Package) -> Self {
        let mut slicer = Self::default();
        // PrusaSlicer: the parts in Slic3r_PE_model.config, the settings as "; key = value" lines
        if let Ok(xml) = read(package, "Metadata/Slic3r_PE_model.config") {
            slicer.objects = objects_config(&xml);
        }
        if let Ok(config) = read(package, "Metadata/Slic3r_PE.config") {
            slicer.prusa_settings(&String::from_utf8_lossy(&config));
        }
        // Bambu Studio and OrcaSlicer: the parts in model_settings.config, the settings as JSON
        if let Ok(xml) = read(package, "Metadata/model_settings.config") {
            slicer.objects = objects_config(&xml);
            let plates = Tags::new(&xml).filter(|t| t.name == b"plate" && !t.end && !t.empty).count();
            if let Ok(json) = read(package, "Metadata/project_settings.config") {
                slicer.bambu_settings(&json, plates.max(1));
            }
        }
        slicer
    }

    /// PrusaSlicer's settings: extruder colours (or filament colours) and the bed.
    fn prusa_settings(&mut self, config: &str) {
        let mut settings: HashMap<&str, &str> = HashMap::new();
        for line in config.lines() {
            if let Some((key, value)) = line.strip_prefix(';').and_then(|l| l.split_once('=')) {
                settings.insert(key.trim(), value.trim());
            }
            if let Some(rest) = line.strip_prefix("; generated by ") {
                self.program = rest.split(" on ").next().map(str::to_string);
            }
        }
        let list =
            |key: &str| settings.get(key).map(|v| v.split(';').map(str::trim).collect::<Vec<_>>()).unwrap_or_default();
        let (extruders, filaments) = (list("extruder_colour"), list("filament_colour"));
        self.palette = (0..extruders.len().max(filaments.len()))
            .map(|i| {
                let pick = |l: &[&str]| l.get(i).and_then(|c| colour(c.as_bytes()));
                pick(&extruders).or_else(|| pick(&filaments)).unwrap_or(NO_COLOUR)
            })
            .collect();
        let corners: Vec<&str> = settings.get("bed_shape").map(|s| s.split(',').collect()).unwrap_or_default();
        let height = settings.get("max_print_height").and_then(|h| h.parse().ok()).unwrap_or(0.0);
        if let Some(plate) = plate(&corners, height, DVec3::ZERO) {
            self.plates.push(plate);
        }
    }

    /// Bambu Studio's and OrcaSlicer's settings: filament colours and `plates` build plates of the printer, laid
    /// out in a grid as those programs do.
    fn bambu_settings(&mut self, json: &[u8], plates: usize) {
        let Ok(settings) = serde_json::from_slice::<serde_json::Value>(json) else { return };
        let strings = |key: &str| -> Vec<String> {
            match &settings[key] {
                serde_json::Value::Array(list) => list.iter().filter_map(|v| v.as_str().map(str::to_string)).collect(),
                serde_json::Value::String(s) => s.split([';', ',']).map(str::to_string).collect(),
                _ => Vec::new(),
            }
        };
        self.palette = strings("filament_colour").iter().map(|c| colour(c.as_bytes()).unwrap_or(NO_COLOUR)).collect();
        let corners = strings("printable_area");
        let corners: Vec<&str> = corners.iter().map(String::as_str).collect();
        let height = match &settings["printable_height"] {
            serde_json::Value::Number(n) => n.as_f64().unwrap_or(0.0),
            serde_json::Value::String(s) => s.parse().unwrap_or(0.0),
            _ => 0.0,
        };
        let Some(first) = plate(&corners, height, DVec3::ZERO) else { return };
        // Bambu Studio's PartPlateList: columns ≈ √plates, each plate a fifth of its size away from the last
        let root = (plates as f64).sqrt();
        let columns = if root > root.round() { root.round() as usize + 1 } else { root.round() as usize }.max(1);
        for index in 0..plates {
            let (row, column) = (index / columns, index % columns);
            let offset = DVec3::new(
                column as f64 * first.size[0] * (1.0 + PLATE_GAP),
                -(row as f64) * first.size[1] * (1.0 + PLATE_GAP),
                0.0,
            );
            self.plates.extend(plate(&corners, height, offset));
        }
    }

    /// The extruder triangle `t` of the mesh of object `mesh_object` is printed with, as part of the root model's
    /// object `top`; `None` when that part is not printed.
    fn extruder(&self, top: u32, mesh_object: u32, t: u32) -> Option<u32> {
        let Some(object) = self.objects.get(&top) else { return Some(1) };
        let volume = object.parts.get(&mesh_object).or_else(|| {
            let i = object.volumes.partition_point(|v| v.last < t);
            object.volumes.get(i).filter(|v| v.first <= t)
        });
        let own = object.extruder.max(1);
        match volume {
            Some(v) if !v.printed => None,
            Some(v) if v.extruder > 0 => Some(v.extruder),
            _ => Some(own),
        }
    }

    /// The colour of extruder `extruder` (1 for the first).
    fn colour(&self, extruder: u32) -> [u8; 3] {
        self.palette.get(extruder.saturating_sub(1) as usize).or(self.palette.last()).copied().unwrap_or(NO_COLOUR)
    }
}

/// A build plate from the corners of its printable area (`"0x0"`, `"256x0"`…) and its height, moved by `offset`.
fn plate(corners: &[&str], height: f64, offset: DVec3) -> Option<Plate> {
    let points: Vec<(f64, f64)> = corners
        .iter()
        .filter_map(|c| {
            c.trim().split_once('x').and_then(|(x, y)| Some((x.trim().parse().ok()?, y.trim().parse().ok()?)))
        })
        .collect();
    let (min_x, max_x) = points.iter().fold((f64::MAX, f64::MIN), |(lo, hi), p| (lo.min(p.0), hi.max(p.0)));
    let (min_y, max_y) = points.iter().fold((f64::MAX, f64::MIN), |(lo, hi), p| (lo.min(p.1), hi.max(p.1)));
    (points.len() >= 3 && max_x > min_x && max_y > min_y && height > 0.0).then(|| Plate {
        size: [max_x - min_x, max_y - min_y, height],
        position: [(min_x + max_x) / 2.0 + offset.x, (min_y + max_y) / 2.0 + offset.y, offset.z],
    })
}

/// The objects of a slicer's model configuration: PrusaSlicer's `<object id><volume firstid lastid>` or Bambu
/// Studio's `<object id><part id subtype>`, with `<metadata key value>` for names, extruders and part types.
fn objects_config(xml: &[u8]) -> HashMap<u32, SlicerObject> {
    let mut objects = HashMap::new();
    let mut object: Option<(u32, SlicerObject)> = None;
    // the open part: (Bambu part id, the part); a PrusaSlicer volume has no id
    let mut part: Option<(Option<u32>, Volume)> = None;
    for tag in Tags::new(xml) {
        match (tag.name, tag.end) {
            (b"object", false) => {
                object = tag.get(b"id").and_then(integer).map(|id| (id, SlicerObject::default()));
                if tag.empty {
                    object = None;
                }
            }
            (b"object", true) | (b"plate", false) => {
                if let Some((id, done)) = object.take() {
                    objects.insert(id, done);
                }
            }
            (b"volume", false) => {
                let first = tag.get(b"firstid").and_then(integer).unwrap_or(0);
                let last = tag.get(b"lastid").and_then(integer).unwrap_or(u32::MAX);
                part = Some((None, Volume { first, last, extruder: 0, printed: true }));
            }
            (b"part", false) => {
                let subtype = tag.get(b"subtype").map(text).unwrap_or_default();
                let printed = subtype.is_empty() || subtype == "normal_part";
                part = Some((tag.get(b"id").and_then(integer), Volume { printed, ..Volume::default() }));
            }
            (b"volume" | b"part", true) => {
                if let (Some((id, done)), Some((_, owner))) = (part.take(), &mut object) {
                    match id {
                        Some(id) => {
                            owner.parts.insert(id, done);
                        }
                        None => owner.volumes.push(done),
                    }
                }
            }
            (b"metadata", false) => {
                let (Some(key), Some(value)) = (tag.get(b"key"), tag.get(b"value")) else { continue };
                let value = text(value);
                match (key, &mut part, &mut object) {
                    (b"extruder", Some((_, volume)), _) => volume.extruder = value.parse().unwrap_or(0),
                    (b"volume_type", Some((_, volume)), _) => volume.printed = value == "ModelPart",
                    (b"modifier", Some((_, volume)), _) => volume.printed &= value != "1",
                    (b"extruder", None, Some((_, owner))) => owner.extruder = value.parse().unwrap_or(0),
                    (b"name", None, Some((_, owner))) if !value.is_empty() => owner.name = Some(value),
                    _ => {}
                }
            }
            _ => {}
        }
    }
    if let Some((id, done)) = object {
        objects.insert(id, done);
    }
    for object in objects.values_mut() {
        object.volumes.sort_by_key(|v| v.first);
    }
    objects
}

// ---------------------------------------------------------------------------------------------------------------
// Painting

/// The pieces of the painted triangle `corners` with the extruder each is painted with (0: not painted), from its
/// painting code (PrusaSlicer's `TriangleSelector` serialisation, also written by Bambu Studio and OrcaSlicer).
///
/// The code's hex digits are read from the last one; each is a 4-bit code. Bits 0–1 give the number of split
/// sides. For a split triangle, bits 2–3 give its special side, and its children follow, last child first. For a
/// leaf, bits 2–3 give its state, or are both set and the next digit gives the state minus 3. `None` for a code
/// that does not decode exactly.
fn painted(code: &str, corners: [DVec3; 3]) -> Option<Vec<([DVec3; 3], u32)>> {
    let mut digits = code.bytes().rev().map(|c| (c as char).to_digit(16));
    let mut out = Vec::new();
    paint_walk(&mut digits, corners, &mut out, 0)?;
    digits.next().is_none().then_some(out)
}

fn paint_walk(
    digits: &mut impl Iterator<Item = Option<u32>>,
    corners: [DVec3; 3],
    out: &mut Vec<([DVec3; 3], u32)>,
    depth: usize,
) -> Option<()> {
    let code = digits.next()??;
    let sides = code & 0b11;
    if sides == 0 {
        let state = if code >> 2 == 0b11 { digits.next()?? + 3 } else { code >> 2 };
        out.push((corners, state));
        return Some(());
    }
    if depth > 24 {
        return None;
    }
    for child in split(corners, sides, (code >> 2) as usize).iter().rev() {
        paint_walk(digits, *child, out, depth + 1)?;
    }
    Some(())
}

/// The children of a triangle split on `sides` sides (PrusaSlicer's `TriangleSelector::perform_split`): with the
/// corners turned so that the special one (opposite the special side) comes first, one split side halves the side
/// opposite it; two split sides halve the two sides next to it; three halve all.
fn split(t: [DVec3; 3], sides: u32, special: usize) -> Vec<[DVec3; 3]> {
    let special = special % 3;
    let [a, b, c] = [t[special], t[(special + 1) % 3], t[(special + 2) % 3]];
    let mid = |p: DVec3, q: DVec3| (p + q) * 0.5;
    match sides {
        1 => {
            let bc = mid(b, c);
            vec![[a, b, bc], [bc, c, a]]
        }
        2 => {
            let (ab, ac) = (mid(a, b), mid(a, c));
            vec![[a, ab, ac], [ab, b, ac], [b, c, ac]]
        }
        _ => {
            let (ab, bc, ca) = (mid(a, b), mid(b, c), mid(c, a));
            vec![[a, ab, ca], [ab, b, bc], [bc, c, ca], [ab, bc, ca]]
        }
    }
}

// ---------------------------------------------------------------------------------------------------------------
// Scene

/// A mesh object reached from a build item through its components.
#[derive(Clone, Copy)]
struct Piece<'a> {
    file: &'a ModelFile,
    /// The mesh object's id in its file (Bambu Studio's part id), and the object itself.
    id: u32,
    object: &'a Object,
    faces: &'a Faces,
    /// Mesh -> the item's object.
    transform: DMat4,
}

/// The mesh objects of object `id` of `file`, with their transforms into it.
fn collect<'a>(
    files: &'a HashMap<String, ModelFile>,
    file: &'a str,
    id: u32,
    transform: DMat4,
    depth: usize,
    out: &mut Vec<Piece<'a>>,
) -> std::result::Result<(), String> {
    let model = files.get(file).ok_or_else(|| format!("{file} is missing"))?;
    let object = model.objects.get(&id).ok_or_else(|| format!("object {id} is missing"))?;
    match &object.shape {
        Shape::Mesh(faces) => out.push(Piece { file: model, id, object, faces, transform }),
        Shape::Components(list) if depth < MAX_DEPTH => {
            for component in list {
                let path = component.path.as_deref().unwrap_or(file);
                collect(files, path, component.object, transform * component.transform, depth + 1, out)?;
            }
        }
        Shape::Components(_) => return Err("its components nest too deep".into()),
    }
    Ok(())
}

/// How triangles get their colour.
#[derive(Clone, Copy, PartialEq)]
enum Colouring {
    /// None: MeshPeek's model colour.
    Off,
    /// By the slicer's extruders (parts and painting).
    Extruders,
    /// By the model's colour properties.
    Properties,
}

/// The extruder each printed triangle uses, with the painted pieces of painted ones; `visit` gets (triangle,
/// extruder of the whole triangle or `None` when it is painted in pieces, the pieces).
fn extruders(slicer: &Slicer, top: u32, piece: &Piece, mut visit: impl FnMut(u32, Option<u32>, &[([DVec3; 3], u32)])) {
    let corners =
        |t: usize| piece.faces.triangles[t].map(|v| DVec3::from_array(piece.faces.vertices[v as usize].map(f64::from)));
    let mut paint = piece.faces.paint.iter().peekable();
    for t in 0..piece.faces.triangles.len() as u32 {
        while paint.next_if(|(p, _)| *p < t).is_some() {}
        let code = paint.next_if(|(p, _)| *p == t).map(|(_, code)| code);
        let Some(base) = slicer.extruder(top, piece.id, t) else { continue };
        let leaves = code.and_then(|code| painted(code, corners(t as usize)));
        match leaves {
            Some(leaves) if leaves.len() > 1 => {
                let leaves: Vec<_> = leaves.into_iter().map(|(c, s)| (c, if s == 0 { base } else { s })).collect();
                visit(t, None, &leaves);
            }
            Some(leaves) if leaves[0].1 > 0 => visit(t, Some(leaves[0].1), &[]),
            _ => visit(t, Some(base), &[]),
        }
    }
}

/// Builds one scene object's mesh from its pieces, sharing vertices between triangles of the same colour.
struct Builder {
    vertices: Vec<[f32; 3]>,
    faces: Vec<[u32; 3]>,
    colours: Vec<[u8; 3]>,
    /// For each vertex of the current piece: its index in the output and the colour it got there (`u32::MAX`: not
    /// used yet).
    first: Vec<(u32, [u8; 3])>,
    /// The copies of vertices used with other colours.
    copies: HashMap<(u32, [u8; 3]), u32>,
    coloured: bool,
}

impl Builder {
    fn new(coloured: bool) -> Self {
        Self {
            vertices: Vec::new(),
            faces: Vec::new(),
            colours: Vec::new(),
            first: Vec::new(),
            copies: HashMap::new(),
            coloured,
        }
    }

    /// Start a piece whose vertices are `positions`.
    fn start(&mut self, count: usize) {
        self.first.clear();
        self.first.resize(count, (u32::MAX, [0; 3]));
        self.copies.clear();
    }

    /// The output index of the piece's vertex `v` (at `position`) with `colour`.
    fn vertex(&mut self, v: u32, position: [f32; 3], colour: [u8; 3]) -> u32 {
        let (index, first) = self.first[v as usize];
        if index != u32::MAX && (!self.coloured || first == colour) {
            return index;
        }
        if index != u32::MAX {
            if let Some(&copy) = self.copies.get(&(v, colour)) {
                return copy;
            }
        }
        let new = self.vertices.len() as u32;
        self.vertices.push(position);
        if self.coloured {
            self.colours.push(colour);
        }
        if index == u32::MAX {
            self.first[v as usize] = (new, colour);
        } else {
            self.copies.insert((v, colour), new);
        }
        new
    }

    /// A triangle with its own corners (a painted piece).
    fn loose(&mut self, corners: [[f32; 3]; 3], colour: [u8; 3]) {
        let start = self.vertices.len() as u32;
        self.vertices.extend(corners);
        if self.coloured {
            self.colours.extend([colour; 3]);
        }
        self.faces.push([start, start + 1, start + 2]);
    }

    fn finish(self) -> Mesh {
        let mut mesh = Mesh::new(self.vertices, self.faces);
        if self.coloured {
            mesh.colors = Some(self.colours);
        }
        mesh
    }
}

/// The mesh of a build item of the root model's object `top` made of `pieces`.
fn item_mesh(pieces: &[Piece], top: u32, slicer: &Slicer, colouring: Colouring) -> Mesh {
    let mut builder = Builder::new(colouring != Colouring::Off);
    for piece in pieces {
        let identity = piece.transform == DMat4::IDENTITY;
        let place = |p: DVec3| if identity { p } else { piece.transform.transform_point3(p) };
        let positions: Vec<[f32; 3]> = if identity {
            piece.faces.vertices.clone()
        } else {
            piece
                .faces
                .vertices
                .iter()
                .map(|v| place(DVec3::from_array(v.map(f64::from))).as_vec3().to_array())
                .collect()
        };
        builder.start(positions.len());
        let triangles = &piece.faces.triangles;
        match colouring {
            Colouring::Extruders | Colouring::Off
                if !slicer.objects.is_empty() || colouring == Colouring::Extruders =>
            {
                extruders(slicer, top, piece, |t, whole, leaves| {
                    if let Some(extruder) = whole {
                        let colour = slicer.colour(extruder);
                        let face = triangles[t as usize].map(|v| builder.vertex(v, positions[v as usize], colour));
                        builder.faces.push(face);
                    } else {
                        for (corners, extruder) in leaves {
                            builder.loose(corners.map(|c| place(c).as_vec3().to_array()), slicer.colour(*extruder));
                        }
                    }
                });
            }
            _ => {
                let groups = &piece.file.groups;
                let lookup = |group: u32, index: u32| groups.get(&group).and_then(|g| g.get(index as usize)).copied();
                let own = piece.object.property.and_then(|(g, i)| lookup(g, i)).unwrap_or(NO_COLOUR);
                let mut properties = piece.faces.properties.iter().peekable();
                for (t, triangle) in triangles.iter().enumerate() {
                    let colours = match properties.next_if(|p| p.0 == t as u32) {
                        Some(&(_, group, indices)) => {
                            let group =
                                if group == u32::MAX { piece.object.property.map_or(u32::MAX, |p| p.0) } else { group };
                            indices.map(|i| lookup(group, i).unwrap_or(own))
                        }
                        None => [own; 3],
                    };
                    let face =
                        [0, 1, 2].map(|k| builder.vertex(triangle[k], positions[triangle[k] as usize], colours[k]));
                    builder.faces.push(face);
                }
            }
        }
    }
    builder.finish()
}

/// A build item: the root model's object it places, the item, and the mesh objects it is made of.
type Placed<'a> = (u32, &'a Item, Vec<Piece<'a>>);

/// Whether the scene has more than one colour, and from where.
fn colouring(files: &HashMap<String, ModelFile>, items: &[Placed], slicer: &Slicer) -> Colouring {
    if !slicer.palette.is_empty() && !slicer.objects.is_empty() {
        let mut used = HashSet::new();
        for (top, _, pieces) in items {
            for piece in pieces {
                extruders(slicer, *top, piece, |_, whole, leaves| {
                    used.extend(whole.into_iter().chain(leaves.iter().map(|l| l.1)).map(|e| slicer.colour(e)));
                });
                if used.len() > 1 {
                    return Colouring::Extruders;
                }
            }
        }
    }
    let properties =
        items.iter().flat_map(|(_, _, p)| p).any(|p| p.object.property.is_some() || !p.faces.properties.is_empty());
    let any_colours = files.values().any(|f| f.groups.values().any(|g| !g.is_empty()));
    if properties && any_colours { Colouring::Properties } else { Colouring::Off }
}

/// Turn the parsed package into the scene: one object per build item.
fn build_scene(files: &HashMap<String, ModelFile>, root: &str, slicer: &Slicer, path: &Path) -> Scene {
    let model = &files[root];
    let mut scene = Scene { plates: slicer.plates.clone(), ..Scene::default() };
    let mut items: Vec<Placed> = Vec::new();
    for item in &model.items {
        let file = item.path.as_deref().unwrap_or(root);
        let mut pieces = Vec::new();
        match collect(files, file, item.object, DMat4::IDENTITY, 0, &mut pieces) {
            Ok(()) => items.push((item.object, item, pieces)),
            Err(reason) => scene.skipped.push(format!("object {}: {reason}", item.object)),
        }
    }
    let colouring = colouring(files, &items, slicer);

    let file_name = stem(path);
    let many = items.len() > 1;
    let mut info = Vec::new();
    if let Some(program) = model.metadata("Application").map(str::to_string).or_else(|| slicer.program.clone()) {
        info.push(("Made with".to_string(), program));
    }
    for key in ["Title", "Designer"] {
        if let Some(value) = model.metadata(key) {
            info.push((key.to_string(), value.to_string()));
        }
    }
    if model.unit != 1.0 {
        info.push(("Unit".to_string(), model.unit_name.clone()));
    }
    let meshes: Vec<Mesh> =
        items.par_iter().map(|(top, _, pieces)| item_mesh(pieces, *top, slicer, colouring)).collect();
    for (k, ((top, item, pieces), mesh)) in items.iter().zip(meshes).enumerate() {
        if mesh.faces.is_empty() {
            continue;
        }
        let object = &files[item.path.as_deref().unwrap_or(root)].objects[top];
        let name = slicer
            .objects
            .get(top)
            .and_then(|o| o.name.clone())
            .or_else(|| object.name.clone())
            .unwrap_or_else(|| if many { format!("{file_name} {}", k + 1) } else { file_name.clone() });
        let mut placed = SceneObject::new(name, mesh, path);
        placed.transform = DMat4::from_scale(DVec3::splat(model.unit)) * item.transform;
        if object.support || pieces.iter().all(|p| p.object.support) {
            placed.kind = ObjectKind::Support;
        }
        placed.info = info.clone();
        if pieces.len() > 1 {
            placed.info.push(("Parts".to_string(), pieces.len().to_string()));
        }
        if let Some(colours) = &placed.mesh.colors {
            let distinct: HashSet<[u8; 3]> = colours.iter().copied().collect();
            placed.info.push(("Colours".to_string(), distinct.len().to_string()));
        }
        scene.objects.push(placed);
    }
    scene
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    /// A 3MF package of `parts` (name, contents).
    fn package(parts: &[(&str, &str)]) -> Vec<u8> {
        let mut zip = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for (name, contents) in parts {
            zip.start_file(*name, zip::write::SimpleFileOptions::default()).unwrap();
            zip.write_all(contents.as_bytes()).unwrap();
        }
        zip.finish().unwrap().into_inner()
    }

    const RELS: &str = r#"<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>"#;

    /// A unit right-angled triangle pair (a square) as mesh XML.
    const SQUARE: &str = r#"<mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="1" y="1" z="0"/>
        <vertex x="0" y="1" z="0"/></vertices><triangles><triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="2" v3="3" pid="5" p1="1"/></triangles></mesh>"#;

    fn load(parts: &[(&str, &str)]) -> Scene {
        load_bytes(&package(parts), Path::new("t.3mf")).unwrap()
    }

    #[test]
    fn items_place_objects_and_components_in_other_files() {
        let root = format!(
            r#"<?xml version="1.0" encoding="UTF-8"?>
            <model unit="centimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
                   xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
              <metadata name="Title">Two &amp; one</metadata>
              <!-- a comment with <tags> inside -->
              <resources>
                <object id="1" name="square" type="model">{SQUARE}</object>
                <object id="2" type="model"><components>
                  <component objectid="1" transform="1 0 0 0 1 0 0 0 1 5 0 0"/>
                  <component p:path="/3D/Objects/o.model" objectid="7"/>
                </components></object>
              </resources>
              <build><item objectid="1"/><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 2"/></build>
            </model>"#
        );
        let other =
            format!(r#"<model unit="centimeter"><resources><object id="7">{SQUARE}</object></resources></model>"#);
        let scene = load(&[("_rels/.rels", RELS), ("3D/3dmodel.model", &root), ("3D/Objects/o.model", &other)]);
        assert_eq!(scene.objects.len(), 2);
        let (square, pair) = (&scene.objects[0], &scene.objects[1]);
        assert_eq!(square.name, "square");
        assert_eq!(square.mesh.faces.len(), 2);
        assert!(square.mesh.colors.is_none(), "a triangle names a colour group the file lacks: no colours");
        assert!(square.info.contains(&("Title".to_string(), "Two & one".to_string())));
        // the second item: both components, one moved by 5 units, all placed 2 units up, in centimetres
        assert_eq!((pair.name.as_str(), pair.mesh.faces.len()), ("t 2", 4));
        assert!(pair.mesh.vertices.iter().any(|v| v[0] == 6.0));
        assert_eq!(pair.transform.transform_point3(DVec3::ZERO), DVec3::new(0.0, 0.0, 20.0));
        assert_eq!(pair.transform.transform_point3(DVec3::X), DVec3::new(10.0, 0.0, 20.0));
    }

    #[test]
    fn colour_groups_colour_the_triangles() {
        let root = format!(
            r##"<model><resources>
                <basematerials id="5"><base name="red" displaycolor="#FF0000"/><base name="blue" displaycolor="#0000FFFF"/></basematerials>
                <object id="1" pid="5" pindex="0">{SQUARE}</object>
              </resources><build><item objectid="1"/></build></model>"##
        );
        let scene = load(&[("3D/3dmodel.model", &root)]);
        let mesh = &scene.objects[0].mesh;
        let colours = mesh.colors.as_ref().expect("coloured");
        let corner = |t: usize, k: usize| colours[mesh.faces[t][k] as usize];
        assert_eq!(corner(0, 0), [255, 0, 0]);
        assert_eq!(corner(1, 0), [0, 0, 255]);
        // the corners both triangles share were copied for the second colour
        assert_eq!(mesh.vertices.len(), 6);
    }

    #[test]
    fn prusaslicer_volumes_take_their_extruders_colours_and_modifiers_are_left_out() {
        let root = r#"<model unit="millimeter" xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06"><resources>
                <object id="1" type="model"><mesh><vertices>
                  <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>
                  <vertex x="5" y="0" z="0"/><vertex x="6" y="0" z="0"/><vertex x="5" y="1" z="0"/>
                  <vertex x="9" y="0" z="0"/><vertex x="9" y="1" z="0"/><vertex x="8" y="1" z="0"/>
                </vertices><triangles>
                  <triangle v1="0" v2="1" v3="2"/><triangle v1="3" v2="4" v3="5" slic3rpe:mmu_segmentation="841"/>
                  <triangle v1="6" v2="7" v3="8"/>
                </triangles></mesh></object>
              </resources><build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 100 100 0"/></build></model>"#;
        let model_config = r#"<config><object id="1" instances_count="1"><metadata type="object" key="name" value="Thing"/>
            <volume firstid="0" lastid="0"><metadata type="volume" key="extruder" value="2"/></volume>
            <volume firstid="1" lastid="1"><metadata type="volume" key="volume_type" value="ModelPart"/></volume>
            <volume firstid="2" lastid="2"><metadata type="volume" key="volume_type" value="ParameterModifier"/></volume>
            </object></config>"#;
        let settings = "; generated by PrusaSlicer 2.9.6 on 2026-09-15 at 12:05:43 UTC\n\
            ; bed_shape = 0x0,200x0,200x200,0x200\n; extruder_colour = #FF0000;;#0000FF\n\
            ; filament_colour = #111111;#00FF00;#222222\n; max_print_height = 250\n";
        let scene = load(&[
            ("3D/3dmodel.model", root),
            ("Metadata/Slic3r_PE_model.config", model_config),
            ("Metadata/Slic3r_PE.config", settings),
        ]);
        assert_eq!(scene.plates, vec![Plate { size: [200.0, 200.0, 250.0], position: [100.0, 100.0, 0.0] }]);
        let object = &scene.objects[0];
        assert_eq!(object.name, "Thing");
        assert!(object.info.contains(&("Made with".to_string(), "PrusaSlicer 2.9.6".to_string())));
        let mesh = &object.mesh;
        let colours = mesh.colors.as_ref().unwrap();
        // the modifier is left out: the first triangle (extruder 2: the filament colour, its extruder colour being
        // empty) and the two painted halves of the second
        assert_eq!(mesh.faces.len(), 3);
        assert!(mesh.vertices.iter().all(|v| v[0] < 8.0));
        let colour_of = |t: usize| colours[mesh.faces[t][0] as usize];
        assert_eq!(colour_of(0), [0, 255, 0]);
        let mut painted: Vec<[u8; 3]> = vec![colour_of(1), colour_of(2)];
        painted.sort();
        assert_eq!(painted, vec![[0, 255, 0], [255, 0, 0]]);
    }

    #[test]
    fn bambu_parts_and_plates() {
        let root = r#"<model unit="millimeter" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
            <metadata name="Application">BambuStudio-02.00.03.54</metadata><resources>
            <object id="2" type="model"><components><component p:path="/3D/Objects/object_1.model" objectid="1"/>
            <component p:path="/3D/Objects/object_1.model" objectid="3"/></components></object>
            </resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 400 100 0"/></build></model>"#;
        let parts = format!(
            r#"<model><resources><object id="1">{SQUARE}</object>
               <object id="3"><mesh><vertices><vertex x="0" y="0" z="1"/><vertex x="1" y="0" z="1"/><vertex x="0" y="1" z="1"/></vertices>
               <triangles><triangle v1="0" v2="1" v3="2" paint_color="0C"/></triangles></mesh></object></resources></model>"#
        );
        let model_settings = r#"<config><object id="2"><metadata key="name" value="Box"/><metadata key="extruder" value="1"/>
            <part id="1" subtype="normal_part"><metadata key="extruder" value="2"/></part>
            <part id="3" subtype="normal_part"/></object>
            <plate><metadata key="plater_id" value="1"/></plate><plate><metadata key="plater_id" value="2"/></plate></config>"#;
        let project = r##"{"filament_colour": ["#FFFFFF", "#00FF00", "#0000FF"], "printable_area": ["0x0", "256x0", "256x256", "0x256"],
            "printable_height": "250", "version": "02.00.03.54"}"##;
        let scene = load(&[
            ("_rels/.rels", RELS),
            ("3D/3dmodel.model", root),
            ("3D/Objects/object_1.model", &parts),
            ("Metadata/model_settings.config", model_settings),
            ("Metadata/project_settings.config", project),
        ]);
        assert_eq!(scene.plates.len(), 2);
        assert_eq!(scene.plates[1].position, [256.0 * 1.2 + 128.0, 128.0, 0.0]);
        let object = &scene.objects[0];
        assert_eq!((object.name.as_str(), object.mesh.faces.len()), ("Box", 3));
        assert!(object.info.contains(&("Made with".to_string(), "BambuStudio-02.00.03.54".to_string())));
        let colours = object.mesh.colors.as_ref().unwrap();
        let colour_of = |t: usize| colours[object.mesh.faces[t][0] as usize];
        // part 1 uses filament 2; part 3 is painted with filament 3 ("0C": state 3)
        assert_eq!([colour_of(0), colour_of(1), colour_of(2)], [[0, 255, 0], [0, 255, 0], [0, 0, 255]]);
    }

    #[test]
    fn painting_codes_decode_into_pieces() {
        let t = [DVec3::ZERO, DVec3::X, DVec3::Y];
        assert_eq!(painted("8", t), Some(vec![(t, 2)]));
        assert_eq!(painted("0C", t), Some(vec![(t, 3)]));
        assert_eq!(painted("1C", t), Some(vec![(t, 4)]));
        // a triangle split once (code 1): its second child painted 1 (code 4), its first painted 2 (code 8)
        let pieces = painted("841", t).unwrap();
        assert_eq!(pieces.len(), 2);
        let mid = DVec3::new(0.5, 0.5, 0.0);
        assert_eq!(pieces[0], ([mid, DVec3::Y, DVec3::ZERO], 1));
        assert_eq!(pieces[1], ([DVec3::ZERO, DVec3::X, mid], 2));
        // codes that leave digits over or run out are refused
        assert_eq!(painted("88", t), None);
        assert_eq!(painted("1", t), None);
        // all four children of a three-side split
        let four = painted("88883", t).unwrap();
        assert_eq!(four.len(), 4);
        let area = |c: &[DVec3; 3]| (c[1] - c[0]).cross(c[2] - c[0]).length() / 2.0;
        assert!((four.iter().map(|(c, _)| area(c)).sum::<f64>() - 0.5).abs() < 1e-12);
    }

    #[test]
    fn xml_tags_attributes_and_text() {
        let xml = br#"<?xml version="1.0"?><a:b c = 'x>y' d="&lt;1&gt;"/><e>t &#65;&#x42; </e>"#;
        let mut tags = Tags::new(xml);
        let tag = tags.next().unwrap();
        assert_eq!((tag.name, tag.empty, tag.end), (&b"b"[..], true, false));
        assert_eq!(tag.get(b"c"), Some(&b"x>y"[..]));
        assert_eq!(text(tag.get(b"d").unwrap()), "<1>");
        assert_eq!(tags.next().unwrap().name, b"e");
        assert_eq!(text(tags.text()), "t AB");
        assert!(tags.next().unwrap().end);
        assert!(tags.next().is_none());
        assert_eq!(integer(b" 42 "), Some(42));
        assert_eq!(integer(b"-1"), None);
        assert_eq!(colour(b"#0080FFCC"), Some([0, 128, 255]));
    }

    #[test]
    fn rejects_other_files() {
        assert!(load_bytes(b"solid not a 3mf", Path::new("t.3mf")).is_err());
        let broken =
            package(&[("3D/3dmodel.model", "<model><resources><object id=\"1\"><mesh><vertices><vertex x=\"0\"/>")]);
        assert!(load_bytes(&broken, Path::new("t.3mf")).is_err());
    }
}
