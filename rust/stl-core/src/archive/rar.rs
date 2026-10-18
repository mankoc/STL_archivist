//! RAR catalog and member reads: a port of what `rarfile` (4.2) returns for
//! `RarFile(path).infolist()` and `RarFile.read(name)` in `app/crawler/archive.py`.
//!
//! The catalog parses RAR 2.9 / 3 / 4 and RAR5 headers as `rarfile` does:
//! names decoded the same way (RAR3's compressed-Unicode names, then UTF-8,
//! UTF-16LE, cp1252), directories and old file versions dropped, and parsing
//! stopped where `rarfile` stops (a header CRC error, EOF, an unknown RAR5 block,
//! encrypted headers). Anything `rarfile` would treat differently or that this
//! port does not cover exactly — SFX archives, volumes, comments, malformed
//! headers — returns [`ArchiveError::Unsupported`], and the Python reader runs.
//!
//! Member data comes from the UnRAR tool (`unrar p`), as `rarfile` gets it;
//! stored members are read directly. Data whose CRC does not match is left
//! out, as `rarfile`'s read raises for it.

use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use super::{ArchiveError, ArchiveMember};

const RAR3_ID: &[u8] = b"Rar!\x1a\x07\x00";
const RAR5_ID: &[u8] = b"Rar!\x1a\x07\x01\x00";

fn unsupported<T>(m: impl Into<String>) -> Result<T, ArchiveError> {
    Err(ArchiveError::Unsupported(m.into()))
}

/// One catalog entry with what a read needs.
#[derive(Debug, Clone)]
struct Entry {
    name: String,
    crc: u32,
    size: u64,
    data_offset: u64,
    compress_size: u64,
    stored: bool,
    password: bool,
    has_crc: bool,
}

struct Catalog {
    entries: Vec<Entry>,
}

// ── decoding ─────────────────────────────────────────────────────────────────

/// `bytes.decode("utf-16le", "replace")`.
fn utf16le_replace(b: &[u8]) -> String {
    let units: Vec<u16> = b.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes(*c)).collect();
    let mut s: String = char::decode_utf16(units).map(|r| r.unwrap_or('\u{FFFD}')).collect();
    if b.len() % 2 == 1 {
        s.push('\u{FFFD}');
    }
    s
}

/// Strict UTF-16LE (`"utf-16le"` without errors), or `None`.
fn utf16le_strict(b: &[u8]) -> Option<String> {
    if b.len() % 2 == 1 {
        return None;
    }
    let units: Vec<u16> = b.as_chunks::<2>().0.iter().map(|c| u16::from_le_bytes(*c)).collect();
    char::decode_utf16(units).collect::<Result<String, _>>().ok()
}

/// `bytes.decode("windows-1252", "replace")`.
fn cp1252_replace(b: &[u8]) -> String {
    const HIGH: [u32; 32] = [
        0x20AC, 0xFFFD, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152,
        0xFFFD, 0x017D, 0xFFFD, 0xFFFD, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122,
        0x0161, 0x203A, 0x0153, 0xFFFD, 0x017E, 0x0178,
    ];
    b.iter()
        .map(|&c| match c {
            0x80..=0x9F => char::from_u32(HIGH[(c - 0x80) as usize]).unwrap_or('\u{FFFD}'),
            c => c as char,
        })
        .collect()
}

/// `RAR3Parser._decode`: UTF-8, then UTF-16LE, then cp1252 with replacement.
fn decode_name(b: &[u8]) -> String {
    if let Ok(s) = std::str::from_utf8(b) {
        return s.to_string();
    }
    if let Some(s) = utf16le_strict(b) {
        return s;
    }
    cp1252_replace(b)
}

