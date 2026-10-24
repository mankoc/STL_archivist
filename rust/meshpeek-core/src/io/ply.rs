//! PLY reader (ASCII and binary, little/big endian): vertex positions, optional vertex colours, faces.

use std::path::Path;

use crate::error::{LoadError, Result};
use crate::io::{fan, map_file};
use crate::mesh::Mesh;

/// Load a PLY file.
pub fn load(path: &Path) -> Result<Mesh> {
    load_bytes(&map_file(path)?, path)
}

/// Parse file contents already in memory; `path` only names the file in error messages.
pub fn load_bytes(data: &[u8], path: &Path) -> Result<Mesh> {
    parse(data).map_err(|e| LoadError::format(format!("{}: {e}", path.display())))
}

/// Body encoding named in the header.
#[derive(Clone, Copy, PartialEq)]
enum Format {
    Ascii,
    Little,
    Big,
}

/// Numeric property types.
#[derive(Clone, Copy)]
enum Scalar {
    I8,
    U8,
    I16,
    U16,
    I32,
    U32,
    F32,
    F64,
}

impl Scalar {
    /// The type for a header type name (`uchar`, `float32`, …).
    fn parse(name: &str) -> Option<Self> {
        Some(match name {
            "char" | "int8" => Self::I8,
            "uchar" | "uint8" => Self::U8,
            "short" | "int16" => Self::I16,
            "ushort" | "uint16" => Self::U16,
            "int" | "int32" => Self::I32,
            "uint" | "uint32" => Self::U32,
            "float" | "float32" => Self::F32,
            "double" | "float64" => Self::F64,
            _ => return None,
        })
    }

    /// Size in bytes in a binary body.
    fn size(self) -> usize {
        match self {
            Self::I8 | Self::U8 => 1,
            Self::I16 | Self::U16 => 2,
            Self::I32 | Self::U32 | Self::F32 => 4,
            Self::F64 => 8,
        }
    }
}

/// One `property` line of an element.
struct Property {
    name: String,
    scalar: Scalar,
    list_count: Option<Scalar>, // Some(count type) for list properties
}

/// One `element` of the header (e.g. `vertex 1000`) with its properties.
struct Element {
    name: String,
    count: usize,
    properties: Vec<Property>,
}

/// Reads values one after another from the body, whatever the encoding.
struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
    format: Format,
}

impl Reader<'_> {
    /// The next value, of type `scalar` in a binary body (any number in an ASCII one), as `f64`.
    fn value(&mut self, scalar: Scalar) -> std::result::Result<f64, String> {
        if self.format == Format::Ascii {
            while self.pos < self.data.len() && self.data[self.pos].is_ascii_whitespace() {
                self.pos += 1;
            }
            let start = self.pos;
            while self.pos < self.data.len() && !self.data[self.pos].is_ascii_whitespace() {
                self.pos += 1;
            }
            return fast_float2::parse(&self.data[start..self.pos]).map_err(|_| "bad number in ASCII body".to_string());
        }
        let size = scalar.size();
        let bytes = self.data.get(self.pos..self.pos + size).ok_or("file ends early")?;
        self.pos += size;
        let mut b = [0u8; 8];
        b[..size].copy_from_slice(bytes);
        if self.format == Format::Big {
            b[..size].reverse();
        }
        Ok(match scalar {
            Scalar::I8 => b[0] as i8 as f64,
            Scalar::U8 => b[0] as f64,
            Scalar::I16 => i16::from_le_bytes([b[0], b[1]]) as f64,
            Scalar::U16 => u16::from_le_bytes([b[0], b[1]]) as f64,
            Scalar::I32 => i32::from_le_bytes(b[..4].try_into().unwrap()) as f64,
            Scalar::U32 => u32::from_le_bytes(b[..4].try_into().unwrap()) as f64,
            Scalar::F32 => f32::from_le_bytes(b[..4].try_into().unwrap()) as f64,
            Scalar::F64 => f64::from_le_bytes(b),
        })
    }
}

