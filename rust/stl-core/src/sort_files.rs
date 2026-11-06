//! Sort files: move archives into a folder tree built from a path pattern
//! (`{collection}/{creator}/{title}`), a port of `app/ui/sort_dialog.py`.
//! Every move is planned first; if one fails, the files already moved go back
//! and the caller leaves the database unchanged. When every archive of a folder
//! goes to one folder, the folder's other files (pictures, links, `info.json`)
//! go with them.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use rusqlite::types::Value;

use crate::path_pattern;
use crate::storage::browser::SortSource;
use crate::storage::sidecar::METADATA_FILENAME;

/// What a field without a value becomes unless the user chose otherwise.
pub const UNKNOWN: &str = "_unknown";

/// `str(v).strip()`, or `missing` for nothing (`_safe`).
fn safe(v: Option<String>, missing: &str) -> String {
    let s = v.map(|s| s.trim().to_string()).unwrap_or_default();
    if s.is_empty() { missing.into() } else { s }
}

fn value_text(v: &Value) -> Option<String> {
    match v {
        Value::Null => None,
        Value::Integer(i) => Some(i.to_string()),
        Value::Real(f) => Some(crate::pyjson::float_repr(*f)),
        Value::Text(s) => Some(s.clone()),
        Value::Blob(_) => None,
    }
}

fn int(v: &Value) -> Option<i64> {
    match v {
        Value::Integer(i) => Some(*i),
        _ => None,
    }
}

/// The token values `render_path_pattern` substitutes for one model, `missing`
/// for the fields it has no value in.
pub fn values(s: &SortSource, missing: &str) -> HashMap<String, String> {
    let date = path_pattern::format_release_date(int(&s.release_year), int(&s.release_month));
    let pairs = [
        ("source", s.source.clone()),
        ("type", s.source.clone()),
        ("creator", s.creator.clone()),
        ("author", s.creator.clone()),
        ("title", s.title.clone()),
        ("name", s.title.clone()),
        ("date", Some(date.clone())),
        ("month", Some(date)),
        ("url", s.url.clone()),
        ("sculptor", s.sculptor.clone()),
        ("collection", s.collection.clone()),
        ("category", s.collection.clone()),
        ("subcollection", s.subcollection.clone()),
        ("subcategory", s.subcollection.clone()),
        ("sequence", value_text(&s.sequence)),
        ("release", s.release.clone()),
    ];
    pairs.into_iter().map(|(k, v)| (k.to_string(), safe(v, missing))).collect()
}

/// `_sanitise_segment`: unsafe characters become `_` and runs of `_` collapse.
/// Nothing left, or only dots (`..` would leave the target folder), gives `""`.
pub fn sanitise_segment(segment: &str) -> String {
    let mut out = String::new();
    for ch in segment.trim().chars() {
        let c = if r#"\/:*?"<>|"#.contains(ch) { '_' } else { ch };
        if !(c == '_' && out.ends_with('_')) {
            out.push(c);
        }
    }
    let s = out.trim_matches('_');
    if s.chars().all(|c| c == '.') { String::new() } else { s.to_string() }
}

/// `_compute_target`: `base` / sanitised folders / sanitised name + the archive's
/// extension. Fields without a value become `missing`; when that is empty, a
/// folder level with nothing in it is left out, and a file name with nothing in
/// it is the archive's own.
pub fn target(base: &Path, pattern: &str, source: &SortSource, missing: &str) -> PathBuf {
    let src = source.archive_path.clone().unwrap_or_default();
    let src_path = Path::new(&src);
    let ext = src_path.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    let rendered = path_pattern::render(pattern.trim_matches(['/', '\\']), &values(source, missing), missing);
    let rendered = rendered.replace('\\', "/");
    let fill = sanitise_segment(missing);
    let mut parts: Vec<&str> = rendered.split('/').collect();
    let name = parts.pop().map(sanitise_segment).unwrap_or_default();
    let mut dir = base.to_path_buf();
    for d in parts.into_iter().filter(|d| !d.is_empty()) {
        let d = sanitise_segment(d);
        let d = if d.is_empty() { fill.clone() } else { d };
        if !d.is_empty() {
            dir.push(d);
        }
    }
    let own = sanitise_segment(&src_path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default());
    let stem = [name, own, fill].into_iter().find(|s| !s.is_empty()).unwrap_or_else(|| UNKNOWN.into());
    dir.join(format!("{stem}{ext}"))
}

