//! Rescan and Generate Thumbnails for one archive: an archive the readers refuse
//! is reported, not guessed at. The results go to `storage::jobs::apply_rescan` / `replace_thumbnails`.

use crate::crawler::{SIZE_LIMIT_IMAGE, extract_native, fallback_fingerprint};
use crate::metadata;
use crate::path_pattern;
use crate::rules::{self, Checks, MemberPath, PresupportPatterns};
use crate::storage::crawl::{round4, stat};
use crate::storage::jobs::Rescan;
use crate::winpath::WinPath;

/// The library settings a rescan uses.
#[derive(Debug, Clone)]
pub struct Options {
    pub unrar: String,
    pub warn: Checks,
    pub presupport_patterns: Vec<String>,
}

/// The printable suffixes the rescan stores in the STL file list (the
/// desktop's `_stl_exts`, narrower than the crawler's).
const RESCAN_STL_EXTS: [&str; 5] = [".stl", ".obj", ".lys", ".chitubox", ".ags"];

fn patterns(list: &[String]) -> Result<PresupportPatterns, String> {
    PresupportPatterns::new(Some(list)).map_err(|e| e.to_string())
}

/// Rescan values for the archive at `path`, whose record has `old_size`,
/// `old_mtime` and `old_hash`. The hash is recomputed only when the size or
/// modification time changed; the file list (`files`) is always read again.
pub fn rescan(path: &str, old_size: Option<i64>, old_mtime: Option<i64>, old_hash: &str, opts: &Options) -> Result<Rescan, String> {
    let path = &crate::relpath::on_disk(path);
    let p = std::path::Path::new(path);
    if !p.exists() {
        return Err("Archive not found".into());
    }
    let (fs_size, fs_mtime) = match stat(path) {
        Some((s, m)) => (Some(s), Some(m)),
        None => (None, None),
    };
    let unchanged = fs_size.is_some() && fs_mtime.is_some() && fs_size == old_size && fs_mtime == old_mtime;
    let members = crate::archive::list_members(p).ok();
    let hash = match &members {
        _ if unchanged => old_hash.to_string(),
        Some(members) => crate::archive::fingerprint_of(members),
        None => fallback_fingerprint(path),
    };
    let pats = patterns(&opts.presupport_patterns)?;
    let extracted = extract_native(path, &opts.unrar, Some(0)).ok_or("the server cannot read this archive; left unchanged")?;
    if extracted.names.is_empty() {
        return Err("Could not read archive contents — left unchanged".into());
    }
    let files: Vec<(String, Vec<u8>)> = extracted
        .contents
        .iter()
        .filter(|(n, _)| n.to_lowercase().ends_with(".url") || n.to_lowercase().ends_with("info.json"))
        .cloned()
        .collect();
    let parent = crate::winpath::native(&WinPath::new(path).parent().as_posix());
    let meta = metadata::extract(path, &files, Some(&parent));
    let release = meta.release_date.as_deref().map(path_pattern::parse_release_date).and_then(|(y, m)| y.map(|y| (y, m)));
    let names = &extracted.names;
    let warnings = rules::detect_warnings(names, opts.warn, &pats).map_err(|e| e.to_string())?;
    let name = WinPath::new(path).name().to_string();
    let mut scale_names = vec![name];
    scale_names.extend(names.iter().cloned());
    let compression_rate = match (fs_size, extracted.uncompressed) {
        (Some(s), Some(u)) if u > 0 => Some(round4(s as f64 / u as f64)),
        _ => None,
    };
    let stl_files = names
        .iter()
        .filter(|n| RESCAN_STL_EXTS.contains(&MemberPath::new(&n.replace('\\', "/")).suffix().to_lowercase().as_str()))
        .cloned()
        .collect();
    Ok(Rescan {
        archive_hash: hash,
        archive_size: fs_size,
        archive_mtime_ns: fs_mtime,
        compression_rate,
        title: meta.title,
        creator: meta.creator,
        source: meta.distributor,
        release,
        url: meta.url,
        description: meta.description,
        presupports: rules::detect_presupport(names).to_string(),
        warning: Some(warnings).filter(|w| !w.is_empty()),
        scales: rules::detect_scale(&scale_names),
        stl_files,
        files: members,
    })
}

/// What Generate Thumbnails stores for one archive.
#[derive(Debug, Default)]
pub struct Thumbnails {
    /// `(member name, JPEG or None for a failed conversion)` in member-name order.
    pub thumbs: Vec<(String, Option<Vec<u8>>)>,
    pub warning: Option<String>,
    /// Members whose conversion failed.
    pub failed: Vec<String>,
}

