//! ZIP catalog reader: a line-by-line port of CPython 3.14's `zipfile`
//! (`_EndRecData`, `_EndRecData64`, `_RealGetContents`, `ZipInfo._decodeExtra`,
//! `_sanitize_filename`), restricted to what the catalog needs.
//!
//! Member names decide the archive fingerprint, so every rule that changes a
//! name is reproduced, with the Windows behaviour of `os.sep`. Where CPython
//! would raise, this returns an error; anything CPython would accept silently
//! but that looks damaged (short reads) is also an error, so the Python reader
//! decides those archives.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use super::{format_err, ArchiveError, ArchiveMember};

const SIZE_END_CENT_DIR: u64 = 22;
const SIZE_END_CENT_DIR64_LOCATOR: u64 = 20;
const SIZE_END_CENT_DIR64: u64 = 56;
const SIZE_CENTRAL_DIR: usize = 46;
const ZIP_MAX_COMMENT: u64 = (1 << 16) - 1;
const MAX_EXTRACT_VERSION: u8 = 63;
const MASK_UTF_FILENAME: u16 = 1 << 11;

const END_ARCHIVE: &[u8; 4] = b"PK\x05\x06";
const END_ARCHIVE64_LOCATOR: &[u8; 4] = b"PK\x06\x07";
const END_ARCHIVE64: &[u8; 4] = b"PK\x06\x06";
const CENTRAL_DIR: &[u8; 4] = b"PK\x01\x02";

fn u16le(b: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([b[at], b[at + 1]])
}
fn u32le(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(b[at..at + 4].try_into().unwrap())
}
fn u64le(b: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(b[at..at + 8].try_into().unwrap())
}

/// The fields of the end-of-central-directory record the catalog needs.
struct EndRec {
    size_cd: u64,
    offset_cd: u64,
    /// File offset of the (ZIP64) end record; `_ECD_LOCATION`.
    location: u64,
}

fn read_exact_at(f: &mut File, pos: u64, len: usize) -> Result<Vec<u8>, ArchiveError> {
    f.seek(SeekFrom::Start(pos))?;
    let mut buf = vec![0u8; len];
    let mut got = 0;
    while got < len {
        let n = f.read(&mut buf[got..])?;
        if n == 0 {
            break;
        }
        got += n;
    }
    buf.truncate(got);
    Ok(buf)
}

/// `_EndRecData`: locate the end record (no comment first, then search the last 64 KiB).
fn end_rec(f: &mut File) -> Result<EndRec, ArchiveError> {
    let filesize = f.seek(SeekFrom::End(0))?;
    if filesize < SIZE_END_CENT_DIR {
        return Err(format_err("File is not a zip file"));
    }
    let data = read_exact_at(f, filesize - SIZE_END_CENT_DIR, SIZE_END_CENT_DIR as usize)?;
    if data.len() == SIZE_END_CENT_DIR as usize && &data[0..4] == END_ARCHIVE && data[20..22] == [0, 0] {
        let rec = parse_end_rec(&data, filesize - SIZE_END_CENT_DIR);
        return end_rec64(f, filesize - SIZE_END_CENT_DIR, rec);
    }
    let max_comment_start = filesize.saturating_sub(ZIP_MAX_COMMENT + SIZE_END_CENT_DIR);
    let data = read_exact_at(f, max_comment_start, (ZIP_MAX_COMMENT + SIZE_END_CENT_DIR) as usize)?;
    let start = data
        .windows(4)
        .rposition(|w| w == END_ARCHIVE)
        .ok_or_else(|| format_err("File is not a zip file"))?;
    let rec = data.get(start..start + SIZE_END_CENT_DIR as usize)
        .ok_or_else(|| format_err("File is not a zip file"))?;
    let rec = parse_end_rec(rec, max_comment_start + start as u64);
    end_rec64(f, max_comment_start + start as u64, rec)
}

fn parse_end_rec(d: &[u8], location: u64) -> EndRec {
    EndRec {
        size_cd: u32le(d, 12) as u64,
        offset_cd: u32le(d, 16) as u64,
        location,
    }
}

