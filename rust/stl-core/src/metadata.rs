//! Model metadata inference: a port of `app/crawler/metadata.py`
//! (`extract_metadata`) and `archive.find_url_file`.
//!
//! Python's `re` semantics are reproduced explicitly: `\b` is a boundary
//! between `[\p{L}\p{N}_]` (`str.isalnum()` or `_`) and anything else, `\d` is
//! `\p{Nd}`, `\s` is `str.isspace()`. Paths follow Windows `pathlib`
//! (`parts`, case-insensitive `relative_to`). Input the port does not reproduce
//! exactly returns [`Unsupported`], and the caller uses Python: `info.json`
//! that `serde_json` rejects (Python also reads `NaN`) or whose fields are not
//! strings, and a `.url` search that would have to open a RAR archive or an
//! archive the native reader cannot list.

use std::path::Path;
use std::sync::LazyLock;

use fancy_regex::{Regex, RegexBuilder};

use crate::archive;
use crate::rules::MemberPath;

/// Input the native port does not reproduce exactly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unsupported(pub String);

impl std::fmt::Display for Unsupported {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "unsupported: {}", self.0)
    }
}

impl std::error::Error for Unsupported {}

/// `ModelMetadata`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Metadata {
    pub title: Option<String>,
    pub creator: Option<String>,
    pub distributor: Option<String>,
    pub release_date: Option<String>,
    pub url: Option<String>,
    pub description: Option<String>,
    pub tags: Vec<String>,
    pub release: Option<String>,
}

const W: &str = r"[\p{L}\p{N}_]";

fn word_re(core: &str, ci: bool) -> Regex {
    RegexBuilder::new(&format!("(?<!{W}){core}(?!{W})")).case_insensitive(ci).build().expect("built-in pattern")
}

static DISTRIBUTORS: LazyLock<Vec<(Regex, &'static str)>> = LazyLock::new(|| {
    [
        ("patreons?", "Patreon"),
        ("kickstarters?", "Kickstarter"),
        ("myminifactory", "MyMiniFactory"),
        ("artstation", "ArtStation"),
        ("gumroad", "Gumroad"),
        ("cults3d", "Cults3D"),
        ("cults", "Cults3D"),
        ("thingiverse", "Thingiverse"),
        ("printables", "Printables"),
    ]
    .into_iter()
    .map(|(p, n)| (word_re(p, true), n))
    .collect()
});

static DATES: LazyLock<[Regex; 2]> =
    LazyLock::new(|| [word_re(r"(\p{Nd}{4}[-_]\p{Nd}{2})", false), word_re(r"(\p{Nd}{4})", false)]);

const PY_SPACE: &str = r"[\s\x1c-\x1f]";

static DASHES: LazyLock<regex::Regex> = LazyLock::new(|| regex::Regex::new(r"[_\-]+").expect("built-in pattern"));
static DATE_PREFIX: LazyLock<regex::Regex> = LazyLock::new(|| {
    regex::Regex::new(&format!(r"^\p{{Nd}}{{4}}(?:{PY_SPACE}|[\-_])\p{{Nd}}{{2}}{PY_SPACE}*")).expect("built-in pattern")
});
static YEAR_PREFIX: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(&format!(r"^\p{{Nd}}{{4}}{PY_SPACE}+")).expect("built-in pattern"));

/// `str.isspace()`.
fn py_isspace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// `str.strip()`.
fn py_strip(s: &str) -> &str {
    s.trim_matches(py_isspace)
}

