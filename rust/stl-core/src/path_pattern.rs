//! Folder path patterns (`{creator}/{release}/{title}`): a port of the pure parts
//! of `app/utils/path_pattern.py` (`parse_release_date`, `format_release_date`,
//! `pattern_to_regex`, `parse_path`, the substitution in `render_path_pattern`).
//! Writing the extracted values to a model (`apply_extracted_to_model`) needs the
//! database and moves with the write services (step 4d).
//!
//! The generated regex only uses literals, `[^/]+`, `[^/]+?` and groups, where
//! the `regex` crate's leftmost-first matching gives Python's results. Groups get
//! synthetic names (`g0`, `g1`, ...) because Python accepts token names the
//! `regex` crate does not.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;

/// Token name (lowercase) -> field key, as `TOKEN_MAP`.
pub fn token_field(token: &str) -> Option<&'static str> {
    Some(match token {
        "source" => "source",
        "creator" | "author" => "creator",
        "title" | "name" => "title",
        "date" | "month" => "release_date",
        "url" => "url",
        "sculptor" => "sculptor_name",
        "collection" | "category" => "collection_name",
        "subcollection" | "subcategory" => "subcollection_name",
        "sequence" => "sequence",
        "release" => "release_name",
        _ => return None,
    })
}

/// `(year, month)` from `"YYYY-MM"`, `"YYYY_MM"` or `"YYYY"` (surrounding space ignored).
/// ASCII digits only; see [`py_int`].
pub fn parse_release_date(s: &str) -> (Option<i64>, Option<i64>) {
    static YM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^([0-9]{4})[-_]([0-9]{1,2})$").unwrap());
    static Y: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^([0-9]{4})$").unwrap());
    let t = py_strip(s);
    if let Some(c) = YM.captures(t) {
        return (py_int(&c[1]), py_int(&c[2]));
    }
    if let Some(c) = Y.captures(t) {
        return (py_int(&c[1]), None);
    }
    (None, None)
}

/// `str.strip()` with no arguments: Unicode whitespace.
fn py_strip(s: &str) -> &str {
    s.trim_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}

/// `int()` of ASCII digits. Python's `int()` also takes other Unicode decimal
/// digits; release dates and `{token:n}` limits written with them are not
/// supported here (deliberately: none exist in practice).
fn py_int(digits: &str) -> Option<i64> {
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    digits.parse().ok()
}

/// `format_release_date`: `""`, `"YYYY"` or `"YYYY-MM"`.
pub fn format_release_date(year: Option<i64>, month: Option<i64>) -> String {
    match (year, month) {
        (None, _) => String::new(),
        (Some(y), Some(m)) => format!("{y}-{m:02}"),
        (Some(y), None) => y.to_string(),
    }
}

/// A compiled pattern: the regex plus, per group, the token it captures.
pub struct CompiledPattern {
    rx: Regex,
    anchored: Regex,
    groups: Vec<String>,
}

static PLACEHOLDER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{\w+(?::\d+)?\}|\*").unwrap());

/// `pattern_to_regex`: `None` when the pattern has no `{token}` or `*`.
pub fn compile(pattern: &str) -> Option<CompiledPattern> {
    let marks: Vec<regex::Match> = PLACEHOLDER.find_iter(pattern).collect();
    if marks.is_empty() {
        return None;
    }
    let mut out = String::new();
    let mut groups = Vec::new();
    let mut last = 0;
    for (k, m) in marks.iter().enumerate() {
        out.push_str(&regex::escape(&pattern[last..m.start()]));
        let text = m.as_str();
        if text == "*" {
            out.push_str("[^/]+");
        } else {
            let token = text[1..text.len() - 1].split(':').next().unwrap_or("");
            // Only the last placeholder is greedy, so tokens fill left to right.
            let q = if k + 1 == marks.len() { "[^/]+" } else { "[^/]+?" };
            out.push_str(&format!("(?P<g{}>{q})", groups.len()));
            groups.push(token.to_string());
        }
        last = m.end();
    }
    out.push_str(&regex::escape(&pattern[last..]));
    let rx = Regex::new(&out).ok()?;
    let anchored = Regex::new(&format!("^(?:{out})$")).ok()?;
    Some(CompiledPattern { rx, anchored, groups })
}

/// `os.path.splitext` on Windows (`ntpath`), root part only.
fn splitext_root(p: &str) -> &str {
    let sep = p.rfind(['/', '\\']).map(|i| i as isize).unwrap_or(-1);
    if let Some(dot) = p.rfind('.')
        && dot as isize > sep
    {
        let start = (sep + 1) as usize;
        if p[start..dot].chars().any(|c| c != '.') {
            return &p[..dot];
        }
    }
    p
}

impl CompiledPattern {
    /// `_result_from_match`: `(field, value)` in pattern order; a captured title
    /// loses its extension.
    fn result(&self, caps: &regex::Captures) -> Fields {
        let mut out = Fields::new();
        for (k, token) in self.groups.iter().enumerate() {
            let Some(m) = caps.name(&format!("g{k}")) else { continue };
            let Some(field) = token_field(&token.to_lowercase()) else { continue };
            let mut value = m.as_str();
            if value.is_empty() {
                continue;
            }
            if field == "title" {
                let root = splitext_root(value);
                if !root.is_empty() {
                    value = root;
                }
            }
            set(&mut out, field, value);
        }
        out
    }
}

/// Extracted fields in the order Python's dict holds them: pattern order, a
/// repeated field keeping its first position and its last value.
pub type Fields = Vec<(String, String)>;

fn set(fields: &mut Fields, field: &str, value: &str) {
    match fields.iter_mut().find(|(f, _)| f == field) {
        Some(slot) => slot.1 = value.to_string(),
        None => fields.push((field.to_string(), value.to_string())),
    }
}