/// `_EndRecData64`: apply the ZIP64 end record when a locator precedes the end record.
fn end_rec64(f: &mut File, offset: u64, rec: EndRec) -> Result<EndRec, ArchiveError> {
    let Some(mut offset) = offset.checked_sub(SIZE_END_CENT_DIR64_LOCATOR) else {
        return Ok(rec);
    };
    let data = read_exact_at(f, offset, SIZE_END_CENT_DIR64_LOCATOR as usize)?;
    if data.len() != SIZE_END_CENT_DIR64_LOCATOR as usize {
        return Err(format_err("Unknown I/O error"));
    }
    if &data[0..4] != END_ARCHIVE64_LOCATOR {
        return Ok(rec);
    }
    let diskno = u32le(&data, 4);
    let reloff = u64le(&data, 8);
    let disks = u32le(&data, 16);
    if diskno != 0 || disks > 1 {
        return Err(ArchiveError::Unsupported("multi-disk zip".into()));
    }
    offset = offset
        .checked_sub(SIZE_END_CENT_DIR64)
        .ok_or_else(|| format_err("Corrupt zip64 end of central directory locator"))?;
    if reloff > offset {
        return Err(format_err("Corrupt zip64 end of central directory locator"));
    }
    let mut extrasz = offset - reloff;
    let mut data = read_exact_at(f, reloff, SIZE_END_CENT_DIR64 as usize)?;
    if data.len() != SIZE_END_CENT_DIR64 as usize {
        return Err(format_err("Unknown I/O error"));
    }
    if &data[0..4] != END_ARCHIVE64 && reloff != offset {
        extrasz = 0;
        data = read_exact_at(f, offset, SIZE_END_CENT_DIR64 as usize)?;
        if data.len() != SIZE_END_CENT_DIR64 as usize {
            return Err(format_err("Unknown I/O error"));
        }
    }
    if &data[0..4] != END_ARCHIVE64 {
        return Err(format_err("Zip64 end of central directory record not found"));
    }
    let sz = u64le(&data, 4);
    let dirsize = u64le(&data, 40);
    let diroffset = u64le(&data, 48);
    if diroffset.checked_add(dirsize) != Some(reloff)
        || sz.checked_add(12) != Some(SIZE_END_CENT_DIR64 + extrasz)
    {
        return Err(format_err("Corrupt zip64 end of central directory record"));
    }
    Ok(EndRec { size_cd: dirsize, offset_cd: diroffset, location: offset - extrasz })
}

/// `bytes.decode('cp437')`.
fn decode_cp437(b: &[u8]) -> String {
    b.iter()
        .map(|&c| if c < 0x80 { c as char } else { CP437_HIGH[(c - 0x80) as usize] })
        .collect()
}

/// `_sanitize_filename` with Windows `os.sep`: cut at the first NUL, `\` → `/`.
fn sanitize(name: String) -> String {
    let name = match name.find('\0') {
        Some(i) => name[..i].to_string(),
        None => name,
    };
    if cfg!(windows) && name.contains('\\') { name.replace('\\', "/") } else { name }
}

/// A central-directory entry with what extraction needs.
struct Entry {
    member: ArchiveMember,
    raw_name: Vec<u8>,
    flags: u16,
    method: u16,
    compress_size: u64,
    /// Absolute offset of the local header (`header_offset + concat`).
    header_offset: u64,
    /// `_end_offset`: where the next entry (or the central directory) starts.
    end_offset: u64,
}

pub(super) fn list_members(path: &Path) -> Result<Vec<ArchiveMember>, ArchiveError> {
    let mut f = File::open(path)?;
    Ok(entries(&mut f)?.into_iter().map(|e| e.member).collect())
}

/// Decompress the named members: `ZipFile.read(name)` semantics for each.
///
/// `NameToInfo` keeps the last entry of a duplicated name, and so does this.
/// A member that `zipfile` would fail to read (header name mismatch,
/// overlapped entries, bad CRC) is left out, as `read_batch` does. Encrypted
/// members and methods other than stored/deflate make the whole call fail,
/// so the Python reader handles that archive.
pub(super) fn read_members(
    path: &Path,
    wanted: &[String],
) -> Result<Vec<(String, Vec<u8>)>, ArchiveError> {
    let mut f = File::open(path)?;
    let entries = entries(&mut f)?;
    let mut by_name = std::collections::HashMap::new();
    for e in &entries {
        by_name.insert(e.member.name.as_str(), e);
    }
    let selected: Vec<(&String, &Entry)> =
        wanted.iter().filter_map(|n| by_name.get(n.as_str()).map(|e| (n, *e))).collect();
    for (_, e) in &selected {
        // Encrypted (bit 0), compressed patched data (5), strong encryption (6).
        if e.flags & 0b110_0001 != 0 {
            return Err(ArchiveError::Unsupported("encrypted or patched zip member".into()));
        }
        if e.method != 0 && e.method != 8 {
            return Err(ArchiveError::Unsupported(format!("zip compression method {}", e.method)));
        }
    }
    let mut out = Vec::new();
    for (name, e) in selected {
        if let Some(data) = read_entry(&mut f, e)?
            && !data.is_empty()
        {
            out.push((name.clone(), data));
        }
    }
    Ok(out)
}