/// `UnicodeFilename(std_name, encdata).decode()` and its `failed` flag.
fn unicode_filename(std_name: &[u8], enc: &[u8]) -> (String, bool) {
    let mut failed = false;
    let mut encpos = 0usize;
    let mut pos = 0usize;
    let mut buf: Vec<u8> = Vec::new();
    let enc_byte = |encpos: &mut usize, failed: &mut bool| -> u8 {
        match enc.get(*encpos) {
            Some(&c) => {
                *encpos += 1;
                c
            }
            None => {
                *failed = true;
                0
            }
        }
    };
    let std_byte = |pos: usize, failed: &mut bool| -> u8 {
        match std_name.get(pos) {
            Some(&c) => c,
            None => {
                *failed = true;
                b'?'
            }
        }
    };
    let hi = enc_byte(&mut encpos, &mut failed);
    let mut flagbits = 0u32;
    let mut flags = 0u8;
    while encpos < enc.len() {
        if flagbits == 0 {
            flags = enc_byte(&mut encpos, &mut failed);
            flagbits = 8;
        }
        flagbits -= 2;
        let t = (flags >> flagbits) & 3;
        match t {
            0 => {
                let lo = enc_byte(&mut encpos, &mut failed);
                buf.extend([lo, 0]);
                pos += 1;
            }
            1 => {
                let lo = enc_byte(&mut encpos, &mut failed);
                buf.extend([lo, hi]);
                pos += 1;
            }
            2 => {
                let lo = enc_byte(&mut encpos, &mut failed);
                let h = enc_byte(&mut encpos, &mut failed);
                buf.extend([lo, h]);
                pos += 1;
            }
            _ => {
                let n = enc_byte(&mut encpos, &mut failed);
                if n & 0x80 != 0 {
                    let c = enc_byte(&mut encpos, &mut failed);
                    for _ in 0..((n & 0x7f) as usize + 2) {
                        let lo = std_byte(pos, &mut failed).wrapping_add(c);
                        buf.extend([lo, hi]);
                        pos += 1;
                    }
                } else {
                    for _ in 0..(n as usize + 2) {
                        let lo = std_byte(pos, &mut failed);
                        buf.extend([lo, 0]);
                        pos += 1;
                    }
                }
            }
        }
    }
    (utf16le_replace(&buf), failed)
}

// ── RAR3 ─────────────────────────────────────────────────────────────────────

fn le16(b: &[u8], p: usize) -> Option<u16> {
    b.get(p..p + 2).map(|s| u16::from_le_bytes([s[0], s[1]]))
}

fn le32(b: &[u8], p: usize) -> Option<u32> {
    b.get(p..p + 4).map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
}

/// `_parse_ext_time`'s end position, or `None` where `rarfile` would raise.
fn ext_time_end(data: &[u8], mut pos: usize, has_mtime: bool) -> Option<usize> {
    let mut flags = 0u16;
    if pos + 2 <= data.len() {
        flags = le16(data, pos)?;
        pos += 2;
    }
    for (k, shift) in [12u16, 8, 4, 0].into_iter().enumerate() {
        let flag = (flags >> shift) & 0xF;
        if flag & 8 != 0 {
            // mtime has a base time (the DOS time); the others load one.
            if !(k == 0 && has_mtime) {
                le32(data, pos)?;
                pos += 4;
            }
            let mut rem: u32 = 0;
            for _ in 0..(flag & 3) {
                rem = ((*data.get(pos)? as u32) << 16) | (rem >> 8);
                pos += 1;
            }
            if rem as u64 * 100 > 999_999_999 {
                return None; // nsdatetime rejects it: rarfile raises
            }
        }
    }
    Some(pos)
}