/// Generate Thumbnails: the archive's images, sorted by name, converted until
/// `max_thumbnails` succeed (failures are kept, as the desktop records them),
/// and the warnings (the desktop checks printable files here regardless of the
/// setting). Only as many images are read as may be needed: more after a failure.
pub fn thumbnails(path: &str, max_thumbnails: usize, max_size: u32, opts: &Options) -> Result<Thumbnails, String> {
    let path = &crate::relpath::on_disk(path);
    if !std::path::Path::new(path).exists() {
        return Err("archive not found".into());
    }
    let pats = patterns(&opts.presupport_patterns)?;
    let members = crate::archive::list_members(std::path::Path::new(path)).map_err(|_| "the server cannot read this archive")?;
    let mut images: Vec<String> = members
        .iter()
        .filter(|m| m.size.is_none_or(|s| s <= SIZE_LIMIT_IMAGE))
        .filter(|m| rules::IMAGE_SUFFIXES.contains(&MemberPath::new(&m.name.replace('\\', "/")).suffix().to_lowercase().as_str()))
        .map(|m| m.name.clone())
        .collect();
    images.sort();
    images.dedup();
    let mut out = Thumbnails::default();
    let mut stored = 0;
    let mut unread = None;
    let mut rest = images.as_slice();
    while stored < max_thumbnails && !rest.is_empty() {
        let (now, later) = rest.split_at((max_thumbnails - stored).min(rest.len()));
        rest = later;
        let read = match crate::archive::read_members_with(std::path::Path::new(path), now, &opts.unrar) {
            Ok(read) => read,
            Err(e) => {
                unread = Some(e.to_string());
                continue;
            }
        };
        // By name, whatever order the reader gives.
        let read: std::collections::BTreeMap<String, Vec<u8>> = read.into_iter().collect();
        for (name, raw) in read.into_iter().filter(|(_, raw)| raw.len() as u64 <= SIZE_LIMIT_IMAGE) {
            let jpeg = crate::thumbnail::make_thumbnail(&raw, max_size, true).ok();
            if jpeg.is_some() {
                stored += 1;
            } else {
                out.failed.push(name.clone());
            }
            out.thumbs.push((name, jpeg));
        }
    }
    if let Some(why) = unread.filter(|_| out.thumbs.is_empty()) {
        return Err(format!("the archive's images cannot be read ({why})"));
    }
    let warn = Checks { no_printable: true, ..opts.warn };
    let names: Vec<String> = members.into_iter().map(|m| m.name).collect();
    let w = rules::detect_warnings(&names, warn, &pats).map_err(|e| e.to_string())?;
    out.warning = Some(w).filter(|w| !w.is_empty());
    Ok(out)
}

#[cfg(test)]
mod tests {
    use std::io::Write;

    use super::*;

    fn png(rgb: [u8; 3]) -> Vec<u8> {
        let img = image::RgbImage::from_pixel(20, 10, image::Rgb(rgb));
        let mut out = std::io::Cursor::new(Vec::new());
        img.write_to(&mut out, image::ImageFormat::Png).unwrap();
        out.into_inner()
    }

    fn zip(path: &std::path::Path, members: &[(&str, &[u8])]) {
        let mut z = zip::ZipWriter::new(std::fs::File::create(path).unwrap());
        let o = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
        for (n, d) in members {
            z.start_file(*n, o).unwrap();
            z.write_all(d).unwrap();
        }
        z.finish().unwrap();
    }

    fn opts() -> Options {
        Options {
            unrar: String::new(),
            warn: Checks::default(),
            presupport_patterns: rules::DEFAULT_PRESUPPORT_PATTERNS.iter().map(|s| s.to_string()).collect(),
        }
    }

    #[test]
    fn rescan_reads_metadata_rules_and_hash() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("Hero 32mm.zip");
        let info = br#"{"name": "The Hero", "author": "Studio"}"#;
        zip(&p, &[("hero_supported.stl", b"solid\nendsolid\n"), ("parts/arm.obj", b"o"), ("info.json", info)]);
        let path = p.to_string_lossy().into_owned();
        let r = rescan(&path, None, None, "", &opts()).unwrap();
        assert_eq!(r.archive_hash, crate::archive::fingerprint(&p).unwrap());
        assert_eq!(r.files, Some(crate::archive::list_members(&p).unwrap()), "the file list, read again");
        assert_eq!((r.title.as_deref(), r.creator.as_deref()), (Some("The Hero"), Some("Studio")));
        assert_eq!(r.scales, vec!["32 mm".to_string()]);
        assert_eq!(r.stl_files, vec!["hero_supported.stl".to_string(), "parts/arm.obj".to_string()]);
        assert!(r.warning.as_deref().unwrap().contains(rules::WARNING_NO_IMAGES));
        assert!(r.compression_rate.is_some());
        // Unchanged size and time: the stored hash is kept.
        let (s, m) = stat(&path).unwrap();
        assert_eq!(rescan(&path, Some(s), Some(m), "kept", &opts()).unwrap().archive_hash, "kept");
        assert!(rescan(&dir.path().join("gone.zip").to_string_lossy(), None, None, "", &opts()).is_err());
    }

    #[test]
    fn images_that_cannot_be_read_give_way_to_the_next() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("a.zip");
        let image = png([1, 2, 3]);
        zip(&path, &[("a.png", &image), ("b.png", &image), ("c.png", &image)]);
        let mut bytes = std::fs::read(&path).unwrap();
        let first = bytes.windows(4).position(|w| w == b"PK\x01\x02").unwrap();
        bytes[first + 8] |= 1; // a.png is encrypted
        std::fs::write(&path, &bytes).unwrap();
        let t = thumbnails(&path.to_string_lossy(), 1, 100, &opts()).unwrap();
        assert_eq!(t.thumbs.iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(), ["b.png"]);
    }

    #[test]
    fn thumbnails_stop_at_the_limit_and_keep_failures() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("a.zip");
        let (r, g) = (png([255, 0, 0]), png([0, 255, 0]));
        zip(&p, &[("b.png", &g), ("a.png", &r), ("broken.jpg", b"not an image"), ("c.png", &r), ("x.stl", b"s")]);
        let t = thumbnails(&p.to_string_lossy(), 2, 64, &opts()).unwrap();
        let names: Vec<&str> = t.thumbs.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(names, ["a.png", "b.png"]);
        let t = thumbnails(&p.to_string_lossy(), 3, 64, &opts()).unwrap();
        let names: Vec<&str> = t.thumbs.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(names, ["a.png", "b.png", "broken.jpg", "c.png"]);
        assert_eq!(t.failed, ["broken.jpg"]);
        assert_eq!(t.warning, None);
    }
}
