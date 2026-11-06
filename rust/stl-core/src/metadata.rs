//! Model metadata inference: a port of `app/crawler/metadata.py`
//! (`extract_metadata`) and `archive.find_url_file`.
//!
//! Python's `re` semantics are reproduced explicitly: `\b` is a boundary
//! between `[\p{L}\p{N}_]` (`str.isalnum()` or `_`) and anything else, `\d` is
//! `\p{Nd}`, `\s` is `str.isspace()`. Paths follow Windows `pathlib`
//! (`parts`, case-insensitive `relative_to`). What cannot be read (an
//! `info.json` that is not JSON, a value that is neither text nor a number, an
//! archive that cannot be listed) is skipped: the rest is still used.

use std::path::Path;
use std::sync::LazyLock;

use fancy_regex::{Regex, RegexBuilder};

use crate::archive;
use crate::rules::MemberPath;

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

/// `str(v)` for a JSON scalar (`None` for a list or an object).
fn py_str(v: &serde_json::Value) -> Option<String> {
    use serde_json::Value;
    match v {
        Value::String(s) => Some(s.clone()),
        Value::Bool(true) => Some("True".into()),
        Value::Bool(false) => Some("False".into()),
        Value::Null => Some("None".into()),
        Value::Number(n) if n.is_i64() || n.is_u64() => Some(n.to_string()),
        Value::Number(n) => Some(n.to_string()),
        Value::Array(_) | Value::Object(_) => None,
    }
}

/// A truthy `info.json` value as text (lists and objects skipped).
fn text_field(info: &serde_json::Map<String, serde_json::Value>, key: &str) -> Option<String> {
    info.get(key).filter(|v| truthy(v)).and_then(py_str)
}

/// `archive.find_url_file`: a `.url` inside the archive, then beside it, then in
/// a `*-links` / `*_links` sibling folder, then in the folder above when that one
/// has at most [`MAX_MODEL_SUBFOLDERS`] subfolders (and is not a drive's root).
pub fn find_url_file(archive_path: &Path) -> Option<String> {
    if let Some(u) = read_url_file(archive_path).filter(|u| !u.is_empty()) {
        return Some(u);
    }
    let parent = archive_path.parent()?;
    let parent = if parent.as_os_str().is_empty() { Path::new(".") } else { parent };
    for f in glob_sorted(parent, |n| ends_ci(n, ".url")) {
        if let Some(u) = parse_url_shortcut(&f)
            && !u.is_empty()
        {
            return Some(u);
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
                return Some(u);
            }
        }
    }
    // A model's folder holding a few part folders (`Supported/`, `Unsupported/`, …)
    // keeps its link above them; a folder of many models is not one model's.
    let grand = parent.parent().filter(|g| !g.as_os_str().is_empty() && g.parent().is_some())?;
    let folders = std::fs::read_dir(grand).ok()?.flatten().filter(|e| e.file_type().is_ok_and(|t| t.is_dir())).count();
    if folders > MAX_MODEL_SUBFOLDERS {
        return None;
    }
    glob_sorted(grand, |n| ends_ci(n, ".url")).iter().find_map(|f| parse_url_shortcut(f).filter(|u| !u.is_empty()))
}

/// Most subfolders a model's folder has for [`find_url_file`] to take its link.
const MAX_MODEL_SUBFOLDERS: usize = 6;

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

/// `archive.read_url_file`: the `URL=` value of the first `.url` member that
/// has one (zip and 7z; the crawler reads RAR members itself).
fn read_url_file(archive_path: &Path) -> Option<String> {
    let ext = archive_path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
    if ext != "zip" && ext != "7z" {
        return None;
    }
    for m in archive::list_members(archive_path).ok()? {
        if MemberPath::new(&m.name).suffix().to_lowercase() != ".url" {
            continue;
        }
        let Ok(data) = archive::read_members(archive_path, std::slice::from_ref(&m.name)) else { continue };
        if let Some((_, bytes)) = data.into_iter().next()
            && let Some(u) = url_line(&decode_ignore(&bytes))
        {
            return Some(u);
        }
    }
    None
}