fn parse_rar3<R: Read + Seek>(f: &mut R) -> Result<Catalog, ArchiveError> {
    let mut entries = Vec::new();
    f.seek(SeekFrom::Start(RAR3_ID.len() as u64))?;
    loop {
        let offset = f.stream_position()?;
        let mut base = [0u8; 7];
        let n = read_up_to(f, &mut base)?;
        if n == 0 {
            break; // clean EOF
        }
        if n < 7 {
            break; // "Unexpected EOF when reading header": rarfile stops
        }
        let header_crc = u16::from_le_bytes([base[0], base[1]]);
        let typ = base[2];
        let flags = u16::from_le_bytes([base[3], base[4]]);
        let size = u16::from_le_bytes([base[5], base[6]]) as usize;
        let mut hdata = base.to_vec();
        if size > 7 {
            let mut rest = vec![0u8; size - 7];
            let got = read_up_to(f, &mut rest)?;
            hdata.extend_from_slice(&rest[..got]);
        }
        let data_offset = offset + hdata.len() as u64;
        if hdata.len() != size {
            break; // unexpected EOF (or a size below the base header): rarfile stops
        }
        let mut pos = 7;
        let mut add_size = 0u64;
        if flags & 0x8000 != 0 {
            add_size = le32(&hdata, pos).ok_or_else(|| ArchiveError::Unsupported("short header".into()))? as u64;
            pos += 4;
        }
        let crc_pos;
        let mut entry: Option<Entry> = None;
        match typ {
            0x72 => continue, // MARK
            0x73 => {
                // MAIN
                if flags & 0x0002 != 0 {
                    return unsupported("archive comment");
                }
                if flags & 0x0001 != 0 {
                    return unsupported("multi-volume archive");
                }
                pos += 6;
                if flags & 0x0200 != 0 {
                    pos += 1;
                }
                crc_pos = pos;
                if flags & 0x0080 != 0 {
                    // Encrypted headers and no password: rarfile stops here.
                    if crc_ok(&hdata, crc_pos, header_crc) {
                        break;
                    }
                }
            }
            0x74 | 0x7a => {
                if flags & 0x8000 == 0 {
                    return unsupported("file block without a data size");
                }
                let p = pos - 4;
                let fld = hdata.get(p..p + 25).ok_or_else(|| ArchiveError::Unsupported("short file header".into()))?;
                let mut compress = u32::from_le_bytes([fld[0], fld[1], fld[2], fld[3]]) as u64;
                let mut file_size = u32::from_le_bytes([fld[4], fld[5], fld[6], fld[7]]) as u64;
                let host_os = fld[8];
                let crc = u32::from_le_bytes([fld[9], fld[10], fld[11], fld[12]]);
                let method = fld[18];
                let name_size = u16::from_le_bytes([fld[19], fld[20]]) as usize;
                let mode = u32::from_le_bytes([fld[21], fld[22], fld[23], fld[24]]);
                let mut q = p + 25;
                if flags & 0x0100 != 0 {
                    let h1 = le32(&hdata, q).ok_or_else(|| ArchiveError::Unsupported("short header".into()))? as u64;
                    let h2 = le32(&hdata, q + 4).ok_or_else(|| ArchiveError::Unsupported("short header".into()))? as u64;
                    q += 8;
                    compress |= h1 << 32;
                    file_size |= h2 << 32;
                    add_size = compress;
                }
                let raw = hdata.get(q..q + name_size).ok_or_else(|| ArchiveError::Unsupported("short name".into()))?;
                q += name_size;
                let name = if flags & 0x0200 != 0 {
                    match raw.iter().position(|&b| b == 0) {
                        Some(nul) => {
                            let (u, failed) = unicode_filename(&raw[..nul], &raw[nul + 1..]);
                            if failed { decode_name(&raw[..nul]) } else { u }
                        }
                        None => String::from_utf8_lossy(raw).into_owned(),
                    }
                } else {
                    decode_name(raw)
                };
                if flags & 0x0400 != 0 {
                    hdata.get(q..q + 8).ok_or_else(|| ArchiveError::Unsupported("short salt".into()))?;
                    q += 8;
                }
                if flags & 0x1000 != 0 {
                    q = ext_time_end(&hdata, q, true).ok_or_else(|| ArchiveError::Unsupported("short ext time".into()))?;
                }
                if typ == 0x74 {
                    crc_pos = q;
                    if flags & 0x0008 != 0 {
                        return unsupported("file comment");
                    }
                    if flags & 0x0003 != 0 {
                        return unsupported("split file (volumes)");
                    }
                    let symlink = host_os == 3 && mode & 0xF000 == 0xA000;
                    let is_dir = !symlink && flags & 0x00E0 == 0x00E0;
                    let name = name.replace('\\', "/").trim_end_matches('/').to_string();
                    if flags & 0x0800 == 0 && !is_dir {
                        entry = Some(Entry {
                            name,
                            crc,
                            size: file_size,
                            data_offset,
                            compress_size: compress,
                            stored: method == 0x30,
                            password: flags & 0x0004 != 0,
                            has_crc: true,
                        });
                    }
                } else {
                    crc_pos = size;
                    if name == "CMT" {
                        return unsupported("archive comment (sub-block)");
                    }
                }
            }
            0x7b => {
                // ENDARC
                if flags & 0x0001 != 0 {
                    return unsupported("multi-volume archive");
                }
                if !crc_ok(&hdata, size, header_crc) {
                    break;
                }
                break;
            }
            _ => return unsupported(format!("RAR3 block type {typ:#x}")),
        }
        if !crc_ok(&hdata, crc_pos, header_crc) {
            break; // header CRC error: rarfile stops parsing
        }
        if let Some(e) = entry {
            entries.push(e);
        }
        if add_size > 0 {
            f.seek(SeekFrom::Start(data_offset + add_size))?;
        }
    }
    Ok(Catalog { entries })
}

