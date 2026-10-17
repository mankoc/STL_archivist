//! Crawler rules over archive member names: presupport classification, warnings,
//! scale detection. A port of `app/crawler/rules.py`; golden tests compare the two.
//!
//! The Python code reads member names through `pathlib` on Windows, so [`MemberPath`]
//! reproduces `WindowsPath` for names that use `/`: empty and `.` segments vanish,
//! a leading `X:` is a drive part, and `suffix` / `stem` follow Python 3.14
//! (`"a."` has suffix `"."`; a name of dots only has no suffix).
//!
//! Presupport filename patterns are Python regexes (the defaults use a
//! lookbehind), compiled here with `fancy-regex`. A pattern that `fancy-regex`
//! rejects makes the call fail with [`RulesError::Pattern`] instead of skipping
//! it, because Python might accept it; the caller then uses the Python rules.

use std::collections::HashSet;
use std::fmt;
use std::sync::LazyLock;

use fancy_regex::{Regex, RegexBuilder};

pub const IMAGE_SUFFIXES: &[&str] = &[".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif", ".gif"];
pub const ARCHIVE_SUFFIXES: &[&str] = &[".7z", ".zip", ".rar"];
const PRINTABLE_SUFFIXES: &[&str] = &[".stl", ".obj", ".lys", ".lyt", ".chitubox", ".ctp"];

pub const DEFAULT_PRESUPPORT_PATTERNS: &[&str] = &[
    r"[_ -]presupported",
    r"(?<!un)[ _-]supported\.stl$",
    r"^sup_.*\.stl$",
    r"^(?:s|\[s\])_.*\.stl$",
];

pub const WARNING_PRESUPPORTED: &str = "Models with presupported stl";
pub const WARNING_NO_IMAGES: &str = "Archive without images";
pub const WARNING_NESTED: &str = "Nested archives";
pub const WARNING_NO_PRINTABLE: &str = "Archive without printable files";

#[derive(Debug)]
pub enum RulesError {
    /// A presupport pattern `fancy-regex` cannot compile (or run).
    Pattern(String),
}

impl fmt::Display for RulesError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RulesError::Pattern(m) => write!(f, "presupport pattern: {m}"),
        }
    }
}

impl std::error::Error for RulesError {}

fn ci(pattern: &str) -> Regex {
    RegexBuilder::new(pattern).case_insensitive(true).build().expect("built-in pattern")
}

static PRESUPPORTED_STEM: LazyLock<Regex> = LazyLock::new(|| ci(r"[_ -]supported"));
static SCALE_MM: LazyLock<Regex> = LazyLock::new(|| ci(r"(?<!\d)(\d+)[_ ]?mm(?![A-Za-z0-9])"));
static SCALE_RATIO: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?<!\d)1[/\-](\d+)(?![A-Za-z0-9])").expect("built-in pattern"));
static DEFAULT_PRESUPPORT: LazyLock<Regex> = LazyLock::new(|| {
    let alternation: Vec<String> = DEFAULT_PRESUPPORT_PATTERNS.iter().map(|p| format!("(?:{p})")).collect();
    ci(&alternation.join("|"))
});

/// A member name as `pathlib.Path(name.replace("\\", "/"))` sees it on Windows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemberPath {
    /// `parent.parts`: the drive (if any) and the directory names.
    pub parents: Vec<String>,
    /// The final component (`""` when there is none).
    pub name: String,
}

impl MemberPath {
    pub fn new(raw: &str) -> Self {
        let norm = raw.replace('\\', "/");
        let mut rest = norm.as_str();
        let mut parents = Vec::new();
        let b = rest.as_bytes();
        if b.len() >= 2 && b[0].is_ascii_alphabetic() && b[1] == b':' {
            parents.push(rest[..2].to_string());
            rest = &rest[2..];
        }
        let mut segs: Vec<&str> = rest.split('/').filter(|s| !s.is_empty() && *s != ".").collect();
        let name = segs.pop().map(str::to_string).unwrap_or_default();
        if name.is_empty() && !parents.is_empty() {
            // "C:" alone: the drive is the whole path; its name is empty.
            return MemberPath { parents: Vec::new(), name };
        }
        parents.extend(segs.into_iter().map(str::to_string));
        MemberPath { parents, name }
    }

