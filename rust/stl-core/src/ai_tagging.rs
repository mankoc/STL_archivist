//! AI tagger text handling: a port of `app/utils/ai_tagging.py` (tag cleanup,
//! prompts, metadata parsing). The model itself runs in an external
//! `llama-server`; this module only builds what is sent and reads what comes back.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;

use crate::rules::{self, MemberPath};
use crate::storage::model_edit::{AiFields, AiInputs};

/// Python's `str.isspace` (Unicode `White_Space` plus the four separators).
fn py_isspace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn py_split(s: &str) -> impl Iterator<Item = &str> {
    s.split(py_isspace).filter(|w| !w.is_empty())
}

fn py_strip(s: &str) -> &str {
    s.trim_matches(py_isspace)
}

/// Python's `str.splitlines()`: every line boundary it knows, `\r\n` as one.
fn py_splitlines(s: &str) -> Vec<&str> {
    let is_break = |c: char| {
        matches!(c, '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}')
    };
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = s.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if is_break(c) {
            out.push(&s[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|&(_, n)| n == '\n') {
                chars.next();
                end += 1;
            }
            start = end;
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// `dedupe_words`: drop repeated words (case-insensitive) within one tag.
pub fn dedupe_words(tag: &str) -> String {
    let mut seen = HashSet::new();
    py_split(tag).filter(|w| seen.insert(w.to_lowercase())).collect::<Vec<_>>().join(" ")
}

static TAG_SPLIT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)[\s\x1c-\x1f]+(?:with|and|in|featuring|wearing|holding)[\s\x1c-\x1f]+|[,;&/+]").expect("valid pattern")
});

/// `atomize_tag`: split a phrase on connectives and punctuation.
pub fn atomize_tag(tag: &str) -> Vec<String> {
    TAG_SPLIT.split(tag).map(py_strip).filter(|p| !p.is_empty()).map(str::to_string).collect()
}

/// `split_cumulative_tags`: reduce each tag to the words it adds over the
/// longest other tag that is a word prefix of it.
pub fn split_cumulative_tags(tags: &[String]) -> Vec<String> {
    let seqs: Vec<Vec<String>> = tags.iter().map(|t| py_split(&t.to_lowercase()).map(str::to_string).collect()).collect();
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for (idx, (tag, words)) in tags.iter().zip(&seqs).enumerate() {
        let mut best = 0;
        for (j, other) in seqs.iter().enumerate() {
            let n = other.len();
            if j != idx && n < words.len() && words[..n] == other[..] && n > best {
                best = n;
            }
        }
        let new_tag = py_split(tag).skip(best).collect::<Vec<_>>().join(" ");
        if !new_tag.is_empty() && seen.insert(new_tag.to_lowercase()) {
            out.push(new_tag);
        }
    }
    out
}

/// `is_corrupted_output`: mostly question marks or garbage.
pub fn is_corrupted_output(text: &str) -> bool {
    let len = text.chars().count();
    if len < 3 {
        return false;
    }
    let questions = text.to_lowercase().chars().filter(|&c| c == '?').count();
    if questions as f64 / len as f64 > 0.3 {
        return true;
    }
    let valid = text.chars().filter(|&c| c.is_alphanumeric() || " ,-./".contains(c)).count();
    (valid as f64 / len as f64) < 0.2
}

/// `clean_tags`: the model's comma-separated tags as a clean list, at most
/// `max_tags` of them when that is positive.
pub fn clean_tags(raw: &str, max_tags: usize) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut tags = Vec::new();
    for part in raw.split(',') {
        for piece in atomize_tag(part) {
            let t = dedupe_words(&piece);
            if !t.is_empty() && seen.insert(t.to_lowercase()) {
                tags.push(t);
            }
        }
    }
    let mut tags = split_cumulative_tags(&tags);
    if max_tags > 0 {
        tags.truncate(max_tags);
    }
    tags
}