fn crc_ok(hdata: &[u8], crc_pos: usize, expect: u16) -> bool {
    let end = crc_pos.min(hdata.len());
    if end < 2 {
        return false;
    }
    (crc32fast::hash(&hdata[2..end]) & 0xFFFF) as u16 == expect
}

fn read_up_to<R: Read>(f: &mut R, buf: &mut [u8]) -> std::io::Result<usize> {
    let mut got = 0;
    while got < buf.len() {
        let n = f.read(&mut buf[got..])?;
        if n == 0 {
            break;
        }
        got += n;
    }
    Ok(got)
}

// ── RAR5 ─────────────────────────────────────────────────────────────────────

fn vint(b: &[u8], mut pos: usize) -> Option<(u64, usize)> {
    let limit = (pos + 11).min(b.len());
    let mut res: u64 = 0;
    let mut ofs = 0u32;
    while pos < limit {
        let c = b[pos];
        if ofs < 64 {
            res = res.wrapping_add(((c & 0x7F) as u64) << ofs);
        }
        pos += 1;
        ofs += 7;
        if c < 0x80 {
            return Some((res, pos));
        }
    }
    None
}

fn bad<T>() -> Result<T, ArchiveError> {
    unsupported("malformed RAR5 header")
}

fn parse_rar5<R: Read + Seek>(f: &mut R) -> Result<Catalog, ArchiveError> {
    let mut entries = Vec::new();
    f.seek(SeekFrom::Start(RAR5_ID.len() as u64))?;
    loop {
        let mut start = vec![0u8; 5];
        let n = read_up_to(f, &mut start)?;
        if n < 5 {
            break; // EOF (clean or not): rarfile stops
        }
        while start[start.len() - 1] & 0x80 != 0 {
            let mut b = [0u8; 1];
            if f.read(&mut b)? == 0 {
                return Ok(Catalog { entries });
            }
            start.push(b[0]);
        }
        let header_crc = u32::from_le_bytes([start[0], start[1], start[2], start[3]]);
        let Some((hdrlen, p)) = vint(&start, 4) else { return bad() };
        if hdrlen > 2 * 1024 * 1024 {
            break;
        }
        let header_size = p + hdrlen as usize;
        let mut hdata = start.clone();
        if header_size > hdata.len() {
            let mut rest = vec![0u8; header_size - hdata.len()];
            let got = read_up_to(f, &mut rest)?;
            hdata.extend_from_slice(&rest[..got]);
        }
        if hdata.len() != header_size {
            break;
        }
        let data_offset = f.stream_position()?;
        if crc32fast::hash(&hdata[4..]) != header_crc {
            break;
        }
        let Some((block_type, pos)) = vint(&hdata, p) else { return bad() };
        let Some((block_flags, mut pos)) = vint(&hdata, pos) else { return bad() };
        let mut extra_size = 0u64;
        let mut add_size = 0u64;
        if block_flags & 0x01 != 0 {
            let Some((v, q)) = vint(&hdata, pos) else { return bad() };
            extra_size = v;
            pos = q;
        }
        if block_flags & 0x02 != 0 {
            let Some((v, q)) = vint(&hdata, pos) else { return bad() };
            add_size = v;
            pos = q;
        }
        match block_type {
            1 => {
                // MAIN
                let Some((main_flags, q)) = vint(&hdata, pos) else { return bad() };
                if main_flags & 0x02 != 0 {
                    vint(&hdata, q).ok_or_else(|| ArchiveError::Unsupported("malformed".into()))?;
                }
                if main_flags & 0x03 != 0 {
                    return unsupported("multi-volume archive");
                }
            }
            2 | 3 => {
                let Some((file_flags, q)) = vint(&hdata, pos) else { return bad() };
                let Some((file_size, q)) = vint(&hdata, q) else { return bad() };
                let Some((_mode, mut q)) = vint(&hdata, q) else { return bad() };
                if file_flags & 0x02 != 0 {
                    if le32(&hdata, q).is_none() {
                        return bad();
                    }
                    q += 4;
                }
                let mut crc = None;
                if file_flags & 0x04 != 0 {
                    crc = le32(&hdata, q);
                    if crc.is_none() {
                        return bad();
                    }
                    q += 4;
                }
                let Some((compr, q)) = vint(&hdata, q) else { return bad() };
                let Some((_os, q)) = vint(&hdata, q) else { return bad() };
                let Some((nlen, q)) = vint(&hdata, q) else { return bad() };
                let Some(raw) = hdata.get(q..q + nlen as usize) else { return bad() };
                let mut q = q + nlen as usize;
                let name = String::from_utf8_lossy(raw).trim_end_matches('/').to_string();
                let (mut version, mut redir, mut password) = (false, false, false);
                if extra_size > 0 {
                    while q + 1 < hdata.len() {
                        let Some((xsize, r)) = vint(&hdata, q) else { return bad() };
                        let Some(xdata) = hdata.get(r..r + xsize as usize) else { return bad() };
                        q = r + xsize as usize;
                        let Some((xtype, _)) = vint(xdata, 0) else { return bad() };
                        match xtype {
                            1 => password = true,
                            4 => version = true,
                            5 => redir = true,
                            2 | 3 | 6 | 7 => {}
                            _ => {}
                        }
                        if !rar5_extra_ok(xtype, xdata) {
                            return bad();
                        }
                    }
                }
                if block_flags & 0x18 != 0 {
                    return unsupported("split file (volumes)");
                }
                if block_type == 3 {
                    if name == "CMT" {
                        return unsupported("archive comment");
                    }
                } else {
                    let is_dir = !redir && file_flags & 0x01 != 0;
                    if !version && !is_dir {
                        entries.push(Entry {
                            name,
                            crc: crc.unwrap_or(0),
                            size: file_size,
                            data_offset,
                            compress_size: add_size,
                            stored: (compr >> 7) & 7 == 0,
                            password,
                            has_crc: crc.is_some(),
                        });
                    }
                }
            }
            4 => break, // encrypted headers, no password: rarfile stops
            5 => {
                let Some((endarc_flags, _)) = vint(&hdata, pos) else { return bad() };
                if endarc_flags & 0x01 != 0 {
                    return unsupported("multi-volume archive");
                }
                break;
            }
            _ => break, // unknown block: rarfile treats it as the end
        }
        if add_size > 0 {
            f.seek(SeekFrom::Start(data_offset + add_size))?;
        }
    }
    Ok(Catalog { entries })
}