/// Parse a PLY file: `x y z` (and `red green blue` when present) of the `vertex` element and the
/// `vertex_indices` list of `face` (polygons are fan-triangulated). Other elements are read past.
pub fn parse(data: &[u8]) -> std::result::Result<Mesh, String> {
    let end = memchr::memmem::find(data, b"end_header").ok_or("missing end_header")?;
    let body_start = memchr::memchr(b'\n', &data[end..]).map(|i| end + i + 1).ok_or("missing end_header line")?;
    let header = std::str::from_utf8(&data[..end]).map_err(|_| "header is not text")?;
    let (format, elements) = parse_header(header)?;
    let mut reader = Reader { data, pos: body_start, format };

    let mut mesh = Mesh::default();
    let mut colors = Vec::new();
    let mut polygon = Vec::new();
    for element in &elements {
        let column = |name: &str| element.properties.iter().position(|p| p.name == name);
        let xyz = [column("x"), column("y"), column("z")];
        let rgb = [column("red"), column("green"), column("blue")];
        let index_list = column("vertex_indices").or_else(|| column("vertex_index"));
        let mut row = vec![0f64; element.properties.len()];
        for _ in 0..element.count {
            polygon.clear();
            for (i, p) in element.properties.iter().enumerate() {
                match p.list_count {
                    None => row[i] = reader.value(p.scalar)?,
                    Some(count_type) => {
                        let n = reader.value(count_type)? as usize;
                        for _ in 0..n {
                            let v = reader.value(p.scalar)?;
                            if Some(i) == index_list {
                                polygon.push(v as u32);
                            }
                        }
                    }
                }
            }
            if element.name == "vertex" {
                let [Some(x), Some(y), Some(z)] = xyz else { return Err("vertex element without x, y, z".into()) };
                mesh.vertices.push([row[x] as f32, row[y] as f32, row[z] as f32]);
                if let [Some(r), Some(g), Some(b)] = rgb {
                    colors.push([row[r] as u8, row[g] as u8, row[b] as u8]);
                }
            } else if element.name == "face" {
                fan(&polygon, &mut mesh.faces);
            }
        }
    }
    if mesh.faces.is_empty() {
        return Err("no triangles found".into());
    }
    if mesh.faces.iter().flatten().any(|&i| i as usize >= mesh.vertices.len()) {
        return Err("face refers to a vertex that does not exist".into());
    }
    if colors.len() == mesh.vertices.len() {
        mesh.colors = Some(colors);
    }
    Ok(mesh)
}

/// The body format and the elements declared in the header text (up to `end_header`).
fn parse_header(header: &str) -> std::result::Result<(Format, Vec<Element>), String> {
    let mut lines = header.lines();
    if lines.next().map(str::trim) != Some("ply") {
        return Err("not a PLY file".into());
    }
    let mut format = None;
    let mut elements: Vec<Element> = Vec::new();
    for line in lines {
        let words: Vec<&str> = line.split_whitespace().collect();
        match words.as_slice() {
            ["format", f, ..] => {
                format = Some(match *f {
                    "ascii" => Format::Ascii,
                    "binary_little_endian" => Format::Little,
                    "binary_big_endian" => Format::Big,
                    other => return Err(format!("unknown format {other}")),
                })
            }
            ["element", name, count] => elements.push(Element {
                name: name.to_string(),
                count: count.parse().map_err(|_| format!("bad element count {count}"))?,
                properties: Vec::new(),
            }),
            ["property", "list", count, item, name] => {
                elements.last_mut().ok_or("property before element")?.properties.push(Property {
                    name: name.to_string(),
                    scalar: Scalar::parse(item).ok_or(format!("unknown type {item}"))?,
                    list_count: Some(Scalar::parse(count).ok_or(format!("unknown type {count}"))?),
                })
            }
            ["property", kind, name] => {
                elements.last_mut().ok_or("property before element")?.properties.push(Property {
                    name: name.to_string(),
                    scalar: Scalar::parse(kind).ok_or(format!("unknown type {kind}"))?,
                    list_count: None,
                })
            }
            _ => {} // comment, obj_info, ...
        }
    }
    Ok((format.ok_or("missing format line")?, elements))
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEADER: &str = "element vertex 3\nproperty float x\nproperty float y\nproperty float z\n\
                          property uchar red\nproperty uchar green\nproperty uchar blue\n\
                          element face 1\nproperty list uchar int vertex_indices\nend_header\n";

    #[test]
    fn ascii_with_colors() {
        let text = format!("ply\nformat ascii 1.0\n{HEADER}0 0 0 255 0 0\n1 0 0 0 255 0\n0 1 0 0 0 255\n3 0 1 2\n");
        let mesh = parse(text.as_bytes()).unwrap();
        assert_eq!(mesh.faces, vec![[0, 1, 2]]);
        assert_eq!(mesh.colors.unwrap(), vec![[255, 0, 0], [0, 255, 0], [0, 0, 255]]);
    }

    #[test]
    fn binary_little_endian() {
        let mut data = format!("ply\nformat binary_little_endian 1.0\n{HEADER}").into_bytes();
        for (v, c) in [([0f32, 0., 0.], [9u8, 8, 7]), ([1., 0., 0.], [0, 0, 0]), ([0., 1., 0.], [0, 0, 0])] {
            v.iter().for_each(|x| data.extend(x.to_le_bytes()));
            data.extend(c);
        }
        data.push(3);
        [0i32, 1, 2].iter().for_each(|i| data.extend(i.to_le_bytes()));
        let mesh = parse(&data).unwrap();
        assert_eq!(mesh.vertices[1], [1., 0., 0.]);
        assert_eq!(mesh.colors.unwrap()[0], [9, 8, 7]);
        assert_eq!(mesh.faces, vec![[0, 1, 2]]);
    }
}
