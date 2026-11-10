//! AI tagger text handling: a port of `app/utils/ai_tagging.py` (tag cleanup,
//! prompts, metadata parsing). The model itself runs in an external
//! `llama-server`; this module only builds what is sent and reads what comes back.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;

use crate::rules::{self, MemberPath};
use crate::storage::model_editor::{AiFields, AiInputs};

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
         SCALE: <ONLY if a scale such as 28mm, 32mm, 1/35 or 54mm is explicitly written in the path, filenames, \
         or existing metadata above. A scale CANNOT be seen in an image — never guess or infer it. Leave empty \
         if it is not stated in text.>\n\
         SOURCE: <one of: patreon, kickstarter, myminifactory — or empty>\n\
         YEAR: <4-digit release year — or empty>\n\
         DESCRIPTION: <one sentence describing the model>\n\
         Output ONLY these 7 lines.",
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
        scale: get("SCALE"),
        description: get("DESCRIPTION"),
        source: if VALID_SOURCES.contains(&source.as_str()) { source } else { String::new() },
        year,
    }
}

/// The editor fields the "AI data" request asks for: the reply's key and what it means.
pub const FIELD_KEYS: [(&str, &str); 12] = [
    ("TITLE", "<model title>"),
    ("CREATOR", "<publisher, brand or author name>"),
    ("TYPE", "<distribution type, such as patreon, kickstarter, myminifactory>"),
    ("DATE", "<release date as YYYY-MM or YYYY>"),
    ("SCULPTOR", "<artist or sculptor name>"),
    ("RELEASE", "<the release or monthly pack it belongs to>"),
    ("COLLECTION", "<series, franchise or collection>"),
    ("SUBCOLLECTION", "<a group within the collection>"),
    ("CATEGORY", "<kind of model, such as bust, figure, terrain, vehicle>"),
    ("SCALE", "<ONLY a scale such as 28mm, 32mm, 1/35 or 75mm written in the path, filenames or metadata — never guessed from images>"),
    ("DESCRIPTION", "<one sentence describing the model>"),
    ("TAGS", "<comma-separated descriptive tags>"),
];

/// Lowercase letters and digits only, for comparing names with paths.
fn squash(s: &str) -> String {
    s.chars().filter(|c| c.is_alphanumeric()).flat_map(char::to_lowercase).collect()
}

/// The names of a library list worth showing the model: all of them when
/// there are at most `cap`, else those written in `context` (the path and
/// file names, compared without case, spaces or punctuation), at most `cap`.
pub fn relevant_names<'a>(names: &'a [String], context: &str, cap: usize) -> Vec<&'a str> {
    if names.len() <= cap {
        return names.iter().map(String::as_str).collect();
    }
    let ctx = squash(context);
    names.iter().filter(|n| squash(n).chars().count() >= 3 && ctx.contains(&squash(n))).take(cap).map(String::as_str).collect()
}

/// The "AI data" prompt: every editor field in `KEY: value` lines, with the
/// library's known names (`vocab`: a field key and its names) to reuse.
pub fn build_fields_prompt(archive_path: &str, meta: &[(String, String)], vocab: &[(&str, Vec<&str>)]) -> String {
    let fields: Vec<String> = FIELD_KEYS.iter().map(|(k, d)| format!("{k}: {d}")).collect();
    let known: Vec<String> = vocab.iter().filter(|(_, v)| !v.is_empty()).map(|(k, v)| format!("- {k}: {}", v.join(" | "))).collect();
    let known = if known.is_empty() {
        String::new()
    } else {
        format!(
            "Values already used in this library (reuse the exact spelling when one fits; a new value is fine \
             when none does):\n{}\n",
            known.join("\n")
        )
    };
    format!(
        "This is a 3D printable miniature model. Full archive path: \"{archive_path}\". {}{known}The archive may \
         contain several images; base the metadata on the main miniature. The folder structure usually encodes \
         creator, sculptor, release, collection and similar fields. Using the images, the full path, the file \
         names and the hints above, fill in as many fields as you can, in this EXACT format (one line per field, \
         leave the value empty if unknown, no extra text):\n{}\nOutput ONLY these {} lines.",
        format_known_meta(meta),
        fields.join("\n"),
        FIELD_KEYS.len()
    )
}