/// Whether `rarfile` parses this file-extra record without raising.
fn rar5_extra_ok(xtype: u64, x: &[u8]) -> bool {
    let Some((_, mut p)) = vint(x, 0) else { return false };
    let take = |p: &mut usize, n: usize| -> bool {
        if *p + n > x.len() {
            return false;
        }
        *p += n;
        true
    };
    let vi = |p: &mut usize| -> Option<u64> {
        let (v, q) = vint(x, *p)?;
        *p = q;
        Some(v)
    };
    match xtype {
        1 => {
            // encryption: algo, flags, kdf byte, salt, iv, [check]
            let (Some(_), Some(flags)) = (vi(&mut p), vi(&mut p)) else { return false };
            take(&mut p, 1) && take(&mut p, 16) && take(&mut p, 16) && (flags & 1 == 0 || take(&mut p, 12))
        }
        2 => {
            let Some(t) = vi(&mut p) else { return false };
            t != 0 || take(&mut p, 32)
        }
        3 => {
            let Some(tflags) = vi(&mut p) else { return false };
            let unix = tflags & 0x01 != 0;
            let present = [0x02u64, 0x04, 0x08].iter().filter(|b| tflags & **b != 0).count();
            for _ in 0..present {
                if unix {
                    if !take(&mut p, 4) {
                        return false;
                    }
                } else {
                    // load_windowstime: the datetime must exist (1970 ..= 9999).
                    let Some(v) = x.get(p..p + 8) else { return false };
                    let ticks = u64::from_le_bytes(v.try_into().unwrap_or([0; 8]));
                    let secs = (ticks / 10_000_000) as i64 - 11_644_473_600;
                    if !(0..253_402_300_800).contains(&secs) {
                        return false;
                    }
                    p += 8;
                }
            }
            if unix && tflags & 0x10 != 0 {
                for _ in 0..present {
                    let Some(v) = x.get(p..p + 4) else { return false };
                    if u32::from_le_bytes(v.try_into().unwrap_or([0; 4])) > 999_999_999 {
                        return false;
                    }
                    p += 4;
                }
            }
            true
        }
        4 => vi(&mut p).is_some() && vi(&mut p).is_some(),
        5 => {
            if vi(&mut p).is_none() || vi(&mut p).is_none() {
                return false;
            }
            let Some(n) = vi(&mut p) else { return false };
            take(&mut p, n as usize)
        }
        6 => {
            let Some(flags) = vi(&mut p) else { return false };
            for bit in [0x01u64, 0x02] {
                if flags & bit != 0 {
                    let Some(n) = vi(&mut p) else { return false };
                    if !take(&mut p, n as usize) {
                        return false;
                    }
                }
            }
            for bit in [0x04u64, 0x08] {
                if flags & bit != 0 && vi(&mut p).is_none() {
                    return false;
                }
            }
            true
        }
        _ => true,
    }
}