/// `format_known_meta`: the catalogued metadata as a prompt hint block.
pub fn format_known_meta(meta: &[(String, String)]) -> String {
    if meta.is_empty() {
        return String::new();
    }
    let lines: Vec<String> = meta.iter().map(|(k, v)| format!("- {k}: {v}")).collect();
    format!(
        "Existing catalogued metadata for this model (extracted when the archive was added — it may be \
         incomplete or inaccurate, so use it as a hint and correct it if the images or path clearly \
         contradict it):\n{}\n",
        lines.join("\n")
    )
}

/// `build_tags_prompt`.
pub fn build_tags_prompt(stems: &[String], archive_path: &str, meta: &[(String, String)]) -> String {
    let names = if stems.is_empty() {
        "(none)".to_string()
    } else {
        stems.iter().map(|s| format!("\"{s}\"")).collect::<Vec<_>>().join(", ")
    };
    let intro = if stems.len() > 1 {
        format!(
            "These {} images come from a single archive of 3D printable miniatures. They usually show the same \
             model from different angles, but some may show a different model, separate parts or accessories, \
             packaging, or promotional art. Identify and describe the main miniature; ignore unrelated images. ",
            stems.len()
        )
    } else {
        "This image shows a 3D printable miniature model. ".to_string()
    };
    format!(
        "{intro}Full archive path: \"{archive_path}\". Image file names: {names}. {}Use the full path, filenames, \
         and any existing metadata as hints alongside the images to identify the character or subject. Tag ONLY \
         what is actually shown in the images. Do NOT add related characters, vehicles, companions, or items from \
         the same franchise that do not appear (for example, do not tag other Star Wars characters just because a \
         ship is from Star Wars). Output ONLY a comma-separated list of descriptive tags. Each tag must be a \
         single, atomic concept — do NOT output cumulative or overlapping phrases. For example, output 'suit', \
         'bow tie', 'gloves' as separate tags, NOT 'figure in a suit with a bow tie and gloves'. Avoid the \
         connecting words 'with', 'and', and 'in' inside a tag. Include: character name (if recognizable), \
         franchise or source material, character type, genre, visual style, and any other relevant descriptors. \
         Output ONLY the comma-separated tags, no other text.",
        format_known_meta(meta)
    )
}

/// `build_meta_prompt`.
pub fn build_meta_prompt(archive_path: &str, meta: &[(String, String)]) -> String {
    format!(
        "This is a 3D printable miniature model. Full archive path: \"{archive_path}\". {}The archive may contain \
         several images; some may show a different model, separate parts, packaging, or promotional art — base \
         the metadata on the main miniature. Based on the images, the full path (folder structure encodes \
         sculptor, series, and source information), and any existing metadata above, output metadata in this \
         EXACT format (one line per field, leave the value empty if unknown, no extra text):\n\
         TITLE: <model title>\n\
         CREATOR: <publisher, brand or author name>\n\
         SCULPTOR: <artist or sculptor name>\n\
         COLLECTION: <the MOST SPECIFIC applicable from: fantasy, sci-fi, comics, tv, movies, games, books, \
         anime, real life, historic, cartoons, music and musicians, modelling, props, pin-ups. Prefer specific \
         over generic — e.g. Batman is 'comics' (not 'fantasy'), a Star Wars trooper is 'movies', a Witcher \
         figure is 'games'. Use 'fantasy'/'sci-fi' only for original/generic creatures with no known franchise.>\n\
         SUBCOLLECTION: <the specific franchise, publisher, series or universe within the collection — e.g. DC \
         or Marvel for comics, Star Wars for movies, Warhammer 40k for games. Empty if none/unknown.>\n\
         SCALE: <ONLY if a scale such as 28mm, 32mm, 1/35 or 54mm is explicitly written in the path, filenames, \
         or existing metadata above. A scale CANNOT be seen in an image — never guess or infer it. Leave empty \
         if it is not stated in text.>\n\
         SOURCE: <one of: patreon, kickstarter, myminifactory — or empty>\n\
         YEAR: <4-digit release year — or empty>\n\
         DESCRIPTION: <one sentence describing the model>\n\
         Output ONLY these 9 lines.",
        format_known_meta(meta)
    )
}

