//! CHITUBOX print files (`.ctb`): the sliced layers of a resin print, as the printer receives them.
//!
//! A print file holds no mesh, only one image per layer. MeshPeek stacks the layers and rebuilds the solid they
//! print: the lit pixels are averaged into a grid of cells about a tenth of a millimetre wide (the grey levels of
//! anti-aliasing included), and the surface at half density is extracted with surface nets. The model and its
//! supports come out as one object, standing on the printer's plate as printed.
//!
//! Format (little-endian; versions 2 to 4, magic `0x12FD0086`; the encrypted format below):
//!
//! - header (112 bytes): magic, version, bed size (x, y, z in mm, float), total height, layer height, exposure,
//!   bottom exposure, light-off delay, bottom layer count, resolution (x, y), offsets of the large preview, the
//!   layer table and the small preview, the layer count, the print time (s), the projector type, the print
//!   parameters (offset, size), the anti-aliasing level, the light PWM, the encryption key and the slicer
//!   information (offset, size: the machine name and the slicer version);
//! - layer table: 36 bytes per layer: height (mm), exposure, light-off delay (floats), the offset and size of the
//!   layer's image, the page number (offsets beyond 4 GB) and three more words;
//! - layer image: run-length encoded, one grey level per byte: bits 0–6 are the grey level (`v << 1 | 1`, 0 is
//!   dark); with bit 7 set, a run length follows in 1 to 4 bytes (prefix `0`, `10`, `110`, `1110`, big-endian).
//!   With a non-zero encryption key, the bytes are XORed with a key stream of the key and the layer number.
//!
//! The encrypted format of newer CHITUBOX versions (magic `0x12FD0107`) has a 48-byte header (settings size and
//! offset, version, signature size and offset). Its settings (288 bytes: the same values in another order, with
//! the offset of a table of layer pointers) are encrypted with AES-256-CBC, as is part of some layers' image; the
//! key and IV are the ones UVtools found and publishes (`CTBEncryptedFile.cs`). Each layer pointer (16 bytes:
//! offset, page) leads to an 88-byte layer header (height, exposure, image size, and the offset and length of the
//! encrypted part of the image) followed by the image.

use std::borrow::Cow;
use std::path::Path;

use aes::cipher::{BlockDecrypt, KeyInit};
use rayon::prelude::*;

use crate::error::{LoadError, Result};
use crate::io::stem;
use crate::mesh::Mesh;
use crate::scene::{Plate, Scene, SceneObject};

/// First four bytes of a CTB file (versions 2 to 4), and of the encrypted format.
const MAGIC: u32 = 0x12FD_0086;
const MAGIC_ENCRYPTED: u32 = 0x12FD_0107;
/// Header size and the layer table's entry size.
const HEADER: usize = 112;
const LAYER: usize = 36;
/// The encrypted format: header, settings, layer pointer and layer header sizes.
const ENCRYPTED_HEADER: usize = 48;
const SETTINGS: usize = 288;
const POINTER: usize = 16;
const LAYER_HEADER: usize = 88;
/// AES-256 key and IV of the encrypted format: UVtools' `Secret1` and `Secret2` (base64) XORed with "UVtools".
const KEY: [u8; 32] = [
    0xD0, 0x5B, 0x8E, 0x33, 0x71, 0xDE, 0x3D, 0x1A, 0xE5, 0x4F, 0x22, 0xDD, 0xDF, 0x5B, 0xFD, 0x94, 0xAB, 0x5D, 0x64,
    0x3A, 0x9D, 0x7E, 0xBF, 0xAF, 0x42, 0x03, 0xF3, 0x10, 0xD8, 0x52, 0x2A, 0xEA,
];
const IV: [u8; 16] = [0x0F, 0x01, 0x0A, 0x05, 0x05, 0x0B, 0x06, 0x07, 0x08, 0x06, 0x0A, 0x0C, 0x0C, 0x0D, 0x09, 0x0F];
/// CHITUBOX writes broken images for some small encrypted layers (shorter than this and not whole AES blocks);
/// they are taken as empty, as UVtools does.
const BROKEN_SMALL_LAYER: usize = 512;
/// The cell size aimed at (mm), and the most cells the grid may have (it grows coarser for big prints).
const CELL: f64 = 0.1;
const MAX_CELLS: usize = 64 << 20;
/// Average grey level (0–255) of a cell at the surface.
const ISO: f32 = 127.5;