/// `value` in a library list's spelling when it matches a name there without
/// case, spaces or punctuation; else as given.
pub fn snap<'a>(value: &'a str, names: &'a [String]) -> &'a str {
    let s = squash(value);
    names.iter().find(|n| !s.is_empty() && squash(n) == s).map_or(value, String::as_str)
}

/// The "AI data" reply as field values: [`parse_meta`] (so `SCALE` only holds
/// scales written in the text), names snapped to the library's spelling
/// (`vocab`: a field key and its names), a `DATE` that is not `YYYY[-MM]`
/// dropped and `TAGS` cleaned. Keys in [`FIELD_KEYS`] order.
pub fn parse_fields(raw: &str, archive_path: &str, stems: &[String], files: &[String], vocab: &[(&str, &[String])]) -> Vec<(String, String)> {
    let meta = parse_meta(raw, archive_path, stems, files);
    let mut out = Vec::new();
    for (key, _) in FIELD_KEYS {
        let Some((_, v)) = meta.iter().find(|(k, _)| k == key) else { continue };
        let v = match key {
            "DATE" => match crate::path_pattern::parse_release_date(v) {
                (Some(y), m) if (1900..=2100).contains(&y) && m.is_none_or(|m| (1..=12).contains(&m)) => {
                    crate::path_pattern::format_release_date(Some(y), m)
                }
                _ => continue,
            },
            "TAGS" => clean_tags(v, 0).join(", "),
            "SCALE" => v.split(',').map(|s| py_strip(s).to_string()).filter(|s| !s.is_empty()).collect::<Vec<_>>().join(", "),
            _ => vocab.iter().find(|(k, _)| *k == key).map_or(v.as_str(), |(_, names)| snap(v, names)).to_string(),
        };
        if !v.is_empty() {
            out.push((key.to_string(), v));
        }
    }
    out
}

/// The tokens a path pattern inferred by the model may use (besides `*`).
pub const PATTERN_TOKENS: [&str; 9] = ["creator", "sculptor", "release", "date", "collection", "subcollection", "title", "source", "sequence"];

/// At most `n` of `paths`, spread evenly over the (sorted) list so several folders show.
pub fn spread<T: Clone>(items: &[T], n: usize) -> Vec<T> {
    if items.len() <= n {
        return items.to_vec();
    }
    (0..n).map(|i| items[i * items.len() / n].clone()).collect()
}

/// The prompt that asks for a folder's path pattern from sample archive paths
/// (relative to the folder) and the library's known names (`vocab`: a token and its names).
pub fn build_pattern_prompt(samples: &[String], vocab: &[(&str, Vec<&str>)]) -> String {
    let known: Vec<String> = vocab.iter().filter(|(_, v)| !v.is_empty()).map(|(k, v)| format!("- {{{k}}}: {}", v.join(" | "))).collect();
    let known = if known.is_empty() {
        String::new()
    } else {
        format!("Names already catalogued in this library, which help to tell what each folder level holds:\n{}\n", known.join("\n"))
    };
    format!(
        "These are archive paths of 3D printable models, relative to a library folder:\n{}\n{known}Write ONE path \
         pattern that reads metadata from these paths. A pattern is the path's folder levels and file name joined \
         with '/', where a placeholder captures a value: {}. Use '*' for a folder level that holds nothing useful. \
         Text between placeholders inside one level is matched literally (for example '{{date}} {{release}}' for \
         '2023-05 Orcs'). The pattern is matched against the END of each path, so it does not have to cover \
         levels above the useful ones. The last level is the archive file name: use {{title}} for it (its extension \
         is dropped). {{date}} is a YYYY-MM or YYYY date. Choose the pattern that fits most paths. Output ONLY the \
         pattern, on one line, for example: {{creator}}/{{release}}/{{title}}",
        samples.iter().map(|s| format!("- {s}")).collect::<Vec<_>>().join("\n"),
        PATTERN_TOKENS.iter().map(|t| format!("{{{t}}}")).collect::<Vec<_>>().join(", ")
    )
}

