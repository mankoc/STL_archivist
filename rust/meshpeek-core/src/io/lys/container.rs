//! Archive layer: V1 files are ZIP, V2+ files are MNGO (asar with `files` renamed to `mangoFiles`).

use std::borrow::Cow;
use std::collections::HashMap;
use std::io::{Cursor, Read};

use crate::error::{LoadError, Result};
use crate::io::Data;

/// The first bytes of a ZIP file (V1 `.lys`).
const ZIP_MAGIC: &[u8] = b"PK\x03\x04";

/// An opened `.lys` container with its member index.
pub enum Archive {
    /// A V1 file.
    Zip(zip::ZipArchive<Cursor<Data>>),
    /// MNGO file contents and its members: name -> (absolute offset, size).
    Mngo(Data, HashMap<String, (usize, usize)>),
}

impl Archive {
    /// Open a file from disk (memory-mapped).
    pub fn open_path(path: &std::path::Path) -> Result<Self> {
        Self::open(Data::Mapped(crate::io::map_file(path)?))
    }

    /// Open file contents, detecting ZIP or MNGO from the first bytes.
    pub fn open(data: Data) -> Result<Self> {
        if data.starts_with(ZIP_MAGIC) {
            let zip = zip::ZipArchive::new(Cursor::new(data))
                .map_err(|e| LoadError::format(format!("bad ZIP archive: {e}")))?;
            return Ok(Self::Zip(zip));
        }
        let members = mngo_index(&data)?;
        Ok(Self::Mngo(data, members))
    }

    /// V1 (ZIP) file: its `scene.bin` is not ciphered.
    pub fn is_zip(&self) -> bool {
        matches!(self, Self::Zip(_))
    }

    /// Member names, sorted (directories joined with `/`).
    pub fn names(&self) -> Vec<String> {
        let mut names: Vec<String> = match self {
            Self::Zip(zip) => zip.file_names().flatten().map(|n| n.into_owned()).collect(), // zip 9: Result names
            Self::Mngo(_, members) => members.keys().cloned().collect(),
        };
        names.sort();
        names
    }

    /// The archive has a member called `name`.
    pub fn contains(&self, name: &str) -> bool {
        match self {
            Self::Zip(zip) => zip.index_for_name(name).is_some(),
            Self::Mngo(_, members) => members.contains_key(name),
        }
    }

    /// A member's bytes; MNGO members are borrowed straight from the memory map.
    pub fn read(&mut self, name: &str) -> Result<Cow<'_, [u8]>> {
        match self {
            Self::Zip(zip) => {
                let mut file = zip.by_name(name).map_err(|e| LoadError::format(format!("{name}: {e}")))?;
                let mut bytes = Vec::with_capacity(file.size() as usize);
                file.read_to_end(&mut bytes).map_err(|e| LoadError::io(name, e))?;
                Ok(Cow::Owned(bytes))
            }
            Self::Mngo(map, members) => {
                let &(start, size) = members.get(name).ok_or_else(|| LoadError::format(format!("{name} missing")))?;
                Ok(Cow::Borrowed(&map[start..start + size]))
            }
        }
    }
}

/// Parse the MNGO header: `[4][headerSize][headerPayloadSize][jsonLen][json...]`, data after `8 + headerSize`.
fn mngo_index(map: &[u8]) -> Result<HashMap<String, (usize, usize)>> {
    let u32_at = |at: usize| -> Result<usize> {
        map.get(at..at + 4)
            .map(|b| u32::from_le_bytes(b.try_into().unwrap()) as usize)
            .ok_or_else(|| LoadError::format("file too short for an MNGO header"))
    };
    let (header_size, json_len) = (u32_at(4)?, u32_at(12)?);
    let json = map.get(16..16 + json_len).ok_or_else(|| LoadError::format("MNGO header runs past end of file"))?;
    let index: serde_json::Value =
        serde_json::from_slice(json).map_err(|e| LoadError::format(format!("unreadable MNGO header: {e}")))?;
    let data_start = 8 + header_size;
    let mut members = HashMap::new();
    walk(&index, "", data_start, &mut members)?;
    for (name, &(start, size)) in &members {
        if start + size > map.len() {
            return Err(LoadError::format(format!("{name} runs past end of file")));
        }
    }
    Ok(members)
}

/// Add the members of one MNGO directory node (recursing into subdirectories) to `out`.
fn walk(
    node: &serde_json::Value,
    prefix: &str,
    data_start: usize,
    out: &mut HashMap<String, (usize, usize)>,
) -> Result<()> {
    let Some(files) = node.get("mangoFiles").and_then(|f| f.as_object()) else { return Ok(()) };
    for (name, child) in files {
        let full = format!("{prefix}{name}");
        if child.get("mangoFiles").is_some() {
            walk(child, &format!("{full}/"), data_start, out)?;
        } else if let (Some(offset), Some(size)) = (child.get("offset"), child.get("size").and_then(|s| s.as_u64())) {
            // Offsets are strings in the index ("offset": "48213"); symlinks/unpacked files are skipped.
            let offset = offset.as_str().and_then(|s| s.parse::<usize>().ok()).or(offset.as_u64().map(|o| o as usize));
            let offset = offset.ok_or_else(|| LoadError::format(format!("{full}: bad offset")))?;
            out.insert(full, (data_start + offset, size as usize));
        }
    }
    Ok(())
}
