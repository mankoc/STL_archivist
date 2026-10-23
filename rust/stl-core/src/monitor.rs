//! Monitored folders: whether crawling a folder would find anything, from file
//! sizes and dates alone (no archive is opened), so a server can check often
//! and crawl only when something changed. "Changed" is what a crawl treats as
//! changed: a new path, or a known one whose size or mtime differs from the
//! record's; "gone" is a record under the folder, not yet missing, whose file
//! no longer exists (the crawl marks it missing).

use std::collections::HashMap;
use std::path::Path;

use rusqlite::{Connection, Result};

use crate::storage::crawl;
use crate::winpath::WinPath;

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct Changes {
    pub new: usize,
    pub modified: usize,
    pub gone: usize,
}

impl Changes {
    pub fn any(&self) -> bool {
        self.new + self.modified + self.gone > 0
    }
}

/// `LIKE` pattern for paths under `folder` (`/` form, `!` escapes).
fn under(folder: &str) -> String {
    let posix = WinPath::new(folder).as_posix();
    let escaped: String = posix.chars().flat_map(|c| if matches!(c, '%' | '_' | '!') { vec!['!', c] } else { vec![c] }).collect();
    format!("{}/%", escaped.trim_end_matches('/'))
}

/// What a crawl of `folder` (`recurse` as the crawl would) would find.
pub fn changes(conn: &Connection, folder: &str, recurse: bool) -> Result<Changes> {
    let files = crate::crawler::list_archives(folder, recurse);
    let mut out = Changes::default();
    let known: HashMap<String, (Option<i64>, Option<i64>)> =
        crawl::existing(conn, &files)?.into_iter().map(|(p, _, size, mtime)| (p, (size, mtime))).collect();
    for f in &files {
        match known.get(f) {
            None => out.new += 1,
            Some(&(size, mtime)) => {
                if crawl::stat(f).is_some_and(|(s, m)| Some(s) != size || Some(m) != mtime) {
                    out.modified += 1;
                }
            }
        }
    }
    let mut st = conn.prepare(
        "SELECT archive_path FROM models WHERE NOT coalesce(is_missing, 0) AND archive_path IS NOT NULL \
         AND replace(archive_path, '\\', '/') LIKE ?1 ESCAPE '!'",
    )?;
    let recorded: Vec<String> = st.query_map([under(folder)], |r| r.get(0))?.collect::<Result<_>>()?;
    out.gone = recorded.iter().filter(|p| !Path::new(p).exists()).count();
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::{Vectors, migrate, open};

    #[test]
    fn finds_new_modified_and_gone_archives() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("lib_100%");
        std::fs::create_dir_all(root.join("sub")).unwrap();
        let path = |n: &str| crate::winpath::native(&root.join(n).to_string_lossy());
        std::fs::write(root.join("a.zip"), b"aaaa").unwrap();
        std::fs::write(root.join("sub").join("b.zip"), b"bbbb").unwrap();
        std::fs::write(root.join("notes.txt"), b"not an archive").unwrap();
        let db = dir.path().join("lib.db");
        migrate(&db, &Vectors::default()).unwrap();
        let conn = open(&db).unwrap();
        let folder = crate::winpath::native(&root.to_string_lossy());
        assert_eq!(changes(&conn, &folder, true).unwrap(), Changes { new: 2, modified: 0, gone: 0 });

        // Recorded as they are: nothing to do.
        let insert = |p: &str, size: i64, mtime: i64| {
            conn.execute(
                "INSERT INTO models (title, archive_path, archive_hash, archive_size, archive_mtime_ns, is_missing, to_print) \
                 VALUES ('x', ?1, ?1, ?2, ?3, 0, 0)",
                rusqlite::params![p, size, mtime],
            )
            .unwrap();
        };
        for n in ["a.zip", "sub/b.zip"] {
            let p = path(n);
            let (s, m) = crawl::stat(&p).unwrap();
            insert(&p, s, m);
        }
        assert!(!changes(&conn, &folder, true).unwrap().any());
        assert_eq!(changes(&conn, &folder, false).unwrap(), Changes::default(), "a non-recursive check sees a.zip only");

        // A rewritten archive, a deleted one, a new one.
        std::fs::write(root.join("a.zip"), b"aaaa-longer").unwrap();
        std::fs::remove_file(root.join("sub").join("b.zip")).unwrap();
        std::fs::write(root.join("c.7z"), b"c").unwrap();
        assert_eq!(changes(&conn, &folder, true).unwrap(), Changes { new: 1, modified: 1, gone: 1 });

        // Records elsewhere, or already missing, are not this folder's business.
        insert(&crate::winpath::native(&dir.path().join("other").join("x.zip").to_string_lossy()), 1, 1);
        conn.execute("UPDATE models SET is_missing = 1 WHERE archive_path LIKE '%b.zip'", []).unwrap();
        assert_eq!(changes(&conn, &folder, true).unwrap(), Changes { new: 1, modified: 1, gone: 0 });
    }
}