/// [`compile`], cached: a crawl parses every archive path with the same pattern
/// (Python keeps 64 in an `lru_cache`; this keeps up to 64 and then starts over).
fn compile_cached(pattern: &str) -> Option<std::sync::Arc<CompiledPattern>> {
    use std::sync::{Arc, Mutex};
    type Cache = HashMap<String, Option<Arc<CompiledPattern>>>;
    static CACHE: LazyLock<Mutex<Cache>> = LazyLock::new(|| Mutex::new(HashMap::new()));
    let mut cache = CACHE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some(hit) = cache.get(pattern) {
        return hit.clone();
    }
    if cache.len() >= 64 {
        cache.clear();
    }
    let compiled = compile(pattern).map(Arc::new);
    cache.insert(pattern.to_string(), compiled.clone());
    compiled
}

/// `parse_path`: field values extracted from `path` by `pattern` (empty when no match).
pub fn parse_path(pattern: &str, path: &str) -> Fields {
    let norm = path.replace('\\', "/");
    let norm_pattern = pattern.replace('\\', "/");
    let Some(cp) = compile_cached(&norm_pattern) else { return Fields::new() };
    let filename = norm.rsplit('/').next().unwrap_or(&norm);
    let parent = match norm.rfind('/') {
        Some(i) => &norm[..i],
        None => norm.as_str(),
    };
    let archive_stem = splitext_root(filename);

    // Pass 1: the whole path, accepted only when the match reaches the end.
    let first = cp.rx.captures(&norm);
    if norm_pattern.starts_with('/') {
        if let Some(c) = &first
            && c.get(0).unwrap().end() == norm.len()
        {
            return cp.result(c);
        }
    } else {
        // The shortest suffix that matches the whole pattern.
        for (i, _) in norm.char_indices() {
            if let Some(c) = cp.anchored.captures(&norm[i..]) {
                return cp.result(&c);
            }
        }
        if let Some(c) = &first
            && c.get(0).unwrap().end() == norm.len()
        {
            return cp.result(c);
        }
    }

    // Pass 1b: a pattern without separators against the filename alone.
    if !pattern.contains('/')
        && let Some(c) = cp.rx.captures(filename)
        && c.get(0).unwrap().end() == filename.len()
    {
        return cp.result(&c);
    }

    // Pass 2: the parent directory; the archive stem fills a missing title.
    if !parent.is_empty()
        && parent != norm
        && let Some(c) = cp.rx.captures(parent)
    {
        let mut out = cp.result(&c);
        if !out.iter().any(|(f, _)| f == "title") && !archive_stem.is_empty() {
            set(&mut out, "title", archive_stem);
        }
        return out;
    }

    // A match that stopped short (e.g. {name} took a folder) is still used.
    first.map(|c| cp.result(&c)).unwrap_or_default()
}

/// The substitution of `render_path_pattern`: each `{token}` / `{token:n}` becomes
/// `values[token]` (`"_unknown"` when absent), cut to `n` characters, with
/// `\ / : * ? " < > |` replaced by `_`.
pub fn render(pattern: &str, values: &HashMap<String, String>) -> String {
    static TOKEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{(\w+(?::\d+)?)\}").unwrap());
    TOKEN
        .replace_all(pattern, |c: &regex::Captures| {
            let mut parts = c[1].splitn(2, ':');
            let token = parts.next().unwrap_or("").to_lowercase();
            let raw = values.get(&token).map(String::as_str).unwrap_or("_unknown");
            let raw: String = match parts.next().and_then(py_int) {
                Some(n) => raw.chars().take(n.max(0) as usize).collect(),
                None => raw.to_string(),
            };
            raw.chars().map(|ch| if r#"\/:*?"<>|"#.contains(ch) { '_' } else { ch }).collect::<String>()
        })
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(pairs: &[(&str, &str)]) -> Fields {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    #[test]
    fn release_dates() {
        assert_eq!(parse_release_date(" 2024-03 "), (Some(2024), Some(3)));
        assert_eq!(parse_release_date("2024_7"), (Some(2024), Some(7)));
        assert_eq!(parse_release_date("2024"), (Some(2024), None));
        assert_eq!(parse_release_date("24-03"), (None, None));
        assert_eq!(parse_release_date("२०२४"), (None, None)); // ASCII digits only (Python: 2024)
        assert_eq!(format_release_date(Some(2024), Some(3)), "2024-03");
        assert_eq!(format_release_date(None, Some(3)), "");
    }

    #[test]
    fn nested_and_flat_paths() {
        let p = "/{source}/{creator}/*/{name}";
        // An absolute pattern matches from the first segment (Python gives the same).
        assert_eq!(parse_path(p, "/lib/Patreon/Sanix/2024/Dragon.7z"),
                   map(&[("source", "lib"), ("creator", "Patreon"), ("title", "2024")]));
        assert_eq!(parse_path(p, "/Patreon/Sanix/2024/Dragon.7z"),
                   map(&[("source", "Patreon"), ("creator", "Sanix"), ("title", "Dragon")]));
        assert_eq!(parse_path("{creator}/{date} - {name}", "F:\\STL\\Sanix\\2024-03 - Dragon.zip"),
                   map(&[("creator", "Sanix"), ("release_date", "2024-03"), ("title", "Dragon")]));
        assert_eq!(parse_path("no placeholders", "a/b.zip"), Fields::new());
    }

    #[test]
    fn rendering() {
        let v: HashMap<String, String> = map(&[("creator", "Sa/nix"), ("title", "Dragon")]).into_iter().collect();
        assert_eq!(render("{creator:1}/{creator}/{title}/{url}", &v), "S/Sa_nix/Dragon/_unknown");
    }
}