/// One member's bytes, or `None` where `ZipFile.open`/`read` would raise.
fn read_entry(f: &mut File, e: &Entry) -> Result<Option<Vec<u8>>, ArchiveError> {
    use std::io::Read as _;
    let hdr = read_exact_at(f, e.header_offset, 30)?;
    if hdr.len() != 30 || &hdr[0..4] != b"PK\x03\x04" {
        return Ok(None); // "Truncated file header" / "Bad magic number for file header"
    }
    let name_len = u16le(&hdr, 26) as u64;
    let extra_len = u16le(&hdr, 28) as u64;
    let local_name = read_exact_at(f, e.header_offset + 30, name_len as usize)?;
    if local_name != e.raw_name || (u16le(&hdr, 6) ^ e.flags) & MASK_UTF_FILENAME != 0 {
        // zipfile compares the *decoded* names; leave that judgement to it.
        return Err(ArchiveError::Unsupported("local header name differs".into()));
    }
    let data_start = e.header_offset + 30 + name_len + extra_len;
    if data_start + e.compress_size > e.end_offset && e.end_offset != e.header_offset {
        return Ok(None); // "Overlapped entries" (zip bomb guard; equal offsets only warn)
    }
    let raw = read_exact_at(f, data_start, e.compress_size as usize)?;
    if raw.len() as u64 != e.compress_size {
        return Ok(None);
    }
    let data = if e.method == 0 {
        raw
    } else {
        let mut out = Vec::with_capacity(e.member.size.unwrap_or(0).min(1 << 26) as usize);
        if flate2::read::DeflateDecoder::new(&raw[..]).read_to_end(&mut out).is_err() {
            return Ok(None);
        }
        out
    };
    if Some(data.len() as u64) != e.member.size {
        // ZipExtFile truncates to file_size; such members are left to it.
        return Err(ArchiveError::Unsupported("zip member size mismatch".into()));
    }
    if crc32fast::hash(&data) != e.member.crc {
        return Ok(None); // "Bad CRC-32"
    }
    Ok(Some(data))
}

fn entries(f: &mut File) -> Result<Vec<Entry>, ArchiveError> {
    let rec = end_rec(f)?;

    // `_handle_prepended_data`: concat is non-zero when data precedes the zip.
    let concat = rec.location as i128 - rec.size_cd as i128 - rec.offset_cd as i128;
    let start_dir = rec.offset_cd as i128 + concat;
    if start_dir < 0 {
        return Err(format_err("Bad offset for central directory"));
    }
    let size_cd = usize::try_from(rec.size_cd).map_err(|_| format_err("central directory too large"))?;
    let cd = read_exact_at(f, start_dir as u64, size_cd)?;
    if cd.len() != size_cd {
        return Err(format_err("short central directory read")); // CPython reads short silently
    }

    let mut members: Vec<Entry> = Vec::new();
    let mut pos = 0usize;
    while pos < size_cd {
        let hdr = cd.get(pos..pos + SIZE_CENTRAL_DIR).ok_or_else(|| format_err("Truncated central directory"))?;
        if &hdr[0..4] != CENTRAL_DIR {
            return Err(format_err("Bad magic number for central directory"));
        }
        let extract_version = hdr[6];
        let flags = u16le(hdr, 8);
        let crc = u32le(hdr, 16);
        let method = u16le(hdr, 10);
        let mut file_size = u32le(hdr, 24) as u64;
        let mut compress_size = u32le(hdr, 20) as u64;
        let name_len = u16le(hdr, 28) as usize;
        let extra_len = u16le(hdr, 30) as usize;
        let comment_len = u16le(hdr, 32) as usize;
        let mut header_offset = u32le(hdr, 42) as u64;

        let p = pos + SIZE_CENTRAL_DIR;
        let raw_name = cd.get(p..p + name_len).ok_or_else(|| format_err("short name read"))?;
        let extra = cd.get(p + name_len..p + name_len + extra_len).ok_or_else(|| format_err("short extra read"))?;
        if cd.len() < p + name_len + extra_len + comment_len {
            return Err(format_err("short comment read"));
        }

        let name = if flags & MASK_UTF_FILENAME != 0 {
            std::str::from_utf8(raw_name).map_err(|_| format_err("invalid UTF-8 file name"))?.to_string()
        } else {
            decode_cp437(raw_name)
        };
        let mut name = sanitize(name);
        if extract_version > MAX_EXTRACT_VERSION {
            return Err(ArchiveError::Unsupported(format!("zip file version {}", extract_version as f32 / 10.0)));
        }

        // `_decodeExtra`
        let mut e = extra;
        while e.len() >= 4 {
            let tp = u16le(e, 0);
            let ln = u16le(e, 2) as usize;
            if ln + 4 > e.len() {
                return Err(format_err(format!("Corrupt extra field {tp:04x}")));
            }
            let data = &e[4..ln + 4];
            if tp == 0x0001 {
                let mut d = data;
                let mut take = |what: &str| -> Result<u64, ArchiveError> {
                    if d.len() < 8 {
                        return Err(format_err(format!("Corrupt zip64 extra field. {what} not found.")));
                    }
                    let v = u64le(d, 0);
                    d = &d[8..];
                    Ok(v)
                };
                if file_size == 0xFFFF_FFFF {
                    file_size = take("File size")?;
                }
                if compress_size == 0xFFFF_FFFF {
                    compress_size = take("Compress size")?;
                }
                if header_offset == 0xFFFF_FFFF {
                    header_offset = take("Header offset")?;
                }
            } else if tp == 0x7075 {
                if data.len() < 5 {
                    return Err(format_err("Corrupt unicode path extra field (0x7075)"));
                }
                let up_version = data[0];
                let up_name_crc = u32le(data, 1);
                if up_version == 1 && up_name_crc == crc32fast::hash(raw_name) {
                    let up = std::str::from_utf8(&data[5..])
                        .map_err(|_| format_err("Corrupt unicode path extra field (0x7075): invalid utf-8 bytes"))?;
                    if !up.is_empty() {
                        name = sanitize(up.to_string());
                    }
                }
            }
            e = &e[ln + 4..];
        }

        members.push(Entry {
            member: ArchiveMember { name, crc, size: Some(file_size) },
            raw_name: raw_name.to_vec(),
            flags,
            method,
            compress_size,
            header_offset: (header_offset as i128 + concat).max(0) as u64,
            end_offset: 0,
        });
        pos = p + name_len + extra_len + comment_len;
    }

    // `_end_offset`: each entry ends where the next local header (by offset)
    // or the central directory starts.
    let mut order: Vec<usize> = (0..members.len()).collect();
    order.sort_by_key(|&k| members[k].header_offset); // stable, like Python's sorted
    let mut end_offset = start_dir as u64;
    for &k in order.iter().rev() {
        members[k].end_offset = end_offset;
        end_offset = members[k].header_offset;
    }
    Ok(members)
}