fn norm_scale(s: &str) -> String {
    s.to_lowercase().replace(' ', "").replace('-', "/")
}

/// `parse_meta`: the model's `KEY: value` lines (empty / n/a / none / unknown
/// dropped; a repeated key keeps its first position), with `SCALE` replaced by
/// the scales written in the path, image names or file list (plus any model
/// suggestion that text backs up). Keys in first-appearance order.
pub fn parse_meta(raw: &str, archive_path: &str, stems: &[String], files: &[String]) -> Vec<(String, String)> {
    let mut meta: Vec<(String, String)> = Vec::new();
    for line in py_splitlines(raw) {
        let Some((key, val)) = line.split_once(':') else { continue };
        let key = py_strip(key).to_uppercase();
        let val = py_strip(val);
        if !val.is_empty() && !["n/a", "none", "unknown", ""].contains(&val.to_lowercase().as_str()) {
            match meta.iter_mut().find(|(k, _)| *k == key) {
                Some(e) => e.1 = val.to_string(),
                None => meta.push((key, val.to_string())),
            }
        }
    }
    let context: Vec<&str> = std::iter::once(archive_path).chain(stems.iter().map(String::as_str)).chain(files.iter().map(String::as_str)).collect();
    let detected = rules::detect_scale(&context);
    let mut have: HashSet<String> = detected.iter().map(|s| norm_scale(s)).collect();
    let mut scales = detected;
    let nctx = norm_scale(&context.join(" "));
    let suggested = meta.iter().find(|(k, _)| k == "SCALE").map(|(_, v)| v.clone()).unwrap_or_default();
    for tok in suggested.split(',') {
        let t = py_strip(tok);
        let n = norm_scale(t);
        if !t.is_empty() && nctx.contains(&n) && !have.contains(&n) {
            scales.push(t.to_string());
            have.insert(n);
        }
    }
    let joined = scales.join(", ");
    match (meta.iter().position(|(k, _)| k == "SCALE"), scales.is_empty()) {
        (Some(i), true) => {
            meta.remove(i);
        }
        (Some(i), false) => meta[i].1 = joined,
        (None, false) => meta.push(("SCALE".into(), joined)),
        (None, true) => {}
    }
    meta
}

const VALID_SOURCES: [&str; 4] = ["patreon", "kickstarter", "myminifactory", "unknown"];

/// `normalize_meta`: parsed metadata as the fields `ai_commit` takes.
pub fn normalize_meta(meta: &[(String, String)]) -> AiFields {
    let get = |k: &str| meta.iter().find(|(key, _)| key == k).map(|(_, v)| py_strip(v).to_string()).unwrap_or_default();
    let source = get("SOURCE").to_lowercase();
    let year: String = get("YEAR").chars().take(4).collect();
    let year = (!year.is_empty() && year.chars().all(|c| c.is_ascii_digit()))
        .then(|| year.parse::<i64>().ok())
        .flatten()
        .filter(|y| (1900..=2100).contains(y));
    AiFields {
        title: get("TITLE"),
        creator: get("CREATOR"),
        sculptor: get("SCULPTOR"),
        collection: get("COLLECTION"),
        subcollection: get("SUBCOLLECTION"),
        scale: get("SCALE"),
        description: get("DESCRIPTION"),
        source: if VALID_SOURCES.contains(&source.as_str()) { source } else { String::new() },
        year,
    }
}

/// Printable file names shown in the prompt, at most.
const FILE_CAP: usize = 60;