/// One line of the move plan.
#[derive(Debug, Clone, PartialEq)]
pub struct PlanRow {
    /// The model (0 for a file that goes with its folder's archives).
    pub id: i64,
    /// `OK`, `COLLISION` (the target exists: a free name is used), `MISS`
    /// (the archive is not on disk), `SKIP` (no path), `ERROR` (no model) or
    /// `FILE` (another file of a folder whose archives all go to one folder).
    pub status: &'static str,
    pub from: String,
    pub to: String,
}

impl PlanRow {
    /// Whether the row is moved.
    pub fn moves(&self) -> bool {
        matches!(self.status, "OK" | "COLLISION" | "FILE")
    }
}

fn is_archive(name: &str) -> bool {
    let n = name.to_lowercase();
    [".zip", ".7z", ".rar"].iter().any(|e| n.ends_with(e))
}

/// `FILE` rows: for each source folder whose archives (on disk, directly in it)
/// all move, and all to one other folder, its other files go there too.
/// Subfolders and the sidecar stay (the sidecar is rewritten where the archives go).
fn companions(rows: &[PlanRow]) -> Vec<PlanRow> {
    let mut by_dir: HashMap<PathBuf, (Vec<String>, Vec<PathBuf>)> = HashMap::new();
    for r in rows.iter().filter(|r| r.moves()) {
        let from = Path::new(&r.from);
        let (Some(dir), Some(name)) = (from.parent(), from.file_name()) else { continue };
        let entry = by_dir.entry(dir.to_path_buf()).or_default();
        entry.0.push(name.to_string_lossy().to_lowercase());
        entry.1.extend(Path::new(&r.to).parent().map(Path::to_path_buf));
    }
    let mut out = Vec::new();
    let mut dirs: Vec<_> = by_dir.into_iter().collect();
    dirs.sort_by(|a, b| a.0.cmp(&b.0));
    for (dir, (moving, mut targets)) in dirs {
        targets.sort();
        targets.dedup();
        let [target] = targets.as_slice() else { continue };
        if *target == dir {
            continue;
        }
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        let mut files = Vec::new();
        let mut all_moving = true;
        for e in entries.flatten() {
            if !e.file_type().is_ok_and(|t| t.is_file()) {
                continue;
            }
            let name = e.file_name().to_string_lossy().into_owned();
            if name.eq_ignore_ascii_case(METADATA_FILENAME) {
                continue;
            }
            if is_archive(&name) {
                all_moving &= moving.contains(&name.to_lowercase());
            } else {
                files.push(name);
            }
        }
        if !all_moving {
            continue;
        }
        files.sort_by_key(|n| n.to_lowercase());
        out.extend(files.into_iter().map(|name| PlanRow {
            id: 0,
            status: "FILE",
            from: dir.join(&name).to_string_lossy().into_owned(),
            to: target.join(&name).to_string_lossy().into_owned(),
        }));
    }
    out
}

/// `_refresh_preview`: the plan for `ids`, in that order, then the [`companions`].
pub fn preview(sources: &[SortSource], ids: &[i64], base: &Path, pattern: &str, missing: &str) -> Vec<PlanRow> {
    let by_id: HashMap<i64, &SortSource> = sources.iter().map(|s| (s.id, s)).collect();
    let mut rows: Vec<PlanRow> = ids
        .iter()
        .map(|&id| {
            let Some(s) = by_id.get(&id) else {
                return PlanRow { id, status: "ERROR", from: format!("model id={id}"), to: "not found in database".into() };
            };
            let Some(src) = s.archive_path.clone().filter(|p| !p.is_empty()) else {
                return PlanRow { id, status: "SKIP", from: s.title.clone().unwrap_or_else(|| id.to_string()), to: "no archive_path set".into() };
            };
            if !Path::new(&src).exists() {
                return PlanRow { id, status: "MISS", from: src, to: "source file not found on disk".into() };
            }
            let to = target(base, pattern, s, missing);
            let collision = to.exists() && to != Path::new(&src);
            PlanRow { id, status: if collision { "COLLISION" } else { "OK" }, from: src, to: to.to_string_lossy().into_owned() }
        })
        .collect();
    rows.extend(companions(&rows));
    // Two rows of the plan going to one place collide as well.
    let mut planned = HashSet::new();
    for r in rows.iter_mut().filter(|r| r.moves()) {
        if !planned.insert(path_key(Path::new(&r.to))) && r.status == "OK" {
            r.status = "COLLISION";
        }
    }
    rows
}