/// `info.json` beside the archive when it is the only archive in its folder (a
/// downloaded model's folder: the archive, its pictures, its link and its info).
fn info_beside(archive_path: &Path) -> Option<Vec<u8>> {
    let parent = archive_path.parent().filter(|p| !p.as_os_str().is_empty())?;
    let is_archive = |n: &str| {
        let n = n.to_lowercase();
        [".zip", ".7z", ".rar"].iter().any(|e| n.ends_with(e)) && n != "metadata.7z"
    };
    if glob_sorted(parent, is_archive).len() != 1 {
        return None;
    }
    let file = glob_sorted(parent, |n| n.eq_ignore_ascii_case("info.json")).into_iter().next()?;
    std::fs::read(file).ok()
}

/// A distributor's usual spelling (`cults3d` → `Cults3D`), else as given.
fn distributor_name(d: &str) -> String {
    let d = py_strip(d);
    DISTRIBUTORS
        .iter()
        .find(|(re, _)| re.find(d).ok().flatten().is_some_and(|m| m.as_str().len() == d.len()))
        .map_or_else(|| d.to_string(), |(_, name)| (*name).to_string())
}

/// `extract_metadata`: path inference, then what the `.url` and `info.json`
/// say ([`from_files`]). `contents` is `extract_archive_contents`' result in
/// member order.
pub fn extract(archive_path: &str, contents: &[(String, Vec<u8>)], source_dir: Option<&str>) -> Metadata {
    let mut meta = infer_from_path(archive_path, source_dir);
    let found = from_files(archive_path, contents);
    let keep = |new: Option<String>, old: &mut Option<String>| {
        if new.is_some() {
            *old = new;
        }
    };
    keep(found.url, &mut meta.url);
    keep(found.title, &mut meta.title);
    keep(found.creator, &mut meta.creator);
    keep(found.distributor, &mut meta.distributor);
    keep(found.description, &mut meta.description);
    keep(found.release, &mut meta.release);
    if !found.tags.is_empty() {
        meta.tags = found.tags;
    }
    meta
}

/// What an archive's link and `info.json` say, nothing inferred from its path:
/// the `.url` (members in `contents` first, then [`find_url_file`]), then
/// `info.json` (in `contents`, else beside an archive alone in its folder),
/// whose `url` wins.
pub fn from_files(archive_path: &str, contents: &[(String, Vec<u8>)]) -> Metadata {
    let mut meta = Metadata::default();
    let url_from_archive = contents
        .iter()
        .filter(|(name, _)| is_url_file(name))
        .find_map(|(_, data)| url_line(&decode_ignore(data)).filter(|u| !u.is_empty()));
    meta.url = url_from_archive.or_else(|| find_url_file(Path::new(archive_path)));

    let raw = match contents.iter().find(|(name, _)| is_info_json(name)) {
        Some((_, raw)) => Some(raw.clone()),
        None => info_beside(Path::new(archive_path)),
    };
    let info = raw.filter(|r| !r.is_empty()).and_then(|r| {
        // A byte order mark is common in files saved on Windows.
        let text = String::from_utf8_lossy(&r);
        match serde_json::from_str(text.trim_start_matches('\u{feff}')) {
            Ok(serde_json::Value::Object(info)) => Some(info),
            _ => None,
        }
    });
    if let Some(info) = info {
        meta.title = text_field(&info, "name");
        meta.creator = text_field(&info, "creator").or_else(|| text_field(&info, "author"));
        meta.distributor = text_field(&info, "distributor").map(|d| distributor_name(&d));
        if let Some(u) = text_field(&info, "url") {
            meta.url = Some(u);
        }
        meta.description = text_field(&info, "description");
        if let Some(serde_json::Value::Array(tags)) = info.get("tags") {
            meta.tags = tags.iter().filter_map(py_str).collect();
        }
        meta.release = text_field(&info, "release");
    }
    meta
}

/// Whether an archive member is `info.json` (in any folder of the archive).
pub fn is_info_json(member: &str) -> bool {
    let n = MemberPath::new(&member.replace('\\', "/")).name;
    n.is_ascii() && n.eq_ignore_ascii_case("info.json")
}