/// `_collect_known_meta`: the catalogued metadata, tags and file names as the
/// prompt's hint block entries.
pub fn known_meta(inputs: &AiInputs) -> Vec<(String, String)> {
    let mut meta = inputs.meta.clone();
    if !inputs.tags.is_empty() {
        meta.push(("Tags".into(), inputs.tags.join(", ")));
    }
    let names: Vec<String> = inputs.files.iter().map(|f| MemberPath::new(f).name.clone()).collect();
    if !names.is_empty() {
        let mut shown = names.iter().take(FILE_CAP).cloned().collect::<Vec<_>>().join(", ");
        if names.len() > FILE_CAP {
            shown.push_str(&format!(", …(+{} more)", names.len() - FILE_CAP));
        }
        meta.push(("Files".into(), shown));
    }
    meta
}

/// The prompt's name for an image: its file stem, or `img<id>`.
pub fn image_stem(image_id: i64, filename: Option<&str>) -> String {
    match filename.filter(|f| !f.is_empty()) {
        Some(f) => MemberPath::new(f).stem().to_string(),
        None => format!("img{image_id}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn tags_are_cleaned() {
        assert_eq!(dedupe_words("gothic gothic Gothic horror"), "gothic horror");
        assert_eq!(atomize_tag("ghostly figure in suit with bow tie and gloves"), v(&["ghostly figure", "suit", "bow tie", "gloves"]));
        assert_eq!(split_cumulative_tags(&v(&["spectral", "spectral warrior", "spectral warrior gothic metal", "dark fantasy"])), v(&["spectral", "warrior", "gothic metal", "dark fantasy"]));
        assert_eq!(clean_tags("Batman, batman, Batman with cape, DC Comics; hero", 0), v(&["Batman", "cape", "DC Comics", "hero"]));
        assert_eq!(clean_tags("a, b, c", 2), v(&["a", "b"]));
    }

    #[test]
    fn corrupted_output() {
        assert!(is_corrupted_output("????????? ok"));
        assert!(is_corrupted_output("@@@@@@####"));
        assert!(!is_corrupted_output("batman, cape"));
        assert!(!is_corrupted_output("??"));
    }

    #[test]
    fn metadata_is_parsed_and_scales_come_from_text() {
        let raw = "TITLE: Dark Knight\nCREATOR: n/a\nSCALE: 28mm, 1:35\nSOURCE: Patreon\nYEAR: 2023 (approx)\ntitle: Batman\nnoise";
        let meta = parse_meta(raw, "D:/STL/Studio/Batman 32mm.zip", &v(&["front 75mm"]), &v(&["parts/body 1-35.stl", "x 1:35 base.stl"]));
        assert_eq!(
            meta,
            vec![
                ("TITLE".to_string(), "Batman".to_string()),
                ("SCALE".to_string(), "32 mm, 75 mm, 1/35, 1:35".to_string()),
                ("SOURCE".to_string(), "Patreon".to_string()),
                ("YEAR".to_string(), "2023 (approx)".to_string()),
            ]
        );
        let f = normalize_meta(&meta);
        assert_eq!((f.title.as_str(), f.source.as_str(), f.year, f.creator.as_str()), ("Batman", "patreon", Some(2023), ""));
        // No scale in the text: the model's guess is dropped.
        assert!(parse_meta("SCALE: 28mm", "D:/a/b.zip", &[], &[]).is_empty());
    }

    #[test]
    fn prompts_carry_hints() {
        let meta = vec![("Title".to_string(), "Batman".to_string())];
        let p = build_tags_prompt(&v(&["front", "back"]), "D:/x.zip", &meta);
        assert!(p.starts_with("These 2 images come from"));
        assert!(p.contains("Image file names: \"front\", \"back\". Existing catalogued metadata"));
        assert!(p.contains("- Title: Batman\n"));
        assert!(build_tags_prompt(&[], "D:/x.zip", &[]).contains("Image file names: (none). Use the full path"));
        assert!(build_meta_prompt("D:/x.zip", &[]).ends_with("Output ONLY these 9 lines."));
        assert_eq!(image_stem(3, Some("renders/front.png")), "front");
        assert_eq!(image_stem(3, None), "img3");
    }
}
