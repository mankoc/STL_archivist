//! `pathlib.WindowsPath` for the forms the library stores (`F:/STL/x.7z`,
//! `F:\STL\x.7z`, `//server/share/x`, `/x`, relative paths), as the Python
//! code reads them with `Path(s).parent.as_posix()` and `Path(s).suffix`.

/// A path split into its anchor (drive and/or root, in `/` form) and parts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WinPath {
    anchor: String,
    parts: Vec<String>,
}

impl WinPath {
    pub fn new(s: &str) -> Self {
        let norm = s.replace('\\', "/");
        let mut rest = norm.as_str();
        let mut anchor = String::new();
        let b = rest.as_bytes();
        if b.len() >= 2 && b[0] == b'/' && b[1] == b'/' && b.get(2) != Some(&b'/') {
            // UNC: //server/share is the drive, followed by the root.
            let after = &rest[2..];
            let mut it = after.splitn(3, '/');
            let server = it.next().unwrap_or("");
            let share = it.next().unwrap_or("");
            anchor = format!("//{server}/{share}/");
            rest = it.next().unwrap_or("");
        } else if b.len() >= 2 && b[0].is_ascii_alphabetic() && b[1] == b':' {
            anchor.push_str(&rest[..2]);
            rest = &rest[2..];
            if rest.starts_with('/') {
                anchor.push('/');
            }
        } else if rest.starts_with('/') {
            anchor.push('/');
        }
        let parts = rest.split('/').filter(|p| !p.is_empty() && *p != ".").map(str::to_string).collect();
        WinPath { anchor, parts }
    }

    /// `as_posix()`.
    pub fn as_posix(&self) -> String {
        if self.parts.is_empty() {
            return if self.anchor.is_empty() { ".".into() } else { self.anchor.clone() };
        }
        format!("{}{}", self.anchor, self.parts.join("/"))
    }

    /// `parent` (itself at the anchor, or `.` for a single relative part).
    pub fn parent(&self) -> WinPath {
        let mut parts = self.parts.clone();
        parts.pop();
        WinPath { anchor: self.anchor.clone(), parts }
    }

    /// `as_posix()` of every proper ancestor, nearest first, as the filter tree's
    /// `while p != p.parent` loop visits them.
    pub fn ancestors(&self) -> Vec<String> {
        let mut out = Vec::new();
        let mut cur = self.clone();
        loop {
            let p = cur.parent();
            if p == cur {
                break;
            }
            out.push(p.as_posix());
            cur = p;
        }
        out
    }

    /// `name`: the final part (`""` for an anchor or `.`).
    pub fn name(&self) -> &str {
        self.parts.last().map(String::as_str).unwrap_or("")
    }

    /// `stem` (Python 3.14): the final part minus its last suffix.
    pub fn stem(&self) -> &str {
        let name = self.name();
        if let Some(i) = name.rfind('.') {
            let stem = &name[..i];
            if !stem.trim_start_matches('.').is_empty() {
                return stem;
            }
        }
        name
    }

    /// `suffix` (Python 3.14) of the final part.
    pub fn suffix(&self) -> &str {
        let name = self.parts.last().map(String::as_str).unwrap_or("");
        let stripped = name.trim_start_matches('.');
        match stripped.rfind('.') {
            Some(i) => &stripped[i..],
            None => "",
        }
    }
}

/// The separator of the paths the library stores and opens: `\` on Windows
/// (as the PyQt app stored them), `/` elsewhere.
pub const SEP: char = if cfg!(windows) { '\\' } else { '/' };

/// A `/`-form path (as [`WinPath::as_posix`] gives) spelled with [`SEP`].
pub fn native(posix: &str) -> String {
    if cfg!(windows) { posix.replace('/', "\\") } else { posix.to_string() }
}

/// `str(Path(dir) / name)` for a plain file name, with [`SEP`].
pub fn join(dir: &str, name: &str) -> String {
    if dir.ends_with('\\') || dir.ends_with('/') { format!("{dir}{name}") } else { format!("{dir}{SEP}{name}") }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_spelling() {
        if cfg!(windows) {
            assert_eq!(join("F:\\STL", "a.zip"), "F:\\STL\\a.zip");
            assert_eq!(native("F:/STL/a"), "F:\\STL\\a");
        } else {
            assert_eq!(join("/library/08 - Gambody", "Model"), "/library/08 - Gambody/Model");
            assert_eq!(native("/library/a"), "/library/a");
        }
        assert_eq!(join("F:/", "a.zip"), "F:/a.zip");
    }

    #[test]
    fn matches_windows_pathlib() {
        // (input, as_posix, ancestors, suffix) from Python 3.14 on Windows.
        let cases: &[(&str, &str, &[&str], &str)] = &[
            ("F:/STL/a/b", "F:/STL/a/b", &["F:/STL/a", "F:/STL", "F:/"], ""),
            ("F:\\STL\\x.7z", "F:/STL/x.7z", &["F:/STL", "F:/"], ".7z"),
            ("//srv/share/dir/x", "//srv/share/dir/x", &["//srv/share/dir", "//srv/share/"], ""),
            ("a/b", "a/b", &["a", "."], ""),
            ("/abs/x", "/abs/x", &["/abs", "/"], ""),
            ("F:a/b", "F:a/b", &["F:a", "F:"], ""),
            ("F:/", "F:/", &[], ""),
            (".", ".", &[], ""),
            ("F:/STL/", "F:/STL", &["F:/"], ""),
            ("C:", "C:", &[], ""),
            ("\\\\srv\\share\\d", "//srv/share/d", &["//srv/share/"], ""),
            ("", ".", &[], ""),
            ("x", "x", &["."], ""),
            ("F:/a.b/c.ZIP", "F:/a.b/c.ZIP", &["F:/a.b", "F:/"], ".ZIP"),
        ];
        for &(s, posix, ancestors, suffix) in cases {
            let p = WinPath::new(s);
            assert_eq!(p.as_posix(), posix, "{s}");
            assert_eq!(p.ancestors(), ancestors, "{s}");
            assert_eq!(p.suffix(), suffix, "{s}");
        }
    }
}