/// `str.splitlines()`.
fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        let brk = matches!(c, '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}');
        if brk {
            out.push(&s[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r'
                && let Some(&(j, '\n')) = it.peek()
            {
                it.next();
                end = j + 1;
            }
            start = end;
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// `bytes.decode("utf-8", errors="ignore")`.
fn decode_ignore(b: &[u8]) -> String {
    b.utf8_chunks().map(|c| c.valid()).collect()
}

/// The first `URL=` line of a `.url` file's text, as `line[4:].strip()`.
fn url_line(text: &str) -> Option<String> {
    for line in py_splitlines(text) {
        let line = py_strip(line);
        let head: Vec<char> = line.chars().take(4).collect();
        if head.len() == 4 && head.iter().collect::<String>().eq_ignore_ascii_case("url=") {
            let rest: String = line.chars().skip(4).collect();
            return Some(py_strip(&rest).to_string());
        }
    }
    None
}

/// Windows `PurePath.parts`.
fn win_parts(path: &str) -> Vec<String> {
    let norm = path.replace('/', "\\");
    let b = norm.as_bytes();
    let mut anchor = String::new();
    let mut rest = norm.as_str();
    if b.len() >= 2 && b[0] == b'\\' && b[1] == b'\\' && b.get(2) != Some(&b'\\') {
        let after = &norm[2..];
        let mut it = after.splitn(3, '\\');
        let server = it.next().unwrap_or("");
        let share = it.next().unwrap_or("");
        anchor = format!("\\\\{server}\\{share}\\");
        rest = it.next().unwrap_or("");
    } else if b.len() >= 2 && b[0].is_ascii_alphabetic() && b[1] == b':' {
        anchor.push_str(&norm[..2]);
        rest = &norm[2..];
        if rest.starts_with('\\') {
            anchor.push('\\');
        }
    } else if rest.starts_with('\\') {
        anchor.push('\\');
    }
    let mut parts: Vec<String> = Vec::new();
    if !anchor.is_empty() {
        parts.push(anchor);
    }
    parts.extend(rest.split('\\').filter(|p| !p.is_empty() && *p != ".").map(str::to_string));
    parts
}

/// `Path(path).relative_to(base).parts` on Windows (case-insensitive), or `None`.
pub(crate) fn win_relative_parts(path: &str, base: &str) -> Option<Vec<String>> {
    let p = win_parts(path);
    let b = win_parts(base);
    if b.len() > p.len() {
        return None;
    }
    let same = p.iter().zip(&b).all(|(x, y)| x.to_lowercase() == y.to_lowercase());
    same.then(|| p[b.len()..].to_vec())
}

/// `_infer_from_path`.
pub fn infer_from_path(archive_path: &str, source_dir: Option<&str>) -> Metadata {
    let mut meta = Metadata::default();
    let parts = match source_dir {
        Some(base) => win_relative_parts(archive_path, base).unwrap_or_else(|| win_parts(archive_path)),
        None => win_parts(archive_path),
    };
    let stem = crate::winpath::WinPath::new(archive_path).stem().to_string();
    let dashed = py_strip(&DASHES.replace_all(&stem, " ")).to_string();
    let t = py_strip(&DATE_PREFIX.replace(&dashed, "")).to_string();
    let t = py_strip(&YEAR_PREFIX.replace(&t, "")).to_string();
    meta.title = Some(if t.is_empty() { dashed } else { t });

    let n = parts.len();
    let mut distributor_idx = None;
    'outer: for (i, part) in parts.iter().take(n.saturating_sub(1)).enumerate() {
        for (re, name) in DISTRIBUTORS.iter() {
            if re.is_match(part).unwrap_or(false) {
                meta.distributor = Some((*name).to_string());
                distributor_idx = Some(i);
                break 'outer;
            }
        }
    }
    if let Some(i) = distributor_idx
        && i + 1 < n.saturating_sub(1)
    {
        meta.creator = Some(parts[i + 1].clone());
    }
    'dates: for part in parts.iter().rev() {
        for re in DATES.iter() {
            if let Ok(Some(c)) = re.captures(part)
                && let Some(m) = c.get(1)
            {
                meta.release_date = Some(m.as_str().to_string());
                break 'dates;
            }
        }
    }
    meta
}

/// Python truthiness of a JSON value.
fn truthy(v: &serde_json::Value) -> bool {
    use serde_json::Value;
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(v)` for the JSON values whose Python form is simple.
fn py_str(v: &serde_json::Value) -> Result<String, Unsupported> {
    use serde_json::Value;
    match v {
        Value::String(s) => Ok(s.clone()),
        Value::Bool(true) => Ok("True".into()),
        Value::Bool(false) => Ok("False".into()),
        Value::Null => Ok("None".into()),
        Value::Number(n) if n.is_i64() || n.is_u64() => Ok(n.to_string()),
        _ => Err(Unsupported("info.json value without a simple str()".into())),
    }
}

fn text_field(info: &serde_json::Map<String, serde_json::Value>, key: &str) -> Result<Option<String>, Unsupported> {
    match info.get(key) {
        Some(v) if truthy(v) => match v {
            serde_json::Value::String(s) => Ok(Some(s.clone())),
            _ => Err(Unsupported(format!("info.json {key} is not a string"))),
        },
        _ => Ok(None),
    }
}

/// `archive.find_url_file`: a `.url` inside the archive, then beside it, then in
/// a `*-links` / `*_links` sibling folder.
pub fn find_url_file(archive_path: &Path) -> Result<Option<String>, Unsupported> {
    if let Some(u) = read_url_file(archive_path)?
        && !u.is_empty()
    {
        return Ok(Some(u));
    }
    let Some(parent) = archive_path.parent() else { return Ok(None) };
    let parent = if parent.as_os_str().is_empty() { Path::new(".") } else { parent };
    for f in glob_sorted(parent, |n| ends_ci(n, ".url")) {
        if let Some(u) = parse_url_shortcut(&f)
            && !u.is_empty()
        {
            return Ok(Some(u));
        }
    }
    for sep in ["-links", "_links"] {
        let mut hits: Vec<(String, String, std::path::PathBuf)> = Vec::new();
        for d in glob_sorted(parent, |n| ends_ci(n, sep)) {
            if !d.is_dir() {
                continue;
            }
            let dname = d.file_name().map(|s| s.to_string_lossy().to_lowercase()).unwrap_or_default();
            for f in glob_sorted(&d, |n| ends_ci(n, ".url")) {
                let fname = f.file_name().map(|s| s.to_string_lossy().to_lowercase()).unwrap_or_default();
                hits.push((dname.clone(), fname, f));
            }
        }
        hits.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
        for (_, _, f) in hits {
            if let Some(u) = parse_url_shortcut(&f)
                && !u.is_empty()
            {
                return Ok(Some(u));
            }
        }
    }
    Ok(None)
}

fn ends_ci(name: &str, tail: &str) -> bool {
    name.to_lowercase().ends_with(tail)
}

/// Entries of `dir` whose name matches, sorted by lowercased name (Windows `sorted(glob)`).
fn glob_sorted(dir: &Path, matches: impl Fn(&str) -> bool) -> Vec<std::path::PathBuf> {
    let Ok(rd) = std::fs::read_dir(dir) else { return Vec::new() };
    let mut v: Vec<(String, std::path::PathBuf)> = rd
        .filter_map(|e| e.ok())
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            matches(&name).then(|| (name.to_lowercase(), e.path()))
        })
        .collect();
    v.sort_by(|a, b| a.0.cmp(&b.0));
    v.into_iter().map(|(_, p)| p).collect()
}

/// `_parse_url_shortcut`.
fn parse_url_shortcut(path: &Path) -> Option<String> {
    let data = std::fs::read(path).ok()?;
    url_line(&decode_ignore(&data))
}

/// `archive.read_url_file`: the `URL=` value of the first `.url` member that has one.
fn read_url_file(archive_path: &Path) -> Result<Option<String>, Unsupported> {
    let ext = archive_path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    if ext != "zip" && ext != "7z" {
        return Err(Unsupported(format!("reading .url members of a .{ext} archive")));
    }
    let members = match archive::list_members(archive_path) {
        Ok(m) => m,
        Err(_) if !archive_path.exists() => return Ok(None),
        Err(e) => return Err(Unsupported(format!("listing the archive: {e}"))),
    };
    for m in members {
        if MemberPath::new(&m.name).suffix().to_lowercase() != ".url" {
            continue;
        }
        let data = archive::read_members(archive_path, std::slice::from_ref(&m.name))
            .map_err(|e| Unsupported(format!("reading {}: {e}", m.name)))?;
        if let Some((_, bytes)) = data.into_iter().next()
            && !bytes.is_empty()
            && let Some(u) = url_line(&decode_ignore(&bytes))
        {
            return Ok(Some(u));
        }
    }
    Ok(None)
}

/// `extract_metadata`: path inference, then the `.url`, then `info.json`.
/// `contents` is `extract_archive_contents`' result in member order.
pub fn extract(archive_path: &str, contents: &[(String, Vec<u8>)], source_dir: Option<&str>) -> Result<Metadata, Unsupported> {
    let mut meta = infer_from_path(archive_path, source_dir);

    let mut url_from_archive: Option<String> = None;
    for (name, data) in contents {
        if MemberPath::new(&name.replace('\\', "/")).suffix().to_lowercase() == ".url" {
            url_from_archive = url_line(&decode_ignore(data));
            if url_from_archive.as_deref().is_some_and(|u| !u.is_empty()) {
                break;
            }
        }
    }
    let url = match url_from_archive.filter(|u| !u.is_empty()) {
        Some(u) => Some(u),
        None => find_url_file(Path::new(archive_path))?,
    };
    if let Some(u) = url.filter(|u| !u.is_empty()) {
        meta.url = Some(u);
    }

    let raw = contents.iter().find(|(name, _)| {
        let n = MemberPath::new(&name.replace('\\', "/")).name;
        n.is_ascii() && n.eq_ignore_ascii_case("info.json")
    });
    if let Some((_, raw)) = raw
        && !raw.is_empty()
    {
        let text = String::from_utf8_lossy(raw);
        let value: serde_json::Value =
            serde_json::from_str(&text).map_err(|e| Unsupported(format!("info.json: {e}")))?;
        let serde_json::Value::Object(info) = value else {
            return Err(Unsupported("info.json is not an object".into()));
        };
        if let Some(t) = text_field(&info, "name")? {
            meta.title = Some(t);
        }
        if let Some(c) = text_field(&info, "creator")? {
            meta.creator = Some(c);
        } else if let Some(a) = text_field(&info, "author")? {
            meta.creator = Some(a);
        }
        if let Some(d) = text_field(&info, "distributor")? {
            meta.distributor = Some(d);
        }
        if let Some(u) = text_field(&info, "url")? {
            meta.url = Some(u);
        }
        if let Some(d) = text_field(&info, "description")? {
            meta.description = Some(d);
        }
        if let Some(serde_json::Value::Array(tags)) = info.get("tags")
            && !tags.is_empty()
        {
            meta.tags = tags.iter().map(py_str).collect::<Result<_, _>>()?;
        }
        if let Some(r) = info.get("release")
            && truthy(r)
        {
            meta.release = Some(py_str(r)?);
        }
    }
    Ok(meta)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn path_inference() {
        let m = infer_from_path(r"F:\STL\Patreon\Sanix\2024-03\Big_Dragon-Bust.7z", Some("F:/stl"));
        assert_eq!(m.distributor.as_deref(), Some("Patreon"));
        assert_eq!(m.creator.as_deref(), Some("Sanix"));
        assert_eq!(m.release_date.as_deref(), Some("2024-03"));
        assert_eq!(m.title.as_deref(), Some("Big Dragon Bust"));
        let m = infer_from_path(r"F:\x\2024_09 Hero.zip", None);
        assert_eq!(m.title.as_deref(), Some("Hero"));
        assert_eq!(m.release_date.as_deref(), Some("2024_09"));
        assert_eq!(infer_from_path(r"F:\2024年\patreons\x.zip", None).release_date, None);
        assert_eq!(infer_from_path(r"F:\Patreon\x.zip", None).creator, None);
    }

    #[test]
    fn python_string_helpers() {
        assert_eq!(py_splitlines("a\r\nb\rc\x1cd\n"), ["a", "b", "c", "d"]);
        assert_eq!(url_line("[x]\r\n  URL= http://a \r\n"), Some("http://a".into()));
        assert_eq!(decode_ignore(b"a\xffb"), "ab");
        assert_eq!(win_parts("F:/a/b.7z"), ["F:\\", "a", "b.7z"]);
        assert_eq!(win_relative_parts("F:/STL/a/b.7z", "f:\\stl"), Some(vec!["a".into(), "b.7z".into()]));
        assert_eq!(win_relative_parts("F:/STL/a/b.7z", "G:/stl"), None);
    }

    #[test]
    fn info_json() {
        let c = vec![("info.json".to_string(), br#"{"name": "N", "author": "A", "tags": ["x", 1, true], "release": 5}"#.to_vec())];
        let m = extract("missing/x.zip", &c, None).unwrap();
        assert_eq!((m.title.as_deref(), m.creator.as_deref(), m.release.as_deref()), (Some("N"), Some("A"), Some("5")));
        assert_eq!(m.tags, ["x", "1", "True"]);
        let bad = vec![("info.json".to_string(), br#"{"name": 1}"#.to_vec())];
        assert!(extract("missing/x.zip", &bad, None).is_err());
    }
}