/// The pattern in the model's reply: the first line with a placeholder,
/// without quotes, backticks or a `Pattern:` label, when it compiles and only
/// uses [`PATTERN_TOKENS`].
pub fn parse_pattern(raw: &str) -> Option<String> {
    static TOKEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\{(\w+)(?::\d+)?\}").expect("valid pattern"));
    let line = py_splitlines(raw).into_iter().map(py_strip).find(|l| l.contains('{'))?;
    let line = line.split_once(':').filter(|(k, _)| !k.contains('{')).map_or(line, |(_, v)| v);
    let p = line.trim_matches(|c: char| py_isspace(c) || "`\"'".contains(c)).replace('\\', "/");
    let tokens_ok = TOKEN.captures_iter(&p).all(|c| PATTERN_TOKENS.contains(&c[1].to_lowercase().as_str()));
    (tokens_ok && crate::path_pattern::compile(&p).is_some()).then_some(p)
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
    fn editor_fields_are_asked_and_parsed() {
        let creators = v(&["Loot Studios", "Titan Forge", "Abc", "Xyz"]);
        assert_eq!(relevant_names(&creators, "D:/STL/loot-studios/2023-05/Orc.zip", 3), vec!["Loot Studios"]);
        assert_eq!(relevant_names(&creators, "anything", 4).len(), 4, "short lists go in whole");
        let p = build_fields_prompt("D:/x.zip", &[], &[("CREATOR", vec!["Loot Studios"]), ("SCULPTOR", vec![])]);
        assert!(p.contains("- CREATOR: Loot Studios\n") && !p.contains("- SCULPTOR"), "{p}");
        assert!(p.contains("\nCATEGORY: <") && p.ends_with("Output ONLY these 12 lines."), "{p}");

        let raw = "TITLE: Orc Warlord\nCREATOR: loot studios\nDATE: 2023-5\nSCALE: 75mm\nCATEGORY: Bust\nTAGS: orc, orc with axe\nYEAR: 2020\nRELEASE: none";
        let vocab: [(&str, &[String]); 1] = [("CREATOR", &creators)];
        let f = parse_fields(raw, "D:/STL/Loot/Orc 32mm.zip", &[], &[], &vocab);
        let s = |k: &str, val: &str| (k.to_string(), val.to_string());
        assert_eq!(
            f,
            vec![s("TITLE", "Orc Warlord"), s("CREATOR", "Loot Studios"), s("DATE", "2023-05"), s("CATEGORY", "Bust"), s("SCALE", "32 mm"), s("TAGS", "orc, axe")]
        );
        assert!(parse_fields("DATE: 1850\nTITLE: x", "a.zip", &[], &[], &[]).iter().all(|(k, _)| k != "DATE"));
    }

    #[test]
    fn path_patterns_are_asked_and_parsed() {
        assert_eq!(spread(&[1, 2, 3, 4, 5, 6], 3), vec![1, 3, 5]);
        assert_eq!(spread(&[1, 2], 3), vec![1, 2]);
        let p = build_pattern_prompt(&v(&["Loot/2023-05 Orcs/Orc.zip"]), &[("creator", vec!["Loot"]), ("sculptor", vec![])]);
        assert!(p.contains("- Loot/2023-05 Orcs/Orc.zip\n") && p.contains("\n- {creator}: Loot\n") && !p.contains("- {sculptor}"), "{p}");
        assert_eq!(parse_pattern("Pattern: `{creator}/{date} {release}/{title}`").as_deref(), Some("{creator}/{date} {release}/{title}"));
        assert_eq!(parse_pattern("Sure!\n\"*\\{Creator}/{title}\"").as_deref(), Some("*/{Creator}/{title}"));
        assert_eq!(parse_pattern("{author}/{title}"), None, "only the offered tokens");
        assert_eq!(parse_pattern("no pattern here"), None);
    }

    #[test]
    fn prompts_carry_hints() {
        let meta = vec![("Title".to_string(), "Batman".to_string())];
        let p = build_tags_prompt(&v(&["front", "back"]), "D:/x.zip", &meta);
        assert!(p.starts_with("These 2 images come from"));
        assert!(p.contains("Image file names: \"front\", \"back\". Existing catalogued metadata"));
        assert!(p.contains("- Title: Batman\n"));
        assert!(build_tags_prompt(&[], "D:/x.zip", &[]).contains("Image file names: (none). Use the full path"));
        assert!(build_meta_prompt("D:/x.zip", &[]).ends_with("Output ONLY these 7 lines."));
        assert_eq!(image_stem(3, Some("renders/front.png")), "front");
        assert_eq!(image_stem(3, None), "img3");
    }
}