/// The header fields MeshPeek uses, from either format.
struct Header {
    version: u32,
    encrypted: bool,
    bed: [f64; 3],
    layer_height: f64,
    exposure: f64,
    bottom_exposure: f64,
    bottom_layers: u32,
    resolution: [usize; 2],
    layers: usize,
    print_time: u32,
    anti_alias: u32,
    /// Key of the layer images' key stream (0: none).
    key: u32,
    machine: Option<String>,
    slicer_version: Option<String>,
}

/// One layer's image in the file, and the part of it encrypted with AES (offset and length within it).
struct LayerImage<'a> {
    data: &'a [u8],
    aes: Option<(usize, usize)>,
}

/// A little-endian u32 at `at`, if the data is long enough.
fn word(data: &[u8], at: usize) -> Option<u32> {
    data.get(at..at.checked_add(4)?).map(|b| u32::from_le_bytes(b.try_into().unwrap()))
}

/// A little-endian f32 at `at`, as f64.
fn float(data: &[u8], at: usize) -> Option<f64> {
    word(data, at).map(|w| f32::from_bits(w) as f64)
}

/// A text of `length` bytes at `offset` (zero bytes and spaces trimmed), when it is there and not empty.
fn text_at(data: &[u8], offset: usize, length: usize) -> Option<String> {
    let text = data.get(offset..offset.checked_add(length)?)?;
    let text = String::from_utf8_lossy(text).trim_matches(char::from(0)).trim().to_string();
    (!text.is_empty()).then_some(text)
}

impl Header {
    /// The header and the layers' images of a print file of either format.
    fn read(data: &[u8]) -> std::result::Result<(Self, Vec<LayerImage<'_>>), String> {
        let (header, layers) = match word(data, 0) {
            Some(MAGIC) => Self::read_plain(data)?,
            Some(MAGIC_ENCRYPTED) => Self::read_encrypted(data)?,
            _ => return Err("not a CHITUBOX print file (unknown file signature)".into()),
        };
        let [x, y] = header.resolution;
        if x == 0 || y == 0 || x > 1 << 16 || y > 1 << 16 {
            return Err("the screen resolution is not valid".into());
        }
        if !header.bed.iter().all(|v| v.is_finite() && *v > 0.0) {
            return Err("the bed size is not valid".into());
        }
        if layers.is_empty() {
            return Err("the file has no layers".into());
        }
        Ok((header, layers))
    }

    /// Versions 2 to 4: the header, then a table of 36-byte layer entries.
    fn read_plain(data: &[u8]) -> std::result::Result<(Self, Vec<LayerImage<'_>>), String> {
        if data.len() < HEADER {
            return Err("the header is cut short".into());
        }
        let w = |at: usize| word(data, at).unwrap();
        let f = |at: usize| float(data, at).unwrap();
        let (table, count) = (w(64) as usize, w(68) as usize);
        if table.saturating_add(count.saturating_mul(LAYER)) > data.len() {
            return Err("the layer table is missing or cut short".into());
        }
        // the slicer information: the machine name (offset, length at +28) and the slicer version (+48)
        let (at, size) = (w(104) as usize, w(108) as usize);
        let slicer = (at != 0 && size >= 52 && at.saturating_add(size) <= data.len()).then_some(at);
        let machine = slicer.and_then(|at| text_at(data, w(at + 28) as usize, w(at + 32) as usize));
        let slicer_version = slicer
            .map(|at| w(at + 48))
            .filter(|v| *v != 0)
            .map(|v| format!("{}.{}.{}", v >> 24, v >> 16 & 0xff, v >> 8 & 0xff));
        let header = Self {
            version: w(4),
            encrypted: false,
            bed: [f(8), f(12), f(16)],
            layer_height: f(32),
            exposure: f(36),
            bottom_exposure: f(40),
            bottom_layers: w(48),
            resolution: [w(52) as usize, w(56) as usize],
            layers: count,
            print_time: w(76),
            anti_alias: w(92),
            key: w(100),
            machine,
            slicer_version,
        };
        let layers = (0..count)
            .map(|i| {
                let entry = table + i * LAYER;
                let offset = ((w(entry + 20) as usize) << 32) + w(entry + 12) as usize;
                let size = w(entry + 16) as usize;
                let data = data.get(offset..offset.saturating_add(size));
                data.map(|data| LayerImage { data, aes: None })
                    .ok_or_else(|| format!("layer {} lies outside the file", i + 1))
            })
            .collect::<std::result::Result<_, _>>()?;
        Ok((header, layers))
    }

