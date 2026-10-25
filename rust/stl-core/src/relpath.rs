//! Portable paths of archives relative to their library's root (see
//! `storage::libraries`). A [`RelPath`] is what the database stores: the same
//! text on every operating system, so a library catalogued on Windows opens on
//! Linux (and back) once its root is set.
//!
//! The form: `/` separators; no leading `/`, drive, empty, `.` or `..` part;
//! Unicode NFC (macOS stores some names decomposed); letter case as on disk.

use std::fmt;

use unicode_normalization::UnicodeNormalization;

use crate::winpath::SEP;

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct RelPath(String);

/// Folders that differ only in letter case are the same folder here.
pub const CASE_INSENSITIVE: bool = cfg!(windows);

/// `path` with `/` separators (and `\` read as one on Windows), no trailing `/`.
fn slashed(path: &str) -> String {
    let s = if cfg!(windows) { path.replace('\\', "/") } else { path.to_string() };
    let t = s.trim_end_matches('/');
    if t.is_empty() && s.starts_with('/') { "/".into() } else { t.to_string() }
}

/// How two absolute paths compare here (case folded on Windows).
pub fn path_key(path: &str) -> String {
    let s = slashed(path);
    if CASE_INSENSITIVE { s.to_lowercase() } else { s }
}

impl RelPath {
    /// A stored relative path, checked.
    pub fn parse(s: &str) -> Result<RelPath, String> {
        if s.is_empty() {
            return Err("an empty relative path".into());
        }
        if s.starts_with('/') || s.contains('\\') && cfg!(windows) || s.as_bytes().get(1) == Some(&b':') {
            return Err(format!("{s:?} is not a relative path"));
        }
        let n: String = s.nfc().collect();
        if n.split('/').any(|p| p.is_empty() || p == "." || p == "..") {
            return Err(format!("{s:?} has an empty, . or .. part"));
        }
        Ok(RelPath(n))
    }

    /// `abs` relative to `root`, or `None` when it is not below `root`.
    pub fn from_abs(root: &str, abs: &str) -> Option<RelPath> {
        let (root, abs) = (slashed(root), slashed(abs));
        // Part by part: the root's parts (an empty first one for `/`), then the rest.
        let root_parts: Vec<&str> = if root == "/" { vec![""] } else { root.split('/').collect() };
        let abs_parts: Vec<&str> = abs.split('/').collect();
        if abs_parts.len() <= root_parts.len() {
            return None;
        }
        let same = |a: &str, b: &str| if CASE_INSENSITIVE { a.to_lowercase() == b.to_lowercase() } else { a == b };
        if !root_parts.iter().zip(&abs_parts).all(|(r, a)| same(r, a)) {
            return None;
        }
        RelPath::parse(&abs_parts[root_parts.len()..].join("/")).ok()
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// The absolute path under `root`, with this system's separator.
    pub fn resolve(&self, root: &str) -> String {
        join(root, &self.0)
    }

    /// The folder part (`""` at the root).
    pub fn folder(&self) -> &str {
        self.0.rfind('/').map_or("", |i| &self.0[..i])
    }

    /// The last part (the archive's file name).
    pub fn name(&self) -> &str {
        self.0.rfind('/').map_or(&self.0, |i| &self.0[i + 1..])
    }
}

impl fmt::Display for RelPath {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// `root` joined with a `/`-separated relative path (or folder; `""` gives the
/// root), spelled with this system's separator.
pub fn join(root: &str, rel: &str) -> String {
    if rel.is_empty() {
        return root.to_string();
    }
    let rel = if SEP == '/' { rel.to_string() } else { rel.replace('/', &SEP.to_string()) };
    crate::winpath::join(root, &rel)
}

/// `root` joined with a relative path in `/` form whatever the system (what
/// the folder tree and path filters compare).
pub fn posix_join(root: &str, rel: &str) -> String {
    let r = root.replace('\\', "/");
    let r = r.trim_end_matches('/');
    match (r.is_empty(), rel.is_empty()) {
        (_, true) if r.is_empty() => "/".into(),
        (_, true) => r.to_string(),
        (true, false) => format!("/{rel}"),
        (false, false) => format!("{r}/{rel}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_only_portable_relative_paths() {
        assert_eq!(RelPath::parse("Studio/2024-01 Release/Dragon.zip").unwrap().as_str(), "Studio/2024-01 Release/Dragon.zip");
        for bad in ["", "/abs/x.zip", "C:/x.zip", "a//b.zip", "a/./b.zip", "a/../b.zip", "a/"] {
            assert!(RelPath::parse(bad).is_err(), "{bad}");
        }
        // Decomposed (macOS) and composed spellings are the same path.
        let decomposed = "Bu\u{0308}ste.zip";
        assert_eq!(RelPath::parse(decomposed).unwrap().as_str(), "B\u{00fc}ste.zip");
    }

    #[test]
    fn relative_to_a_root() {
        let r = RelPath::from_abs("/library/Gambody", "/library/Gambody/Studio/Dragon.zip").unwrap();
        assert_eq!((r.as_str(), r.folder(), r.name()), ("Studio/Dragon.zip", "Studio", "Dragon.zip"));
        assert_eq!(RelPath::from_abs("/library/Gambody/", "/library/Gambody/x.7z").unwrap().folder(), "");
        assert!(RelPath::from_abs("/library/Gambody", "/library/GambodyX/x.zip").is_none(), "a sibling with the same prefix");
        assert!(RelPath::from_abs("/library/Gambody", "/library/Gambody").is_none(), "the root itself");
        assert!(RelPath::from_abs("/", "/x.zip").is_some());
        if cfg!(windows) {
            let r = RelPath::from_abs(r"D:\STL", r"d:\stl\Studio\Dragon.zip").unwrap();
            assert_eq!(r.as_str(), "Studio/Dragon.zip", "case-insensitive root, the archive's own spelling");
            assert_eq!(r.resolve(r"E:\Moved"), r"E:\Moved\Studio\Dragon.zip");
            assert_eq!(RelPath::from_abs("D:/", r"D:\x.zip").unwrap().as_str(), "x.zip");
        } else {
            assert!(RelPath::from_abs("/Library", "/library/x.zip").is_none(), "case matters on Linux");
            assert_eq!(RelPath::parse("a/b.zip").unwrap().resolve("/mnt/stl/"), "/mnt/stl/a/b.zip");
        }
    }

    #[test]
    fn joins() {
        assert_eq!(posix_join(r"D:\STL", "a/b.zip"), "D:/STL/a/b.zip");
        assert_eq!(posix_join("/srv/stl/", ""), "/srv/stl");
        assert_eq!(posix_join("/", "x"), "/x");
    }
}