    /// `PurePath.suffix` (Python 3.14).
    pub fn suffix(&self) -> &str {
        let stripped = self.name.trim_start_matches('.');
        match stripped.rfind('.') {
            Some(i) => &stripped[i..],
            None => "",
        }
    }

    /// `PurePath.stem` (Python 3.14).
    pub fn stem(&self) -> &str {
        if let Some(i) = self.name.rfind('.') {
            let stem = &self.name[..i];
            if !stem.trim_start_matches('.').is_empty() {
                return stem;
            }
        }
        &self.name
    }

    fn suffix_lower(&self) -> String {
        self.suffix().to_lowercase()
    }
}

/// `detect_presupport`: `"Lychee"`, `"Chitubox"`, `"Presupported"` or `"No"`.
pub fn detect_presupport<S: AsRef<str>>(names: &[S]) -> &'static str {
    let (mut chitu, mut presupported) = (false, false);
    for name in names {
        let p = MemberPath::new(name.as_ref());
        let suffix = p.suffix_lower();
        match suffix.as_str() {
            ".lys" | ".lyt" => return "Lychee",
            ".chitu" | ".chitubox" | ".ctp" => chitu = true,
            ".stl" if PRESUPPORTED_STEM.is_match(p.stem()).unwrap_or(false) => presupported = true,
            _ => {}
        }
    }
    if chitu {
        "Chitubox"
    } else if presupported {
        "Presupported"
    } else {
        "No"
    }
}

static DIR_TOKENS: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    ["ps", "presup", "presups", "presupport", "presupports", "presupported", "sup", "sups", "support", "supports", "supported"]
        .into_iter()
        .collect()
});
static NEGATIONS: LazyLock<HashSet<&'static str>> =
    LazyLock::new(|| ["un", "no", "non", "not", "without", "wo"].into_iter().collect());
static UNSUPPORTED_TOKENS: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    [
        "unsup", "unsupp", "unsupport", "unsupports", "unsupported", "nosup", "nosupp", "nosupport", "nosupports",
        "nosupported", "nonsupported", "notsupported", "nops",
    ]
    .into_iter()
    .collect()
});

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SupportState {
    Marked,
    Negated,
}

/// `_segment_support_state`: tokens split on anything but `[a-z0-9]`, read in order.
fn segment_support_state(segment: &str) -> Option<SupportState> {
    let lower = segment.to_lowercase();
    let tokens: Vec<&str> =
        lower.split(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit())).filter(|t| !t.is_empty()).collect();
    let mut marked = false;
    for (i, tok) in tokens.iter().enumerate() {
        if UNSUPPORTED_TOKENS.contains(tok) {
            return Some(SupportState::Negated);
        }
        if DIR_TOKENS.contains(tok) {
            if i > 0 && NEGATIONS.contains(tokens[i - 1]) {
                return Some(SupportState::Negated);
            }
            marked = true;
        }
    }
    marked.then_some(SupportState::Marked)
}

/// Compiled presupport filename patterns (`compile_presupport_patterns`).
pub struct PresupportPatterns(Vec<Regex>);

impl PresupportPatterns {
    /// `None`: the built-in defaults. Any pattern `fancy-regex` rejects is an error.
    pub fn new<S: AsRef<str>>(patterns: Option<&[S]>) -> Result<Self, RulesError> {
        match patterns {
            None => Ok(PresupportPatterns(vec![DEFAULT_PRESUPPORT.clone()])),
            Some(ps) => ps
                .iter()
                .map(|p| {
                    RegexBuilder::new(p.as_ref())
                        .case_insensitive(true)
                        .build()
                        .map_err(|e| RulesError::Pattern(format!("{}: {e}", p.as_ref())))
                })
                .collect::<Result<Vec<_>, _>>()
                .map(PresupportPatterns),
        }
    }