    /// The encrypted format: a 48-byte header, AES-encrypted settings, then layer pointers to 88-byte layer
    /// headers, each followed by its image.
    fn read_encrypted(data: &[u8]) -> std::result::Result<(Self, Vec<LayerImage<'_>>), String> {
        if data.len() < ENCRYPTED_HEADER {
            return Err("the header is cut short".into());
        }
        let at = |offset: usize| word(data, offset).unwrap();
        let (size, offset, version) = (at(4) as usize, at(8) as usize, at(16));
        let encrypted =
            data.get(offset..offset.saturating_add(size)).filter(|s| s.len() >= SETTINGS && s.len() % 16 == 0);
        let mut settings = encrypted.ok_or("the settings are missing or cut short")?.to_vec();
        aes_decrypt(&mut settings);
        let w = |at: usize| word(&settings, at).unwrap();
        let f = |at: usize| float(&settings, at).unwrap();
        let (pointers, count) = (w(8) as usize, w(64) as usize);
        if count == 0 || pointers.saturating_add(count.saturating_mul(POINTER)) > data.len() {
            return Err("the settings do not decrypt (a newer key?) or the layer list is cut short".into());
        }
        let header = Self {
            version,
            encrypted: true,
            bed: [f(12), f(16), f(20)],
            layer_height: f(36),
            exposure: f(40),
            bottom_exposure: f(44),
            bottom_layers: w(52),
            resolution: [w(56) as usize, w(60) as usize],
            layers: count,
            print_time: w(76),
            anti_alias: w(176),
            key: w(128),
            machine: text_at(data, w(160) as usize, w(164) as usize),
            slicer_version: None,
        };
        let layers = (0..count)
            .map(|i| {
                let missing = || format!("layer {} lies outside the file", i + 1);
                let pointer = pointers + i * POINTER;
                let start = ((word(data, pointer + 4).ok_or_else(missing)? as usize) << 32)
                    + word(data, pointer).ok_or_else(missing)? as usize;
                let header = data.get(start..start + LAYER_HEADER).ok_or_else(missing)?;
                let h = |at: usize| word(header, at).unwrap() as usize;
                let image_start = start + h(0).max(LAYER_HEADER);
                let image = data.get(image_start..image_start.saturating_add(h(24))).ok_or_else(missing)?;
                let (aes_offset, aes_length) = (h(32), h(36));
                if aes_length == 0 {
                    return Ok(LayerImage { data: image, aes: None });
                }
                let whole = aes_length % 16 == 0 && aes_offset + aes_length <= image.len();
                if !whole && image.len() < BROKEN_SMALL_LAYER {
                    return Ok(LayerImage { data: &[], aes: None }); // CHITUBOX's broken small layer: empty
                }
                if !whole {
                    return Err(format!("the encrypted part of layer {} is not valid", i + 1));
                }
                Ok(LayerImage { data: image, aes: Some((aes_offset, aes_length)) })
            })
            .collect::<std::result::Result<_, String>>()?;
        Ok((header, layers))
    }

    /// Millimetres per pixel across and down the screen.
    fn pixel(&self) -> [f64; 2] {
        [self.bed[0] / self.resolution[0] as f64, self.bed[1] / self.resolution[1] as f64]
    }
}

/// Decrypt `bytes` (whole 16-byte blocks) in place with the encrypted format's AES-256-CBC key and IV.
fn aes_decrypt(bytes: &mut [u8]) {
    let cipher = aes::Aes256::new(&KEY.into());
    let mut previous = IV;
    for block in bytes.chunks_exact_mut(16) {
        let encrypted: [u8; 16] = (*block).try_into().unwrap();
        cipher.decrypt_block(aes::Block::from_mut_slice(block));
        block.iter_mut().zip(previous).for_each(|(b, p)| *b ^= p);
        previous = encrypted;
    }
}

/// The plain run-length bytes of layer `index`: its AES-encrypted part decrypted, then the key stream removed.
fn layer_bytes<'a>(layer: &LayerImage<'a>, key: u32, index: u32) -> Cow<'a, [u8]> {
    let mut bytes = Cow::Borrowed(layer.data);
    if let Some((offset, length)) = layer.aes {
        aes_decrypt(&mut bytes.to_mut()[offset..offset + length]);
    }
    if key != 0 {
        unstream(bytes.to_mut(), key, index);
    }
    bytes
}

/// XOR layer `index`'s image with the key stream of `key` (applying it twice gives the image back).
fn unstream(image: &mut [u8], key: u32, index: u32) {
    let seed = key.wrapping_mul(0x2d83_cdac).wrapping_add(0xd8a8_3423);
    let mut stream = index.wrapping_mul(0x1e15_30cd).wrapping_add(0xec3d_47cd).wrapping_mul(seed);
    for chunk in image.chunks_mut(4) {
        chunk.iter_mut().enumerate().for_each(|(i, b)| *b ^= (stream >> (8 * i)) as u8);
        stream = stream.wrapping_add(seed);
    }
}