// ── public ───────────────────────────────────────────────────────────────────

fn catalog(path: &Path) -> Result<Catalog, ArchiveError> {
    let mut f = std::fs::File::open(path)?;
    let mut sig = [0u8; 8];
    let n = read_up_to(&mut f, &mut sig)?;
    if n >= 8 && sig == RAR5_ID {
        parse_rar5(&mut f)
    } else if n >= 7 && &sig[..7] == RAR3_ID {
        parse_rar3(&mut f)
    } else {
        unsupported("no RAR signature at the start (SFX or not RAR)")
    }
}

/// The catalog (`RarFile.infolist()` minus directories), in archive order.
pub fn list_members(path: &Path) -> Result<Vec<ArchiveMember>, ArchiveError> {
    Ok(catalog(path)?
        .entries
        .into_iter()
        .map(|e| ArchiveMember { name: e.name, crc: e.crc, size: Some(e.size) })
        .collect())
}

/// Hide the console window the UnRAR tool would open.
fn quiet(cmd: &mut std::process::Command) -> &mut std::process::Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd
}

/// `RarFile.read(name)` for each name, as `_RarReader.read_batch` collects it:
/// missing names, empty data and members `rarfile` fails to read are left out.
pub fn read_members(path: &Path, names: &[String], unrar: &str) -> Result<Vec<(String, Vec<u8>)>, ArchiveError> {
    let cat = catalog(path)?;
    let mut out = Vec::new();
    for name in names {
        // getinfo: the last entry with that name.
        let Some(e) = cat.entries.iter().rev().find(|e| e.name == name.trim_end_matches('/')) else { continue };
        if e.password || !e.has_crc {
            return unsupported("encrypted member or member without a CRC32");
        }
        let data = if e.stored {
            let mut f = std::fs::File::open(path)?;
            f.seek(SeekFrom::Start(e.data_offset))?;
            let mut buf = vec![0u8; e.compress_size.min(e.size) as usize];
            let got = read_up_to(&mut f, &mut buf)?;
            buf.truncate(got);
            buf
        } else {
            if unrar.is_empty() || !Path::new(unrar).is_file() {
                return unsupported("no UnRAR tool configured");
            }
            if name.contains(['*', '?']) || name.starts_with('-') {
                return unsupported("member name UnRAR would read as a pattern");
            }
            let member = name.replace('/', std::path::MAIN_SEPARATOR_STR);
            let output = quiet(std::process::Command::new(unrar).args(["p", "-inul", "-p-", "--"]).arg(path).arg(&member))
                .stdin(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .output()?;
            
            output.stdout
        };
        if data.len() as u64 != e.size || crc32fast::hash(&data) != e.crc {
            continue; // rarfile raises on a short read or bad CRC; read_batch skips it
        }
        if !data.is_empty() {
            out.push((name.clone(), data));
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decoders_match_python() {
        assert_eq!(decode_name("héllo".as_bytes()), "héllo");
        assert_eq!(decode_name(b"a\x00b\x00"), "a\u{0}b\u{0}"); // valid UTF-8 first
        assert_eq!(decode_name(b"\xe9t\xe9"), "été");
        assert_eq!(decode_name(b"\xe9\x00"), "é"); // UTF-16LE before cp1252
        assert_eq!(cp1252_replace(b"\x80\x81\xe9"), "\u{20ac}\u{fffd}é");
        assert_eq!(utf16le_replace(b"a\x00\x00\xd8"), "a\u{fffd}");
        assert_eq!(vint(&[0x81, 0x01], 0), Some((129, 2)));
    }
}