    fn any_match(&self, name: &str) -> Result<bool, RulesError> {
        for rx in &self.0 {
            if rx.is_match(name).map_err(|e| RulesError::Pattern(e.to_string()))? {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

/// `_is_presupported_member`.
fn is_presupported_member(p: &MemberPath, patterns: &PresupportPatterns) -> Result<bool, RulesError> {
    if p.suffix_lower() != ".stl" {
        return Ok(false);
    }
    let states: Vec<Option<SupportState>> = p.parents.iter().map(|s| segment_support_state(s)).collect();
    if states.contains(&Some(SupportState::Negated)) || segment_support_state(p.stem()) == Some(SupportState::Negated) {
        return Ok(false);
    }
    if states.contains(&Some(SupportState::Marked)) {
        return Ok(true);
    }
    patterns.any_match(&p.name)
}

/// Which warnings to check (the crawler's configuration flags).
#[derive(Debug, Clone, Copy)]
pub struct Checks {
    pub presupported: bool,
    pub no_images: bool,
    pub nested_archive: bool,
    pub no_printable: bool,
}

impl Default for Checks {
    fn default() -> Self {
        Checks { presupported: true, no_images: true, nested_archive: true, no_printable: true }
    }
}

/// `explain_warnings`: `(warning, members that triggered it)` in canonical order.
/// Warnings raised by an absence carry no members.
pub fn explain_warnings<S: AsRef<str>>(
    names: &[S],
    checks: Checks,
    patterns: &PresupportPatterns,
) -> Result<Vec<(&'static str, Vec<String>)>, RulesError> {
    let (mut presupported, mut nested) = (Vec::new(), Vec::new());
    let (mut has_image, mut has_printable) = (false, false);
    for name in names {
        let name = name.as_ref();
        let p = MemberPath::new(name);
        let suffix = p.suffix_lower();
        if checks.no_images && IMAGE_SUFFIXES.contains(&suffix.as_str()) {
            has_image = true;
        }
        if checks.no_printable && PRINTABLE_SUFFIXES.contains(&suffix.as_str()) {
            has_printable = true;
        }
        if checks.presupported && is_presupported_member(&p, patterns)? {
            presupported.push(name.to_string());
        }
        if checks.nested_archive && ARCHIVE_SUFFIXES.contains(&suffix.as_str()) {
            nested.push(name.to_string());
        }
    }
    let mut out = Vec::new();
    if checks.presupported && !presupported.is_empty() {
        out.push((WARNING_PRESUPPORTED, presupported));
    }
    if checks.no_images && !has_image {
        out.push((WARNING_NO_IMAGES, Vec::new()));
    }
    if checks.nested_archive && !nested.is_empty() {
        out.push((WARNING_NESTED, nested));
    }
    if checks.no_printable && !has_printable {
        out.push((WARNING_NO_PRINTABLE, Vec::new()));
    }
    Ok(out)
}

/// `detect_warnings`: the warning lines joined with `\n` (`""` when none apply).
pub fn detect_warnings<S: AsRef<str>>(
    names: &[S],
    checks: Checks,
    patterns: &PresupportPatterns,
) -> Result<String, RulesError> {
    let lines: Vec<&str> = explain_warnings(names, checks, patterns)?.into_iter().map(|(w, _)| w).collect();
    Ok(lines.join("\n"))
}

/// `detect_scale`: `"<n> mm"` and `"1/<n>"` found in each name's stem, first
/// appearance order, without duplicates.
pub fn detect_scale<S: AsRef<str>>(names: &[S]) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut found = Vec::new();
    for name in names {
        let p = MemberPath::new(name.as_ref());
        let stem = p.stem();
        for (rx, fmt) in [(&*SCALE_MM, 0u8), (&*SCALE_RATIO, 1u8)] {
            for caps in rx.captures_iter(stem).flatten() {
                let n = caps.get(1).map(|m| m.as_str()).unwrap_or_default();
                let scale = if fmt == 0 { format!("{n} mm") } else { format!("1/{n}") };
                if seen.insert(scale.clone()) {
                    found.push(scale);
                }
            }
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn member_path_matches_windows_pathlib() {
        // (name, parent parts, name, suffix, stem) from Python 3.14 WindowsPath.
        let cases: &[(&str, &[&str], &str, &str, &str)] = &[
            ("a.", &[], "a.", ".", "a"),
            (".hidden", &[], ".hidden", "", ".hidden"),
            ("x/.stl", &["x"], ".stl", "", ".stl"),
            ("dir/file.STL", &["dir"], "file.STL", ".STL", "file"),
            ("C:foo.stl", &["C:"], "foo.stl", ".stl", "foo"),
            ("a/b/../c.stl", &["a", "b", ".."], "c.stl", ".stl", "c"),
            ("a//b.stl", &["a"], "b.stl", ".stl", "b"),
            ("trail/", &[], "trail", "", "trail"),
            ("a.tar.gz", &[], "a.tar.gz", ".gz", "a.tar"),
            ("..", &[], "..", "", ".."),
            ("a/./b.stl", &["a"], "b.stl", ".stl", "b"),
            ("w\\x\\y.stl", &["w", "x"], "y.stl", ".stl", "y"),
        ];
        for &(raw, parents, name, suffix, stem) in cases {
            let p = MemberPath::new(raw);
            assert_eq!(p.parents, parents, "{raw}");
            assert_eq!((p.name.as_str(), p.suffix(), p.stem()), (name, suffix, stem), "{raw}");
        }
    }

    #[test]
    fn presupport_classification() {
        assert_eq!(detect_presupport(&["a/body.stl", "x.lys"]), "Lychee");
        assert_eq!(detect_presupport(&["a.ctp", "b_supported.stl"]), "Chitubox");
        assert_eq!(detect_presupport(&["arm-Supported.stl"]), "Presupported");
        assert_eq!(detect_presupport(&["arm.stl", "pic.jpg"]), "No");
    }

    #[test]
    fn warnings_follow_the_python_rules() {
        let pats = PresupportPatterns::new::<&str>(None).unwrap();
        let w = |names: &[&str]| detect_warnings(names, Checks::default(), &pats).unwrap();
        assert_eq!(w(&["Presupported/body.stl", "a.jpg"]), WARNING_PRESUPPORTED);
        assert_eq!(w(&["supports/Unsupported/120MM_Unsup.stl", "a.jpg"]), "");
        assert_eq!(w(&["No_Supported/x.stl", "a.png"]), "");
        assert_eq!(w(&["Props/x.stl", "a.png"]), "");
        assert_eq!(w(&["body_presupported.stl", "sub.zip"]), "Models with presupported stl\nArchive without images\nNested archives");
        assert_eq!(w(&["unsupported.stl", "a.jpg"]), "");
        assert_eq!(w(&["readme.txt"]), "Archive without images\nArchive without printable files");
    }

    #[test]
    fn invalid_pattern_is_an_error() {
        assert!(PresupportPatterns::new(Some(&["(unclosed"])).is_err());
    }

    #[test]
    fn scales() {
        assert_eq!(detect_scale(&["Hero_32mm_Base.stl", "x/75 mm.stl", "1-35 tank.stl", "a_28MM.stl", "128mmx.stl"]),
                   vec!["32 mm", "75 mm", "1/35", "28 mm"]);
        assert_eq!(detect_scale(&["11/35.stl", "2028mm"]), vec!["2028 mm"]);
    }
}