/// The runs of a decrypted layer image: `visit(first pixel, length, grey level 0–255)` for each lit run. Returns
/// the number of pixels, `None` for a broken image.
fn runs(image: &[u8], mut visit: impl FnMut(usize, usize, u8)) -> Option<usize> {
    let mut pixel = 0usize;
    let mut n = 0;
    while n < image.len() {
        let mut code = image[n];
        let mut length = 1usize;
        if code & 0x80 != 0 {
            code &= 0x7f;
            n += 1;
            let first = *image.get(n)? as usize;
            let extra = match first {
                b if b & 0x80 == 0 => 0,
                b if b & 0xc0 == 0x80 => 1,
                b if b & 0xe0 == 0xc0 => 2,
                b if b & 0xf0 == 0xe0 => 3,
                _ => return None,
            };
            length = first & (0x7f >> extra);
            for _ in 0..extra {
                n += 1;
                length = length << 8 | *image.get(n)? as usize;
            }
        }
        if code != 0 {
            visit(pixel, length, code << 1 | 1);
        }
        pixel += length;
        n += 1;
    }
    Some(pixel)
}

/// Load a `.ctb` file whose bytes are `data`; `path` names it in errors and in the object.
pub fn load_bytes(data: &[u8], path: &Path) -> Result<Scene> {
    let bad = |what: String| LoadError::format(format!("{}: {what}", path.display()));
    let (header, images) = Header::read(data).map_err(bad)?;
    let [width, height] = header.resolution;

    // the lit area of all layers: columns and rows (first, last)
    let areas: Vec<Option<[usize; 4]>> = images
        .par_iter()
        .enumerate()
        .map(|(i, image)| {
            if image.data.is_empty() {
                return Ok(None);
            }
            let image = layer_bytes(image, header.key, i as u32);
            let mut area: Option<[usize; 4]> = None;
            let pixels = runs(&image, |start, length, _| {
                let (first_row, last_row) = (start / width, (start + length - 1) / width);
                let (first_col, last_col) =
                    if first_row == last_row { (start % width, (start + length - 1) % width) } else { (0, width - 1) };
                let a = area.get_or_insert([first_col, last_col, first_row, last_row]);
                *a = [a[0].min(first_col), a[1].max(last_col), a[2].min(first_row), a[3].max(last_row)];
            });
            match pixels {
                Some(n) if n == width * height => Ok(area),
                _ => Err(format!("the image of layer {} is broken", i + 1)),
            }
        })
        .collect::<std::result::Result<_, String>>()
        .map_err(bad)?;
    let lit =
        areas.iter().flatten().copied().reduce(|a, b| [a[0].min(b[0]), a[1].max(b[1]), a[2].min(b[2]), a[3].max(b[3])]);
    let Some([col0, col1, row0, row1]) = lit else { return Err(bad("no layer has lit pixels".into())) };
    let top = areas.iter().rposition(Option::is_some).unwrap_or(0) + 1;

    // the grid: `per_cell` pixels across, `per_slab` layers up, coarser until it fits
    let [px, py] = header.pixel();
    let layer_height =
        if header.layer_height > 0.0 { header.layer_height } else { header.bed[2] / header.layers as f64 };
    let mut target = CELL;
    let (per_cell, per_slab, nx, ny, nz) = loop {
        let per_cell = (target / px.max(py)).round().max(1.0) as usize;
        let per_slab = (target / layer_height).round().max(1.0) as usize;
        let (nx, ny, nz) = ((col1 - col0) / per_cell + 3, (row1 - row0) / per_cell + 3, top.div_ceil(per_slab) + 2);
        if nx * ny * nz <= MAX_CELLS {
            break (per_cell, per_slab, nx, ny, nz);
        }
        target *= 1.25;
    };

    // average grey level of each cell, slab by slab (with an empty border all around)
    let slabs: Vec<Vec<u8>> = (0..nz)
        .into_par_iter()
        .map(|k| {
            let mut grid = vec![0u8; nx * ny];
            if k == 0 || k == nz - 1 {
                return grid;
            }
            let first = (k - 1) * per_slab;
            let layers = first..(first + per_slab).min(top);
            let mut sums = vec![0u32; nx * ny];
            for i in layers.clone() {
                let image = layer_bytes(&images[i], header.key, i as u32);
                runs(&image, |start, length, grey| {
                    let (mut at, end) = (start, start + length);
                    while at < end {
                        let (row, col) = (at / width, at % width);
                        let stop = end.min((row + 1) * width);
                        if (row0..=row1).contains(&row) {
                            // rows count up from the bottom of the lit area, so that the grid's y goes up
                            let j = (row1 - row) / per_cell + 1;
                            let (mut c, last) = (col.max(col0), (stop - row * width - 1).min(col1));
                            while c <= last {
                                let cell = (c - col0) / per_cell;
                                let next = (col0 + (cell + 1) * per_cell - 1).min(last);
                                sums[j * nx + cell + 1] += grey as u32 * (next - c + 1) as u32;
                                c = next + 1;
                            }
                        }
                        at = stop;
                    }
                });
            }
            let full = (per_cell * per_cell * layers.len()) as u32;
            for (cell, sum) in grid.iter_mut().zip(sums) {
                *cell = (sum / full.max(1)).min(255) as u8;
            }
            grid
        })
        .collect();

    // grid sample (i, j, k) -> plate position (mm): the plate's centre is the origin
    let step = [per_cell as f64 * px, per_cell as f64 * py, per_slab as f64 * layer_height];
    let origin = [
        col0 as f64 * px - header.bed[0] / 2.0 + step[0] * 0.5 - step[0],
        header.bed[1] / 2.0 - (row1 + 1) as f64 * py + step[1] * 0.5 - step[1],
        step[2] * 0.5 - step[2],
    ];
    let mesh = surface_nets(&slabs, [nx, ny, nz], origin, step);
    if mesh.faces.is_empty() {
        return Err(bad("the layers hold no solid".into()));
    }

    let mut object = SceneObject::new(stem(path), mesh, path);
    let mut info = Vec::new();
    if let Some(machine) = &header.machine {
        info.push(("Printer".to_string(), machine.clone()));
    }
    if let Some(version) = &header.slicer_version {
        info.push(("Sliced with".to_string(), format!("CHITUBOX {version}")));
    }
    let version = if header.encrypted { format!("{} (encrypted)", header.version) } else { header.version.to_string() };
    info.extend([
        ("Version".to_string(), version),
        ("Layers".to_string(), format!("{} × {} mm", header.layers, round(layer_height, 3))),
        ("Height".to_string(), format!("{} mm", round(top as f64 * layer_height, 2))),
        (
            "Exposure".to_string(),
            format!(
                "{} s, bottom {} s ({} layers)",
                round(header.exposure, 2),
                round(header.bottom_exposure, 2),
                header.bottom_layers
            ),
        ),
        ("Print time".to_string(), duration(header.print_time)),
        ("Screen".to_string(), format!("{width} × {height} px, {} mm per pixel", round(px, 4))),
        ("Anti-aliasing".to_string(), header.anti_alias.max(1).to_string()),
        ("Shown with cells of".to_string(), format!("{} × {} mm", round(step[0], 3), round(step[2], 3))),
    ]);
    object.info = info;
    let plate = Plate { size: header.bed, position: [0.0, 0.0, 0.0] };
    Ok(Scene { objects: vec![object], plates: vec![plate], ..Scene::default() })
}

