//! 7z catalog reader on `sevenz-rust2`, matching `py7zr`'s `SevenZipFile.list()`.
//!
//! `py7zr` lists every entry (directories and empty files included), decodes
//! names as strict UTF-16LE and then replaces `\` with `/`
//! (`archiveinfo.py`); entries without a stored CRC report 0.

use std::path::Path;

use sevenz_rust2::{Archive, BlockDecoder, Password};

use super::{ArchiveError, ArchiveMember};

/// Whether `path` is an archive without entries: a valid signature header
/// announcing an empty next header, which is what 7-Zip writes for an archive
/// of nothing (32 bytes). py7zr lists no entries; `sevenz-rust2` fails on it.
fn is_empty_archive(path: &Path) -> std::io::Result<bool> {
    let mut head = [0u8; 32];
    if std::io::Read::read_exact(&mut std::fs::File::open(path)?, &mut head).is_err() {
        return Ok(false);
    }
    let start_crc = u32::from_le_bytes(head[8..12].try_into().expect("4 bytes"));
    Ok(head[..6] == [0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c]
        && crc32fast::hash(&head[12..32]) == start_crc
        && head[20..32].iter().all(|&b| b == 0)) // next header size and CRC
}

pub(super) fn list_members(path: &Path) -> Result<Vec<ArchiveMember>, ArchiveError> {
    if is_empty_archive(path)? {
        return Ok(Vec::new());
    }
    let archive = Archive::open_with_password(path, &Password::empty()).map_err(map_err)?;
    Ok(archive
        .files
        .iter()
        .map(|f| ArchiveMember {
            name: f.name.replace('\\', "/"),
            crc: if f.has_crc { f.crc as u32 } else { 0 },
            size: Some(f.size),
        })
        .collect())
}

/// Decompress the named members, decoding only the blocks that hold them.
///
/// Inside a solid block everything before a wanted file must be decoded too;
/// unwanted entries are drained and decoding of a block stops after its last
/// wanted file. Any decode or CRC error fails the whole call, so the Python
/// reader (which would also fail on it) decides what to do.
pub(super) fn read_members(
    path: &Path,
    wanted: &[String],
) -> Result<Vec<(String, Vec<u8>)>, ArchiveError> {
    use std::collections::{HashMap, HashSet};

    if is_empty_archive(path)? {
        return Ok(Vec::new());
    }
    let mut file = std::fs::File::open(path)?;
    let archive = Archive::read(&mut file, &Password::empty()).map_err(map_err)?;
    let wanted: HashSet<&str> = wanted.iter().map(String::as_str).collect();

    // Wanted files per block; files without a stream are empty and skipped.
    let mut per_block: HashMap<usize, usize> = HashMap::new();
    for (k, f) in archive.files.iter().enumerate() {
        if let Some(b) = archive.stream_map.file_block_index[k]
            && f.size > 0
            && wanted.contains(f.name.replace('\\', "/").as_str())
        {
            *per_block.entry(b).or_default() += 1;
        }
    }
    let mut blocks: Vec<(usize, usize)> = per_block.into_iter().collect();
    blocks.sort_unstable();

    // `BlockDecoder` visits files `first .. first + num_unpack_sub_streams`, so an
    // entry without a stream (directory, empty file) inside a solid block hides
    // the block's last files (py7zr writes such archives). Leave those to Python.
    let mut streams = vec![0usize; archive.blocks.len()];
    for (f, b) in archive.files.iter().zip(&archive.stream_map.file_block_index) {
        if let Some(b) = *b
            && f.has_stream
        {
            streams[b] += 1;
        }
    }
    for &(block, _) in &blocks {
        let first = archive.stream_map.block_first_file_index[block];
        if archive.files[first..first + streams[block]].iter().any(|f| !f.has_stream) {
            return Err(ArchiveError::Unsupported("7z block interleaves empty entries".into()));
        }
    }

    let mut out: HashMap<String, Vec<u8>> = HashMap::new();
    let password = Password::empty();
    for (block, mut remaining) in blocks {
        BlockDecoder::new(1, block, &archive, &password, &mut file)
            .for_each_entries(&mut |entry, reader| {
                let name = entry.name.replace('\\', "/");
                if entry.size > 0 && wanted.contains(name.as_str()) {
                    let mut data = Vec::with_capacity(entry.size.min(1 << 26) as usize);
                    reader.read_to_end(&mut data)?;
                    out.insert(name, data);
                    remaining -= 1;
                    Ok(remaining > 0)
                } else {
                    std::io::copy(reader, &mut std::io::sink())?;
                    Ok(true)
                }
            })
            .map_err(map_err)?;
    }
    let mut out: Vec<(String, Vec<u8>)> = out.into_iter().filter(|(_, d)| !d.is_empty()).collect();
    out.sort_unstable_by(|a, b| a.0.cmp(&b.0));
    Ok(out)
}

fn map_err(e: sevenz_rust2::Error) -> ArchiveError {
    match e {
        sevenz_rust2::Error::Io(io, _) => ArchiveError::Io(io),
        other => ArchiveError::Format(other.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// What 7-Zip writes for an archive of nothing: the signature header alone.
    fn empty_7z() -> Vec<u8> {
        let mut b = vec![0x37, 0x7a, 0xbc, 0xaf, 0x27, 0x1c, 0x00, 0x04, 0x8d, 0x9b, 0xd5, 0x0f];
        b.resize(32, 0);
        b
    }

    #[test]
    fn an_empty_archive_lists_nothing() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("empty.7z");
        std::fs::write(&p, empty_7z()).unwrap();
        assert_eq!(list_members(&p).unwrap(), Vec::<ArchiveMember>::new(), "as py7zr 1.1.4 lists it");
        assert_eq!(read_members(&p, &["x.stl".into()]).unwrap(), Vec::<(String, Vec<u8>)>::new());
        assert_eq!(crate::archive::fingerprint(&p).unwrap(), crate::archive::fingerprint_of(&[]));

        // A wrong start header CRC, or a header that announces more, is still refused.
        let mut bad = empty_7z();
        bad[8] ^= 1;
        std::fs::write(&p, bad).unwrap();
        assert!(list_members(&p).is_err());
        let mut more = empty_7z();
        more[20] = 1;
        let crc = crc32fast::hash(&more[12..32]).to_le_bytes();
        more[8..12].copy_from_slice(&crc);
        std::fs::write(&p, more).unwrap();
        assert!(list_members(&p).is_err(), "a next header that is not there");
    }
}