/// How two paths of a plan compare (letter case aside on Windows).
fn path_key(path: &Path) -> String {
    crate::relpath::path_key(&path.to_string_lossy())
}

/// `path`, or the first `<stem>_<n><ext>` beside it, free on disk and not in
/// `taken` (the destinations already planned: nothing has moved yet, so the
/// disk alone would give two rows the same name, and a rename replaces its target).
fn unique_target(path: &Path, taken: &HashSet<String>) -> PathBuf {
    let free = |p: &Path| !p.exists() && !taken.contains(&path_key(p));
    if free(path) {
        return path.to_path_buf();
    }
    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let ext = path.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    let parent = path.parent().unwrap_or(Path::new(""));
    (1..).map(|n| parent.join(format!("{stem}_{n}{ext}"))).find(|p| free(p)).expect("a free name")
}

fn move_file(from: &Path, to: &Path) -> std::io::Result<()> {
    if std::fs::rename(from, to).is_err() {
        // Another drive: copy, then remove.
        std::fs::copy(from, to)?;
        std::fs::remove_file(from)?;
    }
    Ok(())
}

/// `_do_move`: move every planned file (to a free name), or none. Returns
/// `(model id, new path)` of the archives for the database, then removes source
/// folders left empty (or holding only their sidecar, whose models all left). On a failure the moved files go back and the error says what
/// happened (including any file that could not be moved back).
pub fn execute(rows: &[PlanRow]) -> Result<Vec<(i64, String)>, String> {
    let mut plan = Vec::new();
    let mut taken = HashSet::new();
    for r in rows.iter().filter(|r| r.moves()) {
        let src = PathBuf::from(&r.from);
        if !src.exists() {
            return Err(format!("source file no longer exists: {}; no files were moved", src.display()));
        }
        let to = Path::new(&r.to);
        // An archive already where the pattern puts it stays there.
        let dst = if path_key(&src) == path_key(to) { src.clone() } else { unique_target(to, &taken) };
        taken.insert(path_key(&dst));
        plan.push((src, dst, r.id));
    }
    let mut moved: Vec<(PathBuf, PathBuf)> = Vec::new();
    let mut error = None;
    for (src, dst, _) in plan.iter().filter(|(src, dst, _)| src != dst) {
        if let Some(dir) = dst.parent()
            && let Err(e) = std::fs::create_dir_all(dir)
        {
            error = Some(format!("cannot create {}: {e}", dir.display()));
            break;
        }
        match move_file(src, dst) {
            Ok(()) => moved.push((src.clone(), dst.clone())),
            Err(e) => {
                error = Some(format!("move failed {} → {}: {e}", src.display(), dst.display()));
                break;
            }
        }
    }
    if let Some(e) = error {
        let failed: Vec<String> = moved
            .iter()
            .rev()
            .filter_map(|(src, dst)| move_file(dst, src).err().map(|x| format!("{} → {}: {x}", dst.display(), src.display())))
            .collect();
        return Err(if failed.is_empty() {
            format!("{e}; every move was rolled back and nothing changed")
        } else {
            format!("{e}; these files could not be moved back: {}", failed.join("; "))
        });
    }
    let mut dirs: Vec<PathBuf> = plan.iter().filter(|(s, d, _)| s != d).filter_map(|(s, _, _)| s.parent().map(Path::to_path_buf)).collect();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.components().count()));
    dirs.dedup();
    for d in dirs {
        let names: Vec<String> = match std::fs::read_dir(&d) {
            Ok(it) => it.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect(),
            Err(_) => continue,
        };
        if let [only] = names.as_slice()
            && only.eq_ignore_ascii_case(METADATA_FILENAME)
        {
            let _ = std::fs::remove_file(d.join(only));
        } else if !names.is_empty() {
            continue;
        }
        let _ = std::fs::remove_dir(&d);
    }
    Ok(plan.into_iter().filter(|(_, _, id)| *id > 0).map(|(_, dst, id)| (id, dst.to_string_lossy().into_owned())).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn source(id: i64, path: &str, title: Option<&str>, creator: Option<&str>) -> SortSource {
        SortSource {
            id,
            archive_path: Some(path.to_string()),
            title: title.map(str::to_string),
            source: None,
            creator: creator.map(str::to_string),
            release_year: Value::Integer(2024),
            release_month: Value::Null,
            url: None,
            sequence: Value::Null,
            sculptor: None,
            collection: Some("Fantasy".into()),
            subcollection: None,
            release: None,
        }
    }

    /// `name` in `dir`, holding `text`.
    fn file(dir: &Path, name: &str, text: &str) -> String {
        std::fs::create_dir_all(dir).unwrap();
        let p = dir.join(name);
        std::fs::write(&p, text).unwrap();
        p.to_string_lossy().into_owned()
    }

    fn contents(dir: &Path) -> Vec<String> {
        let mut v: Vec<String> = std::fs::read_dir(dir).unwrap().flatten().filter(|e| e.path().is_file()).map(|e| std::fs::read_to_string(e.path()).unwrap()).collect();
        v.sort();
        v
    }

    #[test]
    fn two_archives_with_one_destination_are_both_kept() {
        let dir = tempfile::tempdir().unwrap();
        let (a, b) = (file(&dir.path().join("a"), "Dragon.zip", "first"), file(&dir.path().join("b"), "Dragon.zip", "second"));
        let base = dir.path().join("sorted");
        let sources = [source(1, &a, Some("Dragon"), Some("Sanix")), source(2, &b, Some("Dragon"), Some("Sanix"))];
        let rows = preview(&sources, &[1, 2], &base, "{title}", UNKNOWN);
        assert_eq!(rows.iter().map(|r| r.status).collect::<Vec<_>>(), ["OK", "COLLISION"], "the plan shows it");
        let moved = execute(&rows).unwrap();
        assert_eq!(contents(&base), ["first", "second"]);
        assert_eq!(moved[0].1, base.join("Dragon.zip").to_string_lossy());
        assert_eq!(moved[1].1, base.join("Dragon_1.zip").to_string_lossy(), "the database gets the name used");
    }

    #[test]
    fn folders_sorted_together_keep_their_other_files() {
        let dir = tempfile::tempdir().unwrap();
        let (da, db) = (dir.path().join("a"), dir.path().join("b"));
        let (one, two) = (file(&da, "One.zip", "one"), file(&db, "Two.zip", "two"));
        file(&da, "cover.jpg", "cover of a");
        file(&db, "cover.jpg", "cover of b");
        let base = dir.path().join("sorted");
        let sources = [source(1, &one, Some("One"), Some("Studio")), source(2, &two, Some("Two"), Some("Studio"))];
        execute(&preview(&sources, &[1, 2], &base, "{creator}/{title}", UNKNOWN)).unwrap();
        assert_eq!(contents(&base.join("Studio")), ["cover of a", "cover of b", "one", "two"]);
    }

    #[test]
    fn an_archive_already_in_place_stays() {
        let dir = tempfile::tempdir().unwrap();
        let base = dir.path().join("sorted");
        let a = file(&base, "Dragon.zip", "first");
        let rows = preview(&[source(1, &a, Some("Dragon"), None)], &[1], &base, "{title}", UNKNOWN);
        assert_eq!(execute(&rows).unwrap(), vec![(1, a)]);
        assert_eq!(std::fs::read_dir(&base).unwrap().count(), 1, "not renamed to Dragon_1.zip");
    }

    #[test]
    fn segments_and_targets() {
        assert_eq!(sanitise_segment(" a:b**c "), "a_b_c");
        assert_eq!(sanitise_segment("___"), "");
        assert_eq!(sanitise_segment(" .. "), "");
        let s = source(1, "D:/old/Dragon.zip", Some("Red: Dragon"), None);
        let t = target(Path::new("D:/new"), "/{collection}/{creator}/{title} {date}/", &s, UNKNOWN);
        assert_eq!(t, Path::new("D:/new").join("Fantasy").join("unknown").join("Red_ Dragon 2024.zip"), "as Python: \"_unknown\".strip(\"_\")");
        let s = SortSource { source: Some("Patreon".into()), ..source(1, "D:/old/Dragon.zip", Some("Dragon"), Some("Sanix")) };
        let t = target(Path::new("D:/new"), "{creator}/{Type}/{date} - {title}", &s, UNKNOWN);
        assert_eq!(t, Path::new("D:/new").join("Sanix").join("Patreon").join("2024 - Dragon.zip"), "{{type}} is {{source}}, any case");
    }

    #[test]
    fn first_characters_of_a_field() {
        let base = Path::new("D:/new");
        let s = source(1, "D:/old/Dragon.zip", Some("Dragon"), Some("Sanix"));
        let to = |pattern| target(base, pattern, &s, UNKNOWN);
        assert_eq!(to("{title:1}/{author:4}/{title}"), base.join("D").join("Sani").join("Dragon.zip"));
        assert_eq!(to("{Title:3}"), base.join("Dra.zip"));
        assert_eq!(to("{author:40}/{title}"), base.join("Sanix").join("Dragon.zip"), "shorter than asked");
        let s = source(1, "D:/old/Dragon.zip", Some("Éclair"), None);
        assert_eq!(target(base, "{title:1}/{title}", &s, UNKNOWN), base.join("É").join("Éclair.zip"), "characters, not bytes");
    }

    #[test]
    fn missing_fields() {
        let base = Path::new("D:/new");
        let s = source(1, "D:/old/Dragon.zip", Some("Red"), None);
        let to = |pattern, missing| target(base, pattern, &s, missing);
        assert_eq!(to("{collection}/{creator}/{title}", "N/A"), base.join("Fantasy").join("N_A").join("Red.zip"));
        assert_eq!(to("{collection}/{creator}/{title}", ""), base.join("Fantasy").join("Red.zip"), "no folder level");
        assert_eq!(to("{nope}/{title}", "none"), base.join("none").join("Red.zip"), "a token nothing fills");
        assert_eq!(to("{nope}/{title}", ""), base.join("Red.zip"));
        assert_eq!(to("{title} {creator}", ""), base.join("Red.zip"));
        // A file name with nothing in it is the archive's, not its folder's.
        let s = source(1, "D:/old/Dragon.zip", None, Some("Sanix"));
        assert_eq!(target(base, "{creator}/{title}", &s, ""), base.join("Sanix").join("Dragon.zip"));
        assert_eq!(target(base, "{title}", &s, "none"), base.join("none.zip"));
    }

    #[test]
    fn stays_in_the_target_folder() {
        let base = Path::new("D:/new");
        let s = source(1, "D:/old/Dragon.zip", Some("Red"), None);
        assert_eq!(target(base, "../../{title}", &s, ""), base.join("Red.zip"));
        assert_eq!(target(base, "{creator}/{title}", &s, ".."), base.join("Red.zip"));
        let s = source(1, "D:/old/Dragon.zip", Some(".."), None);
        assert_eq!(target(base, "{title}/x", &s, ""), base.join("x.zip"));
    }

    #[test]
    fn moves_and_rolls_back() {
        let dir = tempfile::tempdir().unwrap();
        let old = dir.path().join("old");
        std::fs::create_dir_all(&old).unwrap();
        for n in ["a.zip", "b.zip"] {
            std::fs::write(old.join(n), n).unwrap();
        }
        let new = dir.path().join("new");
        std::fs::create_dir_all(new.join("Fantasy")).unwrap();
        std::fs::write(new.join("Fantasy").join("Same.zip"), "taken").unwrap();
        let sources = vec![
            source(1, &old.join("a.zip").to_string_lossy(), Some("Same"), None),
            source(2, &old.join("b.zip").to_string_lossy(), Some("Other"), None),
        ];
        let rows = preview(&sources, &[1, 2, 3], &new, "{collection}/{title}", UNKNOWN);
        assert_eq!(rows.iter().map(|r| r.status).collect::<Vec<_>>(), ["COLLISION", "OK", "ERROR"]);
        let done = execute(&rows).unwrap();
        assert_eq!(done[0].1, new.join("Fantasy").join("Same_1.zip").to_string_lossy());
        assert_eq!(std::fs::read_to_string(new.join("Fantasy").join("Other.zip")).unwrap(), "b.zip");
        assert!(!old.exists(), "the emptied source folder is removed");

        // A failing move (its source vanished after the preview) moves nothing.
        std::fs::create_dir_all(&old).unwrap();
        std::fs::write(old.join("c.zip"), "c").unwrap();
        let rows = vec![
            PlanRow { id: 4, status: "OK", from: old.join("c.zip").to_string_lossy().into(), to: new.join("c.zip").to_string_lossy().into() },
            PlanRow { id: 5, status: "OK", from: old.join("gone.zip").to_string_lossy().into(), to: new.join("g.zip").to_string_lossy().into() },
        ];
        assert!(execute(&rows).unwrap_err().contains("no files were moved"));
        assert!(old.join("c.zip").is_file());
    }

    #[test]
    fn a_folders_other_files_go_with_its_archives() {
        let dir = tempfile::tempdir().unwrap();
        let old = dir.path().join("Oola");
        std::fs::create_dir_all(old.join("extra")).unwrap();
        for n in ["oola.zip", "cover.jpg", "oola.url", "info.json", "METADATA.7z", "extra/notes.txt"] {
            std::fs::write(old.join(n), n).unwrap();
        }
        let mixed = dir.path().join("Mixed");
        std::fs::create_dir_all(&mixed).unwrap();
        for n in ["a.zip", "b.zip", "cover.jpg"] {
            std::fs::write(mixed.join(n), n).unwrap();
        }
        let new = dir.path().join("new");
        let sources = vec![
            source(1, &old.join("oola.zip").to_string_lossy(), Some("Oola"), None),
            source(2, &mixed.join("a.zip").to_string_lossy(), Some("A"), None),
        ];
        let rows = preview(&sources, &[1, 2], &new, "{collection}/{title}", UNKNOWN);
        let files: Vec<(&str, String)> = rows.iter().filter(|r| r.status == "FILE").map(|r| (r.status, r.to.clone())).collect();
        let target = new.join("Fantasy");
        assert_eq!(
            files.iter().map(|(_, to)| to.clone()).collect::<Vec<_>>(),
            ["cover.jpg", "info.json", "oola.url"].map(|n| target.join(n).to_string_lossy().into_owned()),
            "Mixed keeps its cover: b.zip stays there; the sidecar and subfolders stay"
        );
        let done = execute(&rows).unwrap();
        assert_eq!(done.iter().map(|(id, _)| *id).collect::<Vec<_>>(), [1, 2], "only archives reach the database");
        assert_eq!(std::fs::read_to_string(target.join("info.json")).unwrap(), "info.json");
        assert!(!old.join("cover.jpg").exists() && old.join("extra").join("notes.txt").is_file());
        assert!(mixed.join("cover.jpg").is_file());

        // A folder left with only its sidecar is removed.
        std::fs::remove_dir_all(old.join("extra")).unwrap();
        let both = [source(3, &target.join("Oola.zip").to_string_lossy(), Some("Oola"), None), source(5, &target.join("A.zip").to_string_lossy(), Some("A"), None)];
        let rows = preview(&both, &[3, 5], &dir.path().join("again"), "{title}", UNKNOWN);
        execute(&rows).unwrap();
        assert!(dir.path().join("again").join("info.json").is_file());
        assert!(!target.exists(), "emptied");
        std::fs::write(old.join("x.zip"), "x").unwrap();
        let rows = preview(&[source(4, &old.join("x.zip").to_string_lossy(), Some("X"), None)], &[4], &dir.path().join("third"), "{title}", UNKNOWN);
        execute(&rows).unwrap();
        assert!(!old.exists(), "a folder holding only its sidecar goes too");
    }
}