/// `value` with at most `digits` decimals, without trailing zeros.
fn round(value: f64, digits: usize) -> String {
    let text = format!("{value:.digits$}");
    if text.contains('.') { text.trim_end_matches('0').trim_end_matches('.').to_string() } else { text }
}

/// Seconds as hours and minutes.
fn duration(seconds: u32) -> String {
    let (hours, minutes) = (seconds / 3600, seconds % 3600 / 60);
    if hours > 0 { format!("{hours} h {minutes} min") } else { format!("{minutes} min") }
}

/// The surface where the density `slabs` (one `nx × ny` grid of 0–255 per z, x fastest) crosses [`ISO`], by naive
/// surface nets: one vertex per cell the surface passes through (the mean of the crossings on its edges), one quad
/// per grid edge it crosses, facing away from the dense side. Sample (i, j, k) lies at `origin + (i, j, k) *
/// step`. The grid's border must be empty, which closes the surface.
fn surface_nets(slabs: &[Vec<u8>], [nx, ny, nz]: [usize; 3], origin: [f64; 3], step: [f64; 3]) -> Mesh {
    let value = |i: usize, j: usize, k: usize| slabs[k][j * nx + i] as f32;
    // the vertices of each layer of cells, as (cell within the layer, position), in cell order
    let layers: Vec<Vec<(u32, [f32; 3])>> = (0..nz - 1)
        .into_par_iter()
        .map(|k| {
            let mut out = Vec::new();
            for j in 0..ny - 1 {
                for i in 0..nx - 1 {
                    let corners: [f32; 8] = std::array::from_fn(|c| value(i + (c & 1), j + (c >> 1 & 1), k + (c >> 2)));
                    let inside = corners.iter().filter(|v| **v > ISO).count();
                    if inside == 0 || inside == 8 {
                        continue;
                    }
                    let (mut sum, mut count) = ([0.0f32; 3], 0.0f32);
                    for (a, b) in EDGES {
                        let (va, vb) = (corners[a], corners[b]);
                        if (va > ISO) != (vb > ISO) {
                            let t = (ISO - va) / (vb - va);
                            for (axis, total) in sum.iter_mut().enumerate() {
                                let (pa, pb) = ((a >> axis & 1) as f32, (b >> axis & 1) as f32);
                                *total += pa + (pb - pa) * t;
                            }
                            count += 1.0;
                        }
                    }
                    let local = [i as f32, j as f32, k as f32];
                    let position = std::array::from_fn(|axis| {
                        (origin[axis] + (local[axis] + sum[axis] / count) as f64 * step[axis]) as f32
                    });
                    out.push(((j * (nx - 1) + i) as u32, position));
                }
            }
            out
        })
        .collect();
    let mut offsets = Vec::with_capacity(layers.len());
    let mut total = 0u32;
    for layer in &layers {
        offsets.push(total);
        total += layer.len() as u32;
    }
    let vertex = |i: usize, j: usize, k: usize| -> Option<u32> {
        let layer = &layers[k];
        let cell = (j * (nx - 1) + i) as u32;
        layer.binary_search_by_key(&cell, |v| v.0).ok().map(|at| offsets[k] + at as u32)
    };
    // quads: the grid edges in sample slab k (along x and y) and between slabs k and k + 1 (along z)
    let faces: Vec<Vec<[u32; 3]>> = (0..nz)
        .into_par_iter()
        .map(|k| {
            let mut out = Vec::new();
            let mut quad = |cells: [(usize, usize, usize); 4], outward: bool| {
                let Some(v) = cells.iter().map(|&(i, j, k)| vertex(i, j, k)).collect::<Option<Vec<u32>>>() else {
                    return;
                };
                if outward {
                    out.extend([[v[0], v[1], v[2]], [v[0], v[2], v[3]]]);
                } else {
                    out.extend([[v[0], v[2], v[1]], [v[0], v[3], v[2]]]);
                }
            };
            // (the border samples are empty, so edges there cross nothing: the quads only skip them)
            let between = k >= 1 && k + 1 < nz;
            for j in 0..ny - 1 {
                for i in 0..nx - 1 {
                    let here = value(i, j, k) > ISO;
                    if between && j >= 1 && here != (value(i + 1, j, k) > ISO) {
                        quad([(i, j - 1, k - 1), (i, j, k - 1), (i, j, k), (i, j - 1, k)], here);
                    }
                    if between && i >= 1 && here != (value(i, j + 1, k) > ISO) {
                        quad([(i - 1, j, k - 1), (i - 1, j, k), (i, j, k), (i, j, k - 1)], here);
                    }
                    if k + 1 < nz && i >= 1 && j >= 1 && here != (value(i, j, k + 1) > ISO) {
                        quad([(i - 1, j - 1, k), (i, j - 1, k), (i, j, k), (i - 1, j, k)], here);
                    }
                }
            }
            out
        })
        .collect();
    let vertices = layers.into_iter().flat_map(|layer| layer.into_iter().map(|(_, p)| p)).collect();
    Mesh::new(vertices, faces.concat())
}