/// Whether an archive member is a `.url` link.
pub fn is_url_file(member: &str) -> bool {
    MemberPath::new(&member.replace('\\', "/")).suffix().to_lowercase() == ".url"
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
        let m = extract("missing/x.zip", &c, None);
        assert_eq!((m.title.as_deref(), m.creator.as_deref(), m.release.as_deref()), (Some("N"), Some("A"), Some("5")));
        assert_eq!(m.tags, ["x", "1", "True"]);
        // What cannot be read is skipped, the rest kept.
        let odd = vec![(
            "info.json".to_string(),
            br#"{"name": 1.5, "author": ["x"], "distributor": "cults3d", "tags": ["a", {}]}"#.to_vec(),
        )];
        let m = extract(r"F:\STL\Patreon\Sanix\x.zip", &odd, Some("F:/STL"));
        assert_eq!((m.title.as_deref(), m.creator.as_deref(), m.distributor.as_deref()), (Some("1.5"), Some("Sanix"), Some("Cults3D")));
        assert_eq!(m.tags, ["a"]);
        let bad = vec![("info.json".to_string(), b"not json".to_vec())];
        assert_eq!(extract("missing/x.zip", &bad, None).title.as_deref(), Some("x"));
    }

    #[test]
    fn info_json_and_url_beside_a_lone_archive() {
        let dir = tempfile::tempdir().unwrap();
        let folder = dir.path().join("Oola");
        std::fs::create_dir_all(&folder).unwrap();
        std::fs::write(folder.join("oola.zip"), b"not really a zip").unwrap();
        std::fs::write(folder.join("model.url"), b"[InternetShortcut]\r\nURL=https://example.com/oola\r\n").unwrap();
        let info = "\u{feff}{\"name\": \"Oola slave crouching\", \"author\": \"Tchelos\", \"distributor\": \"cults3d\", \
                    \"url\": \"https://cults3d.com/oola\", \"description\": \"Star Wars\", \"tags\": [\"oola\", \"jedi\"]}";
        std::fs::write(folder.join("info.json"), info).unwrap();
        let path = folder.join("oola.zip").to_string_lossy().into_owned();
        let m = extract(&path, &[], None);
        assert_eq!(m.title.as_deref(), Some("Oola slave crouching"));
        assert_eq!(m.creator.as_deref(), Some("Tchelos"));
        assert_eq!(m.distributor.as_deref(), Some("Cults3D"));
        assert_eq!(m.url.as_deref(), Some("https://cults3d.com/oola"), "info.json's url wins over the .url");
        assert_eq!(m.description.as_deref(), Some("Star Wars"));
        assert_eq!(m.tags, ["oola", "jedi"]);

        // With a second archive in the folder, the info is not this archive's.
        std::fs::write(folder.join("other.7z"), b"x").unwrap();
        let m = extract(&path, &[], None);
        assert_eq!(m.title.as_deref(), Some("oola"));
        assert_eq!(m.url.as_deref(), Some("https://example.com/oola"), "the .url beside it still counts");
    }

    #[test]
    fn url_from_a_model_folder_above_its_part_folders() {
        let dir = tempfile::tempdir().unwrap();
        let model = dir.path().join("Library").join("Dragon");
        for part in ["Supported", "Unsupported"] {
            std::fs::create_dir_all(model.join(part)).unwrap();
            std::fs::write(model.join(part).join("dragon.zip"), b"x").unwrap();
        }
        std::fs::write(model.join("Dragon.url"), b"[InternetShortcut]\r\nURL=https://shop.example/dragon\r\n").unwrap();
        let archive = model.join("Supported").join("dragon.zip");
        assert_eq!(find_url_file(&archive).as_deref(), Some("https://shop.example/dragon"));

        // Above more than six subfolders it is a library folder, not a model's.
        for i in 0..5 {
            std::fs::create_dir_all(model.join(format!("extra{i}"))).unwrap();
        }
        assert_eq!(find_url_file(&archive), None);
        // Nor is the link taken from two levels up.
        let deeper = model.join("Supported").join("v2");
        std::fs::create_dir_all(&deeper).unwrap();
        std::fs::write(deeper.join("dragon.zip"), b"x").unwrap();
        std::fs::remove_dir_all(model.join("extra0")).unwrap();
        assert_eq!(find_url_file(&deeper.join("dragon.zip")), None);
    }
}
