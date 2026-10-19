//! Sort files: move archives into a folder tree built from a path pattern
//! (`{collection}/{creator}/{title}`), a port of `app/ui/sort_dialog.py`.
//! Every move is planned first; if one fails, the files already moved go back
//! and the caller leaves the database unchanged.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use rusqlite::types::Value;

use crate::path_pattern;
use crate::storage::browser::SortSource;

/// `str(v).strip()`, or `_unknown` for nothing (`_safe`).
fn safe(v: Option<String>) -> String {
    let s = v.map(|s| s.trim().to_string()).unwrap_or_default();
    if s.is_empty() { "_unknown".into() } else { s }
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

/// The token values `render_path_pattern` substitutes for one model.
pub fn values(s: &SortSource) -> HashMap<String, String> {
    let date = path_pattern::format_release_date(int(&s.release_year), int(&s.release_month));
    let pairs = [
        ("source", s.source.clone()),
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
    pairs.into_iter().map(|(k, v)| (k.to_string(), safe(v))).collect()
}

/// `_sanitise_segment`: unsafe characters become `_`, runs of `_` collapse,
/// and an empty result is `_unknown`.
pub fn sanitise_segment(segment: &str) -> String {
    let mut out = String::new();
    for ch in segment.trim().chars() {
        let c = if r#"\/:*?"<>|"#.contains(ch) { '_' } else { ch };
        if !(c == '_' && out.ends_with('_')) {
            out.push(c);
        }
    }
    let s = out.trim_matches('_');
    if s.is_empty() { "_unknown".into() } else { s.to_string() }
}

/// `_compute_target`: `base` / sanitised folders / sanitised name + the archive's extension.
pub fn target(base: &Path, pattern: &str, source: &SortSource) -> PathBuf {
    let src = source.archive_path.clone().unwrap_or_default();
    let src_path = Path::new(&src);
    let ext = src_path.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    let rendered = path_pattern::render(pattern.trim_matches(['/', '\\']), &values(source));
    let rendered = rendered.replace('\\', "/");
    let parts: Vec<&str> = rendered.split('/').filter(|p| !p.is_empty()).collect();
    let (dirs, stem) = match parts.split_last() {
        Some((last, dirs)) => (dirs.iter().map(|d| sanitise_segment(d)).collect::<Vec<_>>(), sanitise_segment(last)),
        None => (Vec::new(), sanitise_segment(&src_path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default())),
    };
    let mut dir = base.to_path_buf();
    for d in dirs {
        dir.push(d);
    }
    dir.join(format!("{stem}{ext}"))
}

/// One line of the move plan.
#[derive(Debug, Clone, PartialEq)]
pub struct PlanRow {
    pub id: i64,
    /// `OK`, `COLLISION` (the target exists: a free name is used), `MISS`
    /// (the archive is not on disk), `SKIP` (no path) or `ERROR` (no model).
    pub status: &'static str,
    pub from: String,
    pub to: String,
}

impl PlanRow {
    /// Whether the row is moved.
    pub fn moves(&self) -> bool {
        matches!(self.status, "OK" | "COLLISION")
    }
}

/// `_refresh_preview`: the plan for `ids`, in that order.
pub fn preview(sources: &[SortSource], ids: &[i64], base: &Path, pattern: &str) -> Vec<PlanRow> {
    let by_id: HashMap<i64, &SortSource> = sources.iter().map(|s| (s.id, s)).collect();
    ids.iter()
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
            let to = target(base, pattern, s);
            let collision = to.exists() && to != Path::new(&src);
            PlanRow { id, status: if collision { "COLLISION" } else { "OK" }, from: src, to: to.to_string_lossy().into_owned() }
        })
        .collect()
}

/// `_unique_target`: `path`, or the first free `<stem>_<n><ext>` beside it.
fn unique_target(path: &Path) -> PathBuf {
    if !path.exists() {
        return path.to_path_buf();
    }
    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let ext = path.extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default();
    let parent = path.parent().unwrap_or(Path::new(""));
    (1..).map(|n| parent.join(format!("{stem}_{n}{ext}"))).find(|p| !p.exists()).expect("a free name")
}

fn move_file(from: &Path, to: &Path) -> std::io::Result<()> {
    if std::fs::rename(from, to).is_err() {
        // Another drive: copy, then remove.
        std::fs::copy(from, to)?;
        std::fs::remove_file(from)?;
    }
    Ok(())
}

/// `_do_move`: move every planned archive (to a free name), or none. Returns
/// `(model id, new path)` for the database, then removes source folders left
/// empty. On a failure the moved files go back and the error says what
/// happened (including any file that could not be moved back).
pub fn execute(rows: &[PlanRow]) -> Result<Vec<(i64, String)>, String> {
    let mut plan = Vec::new();
    for r in rows.iter().filter(|r| r.moves()) {
        let src = PathBuf::from(&r.from);
        if !src.exists() {
            return Err(format!("source file no longer exists: {}; no files were moved", src.display()));
        }
        plan.push((src, unique_target(Path::new(&r.to)), r.id));
    }
    let mut moved: Vec<(PathBuf, PathBuf)> = Vec::new();
    let mut error = None;
    for (src, dst, _) in &plan {
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
    let mut dirs: Vec<PathBuf> = plan.iter().filter_map(|(s, _, _)| s.parent().map(Path::to_path_buf)).collect();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.components().count()));
    dirs.dedup();
    for d in dirs {
        if std::fs::read_dir(&d).is_ok_and(|mut it| it.next().is_none()) {
            let _ = std::fs::remove_dir(&d);
        }
    }
    Ok(plan.into_iter().map(|(_, dst, id)| (id, dst.to_string_lossy().into_owned())).collect())
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

    #[test]
    fn segments_and_targets() {
        assert_eq!(sanitise_segment(" a:b**c "), "a_b_c");
        assert_eq!(sanitise_segment("___"), "_unknown");
        let s = source(1, "D:/old/Dragon.zip", Some("Red: Dragon"), None);
        let t = target(Path::new("D:/new"), "/{collection}/{creator}/{title} {date}/", &s);
        assert_eq!(t, Path::new("D:/new").join("Fantasy").join("unknown").join("Red_ Dragon 2024.zip"), "as Python: \"_unknown\".strip(\"_\")");
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
        let rows = preview(&sources, &[1, 2, 3], &new, "{collection}/{title}");
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
}