/// CP437 code points for bytes 0x80..=0xFF, generated from Python's `cp437` codec.
const CP437_HIGH: [char; 128] = [
    '\u{00c7}', '\u{00fc}', '\u{00e9}', '\u{00e2}', '\u{00e4}', '\u{00e0}', '\u{00e5}', '\u{00e7}',
    '\u{00ea}', '\u{00eb}', '\u{00e8}', '\u{00ef}', '\u{00ee}', '\u{00ec}', '\u{00c4}', '\u{00c5}',
    '\u{00c9}', '\u{00e6}', '\u{00c6}', '\u{00f4}', '\u{00f6}', '\u{00f2}', '\u{00fb}', '\u{00f9}',
    '\u{00ff}', '\u{00d6}', '\u{00dc}', '\u{00a2}', '\u{00a3}', '\u{00a5}', '\u{20a7}', '\u{0192}',
    '\u{00e1}', '\u{00ed}', '\u{00f3}', '\u{00fa}', '\u{00f1}', '\u{00d1}', '\u{00aa}', '\u{00ba}',
    '\u{00bf}', '\u{2310}', '\u{00ac}', '\u{00bd}', '\u{00bc}', '\u{00a1}', '\u{00ab}', '\u{00bb}',
    '\u{2591}', '\u{2592}', '\u{2593}', '\u{2502}', '\u{2524}', '\u{2561}', '\u{2562}', '\u{2556}',
    '\u{2555}', '\u{2563}', '\u{2551}', '\u{2557}', '\u{255d}', '\u{255c}', '\u{255b}', '\u{2510}',
    '\u{2514}', '\u{2534}', '\u{252c}', '\u{251c}', '\u{2500}', '\u{253c}', '\u{255e}', '\u{255f}',
    '\u{255a}', '\u{2554}', '\u{2569}', '\u{2566}', '\u{2560}', '\u{2550}', '\u{256c}', '\u{2567}',
    '\u{2568}', '\u{2564}', '\u{2565}', '\u{2559}', '\u{2558}', '\u{2552}', '\u{2553}', '\u{256b}',
    '\u{256a}', '\u{2518}', '\u{250c}', '\u{2588}', '\u{2584}', '\u{258c}', '\u{2590}', '\u{2580}',
    '\u{03b1}', '\u{00df}', '\u{0393}', '\u{03c0}', '\u{03a3}', '\u{03c3}', '\u{00b5}', '\u{03c4}',
    '\u{03a6}', '\u{0398}', '\u{03a9}', '\u{03b4}', '\u{221e}', '\u{03c6}', '\u{03b5}', '\u{2229}',
    '\u{2261}', '\u{00b1}', '\u{2265}', '\u{2264}', '\u{2320}', '\u{2321}', '\u{00f7}', '\u{2248}',
    '\u{00b0}', '\u{2219}', '\u{00b7}', '\u{221a}', '\u{207f}', '\u{00b2}', '\u{25a0}', '\u{00a0}',
];