/// The 12 edges of a cell as pairs of corners (corner `c` at offset `(c & 1, c >> 1 & 1, c >> 2)`).
const EDGES: [(usize, usize); 12] =
    [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)];

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// Run-length encode `pixels` (grey levels 0–255) as a CTB layer image.
    fn encode(pixels: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        let mut at = 0;
        while at < pixels.len() {
            let level = pixels[at] >> 1;
            let run = pixels[at..].iter().take_while(|p| *p >> 1 == level).count().min(0x0fff_ffff);
            if run == 1 {
                out.push(level);
            } else {
                out.push(level | 0x80);
                match run {
                    r if r < 0x80 => out.push(r as u8),
                    r if r < 0x4000 => out.extend([0x80 | (r >> 8) as u8, r as u8]),
                    r if r < 0x20_0000 => out.extend([0xc0 | (r >> 16) as u8, (r >> 8) as u8, r as u8]),
                    r => out.extend([0xe0 | (r >> 24) as u8, (r >> 16) as u8, (r >> 8) as u8, r as u8]),
                }
            }
            at += run;
        }
        out
    }

    /// A CTB file of `layers` images of `width × height` pixels on a `bed` (mm), encrypted with `key`.
    fn ctb(width: usize, height: usize, bed: [f32; 3], layer_height: f32, layers: &[Vec<u8>], key: u32) -> Vec<u8> {
        let mut data = vec![0u8; HEADER];
        let put = |data: &mut Vec<u8>, at: usize, value: u32| data[at..at + 4].copy_from_slice(&value.to_le_bytes());
        put(&mut data, 0, MAGIC);
        put(&mut data, 4, 3);
        for (k, v) in bed.iter().enumerate() {
            put(&mut data, 8 + 4 * k, v.to_bits());
        }
        put(&mut data, 32, layer_height.to_bits());
        put(&mut data, 52, width as u32);
        put(&mut data, 56, height as u32);
        put(&mut data, 64, HEADER as u32);
        put(&mut data, 68, layers.len() as u32);
        put(&mut data, 100, key);
        let table = data.len();
        data.resize(table + LAYER * layers.len(), 0);
        for (i, pixels) in layers.iter().enumerate() {
            let mut image = encode(pixels);
            unstream(&mut image, key, i as u32); // the stream is its own inverse
            let entry = table + LAYER * i;
            put(&mut data, entry, ((i + 1) as f32 * layer_height).to_bits());
            let offset = data.len() as u32;
            put(&mut data, entry + 12, offset);
            put(&mut data, entry + 16, image.len() as u32);
            data.extend(image);
        }
        data
    }

    #[test]
    fn runs_decode_short_and_long_runs() {
        let mut pixels = vec![0u8; 70_000];
        pixels[5] = 255;
        pixels[100..300].fill(255);
        pixels[400..70_000].fill(127);
        let image = encode(&pixels);
        let mut seen = Vec::new();
        assert_eq!(runs(&image, |start, length, grey| seen.push((start, length, grey))), Some(70_000));
        assert_eq!(seen, vec![(5, 1, 255), (100, 200, 255), (400, 69_600, 127)]);
        assert_eq!(runs(&[0x85, 0xff], |_, _, _| {}), None, "a broken run length");
    }

    #[test]
    fn a_stack_of_squares_becomes_a_closed_box_on_the_plate() {
        // 0.5 mm pixels, 0.1 mm layers: a 4 × 2 mm square (8 × 4 pixels) printed 2 mm high, in the bed's corner
        let (width, height) = (40, 30);
        let mut square = vec![0u8; width * height];
        for row in 2..6 {
            square[row * width + 3..row * width + 11].fill(255);
        }
        let layers = vec![square; 20];
        let data = ctb(width, height, [20.0, 15.0, 100.0], 0.1, &layers, 0x1234_5678);
        let scene = load_bytes(&data, Path::new("box.ctb")).unwrap();
        assert_eq!(scene.plates, vec![Plate { size: [20.0, 15.0, 100.0], position: [0.0; 3] }]);
        let mesh = &scene.objects[0].mesh;
        let (lo, hi) = mesh.bounds().unwrap();
        // x: pixels 3..11 -> -10 + 1.5 .. -10 + 5.5; y: rows 2..6 from the top -> 7.5 - 3 .. 7.5 - 1; z: 0 .. 2
        let near = |a: f32, b: f32| (a - b).abs() < 0.3;
        assert!(near(lo[0], -8.5) && near(hi[0], -4.5), "x {lo:?} {hi:?}");
        assert!(near(lo[1], 4.5) && near(hi[1], 6.5), "y {lo:?} {hi:?}");
        assert!(near(lo[2], 0.0) && near(hi[2], 2.0), "z {lo:?} {hi:?}");
        closed_and_outward(mesh);
        assert!(scene.objects[0].info.iter().any(|(k, v)| k == "Layers" && v == "20 × 0.1 mm"));
    }

    /// Every edge is shared by two triangles that run along it in opposite directions, and the triangles face away
    /// from the inside (the enclosed volume is positive).
    fn closed_and_outward(mesh: &Mesh) {
        let mut edges: HashMap<(u32, u32), i32> = HashMap::new();
        for f in &mesh.faces {
            for (a, b) in [(f[0], f[1]), (f[1], f[2]), (f[2], f[0])] {
                *edges.entry((a.min(b), a.max(b))).or_default() += if a < b { 1 } else { -1 };
            }
        }
        assert!(edges.values().all(|n| *n == 0), "the surface is closed and consistently wound");
        let volume: f64 = mesh
            .faces
            .iter()
            .map(|f| {
                let [a, b, c] = f.map(|v| glam::DVec3::from_array(mesh.vertices[v as usize].map(f64::from)));
                a.dot(b.cross(c)) / 6.0
            })
            .sum();
        assert!(volume > 0.0, "the triangles face outwards");
    }

    #[test]
    fn a_ball_is_closed_and_faces_outwards() {
        let n = 24;
        let slabs: Vec<Vec<u8>> = (0..n)
            .map(|k| {
                (0..n * n)
                    .map(|c| {
                        let (i, j) = (c % n, c / n);
                        let d =
                            ((i as f64 - 11.5).powi(2) + (j as f64 - 11.5).powi(2) + (k as f64 - 11.5).powi(2)).sqrt();
                        ((8.0 - d) * 64.0 + 128.0).clamp(0.0, 255.0) as u8
                    })
                    .collect()
            })
            .collect();
        let mesh = surface_nets(&slabs, [n, n, n], [0.0; 3], [1.0; 3]);
        assert!(mesh.faces.len() > 500);
        closed_and_outward(&mesh);
    }

    /// Encrypt `bytes` (whole blocks) with the encrypted format's AES-256-CBC key and IV.
    fn aes_encrypt(bytes: &mut [u8]) {
        use aes::cipher::BlockEncrypt;
        let cipher = aes::Aes256::new(&KEY.into());
        let mut previous = IV;
        for block in bytes.chunks_exact_mut(16) {
            block.iter_mut().zip(previous).for_each(|(b, p)| *b ^= p);
            cipher.encrypt_block(aes::Block::from_mut_slice(block));
            previous = (*block).try_into().unwrap();
        }
    }

    #[test]
    fn the_encrypted_format_decrypts() {
        // the same box as above, in the encrypted format: settings, layer pointers, then each layer's header and
        // image, the first 16 bytes of every image encrypted with AES as well
        let (width, height, count) = (40usize, 30usize, 20usize);
        let mut square = vec![0u8; width * height];
        for row in 2..6 {
            square[row * width + 3..row * width + 11].fill(255);
        }
        let mut settings = vec![0u8; SETTINGS];
        let mut put = |at: usize, value: u32| settings[at..at + 4].copy_from_slice(&value.to_le_bytes());
        let pointers = ENCRYPTED_HEADER + SETTINGS;
        put(8, pointers as u32);
        for (at, value) in [(12, 20.0f32), (16, 15.0), (20, 100.0), (36, 0.1)] {
            put(at, value.to_bits());
        }
        put(56, width as u32);
        put(60, height as u32);
        put(64, count as u32);
        put(128, 0xBEEF);
        aes_encrypt(&mut settings);
        let mut data = vec![0u8; ENCRYPTED_HEADER];
        for (at, value) in [(0, MAGIC_ENCRYPTED), (4, SETTINGS as u32), (8, ENCRYPTED_HEADER as u32), (16, 5)] {
            data[at..at + 4].copy_from_slice(&value.to_le_bytes());
        }
        data.extend(settings);
        data.resize(pointers + POINTER * count, 0);
        for i in 0..count {
            let mut image = encode(&square);
            unstream(&mut image, 0xBEEF, i as u32);
            assert!(image.len() >= 16);
            aes_encrypt(&mut image[..16]);
            let start = data.len() as u32;
            data[pointers + POINTER * i..pointers + POINTER * i + 4].copy_from_slice(&start.to_le_bytes());
            let mut header = vec![0u8; LAYER_HEADER];
            for (at, value) in [(0, LAYER_HEADER as u32), (24, image.len() as u32), (32, 0), (36, 16)] {
                header[at..at + 4].copy_from_slice(&value.to_le_bytes());
            }
            data.extend(header);
            data.extend(image);
        }
        let scene = load_bytes(&data, Path::new("box.ctb")).unwrap();
        let (lo, hi) = scene.objects[0].mesh.bounds().unwrap();
        let near = |a: f32, b: f32| (a - b).abs() < 0.3;
        assert!(near(lo[0], -8.5) && near(hi[0], -4.5) && near(lo[2], 0.0) && near(hi[2], 2.0), "{lo:?} {hi:?}");
        assert!(scene.objects[0].info.iter().any(|(k, v)| k == "Version" && v == "5 (encrypted)"));
    }

    #[test]
    fn other_files_are_refused() {
        assert!(load_bytes(b"not a print file at all", Path::new("t.ctb")).is_err());
        // an encrypted file whose settings are missing
        let mut encrypted = vec![0u8; 200];
        encrypted[..4].copy_from_slice(&MAGIC_ENCRYPTED.to_le_bytes());
        let error = load_bytes(&encrypted, Path::new("t.ctb")).unwrap_err().to_string();
        assert!(error.contains("settings"), "{error}");
    }
}
